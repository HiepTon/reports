#!/usr/bin/env python3
"""
Fetch recent headlines from major security news RSS/Atom feeds.

Produces topic (title), summary, analysis, and canonical link. By default summaries come from RSS
and analysis from local heuristics; with --summarize and GROQ_API_KEY, Groq (an LLM) rewrites summary
and analysis using plain text extracted from each article page (see --gemini-no-fetch-article to use
RSS only). Requests run in small batches with pauses and retries for free-tier limits.

Usage:
  pip install -r requirements-security-news.txt
  python scripts/fetch_security_news.py
  python scripts/fetch_security_news.py --limit 10 --json
  python scripts/fetch_security_news.py --html output/security_news.html
  python scripts/fetch_security_news.py --days 7 --html output/security_news.html
  python scripts/fetch_security_news.py --sources bleepingcomputer,cisa
  python scripts/fetch_security_news.py --feeds-config /path/to/feeds.json
  GROQ_API_KEY=... python scripts/fetch_security_news.py --summarize --html out.html
"""
from __future__ import annotations

import argparse
import html as html_module
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import feedparser

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from digest_reader_embed import (
    READ_NEWS_AZURE_VOICE_EN_DEFAULT,
    READ_NEWS_AZURE_VOICE_FALLBACK_EN_DEFAULT,
    digest_reader_css,
    digest_reader_script,
    digest_reader_sdk_script_tag,
    digest_reader_toolbar_inner,
)
from digest_rebuild_embed import (
    digest_rebuild_css,
    digest_rebuild_script,
    digest_rebuild_toolbar_inner,
)
from news_filters import (
    effective_cap,
    is_excluded,
    load_config_exclude_keywords,
    parse_cli_keywords,
    parse_max_items,
)
import groq_summary as groq
import gemini_summary

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FEEDS_PATH = _REPO_ROOT / "config" / "security_news_feeds.json"

# Summary providers selectable via --summary-provider / SUMMARY_PROVIDER.
_SUMMARY_PROVIDERS = {"groq": groq, "gemini": gemini_summary}


def default_feeds_config_path() -> Path:
    return _DEFAULT_FEEDS_PATH


def load_feeds(path: Path) -> dict[str, tuple[str, str, int | None]]:
    """Load feed id -> (display title, rss_or_atom_url, per-feed max_items) from JSON config."""
    if not path.is_file():
        raise FileNotFoundError(f"Feeds config not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("feeds")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"Config {path} must contain a non-empty 'feeds' object.")
    out: dict[str, tuple[str, str, int | None]] = {}
    for feed_id, meta in raw.items():
        key = str(feed_id).strip().lower()
        if not key:
            continue
        if not isinstance(meta, dict):
            raise ValueError(f"Feed '{feed_id}': value must be an object with title and feed_url.")
        title = (meta.get("title") or meta.get("name") or "").strip()
        url = (meta.get("feed_url") or meta.get("url") or "").strip()
        if not title or not url:
            raise ValueError(f"Feed '{key}': requires non-empty 'title' and 'feed_url'.")
        out[key] = (title, url, parse_max_items(meta))
    return out


@dataclass(frozen=True)
class NewsItem:
    source_id: str
    source_name: str
    topic: str
    summary: str
    analysis: str
    link: str
    published: str | None
    summarized: bool = False  # True once an LLM summary+analysis replaced the RSS heuristics


_TAG_RE = re.compile(r"<[^>]+>")

# Browser UA: some feeds (e.g. BleepingComputer) 403 a bot-identifying User-Agent.
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
_ARTICLE_PAGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def strip_html(text: str, max_len: int) -> str:
    if not text:
        return ""
    plain = _TAG_RE.sub(" ", text)
    plain = html_module.unescape(plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) > max_len:
        plain = plain[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return plain


def normalize_url(url: str) -> str:
    if not url:
        return ""
    p = urlparse(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    new_query = urlencode(q)
    path = (p.path or "/").rstrip("/") or "/"
    return urlunparse((p.scheme, p.netloc.lower(), path, p.params, new_query, ""))


def entry_link(entry: feedparser.FeedParserDict) -> str:
    if entry.get("link"):
        return str(entry.link)
    for link in entry.get("links", []) or []:
        if link.get("rel") == "alternate" and link.get("href"):
            return str(link["href"])
    return ""


def linkedin_share_url(article_url: str) -> str:
    """Opens LinkedIn share dialog for the article URL (user must be logged in to post)."""
    return "https://www.linkedin.com/sharing/share-offsite/?url=" + quote(article_url, safe="")


def parse_item_datetime(item: NewsItem) -> datetime | None:
    if not item.published:
        return None
    try:
        return datetime.fromisoformat(item.published.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_by_recent_days(items: list[NewsItem], days: int) -> tuple[list[NewsItem], int, int]:
    """Keep items with published >= (now - days). Drops items without parseable dates."""
    if days < 1:
        raise ValueError("--days must be >= 1")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept: list[NewsItem] = []
    dropped_no_date = 0
    dropped_old = 0
    for it in items:
        dt = parse_item_datetime(it)
        if dt is None:
            dropped_no_date += 1
            continue
        if dt < cutoff:
            dropped_old += 1
            continue
        kept.append(it)
    return kept, dropped_no_date, dropped_old


def entry_published_iso(entry: feedparser.FeedParserDict) -> str | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return None
    try:
        dt = datetime(*t[:6], tzinfo=timezone.utc)
        return dt.isoformat()
    except (TypeError, ValueError):
        return entry.get("published") or entry.get("updated")


def heuristic_analysis(title: str, summary: str) -> str:
    """Short, non-LLM analyst-style note from keywords (defensive lens)."""
    blob = f"{title} {summary}".lower()
    notes: list[str] = []

    def hit(*words: str) -> bool:
        return any(w in blob for w in words)

    if hit("cve-", " cve ", "nvd", "vulnerability", "0-day", "zero-day"):
        notes.append(
            "Vulnerability-led story: prioritize patch or mitigation timelines, "
            "exposure in your asset inventory, and whether exploit code is public."
        )
    if hit("ransomware", "lockbit", "blackcat", "alphv", "play ransomware"):
        notes.append(
            "Ransomware angle: validate offline backups, identity blast radius, "
            "and incident response playbooks including legal and communications."
        )
    if hit("phish", "phishing", "credential", "mfa", "otp"):
        notes.append(
            "Social engineering or credentials: tighten MFA policies, "
            "review high-risk users, and run recent awareness nudges tied to observed lures."
        )
    if hit("apt", "state-sponsored", "nation-state", "china", "russia", "iran", "north korea", "dprk"):
        notes.append(
            "Threat-actor reporting: treat as motivation and TTP signal; map claimed TTPs to "
            "detection content and hunt hypotheses rather than assuming immediate relevance."
        )
    if hit("breach", "data leak", "exfil", "stolen data", "cyberattack", "extortion", "stolen "):
        notes.append(
            "Data-loss storyline: assess regulatory notice clocks, customer impact, "
            "and whether indicators overlap with your third-party or SaaS footprint."
        )
    if hit("patch", "update tuesday", "security update", "kb", "ios ", "android "):
        notes.append(
            "Patching narrative: stage expedited rollout for internet-facing and privileged systems first; "
            "confirm vendor guidance on reboot or config follow-ups."
        )
    if hit("supply chain", "npm", "pypi", "github", "package"):
        notes.append(
            "Supply-chain risk: review software provenance, dependency pinning, "
            "and CI signing or SBOM consumption if the ecosystem mentioned matches yours."
        )
    if hit("detection engineering", "synthetic", "siem", "sigma ", "telemetry", "threat hunting"):
        notes.append(
            "Detection or analytics content: validate log fidelity in your environment, "
            "avoid rule sprawl, and measure false-positive cost before wide rollout."
        )
    if hit("ics", "scada", "ot ", "plc", "industrial"):
        notes.append(
            "OT-relevant: segment process networks, validate emergency procedures, "
            "and align IT-style IOCs with plant-specific monitoring limits."
        )
    if hit("fuzzing", "use-after-free", "uaf", "type confusion", "memory corruption", "integer overflow", "sandbox escape"):
        notes.append(
            "Deep technical / exploitation research: expect vendor patches on their cadence; "
            "prioritize mitigations (sandbox updates, site isolation) over chasing every PoC detail unless you ship the affected component."
        )

    if not notes:
        notes.append(
            "General situational awareness: corroborate with vendor advisories or primary sources, "
            "then decide if controls, detection, or comms need a targeted change this sprint."
        )

    # Keep analysis compact: first two distinct themes max.
    seen: set[str] = set()
    out: list[str] = []
    for n in notes:
        if n not in seen:
            seen.add(n)
            out.append(n)
        if len(out) >= 2:
            break
    return " ".join(out)


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*", re.I | re.M)
_JSON_FENCE_TAIL_RE = re.compile(r"\s*```\s*$", re.M)


def strip_json_fenced_text(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = _JSON_FENCE_RE.sub("", t, count=1)
        t = _JSON_FENCE_TAIL_RE.sub("", t)
    return t.strip()


def _gemini_model_candidates(primary: str, fallback: str | None) -> list[str]:
    """[primary, *fallbacks] deduped. `fallback` may be a comma-separated chain; each Groq
    model has its own daily token budget, so more candidates = more daily headroom."""
    out: list[str] = []
    for m in [primary or ""] + (fallback or "").split(","):
        m = m.strip()
        if m and not any(m.lower() == e.lower() for e in out):
            out.append(m)
    return out


def _parse_gemini_json_list(raw_text: str) -> list[dict]:
    try:
        parsed = json.loads(strip_json_fenced_text(raw_text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini response was not valid JSON: {exc}") from exc

    if isinstance(parsed, dict):
        for key in ("articles", "items", "results", "output"):
            if key in parsed and isinstance(parsed[key], list):
                parsed = parsed[key]
                break

    if not isinstance(parsed, list):
        raise RuntimeError("Gemini JSON must be an array (or an object wrapping an array).")
    return parsed


def _rows_to_index_maps(rows: list) -> tuple[dict[int, dict], dict[str, dict]]:
    by_index: dict[int, dict] = {}
    by_link: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        idx = row.get("index")
        if isinstance(idx, bool):
            continue
        if isinstance(idx, float) and idx.is_integer():
            idx = int(idx)
        if isinstance(idx, int) and idx >= 0:
            by_index[idx] = row
        lk = row.get("link")
        if isinstance(lk, str) and lk.strip():
            by_link[normalize_url(lk.strip())] = row
    return by_index, by_link


def _apply_gemini_rows(
    out: list[NewsItem],
    indices: list[int],
    by_index: dict[int, dict],
    by_link: dict[str, dict],
) -> None:
    for i in indices:
        it = out[i]
        row = by_index.get(i) or by_link.get(normalize_url(it.link))
        if not row:
            continue
        summary = str(row.get("summary") or "").strip()
        analysis = str(row.get("analysis") or "").strip()
        if not summary or not analysis:
            continue
        out[i] = replace(
            out[i],
            summary=summary[:4000],
            analysis=analysis[:4000],
            summarized=True,
        )


def _truncate_for_gemini(text: str, max_chars: int) -> str:
    t = (text or "").strip()
    if len(t) > max_chars:
        return t[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return t


def _fetch_article_bodies_for_chunk(
    out: list[NewsItem],
    indices: list[int],
    *,
    timeout: int,
    max_bytes: int,
    max_chars: int,
    pause_s: float,
    user_agent: str,
    accept_language: str,
) -> dict[int, str]:
    from article_fetch import fetch_article_plain_text

    bodies: dict[int, str] = {}
    for ii, i in enumerate(indices):
        if pause_s > 0 and ii > 0:
            time.sleep(pause_s)
        it = out[i]
        try:
            text = fetch_article_plain_text(
                it.link,
                timeout=timeout,
                max_bytes=max_bytes,
                max_chars=max_chars,
                user_agent=user_agent,
                accept_language=accept_language,
            )
            if len(text.strip()) < 60:
                raise RuntimeError("extracted article text too short")
        except Exception as exc:  # noqa: BLE001 — best-effort fallback per URL
            print(
                f"Warning: article fetch/extract failed [{it.source_name}] {it.link}: {exc}",
                file=sys.stderr,
            )
            bodies[i] = (it.summary or "").strip() or "(No usable text.)"
        else:
            bodies[i] = text
    return bodies


def _gemini_payload_rows(
    out: list[NewsItem],
    indices: list[int],
    bodies: dict[int, str],
    *,
    full_article: bool,
    body_char_cap: int,
) -> list[dict]:
    payload: list[dict] = []
    for i in indices:
        it = out[i]
        body = _truncate_for_gemini(bodies.get(i, it.summary), body_char_cap)
        row: dict = {
            "index": i,
            "link": it.link,
            "source": it.source_name,
            "title": it.topic,
        }
        if full_article:
            row["article_plain_text"] = body
        else:
            row["rss_excerpt"] = body
        payload.append(row)
    return payload


def groq_batch_enrich(
    items: list[NewsItem],
    *,
    api_key: str,
    model: str,
    model_fallback: str | None = None,
    full_article: bool = True,
    article_fetch_timeout_s: int = 30,
    article_max_bytes: int = 2_500_000,
    article_fetch_pause_s: float = 0.35,
    article_user_agent: str = _ARTICLE_PAGE_UA,
    article_accept_language: str = "en-US,en;q=0.9",
    max_article_chars: int = 16_000,
    max_excerpt_chars: int = 480,
    max_output_tokens: int,
    request_timeout_s: int,
    chunk_size: int,
    chunk_pause_s: float,
    max_retries: int,
    summary_tpm: int | None = None,
    svc=groq,  # summary provider module (groq_summary or gemini_summary)
) -> tuple[list[NewsItem], bool]:
    """
    Rewrite summaries/analysis via Groq (OpenAI-compatible chat completions) using chunked
    requests and pauses to respect free-tier requests-per-minute, with retries on transient
    errors (429/5xx), then an optional fallback model per chunk.
    """
    if not items:
        return items, False

    n = len(items)
    if chunk_size <= 0 or chunk_size > n:
        chunk_size = n

    if full_article:
        instructions = (
            "You are a careful cybersecurity editor. Each input item includes metadata plus "
            "`article_plain_text`: plain text extracted from the article HTML (navigation and ads "
            "are mostly removed; the extract may be partial). Base summary and analysis primarily "
            "on that text and the title. Do not invent incidents, victims, or CVEs not grounded in "
            "the text. If the body is thin, truncated, or unclear, say what is supported and note uncertainty.\n\n"
            "Return ONLY a JSON array (no markdown fences). Each element must be an object with keys exactly: "
            '"index" (integer, matching input), "link" (string, same as input), '
            '"summary" (string, 2–4 sentences, plain English), '
            '"analysis" (string, 2–4 sentences: implications for defenders, patch/hunt/priority angle).\n\n'
            "The array MUST have the same length as the input list and use the same index values.\n\n"
            "INPUT_ARTICLES_JSON:\n"
        )
    else:
        instructions = (
            "You are a careful cybersecurity editor. You receive ONLY short RSS excerpts and metadata — "
            "not full articles. Do not invent incidents, victims, or CVEs that are not clearly supported by the excerpt. "
            "If the excerpt is thin, say what is known and what would require reading the source.\n\n"
            "Return ONLY a JSON array (no markdown fences). Each element must be an object with keys exactly: "
            '"index" (integer, matching input), "link" (string, same as input), '
            '"summary" (string, 2–4 sentences, plain English), '
            '"analysis" (string, 2–4 sentences: implications for defenders, patch/hunt/priority angle).\n\n'
            "The array MUST have the same length as the input list and use the same index values.\n\n"
            "INPUT_ARTICLES_JSON:\n"
        )

    out = list(items)
    used_fallback = False
    if summary_tpm is None:
        summary_tpm = svc.DEFAULT_TPM_LIMIT
    model_candidates = _gemini_model_candidates(model, model_fallback)
    # Models that hit their daily cap this run — skipped for the rest of it (a daily cap
    # won't refill in minutes, so re-trying the model on later chunks only wastes a call).
    exhausted_models: set[str] = set()
    # Live TPM bucket from Groq's response headers, used to pace requests (see below).
    rate_limit = svc.RateLimit()

    for start in range(0, n, chunk_size):
        end = min(start + chunk_size, n)
        indices = list(range(start, end))
        if full_article:
            bodies = _fetch_article_bodies_for_chunk(
                out,
                indices,
                timeout=article_fetch_timeout_s,
                max_bytes=article_max_bytes,
                max_chars=max_article_chars,
                pause_s=article_fetch_pause_s,
                user_agent=article_user_agent,
                accept_language=article_accept_language,
            )
        else:
            bodies = {i: out[i].summary for i in indices}
        # Keep each request under the free-tier TPM: small output reservation + input trimmed to fit.
        out_tokens = svc.output_token_budget(len(indices), max_output_tokens)
        body_char_cap = min(
            max_article_chars if full_article else max_excerpt_chars,
            svc.input_char_budget_per_item(len(indices), output_tokens=out_tokens, tpm_limit=summary_tpm),
        )
        payload = _gemini_payload_rows(
            out,
            indices,
            bodies,
            full_article=full_article,
            body_char_cap=body_char_cap,
        )
        prompt = instructions + json.dumps(payload, ensure_ascii=False)

        available = [m for m in model_candidates if m not in exhausted_models]
        if not available:
            print(
                f"Groq: all models hit their daily cap; keeping RSS excerpts from chunk "
                f"{start}-{end - 1} onward.",
                file=sys.stderr,
            )
            break

        chunk_ok = False
        last_err: BaseException | None = None

        for mi, active_model in enumerate(available):
            for attempt in range(max_retries):
                tokens_this_chunk = out_tokens
                try:
                    raw_text = svc.chat_text(
                        token=api_key,
                        model=active_model,
                        prompt=prompt,
                        max_tokens=tokens_this_chunk,
                        temperature=0.35,
                        timeout_s=request_timeout_s,
                        reasoning_effort="low",
                        rate_out=rate_limit,
                    )
                    if not raw_text:
                        raise RuntimeError("Groq returned empty text.")

                    rows = _parse_gemini_json_list(raw_text)
                    if len(rows) != len(indices):
                        print(
                            f"Warning: Groq returned {len(rows)} rows for chunk indices {start}-{end - 1}, "
                            f"expected {len(indices)}; merging partial results.",
                            file=sys.stderr,
                        )
                    by_i, by_l = _rows_to_index_maps(rows)
                    _apply_gemini_rows(out, indices, by_i, by_l)
                    chunk_ok = True
                    if active_model != model_candidates[0]:
                        used_fallback = True
                        print(
                            f"Groq chunk {start}-{end - 1}: OK using fallback model {active_model!r}.",
                            file=sys.stderr,
                        )
                    break
                except Exception as exc:  # noqa: BLE001 — retry transient, else fall through to next model
                    last_err = exc
                    if svc.is_daily_quota(exc) or svc.is_model_unavailable(exc):
                        # Daily cap (won't refill) or a 404/unavailable model id: retire this
                        # model for the rest of the run and fall through to the next candidate.
                        exhausted_models.add(active_model)
                        reason = "daily quota hit" if svc.is_daily_quota(exc) else "model unavailable (404)"
                        print(
                            f"Groq {reason} on chunk {start}-{end - 1} with model {active_model!r} "
                            f"({exc!s}); retiring it for this run.",
                            file=sys.stderr,
                        )
                        break
                    if svc.is_transient(exc) and attempt < max_retries - 1:
                        delay = svc.retry_sleep_seconds(exc, attempt)
                        print(
                            f"Groq transient error on chunk {start}-{end - 1} ({exc!s}); sleeping {delay:.1f}s "
                            f"(retry {attempt + 2}/{max_retries}, model={active_model!r}).",
                            file=sys.stderr,
                        )
                        time.sleep(delay)
                        continue
                    # An empty/malformed response is usually a one-off; retry the same model.
                    msg = str(exc).lower()
                    if ("empty text" in msg or "returned empty" in msg or isinstance(exc, json.JSONDecodeError)) and attempt < max_retries - 1:
                        delay = min(5.0 * (1.6**attempt), 60.0)
                        print(
                            f"Groq empty/malformed response on chunk {start}-{end - 1}; sleeping {delay:.1f}s "
                            f"(retry {attempt + 2}/{max_retries}, model={active_model!r}).",
                            file=sys.stderr,
                        )
                        time.sleep(delay)
                        continue
                    break

            if chunk_ok:
                break

            if mi < len(available) - 1:
                print(
                    f"Groq chunk {start}-{end - 1}: model {active_model!r} failed ({last_err!s}); "
                    f"retrying with {available[mi + 1]!r}.",
                    file=sys.stderr,
                )

        if not chunk_ok:
            assert last_err is not None
            if svc.is_daily_quota(last_err):
                # All models are out of daily budget — remaining chunks would fail too.
                # Keep RSS excerpts for this and later chunks instead of crashing.
                print(
                    f"Groq daily quota exhausted at chunk {start}-{end - 1}; keeping RSS excerpts "
                    "for the remaining articles.",
                    file=sys.stderr,
                )
                break
            # One chunk failed on every model (e.g. an empty response): keep RSS excerpts for
            # just these articles and continue — never discard the chunks that did summarize.
            print(
                f"Groq chunk {start}-{end - 1}: all models failed ({last_err!s}); "
                f"keeping RSS excerpts for these {len(indices)} article(s) and continuing.",
                file=sys.stderr,
            )
            continue

        if end < n:
            # Pace the next request off the live TPM bucket instead of a blind fixed wait:
            # wait only long enough for the next ~full-size request to fit under the cap.
            delay = svc.pace_delay_seconds(
                rate_limit, int(summary_tpm * 0.85), tpm_limit=summary_tpm, fallback_s=chunk_pause_s
            )
            if delay > 0:
                time.sleep(delay)

    return out, used_fallback


def fetch_feed_bytes(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _DEFAULT_UA,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — intentional URL fetch
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc


def _parse_kev_json(source_id: str, source_name: str, url: str, timeout: int, max_recent: int = 60) -> list[NewsItem]:
    """Parse CISA's Known Exploited Vulnerabilities catalog JSON into NewsItems.

    CISA retired its RSS/XML feeds (May 2025); this JSON under /sites/default/files/feeds/
    is the surviving machine-readable source and is served outside the WAF that 403s the
    old .xml feeds. Newest-by-dateAdded first; each CVE links to its NVD detail page.
    """
    raw = fetch_feed_bytes(url, timeout)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{source_name}: invalid KEV JSON ({exc})") from exc
    vulns = data.get("vulnerabilities") if isinstance(data, dict) else None
    if not vulns:
        raise RuntimeError(f"{source_name}: no 'vulnerabilities' in KEV JSON")
    vulns = sorted(vulns, key=lambda v: str(v.get("dateAdded") or ""), reverse=True)[:max_recent]

    items: list[NewsItem] = []
    for v in vulns:
        cve = str(v.get("cveID") or "").strip()
        if not cve:
            continue
        name = str(v.get("vulnerabilityName") or "").strip()
        vp = " ".join(p for p in (str(v.get("vendorProject") or "").strip(), str(v.get("product") or "").strip()) if p)
        topic = f"{cve}: {name}" if name else cve
        parts = []
        if vp:
            parts.append(f"{vp}.")
        desc = strip_html(str(v.get("shortDescription") or ""), 340)
        if desc:
            parts.append(desc)
        action = str(v.get("requiredAction") or "").strip()
        if action:
            due = str(v.get("dueDate") or "").strip()
            parts.append(f"Required action: {action}" + (f" (due {due})." if due else "."))
        if str(v.get("knownRansomwareCampaignUse") or "").strip().lower() == "known":
            parts.append("Known use in ransomware campaigns.")
        summary = strip_html(" ".join(parts), 460) or "(CISA KEV entry.)"
        date_added = str(v.get("dateAdded") or "").strip()
        items.append(
            NewsItem(
                source_id=source_id,
                source_name=source_name,
                topic=topic[:300],
                summary=summary,
                analysis=heuristic_analysis(topic, summary),
                link=f"https://nvd.nist.gov/vuln/detail/{cve}",
                published=f"{date_added}T00:00:00+00:00" if date_added else None,
            )
        )
    if not items:
        raise RuntimeError(f"{source_name}: no usable KEV entries")
    return items


def parse_feed(source_id: str, source_name: str, url: str, timeout: int) -> list[NewsItem]:
    if url.endswith(".json"):  # CISA KEV catalog (JSON), not an RSS/Atom feed
        return _parse_kev_json(source_id, source_name, url, timeout)
    raw = fetch_feed_bytes(url, timeout)
    parsed = feedparser.parse(raw)
    if getattr(parsed, "bozo_exception", None) and not parsed.entries:
        raise RuntimeError(f"{source_name}: feed error ({parsed.bozo_exception})")

    items: list[NewsItem] = []
    for entry in parsed.entries:
        title = strip_html(str(entry.get("title") or ""), 300)
        if not title:
            continue
        raw_summary = (
            entry.get("summary")
            or entry.get("description")
            or (entry.get("content", [{}])[0].get("value") if entry.get("content") else "")
            or ""
        )
        summary = strip_html(str(raw_summary), 420)
        if not summary:
            summary = "(No summary in feed; open the article for detail.)"

        link = entry_link(entry)
        if not link:
            continue

        items.append(
            NewsItem(
                source_id=source_id,
                source_name=source_name,
                topic=title,
                summary=summary,
                analysis=heuristic_analysis(title, summary),
                link=link,
                published=entry_published_iso(entry),
            )
        )
    if not items:
        raise RuntimeError(f"{source_name}: no entries parsed from {url}")
    return items


def _item_timestamp(item: NewsItem) -> float:
    """Epoch seconds for an item's published date (0.0 when missing/unparseable)."""
    if item.published:
        try:
            return datetime.fromisoformat(item.published.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def gather(
    feeds: dict[str, tuple[str, str, int | None]],
    source_ids: list[str],
    per_source: int,
    timeout: int,
    pause_s: float,
    exclude_keywords: list[str] | None = None,
) -> list[NewsItem]:
    collected: list[NewsItem] = []
    errors: list[str] = []
    keywords = exclude_keywords or []
    excluded_count = 0

    for i, sid in enumerate(source_ids):
        if sid not in feeds:
            errors.append(f"Unknown source id: {sid}")
            continue
        name, url, feed_max = feeds[sid]
        try:
            parsed = parse_feed(sid, name, url, timeout)
            kept = [it for it in parsed if not is_excluded(f"{it.topic} {it.summary}", keywords)]
            excluded_count += len(parsed) - len(kept)
            collected.extend(kept[: effective_cap(feed_max, per_source)])
        except Exception as exc:  # noqa: BLE001 — surface per-feed failures
            errors.append(f"{name}: {exc}")
        if pause_s > 0 and i < len(source_ids) - 1:
            time.sleep(pause_s)

    if excluded_count:
        print(f"Exclude keywords: dropped {excluded_count} item(s) matching {keywords}.", file=sys.stderr)

    if errors:
        print("Warnings:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)

    # Order by feed sequence in the config (source_ids order), then newest-first within each feed.
    # Dedupe by normalized URL: the earlier feed in the config keeps a shared article.
    rank = {sid: i for i, sid in enumerate(source_ids)}
    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for it in sorted(
        collected,
        key=lambda it: (rank.get(it.source_id, len(rank)), -_item_timestamp(it), it.link),
    ):
        nu = normalize_url(it.link)
        if nu in seen:
            continue
        seen.add(nu)
        deduped.append(it)
    return deduped


def format_console(items: list[NewsItem]) -> str:
    blocks: list[str] = []
    for it in items:
        pub = it.published or "date unknown"
        li = linkedin_share_url(it.link)
        blocks.append(
            f"## [{it.source_name}] {it.topic}\n"
            f"**Published:** {pub}\n"
            f"**Summary:** {it.summary}\n"
            f"**Analysis:** {it.analysis}\n"
            f"**Link:** {it.link}\n"
            f"**Post to LinkedIn:** {li}\n"
        )
    return "\n".join(blocks)


def build_html(
    items: list[NewsItem],
    generated_at: datetime | None = None,
    server_days: int | None = None,
    summary_model: str | None = None,
    summary_article_pages: bool = False,
    read_news_azure_voice: str = READ_NEWS_AZURE_VOICE_EN_DEFAULT,
    read_news_azure_voice_fallback: str = READ_NEWS_AZURE_VOICE_FALLBACK_EN_DEFAULT,
    read_news_azure_region: str | None = None,
) -> str:
    when = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    day_default = str(server_days if server_days is not None else 14)
    filter_note = (
        f"Feed window (server): last {server_days} day(s), parseable publish dates only."
        if server_days is not None
        else "No server date window; use the control below to hide older cards in the browser."
    )
    if summary_model:
        if summary_article_pages:
            summary_note = (
                f"Summaries and analysis were generated by Groq ({summary_model}) from plain text "
                f"extracted from each article page (not only RSS snippets); verify critical facts against the source."
            )
        else:
            summary_note = (
                f"Summaries and analysis were generated by Groq ({summary_model}) from RSS excerpts only; "
                f"verify critical facts against the original article."
            )
    else:
        summary_note = "Summaries are from RSS feeds; analysis uses local keyword heuristics (no LLM)."
    reader_frag = (
        " Read news: Azure AI Speech synthesizes the summaries on this page (voice "
        + html_module.escape(read_news_azure_voice)
        + " / fallback "
        + html_module.escape(read_news_azure_voice_fallback)
        + "); Azure key + region in-toolbar, saved in localStorage (persists across visits)."
    )
    cards: list[str] = []
    for it in items:
        pub = html_module.escape(it.published or "date unknown")
        topic = html_module.escape(it.topic)
        src = html_module.escape(it.source_name)
        summary = html_module.escape(it.summary)
        analysis = html_module.escape(it.analysis)
        link_href = html_module.escape(it.link, quote=True)
        link_text = html_module.escape(it.link)
        time_attr = ""
        if it.published:
            time_attr = f' datetime="{html_module.escape(it.published)}"'
        dt = parse_item_datetime(it)
        data_ts = f' data-ts="{int(dt.timestamp() * 1000)}"' if dt else ""
        li_url = linkedin_share_url(it.link)
        li_href = html_module.escape(li_url, quote=True)
        nosum_badge = "" if it.summarized else '<span class="badge-nosum" title="Not AI-summarized; showing the RSS description + heuristics.">Not summarized</span>'
        sum_heading = "Summary" if it.summarized else "Description (RSS)"
        cards.append(
            f"""<article class="card"{data_ts}>
  <header class="card-head">
    <span class="src">{src}</span>
    {nosum_badge}
    <time{time_attr}>{pub}</time>
  </header>
  <h2 class="topic"><a href="{link_href}" rel="noopener noreferrer">{topic}</a></h2>
  <section class="block"><h3>{sum_heading}</h3><p>{summary}</p></section>
  <section class="block analysis"><h3>Analysis</h3><p>{analysis}</p></section>
  <div class="actions">
    <a class="btn-li" href="{li_href}" target="_blank" rel="noopener noreferrer">Post to LinkedIn</a>
    <a class="btn-sec" href="{link_href}" rel="noopener noreferrer">Open article</a>
  </div>
  <p class="linkrow"><a class="full" href="{link_href}" rel="noopener noreferrer">{link_text}</a></p>
</article>"""
        )

    body = "\n".join(cards)
    summary_note_esc = html_module.escape(summary_note)
    filter_hint_esc = html_module.escape(
        f"{filter_note} Client filter hides cards without a machine date."
    ) + reader_frag
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Security news digest</title>
  <style>
    :root {{
      --bg: #0f1419;
      --card: #1a2332;
      --text: #e7ecf3;
      --muted: #9fb0c8;
      --accent: #5b9bd5;
      --border: #2a3a52;
      --li: #0a66c2;
      --li-hover: #004182;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, Ubuntu, sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.55;
    }}
    .wrap {{ max-width: 52rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }}
    h1 {{ font-size: 1.35rem; font-weight: 650; margin: 0 0 0.25rem; }}
    .meta {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 0.75rem; }}
    .submeta {{ display: inline-block; margin-top: 0.35rem; font-size: 0.82rem; color: #8fa6bf; line-height: 1.45; }}
    .toolbar {{
      display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem;
      background: var(--card); border: 1px solid var(--border); border-radius: 10px;
      padding: 0.75rem 1rem; margin-bottom: 1.25rem; font-size: 0.92rem;
    }}
    .toolbar label {{ color: var(--muted); display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }}
    .toolbar input[type="number"] {{
      width: 4.5rem; padding: 0.35rem 0.45rem; border-radius: 6px; border: 1px solid var(--border);
      background: var(--bg); color: var(--text); font-size: 0.95rem;
    }}
    .toolbar button {{
      cursor: pointer; border: none; border-radius: 6px; padding: 0.4rem 0.85rem;
      font-size: 0.88rem; font-weight: 600;
    }}
    .toolbar .apply {{ background: var(--accent); color: #0a111a; }}
    .toolbar .reset {{ background: transparent; color: var(--muted); border: 1px solid var(--border); }}
    .toolbar .apply:hover {{ filter: brightness(1.08); }}
    .toolbar .reset:hover {{ color: var(--text); }}
    .filter-hint {{ width: 100%; font-size: 0.8rem; color: var(--muted); margin: 0.25rem 0 0; }}
    .card {{
      background: var(--card); border: 1px solid var(--border); border-radius: 10px;
      padding: 1rem 1.15rem 1.1rem; margin-bottom: 1rem;
    }}
    .card-head {{ display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; justify-content: space-between;
      align-items: baseline; font-size: 0.82rem; color: var(--muted); margin-bottom: 0.35rem; }}
    .src {{ font-weight: 600; color: var(--accent); }}
    .card-head time {{ margin-left: auto; }}
    .badge-nosum {{ background: transparent; color: #d8a24a; border: 1px solid #6b562f;
      border-radius: 6px; padding: 0.1rem 0.45rem; font-weight: 600; font-size: 0.78rem; }}
    .topic {{ font-size: 1.1rem; margin: 0.2rem 0 0.75rem; line-height: 1.35; }}
    .topic a {{ color: var(--text); text-decoration: none; }}
    .topic a:hover {{ text-decoration: underline; color: var(--accent); }}
    .block h3 {{ margin: 0 0 0.35rem; font-size: 0.72rem; text-transform: uppercase;
      letter-spacing: 0.06em; color: var(--muted); font-weight: 650; }}
    .block p {{ margin: 0; font-size: 0.95rem; color: #d5dde8; }}
    .analysis p {{ color: #cfe0c9; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.85rem; }}
    .actions a {{
      display: inline-block; text-decoration: none; border-radius: 6px; padding: 0.45rem 0.9rem;
      font-size: 0.88rem; font-weight: 600;
    }}
    .btn-li {{ background: var(--li); color: #fff; }}
    .btn-li:hover {{ background: var(--li-hover); }}
    .btn-sec {{ background: transparent; color: var(--accent); border: 1px solid var(--border); }}
    .btn-sec:hover {{ border-color: var(--accent); }}
    .linkrow {{ margin: 0.85rem 0 0; word-break: break-all; font-size: 0.85rem; }}
    a.full {{ color: var(--accent); }}
{digest_reader_css()}
{digest_rebuild_css()}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Security news digest</h1>
    <p class="meta">Generated {html_module.escape(when)} · <span id="visibleCount">{len(items)}</span> shown · <a class="full" href="archive/">🕘 History</a><br/>
    <span class="submeta">{summary_note_esc}</span></p>
    <div class="toolbar">
      <label>Show articles from last
        <input type="number" id="dayWindow" min="1" max="3650" value="{day_default}"/>
        days
      </label>
      <button type="button" class="apply" id="applyDays">Apply</button>
      <button type="button" class="reset" id="resetDays">Show all</button>
      <p class="filter-hint">{filter_hint_esc}</p>
{digest_reader_toolbar_inner(lang="en")}
{digest_rebuild_toolbar_inner(lang="en", selected="security-news-daily.yml")}
    </div>
{body}
  </div>
  <script>
(function() {{
  function countVisible() {{
    const n = document.querySelectorAll("article.card:not([hidden])").length;
    const el = document.getElementById("visibleCount");
    if (el) el.textContent = n;
  }}
  function applyDayFilter() {{
    const input = document.getElementById("dayWindow");
    const n = parseInt(input && input.value, 10);
    if (!input || !Number.isFinite(n) || n < 1) {{
      alert("Enter a positive number of days.");
      return;
    }}
    const cutoff = Date.now() - n * 86400000;
    document.querySelectorAll("article.card").forEach(function(el) {{
      const raw = el.getAttribute("data-ts");
      if (raw === null || raw === "") {{
        el.hidden = true;
        return;
      }}
      const ts = parseInt(raw, 10);
      if (!Number.isFinite(ts)) {{
        el.hidden = true;
        return;
      }}
      el.hidden = ts < cutoff;
    }});
    countVisible();
  }}
  function resetFilter() {{
    document.querySelectorAll("article.card").forEach(function(el) {{ el.hidden = false; }});
    countVisible();
  }}
  const b1 = document.getElementById("applyDays");
  const b2 = document.getElementById("resetDays");
  if (b1) b1.addEventListener("click", applyDayFilter);
  if (b2) b2.addEventListener("click", resetFilter);
}})();
  </script>
  {digest_reader_sdk_script_tag()}
  <script>
{digest_reader_script(
        lang="en",
        voice=read_news_azure_voice,
        voice_fallback=read_news_azure_voice_fallback,
        region_default=read_news_azure_region,
    )}
  </script>
  <script>
{digest_rebuild_script(lang="en")}
  </script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Latest security headlines from major RSS feeds.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--feeds-config",
        type=Path,
        default=None,
        help="JSON file with a 'feeds' map (see config/security_news_feeds.json).",
    )
    parser.add_argument(
        "--sources",
        default="all",
        help="Comma-separated feed ids from your config, or 'all' (default).",
    )
    parser.add_argument(
        "--per-source",
        type=int,
        default=8,
        help="Max items to read from each feed (default 8). A feed's 'max_items' in the config overrides this.",
    )
    parser.add_argument("--limit", type=int, default=15, help="Max items after merge, sort, dedupe (default 15).")
    parser.add_argument(
        "--exclude-keywords",
        default=None,
        metavar="K1,K2,...",
        help="Comma-separated keywords; drop items whose title or summary contains any (case-insensitive). "
        "Merged with the config's top-level 'exclude_keywords' list.",
    )
    parser.add_argument("--timeout", type=int, default=25, help="HTTP timeout seconds per feed fetch (default 25).")
    parser.add_argument("--pause", type=float, default=0.0, help="Seconds to sleep between feed fetches (politeness).")
    parser.add_argument("--json", action="store_true", help="Print JSON array to stdout.")
    parser.add_argument(
        "--html",
        metavar="PATH",
        help="Write a standalone HTML digest to PATH (parent directories are created).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        metavar="N",
        help="Only include items published in the last N days (requires parseable dates; others are dropped).",
    )
    parser.add_argument(
        "--summarize",
        "--gemini",
        dest="summarize",
        action="store_true",
        help="Rewrite summary+analysis with an LLM via Groq (needs GROQ_API_KEY). "
        "Uses chunked requests + pauses to reduce 429 rate-limit errors on the free tier.",
    )
    parser.add_argument(
        "--summary-provider",
        dest="summary_provider",
        choices=sorted(_SUMMARY_PROVIDERS),
        default=os.environ.get("SUMMARY_PROVIDER", "groq"),
        help="Which LLM provider summarizes: 'groq' (GROQ_API_KEY) or 'gemini' (GEMINI_API_KEY, "
        "from aistudio.google.com). Default: groq, or the SUMMARY_PROVIDER env var.",
    )
    parser.add_argument(
        "--summary-model",
        dest="summary_model",
        default=None,
        metavar="MODEL_ID",
        help="Primary chat model id. Defaults to the selected provider's default "
        f"(groq: {groq.SUMMARY_MODEL_DEFAULT}; gemini: {gemini_summary.SUMMARY_MODEL_DEFAULT}).",
    )
    parser.add_argument(
        "--summary-model-fallback",
        dest="summary_model_fallback",
        default=None,
        metavar="MODEL_IDS",
        help="Comma-separated fallback chain: if the primary model fails (or hits its daily cap), "
        "each chunk is retried with the next model (each model has its own daily budget). "
        "Defaults to the selected provider's fallback chain.",
    )
    parser.add_argument(
        "--gemini-max-excerpt-chars",
        type=int,
        default=480,
        help="With --gemini-no-fetch-article: max RSS excerpt chars per item sent to Gemini.",
    )
    parser.add_argument(
        "--gemini-no-fetch-article",
        action="store_true",
        help="Do not download article pages; use RSS excerpts only for Gemini (legacy, fewer HTTP requests).",
    )
    parser.add_argument(
        "--gemini-article-timeout",
        type=int,
        default=30,
        help="HTTP timeout (seconds) for each article page fetch when building Gemini input.",
    )
    parser.add_argument(
        "--gemini-article-max-bytes",
        type=int,
        default=2_500_000,
        metavar="N",
        help="Max raw HTML bytes to download per article (memory cap).",
    )
    parser.add_argument(
        "--gemini-article-fetch-pause",
        type=float,
        default=0.35,
        help="Seconds to sleep between article page fetches (politeness to publishers).",
    )
    parser.add_argument(
        "--gemini-max-article-chars",
        type=int,
        default=6_000,
        help="Max plain-text chars per article sent to the model (further trimmed to fit --summary-tpm).",
    )
    parser.add_argument(
        "--gemini-max-output-tokens",
        type=int,
        default=2048,
        help="Max output tokens per chunk response (covers gpt-oss reasoning + the JSON).",
    )
    parser.add_argument(
        "--summary-tpm",
        type=int,
        default=None,
        metavar="N",
        help="Tokens-per-minute limit of your provider tier (default: the provider's free-tier "
        "value). Each request's input is trimmed so it stays under this cap.",
    )
    parser.add_argument(
        "--gemini-timeout",
        type=int,
        default=180,
        help="Gemini HTTP timeout in seconds per chunk request.",
    )
    parser.add_argument(
        "--gemini-chunk-size",
        type=int,
        default=3,
        metavar="N",
        help="Articles per request (smaller reduces per-request tokens). Use 0 for one request for all.",
    )
    parser.add_argument(
        "--gemini-chunk-pause",
        type=float,
        default=45.0,
        help="Seconds to sleep between chunks (keeps cumulative tokens/minute under the free-tier TPM).",
    )
    parser.add_argument(
        "--gemini-retries",
        type=int,
        default=7,
        help="Retries per chunk on transient Gemini errors (429, overload, 503, etc.; uses server 'retry in Xs' when present).",
    )
    parser.add_argument(
        "--read-news-azure-voice",
        default=READ_NEWS_AZURE_VOICE_EN_DEFAULT,
        metavar="VOICE_ID",
        help="Azure AI Speech neural voice id for the browser reader (e.g. en-US-AriaNeural).",
    )
    parser.add_argument(
        "--read-news-azure-voice-fallback",
        default=READ_NEWS_AZURE_VOICE_FALLBACK_EN_DEFAULT,
        metavar="VOICE_ID",
        help="Reader fallback Azure voice id if the primary voice fails.",
    )
    parser.add_argument(
        "--read-news-azure-region",
        default=None,
        metavar="REGION",
        help="Optional default Azure region prefilled in the reader toolbar (visitor can override).",
    )
    args = parser.parse_args()

    if args.days is not None and args.days < 1:
        print("Error: --days must be >= 1.", file=sys.stderr)
        return 2

    feeds_path = args.feeds_config if args.feeds_config is not None else default_feeds_config_path()
    try:
        feeds = load_feeds(feeds_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error loading feeds config ({feeds_path}): {exc}", file=sys.stderr)
        return 2

    if args.sources.strip().lower() == "all":
        sids = list(feeds.keys())  # config order (feeds are loaded in config sequence)
    else:
        sids = [s.strip().lower() for s in args.sources.split(",") if s.strip()]

    exclude_keywords = load_config_exclude_keywords(feeds_path) + parse_cli_keywords(args.exclude_keywords)
    items = gather(
        feeds,
        sids,
        per_source=args.per_source,
        timeout=args.timeout,
        pause_s=args.pause,
        exclude_keywords=exclude_keywords,
    )
    server_days: int | None = None
    if args.days is not None:
        server_days = args.days
        items, dropped_nd, dropped_old = filter_by_recent_days(items, args.days)
        if dropped_nd or dropped_old:
            print(
                f"Date filter (--days {args.days}): dropped {dropped_old} older than window, "
                f"{dropped_nd} without parseable dates.",
                file=sys.stderr,
            )
    items = items[: args.limit]

    summary_model_used: str | None = None
    if args.summarize:
        svc = _SUMMARY_PROVIDERS[args.summary_provider]
        provider_name = args.summary_provider
        key = svc.api_key()
        if not key:
            env_var = "GEMINI_API_KEY" if provider_name == "gemini" else "GROQ_API_KEY"
            print(f"Error: --summarize with provider '{provider_name}' requires {env_var}.", file=sys.stderr)
            return 2
        model = args.summary_model or svc.SUMMARY_MODEL_DEFAULT
        model_fallback = args.summary_model_fallback if args.summary_model_fallback is not None else svc.SUMMARY_MODEL_FALLBACK_DEFAULT
        try:
            if not args.gemini_no_fetch_article:
                print(
                    f"Summaries ({provider_name}): fetching each article page and extracting text "
                    "(use --gemini-no-fetch-article to use RSS excerpts only).",
                    file=sys.stderr,
                )
            items, summary_used_fallback = groq_batch_enrich(
                items,
                api_key=key,
                model=model,
                model_fallback=model_fallback,
                full_article=not args.gemini_no_fetch_article,
                article_fetch_timeout_s=args.gemini_article_timeout,
                article_max_bytes=args.gemini_article_max_bytes,
                article_fetch_pause_s=args.gemini_article_fetch_pause,
                max_article_chars=args.gemini_max_article_chars,
                max_excerpt_chars=args.gemini_max_excerpt_chars,
                max_output_tokens=args.gemini_max_output_tokens,
                request_timeout_s=args.gemini_timeout,
                chunk_size=args.gemini_chunk_size,
                chunk_pause_s=args.gemini_chunk_pause,
                max_retries=args.gemini_retries,
                summary_tpm=args.summary_tpm,
                svc=svc,
            )
            summary_model_used = model
            if summary_used_fallback:
                summary_model_used = f"{model} (fallback used: {model_fallback})"
            print(
                f"{provider_name} batch enrichment OK ({summary_model_used}, {len(items)} articles).",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{provider_name} batch failed; keeping RSS/heuristic text: {exc}", file=sys.stderr)

    if args.html:
        out = Path(args.html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            build_html(
                items,
                server_days=server_days,
                summary_model=summary_model_used,
                summary_article_pages=bool(summary_model_used) and not args.gemini_no_fetch_article,
                read_news_azure_voice=args.read_news_azure_voice,
                read_news_azure_voice_fallback=args.read_news_azure_voice_fallback,
                read_news_azure_region=args.read_news_azure_region,
            ),
            encoding="utf-8",
        )
        print(f"Wrote {out.resolve()} ({len(items)} items).", file=sys.stderr)

    if args.json:
        rows = []
        for x in items:
            d = asdict(x)
            d["linkedin_share_url"] = linkedin_share_url(x.link)
            rows.append(d)
        print(json.dumps(rows, indent=2))
    elif not args.html:
        print(format_console(items))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
