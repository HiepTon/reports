#!/usr/bin/env python3
"""
Tổng hợp tin RSS từ các báo Việt Nam, tóm tắt và gắn nhóm chủ đề bằng Gemini (tùy chọn).

Vietnam news digest: fetch RSS (Tuổi Trẻ, Thanh Niên, Dân Trí fetched first; global sort newest-first with
priority tie-break). After --limit, Tuổi Trẻ (tuoitre.vn) items are moved to the top as tin nổi bật.
Optional --gemini for Vietnamese summaries + categories.

Usage:
  pip install -r requirements-vietnam-news.txt
  python scripts/fetch_vietnam_news.py --limit 15
  GEMINI_API_KEY=... python scripts/fetch_vietnam_news.py --gemini --html output/vietnam/index.html
"""

from __future__ import annotations

import argparse
import gzip
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
_DEFAULT_FEEDS_PATH = _REPO_ROOT / "config" / "vietnam_news_feeds.json"

# Gemini must pick exactly one label (Vietnamese) per article
VIETNAM_CATEGORY_LABELS: tuple[str, ...] = (
    "Thời sự",
    "Kinh tế",
    "Thế giới",
    "Pháp luật",
    "Công nghệ",
    "Thể thao",
    "Văn hóa – Giải trí",
    "Đời sống",
    "Sức khỏe",
    "Giáo dục",
    "Môi trường",
    "Khác",
)

# Fetch + sort: these source ids first (tuổi trẻ.vn, thanhnien.vn, dantri.com.vn), then others by time
PRIORITY_SOURCE_ORDER: tuple[str, ...] = ("tuoitre", "thanhnien", "dantri")

# After merge/limit: show tuoitre.vn first (“top news”), then other sources (each group newest-first).
TOP_NEWS_SOURCE_ID = "tuoitre"

_TAG_RE = re.compile(r"<[^>]+>")
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*", re.I | re.M)
_JSON_FENCE_TAIL_RE = re.compile(r"\s*```\s*$", re.M)
_RETRY_IN_RE = re.compile(r"retry in ([\d.]+)\s*s", re.I)


@dataclass(frozen=True)
class VietnamNewsItem:
    source_id: str
    source_name: str
    topic: str
    summary: str
    category: str
    link: str
    published: str | None


def default_feeds_config_path() -> Path:
    return _DEFAULT_FEEDS_PATH


def load_feeds(path: Path) -> dict[str, tuple[str, str, list[str]]]:
    """Returns feed_id -> (title, primary_feed_url, extra_fallback_urls)."""
    if not path.is_file():
        raise FileNotFoundError(f"Feeds config not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("feeds")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"Config {path} must contain a non-empty 'feeds' object.")
    out: dict[str, tuple[str, str, list[str]]] = {}
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
        fb_raw = meta.get("feed_url_fallbacks") or meta.get("fallback_feed_urls") or []
        fallbacks: list[str] = []
        if isinstance(fb_raw, list):
            fallbacks = [str(u).strip() for u in fb_raw if str(u).strip()]
        out[key] = (title, url, fallbacks)
    return out


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


def entry_published_iso(entry: feedparser.FeedParserDict) -> str | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return None
    try:
        dt = datetime(*t[:6], tzinfo=timezone.utc)
        return dt.isoformat()
    except (TypeError, ValueError):
        return entry.get("published") or entry.get("updated")


def parse_item_datetime(item: VietnamNewsItem) -> datetime | None:
    if not item.published:
        return None
    try:
        return datetime.fromisoformat(item.published.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_by_recent_days(items: list[VietnamNewsItem], days: int) -> tuple[list[VietnamNewsItem], int, int]:
    if days < 1:
        raise ValueError("--days must be >= 1")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept: list[VietnamNewsItem] = []
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


_DEFAULT_UA = (
    "Mozilla/5.0 (compatible; reports-fetch_vietnam_news/1.0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _maybe_decompress_feed_body(raw: bytes) -> bytes:
    """Decode gzip-wrapped bodies when the server omits transparent urllib decoding."""
    if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B:
        try:
            return gzip.decompress(raw)
        except OSError:
            pass
    return raw


def fetch_feed_bytes(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _DEFAULT_UA,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return _maybe_decompress_feed_body(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc


def parse_feed(source_id: str, source_name: str, url: str, timeout: int) -> list[VietnamNewsItem]:
    raw = fetch_feed_bytes(url, timeout)
    parsed = feedparser.parse(raw)
    if getattr(parsed, "bozo_exception", None) and not parsed.entries:
        raise RuntimeError(f"{source_name}: feed error ({parsed.bozo_exception})")

    items: list[VietnamNewsItem] = []
    for entry in parsed.entries:
        title = strip_html(str(entry.get("title") or ""), 400)
        if not title:
            continue
        raw_summary = (
            entry.get("summary")
            or entry.get("description")
            or (entry.get("content", [{}])[0].get("value") if entry.get("content") else "")
            or ""
        )
        summary = strip_html(str(raw_summary), 500)
        if not summary:
            summary = "(Không có mô tả RSS; mở bài để đọc.)"

        link = entry_link(entry)
        if not link:
            continue

        items.append(
            VietnamNewsItem(
                source_id=source_id,
                source_name=source_name,
                topic=title,
                summary=summary,
                category="Chưa phân loại",
                link=link,
                published=entry_published_iso(entry),
            )
        )
    if not items:
        raise RuntimeError(f"{source_name}: no entries parsed from {url}")
    return items


def parse_feed_try_urls(
    source_id: str, source_name: str, urls: list[str], timeout: int
) -> list[VietnamNewsItem]:
    """Try each RSS URL until one yields items (primary first, then fallbacks)."""
    errs: list[str] = []
    for u in urls:
        u = u.strip()
        if not u:
            continue
        try:
            return parse_feed(source_id, source_name, u, timeout)
        except Exception as exc:  # noqa: BLE001
            errs.append(f"{u}: {exc}")
    raise RuntimeError(f"{source_name}: all feed URLs failed ({len(errs)}): " + " | ".join(errs))


def reorder_sources_for_priority(source_ids: list[str]) -> list[str]:
    """tuoitre → thanhnien → dantri first (when present), then remaining ids in stable order."""
    normalized = [s.strip().lower() for s in source_ids if s.strip()]
    pri = [s for s in PRIORITY_SOURCE_ORDER if s in normalized]
    rest = [s for s in normalized if s not in PRIORITY_SOURCE_ORDER]
    return pri + rest


def _priority_sort_tuple(source_id: str) -> tuple[int, int]:
    sid = source_id.strip().lower()
    try:
        return (0, PRIORITY_SOURCE_ORDER.index(sid))
    except ValueError:
        return (1, 0)


def sort_key(item: VietnamNewsItem) -> tuple[float, int, int, str]:
    """Newest first globally; on equal timestamp, prefer tuoitre → thanhnien → dantri → others."""
    pr = _priority_sort_tuple(item.source_id)
    if item.published:
        try:
            ts = datetime.fromisoformat(item.published.replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = 0.0
    else:
        ts = 0.0
    return (-ts, pr[0], pr[1], item.link)


def _is_tuoitre_top_item(item: VietnamNewsItem) -> bool:
    if item.source_id.strip().lower() == TOP_NEWS_SOURCE_ID:
        return True
    try:
        host = urlparse(item.link).netloc.lower()
    except (ValueError, AttributeError):
        return False
    return host == "tuoitre.vn" or host.endswith(".tuoitre.vn")


def prioritize_tuoitre_top(items: list[VietnamNewsItem]) -> list[VietnamNewsItem]:
    """Tuổi Trẻ first (newest within group), then other sources (newest within group)."""
    top = [x for x in items if _is_tuoitre_top_item(x)]
    rest = [x for x in items if not _is_tuoitre_top_item(x)]
    top.sort(key=sort_key)
    rest.sort(key=sort_key)
    return top + rest


def gather(
    feeds: dict[str, tuple[str, str, list[str]]],
    source_ids: list[str],
    per_source: int,
    timeout: int,
    pause_s: float,
) -> list[VietnamNewsItem]:
    source_ids = reorder_sources_for_priority(source_ids)
    collected: list[VietnamNewsItem] = []
    errors: list[str] = []

    for i, sid in enumerate(source_ids):
        if sid not in feeds:
            errors.append(f"Unknown source id: {sid}")
            continue
        name, url, fallbacks = feeds[sid]
        try:
            urls = [url] + list(fallbacks)
            batch = parse_feed_try_urls(sid, name, urls, timeout)[:per_source]
            collected.extend(batch)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
        if pause_s > 0 and i < len(source_ids) - 1:
            time.sleep(pause_s)

    if errors:
        print("Warnings:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)

    seen: set[str] = set()
    deduped: list[VietnamNewsItem] = []
    for it in sorted(collected, key=sort_key):
        nu = normalize_url(it.link)
        if nu in seen:
            continue
        seen.add(nu)
        deduped.append(it)
    return deduped


def strip_json_fenced_text(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = _JSON_FENCE_RE.sub("", t, count=1)
        t = _JSON_FENCE_TAIL_RE.sub("", t)
    return t.strip()


def gemini_api_key() -> str | None:
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip() or None


def _is_gemini_json_retryable(exc: BaseException) -> bool:
    if isinstance(exc, json.JSONDecodeError):
        return True
    msg = str(exc).lower()
    return (
        "unterminated string" in msg
        or "invalid control character" in msg
        or "invalid \\escape" in msg
    )


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
    parsed = json.loads(strip_json_fenced_text(raw_text))
    if isinstance(parsed, dict):
        for key in ("articles", "items", "results", "output"):
            if key in parsed and isinstance(parsed[key], list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        raise RuntimeError("Gemini JSON must be an array (or an object wrapping an array).")
    return parsed


def _normalize_category(raw: str) -> str:
    t = (raw or "").strip()
    if t in VIETNAM_CATEGORY_LABELS:
        return t
    for lab in VIETNAM_CATEGORY_LABELS:
        if lab.lower() == t.lower():
            return lab
    return "Khác"


def _rows_to_maps(rows: list) -> tuple[dict[int, dict], dict[str, dict]]:
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


def _payload_for_indices(out: list[VietnamNewsItem], indices: list[int], max_excerpt_chars: int) -> list[dict]:
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


def gemini_vietnam_enrich(
    items: list[VietnamNewsItem],
    *,
    api_key: str,
    model: str,
    max_excerpt_chars: int,
    max_output_tokens: int,
    request_timeout_s: int,
    chunk_size: int,
    chunk_pause_s: float,
    max_retries: int,
) -> list[VietnamNewsItem]:
    if not items:
        return items

    try:
        from google import genai
        from google.genai import errors as genai_errors
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Install google-genai (see requirements-vietnam-news.txt).") from exc

    cats_literal = " | ".join(VIETNAM_CATEGORY_LABELS)
    instructions = (
        "Bạn là biên tập viên tin tức Việt Nam. Bạn chỉ nhận tiêu đề, nguồn và đoạn mô tả RSS — không có toàn văn bài báo. "
        "Không bịa sự kiện, con số hay trích dẫn không có trong dữ liệu.\n\n"
        "Trả về DUY NHẤT một mảng JSON (không dùng markdown). Mỗi phần tử là object với các khóa đúng: "
        '"index" (số nguyên khớp input), "link" (chuỗi giống input), '
        '"summary" (2–4 câu tiếng Việt, dễ đọc), '
        f'"category" (chuỗi, PHẢI là một trong các nhãn sau, đúng chính tả: {cats_literal}).\n\n'
        "Trong summary/category chỉ dùng chuỗi JSON hợp lệ: không xuống dòng thô trong string; dùng \\n nếu cần.\n\n"
        "Mảng phải cùng độ dài và cùng các index như danh sách đầu vào.\n\n"
        "INPUT_ARTICLES_JSON:\n"
    )

    n = len(items)
    if chunk_size <= 0 or chunk_size > n:
        chunk_size = n

    client = genai.Client(api_key=api_key)
    out = list(items)

    for start in range(0, n, chunk_size):
        end = min(start + chunk_size, n)
        indices = list(range(start, end))
        payload = _payload_for_indices(out, indices, max_excerpt_chars)
        prompt = instructions + json.dumps(payload, ensure_ascii=False)

        for attempt in range(max_retries):
            base_cap = min(max_output_tokens, max(2048, 500 * len(indices)))
            tokens_this_chunk = min(max_output_tokens, int(base_cap * (1.4**attempt)))

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
                        f"Warning: Gemini returned {len(rows)} rows for chunk {start}-{end - 1}, "
                        f"expected {len(indices)}; merging partial.",
                        file=sys.stderr,
                    )
                by_i, by_l = _rows_to_maps(rows)
                for i in indices:
                    it = out[i]
                    row = by_i.get(i) or by_l.get(normalize_url(it.link))
                    if not row:
                        continue
                    summary = str(row.get("summary") or "").strip()
                    cat = _normalize_category(str(row.get("category") or ""))
                    if not summary:
                        continue
                    out[i] = replace(out[i], summary=summary[:4000], category=cat)
                break
            except genai_errors.APIError as exc:
                if _is_gemini_rate_limit(exc) and attempt < max_retries - 1:
                    delay = _gemini_retry_sleep_seconds(exc, attempt)
                    print(
                        f"Gemini 429; chunk {start}-{end - 1}, chờ {delay:.1f}s "
                        f"(lần {attempt + 2}/{max_retries}).",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                raise
            except Exception as exc:
                if _is_gemini_rate_limit(exc) and attempt < max_retries - 1:
                    delay = _gemini_retry_sleep_seconds(exc, attempt)
                    print(
                        f"Gemini quota; chunk {start}-{end - 1}, chờ {delay:.1f}s "
                        f"(lần {attempt + 2}/{max_retries}).",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                if _is_gemini_json_retryable(exc) and attempt < max_retries - 1:
                    delay = min(5.0 * (1.6**attempt), 90.0)
                    print(
                        f"Gemini JSON lỗi ({exc!s}); chunk {start}-{end - 1}, chờ {delay:.1f}s "
                        f"(lần {attempt + 2}/{max_retries}, max_output_tokens={tokens_this_chunk}).",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                raise

        if end < n and chunk_pause_s > 0:
            time.sleep(chunk_pause_s)

    return out


def _category_slug(cat: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", cat.lower(), flags=re.UNICODE)
    return re.sub(r"-+", "-", s).strip("-") or "khac"


def _vietnam_card_html(it: VietnamNewsItem) -> str:
    pub = html_module.escape(it.published or "không rõ ngày")
    topic = html_module.escape(it.topic)
    src = html_module.escape(it.source_name)
    summary = html_module.escape(it.summary)
    cat = html_module.escape(it.category)
    slug = html_module.escape(_category_slug(it.category))
    link_href = html_module.escape(it.link, quote=True)
    link_text = html_module.escape(it.link)
    time_attr = ""
    if it.published:
        time_attr = f' datetime="{html_module.escape(it.published)}"'
    dt = parse_item_datetime(it)
    data_ts = f' data-ts="{int(dt.timestamp() * 1000)}"' if dt else ""
    top_cls = " card-top" if _is_tuoitre_top_item(it) else ""
    return (
        f"""<article class="card{top_cls}" data-category="{slug}"{data_ts}>
  <header class="card-head">
    <span class="badge">{cat}</span>
    <span class="src">{src}</span>
    <time{time_attr}>{pub}</time>
  </header>
  <h2 class="topic"><a href="{link_href}" rel="noopener noreferrer">{topic}</a></h2>
  <section class="block"><h3>Tóm tắt</h3><p>{summary}</p></section>
  <p class="linkrow"><a class="full" href="{link_href}" rel="noopener noreferrer">{link_text}</a></p>
</article>"""
    )


def build_html(
    items: list[VietnamNewsItem],
    *,
    generated_at: datetime | None = None,
    server_days: int | None = None,
    gemini_model: str | None = None,
    read_news_summary_model: str = READ_NEWS_SUMMARY_MODEL_DEFAULT,
    read_news_tts_model: str = READ_NEWS_TTS_MODEL_DEFAULT,
    read_news_voice: str = READ_NEWS_VOICE_DEFAULT,
) -> str:
    when = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    day_default = str(server_days if server_days is not None else 7)
    filter_note = (
        f"Lọc theo ngày (server): {server_days} ngày gần nhất (UTC), chỉ bài có ngày parse được."
        if server_days is not None
        else "Không lọc ngày trên server; dùng ô bên dưới để lọc trong trình duyệt."
    )
    if gemini_model:
        ai_note = f"Tóm tắt và nhóm chủ đề: Google Gemini ({gemini_model}), theo đoạn RSS. Tin Tuổi Trẻ (tuoitre.vn) hiển thị trước trong mục tin nổi bật."
    else:
        ai_note = "Tóm tắt từ RSS; nhóm chủ đề: Chưa phân loại (chạy với --gemini để dùng AI). Tin Tuổi Trẻ hiển thị trước trong mục tin nổi bật."

    reader_hint = (
        " Nút Đọc tin: "
        + html_module.escape(read_news_summary_model)
        + " soạn lời đọc, "
        + html_module.escape(read_news_tts_model)
        + " tạo âm thanh (giọng "
        + html_module.escape(read_news_voice)
        + "); cần API key, lưu session trong tab."
    )

    idx = 0
    while idx < len(items) and _is_tuoitre_top_item(items[idx]):
        idx += 1
    top_sec, rest_sec = items[:idx], items[idx:]
    blocks: list[str] = []
    if top_sec:
        blocks.append('<h2 class="digest-section">Tin nổi bật — Tuổi Trẻ Online</h2>')
        blocks.extend(_vietnam_card_html(x) for x in top_sec)
    if rest_sec:
        if top_sec:
            blocks.append('<h2 class="digest-section digest-section-muted">Tin từ các nguồn khác</h2>')
        blocks.extend(_vietnam_card_html(x) for x in rest_sec)
    body = "\n".join(blocks)
    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Tin Việt Nam — tổng hợp</title>
  <style>
    :root {{
      --bg: #0f1419;
      --card: #1a2332;
      --text: #e7ecf3;
      --muted: #9fb0c8;
      --accent: #5bd59b;
      --border: #2a3a52;
      --badge: #3d6b55;
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
    .filter-hint {{ width: 100%; font-size: 0.8rem; color: var(--muted); margin: 0.25rem 0 0; }}
    .card {{
      background: var(--card); border: 1px solid var(--border); border-radius: 10px;
      padding: 1rem 1.15rem 1.1rem; margin-bottom: 1rem;
    }}
    .card-head {{ display: flex; flex-wrap: wrap; gap: 0.5rem 0.75rem; align-items: baseline;
      font-size: 0.82rem; color: var(--muted); margin-bottom: 0.35rem; }}
    .badge {{ background: var(--badge); color: #dfffea; padding: 0.15rem 0.5rem; border-radius: 6px;
      font-weight: 650; font-size: 0.78rem; }}
    .src {{ font-weight: 600; color: var(--accent); margin-left: auto; }}
    .topic {{ font-size: 1.08rem; margin: 0.35rem 0 0.75rem; line-height: 1.35; }}
    .topic a {{ color: var(--text); text-decoration: none; }}
    .topic a:hover {{ text-decoration: underline; color: var(--accent); }}
    .block h3 {{ margin: 0 0 0.35rem; font-size: 0.72rem; text-transform: uppercase;
      letter-spacing: 0.06em; color: var(--muted); font-weight: 650; }}
    .block p {{ margin: 0; font-size: 0.95rem; color: #d5dde8; }}
    .linkrow {{ margin: 0.85rem 0 0; word-break: break-all; font-size: 0.85rem; }}
    a.full {{ color: var(--accent); }}
    .digest-section {{ font-size: 1.02rem; font-weight: 650; margin: 1.35rem 0 0.65rem; color: var(--accent); letter-spacing: 0.02em; }}
    .digest-section:first-of-type {{ margin-top: 0.2rem; }}
    .digest-section-muted {{ color: #8fa6bf; font-size: 0.98rem; }}
    .card.card-top {{ border-color: #3d5c4a; box-shadow: 0 0 0 1px rgba(91, 213, 155, 0.12); }}
{digest_reader_css()}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Tin Việt Nam — tổng hợp RSS</h1>
    <p class="meta">Cập nhật {html_module.escape(when)} · <span id="visibleCount">{len(items)}</span> bài<br/>
    <span class="submeta">{html_module.escape(ai_note)}</span></p>
    <div class="toolbar">
      <label>Hiện bài trong
        <input type="number" id="dayWindow" min="1" max="3650" value="{day_default}"/>
        ngày gần nhất
      </label>
      <button type="button" class="apply" id="applyDays">Áp dụng</button>
      <button type="button" class="reset" id="resetDays">Hiện tất cả</button>
      <p class="filter-hint">{html_module.escape(filter_note)} Bài không có data-ts sẽ bị ẩn khi lọc.{reader_hint}</p>
{digest_reader_toolbar_inner(lang="vi")}
    </div>
{body}
  </div>
  <script>
(function() {{
  function countVisible() {{
    var n = document.querySelectorAll("article.card:not([hidden])").length;
    var el = document.getElementById("visibleCount");
    if (el) el.textContent = n;
  }}
  function applyDayFilter() {{
    var input = document.getElementById("dayWindow");
    var n = parseInt(input && input.value, 10);
    if (!input || !isFinite(n) || n < 1) {{ alert("Nhập số ngày dương."); return; }}
    var cutoff = Date.now() - n * 86400000;
    document.querySelectorAll("article.card").forEach(function(el) {{
      var raw = el.getAttribute("data-ts");
      if (raw === null || raw === "") {{ el.hidden = true; return; }}
      var ts = parseInt(raw, 10);
      if (!isFinite(ts)) {{ el.hidden = true; return; }}
      el.hidden = ts < cutoff;
    }});
    countVisible();
  }}
  function resetFilter() {{
    document.querySelectorAll("article.card").forEach(function(el) {{ el.hidden = false; }});
    countVisible();
  }}
  var b1 = document.getElementById("applyDays");
  var b2 = document.getElementById("resetDays");
  if (b1) b1.addEventListener("click", applyDayFilter);
  if (b2) b2.addEventListener("click", resetFilter);
}})();
  </script>
  <script>
{digest_reader_script(lang="vi", summary_model=read_news_summary_model, tts_model=read_news_tts_model, voice=read_news_voice)}
  </script>
</body>
</html>
"""


def format_console(items: list[VietnamNewsItem]) -> str:
    lines: list[str] = []
    for it in items:
        pub = it.published or "không rõ ngày"
        lines.append(
            f"## [{it.category}] [{it.source_name}] {it.topic}\n"
            f"**Ngày:** {pub}\n"
            f"**Tóm tắt:** {it.summary}\n"
            f"**Link:** {it.link}\n"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tổng hợp tin Việt Nam từ RSS, tùy chọn Gemini (tiếng Việt + nhóm).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--feeds-config", type=Path, default=None, help="JSON feeds (default: config/vietnam_news_feeds.json).")
    parser.add_argument("--sources", default="all", help="Danh sách id nguồn (phẩy) hoặc all.")
    parser.add_argument("--per-source", type=int, default=12)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--pause", type=float, default=0.25)
    parser.add_argument("--days", type=int, default=None, metavar="N")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--html", metavar="PATH")
    parser.add_argument("--gemini", action="store_true")
    parser.add_argument("--gemini-model", default="gemini-3-flash-preview")
    parser.add_argument("--gemini-max-excerpt-chars", type=int, default=480)
    parser.add_argument("--gemini-max-output-tokens", type=int, default=8192)
    parser.add_argument("--gemini-timeout", type=int, default=180)
    parser.add_argument("--gemini-chunk-size", type=int, default=6)
    parser.add_argument("--gemini-chunk-pause", type=float, default=28.0)
    parser.add_argument("--gemini-retries", type=int, default=7)
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
        print("Lỗi: --days phải >= 1.", file=sys.stderr)
        return 2

    feeds_path = args.feeds_config or default_feeds_config_path()
    try:
        feeds = load_feeds(feeds_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Lỗi đọc feeds: {feeds_path}: {exc}", file=sys.stderr)
        return 2

    sids = sorted(feeds.keys()) if args.sources.strip().lower() == "all" else [
        s.strip().lower() for s in args.sources.split(",") if s.strip()
    ]

    items = gather(feeds, sids, args.per_source, args.timeout, args.pause)
    server_days: int | None = None
    if args.days is not None:
        server_days = args.days
        items, dnd, dold = filter_by_recent_days(items, args.days)
        if dnd or dold:
            print(
                f"Lọc --days {args.days}: bỏ {dold} bài quá cũ, {dnd} bài không có ngày.",
                file=sys.stderr,
            )
    items = items[: args.limit]

    gemini_model_used: str | None = None
    if args.gemini:
        key = gemini_api_key()
        if not key:
            print("Cần GEMINI_API_KEY hoặc GOOGLE_API_KEY khi dùng --gemini.", file=sys.stderr)
            return 2
        try:
            items = gemini_vietnam_enrich(
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
            print(f"Gemini OK ({args.gemini_model}, {len(items)} bài).", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"Gemini lỗi, giữ tóm tắt RSS: {exc}", file=sys.stderr)

    items = prioritize_tuoitre_top(items)

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
        print(f"Đã ghi {out.resolve()} ({len(items)} bài).", file=sys.stderr)

    if args.json:
        print(json.dumps([asdict(x) for x in items], ensure_ascii=False, indent=2))
    elif not args.html:
        print(format_console(items))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
