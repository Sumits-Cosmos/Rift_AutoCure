"""
RepoAnalyzerAgent: Detects the programming language, test framework,
and the appropriate test command for the target repository.
"""
import os
import logging
from .shared_state import SharedState

logger = logging.getLogger(__name__)


class RepoAnalyzerAgent:
    """Analyzes a cloned repository to determine language and test setup."""

    # Language detection: (indicator_file, language)
    LANGUAGE_INDICATORS = [
        ("requirements.txt", "python"),
        ("pyproject.toml", "python"),
        ("setup.py", "python"),
        ("Pipfile", "python"),
        ("package.json", "node"),
        ("go.mod", "go"),
        ("Gemfile", "ruby"),
        ("pom.xml", "java"),
        ("build.gradle", "java"),
    ]

    # Framework detection per language: (indicator_file_or_dep, framework, command)
    FRAMEWORK_INDICATORS = {
        "python": [
            ("pytest.ini", "pytest", "pytest -v --tb=short"),
            ("conftest.py", "pytest", "pytest -v --tb=short"),
            ("setup.cfg", "pytest", "pytest -v --tb=short"),
            ("pyproject.toml", "pytest", "pytest -v --tb=short"),
            ("requirements.txt", "pytest", "pytest -v --tb=short"),
        ],
        "node": [
            ("jest.config.js", "jest", "npx jest --ci"),
            ("jest.config.ts", "jest", "npx jest --ci"),
            ("vitest.config.js", "vitest", "npx vitest run"),
            ("vitest.config.ts", "vitest", "npx vitest run"),
            ("package.json", "jest", "npm test -- --passWithNoTests"),
        ],
        "go": [
            ("go.mod", "go test", "go test ./..."),
        ],
        "ruby": [
            ("Gemfile", "rspec", "bundle exec rspec"),
        ],
        "java": [
            ("pom.xml", "maven", "mvn test"),
            ("build.gradle", "gradle", "./gradlew test"),
        ],
    }

    def run(self, state: SharedState) -> SharedState:
        repo_path = state.repo_path
        logger.info(f"[RepoAnalyzerAgent] Analyzing repo at: {repo_path}")

        # --- Detect Language ---
        detected_language = self._detect_language(repo_path)
        if not detected_language:
            logger.warning("[RepoAnalyzerAgent] Could not detect language, defaulting to 'python'")
            detected_language = "python"

        state.language = detected_language
        logger.info(f"[RepoAnalyzerAgent] Detected language: {detected_language}")

        # --- Detect Framework & Command ---
        framework, command = self._detect_framework(repo_path, detected_language)
        state.test_framework = framework
        state.test_command = command
        logger.info(f"[RepoAnalyzerAgent] Framework: {framework} | Command: {command}")

        return state

    def _detect_language(self, repo_path: str) -> str:
        for filename, lang in self.LANGUAGE_INDICATORS:
            if os.path.exists(os.path.join(repo_path, filename)):
                return lang
        # Check for .py files
        for root, _, files in os.walk(repo_path):
            for f in files:
                if f.endswith(".py"):
                    return "python"
                if f.endswith(".js") or f.endswith(".ts"):
                    return "node"
        return ""

    def _detect_framework(self, repo_path: str, language: str):
        indicators = self.FRAMEWORK_INDICATORS.get(language, [])
        for filename, framework, command in indicators:
            filepath = os.path.join(repo_path, filename)
            if os.path.exists(filepath):
                # Special handling: check package.json for test script
                if filename == "package.json":
                    framework, command = self._parse_package_json_test(filepath, framework, command)
                # Special handling: check requirements.txt for pytest
                if filename == "requirements.txt":
                    if not self._dep_in_file(filepath, "pytest"):
                        continue
                return framework, command
        # Fallbacks
        if language == "python":
            return "pytest", "pytest -v --tb=short"
        if language == "node":
            return "jest", "npm test"
        return "unknown", "echo no test command detected"

    def _dep_in_file(self, filepath: str, dep: str) -> bool:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return dep.lower() in f.read().lower()
        except Exception:
            return False

    def _parse_package_json_test(self, filepath: str, default_framework: str, default_cmd: str):
        import json
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            scripts = data.get("scripts", {})
            test_script = scripts.get("test", "")
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "jest" in deps:
                return "jest", "npm test -- --passWithNoTests"
            if "vitest" in deps:
                return "vitest", "npx vitest run"
            if "mocha" in deps:
                return "mocha", "npx mocha"
            if test_script:
                return "custom", f"npm test"
        except Exception:
            pass
        return default_framework, default_cmd
