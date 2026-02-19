"""
FailureClassifierAgent: Uses the Gemini LLM to classify test failures into
structured categories and extract precise error details.

v2: Added STRUCTURAL bug type for ESM/CJS, JSX, missing exports.
    Improved file extraction from stack traces.
    Includes iteration context in LLM prompt.
    Better regex patterns for Node.js / Vitest / Jest output.
    Root-cause ordering: structural > import > syntax > logic.
"""
import os
import re
import json
import logging
import google.generativeai as genai
from .shared_state import SharedState
from .llm_pool import GeminiKeyPool

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """
You are an expert CI/CD failure classifier. Analyze the test output below and classify every error.

Test Output (stdout):
{stdout}

Test Output (stderr):
{stderr}

Exit Code: {exit_code}
Language: {language}
Framework: {framework}
{iteration_context}

For each error or failure found, output a JSON array. Each item must have:
- "file": the relative file path where the error occurred (e.g., "src/main.py", "src/utils.js"). Extract from stack traces, error messages, or FAIL lines. Use "unknown" ONLY if truly unidentifiable.
- "bug_type": EXACTLY one of: STRUCTURAL | SYNTAX | IMPORT | TYPE_ERROR | LOGIC | LINTING | INDENTATION
- "line": line number as integer (0 if unknown)
- "description": concise string: "BUG_TYPE error in FILE line LINE → Fix: SUGGESTION"
- "raw_error": the exact error snippet from the output (max 200 chars)

STRUCTURAL means: ESM/CJS mismatch, missing exports, JSX transform config, vitest globals missing, etc.
These are CONFIG-level issues, not code bugs.

IMPORTANT: Always extract the ACTUAL file path from error output. Look for:
- "at Object.<anonymous> (path:line:col)"
- "FAIL path/to/file.test.js"
- 'File "path/to/file.py", line N'
- "Cannot find module 'path'" - the importing file AND the missing module

Respond ONLY with a valid JSON array. No prose, no markdown fences.
"""


class FailureClassifierAgent:
    """Classifies test failures using Gemini LLM with improved regex fallback."""

    def __init__(self):
        self.pool = GeminiKeyPool()
        if self.pool.available:
            logger.info(f"[FailureClassifierAgent] Using GeminiKeyPool with {len(self.pool.keys)} key(s)")
        else:
            logger.warning("[FailureClassifierAgent] No API keys - using regex fallback.")

    def run(self, state: SharedState) -> SharedState:
        if state.test_exit_code == 0:
            logger.info("[FailureClassifierAgent] Tests passed — no failures to classify.")
            state.classified_failures = []
            state.total_failures = 0
            return state

        stdout = state.test_stdout or ""
        stderr = state.test_stderr or ""
        combined = f"{stdout}\n{stderr}"
        logger.info(f"[FailureClassifierAgent] Classifying failures from output ({len(combined)} chars)...")

        failures = []
        if self.pool.available:
            failures = self._classify_with_llm(state)

        if not failures:
            failures = self._classify_with_regex(state)

        # Root-cause ordering: STRUCTURAL > IMPORT > SYNTAX > TYPE_ERROR > LOGIC
        priority = {"STRUCTURAL": 0, "IMPORT": 1, "SYNTAX": 2, "INDENTATION": 3, "TYPE_ERROR": 4, "LOGIC": 5, "LINTING": 6}
        failures.sort(key=lambda f: priority.get(f.get("bug_type", "LOGIC"), 5))

        state.classified_failures = failures
        state.total_failures = len(failures)
        logger.info(f"[FailureClassifierAgent] Classified {len(failures)} failure(s).")
        for f in failures:
            logger.info(f"  → [{f.get('bug_type')}] {f.get('file', '?')}:{f.get('line', 0)} — {f.get('description', '')[:80]}")
        return state

    def _classify_with_llm(self, state: SharedState):
        # Build iteration context
        iter_ctx = ""
        if state.iteration_history:
            prev_sigs = []
            for snap in state.iteration_history[-3:]:
                prev_sigs.extend(snap.failure_signatures)
            if prev_sigs:
                iter_ctx = f"\nPrevious iteration failures: {', '.join(prev_sigs[-10:])}"
            if state.files_modified:
                iter_ctx += f"\nFiles already modified by prior fixes: {', '.join(list(state.files_modified)[:10])}"
            repeated = state.get_repeated_failures(2)
            if repeated:
                iter_ctx += f"\n⚠️ REPEATED failures (need deeper root-cause analysis): {', '.join(repeated[:5])}"

        prompt = CLASSIFICATION_PROMPT.format(
            stdout=(state.test_stdout or "")[:5000],
            stderr=(state.test_stderr or "")[:5000],
            exit_code=state.test_exit_code,
            language=state.language,
            framework=state.test_framework,
            iteration_context=iter_ctx,
        )

        import time

        try:
            raw_text = self.pool.call_llm(prompt)
            raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)
            parsed = json.loads(raw_text)
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed
            logger.warning("[FailureClassifierAgent] LLM returned empty list, using regex fallback.")
        except Exception as e:
            logger.warning(f"[FailureClassifierAgent] LLM failed ({e}), falling back to regex.")
        return []

    def _classify_with_regex(self, state: SharedState):
        """Enhanced regex-based fallback classifier with robust Vitest support."""
        stdout = state.test_stdout or ""
        stderr = state.test_stderr or ""
        combined = f"{stdout}\n{stderr}"
        failures = []

        # ── STRUCTURAL errors (highest priority) ────────────────────────

        structural_patterns = [
            (r"Cannot use import statement outside a module", "STRUCTURAL",
             "ESM/CJS mismatch → add \"type\": \"module\" to package.json or configure transform"),
            (r"SyntaxError: Unexpected token '<'", "STRUCTURAL",
             "JSX transform not configured → add JSX plugin to test framework config"),
            (r"SyntaxError: Cannot use import statement", "STRUCTURAL",
             "ESM/CJS mismatch → configure transform or use require()"),
            (r"is not exported from|does not provide an export named", "STRUCTURAL",
             "Missing export → check export name matches import"),
            (r"ReferenceError: (describe|it|test|expect|beforeEach|afterEach|beforeAll|afterAll) is not defined", "STRUCTURAL",
             "Test globals not configured → add globals: true to vitest config or import from test framework"),
            (r"Unknown file extension \".ts\"", "STRUCTURAL",
             "TypeScript not configured → add ts-node or tsx transform"),
            (r"ERR_REQUIRE_ESM|Must use import", "STRUCTURAL",
             "ESM module loaded with require() → use dynamic import or configure transform"),
            (r"Failed to parse source for import analysis because the content contains invalid JS syntax", "STRUCTURAL",
             "File contains invalid JS syntax (may need .jsx extension or syntax fix)"),
        ]

        for pattern, bug_type, fix_hint in structural_patterns:
            m = re.search(pattern, combined, re.IGNORECASE)
            if m:
                file_name = self._extract_file_near(combined, m.start())
                failures.append({
                    "file": file_name,
                    "bug_type": bug_type,
                    "line": 0,
                    "description": f"{bug_type} error in {file_name} line 0 → Fix: {fix_hint}",
                    "raw_error": m.group(0)[:200]
                })

        # ── Vitest/Jest FAIL lines with detailed error extraction ─────────

        # Parse Vitest FAIL blocks from stderr:  "FAIL  path > suite > test"
        vitest_fail_pat = re.compile(
            r'FAIL\s+([\w./\\-]+\.(?:test|spec)\.(?:js|ts|jsx|tsx))\s*>\s*(.+?)\n'
            r'((?:.*?\n)*?)(?=\n\s*FAIL|\n\u2500|\Z)',
            re.MULTILINE
        )
        for m in vitest_fail_pat.finditer(combined):
            file_name = m.group(1).strip().replace("\\", "/")
            error_block = m.group(3).strip()
            bug_type, line_num, description = self._parse_error_block(error_block, file_name)
            # Don't duplicate if already covered by structural
            if not any(f["file"] == file_name and f["bug_type"] == bug_type for f in failures):
                failures.append({
                    "file": file_name,
                    "bug_type": bug_type,
                    "line": line_num,
                    "description": description,
                    "raw_error": error_block[:200]
                })

        # ── "ReferenceError: X is not defined" with stack trace ───────────
        ref_err_pat = re.compile(
            r'ReferenceError:\s+(\w+)\s+is not defined\n'
            r'.*?❯\s+([\w./\\-]+\.(?:js|ts|jsx|tsx)):(\d+):\d+',
            re.DOTALL
        )
        for m in ref_err_pat.finditer(combined):
            var_name = m.group(1)
            file_name = m.group(2).strip().replace("\\", "/")
            line_num = int(m.group(3))
            if not any(f["file"] == file_name for f in failures):
                failures.append({
                    "file": file_name,
                    "bug_type": "IMPORT",
                    "line": line_num,
                    "description": f"IMPORT error in {file_name} line {line_num} → Fix: '{var_name}' is not defined, check import statement",
                    "raw_error": m.group(0)[:200]
                })

        # ── "TypeError: X is not a function" with stack trace ─────────────
        type_err_pat = re.compile(
            r'TypeError:\s+(.+?)\s+is not a function\n'
            r'.*?❯\s+([\w./\\-]+\.(?:js|ts|jsx|tsx)):(\d+):\d+',
            re.DOTALL
        )
        for m in type_err_pat.finditer(combined):
            fn_name = m.group(1).strip()
            file_name = m.group(2).strip().replace("\\", "/")
            line_num = int(m.group(3))
            if not any(f["file"] == file_name for f in failures):
                failures.append({
                    "file": file_name,
                    "bug_type": "IMPORT",
                    "line": line_num,
                    "description": f"IMPORT error in {file_name} line {line_num} → Fix: '{fn_name}' is not a function, check import style (default vs named)",
                    "raw_error": m.group(0)[:200]
                })

        # ── Vitest assertion failures: "expected X to be Y" with ❯ file:line ─
        assert_pat = re.compile(
            r'(?:expected\s+.*?(?:to be|not to be|to equal).*?)\n'
            r'.*?❯\s+([\w./\\-]+\.(?:js|ts|jsx|tsx)):(\d+):\d+',
            re.DOTALL
        )
        for m in assert_pat.finditer(combined):
            file_name = m.group(1).strip().replace("\\", "/")
            line_num = int(m.group(2))
            if not any(f["file"] == file_name and f["line"] == line_num for f in failures):
                failures.append({
                    "file": file_name,
                    "bug_type": "LOGIC",
                    "line": line_num,
                    "description": f"LOGIC error in {file_name} line {line_num} → Fix: assertion failure, check function return value",
                    "raw_error": m.group(0)[:200]
                })

        # ── "Cannot find module" with file extraction ──────────────────
        cfm = re.compile(r"Cannot find module '([^']+)'.*?(?:from|Require stack:)\s*['\"]?([^\s'\"]+)", re.DOTALL)
        for m in cfm.finditer(combined):
            missing = m.group(1).strip()
            from_file = m.group(2).strip()
            failures.append({
                "file": from_file if from_file != "unknown" else "package.json",
                "bug_type": "IMPORT",
                "line": 0,
                "description": f"IMPORT error in {from_file} line 0 → Fix: install or fix path to '{missing}'",
                "raw_error": m.group(0)[:200]
            })

        # ── Vitest stdout: lines with ❯ showing failing file ─────────────
        # Pattern: " ❯ src/tests/foo.test.js  (N tests | M failed)"
        vitest_stdout_fail = re.compile(
            r'\u276f\s+([\w./\\-]+\.(?:test|spec)\.(?:js|ts|jsx|tsx))\s+\(.*?failed',
            re.MULTILINE
        )
        for m in vitest_stdout_fail.finditer(stdout):
            file_name = m.group(1).strip().replace("\\", "/")
            if not any(f["file"] == file_name for f in failures):
                failures.append({
                    "file": file_name,
                    "bug_type": "LOGIC",
                    "line": 0,
                    "description": f"LOGIC error in {file_name} → Fix: review failing test assertions",
                    "raw_error": m.group(0)[:200]
                })

        # ── npm ERR! ────────────────────────────────────────────────
        npm_err = re.compile(r"npm ERR!\s+(.+)", re.MULTILINE)
        for m in npm_err.finditer(combined):
            err = m.group(1).strip()
            if "errno" in err.lower() or "missing" in err.lower() or "ENOENT" in err:
                failures.append({
                    "file": "package.json",
                    "bug_type": "IMPORT",
                    "line": 0,
                    "description": f"IMPORT error in package.json line 0 → Fix: {err[:100]}",
                    "raw_error": err[:200]
                })
                break

        # ── Python patterns ─────────────────────────────────────────

        python_patterns = [
            (r"SyntaxError[:\s]+(.+)", "SYNTAX"),
            (r"IndentationError[:\s]+(.+)", "INDENTATION"),
            (r"ImportError[:\s]+(.+)", "IMPORT"),
            (r"ModuleNotFoundError[:\s]+(.+)", "IMPORT"),
            (r"TypeError[:\s]+(.+)", "TYPE_ERROR"),
            (r"NameError[:\s]+(.+)", "LOGIC"),
            (r"AttributeError[:\s]+(.+)", "LOGIC"),
            (r"AssertionError[:\s]*(.*)", "LOGIC"),
        ]
        py_file_line = re.compile(r'File "([^"]+)", line (\d+)')

        for pattern, bug_type in python_patterns:
            for m in re.finditer(pattern, combined, re.MULTILINE):
                err = (m.group(1) or "").strip()
                file_name = "unknown"
                line_num = 0
                search_region = combined[:m.start()]
                fl_matches = list(py_file_line.finditer(search_region))
                if fl_matches:
                    last = fl_matches[-1]
                    fpath = last.group(1)
                    if "/site-packages/" not in fpath and "<" not in fpath:
                        file_name = fpath
                        line_num = int(last.group(2))
                failures.append({
                    "file": file_name,
                    "bug_type": bug_type,
                    "line": line_num,
                    "description": f"{bug_type} error in {file_name} line {line_num} → Fix: {err[:80]}",
                    "raw_error": m.group(0)[:200]
                })
                if len(failures) >= 10:
                    break

        # ── Generic fallback ────────────────────────────────────────

        if not failures:
            any_file = re.search(r'([\w./\\-]+\.(?:py|js|ts|jsx|tsx|go|rb|java))', combined)
            file_name = any_file.group(1) if any_file else "unknown"
            snippet = combined.strip()[:300]
            failures.append({
                "file": file_name,
                "bug_type": "LOGIC",
                "line": 0,
                "description": f"LOGIC error in {file_name} line 0 → Fix: review full test output manually",
                "raw_error": snippet
            })
            logger.info(f"[FailureClassifierAgent] Generic fallback used. Output snippet:\n{snippet}")

        # Deduplicate by (file, bug_type, line)
        seen = set()
        unique = []
        for f in failures:
            key = (f.get("file", ""), f.get("bug_type", ""), f.get("line", 0))
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique[:10]

    # ── Helper methods ──────────────────────────────────────────────────────

    def _parse_error_block(self, error_block: str, file_name: str):
        """Parse a Vitest FAIL error block to extract bug_type, line, description."""
        line_num = 0
        # Extract line from ❯ file:line:col
        line_m = re.search(r'❯\s+[\w./\\-]+\.(?:js|ts|jsx|tsx):(\d+):\d+', error_block)
        if line_m:
            line_num = int(line_m.group(1))

        if "is not defined" in error_block:
            m = re.search(r'(\w+)\s+is not defined', error_block)
            name = m.group(1) if m else "unknown"
            return "IMPORT", line_num, f"IMPORT error in {file_name} line {line_num} → Fix: '{name}' is not defined, check import"
        elif "is not a function" in error_block:
            m = re.search(r'(.+?)\s+is not a function', error_block)
            name = m.group(1).strip() if m else "unknown"
            return "IMPORT", line_num, f"IMPORT error in {file_name} line {line_num} → Fix: '{name}' is not a function, check import style"
        elif "expected" in error_block.lower() and ("to be" in error_block.lower() or "to equal" in error_block.lower()):
            return "LOGIC", line_num, f"LOGIC error in {file_name} line {line_num} → Fix: assertion failure, check function logic/return value"
        elif "SyntaxError" in error_block:
            return "SYNTAX", line_num, f"SYNTAX error in {file_name} line {line_num} → Fix: check syntax"
        elif "TypeError" in error_block:
            return "TYPE_ERROR", line_num, f"TYPE_ERROR in {file_name} line {line_num} → Fix: check types"
        else:
            return "LOGIC", line_num, f"LOGIC error in {file_name} line {line_num} → Fix: review error details"

    def _extract_file_near(self, text: str, pos: int) -> str:
        """Try to find a file path near position `pos` in text."""
        region = text[max(0, pos - 500):pos + 500]
        # Look for common file path patterns
        patterns = [
            re.compile(r'(?:FAIL|at)\s+([\w./\\-]+\.(?:test|spec)\.(?:js|ts|jsx|tsx))'),
            re.compile(r'([\w./\\-]+\.(?:js|ts|jsx|tsx|py|go|rb)):\d+'),
            re.compile(r'File "([^"]+)"'),
            re.compile(r'from ["\']([^"\']+)["\']'),
        ]
        for pat in patterns:
            m = pat.search(region)
            if m:
                return m.group(1).replace("\\", "/")
        return "unknown"

    def _extract_file_from_block(self, block: str) -> str:
        """Extract file path from an error block."""
        patterns = [
            re.compile(r'at\s+\S+\s+\(([^:)]+):\d+:\d+\)'),
            re.compile(r'([\w./\\-]+\.(?:js|ts|jsx|tsx|py)):\d+'),
            re.compile(r'File "([^"]+)"'),
        ]
        for pat in patterns:
            m = pat.search(block)
            if m:
                fpath = m.group(1)
                if "node_modules" not in fpath:
                    return fpath.replace("\\", "/")
        return "unknown"

    def _extract_line_from_block(self, block: str) -> int:
        """Extract line number from an error block."""
        m = re.search(r':(\d+):\d+', block)
        if m:
            return int(m.group(1))
        m = re.search(r'line (\d+)', block, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return 0

    def _extract_assertion_detail(self, combined: str, file_name: str) -> str:
        """Try to extract assertion details for a failing test file."""
        # Look for "Expected X, Received Y" near the file name
        idx = combined.find(file_name)
        if idx == -1:
            return ""
        region = combined[idx:idx + 1000]
        m = re.search(r'(Expected.*?Received.*?)(?:\n\n|\n\s*at\s)', region, re.DOTALL)
        if m:
            detail = m.group(1).strip()[:100]
            return f"LOGIC error in {file_name} → Fix: {detail}"
        return ""
