"""Groq chat-completions client (OpenAI-compatible) for baked summaries.

Server-side only (runs in CI). Auth uses a Groq API key from GROQ_API_KEY.
Endpoint is OpenAI-compatible; model ids follow `vendor/model`
(e.g. `openai/gpt-oss-120b`). Kept dependency-free (urllib) so the fetch
scripts need no extra install.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

# Groq free-tier models (Llama 3.x ids were deprecated mid-2026 in favor of gpt-oss).
SUMMARY_MODEL_DEFAULT = "openai/gpt-oss-120b"
SUMMARY_MODEL_FALLBACK_DEFAULT = "openai/gpt-oss-20b"

# api.groq.com sits behind Cloudflare, which returns "Error 1010: browser_signature_banned"
# for the default urllib User-Agent (Python-urllib/x.y). Send a normal browser UA instead.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


# Groq free tier caps tokens-per-minute (TPM) per request+minute; gpt-oss on_demand is 8000.
# A request whose (instructions + input + reserved output) tokens exceed this is rejected 413.
DEFAULT_TPM_LIMIT = 8000
_CHARS_PER_TOKEN = 2.0  # conservative (low) so estimates run high and we stay under the cap


def output_token_budget(n_items: int, max_output_tokens: int) -> int:
    """Reserve a small output budget proportional to article count (each summary is short)."""
    return max(256, min(max_output_tokens, 256 + 240 * max(1, n_items)))


def input_char_budget_per_item(
    n_items: int,
    *,
    output_tokens: int,
    tpm_limit: int = DEFAULT_TPM_LIMIT,
    instructions_reserve_tokens: int = 600,
    safety: float = 0.85,
) -> int:
    """Max chars per article so instructions + input + reserved output stay under the TPM cap."""
    n = max(1, n_items)
    budget_tokens = tpm_limit * safety - output_tokens - instructions_reserve_tokens
    budget_tokens = max(budget_tokens, 400)
    return max(300, int(budget_tokens * _CHARS_PER_TOKEN / n))


class GroqError(RuntimeError):
    """Carries the HTTP status (when known) so callers can detect retryable errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def groq_api_key() -> str | None:
    return (os.environ.get("GROQ_API_KEY") or "").strip() or None


def is_transient(exc: BaseException) -> bool:
    """429 (rate limit) and 5xx are worth a backoff/fallback retry."""
    status = getattr(exc, "status", None)
    if status == 429:
        return True
    if isinstance(status, int) and 500 <= status < 600:
        return True
    msg = str(exc).lower()
    return any(n in msg for n in ("rate limit", "too many requests", "overloaded", "temporarily unavailable"))


def retry_sleep_seconds(exc: BaseException, attempt: int) -> float:
    """Honor a numeric Retry-After if present, else exponential backoff."""
    ra = getattr(exc, "retry_after", None)
    if isinstance(ra, (int, float)) and ra > 0:
        return min(max(float(ra) + 1.0, 4.0), 120.0)
    return min(6.0 * (2**attempt), 120.0)


def chat_text(
    *,
    token: str,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float = 0.35,
    timeout_s: int = 60,
    system: str | None = None,
) -> str:
    """POST a single-turn chat completion and return the assistant message text."""
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = json.dumps(
        {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    ).encode("utf-8")
    req = urllib.request.Request(
        GROQ_ENDPOINT,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 — intentional API call
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:  # noqa: BLE001
            pass
        err = GroqError(f"HTTP {exc.code}: {detail or exc.reason}", status=exc.code)
        ra = exc.headers.get("Retry-After") if exc.headers else None
        if ra and str(ra).strip().isdigit():
            err.retry_after = int(str(ra).strip())  # type: ignore[attr-defined]
        raise err from exc
    except urllib.error.URLError as exc:
        raise GroqError(f"Network error: {exc.reason}") from exc

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GroqError(f"Unexpected response shape: {json.dumps(data)[:300]}") from exc
    return (text or "").strip()
