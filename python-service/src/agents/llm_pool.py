"""
LLM Key Pool: Round-robin rotation across multiple Gemini API keys.

Handles rate-limit resilience by cycling through keys so that when one key
is exhausted, the next one picks up.  Supports 429 auto-failover.

Usage in .env:
    GEMINI_API_KEYS=key1,key2,key3,key4,...
    # OR single key (backward compat):
    GEMINI_API_KEY=singlekey
"""

import os
import time
import threading
import logging
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class GeminiKeyPool:
    """Thread-safe round-robin pool of Gemini API keys."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Singleton — all agents share the same pool."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Collect keys from GEMINI_API_KEYS (comma-separated) or GEMINI_API_KEY
        keys_str = os.getenv("GEMINI_API_KEYS", "")
        if keys_str:
            self.keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        else:
            single = os.getenv("GEMINI_API_KEY", "")
            self.keys = [single] if single and single not in (
                "your_gemini_api_key_here", "YOUR_GEMINI_KEY_HERE"
            ) else []

        self._index = 0
        self._key_lock = threading.Lock()
        self._model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self._min_sleep = float(os.getenv("LLM_MIN_SLEEP", "4"))

        if self.keys:
            logger.info(
                f"[GeminiKeyPool] Initialized with {len(self.keys)} API key(s), "
                f"model={self._model_name}, min_sleep={self._min_sleep}s"
            )
        else:
            logger.warning("[GeminiKeyPool] No API keys found — LLM calls will fail.")

    @property
    def available(self) -> bool:
        return len(self.keys) > 0

    def _next_key(self) -> str:
        """Get the next key in round-robin order (thread-safe)."""
        with self._key_lock:
            key = self.keys[self._index % len(self.keys)]
            self._index += 1
            return key

    def get_model(self):
        """Get a configured GenerativeModel using the next key in the pool."""
        if not self.keys:
            return None
        key = self._next_key()
        genai.configure(api_key=key)
        return genai.GenerativeModel(self._model_name)

    def call_llm(self, prompt: str, max_retries: int = 3) -> str:
        """
        Call Gemini with automatic key rotation on rate-limit errors.

        Tries the next key on 429/quota errors, sleeping between attempts.
        Returns the response text, or raises the last exception.
        """
        if not self.keys:
            raise RuntimeError("No Gemini API keys configured.")

        last_error = None
        keys_tried = 0

        for attempt in range(max_retries):
            key = self._next_key()
            key_suffix = key[-6:]  # last 6 chars for logging

            # Rate limit spacing
            time.sleep(self._min_sleep)

            try:
                genai.configure(api_key=key)
                model = genai.GenerativeModel(self._model_name)
                response = model.generate_content(prompt)
                return response.text.strip()

            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                is_rate_limit = (
                    "429" in error_str or
                    ("resource" in error_str and "exhausted" in error_str) or
                    "quota" in error_str or
                    "rate" in error_str
                )

                if is_rate_limit:
                    keys_tried += 1
                    logger.warning(
                        f"[GeminiKeyPool] Key ...{key_suffix} rate-limited "
                        f"(attempt {attempt+1}/{max_retries}). Rotating to next key..."
                    )
                    # Short sleep before trying next key
                    if attempt < max_retries - 1:
                        time.sleep(2)
                    continue
                else:
                    # Non-rate-limit error — don't retry with different key
                    raise

        # All retries exhausted
        raise last_error


# Module-level singleton for easy import
pool = GeminiKeyPool()
