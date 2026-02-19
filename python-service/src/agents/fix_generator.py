"""
FixGeneratorAgent: Uses the Ollama LLM (local) to generate minimal code patches
for classified failures and commits them to the local branch.

v2: Context-aware prompts (test output + prior fixes), deterministic
    structural fix handlers, syntax validation gate, cumulative tracking.
v3: Migrated from Gemini to Ollama local inference.
"""
import os
import re
import json
import logging
import subprocess
from dotenv import load_dotenv
from .shared_state import SharedState, FixRecord
from ..llm.ollama_client import get_client as get_ollama_client
from ..ollama_generator import build_healing_prompt

load_dotenv()
logger = logging.getLogger(__name__)


class FixGeneratorAgent:
    """Generates and applies code fixes using Ollama LLM with structural handlers."""

    def __init__(self):
        self.llm = get_ollama_client()
        if self.llm.is_available():
            logger.info(f"[FixGeneratorAgent] Using Ollama model: {self.llm.model}")
        else:
            logger.warning("[FixGeneratorAgent] Ollama not available — LLM fixes will be skipped.")

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
            file_path = failure.get("file", "")
            line_num = failure.get("line", 0)
            
            # DEDUPLICATION: Check if we already attempted this exact fix
            if self._is_fix_already_attempted(state, file_path, bug_type, line_num):
                logger.warning(
                    f"[FixGeneratorAgent] Skipping {bug_type} fix for {file_path}:{line_num} "
                    f"— already attempted in prior iterations. Preventing oscillation."
                )
                continue

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

            if not self.llm.is_available():
                logger.warning("[FixGeneratorAgent] Skipping LLM fix — Ollama not available.")
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

    def _is_fix_already_attempted(self, state: SharedState, file_path: str, bug_type: str, line_num: int) -> bool:
        """Check if this exact fix has already been attempted in prior iterations.
        
        Returns True if the same file+bug_type+line combination was already fixed
        before, indicating we should skip it to prevent oscillation loops.
        """
        for prior_fix in state.all_fixes_applied:
            if (prior_fix.file == file_path and 
                prior_fix.bug_type == bug_type and 
                prior_fix.line == line_num):
                return True
        return False
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
        """Fix DEPENDENCY errors by adding missing package to requirements/package.json.
        
        This is deterministic — no LLM needed. The error message tells us
        exactly which module is missing, so we add it to the dependency file.
        """
        raw = failure.get("raw_error", "") + " " + failure.get("description", "")
        file_target = failure.get("file", "")

        # ── Node.js: handle network/npm dependency errors ──
        if state.language == "node" or file_target == "package.json":
            # Try to extract package name from npm errors
            # Patterns: "Cannot find module 'xxx'", "jest", "vitest", etc.
            npm_match = re.search(r"Cannot find module '([^']+)'", raw)
            if npm_match:
                package = npm_match.group(1).split("/")[0]  # @scope/pkg → @scope
                if package.startswith("."):
                    return None  # relative import, not a package issue
                return self._add_to_package_json(repo_path, package, dev=True)

            # Check if error mentions a known test framework
            test_frameworks = ["jest", "vitest", "mocha", "jasmine", "@testing-library/react",
                              "@testing-library/jest-dom"]
            desc_lower = raw.lower()
            for fw in test_frameworks:
                if fw in desc_lower:
                    return self._add_to_package_json(repo_path, fw, dev=True)

            # Generic npm module extraction
            m = re.search(r"Module not found.*'([^']+)'", raw)
            if m:
                return self._add_to_package_json(repo_path, m.group(1).split("/")[0], dev=True)

            logger.warning("[FixGeneratorAgent] DEPENDENCY: Could not extract npm package from error.")
            return None

        # ── Python: handle "No module named" errors ──
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

        return self._add_to_requirements_txt(repo_path, pip_name, top_module)

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

    def _add_to_package_json(self, repo_path: str, package_name: str, dev: bool = False):
        """Add an npm package to package.json dependencies or devDependencies."""
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
            
            # Add to devDependencies for test frameworks, dependencies for everything else
            target_key = "devDependencies" if dev else "dependencies"
            if target_key not in data:
                data[target_key] = {}
            data[target_key][package_name] = "*"
            
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

    # ── File tree helper ───────────────────────────────────────────────────────
    
    def _get_repo_file_tree(self, repo_path: str, max_files: int = 80) -> str:
        """Get a compact file tree of the repo for LLM context.
        
        Helps the LLM know which files actually exist — critical for
        IMPORT error fixes where the model needs to know correct paths.
        """
        skip_dirs = {
            "node_modules", ".git", "__pycache__", ".next", "dist", "build",
            ".venv", "venv", "coverage", ".nyc_output", ".cache",
        }
        files = []
        for root, dirs, filenames in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in filenames:
                rel = os.path.relpath(os.path.join(root, fname), repo_path).replace("\\", "/")
                files.append(rel)
                if len(files) >= max_files:
                    break
            if len(files) >= max_files:
                break
        return "\n".join(f"  {f}" for f in sorted(files))

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

        # Build prior fix context
        prior_ctx = ""
        prior_fixes_for_file = [f for f in state.all_fixes_applied if f.file == file_rel]
        if prior_fixes_for_file:
            prior_ctx = f"\n⚠️ This file was already fixed {len(prior_fixes_for_file)} time(s) in prior iterations:"
            for pf in prior_fixes_for_file[-3:]:
                prior_ctx += f"\n  - Iteration {pf.iteration}: {pf.commit_message}"
            prior_ctx += "\nMake sure your fix does not revert or conflict with these prior changes."

        # Build test output snippet
        test_snippet = ""
        stdout = state.test_stdout or ""
        stderr = state.test_stderr or ""
        combined = f"{stdout}\n{stderr}"
        # Try to find relevant section for this file
        file_base = os.path.basename(file_rel)
        idx = combined.find(file_base)
        if idx >= 0:
            test_snippet = combined[max(0, idx - 200):idx + 800][:1000]
        else:
            test_snippet = combined[:1000]

        # Build file tree context — always include for visibility
        bug_type = failure.get("bug_type", "")
        repo_tree = self._get_repo_file_tree(repo_path)

        # Use the centralized healing prompt from ollama_generator
        prompt = build_healing_prompt(
            file=file_rel,
            bug_type=bug_type,
            line=failure.get("line", 0),
            description=failure.get("description", ""),
            raw_error=failure.get("raw_error", ""),
            file_content=content[:8000],
            test_output=test_snippet[:2000],
            repo_structure=repo_tree,
            prior_fixes=prior_ctx or "None",
            iteration=state.current_iteration,
        )

        # Attempt 1: Use json_mode for guaranteed JSON output
        try:
            text = self.llm.generate(prompt, json_mode=True, temperature=0.1)
            if not text:
                logger.error(f"[FixGeneratorAgent] LLM returned empty for {file_rel}")
                return None
            
            fix_data = self._extract_json_from_response(text)
            
            # If json_mode still didn't produce valid fix data, retry with a forceful short prompt
            if not fix_data or not fix_data.get("fixed_content"):
                logger.warning(f"[FixGeneratorAgent] First attempt failed for {file_rel}, retrying with simplified prompt...")
                retry_prompt = (
                    f"Fix this {bug_type} bug in {file_rel}.\n\n"
                    f"Error: {failure.get('description', '')}\n\n"
                    f"Current file content:\n{content[:6000]}\n\n"
                    f"Return a JSON object with: \"fixed_content\" (the complete fixed file as a string), "
                    f"\"commit_message\" (starting with [AI-AGENT]), \"explanation\" (one sentence)."
                )
                text2 = self.llm.generate(retry_prompt, json_mode=True, temperature=0.1)
                if text2:
                    fix_data = self._extract_json_from_response(text2)
            
            if not fix_data:
                logger.error(f"[FixGeneratorAgent] Could not extract JSON fix for {file_rel} after retries")
                logger.debug(f"[FixGeneratorAgent] Raw LLM response: {text[:500]}")
                return None
        except Exception as e:
            logger.error(f"[FixGeneratorAgent] LLM fix generation failed for {file_rel}: {e}")
            return None

        fixed_content = self._clean_fixed_content(fix_data.get("fixed_content", ""))
        commit_message = fix_data.get("commit_message", f"[AI-AGENT] Fix {failure.get('bug_type')} in {file_rel}")
        explanation = fix_data.get("explanation", "")
        if not fixed_content:
            logger.warning(f"[FixGeneratorAgent] fixed_content empty after cleaning for {file_rel}")
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

    # ── Response parsing helpers ───────────────────────────────────────────────

    def _extract_json_from_response(self, text: str) -> dict | None:
        """Robustly extract a JSON object from an LLM response.
        
        Handles common issues with smaller models:
        - Response wrapped in markdown code fences
        - Extra text before/after the JSON
        - Mixed markdown and JSON
        """
        text = text.strip()

        # Strategy 1: Direct parse (ideal case)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Strip markdown code fences and retry
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', text, flags=re.MULTILINE)
        cleaned = re.sub(r'\n?```\s*$', '', cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Strategy 3: Find the outermost { ... } in the text
        brace_start = text.find('{')
        if brace_start >= 0:
            # Find matching closing brace
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[brace_start:i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            pass
                        break
        # Strategy 4: JSON repair — fix common small-model issues
        repaired = self._repair_json(text)
        if repaired is not None:
            return repaired

        logger.warning(f"[FixGeneratorAgent] All JSON extraction strategies failed. "
                       f"Response preview: {text[:200]}")
        return None

    def _clean_fixed_content(self, content: str) -> str:
        """Clean fixed_content field from LLM response.
        
        Smaller models often wrap code in markdown fences even inside JSON:
        "fixed_content": "```python\\ndef foo():\\n    pass\\n```"
        
        This strips those fences to get clean source code.
        """
        if not content:
            return ""
        
        # Strip leading/trailing whitespace
        content = content.strip()
        
        # Remove leading markdown code fence: ```python, ```py, ```javascript, etc.
        content = re.sub(r'^```[a-zA-Z]*\s*\n?', '', content)
        # Remove trailing fence
        content = re.sub(r'\n?```\s*$', '', content)
        
        return content.strip()

    def _repair_json(self, text: str):
        """Attempt to repair malformed JSON from small models."""
        start = -1
        for ch in ['{', '[']:
            idx = text.find(ch)
            if idx >= 0 and (start < 0 or idx < start):
                start = idx
        if start < 0:
            return None

        raw = text[start:]
        raw = re.sub(r'\n?```\s*$', '', raw)
        raw = re.sub(r',\s*([}\]])', r'\1', raw)

        open_braces = raw.count('{') - raw.count('}')
        open_brackets = raw.count('[') - raw.count(']')

        if open_braces > 0 or open_brackets > 0:
            last_close = max(raw.rfind('}'), raw.rfind(']'))
            if last_close > 0:
                attempt = raw[:last_close + 1]
                ob = attempt.count('{') - attempt.count('}')
                obr = attempt.count('[') - attempt.count(']')
                attempt += '}' * max(0, ob) + ']' * max(0, obr)
                try:
                    return json.loads(attempt)
                except json.JSONDecodeError:
                    pass

            raw += '"' if raw.count('"') % 2 != 0 else ''
            raw += '}' * max(0, open_braces)
            raw += ']' * max(0, open_brackets)

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

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
