"""
Reusable Ollama LLM client for local inference.

Uses HTTP calls to Ollama API at localhost:11434.
Model: deepseek-coder:6b (configurable via env).
Fully offline — no cloud API keys needed.
"""
import os
import time
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

# Configurable via environment variables
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "deepseek-coder:6.7b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))  # seconds


class OllamaClient:
    """Reusable client for Ollama local LLM inference."""

    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None):
        self.model = model or OLLAMA_MODEL
        self.base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")
        self.generate_url = f"{self.base_url}/api/generate"
        self._available = None  # lazy health check
        logger.info(f"[OllamaClient] Configured: model={self.model}, url={self.base_url}")

    # ── Public API ───────────────────────────────────────────────────────────

    def generate(self, prompt: str, json_mode: bool = False,
                 temperature: float = 0.2, max_retries: int = 2) -> Optional[str]:
        """You are a senior QA engineer and an expert CI/CD test case generator for Cognitest, an autonomous DevOps healing agent built for the RIFT 2026 Hackathon.

Your goal is to generate highly comprehensive, production-grade API test cases from the given Swagger/OpenAPI endpoint definitions. The tests will be executed via Newman (Postman CLI) to verify API correctness. The healing agent will then automatically detect, classify, and fix any failures.

═══════════════════════════════════════════════════════
 BUG TYPES THE AGENT CAN DETECT & FIX
═══════════════════════════════════════════════════════
When generating tests, keep in mind the agent classifies bugs into EXACTLY these categories:

1. LINTING
   - Unused imports (e.g., import os when os is never used)
   - Unused variables, dead code, naming convention violations
   - Example: "Unused import 'os'" in src/utils.py line 15

2. SYNTAX
   - Missing colons (:) at end of function/class/if/for/while definitions
   - Missing or unmatched brackets (), [], {{}}
   - Missing semicolons (JS), missing commas in dicts/objects
   - Example: "SyntaxError: expected ':'" in src/validator.py line 8

3. LOGIC
   - Wrong return values (returning wrong variable)
   - Incorrect calculations (wrong operator: + instead of *)
   - Flawed boolean conditions (and instead of or, wrong comparisons)
   - Off-by-one errors, wrong loop bounds
   - Failed test assertions (expected 200 got 500)
   - Example: "AssertionError: expected 42 but got 0"

4. TYPE_ERROR
   - Passing string where int expected, or vice versa
   - Calling methods on wrong types (None.strip())
   - Missing type conversions (int(), str(), JSON.parse())
   - Example: "TypeError: unsupported operand type(s)"

5. IMPORT
   - Missing import statements (NameError: name 'json' is not defined)
   - Wrong import paths (from utils import X when X doesn't exist)
   - Circular imports causing ImportError
   - Example: "ImportError: cannot import name 'process_data'"

6. INDENTATION
   - Unexpected indent / dedent in Python
   - Mixed tabs and spaces
   - Example: "IndentationError: unexpected indent"

═══════════════════════════════════════════════════════
 API ENDPOINTS TO TEST
═══════════════════════════════════════════════════════
{endpoints}

═══════════════════════════════════════════════════════
 TEST CASE GENERATION RULES
═══════════════════════════════════════════════════════

1. ORDERING (critical for test execution):
   a. Registration / Sign-up endpoints FIRST (creates users)
   b. Login / Authentication endpoints SECOND (gets auth tokens)
   c. CRUD operations on resources THIRD (use captured token)
   d. Edge cases and negative tests LAST

2. COVERAGE REQUIREMENTS:
   - At least 1 positive test (happy path) per endpoint
   - At least 1 negative test per endpoint (missing fields, wrong types, unauthorized)
   - Authentication-aware: mark requiresAuth: true for protected endpoints
   - Test all HTTP methods that each endpoint supports

3. STATUS CODE EXPECTATIONS:
   - 200: Successful GET, PUT, PATCH
   - 201: Successful POST (resource created)
   - 204: Successful DELETE (no content)
   - 400: Missing required fields, validation errors, malformed request body
   - 401: Missing or invalid authentication token
   - 403: Forbidden (wrong role/permissions)
   - 404: Resource not found, invalid ID
   - 409: Conflict (duplicate username, email already exists)
   - 422: Unprocessable entity (valid JSON but fails business rules)
   - 500: Server error (should NOT happen if code is correct)

4. REQUEST BODY RULES:
   - For POST/PUT/PATCH: always include realistic sample request bodies
   - Use realistic field names: name, email, password, title, description, etc.
   - Include edge cases: empty strings, null values, very long strings
   - For negative tests: omit required fields, send wrong data types

5. QUALITY STANDARDS:
   - Each test name must be unique and descriptive
   - Descriptions must clearly state WHAT is being tested and WHY
   - Prioritize tests that are most likely to catch real bugs like:
     Logic errors in calculations, wrong status codes, missing validations,
     type coercion issues, missing error handling

═══════════════════════════════════════════════════════
 RESPONSE FORMAT (strict JSON)
═══════════════════════════════════════════════════════
Return ONLY a JSON array with NO surrounding text, NO markdown, NO comments.

[
  {{
    "name": "Register new user with valid data",
    "method": "POST",
    "path": "/api/auth/register",
    "expected": 201,
    "description": "Verifies user registration with valid email and password returns 201",
    "category": "positive",
    "priority": "high",
    "requiresAuth": false,
    "body": {{"username": "testuser", "email": "test@example.com", "password": "SecurePass123!"}}
  }},
  {{
    "name": "Register with duplicate email should fail",
    "method": "POST",
    "path": "/api/auth/register",
    "expected": 409,
    "description": "Verifies duplicate email returns 409 conflict error",
    "category": "negative",
    "priority": "high",
    "requiresAuth": false,
    "body": {{"username": "testuser2", "email": "test@example.com", "password": "SecurePass123!"}}
  }},
  {{
    "name": "Login with valid credentials",
    "method": "POST",
    "path": "/api/auth/login",
    "expected": 200,
    "description": "Verifies login returns auth token for valid credentials",
    "category": "positive",
    "priority": "high",
    "requiresAuth": false,
    "body": {{"email": "test@example.com", "password": "SecurePass123!"}}
  }},
  {{
    "name": "Get all items requires authentication",
    "method": "GET",
    "path": "/api/items",
    "expected": 401,
    "description": "Verifies unauthenticated request to protected endpoint returns 401",
    "category": "negative",
    "priority": "medium",
    "requiresAuth": false
  }},
  {{
    "name": "Get all items with valid token",
    "method": "GET",
    "path": "/api/items",
    "expected": 200,
    "description": "Verifies authenticated GET returns list of items",
    "category": "positive",
    "priority": "high",
    "requiresAuth": true
  }}
]

Generate as many tests as needed to thoroughly cover ALL endpoints. Aim for at least 3-5 tests per endpoint (mix of positive and negative).

Return ONLY the JSON array.
        """
        prompt_len = len(prompt)
        logger.info(f"[OllamaClient] Generating | model={self.model} | prompt_length={prompt_len} chars | json_mode={json_mode}")

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }
        
        # Force JSON output — Ollama will ensure valid JSON structure
        if json_mode:
            payload["format"] = "json"

        for attempt in range(1, max_retries + 1):
            start_time = time.time()
            try:
                response = requests.post(
                    self.generate_url,
                    json=payload,
                    timeout=OLLAMA_TIMEOUT,
                )
                elapsed = round(time.time() - start_time, 2)

                if response.status_code != 200:
                    logger.error(
                        f"[OllamaClient] HTTP {response.status_code} from Ollama: "
                        f"{response.text[:300]}"
                    )
                    if attempt < max_retries:
                        logger.info(f"[OllamaClient] Retrying (attempt {attempt + 1}/{max_retries})...")
                        continue
                    return None

                data = response.json()
                text = data.get("response", "")
                
                if not text.strip():
                    logger.warning(f"[OllamaClient] Empty response on attempt {attempt}/{max_retries}")
                    if attempt < max_retries:
                        continue
                    return None

                logger.info(
                    f"[OllamaClient] ✅ Response received | "
                    f"time={elapsed}s | response_length={len(text)} chars | attempt={attempt}"
                )
                return text

            except requests.ConnectionError:
                logger.error(
                    "\n" + "=" * 60 +
                    "\n❌  OLLAMA NOT RUNNING  ❌"
                    f"\n   Could not connect to {self.base_url}"
                    "\n   Start Ollama with: ollama serve"
                    f"\n   Then pull the model: ollama pull {self.model}"
                    "\n" + "=" * 60
                )
                return None

            except requests.Timeout:
                elapsed = round(time.time() - start_time, 2)
                logger.error(
                    f"[OllamaClient] ⏱️ Request timed out after {elapsed}s "
                    f"(limit: {OLLAMA_TIMEOUT}s). "
                    f"Consider increasing OLLAMA_TIMEOUT or using a smaller model."
                )
                if attempt < max_retries:
                    logger.info(f"[OllamaClient] Retrying (attempt {attempt + 1}/{max_retries})...")
                    continue
                return None

            except Exception as e:
                elapsed = round(time.time() - start_time, 2)
                logger.error(f"[OllamaClient] Unexpected error after {elapsed}s: {e}")
                return None
        
        return None

    def is_available(self) -> bool:
        """Check if Ollama server is running and the model is available."""
        if self._available is not None:
            return self._available
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                # Check if our model (or a prefix match) is available
                model_base = self.model.split(":")[0]
                self._available = any(model_base in m for m in models)
                if not self._available:
                    logger.warning(
                        f"[OllamaClient] Model '{self.model}' not found. "
                        f"Available: {models}. Run: ollama pull {self.model}"
                    )
                return self._available
        except Exception:
            self._available = False
        return False


# ── Module-level singleton ───────────────────────────────────────────────────

_default_client: Optional[OllamaClient] = None


def get_client() -> OllamaClient:
    """Get or create the default OllamaClient singleton."""
    global _default_client
    if _default_client is None:
        _default_client = OllamaClient()
    return _default_client
