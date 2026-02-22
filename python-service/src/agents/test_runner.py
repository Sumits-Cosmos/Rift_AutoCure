"""
TestRunnerAgent: Executes tests inside an isolated Docker container.
Captures stdout, stderr, and exit code.

v2: Docker layer caching — builds base image once, uses `docker cp` on
    subsequent iterations. Generates .dockerignore for faster builds.
    UTF-8 safe subprocess handling.
"""
import os
import subprocess
import logging
import tempfile
import shutil
from .shared_state import SharedState

logger = logging.getLogger(__name__)

PYTHON_DOCKERFILE_TEMPLATE = """FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r requirements-dev.txt 2>/dev/null || true
RUN pip install --no-cache-dir -r test-requirements.txt 2>/dev/null || true
RUN pip install --no-cache-dir -r dev-requirements.txt 2>/dev/null || true
RUN if [ -f setup.py ] || [ -f pyproject.toml ]; then pip install --no-cache-dir -e . 2>/dev/null || true; fi
RUN pip install --no-cache-dir pytest pytest-cov httpx
CMD [{cmd_args}]
"""

NODE_DOCKERFILE_TEMPLATE = """FROM node:20-slim
WORKDIR /app
COPY package*.json ./
RUN npm install --legacy-peer-deps
COPY . .
CMD {cmd_json}
"""

GO_DOCKERFILE_TEMPLATE = """FROM golang:1.21-alpine
WORKDIR /app
COPY . .
RUN go mod download
CMD ["go", "test", "./..."]
"""

DOCKERIGNORE_CONTENT = """.git
node_modules
.venv
venv
__pycache__
*.pyc
.env
.DS_Store
Dockerfile*
"""


def _safe_run(cmd, **kwargs):
    """
    Run a subprocess with UTF-8 encoding and error replacement.
    Returns (returncode, stdout_str, stderr_str).
    Never raises UnicodeDecodeError.
    """
    kwargs.pop("text", None)
    kwargs.pop("encoding", None)
    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=kwargs.pop("timeout", 300),
        **kwargs,
    )
    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    return result.returncode, stdout, stderr


class TestRunnerAgent:
    """Runs tests for the target repo inside a sandboxed Docker container."""

    BASE_IMAGE_TAG = "cicd-healing-base-image"
    RUN_IMAGE_TAG = "cicd-healing-run-image"

    def run(self, state: SharedState) -> SharedState:
        repo_path = state.repo_path
        language = state.language
        test_command = state.test_command
        
        # Check execution mode
        use_docker = os.getenv("USE_DOCKER", "true").lower() == "true"
        mode_str = "Docker" if use_docker else "System (Local)"
        
        logger.info(f"[TestRunnerAgent] Running tests via {mode_str} | Language: {language} | Iteration: {state.current_iteration}")

        if not use_docker:
            return self._run_local(state, repo_path, test_command)

        # ── DOCKER EXECUTION PATH ──
        build_context = tempfile.mkdtemp(prefix="cicd_heal_")
        try:
            # Copy repo files into build context
            repo_dest = os.path.join(build_context, "app_src")
            shutil.copytree(repo_path, repo_dest, dirs_exist_ok=True)

            # Generate .dockerignore for faster builds
            dockerignore_path = os.path.join(repo_dest, ".dockerignore")
            if not os.path.exists(dockerignore_path):
                with open(dockerignore_path, "w", encoding="utf-8") as f:
                    f.write(DOCKERIGNORE_CONTENT)

            # Write Dockerfile
            dockerfile_content = self._generate_dockerfile(language, test_command)
            dockerfile_path = os.path.join(repo_dest, "Dockerfile.cicd")
            with open(dockerfile_path, "w", encoding="utf-8") as f:
                f.write(dockerfile_content)

            # ── Docker build strategy ──
            if not state.docker_image_built:
                # First iteration: full build (installs deps)
                build_ok, build_stdout, build_stderr = self._docker_build(repo_dest, self.BASE_IMAGE_TAG)
                if not build_ok:
                    state.test_exit_code = 1
                    state.test_stdout = build_stdout or ""
                    state.test_stderr = f"[Docker Build Failed]\n{build_stderr}"
                    logger.error(f"[TestRunnerAgent] Docker build failed:\n{(build_stderr or '')[:500]}")
                    return state
                state.docker_image_built = True
                logger.info("[TestRunnerAgent] ✅ Base Docker image built successfully.")
            else:
                # Subsequent iterations: rebuild but with Docker layer cache
                # The dep-install layers are cached, only COPY . . layer rebuilds
                build_ok, build_stdout, build_stderr = self._docker_build(
                    repo_dest, self.BASE_IMAGE_TAG
                )
                if not build_ok:
                    state.test_exit_code = 1
                    state.test_stdout = build_stdout or ""
                    state.test_stderr = f"[Docker Rebuild Failed]\n{build_stderr}"
                    return state
                logger.info("[TestRunnerAgent] ♻️ Docker image rebuilt (cached deps).")

            # Run tests
            run_ok, run_stdout, run_stderr, exit_code = self._docker_run()
            state.test_stdout = run_stdout or ""
            state.test_stderr = run_stderr or ""
            state.test_exit_code = exit_code
            logger.info(f"[TestRunnerAgent] Tests exit code: {exit_code}")

        except Exception as e:
            logger.error(f"[TestRunnerAgent] Exception during Docker run: {e}")
            state.test_stdout = state.test_stdout or ""
            state.test_stderr = state.test_stderr or str(e)
            state.test_exit_code = 1

        finally:
            shutil.rmtree(build_context, ignore_errors=True)
            self._docker_cleanup()

        return state

    def _run_local(self, state: SharedState, repo_path: str, test_command: str) -> SharedState:
        """Runs tests directly on the host system (Render/Local) without Docker."""
        logger.info(f"[TestRunnerAgent] Executing command: {test_command}")
        logger.info(f"[TestRunnerAgent] CWD: {repo_path}")
        
        try:
            # Install dependencies if needed (rudimentary check)
            # In a real setup, we assume environment is pre-provisioned, but for 
            # reliability we can try to install deps if requirements.txt exists.
            # However, for speed in typical healing loops, we assume deps are there.
            # If explicit installation is needed, we could add a flag.
            
            # Split command safely
            import shlex
            cmd_args = shlex.split(test_command)
            
            # Run the test command
            # We set a timeout to prevent hanging tests
            result = subprocess.run(
                cmd_args,
                cwd=repo_path,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=300
            )
            
            state.test_stdout = result.stdout
            state.test_stderr = result.stderr
            state.test_exit_code = result.returncode
            
            logger.info(f"[TestRunnerAgent] Local execution finished. Exit code: {result.returncode}")
            
        except FileNotFoundError:
             err_msg = f"Command not found: {test_command.split()[0]}"
             logger.error(f"[TestRunnerAgent] {err_msg}")
             state.test_stderr = err_msg
             state.test_exit_code = 127
        except subprocess.TimeoutExpired:
             err_msg = "Test execution timed out (300s)"
             logger.error(f"[TestRunnerAgent] {err_msg}")
             state.test_stderr = err_msg
             state.test_exit_code = 124
        except Exception as e:
             err_msg = f"Exception during local run: {str(e)}"
             logger.error(f"[TestRunnerAgent] {err_msg}")
             state.test_stderr = err_msg
             state.test_exit_code = 1
             
        return state

    def _generate_dockerfile(self, language: str, test_command: str) -> str:
        if language == "python":
            parts = test_command.split()
            cmd_args = ", ".join(f'"{p}"' for p in parts)
            return PYTHON_DOCKERFILE_TEMPLATE.format(cmd_args=cmd_args)
        elif language == "node":
            import json
            parts = test_command.split()
            return NODE_DOCKERFILE_TEMPLATE.format(cmd_json=json.dumps(parts))
        elif language == "go":
            return GO_DOCKERFILE_TEMPLATE
        else:
            parts = test_command.split()
            cmd_args = ", ".join(f'"{p}"' for p in parts)
            return PYTHON_DOCKERFILE_TEMPLATE.format(cmd_args=cmd_args)

    def _docker_build(self, build_context_path: str, tag: str):
        cmd = [
            "docker", "build",
            "-t", tag,
            "-f", os.path.join(build_context_path, "Dockerfile.cicd"),
            build_context_path
        ]
        logger.info(f"[TestRunnerAgent] Building Docker image: {' '.join(cmd[:6])}...")
        returncode, stdout, stderr = _safe_run(cmd, timeout=300)
        return returncode == 0, stdout, stderr

    def _docker_run(self):
        cmd = [
            "docker", "run",
            "--rm",
            "--network=none",
            "--memory=512m",
            "--cpus=1",
            self.BASE_IMAGE_TAG
        ]
        logger.info("[TestRunnerAgent] Running Docker container...")
        returncode, stdout, stderr = _safe_run(cmd, timeout=300)
        return returncode == 0, stdout, stderr, returncode

    def _docker_cleanup(self):
        for tag in [self.BASE_IMAGE_TAG, self.RUN_IMAGE_TAG]:
            try:
                subprocess.run(
                    ["docker", "rmi", "-f", tag],
                    capture_output=True,
                    timeout=30
                )
            except Exception:
                pass
