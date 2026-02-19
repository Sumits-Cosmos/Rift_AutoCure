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
from dotenv import load_dotenv
from .shared_state import SharedState

load_dotenv()
logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """
You are an expert CI/CD failure classifier for the RIFT 2026 autonomous DevOps healing agent.
Your job is to analyze raw test output (stdout + stderr) and classify every distinct error into structured categories so the fix-generator agent can apply precise, targeted patches.

═══════════════════════════════════════════════════════
 TEST OUTPUT TO ANALYZE
═══════════════════════════════════════════════════════

STDOUT:
{stdout}

STDERR:
{stderr}

Exit Code: {exit_code}
Language: {language}
Test Framework: {framework}
{iteration_context}

═══════════════════════════════════════════════════════
 BUG TYPE TAXONOMY (use EXACTLY these categories)
═══════════════════════════════════════════════════════

1. LINTING — Code quality / style issues
   Triggers: unused imports, unused variables, naming violations, dead code
   Python examples:
     - "F401 'os' imported but unused"
     - "W0611: Unused import os"
     - flake8/pylint warnings
   JS examples:
     - "'React' is defined but never used"
     - "no-unused-vars"
     - eslint warnings

2. SYNTAX — Malformed code that won't parse
   Triggers: missing colons, brackets, semicolons, commas, quotes
   Python examples:
     - "SyntaxError: expected ':'"
     - "SyntaxError: invalid syntax"
     - "SyntaxError: EOL while scanning string literal"
     - "SyntaxError: unexpected EOF while parsing"
   JS examples:
     - "SyntaxError: Unexpected token"
     - "SyntaxError: Missing semicolon"
     - "SyntaxError: Unexpected end of input"

3. LOGIC — Code that parses but produces wrong results
   Triggers: assertion failures, wrong return values, incorrect calculations, flawed conditions, off-by-one
   Python examples:
     - "AssertionError: assert 42 == 0"
     - "AssertionError: expected True but got False"
     - "FAILED test_calculate - assert add(2, 3) == 5"
   JS examples:
     - "Expected: 42, Received: 0"
     - "expect(result).toBe(5) — received 3"
     - "AssertionError: expected 'hello' to equal 'world'"

4. TYPE_ERROR — Wrong types at runtime
   Triggers: type mismatches, calling methods on None/undefined, wrong argument types
   Python examples:
     - "TypeError: unsupported operand type(s) for +: 'int' and 'str'"
     - "TypeError: 'NoneType' object is not subscriptable"
     - "TypeError: missing 1 required positional argument"
   JS examples:
     - "TypeError: Cannot read properties of undefined"
     - "TypeError: X is not a function"
     - "TypeError: Cannot convert undefined to object"

5. IMPORT — Import/require resolution failures
   Triggers: missing imports, wrong paths, circular imports
   Python examples:
     - "ImportError: cannot import name 'process_data' from 'utils'"
     - "NameError: name 'json' is not defined" (forgot `import json`)
   JS examples:
     - "Cannot find module './utils'" (wrong relative path)
     - "Module not found: Can't resolve 'lodash'" (might actually be DEPENDENCY)
   IMPORTANT: Distinguish from DEPENDENCY — IMPORT means the module EXISTS in the project
   but the import statement is wrong. DEPENDENCY means the pip/npm PACKAGE is not installed.

6. INDENTATION — Whitespace structure errors (mostly Python)
   Triggers: wrong indentation levels, mixed tabs/spaces
   Python examples:
     - "IndentationError: unexpected indent"
     - "IndentationError: expected an indented block"
     - "TabError: inconsistent use of tabs and spaces"
   JS: Rare, but possible in YAML configs or template literals

7. DEPENDENCY — Missing pip/npm package (NOT installed at all)
   Triggers: ModuleNotFoundError (Python), "Cannot find module" for an npm package
   Python examples:
     - "ModuleNotFoundError: No module named 'flask'"
     - "ModuleNotFoundError: No module named 'pydantic'"
   JS examples:
     - "Cannot find module 'express'" (an npm package, not a local file)
   Fix: Add to requirements.txt / package.json — NOT a code edit.

8. STRUCTURAL — Config-level issues, not code bugs
   Triggers: ESM/CJS mismatch, missing exports, JSX transform, vitest globals
   Examples:
     - "Cannot use import statement outside a module"
     - "ReferenceError: describe is not defined" (vitest globals missing)
     - "SyntaxError: Unexpected token '<'" (JSX not configured)
   Fix: Config file edits (package.json, vitest.config.js, etc.)

═══════════════════════════════════════════════════════
 FILE PATH EXTRACTION RULES
═══════════════════════════════════════════════════════

ALWAYS extract the actual USER source file path from the error output. Look for these patterns:

Python:
  - 'File "src/utils.py", line 15'  →  file = "src/utils.py", line = 15
  - 'src/calculator.py::test_add FAILED'  →  file = "src/calculator.py"
  - Traceback File lines — use the LAST non-library File entry

JavaScript / Node:
  - 'FAIL src/utils.test.js'  →  file = "src/utils.test.js"
  - 'at Object.<anonymous> (src/main.js:42:5)'  →  file = "src/main.js", line = 42
  - '❌ src/validator.test.ts'  →  file = "src/validator.test.ts"

⚠️ CRITICAL: NEVER report library/framework paths as the error file:
  - SKIP: site-packages/, node_modules/, internal/, starlette/, uvicorn/,
    django/, flask/, fastapi/, werkzeug/, pydantic/, _pytest/, pluggy/,
    <frozen *, <string>, <module>
  - Walk BACKWARDS through the stack trace to find the first USER code file.

═══════════════════════════════════════════════════════
 ROOT-CAUSE ANALYSIS GUIDELINES
═══════════════════════════════════════════════════════

- If you see BOTH a NameError and a traceback pointing to a user file with a missing import,
  classify as IMPORT (not LOGIC). The root cause is the missing import statement.

- If you see ModuleNotFoundError for a KNOWN pip/npm package (flask, express, numpy, etc.),
  classify as DEPENDENCY. The fix is adding to requirements.txt, not editing code.

- If you see "Cannot use import statement outside a module", classify as STRUCTURAL even
  though it says "import" — it's a module system config issue.

- If tests fail with assertion errors but the test code is correct, the bug is
  LOGIC in the SOURCE file (not the test file). Report the source file, not the test file.

- If multiple errors exist, classify ALL of them. Report each one separately.
  Prioritize: DEPENDENCY > STRUCTURAL > IMPORT > SYNTAX > INDENTATION > TYPE_ERROR > LOGIC > LINTING

═══════════════════════════════════════════════════════
 RESPONSE FORMAT
═══════════════════════════════════════════════════════

Return a JSON array. Each item MUST have ALL of these fields:

[
  {{
    "file": "src/utils.py",
    "bug_type": "LINTING",
    "line": 15,
    "description": "LINTING error in src/utils.py line 15 → Fix: remove the unused import 'os'",
    "raw_error": "F401 'os' imported but unused"
  }},
  {{
    "file": "src/validator.py",
    "bug_type": "SYNTAX",
    "line": 8,
    "description": "SYNTAX error in src/validator.py line 8 → Fix: add the missing colon at end of function definition",
    "raw_error": "SyntaxError: expected ':'"
  }},
  {{
    "file": "src/calculator.py",
    "bug_type": "LOGIC",
    "line": 22,
    "description": "LOGIC error in src/calculator.py line 22 → Fix: change '+' operator to '*' in multiply function",
    "raw_error": "AssertionError: assert multiply(3, 4) == 12, got 7"
  }},
  {{
    "file": "src/app.py",
    "bug_type": "TYPE_ERROR",
    "line": 45,
    "description": "TYPE_ERROR error in src/app.py line 45 → Fix: convert string argument to int before arithmetic operation",
    "raw_error": "TypeError: unsupported operand type(s) for +: 'int' and 'str'"
  }},
  {{
    "file": "src/main.py",
    "bug_type": "IMPORT",
    "line": 3,
    "description": "IMPORT error in src/main.py line 3 → Fix: add 'import json' at the top of the file",
    "raw_error": "NameError: name 'json' is not defined"
  }}
]

The "description" field MUST follow EXACTLY this format:
  "BUG_TYPE error in FILE line LINE → Fix: SUGGESTION"

Respond ONLY with a valid JSON array. No prose, no markdown fences, no explanation.
"""

# Paths that belong to libraries/frameworks — never try to fix these
LIBRARY_PATH_FRAGMENTS = [
    "site-packages", "node_modules", "internal/",
    "starlette/", "uvicorn/", "django/", "flask/",
    "fastapi/", "werkzeug/", "pydantic/", "httptools/",
    "anyio/", "asyncio/", "concurrent/", "importlib/",
    "<frozen", "<string>", "<module>",
    "_pytest/", "pluggy/", "pytest/",
]


class FailureClassifierAgent:
    """Classifies test failures using Gemini LLM with improved regex fallback."""

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY", "")
        if api_key and api_key not in ("your_gemini_api_key_here", "YOUR_GEMINI_KEY_HERE"):
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel("gemini-2.5-flash")
        else:
            self.model = None
            logger.warning("[FailureClassifierAgent] No Gemini API key - using regex fallback.")

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
        if self.model:
            failures = self._classify_with_llm(state)

        if not failures:
            failures = self._classify_with_regex(state)

        # Root-cause ordering: STRUCTURAL > IMPORT > SYNTAX > TYPE_ERROR > LOGIC
        priority = {"DEPENDENCY": 0, "STRUCTURAL": 1, "IMPORT": 2, "SYNTAX": 3, "INDENTATION": 4, "TYPE_ERROR": 5, "LOGIC": 6, "LINTING": 7}
        failures.sort(key=lambda f: priority.get(f.get("bug_type", "LOGIC"), 5))

        # Filter out library paths — never try to fix framework internals
        failures = [f for f in failures if not self._is_library_path(f.get("file", ""))]

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
        try:
            response = self.model.generate_content(prompt)
            text = response.text.strip()
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            parsed = json.loads(text)
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed
            logger.warning("[FailureClassifierAgent] LLM returned empty list, using regex fallback.")
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "resource" in error_str and "exhausted" in error_str or "quota" in error_str or "rate" in error_str:
                logger.error(
                    "\n" + "=" * 60 +
                    "\n⚠️  GEMINI API RATE LIMIT REACHED  ⚠️"
                    "\n   The API key has hit its request quota."
                    "\n   Classification will use regex fallback for this iteration."
                    "\n   Consider waiting or upgrading your API plan."
                    "\n" + "=" * 60
                )
                print(
                    "\n\033[93m" + "=" * 60 +
                    "\n⚠️  GEMINI API RATE LIMIT REACHED  ⚠️"
                    "\n   Classification falling back to regex."
                    "\n" + "=" * 60 + "\033[0m"
                )
            else:
                logger.warning(f"[FailureClassifierAgent] LLM failed ({e}), falling back to regex.")
        return []

    def _classify_with_regex(self, state: SharedState):
        """Enhanced regex-based fallback classifier."""
        stdout = state.test_stdout or ""
        stderr = state.test_stderr or ""
        combined = f"{stdout}\n{stderr}"
        failures = []

        # ── STRUCTURAL errors (highest priority) ────────────────────────────

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
        ]

        for pattern, bug_type, fix_hint in structural_patterns:
            m = re.search(pattern, combined, re.IGNORECASE)
            if m:
                # Try to extract the file from surrounding context
                file_name = self._extract_file_near(combined, m.start())
                failures.append({
                    "file": file_name,
                    "bug_type": bug_type,
                    "line": 0,
                    "description": f"{bug_type} error in {file_name} line 0 → Fix: {fix_hint}",
                    "raw_error": m.group(0)[:200]
                })

        # ── Node / Jest / Vitest patterns ───────────────────────────────────

        # "Cannot find module" with file extraction
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

        # Jest/Vitest FAIL line: "FAIL src/foo.test.js" or "❌ src/foo.test.js"
        fail_file_pat = re.compile(r"(?:FAIL|❌)\s+([\w./\\-]+\.(?:test|spec)\.(?:js|ts|jsx|tsx))", re.MULTILINE)
        for m in fail_file_pat.finditer(combined):
            file_name = m.group(1).strip().replace("\\", "/")
            if self._is_library_path(file_name):
                continue
            # Try to extract assertion error details
            desc = self._extract_assertion_detail(combined, file_name)
            failures.append({
                "file": file_name,
                "bug_type": "LOGIC",
                "line": 0,
                "description": desc or f"LOGIC error in {file_name} line 0 → Fix: review failing test assertions",
                "raw_error": m.group(0)[:200]
            })

        # Pytest FAILED line: "backend/test_main.py::test_name FAILED"
        pytest_fail_pat = re.compile(r'([\w./\\-]+\.py)::([\w_]+)\s+FAILED', re.MULTILINE)
        for m in pytest_fail_pat.finditer(combined):
            file_name = m.group(1).strip().replace("\\", "/")
            test_name = m.group(2).strip()
            if self._is_library_path(file_name):
                continue
            # Extract the actual assertion failure detail
            desc = self._extract_pytest_failure_detail(combined, test_name)
            failures.append({
                "file": file_name,
                "bug_type": "LOGIC",
                "line": 0,
                "description": desc or f"LOGIC error in {file_name}::{test_name} → Fix: review failing assertion",
                "raw_error": m.group(0)[:200]
            })

        # "● Test suite failed to run" with file extraction
        suite_fail = re.compile(r"● Test suite failed to run\s*\n\s*([\s\S]*?)(?:\n\n|\n\s*●|\Z)", re.MULTILINE)
        for m in suite_fail.finditer(combined):
            err_block = m.group(1).strip()
            file_name = self._extract_file_from_block(err_block)
            bt = "SYNTAX" if "SyntaxError" in err_block else "IMPORT"
            failures.append({
                "file": file_name,
                "bug_type": bt,
                "line": self._extract_line_from_block(err_block),
                "description": f"{bt} error in {file_name} → Fix: {err_block[:100]}",
                "raw_error": err_block[:200]
            })

        # Stack trace file extraction: "at ... (path:line:col)"
        stack_pat = re.compile(r'at\s+\S+\s+\(([^:)]+):(\d+):\d+\)')
        for m in stack_pat.finditer(combined):
            fpath = m.group(1).strip()
            if self._is_library_path(fpath):
                continue
            # Only add if not already covered
            if not any(f["file"] == fpath for f in failures):
                line_num = int(m.group(2))
                failures.append({
                    "file": fpath,
                    "bug_type": "LOGIC",
                    "line": line_num,
                    "description": f"LOGIC error in {fpath} line {line_num} → Fix: check runtime error at this location",
                    "raw_error": m.group(0)[:200]
                })
                break  # Only take first non-library stack frame

        # npm ERR!
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

        # ── Python patterns ─────────────────────────────────────────────────

        python_patterns = [
            (r"SyntaxError[:\s]+(.+)", "SYNTAX"),
            (r"IndentationError[:\s]+(.+)", "INDENTATION"),
            (r"ImportError[:\s]+(.+)", "IMPORT"),
            # ModuleNotFoundError = missing pip package → DEPENDENCY, not IMPORT
            (r"ModuleNotFoundError[:\s]+No module named '([^']+)'", "DEPENDENCY"),
            (r"ModuleNotFoundError[:\s]+(.+)", "DEPENDENCY"),
            (r"TypeError[:\s]+(.+)", "TYPE_ERROR"),
            (r"NameError[:\s]+(.+)", "LOGIC"),
            (r"AttributeError[:\s]+(.+)", "LOGIC"),
            (r"AssertionError[:\s]*(.*)", "LOGIC"),
        ]
        # Python file:line extraction
        py_file_line = re.compile(r'File "([^"]+)", line (\d+)')

        for pattern, bug_type in python_patterns:
            for m in re.finditer(pattern, combined, re.MULTILINE):
                err = (m.group(1) or "").strip()
                # Find the nearest File "..." line BEFORE this error
                file_name = "unknown"
                line_num = 0
                search_region = combined[:m.start()]
                fl_matches = list(py_file_line.finditer(search_region))
                if fl_matches:
                    # Walk backwards through File matches, skip library paths
                    for fl in reversed(fl_matches):
                        fpath = fl.group(1)
                        if not self._is_library_path(fpath):
                            file_name = fpath
                            line_num = int(fl.group(2))
                            break
                failures.append({
                    "file": file_name,
                    "bug_type": bug_type,
                    "line": line_num,
                    "description": f"{bug_type} error in {file_name} line {line_num} → Fix: {err[:80]}",
                    "raw_error": m.group(0)[:200]
                })
                if len(failures) >= 10:
                    break

        # ── Generic fallback ────────────────────────────────────────────────

        if not failures:
            # Try to at least find a file path in the output
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

        # Deduplicate by (file, bug_type)
        seen = set()
        unique = []
        for f in failures:
            key = (f.get("file", ""), f.get("bug_type", ""), f.get("line", 0))
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique[:10]

    # ── Helper methods ──────────────────────────────────────────────────────

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

    def _is_library_path(self, file_path: str) -> bool:
        """Check if a file path belongs to a library/framework (not user code)."""
        if not file_path or file_path == "unknown":
            return False
        fp_lower = file_path.lower().replace("\\", "/")
        for fragment in LIBRARY_PATH_FRAGMENTS:
            if fragment.lower() in fp_lower:
                return True
        return False

    def _extract_pytest_failure_detail(self, combined: str, test_name: str) -> str:
        """Extract pytest failure details for a specific test function.
        
        Looks for the failure block like:
            _______ test_calculation _______
            ...assertion details...
        """
        # Find the pytest failure header
        header_pat = re.compile(
            rf'_+\s*{re.escape(test_name)}\s*_+\s*\n([\s\S]*?)(?:\n_+|\n=+|\Z)',
            re.MULTILINE
        )
        m = header_pat.search(combined)
        if not m:
            return ""
        
        block = m.group(1).strip()
        
        # Look for assertion details
        # "assert X == Y" or "AssertionError" or "Expected ... but got ..."
        assertion = re.search(r'(assert\s+.+)', block)
        if assertion:
            detail = assertion.group(1).strip()[:120]
            return f"LOGIC error in {test_name} → Fix: {detail}"
        
        assertion_err = re.search(r'(AssertionError[:\s]*.+)', block)
        if assertion_err:
            detail = assertion_err.group(1).strip()[:120]
            return f"LOGIC error in {test_name} → Fix: {detail}"
        
        # "E       ..." lines from pytest
        e_lines = re.findall(r'^E\s+(.+)$', block, re.MULTILINE)
        if e_lines:
            detail = " | ".join(e_lines[:3])[:120]
            return f"LOGIC error in {test_name} → Fix: {detail}"
        
        return f"LOGIC error in {test_name} → Fix: review failing assertion"
