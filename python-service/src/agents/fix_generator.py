"""
FixGeneratorAgent: Uses the Gemini LLM to generate minimal code patches
for classified failures and commits them to the local branch.

v2: Context-aware prompts (test output + prior fixes), deterministic
    structural fix handlers, syntax validation gate, cumulative tracking.
"""
import os
import re
import json
import logging
import subprocess
import google.generativeai as genai
from dotenv import load_dotenv
from .shared_state import SharedState, FixRecord

load_dotenv()
logger = logging.getLogger(__name__)

FIX_PROMPT = """
You are an expert software engineer. You will be given a code file and a classified bug.
Your task is to generate the minimal fix required to resolve the bug.

File: {file}
Bug Type: {bug_type}
Line Number: {line}
Error Description: {description}
Raw Error: {raw_error}

Current File Content:
```
{file_content}
```

Test Output (relevant snippet):
```
{test_output_snippet}
```
{prior_fix_context}

Repository file tree:
{file_tree}

Instructions:
1. Return ONLY a JSON object with the following fields:
   - "fixed_content": the complete fixed file content as a string
   - "commit_message": a git commit message starting with "[AI-AGENT]" describing the fix
   - "explanation": one sentence explaining the change
2. Make MINIMAL changes — only fix the identified bug.
3. Preserve all indentation, formatting, and coding style.
4. Do NOT add comments unless they were already there.
5. Do NOT restructure the file or change unrelated code.
6. NEVER modify test assertions or expected values. Tests define the specification.
7. If a function returns undefined/wrong value, fix the IMPLEMENTATION, not the test.
8. Common implementation bugs: missing return statement, wrong variable name, .length() vs .length, wrong import path.
9. Return ONLY the JSON object — no markdown, no prose.

Example response:
{{
  "fixed_content": "...",
  "commit_message": "[AI-AGENT] Fix SYNTAX error in calculator.py line 8 - add colon",
  "explanation": "Added missing colon at end of function definition on line 8."
}}
"""

# Prompt used when redirecting a test-file LOGIC error to the implementation file
IMPL_FIX_PROMPT = """
You are an expert software engineer. A test is failing because the implementation has a bug.
You must fix the IMPLEMENTATION file, NOT the test file.

Implementation File: {impl_file}
Bug Type: {bug_type}
Error Description: {description}
Raw Error: {raw_error}

Implementation File Content:
```
{impl_content}
```

Test File ({test_file}) Content (READ-ONLY — DO NOT MODIFY):
```
{test_content}
```

Test Output (relevant snippet):
```
{test_output_snippet}
```
{prior_fix_context}

Repository file tree:
{file_tree}

CRITICAL RULES:
1. You must fix the IMPLEMENTATION file. The test file is READ-ONLY.
2. Tests define the expected behavior — they are CORRECT.
3. If a function returns undefined, it's likely missing a 'return' statement.
4. If a function throws TypeError, check for .length() vs .length, wrong method calls, etc.
5. If an import path is wrong, fix it to point to the correct module.
6. Make MINIMAL changes — only fix the identified bug.
7. Preserve all indentation, formatting, and coding style.
8. Return ONLY a JSON object — no markdown, no prose.

Return:
{{
  "fixed_content": "the complete fixed IMPLEMENTATION file content",
  "commit_message": "[AI-AGENT] Fix BUG_TYPE in impl_file - description",
  "explanation": "one sentence explaining the change"
}}
"""


# Test file path patterns — used to detect test files vs implementation files
TEST_FILE_PATTERNS = [
    r'.*\.(test|spec)\.(js|ts|jsx|tsx)$',
    r'__tests__[\\/].*\.(js|ts|jsx|tsx)$',
    r'test_.*\.py$',
    r'.*_test\.py$',
    r'tests?[\\/].*\.py$',
    r'.*_test\.go$',
    r'spec[\\/].*_spec\.rb$',
    r'test[\\/].*_test\.rb$',
]


class FixGeneratorAgent:
    """Generates and applies code fixes using Gemini LLM with structural handlers."""

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY", "")
        if api_key and api_key not in ("your_gemini_api_key_here", "YOUR_GEMINI_KEY_HERE"):
            genai.configure(api_key=api_key)
            model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
            self.model = genai.GenerativeModel(model_name)
            logger.info(f"[FixGeneratorAgent] Using Gemini model: {model_name}")
        else:
            self.model = None
            logger.warning("[FixGeneratorAgent] No Gemini API key - fixes will be skipped.")

    def run(self, state: SharedState) -> SharedState:
        if not state.classified_failures:
            logger.info("[FixGeneratorAgent] No failures to fix.")
            state.fixes_applied = []
            state.total_fixes = 0
            return state

        fixes_applied = []
        repo_path = state.repo_path

        for failure in state.classified_failures:
            bug_type = failure.get("bug_type", "")

            # Try DEPENDENCY fix first (deterministic — add to requirements.txt)
            if bug_type == "DEPENDENCY":
                fix_record = self._fix_missing_dependency(failure, repo_path, state)
                if fix_record:
                    fix_record.iteration = state.current_iteration
                    fixes_applied.append(fix_record)
                    continue

            # Try structural fix (deterministic, no LLM needed)
            if bug_type == "STRUCTURAL":
                fix_record = self._apply_structural_fix(failure, repo_path, state)
                if fix_record:
                    fix_record.iteration = state.current_iteration
                    fixes_applied.append(fix_record)
                    continue

            if not self.model:
                logger.warning("[FixGeneratorAgent] Skipping LLM fix - no model available.")
                continue

            fix_record = self._fix_failure(failure, repo_path, state)
            if fix_record:
                fix_record.iteration = state.current_iteration
                fixes_applied.append(fix_record)

        state.fixes_applied = fixes_applied
        state.total_fixes = len(fixes_applied)
        # Cumulative tracking
        state.all_fixes_applied.extend(fixes_applied)
        state.cumulative_fixes = len(state.all_fixes_applied)
        logger.info(f"[FixGeneratorAgent] Applied {len(fixes_applied)} fix(es) this iteration, {state.cumulative_fixes} total.")
        return state
    # ── Dependency fixes (deterministic — patch requirements.txt) ────────────

    # Common Python module → pip package name mapping
    PIP_NAME_MAP = {
        "cv2": "opencv-python",
        "PIL": "Pillow",
        "sklearn": "scikit-learn",
        "yaml": "PyYAML",
        "bs4": "beautifulsoup4",
        "gi": "PyGObject",
        "attr": "attrs",
        "dotenv": "python-dotenv",
        "jose": "python-jose",
        "jwt": "PyJWT",
        "serial": "pyserial",
        "usb": "pyusb",
        "wx": "wxPython",
        "Crypto": "pycryptodome",
        "lxml": "lxml",
        "dateutil": "python-dateutil",
    }

    def _fix_missing_dependency(self, failure: dict, repo_path: str, state: SharedState):
        """Fix DEPENDENCY errors by adding missing package to requirements.txt.
        
        This is deterministic — no LLM needed. The error message tells us
        exactly which module is missing, so we add it to the dependency file.
        """
        raw = failure.get("raw_error", "") + " " + failure.get("description", "")
        
        # Extract module name from "No module named 'xxx'" or "No module named 'xxx.yyy'"
        m = re.search(r"No module named '([^']+)'", raw)
        if not m:
            m = re.search(r"No module named (\S+)", raw)
        if not m:
            logger.warning("[FixGeneratorAgent] DEPENDENCY: Could not extract module name from error.")
            return None

        module_name = m.group(1).strip().strip("'\"")
        # Get the top-level package (e.g., "fastapi.testclient" → "fastapi")
        top_module = module_name.split(".")[0]
        
        # Map to pip package name (some modules have different pip names)
        pip_name = self.PIP_NAME_MAP.get(top_module, top_module)
        
        logger.info(f"[FixGeneratorAgent] DEPENDENCY fix: missing module '{module_name}' → pip package '{pip_name}'")

        if state.language == "python":
            return self._add_to_requirements_txt(repo_path, pip_name, top_module)
        elif state.language == "node":
            return self._add_to_package_json(repo_path, pip_name)
        
        return None

    def _add_to_requirements_txt(self, repo_path: str, pip_name: str, module_name: str):
        """Add a pip package to requirements.txt."""
        req_path = os.path.join(repo_path, "requirements.txt")
        
        # Read existing requirements
        existing = ""
        if os.path.exists(req_path):
            with open(req_path, "r", encoding="utf-8") as f:
                existing = f.read()
        
        # Check if already present (case-insensitive, ignore version specifiers)
        existing_lower = existing.lower()
        if pip_name.lower() in existing_lower:
            logger.info(f"[FixGeneratorAgent] '{pip_name}' already in requirements.txt — nothing to add.")
            return None
        
        # Append the package
        new_content = existing.rstrip() + f"\n{pip_name}\n"
        with open(req_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        
        commit_msg = f"[AI-AGENT] Fix DEPENDENCY — add '{pip_name}' to requirements.txt"
        self._git_commit(repo_path, req_path, commit_msg)
        
        logger.info(f"[FixGeneratorAgent] ✅ Added '{pip_name}' to requirements.txt")
        return FixRecord(
            file="requirements.txt",
            bug_type="DEPENDENCY",
            line=0,
            commit_message=commit_msg,
            explanation=f"Added missing pip package '{pip_name}' (module '{module_name}') to requirements.txt.",
            status="Fixed"
        )

    def _add_to_package_json(self, repo_path: str, package_name: str):
        """Add an npm package to package.json dependencies."""
        pkg_path = os.path.join(repo_path, "package.json")
        if not os.path.exists(pkg_path):
            return None
        
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            deps = data.get("dependencies", {})
            dev_deps = data.get("devDependencies", {})
            if package_name in deps or package_name in dev_deps:
                logger.info(f"[FixGeneratorAgent] '{package_name}' already in package.json.")
                return None
            
            # Add to dependencies
            if "dependencies" not in data:
                data["dependencies"] = {}
            data["dependencies"][package_name] = "*"
            
            with open(pkg_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            
            commit_msg = f"[AI-AGENT] Fix DEPENDENCY — add '{package_name}' to package.json"
            self._git_commit(repo_path, pkg_path, commit_msg)
            
            return FixRecord(
                file="package.json",
                bug_type="DEPENDENCY",
                line=0,
                commit_message=commit_msg,
                explanation=f"Added missing npm package '{package_name}' to package.json.",
                status="Fixed"
            )
        except Exception as e:
            logger.error(f"[FixGeneratorAgent] Failed to update package.json: {e}")
            return None

    # ── Structural fixes (deterministic) ──────────────────────────────────────

    def _apply_structural_fix(self, failure: dict, repo_path: str, state: SharedState):
        """Apply deterministic fixes for structural errors — no LLM needed."""
        raw = failure.get("raw_error", "") + " " + failure.get("description", "")
        raw_lower = raw.lower()

        # ESM/CJS mismatch
        if "cannot use import statement" in raw_lower or "err_require_esm" in raw_lower:
            return self._fix_esm_cjs(repo_path, state)

        # Vitest globals not defined
        if any(g in raw_lower for g in ["describe is not defined", "test is not defined",
                                         "expect is not defined", "it is not defined"]):
            return self._fix_vitest_globals(repo_path, state)

        # JSX transform
        if "unexpected token '<'" in raw_lower and state.has_jsx:
            return self._fix_jsx_transform(repo_path, state)

        # Missing export — needs LLM, fall back
        logger.info("[FixGeneratorAgent] Structural fix not handled deterministically, will try LLM.")
        return None

    def _fix_esm_cjs(self, repo_path: str, state: SharedState):
        """Fix ESM/CJS mismatch by adding transform config."""
        pkg_path = os.path.join(repo_path, "package.json")
        if not os.path.exists(pkg_path):
            return None

        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            changed = False
            # If using Jest and module system is ESM, add transform config
            if state.test_framework == "jest":
                if data.get("type") == "module":
                    # Add jest config with ESM transform
                    jest_config = data.get("jest", {})
                    if "transform" not in jest_config:
                        jest_config["transform"] = {}
                    if not jest_config["transform"]:
                        jest_config["transform"]["^.+\\.(js|jsx|ts|tsx)$"] = "babel-jest"
                    jest_config["transformIgnorePatterns"] = []
                    data["jest"] = jest_config
                    changed = True
            elif state.test_framework == "vitest":
                # Vitest handles ESM natively, but ensure config exists
                pass

            if not changed:
                # Simpler fix: just ensure "type": "module" is set if files use import
                if "type" not in data:
                    data["type"] = "module"
                    changed = True

            if changed:
                with open(pkg_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.write("\n")

                commit_msg = "[AI-AGENT] Fix STRUCTURAL ESM/CJS mismatch in package.json"
                self._git_commit(repo_path, pkg_path, commit_msg)
                return FixRecord(
                    file="package.json",
                    bug_type="STRUCTURAL",
                    line=0,
                    commit_message=commit_msg,
                    explanation="Configured module system to resolve ESM/CJS import mismatch.",
                    status="Fixed"
                )
        except Exception as e:
            logger.error(f"[FixGeneratorAgent] ESM/CJS fix failed: {e}")
        return None

    def _fix_vitest_globals(self, repo_path: str, state: SharedState):
        """Add globals: true to vitest config."""
        config_files = [
            "vitest.config.ts", "vitest.config.js", "vitest.config.mjs",
            "vite.config.ts", "vite.config.js", "vite.config.mjs",
        ]
        for cfg_name in config_files:
            cfg_path = os.path.join(repo_path, cfg_name)
            if not os.path.exists(cfg_path):
                continue
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    content = f.read()

                if "globals: true" in content or "globals:true" in content:
                    return None  # already configured

                # Insert globals: true into test config
                if "test:" in content or "test :" in content:
                    content = re.sub(
                        r'(test\s*:\s*\{)',
                        r'\1\n    globals: true,',
                        content
                    )
                elif "defineConfig" in content:
                    # Add test section
                    content = content.replace(
                        "defineConfig({",
                        "defineConfig({\n  test: {\n    globals: true,\n  },"
                    )
                else:
                    continue

                with open(cfg_path, "w", encoding="utf-8") as f:
                    f.write(content)

                commit_msg = f"[AI-AGENT] Fix STRUCTURAL vitest globals in {cfg_name}"
                self._git_commit(repo_path, cfg_path, commit_msg)
                state.vitest_globals = True
                return FixRecord(
                    file=cfg_name,
                    bug_type="STRUCTURAL",
                    line=0,
                    commit_message=commit_msg,
                    explanation="Added globals: true to vitest config so describe/test/expect are available.",
                    status="Fixed"
                )
            except Exception as e:
                logger.error(f"[FixGeneratorAgent] Vitest globals fix failed: {e}")
        return None

    def _fix_jsx_transform(self, repo_path: str, state: SharedState):
        """Fix JSX transform by updating test framework config."""
        if state.test_framework == "vitest":
            # Check if @vitejs/plugin-react is configured
            config_files = ["vitest.config.ts", "vitest.config.js", "vite.config.ts", "vite.config.js"]
            for cfg_name in config_files:
                cfg_path = os.path.join(repo_path, cfg_name)
                if not os.path.exists(cfg_path):
                    continue
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if "react" in content.lower():
                        return None  # already has react plugin

                    # Add react plugin
                    if "import" in content:
                        content = "import react from '@vitejs/plugin-react';\n" + content
                    content = content.replace(
                        "defineConfig({",
                        "defineConfig({\n  plugins: [react()],"
                    )
                    with open(cfg_path, "w", encoding="utf-8") as f:
                        f.write(content)

                    commit_msg = f"[AI-AGENT] Fix STRUCTURAL JSX transform in {cfg_name}"
                    self._git_commit(repo_path, cfg_path, commit_msg)
                    return FixRecord(
                        file=cfg_name,
                        bug_type="STRUCTURAL",
                        line=0,
                        commit_message=commit_msg,
                        explanation="Added @vitejs/plugin-react for JSX transform support.",
                        status="Fixed"
                    )
                except Exception as e:
                    logger.error(f"[FixGeneratorAgent] JSX fix failed: {e}")
        return None

    # ── Test-file helpers ──────────────────────────────────────────────────────

    def _is_test_file(self, file_rel: str) -> bool:
        """Check if a file is a test file based on common path patterns."""
        if not file_rel:
            return False
        normalized = file_rel.replace("\\", "/")
        for pattern in TEST_FILE_PATTERNS:
            if re.search(pattern, normalized, re.IGNORECASE):
                return True
        return False

    def _find_implementation_file(self, test_file_rel: str, repo_path: str,
                                   error_desc: str, state: SharedState) -> tuple:
        """Trace a test file's imports to find the implementation file being tested.
        
        Returns (impl_file_rel, impl_file_abs, impl_content) or (None, None, None).
        """
        test_abs = os.path.join(repo_path, test_file_rel)
        if not os.path.isfile(test_abs):
            return None, None, None

        try:
            with open(test_abs, "r", encoding="utf-8") as f:
                test_content = f.read()
        except Exception:
            return None, None, None

        test_dir = os.path.dirname(test_abs)
        candidates = []

        # ── JS/TS: parse import/require statements ──
        # import { X } from './path' or import X from './path'
        js_imports = re.findall(
            r"(?:import\s+.*?from\s+['\"])(\.[^'\"]+)['\"]|(?:require\s*\(\s*['\"])(\.[^'\"]+)['\"]\s*\)",
            test_content
        )
        for groups in js_imports:
            rel_path = groups[0] or groups[1]
            if not rel_path:
                continue
            # Resolve against the test file's directory
            candidate = os.path.normpath(os.path.join(test_dir, rel_path))
            # Try with various extensions
            for ext in ["", ".js", ".ts", ".jsx", ".tsx", ".mjs"]:
                full = candidate + ext
                if os.path.isfile(full):
                    impl_rel = os.path.relpath(full, repo_path).replace("\\", "/")
                    # Skip if this is also a test file or a lib
                    if not self._is_test_file(impl_rel) and "node_modules" not in impl_rel:
                        try:
                            with open(full, "r", encoding="utf-8") as f:
                                content = f.read()
                            candidates.append((impl_rel, full, content))
                        except Exception:
                            pass
                    break

        # ── Python: parse import/from statements ──
        py_imports = re.findall(
            r"from\s+(\.[\w.]+)\s+import|import\s+(\.[\w.]+)",
            test_content
        )
        for groups in py_imports:
            module_path = groups[0] or groups[1]
            if not module_path:
                continue
            # Convert relative import to file path
            parts = module_path.lstrip(".").split(".")
            dots = len(module_path) - len(module_path.lstrip("."))
            base = test_dir
            for _ in range(dots - 1):
                base = os.path.dirname(base)
            candidate = os.path.join(base, *parts) + ".py"
            if os.path.isfile(candidate):
                impl_rel = os.path.relpath(candidate, repo_path).replace("\\", "/")
                if not self._is_test_file(impl_rel):
                    try:
                        with open(candidate, "r", encoding="utf-8") as f:
                            content = f.read()
                        candidates.append((impl_rel, candidate, content))
                    except Exception:
                        pass

        if not candidates:
            return None, None, None

        # If error mentions a specific file, prefer that one
        error_lower = (error_desc or "").lower()
        for impl_rel, impl_abs, content in candidates:
            if os.path.basename(impl_rel).lower().replace(".js", "").replace(".py", "") in error_lower:
                logger.info(f"[FixGeneratorAgent] Traced test→impl: {test_file_rel} → {impl_rel} (matched error desc)")
                return impl_rel, impl_abs, content

        # Return the first non-test import (most likely the SUT)
        impl_rel, impl_abs, content = candidates[0]
        logger.info(f"[FixGeneratorAgent] Traced test→impl: {test_file_rel} → {impl_rel} (first import)")
        return impl_rel, impl_abs, content

    def _get_file_tree(self, repo_path: str, max_entries: int = 80) -> str:
        """Get a compact file tree of the repo for LLM context."""
        entries = []
        for root, dirs, files in os.walk(repo_path):
            # Skip hidden dirs, node_modules, .venv, __pycache__, .git
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                       ("node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build")]
            for fname in sorted(files):
                rel = os.path.relpath(os.path.join(root, fname), repo_path).replace("\\", "/")
                entries.append(rel)
                if len(entries) >= max_entries:
                    entries.append("... (truncated)")
                    return "\n".join(entries)
        return "\n".join(entries) if entries else "(empty)"

    # ── LLM-based fix generation ──────────────────────────────────────────────

    def _fix_failure(self, failure: dict, repo_path: str, state: SharedState):
        file_rel = failure.get("file", "")
        if not file_rel or file_rel == "unknown":
            logger.warning("[FixGeneratorAgent] Skipping fix for unknown file.")
            return None

        file_abs = os.path.join(repo_path, file_rel)
        if not os.path.isfile(file_abs):
            found = self._find_file(repo_path, file_rel)
            if not found:
                logger.warning(f"[FixGeneratorAgent] File not found: {file_rel}")
                return None
            file_abs = found
            file_rel = os.path.relpath(file_abs, repo_path).replace("\\", "/")

        try:
            with open(file_abs, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"[FixGeneratorAgent] Cannot read {file_abs}: {e}")
            return None

        bug_type = failure.get("bug_type", "")

        # ── TEST-FILE PROTECTION: Implementation-first redirection ──
        # For LOGIC and TYPE_ERROR in test files, trace imports to find
        # the implementation file and fix THAT instead.
        if self._is_test_file(file_rel) and bug_type in ("LOGIC", "TYPE_ERROR"):
            logger.info(
                f"[FixGeneratorAgent] 🛡️ Test-file protection: {file_rel} has {bug_type} error. "
                f"Tracing imports to find implementation file..."
            )
            impl_rel, impl_abs, impl_content = self._find_implementation_file(
                file_rel, repo_path, failure.get("description", ""), state
            )
            if impl_rel and impl_content:
                logger.info(f"[FixGeneratorAgent] 🔄 Redirecting fix: {file_rel} → {impl_rel}")
                return self._fix_impl_from_test(
                    failure, repo_path, state,
                    test_file_rel=file_rel, test_content=content,
                    impl_file_rel=impl_rel, impl_file_abs=impl_abs, impl_content=impl_content,
                )
            else:
                logger.warning(
                    f"[FixGeneratorAgent] Could not trace {file_rel} to implementation file. "
                    f"Attempting direct fix with implementation-first instructions."
                )

        # ── Standard fix path (implementation files, or test SYNTAX/IMPORT errors) ──
        # Build prior fix context
        prior_ctx = ""
        prior_fixes_for_file = [f for f in state.all_fixes_applied if f.file == file_rel]
        if prior_fixes_for_file:
            prior_ctx = f"\n⚠️ This file was already fixed {len(prior_fixes_for_file)} time(s) in prior iterations:"
            for pf in prior_fixes_for_file[-3:]:
                prior_ctx += f"\n  - Iteration {pf.iteration}: {pf.commit_message}"
            prior_ctx += "\nMake sure your fix does not revert or conflict with these prior changes."

        # Build test output snippet
        test_snippet = self._build_test_snippet(file_rel, state)

        # Get file tree for context
        file_tree = self._get_file_tree(repo_path)

        prompt = FIX_PROMPT.format(
            file=file_rel,
            bug_type=bug_type,
            line=failure.get("line", 0),
            description=failure.get("description", ""),
            raw_error=failure.get("raw_error", ""),
            file_content=content[:8000],
            test_output_snippet=test_snippet[:2000],
            prior_fix_context=prior_ctx,
            file_tree=file_tree[:2000],
        )

        return self._call_llm_and_apply(prompt, file_rel, file_abs, failure, repo_path, state)

    def _fix_impl_from_test(self, failure: dict, repo_path: str, state: SharedState,
                             test_file_rel: str, test_content: str,
                             impl_file_rel: str, impl_file_abs: str, impl_content: str):
        """Fix an implementation file based on a failing test.
        
        Uses IMPL_FIX_PROMPT which provides both test and impl content,
        but instructs the LLM to ONLY modify the implementation file.
        """
        # Build prior fix context for the IMPL file
        prior_ctx = ""
        prior_fixes_for_file = [f for f in state.all_fixes_applied if f.file == impl_file_rel]
        if prior_fixes_for_file:
            prior_ctx = f"\n⚠️ This implementation file was already fixed {len(prior_fixes_for_file)} time(s):"
            for pf in prior_fixes_for_file[-3:]:
                prior_ctx += f"\n  - Iteration {pf.iteration}: {pf.commit_message}"
            prior_ctx += "\nMake sure your fix does not revert or conflict with these prior changes."

        test_snippet = self._build_test_snippet(test_file_rel, state)
        file_tree = self._get_file_tree(repo_path)

        prompt = IMPL_FIX_PROMPT.format(
            impl_file=impl_file_rel,
            bug_type=failure.get("bug_type", ""),
            description=failure.get("description", ""),
            raw_error=failure.get("raw_error", ""),
            impl_content=impl_content[:8000],
            test_file=test_file_rel,
            test_content=test_content[:4000],
            test_output_snippet=test_snippet[:2000],
            prior_fix_context=prior_ctx,
            file_tree=file_tree[:2000],
        )

        return self._call_llm_and_apply(prompt, impl_file_rel, impl_file_abs, failure, repo_path, state)

    def _build_test_snippet(self, file_rel: str, state: SharedState) -> str:
        """Extract relevant test output snippet for a given file."""
        stdout = state.test_stdout or ""
        stderr = state.test_stderr or ""
        combined = f"{stdout}\n{stderr}"
        file_base = os.path.basename(file_rel)
        idx = combined.find(file_base)
        if idx >= 0:
            return combined[max(0, idx - 200):idx + 800][:1000]
        return combined[:1000]

    def _call_llm_and_apply(self, prompt: str, file_rel: str, file_abs: str,
                             failure: dict, repo_path: str, state: SharedState):
        """Call the LLM with a prompt, parse the JSON response, validate and apply the fix."""
        try:
            response = self.model.generate_content(prompt)
            text = response.text.strip()
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            fix_data = json.loads(text)
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "resource" in error_str and "exhausted" in error_str or "quota" in error_str or "rate" in error_str:
                logger.error(
                    "\n" + "=" * 60 +
                    "\n⚠️  GEMINI API RATE LIMIT REACHED  ⚠️"
                    "\n   Fix generation skipped for: " + file_rel +
                    "\n   Consider waiting or upgrading your API plan."
                    "\n" + "=" * 60
                )
                print(
                    "\n\033[93m" + "=" * 60 +
                    "\n⚠️  GEMINI API RATE LIMIT REACHED  ⚠️"
                    "\n   Fix generation skipped for: " + file_rel +
                    "\n" + "=" * 60 + "\033[0m"
                )
            else:
                logger.error(f"[FixGeneratorAgent] LLM fix generation failed for {file_rel}: {e}")
            return None

        fixed_content = fix_data.get("fixed_content", "")
        commit_message = fix_data.get("commit_message", f"[AI-AGENT] Fix {failure.get('bug_type')} in {file_rel}")
        explanation = fix_data.get("explanation", "")
        if not fixed_content:
            return None

        # ── Syntax validation gate ──
        if not self._validate_syntax(file_rel, fixed_content, state.language):
            logger.warning(f"[FixGeneratorAgent] ⚠️ Fix for {file_rel} FAILED syntax check — reverting.")
            return None

        # Write fixed file
        try:
            with open(file_abs, "w", encoding="utf-8") as f:
                f.write(fixed_content)
        except Exception as e:
            logger.error(f"[FixGeneratorAgent] Cannot write fix to {file_abs}: {e}")
            return None

        self._git_commit(repo_path, file_abs, commit_message)

        return FixRecord(
            file=file_rel,
            bug_type=failure.get("bug_type", ""),
            line=failure.get("line", 0),
            commit_message=commit_message,
            explanation=explanation,
            status="Fixed"
        )

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_syntax(self, file_rel: str, content: str, language: str) -> bool:
        """Basic syntax check to prevent fix-induced errors."""
        if language == "python" and file_rel.endswith(".py"):
            try:
                compile(content, file_rel, "exec")
                return True
            except SyntaxError as e:
                logger.warning(f"[FixGeneratorAgent] Python syntax error in fix: {e}")
                return False
        # For JS/TS — just check for obviously broken content
        if language == "node" and file_rel.endswith((".js", ".ts", ".jsx", ".tsx")):
            # Reject empty content or content that's clearly just the JSON wrapper
            if len(content.strip()) < 10:
                return False
            if content.strip().startswith("{") and '"fixed_content"' in content:
                logger.warning("[FixGeneratorAgent] LLM returned JSON wrapper instead of code content.")
                return False
        return True

    # ── Git helpers ───────────────────────────────────────────────────────────

    def _git_commit(self, repo_path: str, file_path: str, commit_message: str):
        env = {**os.environ, "GIT_AUTHOR_NAME": "AI-Agent", "GIT_AUTHOR_EMAIL": "ai@agent.local",
               "GIT_COMMITTER_NAME": "AI-Agent", "GIT_COMMITTER_EMAIL": "ai@agent.local"}
        try:
            subprocess.run(["git", "add", file_path], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=repo_path, check=True, capture_output=True, env=env
            )
            logger.info(f"[FixGeneratorAgent] Committed fix: {commit_message}")
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            logger.warning(f"[FixGeneratorAgent] Git commit failed: {stderr}")

    def _find_file(self, repo_path: str, filename: str):
        basename = os.path.basename(filename)
        for root, _, files in os.walk(repo_path):
            if basename in files:
                candidate = os.path.join(root, basename)
                if "node_modules" not in candidate and ".git" not in candidate:
                    return candidate
        return None
