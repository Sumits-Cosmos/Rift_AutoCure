"""
Unified LLM client that supports both Google Gemini and xAI Grok.

Provider selection:
  1. LLM_PROVIDER env var ("gemini" or "xai")
  2. Auto-detect: whichever API key is present (XAI_API_KEY > GEMINI_API_KEY)

Usage:
    from src.llm_client import get_llm_client
    client = get_llm_client()
    if client:
        text = client.generate("Your prompt here")
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class _GeminiClient:
    """Wrapper around Google Generative AI (Gemini) SDK."""

    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash"):
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)
        self.provider = "gemini"
        self.model_name = model_name
        logger.info(f"[LLMClient] Initialized Gemini provider (model={model_name})")

    def generate(self, prompt: str) -> str:
        """Generate text from a prompt. Returns the raw text response."""
        response = self.model.generate_content(prompt)
        return response.text.strip()


class _XAIClient:
    """Wrapper around xAI Grok API using the OpenAI-compatible SDK."""

    BASE_URL = "https://api.x.ai/v1"

    def __init__(self, api_key: str, model_name: str = "grok-3-mini-fast"):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=self.BASE_URL)
        self.provider = "xai"
        self.model_name = model_name
        logger.info(f"[LLMClient] Initialized xAI/Grok provider (model={model_name})")

    def generate(self, prompt: str) -> str:
        """Generate text from a prompt. Returns the raw text response."""
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()


# ── Singleton cache ────────────────────────────────────────────────────────────
_cached_client = None
_cached_provider = None


def get_llm_client():
    """
    Returns a unified LLM client (Gemini or xAI) based on env config.
    Returns None if no valid API key is available.
    """
    global _cached_client, _cached_provider

    provider = os.getenv("LLM_PROVIDER", "").lower().strip()
    xai_key = os.getenv("XAI_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()

    # Filter out placeholder values
    placeholder_vals = {"your_gemini_api_key_here", "YOUR_GEMINI_KEY_HERE", ""}
    if gemini_key in placeholder_vals:
        gemini_key = ""
    if xai_key in placeholder_vals:
        xai_key = ""

    # Determine provider
    if not provider:
        if xai_key:
            provider = "xai"
        elif gemini_key:
            provider = "gemini"
        else:
            logger.warning("[LLMClient] No API key found for any LLM provider.")
            return None

    # Return cached client if provider hasn't changed
    if _cached_client and _cached_provider == provider:
        return _cached_client

    try:
        if provider == "xai" and xai_key:
            _cached_client = _XAIClient(api_key=xai_key)
            _cached_provider = provider
        elif provider == "gemini" and gemini_key:
            _cached_client = _GeminiClient(api_key=gemini_key)
            _cached_provider = provider
        else:
            logger.warning(f"[LLMClient] Provider '{provider}' selected but no API key found.")
            return None
    except Exception as e:
        logger.error(f"[LLMClient] Failed to initialize {provider} client: {e}")
        return None

    return _cached_client
