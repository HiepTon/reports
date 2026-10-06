#!/usr/bin/env python3
"""Fetch each feed_url in a feeds config and count items for today / yesterday /
last 7 days (bucketed by publish date in Vietnam time).

Usage:
    python3 scripts/feed_stats.py [CONFIG]

CONFIG defaults to config/weekly_news_feeds.json (resolved from the repo root).
Note: most feeds only expose their latest 50-60 items, so the 7-day count is a
lower bound when a feed's oldest listed item is still within the window.
"""
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VN = timezone(timedelta(hours=7))
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "weekly_news_feeds.json"


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    # RFC822 (standard RSS pubDate)
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(VN)
    except Exception:
        pass
    # ISO8601 (Atom published/updated)
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(VN)
    except Exception:
        pass
    # Tuoi Tre style: "10/5/2026 2:00:00 PM" (M/D/YYYY, assumed VN local time)
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=VN)
        except Exception:
            continue
    return None


def strip_ns(tag):
    return tag.split("}", 1)[-1].lower()


def item_dates(root):
    dates = []
    for el in root.iter():
        if strip_ns(el.tag) in ("item", "entry"):
            dt = None
            for ch in el:
                if strip_ns(ch.tag) in ("pubdate", "published", "updated", "date"):
                    dt = parse_date(ch.text)
                    if dt:
                        break
            dates.append(dt)
    return dates


def fetch(key_meta, today, yesterday, week_cut):
    key, meta = key_meta
    url = meta["feed_url"]
    row = {"key": key, "url": url, "today": 0, "yesterday": 0, "week": 0,
           "total": 0, "no_date": 0, "err": None}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (feed-stats)"})
        data = urllib.request.urlopen(req, timeout=30).read()
        root = ET.fromstring(data)
        for dt in item_dates(root):
            row["total"] += 1
            if dt is None:
                row["no_date"] += 1
                continue
            if dt.date() == today:
                row["today"] += 1
            elif dt.date() == yesterday:
                row["yesterday"] += 1
            if dt >= week_cut:
                row["week"] += 1
    except Exception as e:
        row["err"] = f"{type(e).__name__}: {e}"
    return row


def main():
    config = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    feeds = json.loads(config.read_text(encoding="utf-8"))["feeds"]

    now = datetime.now(VN)
    today = now.date()
    yesterday = today - timedelta(days=1)
    week_cut = now - timedelta(days=7)

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(lambda km: fetch(km, today, yesterday, week_cut), feeds.items()))

    print(f"Now (VN, +07): {now:%Y-%m-%d %H:%M}  | today={today} "
          f"yesterday={yesterday} week>= {week_cut:%Y-%m-%d %H:%M}\n")
    hdr = f"{'feed_url':55} {'today':>5} {'yest':>5} {'7d':>4} {'tot':>4}  note"
    print(hdr)
    print("-" * len(hdr))
    tt = ty = tw = ttot = 0
    for r in sorted(rows, key=lambda x: (-x["week"], x["key"])):
        note = r["err"] or (f"{r['no_date']} items missing date" if r["no_date"] else "")
        print(f"{r['url']:55} {r['today']:>5} {r['yesterday']:>5} "
              f"{r['week']:>4} {r['total']:>4}  {note}")
        if not r["err"]:
            tt += r["today"]; ty += r["yesterday"]; tw += r["week"]; ttot += r["total"]
    print("-" * len(hdr))
    print(f"{'TOTAL':55} {tt:>5} {ty:>5} {tw:>4} {ttot:>4}")


if __name__ == "__main__":
    main()
