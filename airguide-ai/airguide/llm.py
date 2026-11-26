"""Optional Gen-AI phrasing layer (Google Gemini API & Anthropic Messages API).

Supports:
- Google Gemini (GEMINI_API_KEY, default: gemini-1.5-flash)
- Anthropic Claude (ANTHROPIC_API_KEY, default: claude-3-5-sonnet-20241022)

Zero external dependencies (pure standard library).
Disabled unless GEMINI_API_KEY or ANTHROPIC_API_KEY is set.
Any failure returns None so the orchestrator cleanly falls back to the
deterministic logic engine: the assistant functions 100% offline.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-sonnet-20241022"
MAX_WORDS = 150


def active_provider() -> str:
    """Return human-readable identifier of the currently active Gen AI provider."""
    if os.environ.get("GEMINI_API_KEY"):
        model = os.environ.get("AIRGUIDE_MODEL", DEFAULT_GEMINI_MODEL)
        return f"Google Gemini ({model})"
    if os.environ.get("ANTHROPIC_API_KEY"):
        model = os.environ.get("AIRGUIDE_MODEL", DEFAULT_ANTHROPIC_MODEL)
        return f"Anthropic Claude ({model})"
    return "Deterministic Offline Logic Engine (Zero-LLM fallback)"


def enabled() -> bool:
    """True if either Gemini or Anthropic credentials are configured."""
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def _call_gemini(key: str, system: str, user_message: str, timeout: float) -> str | None:
    model = os.environ.get("AIRGUIDE_MODEL", DEFAULT_GEMINI_MODEL)
    url = f"{GEMINI_API_BASE}/{model}:generateContent"
    body = json.dumps(
        {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_message[:500]}],
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 300,
                "temperature": 0.2,
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read(200_000))
    candidates = payload.get("candidates", [])
    if not candidates:
        return None
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()


def _call_anthropic(key: str, system: str, user_message: str, timeout: float) -> str | None:
    model = os.environ.get("AIRGUIDE_MODEL", DEFAULT_ANTHROPIC_MODEL)
    body = json.dumps(
        {
            "model": model,
            "max_tokens": 300,
            "system": system,
            "messages": [{"role": "user", "content": user_message[:500]}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read(200_000))
    return "".join(
        block.get("text", "")
        for block in payload.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def generate(system: str, user_message: str, timeout: float = 15.0) -> str | None:
    """Generate dynamic assistant phrasing while enforcing word caps and offline fallbacks."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    if not gemini_key and not anthropic_key:
        return None

    try:
        if gemini_key:
            text = _call_gemini(gemini_key, system, user_message, timeout)
        else:
            text = _call_anthropic(anthropic_key, system, user_message, timeout)
    # TimeoutError is an OSError subclass and json.JSONDecodeError is a ValueError subclass.
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, AttributeError):
        return None

    if not text or len(text.split()) > MAX_WORDS:
        return None
    return text
