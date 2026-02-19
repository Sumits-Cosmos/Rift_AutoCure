"""
FailureClassifierAgent: Uses the LLM (Gemini or Grok) to classify test failures
into structured categories and extract precise error details.
Includes an improved regex fallback with Vitest / Jest / Python support.
"""
import re
import json
import logging
from .shared_state import SharedState
from ..llm_client import get_llm_client

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """
You are an expert CI/CD failure classifier. Analyze the test output below and classify every error.

Test Output (stdout):
{stdout}

Test Output (stderr):
{stderr}

Exit Code: {exit_code}

For each error or failure found, output a JSON array. Each item must have:
- "file": the filename where the error occurred (e.g., "src/main.py"). Use "unknown" if not determinable.
- "bug_type": EXACTLY one of: LINTING | SYNTAX | LOGIC | TYPE_ERROR | IMPORT | INDENTATION
- "line": line number as integer (0 if unknown)
- "description": short string in format: "BUG_TYPE error in FILE line LINE → Fix: SUGGESTION"
- "raw_error": the exact error snippet from the output (max 200 chars)

If the output shows "No tests found" or "no test files", classify it as:
[{{"file": "unknown", "bug_type": "LOGIC", "line": 0, "description": "LOGIC error in unknown line 0 → Fix: add test files to the repository", "raw_error": "No test files found"}}]

Respond ONLY with a valid JSON array. No prose, no markdown fences, no explanation.
"""


class FailureClassifierAgent:
    """Classifies test failures using LLM with improved regex fallback."""

    def __init__(self):
        self.client = get_llm_client()
        if not self.client:
            logger.warning("[FailureClassifierAgent] No LLM client available — using regex fallback only.")

    def run(self, state: SharedState) -> SharedState:
        if state.test_exit_code == 0:
            logger.info("[FailureClassifierAgent] Tests passed — no failures to classify.")
            state.classified_failures = []
            state.total_failures = 0
            return state

        combined = f"{state.test_stdout}\n{state.test_stderr}"
        logger.info(f"[FailureClassifierAgent] Classifying failures from output ({len(combined)} chars)...")

        if self.client:
            failures = self._classify_with_llm(state)
        else:
            failures = self._classify_with_regex(state)

        if not failures:
            failures = self._classify_with_regex(state)

        state.classified_failures = failures
        state.total_failures = len(failures)
        logger.info(f"[FailureClassifierAgent] Classified {len(failures)} failure(s).")
        for f in failures:
            logger.info(f"  → {f.get('description', 'no description')}")
        return state

    def _classify_with_llm(self, state: SharedState):
        prompt = CLASSIFICATION_PROMPT.format(
            stdout=state.test_stdout[:4000],
            stderr=state.test_stderr[:4000],
            exit_code=state.test_exit_code
        )
        try:
            text = self.client.generate(prompt)
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            parsed = json.loads(text)
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed
            logger.warning("[FailureClassifierAgent] LLM returned empty list, using regex fallback.")
            return self._classify_with_regex(state)
        except Exception as e:
            logger.warning(f"[FailureClassifierAgent] LLM failed ({e}), falling back to regex.")
            return self._classify_with_regex(state)

    def _classify_with_regex(self, state: SharedState):
        """Enhanced regex-based fallback classifier supporting Python + Node.js (Jest + Vitest) output."""
        combined = f"{state.test_stdout}\n{state.test_stderr}"
        failures = []

        # ──────────────────────────────────────────────
        # Vitest-specific patterns
        # ──────────────────────────────────────────────

        # Vitest FAIL line in stderr: " FAIL  src/tests/file.test.js [ src/tests/file.test.js ]"
        vitest_fail_pat = re.compile(
            r"FAIL\s+([\w/\\.]+\.(?:test|spec)\.(?:js|ts|jsx|tsx))\s*(?:\[|$)",
            re.MULTILINE
        )
        for m in vitest_fail_pat.finditer(combined):
            file_name = m.group(1).strip()
            failures.append({
                "file": file_name,
                "bug_type": "SYNTAX",
                "line": 0,
                "description": f"SYNTAX error in {file_name} line 0 → Fix: check for invalid JS/TS syntax or JSX extension",
                "raw_error": m.group(0)[:200]
            })

        # Vitest assertion failures: "expected X to be Y" — extract file from "❯ src/tests/file.test.js"
        vitest_assert_pat = re.compile(
            r"❯\s+([\w/\\.]+\.(?:test|spec)\.(?:js|ts|jsx|tsx))\s*[:\s]*(\d+)?",
            re.MULTILINE
        )
        for m in vitest_assert_pat.finditer(combined):
            file_name = m.group(1).strip()
            line_num = int(m.group(2)) if m.group(2) else 0
            failures.append({
                "file": file_name,
                "bug_type": "LOGIC",
                "line": line_num,
                "description": f"LOGIC error in {file_name} line {line_num} → Fix: review failing test assertions",
                "raw_error": m.group(0)[:200]
            })

        # Vitest: "Failed to parse source for import analysis" + file from FAIL line
        parse_fail_pat = re.compile(
            r"Failed to parse source for import analysis.*?invalid JS syntax",
            re.DOTALL
        )
        if parse_fail_pat.search(combined):
            # The file is typically in the preceding FAIL line — already captured above
            # But also look for source file references
            source_ref = re.compile(
                r"FAIL\s+([\w/\\.]+\.(?:js|ts|jsx|tsx))\s*\[\s*([\w/\\.]+\.(?:js|ts|jsx|tsx))\s*\]",
                re.MULTILINE
            )
            for m in source_ref.finditer(combined):
                file_name = m.group(1).strip()
                failures.append({
                    "file": file_name,
                    "bug_type": "SYNTAX",
                    "line": 0,
                    "description": f"SYNTAX error in {file_name} line 0 → Fix: file contains invalid JS syntax, check for JSX or missing extension",
                    "raw_error": f"Failed to parse source for import analysis in {file_name}"[:200]
                })

        # Vitest "expected undefined to be X" — try to find source file from test file imports
        vitest_undefined_pat = re.compile(
            r"([\w/\\.]+\.(?:test|spec)\.(?:js|ts|jsx|tsx))\s*>\s*.*?>\s*.*?\n\s*→\s*expected\s+(\S+)\s+to\s+be",
            re.MULTILINE
        )
        for m in vitest_undefined_pat.finditer(combined):
            file_name = m.group(1).strip()
            if not any(f["file"] == file_name for f in failures):
                failures.append({
                    "file": file_name,
                    "bug_type": "LOGIC",
                    "line": 0,
                    "description": f"LOGIC error in {file_name} line 0 → Fix: function returns undefined, check implementation",
                    "raw_error": m.group(0)[:200]
                })

        # ──────────────────────────────────────────────
        # Node / Jest specific patterns
        # ──────────────────────────────────────────────
        # "No tests found" variants
        no_tests_patterns = [
            r"no tests found",
            r"no test files found",
            r"Your test suite must contain at least one test",
            r"Cannot find module",
            r"jest: command not found",
        ]
        for pat in no_tests_patterns:
            if re.search(pat, combined, re.IGNORECASE):
                failures.append({
                    "file": "unknown",
                    "bug_type": "IMPORT",
                    "line": 0,
                    "description": f"IMPORT error in unknown line 0 → Fix: {re.search(pat, combined, re.IGNORECASE).group(0)}",
                    "raw_error": re.search(pat, combined, re.IGNORECASE).group(0)[:200]
                })

        # Jest FAIL line: "FAIL src/foo.test.js"
        jest_fail_file_pat = re.compile(r"^(?:FAIL|●)\s+([\w/\\.]+\.(?:test|spec)\.\w+)", re.MULTILINE)
        for m in jest_fail_file_pat.finditer(combined):
            file_name = m.group(1).strip()
            if not any(f["file"] == file_name for f in failures):
                failures.append({
                    "file": file_name,
                    "bug_type": "LOGIC",
                    "line": 0,
                    "description": f"LOGIC error in {file_name} line 0 → Fix: review failing test assertions",
                    "raw_error": m.group(0)[:200]
                })

        # Jest "● Test suite failed to run" — module or syntax problem
        suite_fail = re.compile(r"● Test suite failed to run\s*\n\s*(.+)", re.MULTILINE)
        for m in suite_fail.finditer(combined):
            err = m.group(1).strip()
            bt = "IMPORT" if "Cannot find" in err or "SyntaxError" not in err else "SYNTAX"
            failures.append({
                "file": "unknown",
                "bug_type": bt,
                "line": 0,
                "description": f"{bt} error in unknown line 0 → Fix: {err[:100]}",
                "raw_error": err[:200]
            })

        # npm ERR!
        npm_err = re.compile(r"npm ERR! (.+)", re.MULTILINE)
        for m in npm_err.finditer(combined):
            err = m.group(1).strip()
            if "errno" in err.lower() or "missing" in err.lower():
                failures.append({
                    "file": "package.json",
                    "bug_type": "IMPORT",
                    "line": 0,
                    "description": f"IMPORT error in package.json line 0 → Fix: {err[:100]}",
                    "raw_error": err[:200]
                })

        # ──────────────────────────────────────────────
        # Python patterns
        # ──────────────────────────────────────────────
        python_patterns = [
            (r"SyntaxError[:\s]+(.+)", "SYNTAX"),
            (r"IndentationError[:\s]+(.+)", "INDENTATION"),
            (r"ImportError[:\s]+(.+)|ModuleNotFoundError[:\s]+(.+)", "IMPORT"),
            (r"TypeError[:\s]+(.+)", "TYPE_ERROR"),
            (r"NameError[:\s]+(.+)|AttributeError[:\s]+(.+)", "LOGIC"),
        ]
        file_line_pat = re.compile(r'(?:File "([^"]+)"|(\S+\.py)):.*?line (\d+)', re.DOTALL)

        for pattern, bug_type in python_patterns:
            for m in re.finditer(pattern, combined, re.MULTILINE):
                err = (m.group(1) or "").strip()
                fl = file_line_pat.search(combined)
                file_name = (fl.group(1) or fl.group(2) if fl else "unknown") or "unknown"
                line_num = int(fl.group(3)) if fl else 0
                failures.append({
                    "file": file_name,
                    "bug_type": bug_type,
                    "line": line_num,
                    "description": f"{bug_type} error in {file_name} line {line_num} → Fix: {err[:80]}",
                    "raw_error": m.group(0)[:200]
                })
                if len(failures) >= 10:
                    break

        # ──────────────────────────────────────────────
        # Generic fallback if nothing matched
        # ──────────────────────────────────────────────
        if not failures:
            snippet = combined.strip()[:300]
            failures.append({
                "file": "unknown",
                "bug_type": "LOGIC",
                "line": 0,
                "description": "LOGIC error in unknown line 0 → Fix: review full test output manually",
                "raw_error": snippet
            })
            logger.info(f"[FailureClassifierAgent] Generic fallback used. Output snippet:\n{snippet}")

        # Deduplicate by raw_error
        seen = set()
        unique = []
        for f in failures:
            key = f["raw_error"][:80]
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique[:10]
