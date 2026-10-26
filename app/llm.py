"""The only place the app talks to an LLM.

Uses the openai library against any OpenAI-compatible endpoint (currently
Gemini), configured from .env via LLM_API_KEY, LLM_BASE_URL and LLM_MODEL,
so the provider or model can be swapped without code changes.
"""

import os
import time

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

load_dotenv()

TIMEOUT = 60
RATE_LIMIT_WAITS = (5, 15, 30)  # seconds before each retry on HTTP 429


def _env(name):
    """Return an env var, or raise ValueError naming it (never the value)."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing environment variable: {name}")
    return value


def get_client():
    """Build an OpenAI client from LLM_API_KEY and LLM_BASE_URL."""
    api_key = _env("LLM_API_KEY")
    base_url = _env("LLM_BASE_URL")
    # max_retries=0: chat() owns retry policy
    return OpenAI(api_key=api_key, base_url=base_url, timeout=TIMEOUT, max_retries=0)


def chat(messages, tools=None):
    """Send a chat completion with LLM_MODEL and return the assistant message.

    Retries on HTTP 429 after 5s, 15s and 30s, then re-raises.
    """
    client = get_client()
    model = _env("LLM_MODEL")
    kwargs = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    for attempt in range(len(RATE_LIMIT_WAITS) + 1):
        try:
            return client.chat.completions.create(**kwargs).choices[0].message
        except RateLimitError:
            if attempt == len(RATE_LIMIT_WAITS):
                raise
            time.sleep(RATE_LIMIT_WAITS[attempt])
