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
from .shared_state import SharedState, FixRecord
from .llm_pool import GeminiKeyPool

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
{related_files_section}
Test Output (relevant snippet):
```
{test_output_snippet}
```
{prior_fix_context}

Instructions:
1. Return ONLY a JSON object with the following fields:
   - "fixed_content": the complete fixed file content as a string
   - "commit_message": a git commit message starting with "[AI-AGENT]" describing the fix
   - "explanation": one sentence explaining the change
2. Make MINIMAL changes — only fix the identified bug.
3. Preserve all indentation, formatting, and coding style.
4. Do NOT add comments unless they were already there.
5. Do NOT restructure the file or change unrelated code.
6. CRITICAL: If a SOURCE file (not a test file) has a function that is missing a `return` statement, ADD the `return` statement to the source file. Do NOT modify the test assertions to expect `undefined`.
7. If the file IS a test file and the error is about imports (function not found, not a function, wrong module path), fix the import statement — do NOT rewrite the test logic.
8. For IMPORT errors: Look at the Related Files section above to see how the imported module actually exports its functions. Use the EXACT export style (default vs named) shown in the related file.
9. Return ONLY the JSON object — no markdown, no prose.

Example response:
{{
  "fixed_content": "...",
  "commit_message": "[AI-AGENT] Fix SYNTAX error in calculator.py line 8 - add colon",
  "explanation": "Added missing colon at end of function definition on line 8."
}}
"""


class FixGeneratorAgent:
    """Generates and applies code fixes using Gemini LLM with structural handlers."""

    def __init__(self):
        self.pool = GeminiKeyPool()
        if self.pool.available:
            logger.info(f"[FixGeneratorAgent] Using GeminiKeyPool with {len(self.pool.keys)} key(s)")
        else:
            logger.warning("[FixGeneratorAgent] No Gemini API keys - fixes will be skipped.")

    def run(self, state: SharedState) -> SharedState:
        if not state.classified_failures:
            logger.info("[FixGeneratorAgent] No failures to fix.")
            state.fixes_applied = []
            state.total_fixes = 0
            return state

        fixes_applied = []
        repo_path = state.repo_path

        for failure in state.classified_failures:
            # ── Skip oscillating failures ─────────────────────────────────
            sig = f"{failure.get('file', '?')}:{failure.get('bug_type', '?')}"
            sig_count = state.failure_fingerprints.get(sig, 0)
            if sig_count >= 3:
                logger.warning(
                    f"[FixGeneratorAgent] ⚠️ Skipping oscillating failure {sig} "
                    f"(seen {sig_count} times). LLM cannot fix this — needs manual intervention."
                )
                continue

            # Try structural fix first (deterministic, no LLM needed)
            if failure.get("bug_type") == "STRUCTURAL":
                fix_record = self._apply_structural_fix(failure, repo_path, state)
                if fix_record:
                    fix_record.iteration = state.current_iteration
                    fixes_applied.append(fix_record)
                    continue

            if not self.pool.available:
                logger.warning("[FixGeneratorAgent] Skipping LLM fix - no API keys available.")
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

        # ── Build related files section for IMPORT errors ──────────────
        related_section = ""
        bug_type = failure.get("bug_type", "")
        if bug_type in ("IMPORT", "TYPE_ERROR", "LOGIC"):
            related_files = self._find_related_files(content, file_abs, repo_path)
            if related_files:
                related_section = "\n\nRelated Files (imported modules — check their exports):" 
                for rf_path, rf_content in related_files.items():
                    related_section += f"\n\n--- {rf_path} ---\n```\n{rf_content[:3000]}\n```"

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

        prompt = FIX_PROMPT.format(
            file=file_rel,
            bug_type=failure.get("bug_type", ""),
            line=failure.get("line", 0),
            description=failure.get("description", ""),
            raw_error=failure.get("raw_error", ""),
            file_content=content[:8000],
            related_files_section=related_section,
            test_output_snippet=test_snippet[:2000],
            prior_fix_context=prior_ctx,
        )

        # ─── LLM CALL VIA KEY POOL ────────────────────────────────────────────
        try:
            raw_text = self.pool.call_llm(prompt)
            raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)
            fix_data = json.loads(raw_text)
        except Exception as e:
            logger.error(f"[FixGeneratorAgent] LLM call failed for {file_rel}: {e}")
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

    def _find_related_files(self, content: str, file_abs: str, repo_path: str) -> dict:
        """
        For IMPORT errors: find the files that the current file imports from
        and return their content so the LLM can see the actual exports.
        """
        related = {}
        # Match ESM imports: import { foo } from './bar'  or  import foo from '../utils/bar.js'
        import_re = re.compile(r"(?:import|from)\s+.*?['\"]([./][^'\"]+)['\"]")
        # Match CJS requires: require('./bar')
        require_re = re.compile(r"require\(['\"]([./][^'\"]+)['\"]\)")

        dir_of_file = os.path.dirname(file_abs)

        for pattern in [import_re, require_re]:
            for m in pattern.finditer(content):
                rel_import = m.group(1)
                # Resolve the import path
                candidates = [rel_import]
                if not os.path.splitext(rel_import)[1]:
                    # No extension — try common ones
                    candidates = [
                        rel_import + ext
                        for ext in [".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"]
                    ]
                    candidates.append(os.path.join(rel_import, "index.js"))

                for candidate in candidates:
                    abs_path = os.path.normpath(os.path.join(dir_of_file, candidate))
                    if os.path.isfile(abs_path) and "node_modules" not in abs_path:
                        try:
                            with open(abs_path, "r", encoding="utf-8") as f:
                                rel_path = os.path.relpath(abs_path, repo_path).replace("\\", "/")
                                related[rel_path] = f.read()
                        except Exception:
                            pass
                        break  # found it, no need to try other extensions

        return related
