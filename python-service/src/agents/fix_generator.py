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

Instructions:
1. Return ONLY a JSON object with the following fields:
   - "fixed_content": the complete fixed file content as a string
   - "commit_message": a git commit message starting with "[AI-AGENT]" describing the fix
   - "explanation": one sentence explaining the change
2. Make MINIMAL changes — only fix the identified bug.
3. Preserve all indentation, formatting, and coding style.
4. Do NOT add comments unless they were already there.
5. Do NOT restructure the file or change unrelated code.
6. If the file is a test file, fix the test — do NOT rewrite it from scratch.
7. Return ONLY the JSON object — no markdown, no prose.

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
            # Try structural fix first (deterministic, no LLM needed)
            if failure.get("bug_type") == "STRUCTURAL":
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
            test_output_snippet=test_snippet[:2000],
            prior_fix_context=prior_ctx,
        )

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
