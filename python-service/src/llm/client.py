import os
import time
import logging
import asyncio
import json
import re
from typing import Optional, Any, Dict, List, Union
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
logger = logging.getLogger(__name__)

class LLMClient:
    """
    Centralized LLM client with robust rate limiting and retry logic.
    Attributes:
        model: The underlying GenAI model instance.
        provider: 'gemini' (default) or 'xai' (future support).
        min_sleep: Minimum seconds to sleep between ANY call (Token Bucket).
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LLMClient, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        
        # Rate limiting config
        # Default to 4s for free tier safety (15 RPM = 1 req / 4s)
        self.min_sleep = float(os.getenv("LLM_MIN_SLEEP", "4.0")) 
        self.last_call_time = 0.0
        
        if not self.api_key or "your_key" in self.api_key.lower():
            logger.warning("[LLMClient] No valid GEMINI_API_KEY found.")
            self.model = None
        else:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(self.model_name)
            logger.info(f"[LLMClient] Initialized with model {self.model_name}, min_sleep={self.min_sleep}s")

        self.input_token_limit = 30000  # Safety cap for Gemini Flash
        self._initialized = True

    def _wait_for_rate_limit(self):
        """Enforce strict minimum interval between calls (Token Bucket style)."""
        elapsed = time.time() - self.last_call_time
        if elapsed < self.min_sleep:
            sleep_time = self.min_sleep - elapsed
            logger.debug(f"[LLMClient] Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        self.last_call_time = time.time()

    def generate_content(self, prompt: str, max_retries: int = 5) -> str:
        """
        Generate content with exponential backoff for 429/503 errors.
        Returns empty string on total failure.
        """
        if not self.model:
            logger.error("[LLMClient] Model not initialized (missing API key).")
            return ""

        # Truncate prompt if too huge (basic safety)
        if len(prompt) > (self.input_token_limit * 4):
            logger.warning(f"[LLMClient] Prompt too long ({len(prompt)} chars), truncating.")
            prompt = prompt[:(self.input_token_limit * 4)]

        for attempt in range(max_retries):
            self._wait_for_rate_limit()
            
            try:
                # Call API
                if os.getenv("DEBUG_LLM"):
                    logger.info(f"[LLMClient] Sending request (Attempt {attempt+1}/{max_retries})...")
                
                response = self.model.generate_content(prompt)
                
                # Check for safety blocks or empty response
                if not response.parts:
                    logger.warning("[LLMClient] Empty response (possibly safety block).")
                    return ""
                    
                text = response.text
                if not text:
                     logger.warning("[LLMClient] Response object valid but text is empty.")
                     return ""
                     
                return text

            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = (
                    "429" in error_str or 
                    "quota" in error_str or 
                    "resource exhausted" in error_str or
                    "too many requests" in error_str
                )
                
                if is_rate_limit:
                    wait_time = (2 ** attempt) * 5  # Exponential: 5s, 10s, 20s, 40s...
                    logger.warning(
                        f"\n⚠️  GEMINI RATE LIMIT (Attempt {attempt+1}/{max_retries}) ⚠️"
                        f"\n   Sleeping {wait_time}s before retry..."
                    )
                    time.sleep(wait_time)
                    # "Refill" the bucket timestamp so we don't double-wait
                    self.last_call_time = time.time()
                else:
                    logger.error(f"[LLMClient] Error generating content: {e}")
                    # For non-rate-limit errors (500, 503), try a short sleep/retry
                    if attempt < max_retries - 1:
                        time.sleep(2)
                    else:
                        return ""

        logger.error("[LLMClient] Max retries exceeded.")
        return ""

    def generate_json(self, prompt: str, default_value: Any = None) -> Any:
        """
        Helper to generate and parse JSON safely.
        """
        text = self.generate_content(prompt)
        if not text:
            return default_value

        # Clean markdown code blocks
        text = re.sub(r"^```[a-z]*\n?", "", text.strip())
        text = re.sub(r"\n?```$", "", text.strip())

        try:
            return json.loads(text)
        except json.JSONDecodeError:
             logger.warning(f"[LLMClient] Failed to parse JSON from response: {text[:200]}...")
             return default_value
