# reports

Workspace for security reporting utilities and generated artifacts.

## Security news digest

`scripts/fetch_security_news.py` pulls recent headlines from public RSS/Atom feeds, then either keeps **RSS summaries + keyword heuristics** or, with **`--gemini`**, calls **Google Gemini in small batches** (with pauses and **429 retries**) to rewrite **summary** and **analysis**—this stays closer to **free-tier** token and request limits than one giant prompt. It always outputs the **canonical article URL**.

**Feed list:** edit [`config/security_news_feeds.json`](config/security_news_feeds.json) (see [Adding feeds](#adding-or-changing-feeds)). Override path with `--feeds-config`.

### Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-security-news.txt
```

### Usage

Text (Markdown-style) to stdout:

```bash
python scripts/fetch_security_news.py
python scripts/fetch_security_news.py --limit 20 --per-source 6
```

JSON to stdout:

```bash
python scripts/fetch_security_news.py --json --limit 10
```

Standalone **HTML** file (directory is created if needed). Progress is printed to stderr:

```bash
python scripts/fetch_security_news.py --html output/security_news.html
python scripts/fetch_security_news.py --html output/index.html
```

Only articles **published in the last N days** (RSS date, UTC). Entries without a parseable date are excluded when this flag is set. Increase `--per-source` if the list is too short after filtering.

```bash
python scripts/fetch_security_news.py --days 7 --html output/security_news.html --per-source 15
```

The HTML page includes a **number input** and **Apply** / **Show all** controls to narrow or reset the view in the browser (cards without a machine-readable date are hidden when you apply a client-side window). Each story has a **Post to LinkedIn** button that opens LinkedIn’s [share-offsite](https://www.linkedin.com/sharing/share-offsite/) flow with the article URL (you must be logged into LinkedIn; LinkedIn decides preview text from the article’s Open Graph tags, not from this digest).

Combine sources (comma-separated ids from your config, or `all`):

```bash
python scripts/fetch_security_news.py --sources projectzero,cisa,krebs --limit 8
```

Optional pause between feed requests (`--pause 0.5`) and HTTP timeout (`--timeout 30`).

### Google Gemini (chunked + 429 retries)

1. Create an API key in [Google AI Studio](https://aistudio.google.com/apikey).
2. Export **`GEMINI_API_KEY`** (or **`GOOGLE_API_KEY`**).
3. Run with **`--gemini`**.

```bash
export GEMINI_API_KEY="your-key"
python scripts/fetch_security_news.py --days 7 --limit 25 --gemini --html output/index.html
```

**Free tier / 429 RESOURCE_EXHAUSTED:** the script defaults to **several small API calls** (`--gemini-chunk-size` default **6** articles) with a **pause between chunks** (`--gemini-chunk-pause`, default **28s**) and **retries** that honor Google’s “retry in Xs” hint (`--gemini-retries`, default **7**). That reduces spikes in **input tokens per minute** and **requests per minute**. You can tighten further: `--gemini-chunk-size 4 --gemini-chunk-pause 35 --gemini-max-excerpt-chars 400`.

Default model is **`gemini-3-flash-preview`** (Gemini 3 Flash), which supports the **`generateContent`** API this script uses. **Gemini 3.x “Flash Live”** models (for example `gemini-3.1-flash-live-preview`) are for the **Live API** (WebSocket), not `generateContent`—using them here returns **404**. Override with **`--gemini-model`** to any id your key supports (check **List models** in [Google AI Studio](https://aistudio.google.com/)).

Tuning flags: **`--gemini-max-excerpt-chars`** (default 480), **`--gemini-max-output-tokens`**, **`--gemini-timeout`**, **`--gemini-chunk-size`** (use **0** for a single request containing every article—higher 429 risk on free tier).

If Gemini errors or returns unusable JSON, the script **falls back** to RSS + heuristics and still writes HTML/JSON.

**GitHub Actions:** add a repository secret **`GEMINI_API_KEY`**. The scheduled workflow passes **`--gemini` automatically when the secret is set**; if unset, the build uses RSS + heuristics only (no failure).

### Adding or changing feeds

1. Open [`config/security_news_feeds.json`](config/security_news_feeds.json).
2. Add an entry under `"feeds"` with a **lowercase id** (letters, numbers, underscores), a **`title`** (shown in the digest), and **`feed_url`** (RSS or Atom URL).

Example:

```json
"nakedsecurity": {
  "title": "Naked Security (Sophos)",
  "feed_url": "https://nakedsecurity.sophos.com/feed/"
}
```

3. Run locally to verify the feed parses (`python scripts/fetch_security_news.py --sources nakedsecurity --limit 3`).
4. Commit the JSON change and push; the next scheduled or manual workflow run will pick it up.

Many sites expose `/feed/`, `/rss`, or FeedBurner URLs. Prefer **official RSS/Atom** links; scraping HTML is out of scope for this script.

**SC Media / SC World:** scripted access to scworld.com is often blocked or not real RSS. If you get a stable feed URL, add it to the JSON like any other source.

### Other scripts

- `scripts/build_insight_report_docx.py` — builds insight report documents (see script docstring and usage there).

### Notes

- Without **`--gemini`**, analysis is **heuristic** (CVE, ransomware, phishing, patch, OT, etc.). With **`--gemini`**, both summary and analysis are model-generated from RSS excerpts only—**verify** important claims against the source article.
- Feeds and sites change; failures for one source are reported on stderr and skipped.
- Text output includes a **Post to LinkedIn** URL line per item; JSON includes **`linkedin_share_url`** on each object (same URL as the HTML button).

## Put this repo on GitHub

From your machine (replace `YOUR_USER` / `reports` with your account and repo name):

```bash
cd /path/to/reports
git init
git add .
git commit -m "Initial commit: security news digest and workflows"
git branch -M main
git remote add origin https://github.com/YOUR_USER/reports.git
git push -u origin main
```

Create the empty repository first in the GitHub UI (**New repository**), then run the commands above. Do not commit API keys; use **GitHub Actions secrets** (e.g. `GEMINI_API_KEY`) for Gemini.

Repository secrets (usual case)
  Open the repo on GitHub.
  Go to Settings (repo tabs).
  In the left sidebar: Secrets and variables → Actions.
  Open the Secrets tab (not “Variables” unless you want non-secret config).
  Click New repository secret.
  Name: use LIKE_THIS (e.g. GEMINI_API_KEY, HF_TOKEN). Convention: uppercase with underscores.
  Secret: paste the value once; you cannot view it again after saving (only update or delete).
  Save.

## GitHub Pages (public URL)

The workflow [`.github/workflows/security-news-daily.yml`](.github/workflows/security-news-daily.yml) builds **`output/index.html`** and deploys the whole **`output/`** directory to **GitHub Pages** on every run (schedule + manual).

### One-time repository settings

1. On GitHub: open the repo → **Settings** → **Pages**.
2. Under **Build and deployment**, set **Source** to **GitHub Actions** (not “Deploy from a branch”).
3. Save if prompted.

The first **deploy** job may ask you to **review and enable** the `github-pages` environment (GitHub shows a banner in the Actions run). Approve it once.

### Public URL shape

| Repo type | Site URL |
|-----------|----------|
| User/org site repo named `username.github.io` | `https://username.github.io/` |
| Normal project repo `username/reports` | `https://username.github.io/reports/` |

The digest is at the site root because the artifact contains **`index.html`**.

After a successful run, open **Actions** → latest workflow → **deploy** job → **github-pages** environment link, or check **Settings → Pages** for the live URL.

**Private repositories:** GitHub Pages for private repos requires a **paid** plan in many setups; for a **free** public digest URL, use a **public** repository (or keep using workflow artifacts only).

### Schedule and workflow artifact

- **Schedule:** `08:15` UTC daily (`cron` in the workflow file); [scheduled runs](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule) can be delayed slightly on GitHub’s side.
- **Manual run:** **Actions** → **Security news digest** → **Run workflow**.
- **Artifact:** each run still uploads **`security-news`** (zip with `index.html`) for offline download.

Edit the workflow YAML to change `--days`, `--limit`, `--per-source`, or `cron`.

## Run daily on your own machine

Use **cron** (Linux/macOS) or **Task Scheduler** (Windows) to run the same `python scripts/fetch_security_news.py ... --html ...` command once a day inside your local venv.
