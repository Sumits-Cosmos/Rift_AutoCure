"""
OrchestratorAgent: The top-level controller that coordinates all sub-agents.
Manages the retry loop (max N iterations), maintains shared state,
tracks CI/CD timeline, calculates score, pushes to remote,
and produces the final results.json.

FIXED LOOP ORDER:
  1. Clone repo & create branch
  2. RepoAnalyzerAgent → detect lang/framework + test files
  3. If no test files → LLM generates basic test stubs  
  4. (Loop up to retry_limit):
     a. TestRunnerAgent   → run tests in Docker
     b. FailureClassifierAgent → classify errors
     c. FixGeneratorAgent → apply patches
     d. CIMonitorAgent    → decide continue/stop
  5. Push to remote, write results.json & return
"""
import os
import re
import json
import time
import uuid
import shutil
import logging
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone

from .shared_state import SharedState, FixRecord
from .repo_analyzer import RepoAnalyzerAgent
from .test_runner import TestRunnerAgent
from .failure_classifier import FailureClassifierAgent, format_output_line
from .fix_generator import FixGeneratorAgent
from .ci_monitor import CIMonitorAgent

logger = logging.getLogger(__name__)


def calculate_score(start_time: float, end_time: float, total_commits: int) -> dict:
    """Calculate hackathon score based on time and commit count."""
    base = 100
    time_taken = int(end_time - start_time)
    speed_bonus = 10 if time_taken < 300 else 0  # < 5 minutes
    efficiency_penalty = max(0, (total_commits - 20) * 2)
    final = base + speed_bonus - efficiency_penalty
    return {
        "base": base,
        "speed_bonus": speed_bonus,
        "efficiency_penalty": efficiency_penalty,
        "final": max(0, final),
        "time_taken_seconds": time_taken,
    }


class OrchestratorAgent:
    """
    Top-level orchestrator that runs the full CI/CD healing pipeline.
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
            run_id=str(uuid.uuid4()),
            repo_url=repo_url,
            team_name=team_name,
            leader_name=leader_name,
            retry_limit=self.retry_limit,
            start_time=time.time(),
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
                    state.cicd_timeline.append({
                        "iteration": state.current_iteration,
                        "status": "PASSED",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "failures_count": 0,
                    })
                    logger.info("[OrchestratorAgent] ✅ Tests passed!")
                    break

                # Log raw output to help debugging
                logger.info(f"[OrchestratorAgent] Test stdout (first 500):\n{state.test_stdout[:500]}")
                logger.info(f"[OrchestratorAgent] Test stderr (first 500):\n{state.test_stderr[:500]}")

                # c) Classify failures
                state = self.classifier.run(state)

                # Record timeline entry for this FAILED iteration
                state.cicd_timeline.append({
                    "iteration": state.current_iteration,
                    "status": "FAILED",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "failures_count": state.total_failures,
                })

                # d) Apply fixes — track per-iteration count
                fixes_before = state.total_fixes
                state = self.fixer.run(state)
                fixes_this_iteration = state.total_fixes - fixes_before

                # e) Now let the monitor decide if we should continue
                monitor_result = self.monitor.run(state, fixes_this_iteration)
                if monitor_result["should_stop"]:
                    reason = monitor_result["reason"]
                    if reason == "RETRY_LIMIT_REACHED":
                        state.final_status = "PARTIAL" if state.total_fixes > 0 else "FAILED"
                    elif reason == "NO_PROGRESS":
                        state.final_status = "PARTIAL" if state.total_fixes > 0 else "FAILED"
                        if not state.error_message:
                            state.error_message = (
                                "No fixes could be applied — the LLM fix generator could not "
                                "resolve the detected failures. Check your GEMINI_API_KEY."
                            )
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

            # ── Step 5: Push to remote ─────────────────────────────────────
            if state.total_commits > 0:
                self._push_to_remote(state)

        except Exception as e:
            logger.exception(f"[OrchestratorAgent] Unhandled error: {e}")
            state.final_status = "FAILED"
            state.error_message = str(e)
        finally:
            state.end_time = time.time()
            result = self._build_result(state)
            # Write results.json to local path before cleanup
            self._write_results_json(state, result)
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return result

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
            return True

        for root, _, files in os.walk(state.repo_path):
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
            with open(os.path.join(repo, "tests", "__init__.py"), "w") as f:
                f.write("")
        elif lang == "node":
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
            state.total_commits += 1
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
            # Inject GITHUB_TOKEN for authentication if available
            token = os.getenv("GITHUB_TOKEN", "")
            if token and "github.com" in repo_url:
                # Transform https://github.com/... to https://TOKEN@github.com/...
                auth_url = repo_url.replace("https://github.com", f"https://{token}@github.com")
            else:
                auth_url = repo_url

            result = subprocess.run(
                ["git", "clone", auth_url, dest],
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

    def _push_to_remote(self, state: SharedState):
        """Push the current branch to the remote origin."""
        token = os.getenv("GITHUB_TOKEN", "")
        repo_path = state.repo_path

        # Verify we're not on main
        try:
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=repo_path, capture_output=True, text=True
            )
            current_branch = branch_result.stdout.strip()
            if current_branch in ("main", "master"):
                logger.error("[OrchestratorAgent] ❌ Refusing to push to main/master!")
                return
        except Exception:
            pass

        # Set remote URL with token if available
        if token:
            try:
                remote_result = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    cwd=repo_path, capture_output=True, text=True
                )
                remote_url = remote_result.stdout.strip()
                if "github.com" in remote_url and token not in remote_url:
                    auth_url = remote_url.replace("https://github.com", f"https://{token}@github.com")
                    subprocess.run(
                        ["git", "remote", "set-url", "origin", auth_url],
                        cwd=repo_path, capture_output=True, text=True
                    )
            except Exception as e:
                logger.warning(f"[OrchestratorAgent] Could not set remote URL: {e}")

        try:
            result = subprocess.run(
                ["git", "push", "-u", "origin", state.branch_name],
                cwd=repo_path, capture_output=True, text=True, timeout=120,
                env={**os.environ}
            )
            if result.returncode == 0:
                logger.info(f"[OrchestratorAgent] ✅ Pushed to remote branch: {state.branch_name}")
            else:
                logger.warning(f"[OrchestratorAgent] Push warning: {result.stderr}")
        except Exception as e:
            logger.warning(f"[OrchestratorAgent] Push failed: {e}")

    def _write_results_json(self, state: SharedState, result: dict):
        """Write results.json — always runs even on error (called from finally)."""
        try:
            results_path = os.path.join(state.repo_path, "results.json")
            with open(results_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, default=str)
            logger.info(f"[OrchestratorAgent] Wrote results.json to {results_path}")
        except Exception as e:
            logger.warning(f"[OrchestratorAgent] Could not write results.json: {e}")

    def _build_result(self, state: SharedState) -> dict:
        elapsed = 0.0
        if state.start_time:
            end = state.end_time or time.time()
            elapsed = end - state.start_time

        # Build fixes list matching hackathon schema
        fixes_list = []
        for fix in state.fixes_applied:
            if isinstance(fix, FixRecord):
                fixes_list.append({
                    "file": fix.file,
                    "bug_type": fix.bug_type,
                    "line_number": fix.line,
                    "commit_message": fix.commit_message,
                    "status": fix.status,
                    "output_line": fix.output_line,
                })
            elif isinstance(fix, dict):
                fixes_list.append(fix)

        # Calculate score
        score = calculate_score(
            state.start_time or time.time(),
            state.end_time or time.time(),
            state.total_commits,
        )

        # Map internal status to hackathon status
        ci_status = state.final_status
        if ci_status == "PARTIAL":
            ci_status = "FAILED"  # hackathon only uses PASSED/FAILED

        result = {
            "run_id": state.run_id,
            "repo_url": state.repo_url,
            "team_name": state.team_name,
            "leader_name": state.leader_name,
            "branch_name": state.branch_name,
            "total_failures": state.total_failures,
            "total_fixes": state.total_fixes,
            "total_commits": state.total_commits,
            "ci_status": ci_status,
            "time_taken_seconds": round(elapsed, 2),
            "score": score,
            "fixes": fixes_list,
            "cicd_timeline": state.cicd_timeline,
            "language": state.language,
            "test_framework": state.test_framework,
            "iterations_used": state.current_iteration,
            "max_retries": state.retry_limit,
            "status": ci_status,  # also include as top-level for polling
            "error": state.error_message,
        }

        logger.info(f"[OrchestratorAgent] Final result: {json.dumps(result, indent=2)}")
        return result
