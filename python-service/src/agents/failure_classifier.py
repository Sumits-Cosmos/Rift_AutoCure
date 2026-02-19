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
from dotenv import load_dotenv
from .shared_state import SharedState
from ..llm.ollama_client import get_client as get_ollama_client

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
- "bug_type": EXACTLY one of: DEPENDENCY | STRUCTURAL | SYNTAX | IMPORT | TYPE_ERROR | LOGIC | LINTING | INDENTATION
- "line": line number as integer (0 if unknown)
- "description": concise string: "BUG_TYPE error in FILE line LINE → Fix: SUGGESTION"
- "raw_error": the exact error snippet from the output (max 200 chars)

DEPENDENCY means: a pip/npm package is NOT INSTALLED (ModuleNotFoundError, "Cannot find module" for an npm package).
The fix is to add the package to requirements.txt or package.json, NOT to edit source code.

STRUCTURAL means: ESM/CJS mismatch, missing exports, JSX transform config, vitest globals missing, etc.
These are CONFIG-level issues, not code bugs.

IMPORTANT: Always extract the ACTUAL file path from error output. Look for:
- "at Object.<anonymous> (path:line:col)"
- "FAIL path/to/file.test.js"
- 'File "path/to/file.py", line N'
- "Cannot find module 'path'" - the importing file AND the missing module
- pytest style: "path/to/file.py::test_name FAILED"

CRITICAL: IGNORE library/framework paths in stack traces. These are NOT user code:
- starlette/, uvicorn/, django/, flask/, fastapi/
- site-packages/, node_modules/
- internal/, <frozen *, <string>
Always look for the USER'S source file, not framework internals.

Respond ONLY with a valid JSON array. No prose, no markdown fences.
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
    """Classifies test failures using Ollama LLM with improved regex fallback."""

    def __init__(self):
        self.llm = get_ollama_client()
        if self.llm.is_available():
            logger.info(f"[FailureClassifierAgent] Using Ollama model: {self.llm.model}")
        else:
            logger.warning("[FailureClassifierAgent] Ollama not available — using regex fallback.")

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

        # ── Early detection: network errors (unfixable in code) ──
        network_patterns = ["EAI_AGAIN", "ENOTFOUND", "ENETUNREACH", "ECONNREFUSED",
                            "getaddrinfo", "network timeout", "ETIMEDOUT"]
        network_hit = any(pat in combined for pat in network_patterns)
        if network_hit:
            # Extract the failing package/registry from the error
            npm_match = re.search(r'request to (\S+) failed', combined)
            target = npm_match.group(1) if npm_match else "npm registry"
            logger.warning(
                f"[FailureClassifierAgent] ⚠️ NETWORK error detected — cannot reach {target}. "
                f"Docker containers run with --network=none. Packages must be installed during docker build."
            )
            # Still try to classify real errors, but add a network failure
            # so the fix generator can attempt to install deps at build time
            failures = [{
                "file": "package.json",
                "bug_type": "DEPENDENCY",
                "line": 0,
                "description": f"Network error: cannot reach {target}. Test framework may not be installed. "
                              f"Ensure jest/vitest is in package.json devDependencies.",
                "raw_error": combined[:500],
            }]
            state.classified_failures = failures
            state.total_failures = len(failures)
            logger.info(f"[FailureClassifierAgent] Classified {len(failures)} failure(s).")
            for f in failures:
                logger.info(f"  → [{f.get('bug_type')}] {f.get('file', '?')}:{f.get('line', 0)} — {f.get('description', '')[:80]}")
            return state

        failures = []
        if self.llm.is_available():
            failures = self._classify_with_llm(state)

        if not failures:
            failures = self._classify_with_regex(state)

        # Root-cause ordering: STRUCTURAL > IMPORT > SYNTAX > TYPE_ERROR > LOGIC
        priority = {"DEPENDENCY": 0, "STRUCTURAL": 1, "IMPORT": 2, "SYNTAX": 3, "INDENTATION": 4, "TYPE_ERROR": 5, "LOGIC": 6, "LINTING": 7}
        failures.sort(key=lambda f: priority.get(f.get("bug_type", "LOGIC"), 5))

        # Filter out library paths — never try to fix framework internals
        failures = [f for f in failures if not self._is_library_path(f.get("file", ""))]

        # Strip Docker /app/ prefix from file paths — the LLM and regex
        # often return container-internal paths like /app/src/utils.py
        for f in failures:
            fp = f.get("file", "")
            if fp.startswith("/app/"):
                f["file"] = fp[5:]  # strip '/app/'

        # Filter out system/infra paths that cannot be fixed
        # (npm logs, /root/, /usr/, temp dirs, etc.)
        system_prefixes = ("/root/", "/usr/", "/tmp/", "/var/", "/etc/")
        failures = [f for f in failures
                    if not any(f.get("file", "").startswith(p) for p in system_prefixes)
                    and not f.get("file", "").endswith("-debug-0.log")]

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
            text = self.llm.generate(prompt, json_mode=True, temperature=0.1)
            if not text:
                logger.warning("[FailureClassifierAgent] LLM returned empty, falling back to regex.")
                return []
            
            # Robust JSON extraction for smaller models
            parsed = self._extract_json_from_response(text)
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed
            
            # Retry with shorter, more forceful prompt
            logger.warning("[FailureClassifierAgent] First classification attempt failed, retrying...")
            retry_prompt = (
                f"Analyze this test output and return a JSON array of failures.\n\n"
                f"STDOUT:\n{(state.test_stdout or '')[:3000]}\n\n"
                f"STDERR:\n{(state.test_stderr or '')[:3000]}\n\n"
                f"Return a JSON array where each element has: "
                f"\"file\" (path), \"bug_type\" (SYNTAX/IMPORT/LOGIC/TYPE_ERROR), "
                f"\"line\" (number), \"description\" (one sentence), \"raw_error\" (error text)."
            )
            text2 = self.llm.generate(retry_prompt, json_mode=True, temperature=0.1)
            if text2:
                parsed2 = self._extract_json_from_response(text2)
                if isinstance(parsed2, list) and len(parsed2) > 0:
                    return parsed2
            
            logger.warning("[FailureClassifierAgent] LLM returned no usable results after retry, using regex fallback.")
        except Exception as e:
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
                        # Strip Docker container /app/ prefix
                        if fpath.startswith("/app/"):
                            fpath = fpath[5:]
                        if not self._is_library_path(fpath):
                            file_name = fpath
                            line_num = int(fl.group(2))
                            break
                
                # If still unknown, try to find the source file from pytest FAILED lines
                if file_name == "unknown":
                    # Look for "tests/test_suite.py::test_name FAILED" above the error
                    fail_match = re.search(
                        r'([\w./\\-]+\.py)::[\w:]+\s+FAILED', search_region
                    )
                    if fail_match:
                        # We know the test file — but the real bug is in SOURCE code
                        # Try to find source file references in the error block
                        err_block = combined[max(0, m.start() - 500):m.end() + 200]
                        src_file = re.search(
                            r'(?:in|from)\s+(?:/app/)?(\S+\.py)', err_block
                        )
                        if src_file:
                            fpath = src_file.group(1)
                            if fpath.startswith("/app/"):
                                fpath = fpath[5:]
                            file_name = fpath
                        else:
                            # Fall back to the test file — at least the agent can read it
                            file_name = fail_match.group(1).replace("\\", "/")
                
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

    # ── Response parsing helper ────────────────────────────────────────────────

    def _extract_json_from_response(self, text: str):
        """Robustly extract a JSON array/object from an LLM response.
        
        Handles: single objects, arrays, objects wrapping arrays,
        markdown fences, and malformed JSON from smaller models.
        Always returns a list (or empty list on failure).
        """
        text = text.strip()

        # Strategy 1: Direct parse
        try:
            parsed = json.loads(text)
            return self._normalize_to_list(parsed)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Strip markdown code fences and retry
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', text, flags=re.MULTILINE)
        cleaned = re.sub(r'\n?```\s*$', '', cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()
        try:
            parsed = json.loads(cleaned)
            return self._normalize_to_list(parsed)
        except json.JSONDecodeError:
            pass

        # Strategy 3: Find outermost JSON structure (array or object)
        # Try array first
        bracket_start = text.find('[')
        if bracket_start >= 0:
            depth = 0
            for i in range(bracket_start, len(text)):
                if text[i] == '[':
                    depth += 1
                elif text[i] == ']':
                    depth -= 1
                    if depth == 0:
                        candidate = text[bracket_start:i + 1]
                        try:
                            parsed = json.loads(candidate)
                            return self._normalize_to_list(parsed)
                        except json.JSONDecodeError:
                            pass
                        break

        # Try object
        brace_start = text.find('{')
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[brace_start:i + 1]
                        try:
                            parsed = json.loads(candidate)
                            return self._normalize_to_list(parsed)
                        except json.JSONDecodeError:
                            pass
                        break

        # Strategy 4: JSON repair — fix common small-model issues
        repaired = self._repair_json(text)
        if repaired is not None:
            return self._normalize_to_list(repaired)

        logger.warning(f"[FailureClassifierAgent] All JSON extraction strategies failed. "
                       f"Response preview: {text[:200]}")
        return []

    def _normalize_to_list(self, parsed) -> list:
        """Normalize any parsed JSON into a list of failure dicts.
        
        Handles:
          - list of dicts: return as-is
          - single dict with 'file'/'bug_type': wrap in list
          - dict containing an array value: extract and return the array
        """
        if isinstance(parsed, list):
            # Filter to only dicts that look like failure records
            return [item for item in parsed if isinstance(item, dict) and 
                    ("file" in item or "bug_type" in item)]
        
        if isinstance(parsed, dict):
            # Single failure object → wrap in list
            if "file" in parsed or "bug_type" in parsed:
                return [parsed]
            
            # Object wrapping an array (e.g., {"failures": [...]})
            for val in parsed.values():
                if isinstance(val, list) and len(val) > 0:
                    items = [item for item in val if isinstance(item, dict) and 
                             ("file" in item or "bug_type" in item)]
                    if items:
                        return items
        
        return []

    def _repair_json(self, text: str):
        """Attempt to repair malformed JSON from small models."""
        # Find the start of JSON
        start = -1
        for ch in ['[', '{']:
            idx = text.find(ch)
            if idx >= 0 and (start < 0 or idx < start):
                start = idx
        if start < 0:
            return None

        raw = text[start:]
        # Strip trailing markdown fences
        raw = re.sub(r'\n?```\s*$', '', raw)

        # Fix trailing commas before } or ]
        raw = re.sub(r',\s*([}\]])', r'\1', raw)

        # Try to close unclosed structures
        open_braces = raw.count('{') - raw.count('}')
        open_brackets = raw.count('[') - raw.count(']')

        # Truncate at last complete object if strings are unclosed
        # Find the last valid closing brace/bracket
        if open_braces > 0 or open_brackets > 0:
            # Try to find the last complete JSON object
            last_close = max(raw.rfind('}'), raw.rfind(']'))
            if last_close > 0:
                attempt = raw[:last_close + 1]
                # Balance remaining
                ob = attempt.count('{') - attempt.count('}')
                obr = attempt.count('[') - attempt.count(']')
                attempt += '}' * max(0, ob) + ']' * max(0, obr)
                try:
                    return json.loads(attempt)
                except json.JSONDecodeError:
                    pass

            # Last resort: close everything
            raw += '"' if raw.count('"') % 2 != 0 else ''
            raw += '}' * max(0, open_braces)
            raw += ']' * max(0, open_brackets)

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

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
