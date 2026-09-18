"""Shared config helpers: per-source item caps and keyword exclusion for news fetchers.

Feeds JSON may set a top-level "exclude_keywords" list and a per-feed "max_items":

    {
      "exclude_keywords": ["sponsored", "webinar"],
      "feeds": {
        "example": {"title": "…", "feed_url": "…", "max_items": 3}
      }
    }

CLI flags (--exclude-keywords) merge with the config list; --per-source is the
global fallback when a feed has no "max_items".
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path


def normalize_keywords(values: Iterable[object]) -> list[str]:
    """Lowercase, strip, and dedupe keywords, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values or []:
        k = str(v).strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def parse_cli_keywords(raw: str | None) -> list[str]:
    """Split a comma-separated --exclude-keywords value into normalized keywords."""
    if not raw:
        return []
    return normalize_keywords(raw.split(","))


def load_config_exclude_keywords(path: Path) -> list[str]:
    """Read top-level 'exclude_keywords' (list) from a feeds JSON config; [] if absent."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw = data.get("exclude_keywords") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return normalize_keywords(raw)


def parse_max_items(meta: dict) -> int | None:
    """Optional per-feed cap from config ('max_items'); None means use the global default."""
    raw = meta.get("max_items")
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def effective_cap(feed_max: int | None, per_source: int) -> int:
    """Per-feed cap wins when set; otherwise fall back to the global --per-source value."""
    return feed_max if feed_max is not None else per_source


def is_excluded(text: str, keywords: list[str]) -> bool:
    """True when any keyword appears (case-insensitive substring) in text."""
    if not keywords:
        return False
    low = (text or "").lower()
    return any(kw in low for kw in keywords)
