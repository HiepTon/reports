"""Google Gemini (AI Studio) chat client — a drop-in alternative to groq_summary.

Exposes the same names the fetch pipelines use (chat_text, is_daily_quota,
is_model_unavailable, is_transient, retry_sleep_seconds, RateLimit,
pace_delay_seconds, output_token_budget, input_char_budget_per_item, api_key,
DEFAULT_TPM_LIMIT, SUMMARY_MODEL_DEFAULT/FALLBACK), so the enrich loop can swap
providers by binding a different module.

Auth: GEMINI_API_KEY (from https://aistudio.google.com). Dependency-free (urllib).
Free tier is RPM/RPD-bound (TPM is generous), so pacing is by requests-per-minute.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

# Reuse the provider-agnostic helpers so behaviour matches Groq's pipeline exactly.
from groq_summary import (  # noqa: F401 — re-exported for the enrich pipeline
    RateLimit,
    USER_AGENT,
    input_char_budget_per_item,
    output_token_budget,
)

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Free-tier chat models (verified 2026-09). gemini-3.1-flash-lite: 15 RPM / 500 RPD, defaults
# to "minimal" thinking (fast + cheap, ideal for extraction). Fallback gemini-2.5-flash-lite
# has its own separate daily quota. Both share ~250K TPM. IDs churn — override with --summary-model.
DISPLAY_NAME = "Gemini"
SUMMARY_MODEL_DEFAULT = "gemini-3.1-flash-lite"
SUMMARY_MODEL_FALLBACK_DEFAULT = "gemini-2.5-flash-lite"
DEFAULT_RPM_LIMIT = 15        # gemini-3.1-flash-lite free tier (the primary)
DEFAULT_TPM_LIMIT = 250000    # generous; input is capped by --gemini-max-article-chars anyway
# High TPM lets us merge many items per request (fewer requests → fewer RPD + less overhead);
# the big output budget leaves room for that many summaries in one response.
DEFAULT_CHUNK_SIZE = 12
DEFAULT_MAX_OUTPUT_TOKENS = 8192


class GeminiError(RuntimeError):
    """Carries the HTTP status (when known) so callers can detect retryable errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def api_key() -> str | None:
    return (os.environ.get("GEMINI_API_KEY") or "").strip() or None


def is_daily_quota(exc: BaseException) -> bool:
    """A 429 that is a daily request/token cap (won't refill within the run)."""
    if getattr(exc, "status", None) != 429:
        return False
    m = str(exc).lower()
    return "perday" in m or "per day" in m or "requests per day" in m or ("quota" in m and "day" in m)


def is_model_unavailable(exc: BaseException) -> bool:
    """404 / not-found / unsupported model id — retire it for the run."""
    if getattr(exc, "status", None) == 404:
        return True
    m = str(exc).lower()
    return "not found" in m or "does not exist" in m or "is not supported" in m or "not supported for" in m


def is_transient(exc: BaseException) -> bool:
    """Per-minute 429 and 5xx/overload are worth a backoff retry (daily caps are not)."""
    if is_daily_quota(exc):
        return False
    st = getattr(exc, "status", None)
    if st == 429:
        return True
    if isinstance(st, int) and 500 <= st < 600:
        return True
    m = str(exc).lower()
    return any(s in m for s in ("overloaded", "unavailable", "try again", "deadline", "temporarily"))


def retry_sleep_seconds(exc: BaseException, attempt: int) -> float:
    """Honor a numeric retryDelay if present, else exponential backoff."""
    ra = getattr(exc, "retry_after", None)
    if isinstance(ra, (int, float)) and ra > 0:
        return min(max(float(ra) + 1.0, 2.0), 120.0)
    return min(6.0 * (2**attempt), 120.0)


def pace_delay_seconds(
    rl: RateLimit,
    next_request_tokens: int,
    *,
    tpm_limit: int = DEFAULT_TPM_LIMIT,
    rpm_limit: int = DEFAULT_RPM_LIMIT,
    buffer_s: float = 0.5,
    fallback_s: float = 0.0,
) -> float:
    """Wait enough to respect BOTH the RPM cap and the TPM cap.

    Gemini returns no per-response token headers, so we estimate: the per-request floor is
    60/RPM, and a request of N tokens needs ~60*N/TPM seconds to refill under the TPM budget.
    Merging many items into one request raises N (so tpm_gap grows) but cuts the request count.
    """
    rpm_gap = 60.0 / max(1, rpm_limit)
    tpm_gap = 60.0 * max(0, next_request_tokens) / max(1, tpm_limit)
    return max(rpm_gap, tpm_gap) + buffer_s


def chat_text(
    *,
    token: str,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float = 0.35,
    timeout_s: int = 60,
    system: str | None = None,
    reasoning_effort: str | None = None,
    json_object: bool = False,
    rate_out: RateLimit | None = None,  # noqa: ARG001 — Gemini has no per-response token headers
) -> str:
    """POST a single-turn generateContent request and return the model's text."""
    gen_cfg: dict = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if json_object:
        gen_cfg["responseMimeType"] = "application/json"
    # Keep "thinking" low so hidden reasoning does not eat the output budget and return empty
    # text. Gemini 3.x uses thinkingConfig.thinkingLevel; 2.5 uses the legacy thinkingBudget.
    if "gemini-3" in model:
        lvl = reasoning_effort if reasoning_effort in ("minimal", "low", "medium", "high") else "low"
        gen_cfg["thinkingConfig"] = {"thinkingLevel": lvl}
    elif "2.5-flash" in model:
        gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
    body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        GEMINI_ENDPOINT.format(model=model),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-goog-api-key": token,
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
        err = GeminiError(f"HTTP {exc.code}: {detail or exc.reason}", status=exc.code)
        m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', detail)
        if m:
            err.retry_after = float(m.group(1))  # type: ignore[attr-defined]
        raise err from exc
    except urllib.error.URLError as exc:
        raise GeminiError(f"Network error: {exc.reason}") from exc

    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    except (KeyError, IndexError, TypeError) as exc:
        # Blocked prompt, safety stop, or empty candidate — surface for retry/fallback.
        raise GeminiError(f"Unexpected Gemini response: {json.dumps(data)[:300]}") from exc
    return (text or "").strip()
