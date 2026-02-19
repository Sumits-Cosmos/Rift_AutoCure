"""
RepoAnalyzerAgent: Detects the programming language, test framework,
module system (ESM/CJS), and the appropriate test command for the target repository.

v2: Added module system detection (ESM vs CJS), JSX detection,
    vitest globals detection, and broken test script handling.
"""
import os
import json
import logging
from .shared_state import SharedState

logger = logging.getLogger(__name__)


class RepoAnalyzerAgent:
    """Analyzes a cloned repository to determine language, test setup, and module system."""

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
            ("jest.config.cjs", "jest", "npx jest --ci"),
            ("jest.config.mjs", "jest", "npx jest --ci"),
            ("vitest.config.js", "vitest", "npx vitest run"),
            ("vitest.config.ts", "vitest", "npx vitest run"),
            ("vitest.config.mjs", "vitest", "npx vitest run"),
            ("vite.config.ts", "vitest", "npx vitest run"),
            ("vite.config.js", "vitest", "npx vitest run"),
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

        # --- Detect Module System & Structural Details (Node.js) ---
        if detected_language == "node":
            self._detect_node_module_system(repo_path, state)
            self._detect_jsx_usage(repo_path, state)
            self._detect_vitest_globals(repo_path, state)

        return state

    def _detect_language(self, repo_path: str) -> str:
        for filename, lang in self.LANGUAGE_INDICATORS:
            if os.path.exists(os.path.join(repo_path, filename)):
                return lang
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
                if filename == "package.json":
                    framework, command = self._parse_package_json_test(filepath, framework, command)
                if filename == "requirements.txt":
                    if not self._dep_in_file(filepath, "pytest"):
                        continue
                # For vite.config.*, confirm vitest is actually in deps
                if filename.startswith("vite.config"):
                    pkg = os.path.join(repo_path, "package.json")
                    if os.path.exists(pkg):
                        try:
                            with open(pkg, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                            if "vitest" not in deps:
                                continue
                        except Exception:
                            continue
                return framework, command
        # Fallbacks
        if language == "python":
            return "pytest", "pytest -v --tb=short"
        if language == "node":
            return "jest", "npx jest --ci --passWithNoTests"
        return "unknown", "echo no test command detected"

    def _dep_in_file(self, filepath: str, dep: str) -> bool:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return dep.lower() in f.read().lower()
        except Exception:
            return False

    # ── Broken test script detection ──────────────────────────────────────────

    _BROKEN_TEST_SCRIPTS = [
        "echo \"error: no test specified\"",
        "echo 'error: no test specified'",
        "echo \"Error: no test specified\"",
        "no test specified",
    ]

    def _is_test_script_broken(self, test_script: str) -> bool:
        low = test_script.lower().strip()
        for pattern in self._BROKEN_TEST_SCRIPTS:
            if pattern.lower() in low:
                return True
        if low.startswith("echo") and "exit 1" in low:
            return True
        return False

    def _parse_package_json_test(self, filepath: str, default_framework: str, default_cmd: str):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            scripts = data.get("scripts", {})
            test_script = scripts.get("test", "")
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}

            broken = not test_script or self._is_test_script_broken(test_script)
            if broken:
                logger.warning(
                    f"[RepoAnalyzerAgent] ⚠️ package.json test script is "
                    f"{'missing' if not test_script else 'broken placeholder'}: "
                    f"'{test_script}'. Will use direct framework runner."
                )

            if "vitest" in deps:
                return "vitest", "npx vitest run"
            if "jest" in deps:
                if broken:
                    return "jest", "npx jest --ci --passWithNoTests"
                return "jest", "npm test -- --passWithNoTests"
            if "mocha" in deps:
                return "mocha", "npx mocha"

            if test_script and not broken:
                return "custom", "npm test"

            logger.info("[RepoAnalyzerAgent] No framework in deps, defaulting to npx jest.")
            return "jest", "npx jest --ci --passWithNoTests"

        except Exception as e:
            logger.warning(f"[RepoAnalyzerAgent] Error parsing package.json: {e}")
        return default_framework, default_cmd

    # ── Module system detection ───────────────────────────────────────────────

    def _detect_node_module_system(self, repo_path: str, state: SharedState):
        """Detect ESM vs CJS from package.json and file extensions."""
        pkg_path = os.path.join(repo_path, "package.json")
        if not os.path.exists(pkg_path):
            return
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("type") == "module":
                state.module_system = "esm"
                logger.info("[RepoAnalyzerAgent] Module system: ESM (type: module)")
            else:
                state.module_system = "cjs"
                logger.info("[RepoAnalyzerAgent] Module system: CJS (default)")
        except Exception:
            pass

    def _detect_jsx_usage(self, repo_path: str, state: SharedState):
        """Check if the repo uses JSX files."""
        for root, _, files in os.walk(repo_path):
            parts = root.replace("\\", "/").split("/")
            if any(p.startswith(".") or p in ("node_modules",) for p in parts):
                continue
            for fname in files:
                if fname.endswith((".jsx", ".tsx")):
                    state.has_jsx = True
                    logger.info(f"[RepoAnalyzerAgent] JSX detected: {fname}")
                    return

    def _detect_vitest_globals(self, repo_path: str, state: SharedState):
        """Check vitest config for globals: true."""
        config_files = [
            "vitest.config.js", "vitest.config.ts", "vitest.config.mjs",
            "vite.config.js", "vite.config.ts", "vite.config.mjs",
        ]
        for cfg_name in config_files:
            cfg_path = os.path.join(repo_path, cfg_name)
            if not os.path.exists(cfg_path):
                continue
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if "globals" in content and "true" in content:
                    state.vitest_globals = True
                    logger.info("[RepoAnalyzerAgent] Vitest globals: true detected")
                    return
            except Exception:
                pass
