#!/usr/bin/env python3
"""
Fetch recent headlines from major security news RSS/Atom feeds.

Produces topic (title), summary, analysis, and canonical link. By default summaries come from RSS
and analysis from local heuristics; with --gemini and GEMINI_API_KEY, Gemini rewrites summary and
analysis in small batches (fewer tokens per request, pauses + retries on 429) to fit free-tier limits.

Usage:
  pip install -r requirements-security-news.txt
  python scripts/fetch_security_news.py
  python scripts/fetch_security_news.py --limit 10 --json
  python scripts/fetch_security_news.py --html output/security_news.html
  python scripts/fetch_security_news.py --days 7 --html output/security_news.html
  python scripts/fetch_security_news.py --sources bleepingcomputer,cisa
  python scripts/fetch_security_news.py --feeds-config /path/to/feeds.json
  GEMINI_API_KEY=... python scripts/fetch_security_news.py --gemini --html out.html
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
    READ_NEWS_SUMMARY_MODEL_DEFAULT,
    READ_NEWS_TTS_MODEL_DEFAULT,
    READ_NEWS_VOICE_DEFAULT,
    digest_reader_css,
    digest_reader_script,
    digest_reader_toolbar_inner,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FEEDS_PATH = _REPO_ROOT / "config" / "security_news_feeds.json"


def default_feeds_config_path() -> Path:
    return _DEFAULT_FEEDS_PATH


def load_feeds(path: Path) -> dict[str, tuple[str, str]]:
    """Load feed id -> (display title, rss_or_atom_url) from JSON config."""
    if not path.is_file():
        raise FileNotFoundError(f"Feeds config not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("feeds")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"Config {path} must contain a non-empty 'feeds' object.")
    out: dict[str, tuple[str, str]] = {}
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
        out[key] = (title, url)
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


_TAG_RE = re.compile(r"<[^>]+>")


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


def gemini_api_key() -> str | None:
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip() or None


_RETRY_IN_RE = re.compile(r"retry in ([\d.]+)\s*s", re.I)


def _is_gemini_rate_limit(exc: BaseException) -> bool:
    try:
        from google.genai import errors as genai_errors

        if isinstance(exc, genai_errors.APIError) and getattr(exc, "code", None) == 429:
            return True
    except ImportError:
        pass
    msg = str(exc).upper()
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg


def _gemini_retry_sleep_seconds(exc: BaseException, attempt: int) -> float:
    m = _RETRY_IN_RE.search(str(exc))
    if m:
        return min(max(float(m.group(1)) + 2.0, 6.0), 120.0)
    return min(15.0 * (2**attempt), 120.0)


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
        )


def _gemini_payload_for_indices(
    out: list[NewsItem],
    indices: list[int],
    max_excerpt_chars: int,
) -> list[dict]:
    payload: list[dict] = []
    for i in indices:
        it = out[i]
        excerpt = it.summary
        if len(excerpt) > max_excerpt_chars:
            excerpt = excerpt[: max_excerpt_chars - 1].rsplit(" ", 1)[0] + "…"
        payload.append(
            {
                "index": i,
                "link": it.link,
                "source": it.source_name,
                "title": it.topic,
                "rss_excerpt": excerpt,
            }
        )
    return payload


def gemini_batch_enrich(
    items: list[NewsItem],
    *,
    api_key: str,
    model: str,
    max_excerpt_chars: int,
    max_output_tokens: int,
    request_timeout_s: int,
    chunk_size: int,
    chunk_pause_s: float,
    max_retries: int,
) -> list[NewsItem]:
    """
    Rewrite summaries/analysis via Gemini using chunked API calls and pauses to respect
    free-tier tokens-per-minute / requests-per-minute, with retries on 429 RESOURCE_EXHAUSTED.
    """
    if not items:
        return items

    try:
        from google import genai
        from google.genai import errors as genai_errors
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Install google-genai (see requirements-security-news.txt).") from exc

    n = len(items)
    if chunk_size <= 0 or chunk_size > n:
        chunk_size = n

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

    client = genai.Client(api_key=api_key)
    out = list(items)

    for start in range(0, n, chunk_size):
        end = min(start + chunk_size, n)
        indices = list(range(start, end))
        payload = _gemini_payload_for_indices(out, indices, max_excerpt_chars)
        prompt = instructions + json.dumps(payload, ensure_ascii=False)
        tokens_this_chunk = min(max_output_tokens, max(2048, 450 * len(indices)))

        for attempt in range(max_retries):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.35,
                        max_output_tokens=tokens_this_chunk,
                        response_mime_type="application/json",
                        http_options=types.HttpOptions(timeout=request_timeout_s * 1000),
                    ),
                )
                raw_text = (getattr(resp, "text", None) or "").strip()
                if not raw_text:
                    pf = getattr(resp, "prompt_feedback", None)
                    raise RuntimeError(f"Gemini returned empty text. prompt_feedback={pf!r}")

                rows = _parse_gemini_json_list(raw_text)
                if len(rows) != len(indices):
                    print(
                        f"Warning: Gemini returned {len(rows)} rows for chunk indices {start}-{end - 1}, "
                        f"expected {len(indices)}; merging partial results.",
                        file=sys.stderr,
                    )
                by_i, by_l = _rows_to_index_maps(rows)
                _apply_gemini_rows(out, indices, by_i, by_l)
                break
            except genai_errors.APIError as exc:
                if _is_gemini_rate_limit(exc) and attempt < max_retries - 1:
                    delay = _gemini_retry_sleep_seconds(exc, attempt)
                    print(
                        f"Gemini 429 / quota on chunk {start}-{end - 1}; sleeping {delay:.1f}s "
                        f"(retry {attempt + 2}/{max_retries}).",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                raise
            except Exception as exc:
                if _is_gemini_rate_limit(exc) and attempt < max_retries - 1:
                    delay = _gemini_retry_sleep_seconds(exc, attempt)
                    print(
                        f"Gemini rate/quota error on chunk {start}-{end - 1}; sleeping {delay:.1f}s "
                        f"(retry {attempt + 2}/{max_retries}).",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                raise

        if end < n and chunk_pause_s > 0:
            time.sleep(chunk_pause_s)

    return out


_DEFAULT_UA = (
    "Mozilla/5.0 (compatible; reports-fetch_security_news/1.0; "
    "+https://github.com/) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


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


def parse_feed(source_id: str, source_name: str, url: str, timeout: int) -> list[NewsItem]:
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


def sort_key(item: NewsItem) -> tuple[float, str]:
    if item.published:
        try:
            ts = datetime.fromisoformat(item.published.replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = 0.0
    else:
        ts = 0.0
    return (-ts, item.link)


def gather(
    feeds: dict[str, tuple[str, str]],
    source_ids: list[str],
    per_source: int,
    timeout: int,
    pause_s: float,
) -> list[NewsItem]:
    collected: list[NewsItem] = []
    errors: list[str] = []

    for i, sid in enumerate(source_ids):
        if sid not in feeds:
            errors.append(f"Unknown source id: {sid}")
            continue
        name, url = feeds[sid]
        try:
            batch = parse_feed(sid, name, url, timeout)[:per_source]
            collected.extend(batch)
        except Exception as exc:  # noqa: BLE001 — surface per-feed failures
            errors.append(f"{name}: {exc}")
        if pause_s > 0 and i < len(source_ids) - 1:
            time.sleep(pause_s)

    if errors:
        print("Warnings:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)

    # Dedupe by normalized URL, keep newest-first occurrence
    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for it in sorted(collected, key=sort_key):
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
    gemini_model: str | None = None,
    read_news_summary_model: str = READ_NEWS_SUMMARY_MODEL_DEFAULT,
    read_news_tts_model: str = READ_NEWS_TTS_MODEL_DEFAULT,
    read_news_voice: str = READ_NEWS_VOICE_DEFAULT,
) -> str:
    when = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    day_default = str(server_days if server_days is not None else 14)
    filter_note = (
        f"Feed window (server): last {server_days} day(s), parseable publish dates only."
        if server_days is not None
        else "No server date window; use the control below to hide older cards in the browser."
    )
    if gemini_model:
        gemini_note = (
            f"Summaries and analysis were generated by Google Gemini ({gemini_model}) in one batched request "
            f"from RSS excerpts only; verify critical facts against the original article."
        )
    else:
        gemini_note = "Summaries are from RSS feeds; analysis uses local keyword heuristics (no LLM)."
    reader_frag = (
        " Read news: "
        + html_module.escape(read_news_summary_model)
        + " prepares lines, "
        + html_module.escape(read_news_tts_model)
        + " synthesizes audio (voice "
        + html_module.escape(read_news_voice)
        + "); API key prompt, sessionStorage only."
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
        cards.append(
            f"""<article class="card"{data_ts}>
  <header class="card-head">
    <span class="src">{src}</span>
    <time{time_attr}>{pub}</time>
  </header>
  <h2 class="topic"><a href="{link_href}" rel="noopener noreferrer">{topic}</a></h2>
  <section class="block"><h3>Summary</h3><p>{summary}</p></section>
  <section class="block analysis"><h3>Analysis</h3><p>{analysis}</p></section>
  <div class="actions">
    <a class="btn-li" href="{li_href}" target="_blank" rel="noopener noreferrer">Post to LinkedIn</a>
    <a class="btn-sec" href="{link_href}" rel="noopener noreferrer">Open article</a>
  </div>
  <p class="linkrow"><a class="full" href="{link_href}" rel="noopener noreferrer">{link_text}</a></p>
</article>"""
        )

    body = "\n".join(cards)
    gemini_note_esc = html_module.escape(gemini_note)
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
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Security news digest</h1>
    <p class="meta">Generated {html_module.escape(when)} · <span id="visibleCount">{len(items)}</span> shown<br/>
    <span class="submeta">{gemini_note_esc}</span></p>
    <div class="toolbar">
      <label>Show articles from last
        <input type="number" id="dayWindow" min="1" max="3650" value="{day_default}"/>
        days
      </label>
      <button type="button" class="apply" id="applyDays">Apply</button>
      <button type="button" class="reset" id="resetDays">Show all</button>
      <p class="filter-hint">{filter_hint_esc}</p>
{digest_reader_toolbar_inner(lang="en")}
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
  <script>
{digest_reader_script(lang="en", summary_model=read_news_summary_model, tts_model=read_news_tts_model, voice=read_news_voice)}
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
    parser.add_argument("--per-source", type=int, default=8, help="Max items to read from each feed (default 8).")
    parser.add_argument("--limit", type=int, default=15, help="Max items after merge, sort, dedupe (default 15).")
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
        "--gemini",
        action="store_true",
        help="Rewrite summary+analysis with Gemini (needs GEMINI_API_KEY or GOOGLE_API_KEY). "
        "Uses chunked requests + pauses to reduce 429 quota errors on the free tier.",
    )
    parser.add_argument(
        "--gemini-model",
        default="gemini-3-flash-preview",
        help="Model id for generateContent (default: Gemini 3 Flash preview). "
        "Live-only ids (e.g. gemini-3.1-flash-live-preview) are not supported here—use AI Studio ListModels.",
    )
    parser.add_argument(
        "--gemini-max-excerpt-chars",
        type=int,
        default=480,
        help="Max RSS excerpt chars per article sent to Gemini (smaller = fewer input tokens).",
    )
    parser.add_argument(
        "--gemini-max-output-tokens",
        type=int,
        default=8192,
        help="Max output tokens per Gemini chunk response.",
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
        default=6,
        metavar="N",
        help="Articles per Gemini request (smaller reduces input-token bursts). Use 0 for one request for all.",
    )
    parser.add_argument(
        "--gemini-chunk-pause",
        type=float,
        default=28.0,
        help="Seconds to sleep between Gemini chunks (helps free-tier requests/minute).",
    )
    parser.add_argument(
        "--gemini-retries",
        type=int,
        default=7,
        help="Retries per chunk on 429 / RESOURCE_EXHAUSTED (uses server 'retry in Xs' when present).",
    )
    parser.add_argument(
        "--read-news-summary-model",
        default=READ_NEWS_SUMMARY_MODEL_DEFAULT,
        metavar="MODEL_ID",
        help="Model for turning visible cards into read-aloud lines (JSON).",
    )
    parser.add_argument(
        "--read-news-tts-model",
        default=READ_NEWS_TTS_MODEL_DEFAULT,
        metavar="MODEL_ID",
        help="Gemini TTS model for speech audio (generateContent + AUDIO).",
    )
    parser.add_argument(
        "--read-news-voice",
        default=READ_NEWS_VOICE_DEFAULT,
        metavar="NAME",
        help="Prebuilt Gemini TTS voice (e.g. Enceladus, Kore).",
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
        sids = sorted(feeds.keys())
    else:
        sids = [s.strip().lower() for s in args.sources.split(",") if s.strip()]

    items = gather(feeds, sids, per_source=args.per_source, timeout=args.timeout, pause_s=args.pause)
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

    gemini_model_used: str | None = None
    if args.gemini:
        key = gemini_api_key()
        if not key:
            print(
                "Error: --gemini requires GEMINI_API_KEY or GOOGLE_API_KEY in the environment.",
                file=sys.stderr,
            )
            return 2
        try:
            items = gemini_batch_enrich(
                items,
                api_key=key,
                model=args.gemini_model,
                max_excerpt_chars=args.gemini_max_excerpt_chars,
                max_output_tokens=args.gemini_max_output_tokens,
                request_timeout_s=args.gemini_timeout,
                chunk_size=args.gemini_chunk_size,
                chunk_pause_s=args.gemini_chunk_pause,
                max_retries=args.gemini_retries,
            )
            gemini_model_used = args.gemini_model
            print(f"Gemini batch enrichment OK ({args.gemini_model}, {len(items)} articles).", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"Gemini batch failed; keeping RSS/heuristic text: {exc}", file=sys.stderr)

    if args.html:
        out = Path(args.html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            build_html(
                items,
                server_days=server_days,
                gemini_model=gemini_model_used,
                read_news_summary_model=args.read_news_summary_model,
                read_news_tts_model=args.read_news_tts_model,
                read_news_voice=args.read_news_voice,
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
