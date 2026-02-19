"""
TestRunnerAgent: Executes tests inside an isolated Docker container.
Captures stdout, stderr, and exit code.
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
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true
RUN pip install --no-cache-dir pytest pytest-cov 2>/dev/null || true
CMD [{cmd_args}]
"""

NODE_DOCKERFILE_TEMPLATE = """FROM node:20-slim
WORKDIR /app
COPY . .
RUN npm install --legacy-peer-deps 2>/dev/null || true
CMD {cmd_json}
"""

GO_DOCKERFILE_TEMPLATE = """FROM golang:1.21-alpine
WORKDIR /app
COPY . .
RUN go mod download 2>/dev/null || true
CMD ["go", "test", "./..."]
"""


class TestRunnerAgent:
    """Runs tests for the target repo inside a sandboxed Docker container."""

    IMAGE_TAG = "cicd-healing-temp-image"

    def run(self, state: SharedState) -> SharedState:
        repo_path = state.repo_path
        language = state.language
        test_command = state.test_command
        logger.info(f"[TestRunnerAgent] Running tests via Docker | Language: {language}")

        # Generate a Dockerfile in a temp dir that copies the repo
        build_context = tempfile.mkdtemp(prefix="cicd_heal_")
        try:
            # Copy repo files into build context
            repo_dest = os.path.join(build_context, "app_src")
            shutil.copytree(repo_path, repo_dest, dirs_exist_ok=True)

            # Write Dockerfile into the build context root
            dockerfile_content = self._generate_dockerfile(language, test_command)
            dockerfile_path = os.path.join(repo_dest, "Dockerfile.cicd")

            with open(dockerfile_path, "w", encoding="utf-8") as f:
                f.write(dockerfile_content)

            # Build Docker image
            build_ok, build_stdout, build_stderr = self._docker_build(repo_dest)
            if not build_ok:
                state.test_exit_code = 1
                state.test_stdout = build_stdout
                state.test_stderr = f"[Docker Build Failed]\n{build_stderr}"
                logger.error(f"[TestRunnerAgent] Docker build failed:\n{build_stderr}")
                return state

            # Run tests
            run_ok, run_stdout, run_stderr, exit_code = self._docker_run()
            state.test_stdout = run_stdout
            state.test_stderr = run_stderr
            state.test_exit_code = exit_code
            logger.info(f"[TestRunnerAgent] Tests exit code: {exit_code}")

        finally:
            shutil.rmtree(build_context, ignore_errors=True)
            self._docker_cleanup()

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
            # Generic fallback
            parts = test_command.split()
            cmd_args = ", ".join(f'"{p}"' for p in parts)
            return PYTHON_DOCKERFILE_TEMPLATE.format(cmd_args=cmd_args)

    def _docker_build(self, build_context_path: str):
        cmd = [
            "docker", "build",
            "-t", self.IMAGE_TAG,
            "-f", os.path.join(build_context_path, "Dockerfile.cicd"),
            build_context_path
        ]
        logger.info(f"[TestRunnerAgent] Building Docker image: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )
        return result.returncode == 0, result.stdout, result.stderr

    def _docker_run(self):
        cmd = [
            "docker", "run",
            "--rm",
            "--network=none",         # No network access for safety
            "--memory=512m",          # Memory limit
            "--cpus=1",               # CPU limit
            self.IMAGE_TAG
        ]
        logger.info(f"[TestRunnerAgent] Running Docker container...")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )
        return result.returncode == 0, result.stdout, result.stderr, result.returncode

    def _docker_cleanup(self):
        try:
            subprocess.run(
                ["docker", "rmi", "-f", self.IMAGE_TAG],
                capture_output=True,
                timeout=30
            )
        except Exception:
            pass
