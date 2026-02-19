"""
OrchestratorAgent: The top-level controller that coordinates all sub-agents.
Manages the retry loop, maintains shared state, and produces the final results.

v2: Cumulative fix tracking, iteration snapshots, oscillation/no-progress
    early termination, improved result reporting with all_fixes.

LOOP ORDER:
  1. Clone repo & create branch
  2. RepoAnalyzerAgent → detect lang/framework + test files + module system
  3. If no test files → LLM generates basic test stubs
  4. (Loop up to retry_limit):
     a. TestRunnerAgent        → run tests in Docker
     b. FailureClassifierAgent → classify errors (STRUCTURAL first)
     c. FixGeneratorAgent      → apply patches (structural → LLM)
     d. CIMonitorAgent         → decide continue/stop (oscillation/progress-aware)
"""
import os
import re
import json
import time
import shutil
import logging
import subprocess
import tempfile

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

    def __init__(self, retry_limit: int = 5, log_callback=None):
        self.retry_limit = retry_limit
        self._log = log_callback or (lambda msg, level="info": None)
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
            self._log(f"Cloning repository: {repo_url}")
            logger.info(f"[OrchestratorAgent] Cloning {repo_url} into {tmp_dir}")
            clone_ok = self._clone_repo(repo_url, tmp_dir)
            if not clone_ok:
                state.final_status = "FAILED"
                state.error_message = f"Failed to clone repository: {repo_url}"
                self._log(f"❌ Clone failed for {repo_url}", "error")
                return self._build_result(state)

            self._log(f"✅ Repository cloned successfully")
            self._log(f"Creating branch: {state.branch_name}")
            logger.info(f"[OrchestratorAgent] Creating branch: {state.branch_name}")
            self._create_branch(tmp_dir, state.branch_name)
            self._log(f"✅ Branch '{state.branch_name}' created")

            # ── Step 2: Analyze repo ───────────────────────────────────────
            self._log("Analyzing repository structure (language, framework, test files)...")
            state = self.analyzer.run(state)
            self._log(f"✅ Detected: language={state.language}, framework={state.test_framework}, module={state.module_system or 'N/A'}")

            # ── Step 3: Check / generate test files ────────────────────────
            self._log("Checking for existing test files...")
            has_tests = self._has_test_files(state)
            if not has_tests:
                self._log("⚠ No test files found — attempting to generate placeholder tests...", "warn")
                generated = self._generate_placeholder_tests(state)
                if generated:
                    logger.info("[OrchestratorAgent] ✅ Generated placeholder test file.")
                    self._log("✅ Generated placeholder test file")
                    state.error_message = (
                        "No existing test files were found in the repository. "
                        "A placeholder test file was generated to allow the pipeline to proceed."
                    )
                    self._commit_generated_tests(state)
                else:
                    logger.warning("[OrchestratorAgent] ❌ No test files found and could not generate stubs.")
                    self._log("❌ No test files found and could not generate stubs", "error")
                    state.final_status = "FAILED"
                    state.error_message = (
                        f"No test files found in the repository. "
                        f"Searched for {state.language} test patterns. "
                        f"Test framework detected: {state.test_framework}. "
                        f"Please add test files to your repository before running the healing agent."
                    )
                    return self._build_result(state)
            else:
                self._log("✅ Test files found")

            # ── Step 4: Healing loop ───────────────────────────────────────
            self._log(f"Starting healing loop (max {state.retry_limit} iterations)...")
            while state.current_iteration < state.retry_limit:
                state.current_iteration += 1
                self._log(f"━━━ Iteration {state.current_iteration}/{state.retry_limit} ━━━")
                logger.info(f"\n{'='*60}")
                logger.info(f"[OrchestratorAgent] >>> ITERATION {state.current_iteration}/{state.retry_limit}")
                logger.info(f"{'='*60}")

                # a) Run tests in Docker
                self._log("Running tests in Docker container...")
                state = self.runner.run(state)

                # b) Short-circuit only if tests PASSED
                if state.test_exit_code == 0:
                    state.final_status = "PASSED"
                    self._log("✅ All tests passed!")
                    logger.info("[OrchestratorAgent] ✅ Tests passed!")
                    # Record final clean iteration
                    state.classified_failures = []
                    state.fixes_applied = []
                    state.total_failures = 0
                    state.total_fixes = 0
                    state.record_iteration()
                    
                    # ─── Step 5: Post-Success Actions ──────────────────────────
                    # 1. Push to remote
                    self._log("Pushing fixed code to remote repository...")
                    self._git_push(state)
                    self._log(f"Git push: {getattr(state, 'git_push_status', 'unknown')}")
                    
                    # 2. Deploy
                    self._log("Attempting deployment...")
                    self._deploy_container(state)
                    if hasattr(state, 'deployment_url') and state.deployment_url and not state.deployment_url.startswith('Failed'):
                        self._log(f"✅ Deployed at {state.deployment_url}")
                    else:
                        self._log("⚠ Deployment skipped or failed", "warn")

                    break

                # Log raw output to help debugging (guard against None)
                stdout = state.test_stdout or ""
                stderr = state.test_stderr or ""
                logger.info(f"[OrchestratorAgent] Test stdout (first 500):\n{stdout[:500]}")
                logger.info(f"[OrchestratorAgent] Test stderr (first 500):\n{stderr[:500]}")

                # c) Classify failures — ALWAYS before any stop decision
                self._log(f"Tests failed (exit code {state.test_exit_code}). Classifying failures...")
                state = self.classifier.run(state)
                num_failures = len(state.classified_failures)
                self._log(f"Found {num_failures} failure(s): {', '.join(f.get('bug_type','?') for f in state.classified_failures[:5])}")

                # d) Apply fixes
                self._log("Generating and applying fixes...")
                state = self.fixer.run(state)
                num_fixes = len(state.fixes_applied)
                self._log(f"Applied {num_fixes} fix(es) this iteration (cumulative: {state.cumulative_fixes})")

                # e) Now let the monitor decide if we should continue
                monitor_result = self.monitor.run(state)

                logger.info(
                    f"[OrchestratorAgent] Monitor decision: {monitor_result['reason']} | "
                    f"Cumulative fixes: {state.cumulative_fixes}"
                )
                self._log(f"Monitor: {monitor_result['reason']}")

                if monitor_result["should_stop"]:
                    reason = monitor_result["reason"]
                    if reason == "TESTS_PASSED":
                        state.final_status = "PASSED"
                    elif reason == "OSCILLATING":
                        state.final_status = "PARTIAL" if state.cumulative_fixes > 0 else "FAILED"
                        repeated = state.get_repeated_failures()
                        state.error_message = (
                            f"Healing loop detected oscillation — same failures recurring: "
                            f"{', '.join(repeated[:5])}. "
                            f"Applied {state.cumulative_fixes} fix(es) before stopping."
                        )
                    elif reason == "NO_PROGRESS":
                        state.final_status = "PARTIAL" if state.cumulative_fixes > 0 else "FAILED"
                        state.error_message = (
                            f"No progress for 2 consecutive iterations. "
                            f"Applied {state.cumulative_fixes} fix(es) total but "
                            f"{state.total_failures} failure(s) remain."
                        )
                    elif reason == "RETRY_LIMIT_REACHED":
                        state.final_status = "PARTIAL" if state.cumulative_fixes > 0 else "FAILED"
                        state.error_message = (
                            f"Retry limit ({state.retry_limit}) reached. "
                            f"Applied {state.cumulative_fixes} fix(es) but "
                            f"{state.total_failures} failure(s) remain."
                        )
                    else:
                        state.final_status = "PARTIAL" if state.cumulative_fixes > 0 else "FAILED"
                    break
            else:
                # while-else: loop exhausted without break
                state.final_status = "PARTIAL" if state.cumulative_fixes > 0 else "FAILED"

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
        except Exception as e:
            logger.warning(f"[OrchestratorAgent] Could not commit generated tests: {e}")

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _make_branch_name(self, team_name: str, leader_name: str) -> str:
        def sanitize(s: str) -> str:
            # Replace spaces with underscores, remove non-alphanumeric (keep underscores), uppercase
            s = s.replace(" ", "_")
            s = re.sub(r"[^A-Za-z0-9_]", "", s)
            return s.upper()
        
        t = sanitize(team_name)
        l = sanitize(leader_name)
        return f"{t}_{l}_AI_Fix"

    def _git_push(self, state: SharedState):
        """Push the current branch to origin using system credentials."""
        repo_path = state.repo_path
        branch = state.branch_name
        logger.info(f"[OrchestratorAgent] Pushing branch {branch} to origin...")
        
        # We use the system's git credentials (SSH or credential helper)
        # So we do NOT override GIT_AUTHOR/COMMITTER with dummy values for the push
        # (Though we did for commits, which is fine)
        
        try:
            # We assume credentials are in the URL or SSH agent is active
            subprocess.run(
                ["git", "push", "-u", "origin", branch],
                cwd=repo_path, check=True, capture_output=True, text=True
            )
            state.git_push_status = "Success"
            logger.info("[OrchestratorAgent] ✅ Git push successful.")
        except subprocess.CalledProcessError as e:
            state.git_push_status = f"Failed (No Auth): {e.stderr.strip()[:100]}..."
            logger.warning(f"[OrchestratorAgent] ❌ Git push failed: {e.stderr}")

    def _deploy_container(self, state: SharedState):
        """Deploy the fixed app to a random port using Docker."""
        import random, socket
        
        def is_port_in_use(port):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                return s.connect_ex(('localhost', port)) == 0

        # Find a free port
        port = random.randint(9000, 9999)
        while is_port_in_use(port):
            port = random.randint(9000, 9999)
            
        logger.info(f"[OrchestratorAgent] Deploying to port {port}...")
        
        # Build image name
        img_tag = f"cicd-deploy-{state.branch_name.lower()}"
        
        try:
            # 1. Build prod image (using Dockerfile.cicd from TestRunner or repo's Dockerfile)
            # For simplicity, we reuse the Dockerfile.cicd generated by TestRunner 
            # OR we try to find a real Dockerfile. 
            # Let's try to find a real Dockerfile first, else use the one we made.
            
            dockerfile = "Dockerfile"
            if not os.path.exists(os.path.join(state.repo_path, "Dockerfile")):
                 # If no Dockerfile, use the one we generated for testing (Dockerfile.cicd)
                 # But we need to make sure it exposes a port. 
                 # Actually, let's just use the test image for the prototype 
                 # and override the command to start the app.
                 # BUT TestRunner cleans up. We need to rebuild or keep it.
                 # Let's rely on TestRunner having built 'cicd-healing-base-image'.
                 
                 # Better approach for prototype:
                 # Just run the 'cicd-healing-base-image' but with a start command.
                 pass
            
            # For this prototype: We will try to run 'npm start' or 'python app.py'
            # inside the container we already built/used.
            
            # But wait, TestRunner cleans up images.
            # Let's just create a quick deployment container.
            
            # Simple Deploy Strategy:
            # 1. docker build -t <tag> .
            # 2. docker run -d -p <port>:<inner_port> <tag>
            
            inner_port = 3000 # Default assumption
            if state.language == "python": inner_port = 8000
            
            # Build
            subprocess.run(
                ["docker", "build", "-t", img_tag, "."],
                cwd=state.repo_path, check=True, capture_output=True
            )
            
            # Run
            # Helper to find command
            cmd = []
            if state.language == "node":
                 cmd = ["npm", "start"]
            elif state.language == "python":
                 # Try to guess: uvicorn, flask, python main.py
                 if os.path.exists(os.path.join(state.repo_path, "main.py")):
                      cmd = ["python", "main.py"]
                 else:
                      cmd = ["python", "app.py"]
            
            container_name = f"deploy-{port}"
            docker_cmd = [
                "docker", "run", "-d",
                "-p", f"{port}:{inner_port}",
                "--name", container_name,
                img_tag
            ] + cmd
            
            subprocess.run(docker_cmd, check=True, capture_output=True)
            
            state.deployment_url = f"http://localhost:{port}"
            logger.info(f"[OrchestratorAgent] ✅ Deployed at {state.deployment_url}")
            
        except Exception as e:
            logger.error(f"[OrchestratorAgent] Deployment failed: {e}")
            state.deployment_url = f"Failed: {str(e)}"

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

        # Build cumulative fixes list (all iterations)
        all_fixes_list = []
        for fix in state.all_fixes_applied:
            if isinstance(fix, FixRecord):
                all_fixes_list.append({
                    "file": fix.file,
                    "bug_type": fix.bug_type,
                    "line": fix.line,
                    "commit_message": fix.commit_message,
                    "explanation": fix.explanation,
                    "status": fix.status,
                    "iteration": fix.iteration,
                })
            elif isinstance(fix, dict):
                all_fixes_list.append(fix)

        # Build iteration history
        iter_history = []
        for snap in state.iteration_history:
            iter_history.append({
                "iteration": snap.iteration,
                "exit_code": snap.exit_code,
                "failure_count": snap.failure_count,
                "fix_count": snap.fix_count,
                "failure_signatures": snap.failure_signatures,
            })

        result = {
            "repository": state.repo_url,
            "branch": state.branch_name,
            "total_failures": state.total_failures,
            "total_fixes": state.cumulative_fixes,
            "iterations_used": state.current_iteration,
            "status": state.final_status,
            "time_taken_seconds": round(elapsed, 2),
            "language": state.language,
            "test_framework": state.test_framework,
            "module_system": state.module_system,
            "fixes": all_fixes_list,                   # cumulative fixes for frontend
            "all_fixes": all_fixes_list,               # alias for clarity
            "iteration_history": iter_history,         # per-iteration breakdown
            "repeated_failures": state.get_repeated_failures(),
            "repeated_failures": state.get_repeated_failures(),
            "error": state.error_message,
            "deployment_url": getattr(state, "deployment_url", None),
            "git_push_status": getattr(state, "git_push_status", None),
        }

        
        # Save results.json
        try:
             with open("results.json", "w", encoding="utf-8") as f:
                 json.dump(result, f, indent=2)
             logger.info("[OrchestratorAgent] Saved results.json")
        except Exception as e:
             logger.error(f"[OrchestratorAgent] Failed to save results.json: {e}")

        logger.info(f"[OrchestratorAgent] Final result: {json.dumps(result, indent=2)}")
        return result
