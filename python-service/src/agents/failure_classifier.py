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
            # Try to extract assertion error details
            desc = self._extract_assertion_detail(combined, file_name)
            failures.append({
                "file": file_name,
                "bug_type": "LOGIC",
                "line": 0,
                "description": desc or f"LOGIC error in {file_name} line 0 → Fix: review failing test assertions",
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
            if "node_modules" in fpath or fpath.startswith("internal/"):
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
                break  # Only take first non-node_modules stack frame

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
            (r"ModuleNotFoundError[:\s]+(.+)", "IMPORT"),
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
