"""Fetch article HTML and extract plain text for LLM summarization (no extra dependencies)."""

from __future__ import annotations

import gzip
import html as html_module
import re
import urllib.error
import urllib.request


def maybe_decompress_body(raw: bytes) -> bytes:
    """Decode gzip-wrapped bodies when the server omits transparent urllib decoding."""
    if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B:
        try:
            return gzip.decompress(raw)
        except OSError:
            pass
    return raw


def fetch_url_bytes(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    user_agent: str,
    accept: str,
    accept_language: str,
) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": accept,
            "Accept-Language": accept_language,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — intentional URL fetch
            raw = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    return maybe_decompress_body(raw)


_CHARSET_RE = re.compile(rb'charset\s*=\s*["\']?([a-zA-Z0-9._-]+)', re.I)
_SCRIPT_STYLE_NOSCRIPT_RE = re.compile(r"<(script|style|noscript)\b[\s\S]*?</\1>", re.I)
_TAGS_RE = re.compile(r"<[^>]+>")


def decode_html_bytes(raw: bytes) -> str:
    enc_guess = "utf-8"
    m = _CHARSET_RE.search(raw[:12000])
    if m:
        try:
            enc_guess = m.group(1).decode("ascii").lower().strip()
        except Exception:
            enc_guess = "utf-8"
    if enc_guess in {"iso-8859-1", "iso8859-1"}:
        enc_guess = "latin-1"
    try:
        return raw.decode(enc_guess, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _approx_visible_len(html_frag: str) -> int:
    t = _SCRIPT_STYLE_NOSCRIPT_RE.sub(" ", html_frag)
    t = _TAGS_RE.sub(" ", t)
    return len(re.sub(r"\s+", " ", t).strip())


def _best_article_fragment(html: str) -> str:
    html = _SCRIPT_STYLE_NOSCRIPT_RE.sub(" ", html)
    candidates: list[str] = []
    for m in re.finditer(r"<article\b[^>]*>([\s\S]*?)</article>", html, re.I):
        candidates.append(m.group(1))
    for m in re.finditer(r"<main\b[^>]*>([\s\S]*?)</main>", html, re.I):
        candidates.append(m.group(1))
    if candidates:
        return max(candidates, key=_approx_visible_len)
    bm = re.search(r"<body\b[^>]*>([\s\S]*?)</body>", html, re.I)
    return bm.group(1) if bm else html


def html_to_plain_article_text(html: str, max_chars: int) -> str:
    frag = _best_article_fragment(html)
    frag = _SCRIPT_STYLE_NOSCRIPT_RE.sub(" ", frag)
    plain = _TAGS_RE.sub(" ", frag)
    plain = html_module.unescape(plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) > max_chars:
        plain = plain[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return plain


def fetch_article_plain_text(
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    max_chars: int,
    user_agent: str,
    accept_language: str,
) -> str:
    raw = fetch_url_bytes(
        url,
        timeout=timeout,
        max_bytes=max_bytes,
        user_agent=user_agent,
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        accept_language=accept_language,
    )
    html = decode_html_bytes(raw)
    return html_to_plain_article_text(html, max_chars)
