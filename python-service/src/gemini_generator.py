import os
import json
import logging
from fastapi import HTTPException
from dotenv import load_dotenv
from src.llm.client import LLMClient

load_dotenv()
logger = logging.getLogger(__name__)

# Use a global client instance (singleton)
client = LLMClient()

PROMPT_TEMPLATE = """
You are an API testing generator. Generate comprehensive test cases for the given API endpoints.

IMPORTANT RULES:
1. For authentication endpoints (register, signup, login, signin), generate tests FIRST
2. For protected endpoints, assume a valid auth token will be available
3. Include both positive tests (expected success) and negative tests (expected failure)
4. Use realistic expected status codes:
   - 200/201 for successful operations
   - 400 for bad requests/validation errors
   - 401 for unauthorized access
   - 403 for forbidden access
   - 404 for not found
   - 409 for conflicts (e.g., duplicate username)

Given API endpoints in JSON:
{endpoints}

Generate test cases in this EXACT JSON format:
[
  {{
    "name": "Descriptive test name",
    "method": "GET|POST|PUT|DELETE|PATCH",
    "path": "/api/path",
    "expected": 200,
    "description": "What this test verifies",
    "category": "positive|negative",
    "priority": "high|medium|low",
    "requiresAuth": true|false
  }}
]

ORDER the tests so that:
1. Register/Create user tests come first
2. Login/Auth tests come second  
3. All other tests come after (these will use the captured auth token)

Return ONLY the JSON array, no other text.
"""

async def generate_testcases_from_gemini(parsed, max_retries=3):
    """
    Generate test cases using the centralized LLMClient.
    Note: max_retries arg is kept for compatibility but client handles its own retries.
    """
    if not client.model:
        raise HTTPException(
            status_code=503,
            detail="Gemini API key not configured. Please set GEMINI_API_KEY in python-service/.env file."
        )
    
    endpoints = parsed["endpoints"]
    endpoints_json = json.dumps(endpoints, indent=2)
    prompt = PROMPT_TEMPLATE.format(endpoints=endpoints_json)
    
    logger.info(f"[GeminiGenerator] Sending prompt with {len(endpoints)} endpoints")

    # Client handles rate limiting and retries internally
    # Note: generate_content is blocking, but for this use case it's acceptable
    text = client.generate_content(prompt)

    if not text:
         raise HTTPException(
            status_code=500, 
            detail="Gemini API failed to generate content (Rate limit or Error)."
         )

    # Extract JSON from response
    try:
        start = text.find("[")
        end = text.rfind("]") + 1
        if start == -1 or end == 0:
            logger.error(f"[GeminiGenerator] No JSON array found: {text[:500]}")
            raise HTTPException(status_code=500, detail="Gemini did not return a valid JSON array")
        
        json_text = text[start:end]
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        logger.error(f"[GeminiGenerator] JSON parse error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to parse Gemini response as JSON: {str(e)}")
