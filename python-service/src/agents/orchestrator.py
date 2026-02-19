"""
OrchestratorAgent: The top-level controller that coordinates all sub-agents.
Manages the retry loop (max 5 iterations), maintains shared state,
and produces the final results.json.

FIXED LOOP ORDER:
  1. Clone repo & create branch
  2. RepoAnalyzerAgent → detect lang/framework + test files
  3. If no test files → LLM generates basic test stubs  
  4. (Loop up to retry_limit):
     a. TestRunnerAgent   → run tests in Docker
     b. FailureClassifierAgent → classify errors    ← ALWAYS before monitor stop check
     c. FixGeneratorAgent → apply patches
     d. CIMonitorAgent    → decide continue/stop
"""
import os
import re
import json
import time
import shutil
import logging
import subprocess
import tempfile
from dataclasses import asdict

from .shared_state import SharedState, FixRecord
from .repo_analyzer import RepoAnalyzerAgent
from .test_runner import TestRunnerAgent
from .failure_classifier import FailureClassifierAgent
from .fix_generator import FixGeneratorAgent
from .ci_monitor import CIMonitorAgent

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    """
    Top-level orchestrator that runs the full CI/CD healing pipeline.

    Flow:
      1. Clone repo & create branch
      2. RepoAnalyzerAgent  → detect lang/framework + scan for test files
      3. (If no tests found → generate stub tests or emit clear message)
      4. Loop up to retry_limit:
         a. TestRunnerAgent          → run tests in Docker
         b. FailureClassifierAgent   → classify errors from output
         c. FixGeneratorAgent        → generate + commit patches
         d. CIMonitorAgent           → decide continue / stop
      5. Write results & return
    """

    def __init__(self, retry_limit: int = 5):
        self.retry_limit = retry_limit
        self.analyzer = RepoAnalyzerAgent()
        self.runner = TestRunnerAgent()
        self.classifier = FailureClassifierAgent()
        self.fixer = FixGeneratorAgent()
        self.monitor = CIMonitorAgent()

    def run(self, repo_url: str, team_name: str, leader_name: str) -> dict:
        state = SharedState(
            repo_url=repo_url,
            team_name=team_name,
            leader_name=leader_name,
            retry_limit=self.retry_limit,
            start_time=time.time()
        )

        tmp_dir = tempfile.mkdtemp(prefix="cicd_repo_")
        try:
            state.repo_path = tmp_dir
            state.branch_name = self._make_branch_name(team_name, leader_name)

            # ── Step 1: Clone ──────────────────────────────────────────────
            logger.info(f"[OrchestratorAgent] Cloning {repo_url} into {tmp_dir}")
            clone_ok = self._clone_repo(repo_url, tmp_dir)
            if not clone_ok:
                state.final_status = "FAILED"
                state.error_message = f"Failed to clone repository: {repo_url}"
                return self._build_result(state)

            logger.info(f"[OrchestratorAgent] Creating branch: {state.branch_name}")
            self._create_branch(tmp_dir, state.branch_name)

            # ── Step 2: Analyze repo ───────────────────────────────────────
            state = self.analyzer.run(state)

            # ── Step 3: Check / generate test files ────────────────────────
            has_tests = self._has_test_files(state)
            if not has_tests:
                generated = self._generate_placeholder_tests(state)
                if generated:
                    logger.info("[OrchestratorAgent] ✅ Generated placeholder test file.")
                    state.error_message = (
                        "No existing test files were found in the repository. "
                        "A placeholder test file was generated to allow the pipeline to proceed."
                    )
                    self._commit_generated_tests(state)
                else:
                    logger.warning("[OrchestratorAgent] ❌ No test files found and could not generate stubs.")
                    state.final_status = "FAILED"
                    state.error_message = (
                        f"No test files found in the repository. "
                        f"Searched for {state.language} test patterns. "
                        f"Test framework detected: {state.test_framework}. "
                        f"Please add test files to your repository before running the healing agent."
                    )
                    return self._build_result(state)

            # ── Step 4: Healing loop ───────────────────────────────────────
            while state.current_iteration < state.retry_limit:
                state.current_iteration += 1
                logger.info(f"\n{'='*60}")
                logger.info(f"[OrchestratorAgent] >>> ITERATION {state.current_iteration}/{state.retry_limit}")
                logger.info(f"{'='*60}")

                # a) Run tests in Docker
                state = self.runner.run(state)

                # b) Short-circuit only if tests PASSED
                if state.test_exit_code == 0:
                    state.final_status = "PASSED"
                    logger.info("[OrchestratorAgent] ✅ Tests passed!")
                    break

                # Log raw output to help debugging
                logger.info(f"[OrchestratorAgent] Test stdout (first 500):\n{state.test_stdout[:500]}")
                logger.info(f"[OrchestratorAgent] Test stderr (first 500):\n{state.test_stderr[:500]}")

                # c) Classify failures — ALWAYS before any stop decision
                state = self.classifier.run(state)

                # d) Apply fixes
                state = self.fixer.run(state)

                # e) Now let the monitor decide if we should continue
                monitor_result = self.monitor.run(state)
                if monitor_result["should_stop"]:
                    reason = monitor_result["reason"]
                    if reason == "RETRY_LIMIT_REACHED":
                        state.final_status = "PARTIAL" if state.total_fixes > 0 else "FAILED"
                    elif reason == "UNCLASSIFIABLE_FAILURE":
                        state.final_status = "FAILED"
                        if not state.error_message:
                            state.error_message = (
                                "Tests failed but no specific errors could be classified. "
                                f"Stdout: {state.test_stdout[:300]} | "
                                f"Stderr: {state.test_stderr[:300]}"
                            )
                    else:
                        state.final_status = "PARTIAL" if state.total_fixes > 0 else "FAILED"
                    break
            else:
                state.final_status = "FAILED"

        except Exception as e:
            logger.exception(f"[OrchestratorAgent] Unhandled error: {e}")
            state.final_status = "FAILED"
            state.error_message = str(e)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        state.end_time = time.time()
        return self._build_result(state)

    # ─── Test file helpers ────────────────────────────────────────────────────

    TEST_FILE_PATTERNS = {
        "python": [
            r"test_.*\.py$", r".*_test\.py$", r"tests?[\\/].*\.py$",
        ],
        "node": [
            r".*\.(test|spec)\.(js|ts|jsx|tsx)$",
            r"__tests__[\\/].*\.(js|ts|jsx|tsx)$",
        ],
        "go": [r".*_test\.go$"],
        "ruby": [r"spec[\\/].*_spec\.rb$", r"test[\\/].*_test\.rb$"],
        "java": [r".*Test\.java$", r".*Tests\.java$"],
    }

    def _has_test_files(self, state: SharedState) -> bool:
        patterns = self.TEST_FILE_PATTERNS.get(state.language, [])
        if not patterns:
            return True  # can't check — assume present

        for root, _, files in os.walk(state.repo_path):
            # Skip hidden dirs like .git, node_modules, venv
            parts = root.replace("\\", "/").split("/")
            if any(p.startswith(".") or p in ("node_modules", ".venv", "venv", "__pycache__") for p in parts):
                continue
            for fname in files:
                rel = os.path.relpath(os.path.join(root, fname), state.repo_path).replace("\\", "/")
                for pat in patterns:
                    if re.search(pat, rel, re.IGNORECASE):
                        logger.info(f"[OrchestratorAgent] Found test file: {rel}")
                        return True

        logger.warning(f"[OrchestratorAgent] No test files found for language='{state.language}'")
        return False

    def _generate_placeholder_tests(self, state: SharedState) -> bool:
        """
        Generate minimal placeholder test files so the pipeline can proceed.
        Returns True if a file was written.
        """
        lang = state.language
        repo = state.repo_path

        if lang == "python":
            content = (
                "# Auto-generated placeholder test by CI Healing Agent\n"
                "def test_placeholder():\n"
                "    \"\"\"Placeholder — replace with real tests.\"\"\"\n"
                "    assert True\n"
            )
            path = os.path.join(repo, "tests", "test_placeholder.py")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # Also create __init__.py
            with open(os.path.join(repo, "tests", "__init__.py"), "w") as f:
                f.write("")

        elif lang == "node":
            # Detect if jest is in package.json
            content = (
                "// Auto-generated placeholder test by CI Healing Agent\n"
                "describe('Placeholder', () => {\n"
                "  test('placeholder test - replace with real tests', () => {\n"
                "    expect(true).toBe(true);\n"
                "  });\n"
                "});\n"
            )
            path = os.path.join(repo, "__tests__", "placeholder.test.js")
            os.makedirs(os.path.dirname(path), exist_ok=True)

        elif lang == "go":
            content = (
                'package main\n\nimport "testing"\n\n'
                "func TestPlaceholder(t *testing.T) {\n"
                '    // Auto-generated placeholder\n}\n'
            )
            path = os.path.join(repo, "placeholder_test.go")

        else:
            return False

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"[OrchestratorAgent] Wrote placeholder test: {path}")
            return True
        except Exception as e:
            logger.error(f"[OrchestratorAgent] Failed to write placeholder test: {e}")
            return False

    def _commit_generated_tests(self, state: SharedState):
        env = {**os.environ, "GIT_AUTHOR_NAME": "AI-Agent", "GIT_AUTHOR_EMAIL": "ai@agent.local",
               "GIT_COMMITTER_NAME": "AI-Agent", "GIT_COMMITTER_EMAIL": "ai@agent.local"}
        try:
            subprocess.run(["git", "add", "-A"], cwd=state.repo_path, capture_output=True, env=env)
            subprocess.run(
                ["git", "commit", "-m", "[AI-AGENT] Add placeholder test files (no tests found in repo)"],
                cwd=state.repo_path, capture_output=True, env=env
            )
        except Exception as e:
            logger.warning(f"[OrchestratorAgent] Could not commit generated tests: {e}")

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _make_branch_name(self, team_name: str, leader_name: str) -> str:
        def sanitize(s: str) -> str:
            s = s.upper()
            s = re.sub(r"[^A-Z0-9]+", "_", s)
            s = s.strip("_")
            return s
        return f"{sanitize(team_name)}_{sanitize(leader_name)}_AI_Fix"

    def _clone_repo(self, repo_url: str, dest: str) -> bool:
        try:
            result = subprocess.run(
                ["git", "clone", repo_url, dest],
                capture_output=True, text=True, timeout=120,
                env={**os.environ}
            )
            if result.returncode != 0:
                logger.error(f"[OrchestratorAgent] Clone failed:\n{result.stderr}")
                return False
            # git clone into existing dir — move contents up if in sub-folder
            items = [i for i in os.listdir(dest) if i != ".git"]
            if len(items) == 1 and os.path.isdir(os.path.join(dest, items[0])):
                inner = os.path.join(dest, items[0])
                for item in os.listdir(inner):
                    shutil.move(os.path.join(inner, item), os.path.join(dest, item))
                shutil.rmtree(inner, ignore_errors=True)
            return True
        except Exception as e:
            logger.error(f"[OrchestratorAgent] Clone exception: {e}")
            return False

    def _create_branch(self, repo_path: str, branch_name: str):
        env = {**os.environ, "GIT_AUTHOR_NAME": "AI-Agent", "GIT_AUTHOR_EMAIL": "ai@agent.local",
               "GIT_COMMITTER_NAME": "AI-Agent", "GIT_COMMITTER_EMAIL": "ai@agent.local"}
        try:
            subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=repo_path, capture_output=True, text=True, check=True, env=env
            )
            logger.info(f"[OrchestratorAgent] Branch '{branch_name}' created.")
        except subprocess.CalledProcessError as e:
            logger.warning(f"[OrchestratorAgent] Branch creation warning: {e.stderr}")

    def _build_result(self, state: SharedState) -> dict:
        elapsed = 0.0
        if state.start_time and state.end_time:
            elapsed = state.end_time - state.start_time

        fixes_list = []
        for fix in state.fixes_applied:
            if isinstance(fix, FixRecord):
                fixes_list.append({
                    "file": fix.file,
                    "bug_type": fix.bug_type,
                    "line": fix.line,
                    "commit_message": fix.commit_message,
                    "status": fix.status
                })
            elif isinstance(fix, dict):
                fixes_list.append(fix)

        result = {
            "repository": state.repo_url,
            "branch": state.branch_name,
            "total_failures": state.total_failures,
            "total_fixes": state.total_fixes,
            "iterations_used": state.current_iteration,
            "status": state.final_status,
            "time_taken_seconds": round(elapsed, 2),
            "language": state.language,
            "test_framework": state.test_framework,
            "fixes": fixes_list,
            "error": state.error_message
        }

        logger.info(f"[OrchestratorAgent] Final result: {json.dumps(result, indent=2)}")
        return result
