import os
import google.generativeai as genai
from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here" or GEMINI_API_KEY == "YOUR_GEMINI_KEY_HERE":
    print("WARNING: GEMINI_API_KEY not configured! Please set it in python-service/.env")
    MODEL = None
else:
    genai.configure(api_key=GEMINI_API_KEY)
    # Using gemini-2.5-flash model
    MODEL = genai.GenerativeModel("gemini-2.5-flash")

PROMPT_TEMPLATE = """
You are a senior QA engineer and an expert CI/CD test case generator for Cognitest, an autonomous DevOps healing agent built for the RIFT 2026 Hackathon.

Your goal is to generate highly comprehensive, production-grade API test cases from the given Swagger/OpenAPI endpoint definitions. The tests will be executed via Newman (Postman CLI) to verify API correctness. The healing agent will then automatically detect, classify, and fix any failures.

═══════════════════════════════════════════════════════
 BUG TYPES THE AGENT CAN DETECT & FIX
═══════════════════════════════════════════════════════
When generating tests, keep in mind the agent classifies bugs into EXACTLY these categories:

1. LINTING
   - Unused imports (e.g., `import os` when os is never used)
   - Unused variables, dead code, naming convention violations
   - Example: "Unused import 'os'" in src/utils.py line 15

2. SYNTAX
   - Missing colons (`:`) at end of function/class/if/for/while definitions
   - Missing or unmatched brackets `()`, `[]`, `{{}}`
   - Missing semicolons (JS), missing commas in dicts/objects
   - Example: "SyntaxError: expected ':'" in src/validator.py line 8

3. LOGIC
   - Wrong return values (returning wrong variable)
   - Incorrect calculations (wrong operator: `+` instead of `*`)
   - Flawed boolean conditions (`and` instead of `or`, wrong comparisons)
   - Off-by-one errors, wrong loop bounds
   - Failed test assertions (expected 200 got 500)
   - Example: "AssertionError: expected 42 but got 0"

4. TYPE_ERROR
   - Passing string where int expected, or vice versa
   - Calling methods on wrong types (`None.strip()`)
   - Missing type conversions (`int()`, `str()`, `JSON.parse()`)
   - Example: "TypeError: unsupported operand type(s)"

5. IMPORT
   - Missing import statements (`NameError: name 'json' is not defined`)
   - Wrong import paths (`from utils import X` when X doesn't exist)
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
   - Authentication-aware: mark `requiresAuth: true` for protected endpoints
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

import asyncio
import re
import json

async def generate_testcases_from_gemini(parsed, max_retries=3):
    if MODEL is None:
        raise HTTPException(
            status_code=503,
            detail="Gemini API key not configured. Please set GEMINI_API_KEY in python-service/.env file. Get your key from https://makersuite.google.com/app/apikey"
        )
    
    endpoints = parsed["endpoints"]
    # Convert endpoints to clean JSON string for the prompt
    endpoints_json = json.dumps(endpoints, indent=2)
    prompt = PROMPT_TEMPLATE.format(endpoints=endpoints_json)
    
    print(f"DEBUG: Sending prompt with {len(endpoints)} endpoints")
    print(f"DEBUG: Prompt length: {len(prompt)} chars")

    for attempt in range(max_retries):
        try:
            response = MODEL.generate_content(prompt)
            text = response.text.strip()
            print(f"DEBUG: Received response, length: {len(text)} chars")
            break
        except Exception as e:
            error_msg = str(e)
            print(f"DEBUG: Full error: {error_msg}")
            
            if "API_KEY_INVALID" in error_msg or "API key not valid" in error_msg:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid Gemini API key. Please check your GEMINI_API_KEY in python-service/.env"
                )
            
            # Handle rate limiting with retry
            if "429" in error_msg or "quota" in error_msg.lower() or "rate" in error_msg.lower():
                # Extract retry delay if available
                retry_match = re.search(r'retry in (\d+\.?\d*)', error_msg.lower())
                wait_time = float(retry_match.group(1)) if retry_match else (15 * (attempt + 1))
                
                if attempt < max_retries - 1:
                    print(f"Rate limited. Waiting {wait_time}s before retry {attempt + 2}/{max_retries}...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    raise HTTPException(
                        status_code=429,
                        detail=f"Gemini API rate limit exceeded. Please wait a moment and try again, or use a different API key."
                    )
            
            # For any other error, show the full error message
            raise HTTPException(status_code=500, detail=f"Gemini API error: {error_msg}")

    # Extract JSON from response
    try:
        start = text.find("[")
        end = text.rfind("]") + 1
        if start == -1 or end == 0:
            print(f"DEBUG: Could not find JSON array in response: {text[:500]}")
            raise HTTPException(status_code=500, detail="Gemini did not return a valid JSON array")
        
        json_text = text[start:end]
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        print(f"DEBUG: JSON parse error: {e}")
        print(f"DEBUG: Raw text: {text[:500]}")
        raise HTTPException(status_code=500, detail=f"Failed to parse Gemini response as JSON: {str(e)}")