#!/usr/bin/env python3
"""Generate a browsable index of archived digest snapshots.

Scans <archive-dir>/security/*.html and <archive-dir>/vietnam/*.html (dated
YYYY-MM-DD.html) and writes a self-contained index.html linking to each snapshot,
newest first. Run by the Pages workflows after copying today's page into the
archive. Links are relative to the index location (e.g. "security/2026-09-23.html").
"""

from __future__ import annotations

import argparse
import html
import re
from datetime import datetime, timezone
from pathlib import Path

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$")

# (subdir, EN heading) — the archive index is English (site root is the security digest).
_SECTIONS = [
    ("security", "Security digest"),
    ("vietnam", "Vietnam digest"),
]


def _dated_snapshots(section_dir: Path) -> list[tuple[str, str]]:
    """Return (date, filename) for dated snapshots in section_dir, newest first."""
    if not section_dir.is_dir():
        return []
    found: list[tuple[str, str]] = []
    for p in section_dir.iterdir():
        m = _DATE_RE.match(p.name)
        if m:
            found.append((m.group(1), p.name))
    found.sort(key=lambda t: t[0], reverse=True)
    return found


def _section_html(archive_dir: Path, subdir: str, heading: str) -> str:
    snaps = _dated_snapshots(archive_dir / subdir)
    if not snaps:
        return (
            f'    <section class="group">\n'
            f"      <h2>{html.escape(heading)}</h2>\n"
            f'      <p class="empty">No snapshots yet.</p>\n'
            f"    </section>"
        )
    items = "\n".join(
        f'        <li><a href="{subdir}/{html.escape(fn)}">{html.escape(date)}</a></li>'
        for date, fn in snaps
    )
    return (
        f'    <section class="group">\n'
        f"      <h2>{html.escape(heading)} <span class=\"count\">({len(snaps)})</span></h2>\n"
        f'      <ul class="snaps">\n{items}\n      </ul>\n'
        f"    </section>"
    )


def build_index_html(archive_dir: Path) -> str:
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections = "\n".join(_section_html(archive_dir, sub, head) for sub, head in _SECTIONS)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Digest archive</title>
  <style>
    :root {{ --bg: #0b111a; --card: #121a26; --border: #223046; --text: #d5dde8;
      --muted: #8fa6bf; --accent: #5bd59b; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 1.5rem 16px; background: var(--bg); color: var(--text);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; line-height: 1.5; }}
    .wrap {{ max-width: 760px; margin: 0 auto; }}
    h1 {{ font-size: 1.4rem; margin: 0 0 0.25rem; }}
    .meta {{ color: var(--muted); font-size: 0.85rem; margin: 0 0 0.35rem; }}
    .nav {{ margin: 0 0 1.5rem; font-size: 0.9rem; }}
    .nav a {{ color: var(--accent); text-decoration: none; margin-right: 1rem; }}
    .nav a:hover {{ text-decoration: underline; }}
    .group {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
      padding: 0.9rem 1.1rem; margin: 0 0 1rem; }}
    .group h2 {{ font-size: 1.05rem; margin: 0 0 0.6rem; color: var(--accent); }}
    .count {{ color: var(--muted); font-weight: 400; font-size: 0.85rem; }}
    ul.snaps {{ list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 0.4rem 0.75rem; }}
    ul.snaps a {{ color: var(--text); text-decoration: none; border: 1px solid var(--border);
      border-radius: 6px; padding: 0.25rem 0.55rem; font-variant-numeric: tabular-nums; display: inline-block; }}
    ul.snaps a:hover {{ border-color: var(--accent); color: var(--accent); }}
    .empty {{ color: var(--muted); margin: 0; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Digest archive</h1>
    <p class="meta">Updated {html.escape(when)}</p>
    <p class="nav"><a href="../index.html">← Security (latest)</a><a href="../vietnam/index.html">Vietnam (latest) →</a></p>
{sections}
  </div>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the digest archive index page.")
    ap.add_argument("--archive-dir", required=True, help="Directory holding security/ and vietnam/ snapshots.")
    ap.add_argument("--out", required=True, help="Output path for the index HTML (e.g. site/archive/index.html).")
    args = ap.parse_args()

    archive_dir = Path(args.archive_dir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_index_html(archive_dir), encoding="utf-8")
    print(f"Wrote {out} (archive index).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
