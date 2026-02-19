"""
FailureClassifierAgent: Uses the Gemini LLM to classify test failures into
structured categories and extract precise error details.
Includes a significantly improved regex fallback for vitest / Jest / Python output.

Also provides deterministic classify_error() and format_output_line() utilities.
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

# ─── Deterministic classification maps ──────────────────────────────────────────

FLAKE8_CODE_MAP = {
    "F401": ("LINTING",     "remove the import statement"),
    "F811": ("LINTING",     "remove the redundant import"),
    "F841": ("LOGIC",       "remove the unused variable"),
    "F821": ("LOGIC",       "fix the undefined variable name"),
    "E501": ("LINTING",     "shorten the line to within limit"),
    "E302": ("LINTING",     "add the required blank lines"),
    "E303": ("LINTING",     "remove the extra blank lines"),
    "E711": ("LOGIC",       "use is None instead of equals None"),
    "E712": ("LOGIC",       "use is True instead of equals True"),
    "W291": ("LINTING",     "remove the trailing whitespace"),
    "W293": ("LINTING",     "remove the trailing whitespace"),
    "E101": ("INDENTATION", "replace tabs with spaces"),
    "E111": ("INDENTATION", "fix the indentation level"),
    "E113": ("INDENTATION", "fix the unexpected indentation"),
    "E117": ("INDENTATION", "fix the indentation level"),
    "E901": ("SYNTAX",      "fix the syntax error"),
    "E902": ("SYNTAX",      "fix the invalid syntax"),
    "W605": ("SYNTAX",      "fix the invalid escape sequence"),
}

BUG_RULES = {
    "unused import":           ("LINTING",      "remove the import statement"),
    "imported but unused":     ("LINTING",      "remove the import statement"),
    "redefined unused":        ("LINTING",      "remove the redundant import"),
    "line too long":           ("LINTING",      "shorten the line to within limit"),
    "trailing whitespace":     ("LINTING",      "remove the trailing whitespace"),
    "missing colon":           ("SYNTAX",       "add the colon at the correct position"),
    "invalid syntax":          ("SYNTAX",       "fix the invalid syntax"),
    "invalid js syntax":       ("SYNTAX",       "fix the invalid JS syntax"),
    "syntaxerror":             ("SYNTAX",       "fix the syntax error"),
    "unexpected eof":          ("SYNTAX",       "add the missing closing bracket"),
    "unexpected token":        ("SYNTAX",       "fix the invalid syntax"),
    "indentationerror":        ("INDENTATION",  "fix the indentation level"),
    "unexpected indent":       ("INDENTATION",  "fix the indentation level"),
    "unindent":                ("INDENTATION",  "fix the indentation level"),
    "importerror":             ("IMPORT",       "add the missing import statement"),
    "modulenotfounderror":     ("IMPORT",       "add the missing import statement"),
    "cannot find module":      ("IMPORT",       "install or fix the missing module"),
    "cannot import":           ("IMPORT",       "fix the import path or name"),
    "no module named":         ("IMPORT",       "add the missing import statement"),
    "typeerror":               ("TYPE_ERROR",   "correct the type conversion"),
    "unsupported operand":     ("TYPE_ERROR",   "fix the type mismatch"),
    "is not a function":       ("TYPE_ERROR",   "fix the function reference"),
    "is not defined":          ("LOGIC",        "fix the undefined variable or function"),
    "expected undefined to be":"LOGIC",
    "assertionerror":          ("LOGIC",        "fix the incorrect logic"),
    "assert":                  ("LOGIC",        "fix the incorrect logic"),
    "expected":                ("LOGIC",        "fix the incorrect logic"),
    "index out of range":      ("LOGIC",        "fix the index boundary"),
    "zerodivisionerror":       ("LOGIC",        "add a zero division guard"),
    "keyerror":                ("LOGIC",        "add a key existence check"),
    "attributeerror":          ("LOGIC",        "fix the incorrect attribute access"),
}


def classify_error(error_message: str, flake8_code: str = "") -> tuple:
    """
    Returns (bug_type, fix_description).
    First checks FLAKE8_CODE_MAP using the code (e.g. "F401").
    Then checks BUG_RULES by keyword matching on lowercased error_message.
    Returns ("LOGIC", "fix the incorrect logic") as default fallback.
    Never calls AI — this is deterministic.
    """
    if flake8_code and flake8_code in FLAKE8_CODE_MAP:
        return FLAKE8_CODE_MAP[flake8_code]

    msg_lower = error_message.lower()
    for keyword, val in BUG_RULES.items():
        if keyword in msg_lower:
            if isinstance(val, tuple):
                return val
            return (val, "fix the incorrect logic")

    return ("LOGIC", "fix the incorrect logic")


def format_output_line(bug_type: str, filepath: str, line_number: int, fix_description: str) -> str:
    """
    THE ONLY place this string is built. Never build it anywhere else.
    Returns: f"{bug_type} error in {filepath} line {line_number} → Fix: {fix_description}"
    The arrow \u2192 is unicode → not ASCII ->
    """
    return f"{bug_type} error in {filepath} line {line_number} \u2192 Fix: {fix_description}"


def _infer_source_file(test_file: str) -> str:
    """
    Infer which SOURCE file a test file is testing.
    Examples:
      'src/tests/textUtils.test.js'   → 'src/textUtils.js'
      'src/tests/statsCalc.test.ts'   → 'src/statsCalc.ts'
      'tests/test_utils.py'           → 'src/utils.py'
      '__tests__/foo.test.js'         → 'src/foo.js'
    """
    if not test_file:
        return ""

    # JS/TS: Remove .test. or .spec. from filename
    # e.g. "textUtils.test.js" → "textUtils.js"
    basename = os.path.basename(test_file)

    # Handle .test.js / .spec.js / .test.ts / .spec.ts
    m = re.match(r'^(.+?)\.(test|spec)\.(js|ts|jsx|tsx)$', basename)
    if m:
        source_basename = f"{m.group(1)}.{m.group(3)}"
        # Try to build the source path
        # src/tests/foo.test.js → src/foo.js
        # __tests__/foo.test.js → src/foo.js
        dir_part = os.path.dirname(test_file)
        # Strip /tests or /__tests__ from path
        dir_part = re.sub(r'[/\\]?(?:__)?tests?(?:__)?[/\\]?$', '', dir_part)
        if not dir_part:
            dir_part = 'src'
        return os.path.join(dir_part, source_basename).replace("\\", "/")

    # Python: test_foo.py → foo.py
    m = re.match(r'^test_(.+\.py)$', basename)
    if m:
        source_basename = m.group(1)
        dir_part = os.path.dirname(test_file)
        dir_part = re.sub(r'[/\\]?tests?[/\\]?$', '', dir_part)
        if not dir_part:
            dir_part = 'src'
        return os.path.join(dir_part, source_basename).replace("\\", "/")

    return ""


def _find_source_file_in_repo(repo_path: str, inferred_path: str) -> str:
    """
    Try to find the actual source file in the repo. Returns the relative path
    if found, or the inferred path as fallback.
    """
    if not inferred_path:
        return ""

    # Direct path check
    full = os.path.join(repo_path, inferred_path)
    if os.path.isfile(full):
        return inferred_path

    # Search by basename
    basename = os.path.basename(inferred_path)
    for root, _, files in os.walk(repo_path):
        if 'node_modules' in root or '.git' in root:
            continue
        if basename in files:
            return os.path.relpath(os.path.join(root, basename), repo_path).replace("\\", "/")

    return inferred_path  # Return the inferred path even if not found — LLM might still fix it


# ─── LLM Classification Prompt ─────────────────────────────────────────────────

CLASSIFICATION_PROMPT = """
You are an expert CI/CD failure classifier. Analyze the test output below and classify every error.

Test Output (stdout):
{stdout}

Test Output (stderr):
{stderr}

Exit Code: {exit_code}

For each error or failure found, output a JSON array. Each item must have:
- "file": the SOURCE file where the bug exists (NOT the test file). For example if test file "src/tests/foo.test.js" fails, the source is likely "src/foo.js". Use "unknown" only if truly impossible to determine.
- "bug_type": EXACTLY one of: LINTING | SYNTAX | LOGIC | TYPE_ERROR | IMPORT | INDENTATION
- "line": line number as integer (0 if unknown)
- "description": a short description of what the fix should be
- "raw_error": the exact error snippet from the output (max 200 chars)
- "flake8_code": the flake8 error code if applicable (e.g. "F401"), or "" if not applicable

Critical rules:
- "invalid JS syntax" or "SyntaxError" → bug_type = "SYNTAX"
- "expected X to be Y" → bug_type = "LOGIC" (fix the function logic so tests pass)
- "Cannot find module" → bug_type = "IMPORT"
- Always try to identify the SOURCE file, not the test file.
- If test file is "src/tests/textUtils.test.js", the source file is "src/textUtils.js"
- If test file is "tests/test_calculator.py", the source file is "src/calculator.py"

Respond ONLY with a valid JSON array. No prose, no markdown fences, no explanation.
"""


class FailureClassifierAgent:
    """Classifies test failures using Gemini LLM with improved regex fallback."""

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY", "")
        if api_key and api_key not in ("your_gemini_api_key_here", "YOUR_GEMINI_KEY_HERE", "your_key"):
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel("gemini-2.0-flash")
        else:
            self.model = None
            logger.warning("[FailureClassifierAgent] No valid Gemini API key - using regex fallback.")

    def run(self, state: SharedState) -> SharedState:
        if state.test_exit_code == 0:
            logger.info("[FailureClassifierAgent] Tests passed — no failures to classify.")
            state.classified_failures = []
            state.total_failures = 0
            return state

        combined = f"{state.test_stdout}\n{state.test_stderr}"
        logger.info(f"[FailureClassifierAgent] Classifying failures from output ({len(combined)} chars)...")

        failures = None
        if self.model:
            failures = self._classify_with_llm(state)

        if not failures:
            failures = self._classify_with_regex(state)

        # Enrich each failure with deterministic classify_error and format_output_line
        for f in failures:
            bug_type, fix_desc = classify_error(
                f.get("raw_error", "") or f.get("description", ""),
                f.get("flake8_code", "")
            )
            # Override only if the current bug_type is not valid
            if f.get("bug_type") not in ("LINTING", "SYNTAX", "LOGIC", "TYPE_ERROR", "IMPORT", "INDENTATION"):
                f["bug_type"] = bug_type
            if not f.get("fix_description"):
                f["fix_description"] = fix_desc
            # Try to resolve source file if we have repo_path
            if f.get("file") == "unknown" or not f.get("file"):
                inferred = _infer_source_file(f.get("test_file", ""))
                if inferred and state.repo_path:
                    f["file"] = _find_source_file_in_repo(state.repo_path, inferred)
                elif inferred:
                    f["file"] = inferred
            # Build the exact output line
            f["output_line"] = format_output_line(
                f["bug_type"], f.get("file", "unknown"),
                f.get("line", 0), f.get("fix_description", fix_desc)
            )

        state.classified_failures = failures
        state.total_failures = len(failures)
        logger.info(f"[FailureClassifierAgent] Classified {len(failures)} failure(s).")
        for f in failures:
            logger.info(f"  → {f.get('output_line', f.get('description', 'no description'))}")
        return state

    def _classify_with_llm(self, state: SharedState):
        prompt = CLASSIFICATION_PROMPT.format(
            stdout=state.test_stdout[:4000],
            stderr=state.test_stderr[:4000],
            exit_code=state.test_exit_code
        )
        try:
            parsed = None
            last_error = None
            for attempt in range(3):  # Up to 3 attempts
                try:
                    response = self.model.generate_content(prompt)
                    text = response.text.strip()
                    text = re.sub(r"^```[a-z]*\n?", "", text)
                    text = re.sub(r"\n?```$", "", text)
                    parsed = json.loads(text)
                    break
                except Exception as e:
                    last_error = e
                    err_str = str(e)
                    if "429" in err_str and attempt < 2:
                        import time
                        delay_match = re.search(r'retry in ([\d.]+)s', err_str)
                        wait_time = float(delay_match.group(1)) if delay_match else 15.0
                        wait_time = min(wait_time + 2, 60)
                        logger.warning(
                            f"[FailureClassifierAgent] Rate limited, waiting {wait_time:.0f}s "
                            f"(attempt {attempt + 1}/3)..."
                        )
                        time.sleep(wait_time)
                        continue
                    raise

            if parsed and isinstance(parsed, list) and len(parsed) > 0:
                # Enrich with fix_description from classify_error
                for item in parsed:
                    code = item.get("flake8_code", "")
                    bt, fd = classify_error(item.get("raw_error", ""), code)
                    if "fix_description" not in item:
                        item["fix_description"] = item.get("description", fd)
                    if item.get("bug_type") not in ("LINTING", "SYNTAX", "LOGIC", "TYPE_ERROR", "IMPORT", "INDENTATION"):
                        item["bug_type"] = bt
                logger.info(f"[FailureClassifierAgent] LLM returned {len(parsed)} classified failure(s).")
                return parsed
            logger.warning("[FailureClassifierAgent] LLM returned empty list, using regex fallback.")
            return None
        except Exception as e:
            logger.warning(f"[FailureClassifierAgent] LLM failed ({e}), falling back to regex.")
            return None

    def _classify_with_regex(self, state: SharedState):
        """Enhanced regex-based fallback classifier supporting vitest, Jest, and Python output."""
        stdout = state.test_stdout or ""
        stderr = state.test_stderr or ""
        combined = f"{stdout}\n{stderr}"
        failures = []
        seen_files = set()

        logger.info("[FailureClassifierAgent] Using regex fallback classifier...")

        # ══════════════════════════════════════════════
        # 1) Vitest / Jest: Parse "FAIL" lines from stderr
        #    Match: " FAIL  src/tests/foo.test.js [ ... ]"
        #    or:    "FAIL src/tests/foo.test.js"
        # ══════════════════════════════════════════════
        fail_pat = re.compile(
            r'FAIL\s+([\w/\\.@-]+\.(?:test|spec)\.(?:js|ts|jsx|tsx))',
            re.MULTILINE
        )
        for m in fail_pat.finditer(combined):
            test_file = m.group(1).strip()
            source_file = _infer_source_file(test_file)
            if state.repo_path and source_file:
                source_file = _find_source_file_in_repo(state.repo_path, source_file)

            # Look for the error message that follows this FAIL line
            # e.g., "Error: Failed to parse source for import analysis because the content contains invalid JS syntax"
            after_fail = combined[m.end():m.end()+500]
            error_snippet = ""
            bug_type = "LOGIC"
            fix_desc = "fix the incorrect logic"

            err_match = re.search(r'Error:\s*(.+?)(?:\n|$)', after_fail)
            if err_match:
                error_snippet = err_match.group(1).strip()[:200]
                bt, fd = classify_error(error_snippet)
                bug_type = bt
                fix_desc = fd

            key = source_file or test_file
            if key not in seen_files:
                seen_files.add(key)
                failures.append({
                    "file": source_file or "unknown",
                    "test_file": test_file,
                    "bug_type": bug_type,
                    "line": 0,
                    "fix_description": fix_desc,
                    "raw_error": error_snippet or f"FAIL {test_file}"
                })

        # ══════════════════════════════════════════════
        # 2) Vitest / Jest: Parse assertion failures from stdout
        #    Match: "❯ src/tests/textUtils.test.js > describe > test name"
        #    Match: "→ expected undefined to be 3"
        # ══════════════════════════════════════════════
        # Find test files with failures: "src/tests/foo.test.js  (N tests | M failed)"
        failed_test_pat = re.compile(
            r'(?:❯|›)\s+([\w/\\.@-]+\.(?:test|spec)\.(?:js|ts|jsx|tsx))\s+\(.*?(\d+)\s+failed',
            re.MULTILINE
        )
        for m in failed_test_pat.finditer(combined):
            test_file = m.group(1).strip()
            failed_count = int(m.group(2))
            source_file = _infer_source_file(test_file)
            if state.repo_path and source_file:
                source_file = _find_source_file_in_repo(state.repo_path, source_file)

            key = source_file or test_file
            if key not in seen_files:
                seen_files.add(key)
                # Look for assertion errors after this line
                after_match = combined[m.end():m.end()+1000]
                assertion_match = re.search(r'(?:→|->)\s*(expected\s+.+?)(?:\n|$)', after_match)
                error_snippet = assertion_match.group(1).strip()[:200] if assertion_match else f"{failed_count} test(s) failed"

                bt, fd = classify_error(error_snippet)
                failures.append({
                    "file": source_file or "unknown",
                    "test_file": test_file,
                    "bug_type": bt,
                    "line": 0,
                    "fix_description": fd,
                    "raw_error": error_snippet
                })

        # ══════════════════════════════════════════════
        # 3) Vitest/Jest: Parse specific assertion lines
        #    "AssertionError: expected undefined to be 3"
        #    "expected undefined to be 3 // Object.is equality"
        # ══════════════════════════════════════════════
        assertion_pat = re.compile(
            r'(?:AssertionError|AssertError):\s*(.+?)(?:\n|$)',
            re.MULTILINE | re.IGNORECASE
        )
        for m in assertion_pat.finditer(combined):
            err = m.group(1).strip()[:200]
            if err not in [f.get("raw_error", "") for f in failures]:
                bt, fd = classify_error(err)
                failures.append({
                    "file": "unknown",
                    "bug_type": bt,
                    "line": 0,
                    "fix_description": fd,
                    "raw_error": err
                })

        # ══════════════════════════════════════════════
        # 4) Node / npm: "Cannot find module" / "command not found"
        # ══════════════════════════════════════════════
        module_not_found = re.compile(
            r"Cannot find module '([^']+)'",
            re.MULTILINE
        )
        for m in module_not_found.finditer(combined):
            mod = m.group(1)
            if mod not in seen_files:
                seen_files.add(mod)
                failures.append({
                    "file": "package.json",
                    "bug_type": "IMPORT",
                    "line": 0,
                    "fix_description": f"install or fix the missing module '{mod}'",
                    "raw_error": m.group(0)[:200]
                })

        # npm ERR!
        npm_err = re.compile(r"npm ERR! (.+)", re.MULTILINE)
        for m in npm_err.finditer(combined):
            err = m.group(1).strip()
            key = err[:60]
            if key not in seen_files and ("errno" in err.lower() or "missing" in err.lower()):
                seen_files.add(key)
                failures.append({
                    "file": "package.json",
                    "bug_type": "IMPORT",
                    "line": 0,
                    "fix_description": "fix the npm dependency issue",
                    "raw_error": err[:200]
                })

        # ══════════════════════════════════════════════
        # 5) Python: SyntaxError, TypeError, ImportError, etc.
        # ══════════════════════════════════════════════
        python_patterns = [
            (r'File "([^"]+)", line (\d+).*\n\s*.*\n\s*(SyntaxError[:\s]+.+)', "SYNTAX"),
            (r'File "([^"]+)", line (\d+).*\n\s*.*\n\s*(IndentationError[:\s]+.+)', "INDENTATION"),
            (r'File "([^"]+)", line (\d+).*\n\s*.*\n\s*(ImportError[:\s]+.+)', "IMPORT"),
            (r'File "([^"]+)", line (\d+).*\n\s*.*\n\s*(ModuleNotFoundError[:\s]+.+)', "IMPORT"),
            (r'File "([^"]+)", line (\d+).*\n\s*.*\n\s*(TypeError[:\s]+.+)', "TYPE_ERROR"),
            (r'File "([^"]+)", line (\d+).*\n\s*.*\n\s*(NameError[:\s]+.+)', "LOGIC"),
            (r'File "([^"]+)", line (\d+).*\n\s*.*\n\s*(AttributeError[:\s]+.+)', "LOGIC"),
            (r'File "([^"]+)", line (\d+).*\n\s*.*\n\s*(AssertionError.+)', "LOGIC"),
        ]

        for pattern, default_bt in python_patterns:
            for m in re.finditer(pattern, combined, re.MULTILINE):
                file_name = m.group(1)
                line_num = int(m.group(2))
                err_msg = m.group(3).strip()
                # Skip files inside site-packages or venv
                if 'site-packages' in file_name or 'venv' in file_name:
                    continue
                bt, fd = classify_error(err_msg)
                key = f"{file_name}:{line_num}"
                if key not in seen_files:
                    seen_files.add(key)
                    # Make path relative if possible
                    if state.repo_path and file_name.startswith(state.repo_path):
                        file_name = os.path.relpath(file_name, state.repo_path)
                    failures.append({
                        "file": file_name,
                        "bug_type": bt,
                        "line": line_num,
                        "fix_description": fd,
                        "raw_error": err_msg[:200]
                    })

        # Simpler Python patterns without file/line context
        simple_python = [
            (r"(SyntaxError[:\s]+.+?)(?:\n|$)", "SYNTAX"),
            (r"(IndentationError[:\s]+.+?)(?:\n|$)", "INDENTATION"),
            (r"(ImportError[:\s]+.+?)(?:\n|$)", "IMPORT"),
            (r"(ModuleNotFoundError[:\s]+.+?)(?:\n|$)", "IMPORT"),
            (r"(TypeError[:\s]+.+?)(?:\n|$)", "TYPE_ERROR"),
        ]
        for pattern, default_bt in simple_python:
            for m in re.finditer(pattern, combined, re.MULTILINE):
                err_msg = m.group(1).strip()
                key = err_msg[:60]
                if key not in seen_files:
                    seen_files.add(key)
                    bt, fd = classify_error(err_msg)
                    failures.append({
                        "file": "unknown",
                        "bug_type": bt,
                        "line": 0,
                        "fix_description": fd,
                        "raw_error": err_msg[:200]
                    })

        # ══════════════════════════════════════════════
        # 6) Generic fallback
        # ══════════════════════════════════════════════
        if not failures:
            snippet = combined.strip()[:300]
            # Try to extract any test file reference
            any_test = re.search(r'([\w/\\.]+\.(?:test|spec)\.(?:js|ts|jsx|tsx|py))', combined)
            test_file = any_test.group(1) if any_test else ""
            source_file = _infer_source_file(test_file) if test_file else "unknown"
            if state.repo_path and source_file and source_file != "unknown":
                source_file = _find_source_file_in_repo(state.repo_path, source_file)

            failures.append({
                "file": source_file or "unknown",
                "test_file": test_file,
                "bug_type": "LOGIC",
                "line": 0,
                "fix_description": "fix the incorrect logic",
                "raw_error": snippet
            })
            logger.info(f"[FailureClassifierAgent] Generic fallback used.")

        logger.info(f"[FailureClassifierAgent] Regex classified {len(failures)} failure(s):")
        for f in failures:
            logger.info(f"  file={f.get('file')} type={f.get('bug_type')} err={f.get('raw_error', '')[:80]}")

        return failures[:10]
