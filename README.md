# reports

Workspace for security reporting utilities and generated artifacts.

## Security news digest

`scripts/fetch_security_news.py` pulls recent headlines from public RSS/Atom feeds, then either keeps **RSS summaries + keyword heuristics** or, with **`--summarize`**, calls **Groq (an LLM) in small batches** (with pauses and **429 retries**) to rewrite **summary** and **analysis**—this stays closer to **free-tier** request limits than one giant prompt. It always outputs the **canonical article URL**.

**Feed list:** edit [`config/security_news_feeds.json`](config/security_news_feeds.json) (see [Adding feeds](#adding-or-changing-feeds)). Override path with `--feeds-config`.

**Ordering:** items follow the **feed sequence in the config file**, then **newest-first within each feed**. Reorder feeds in the config to change the display order; on a duplicate URL, the earlier feed keeps the article.

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

### LLM summaries via Groq (chunked + 429 retries)

1. Create a free API key at [Groq Console](https://console.groq.com/keys).
2. Export **`GROQ_API_KEY`**.
3. Run with **`--summarize`** (the legacy alias **`--gemini`** still works).

```bash
export GROQ_API_KEY="your-key"
python scripts/fetch_security_news.py --days 7 --limit 25 --summarize --html output/index.html
```

**Free tier / rate limits:** requests are chunked and **paced adaptively** to stay under the provider's limits, plus **retries** that honor a `Retry-After`/`retryDelay` header (`--gemini-retries`, default **7**). `--gemini-chunk-size` defaults **per provider**: **3** for Groq (its 8K TPM only fits a few articles/request) and **12** for Gemini (its 250K TPM lets many items be **merged into one request** — far fewer requests, less RPD used). Pacing is TPM-aware, so bigger Gemini chunks stay under the cap; the token-bound floor (total tokens ÷ TPM) means Gemini (250K TPM) is ~8–10× faster than Groq (8K TPM) for the same articles. To go faster still, cut tokens with `--gemini-no-fetch-article` (RSS only) or a smaller `--gemini-max-article-chars`.

Default model is **`openai/gpt-oss-20b`** with a fallback **chain** **`openai/gpt-oss-120b,qwen/qwen3.8-27b`** (each Groq model has its own daily token budget, so the chain adds headroom). Override with **`--summary-model`** / **`--summary-model-fallback`** (comma-separated) to any Groq chat model id (`vendor/model`; see the [Groq model list](https://console.groq.com/docs/models)).

**Provider choice — Groq or Gemini.** Use **`--summary-provider gemini`** to summarize with **Google Gemini** ([AI Studio](https://aistudio.google.com)) instead of Groq; export **`GEMINI_API_KEY`**. Gemini's free tier is request-bound (not token-bound), so it defaults to **`gemini-3.1-flash-lite`** (15 RPM / 500 RPD, minimal-thinking) with fallback **`gemini-2.5-flash-lite`**, and paces by requests-per-minute. `--summary-provider groq` (default) uses Groq. `--summary-model` / `--summary-tpm` default to the selected provider's own defaults.

Tuning flags (historical `--gemini-*` names, provider-agnostic): **`--gemini-max-excerpt-chars`** (default 480), **`--gemini-max-output-tokens`**, **`--gemini-timeout`**, **`--gemini-chunk-size`** (use **0** for a single request containing every article—higher rate-limit risk on the free tier).

If Groq errors or returns unusable JSON, the script **falls back** to RSS + heuristics and still writes HTML/JSON. Cards that were not AI-summarized (no key, or a per-item Groq failure) are flagged with a **“Not summarized”** badge and their heading reads **“Description (RSS)”** instead of “Summary”, so it’s clear which items show the raw RSS text. The item’s `summarized` boolean is also written to the JSON output.

**GitHub Actions:** add a repository secret **`GROQ_API_KEY`** and/or **`GEMINI_API_KEY`**. The scheduled workflows summarize automatically when a key is set — **preferring Gemini when `GEMINI_API_KEY` is present**, else Groq; if neither is set, the build uses RSS + heuristics only (no failure).

### Browser Read aloud (`output/*.html`)

The standalone HTML builds add **Read news** / **Đọc tin** in the toolbar. The reader **speaks the summaries already baked into the page** (no in-browser LLM) and synthesizes audio with **Azure AI Speech** via its browser SDK (the CORS-safe way to call Azure TTS from a page):

| Step | Needs | Backend |
|------|-------|---------|
| Read-aloud text | Nothing — uses the on-page summaries | — |
| Text-to-speech audio | **Azure Speech resource key + region** ([create one](https://portal.azure.com/#create/Microsoft.CognitiveServicesSpeechServices)) | Azure Speech SDK (`window.SpeechSDK`) |

The Azure **key + region** are entered in-page once and saved in **`localStorage`** (persists across visits until the visitor edits or clears them). Azure's **F0 (free)** tier gives ~500K characters/month and needs no billing account. URL overrides: **`readerVoice`** / **`readerVoiceFallback`** ([Azure voice ids](https://learn.microsoft.com/azure/ai-services/speech-service/language-support?tabs=tts)) and **`readerRegion`**. Default voices: `en-US-AriaNeural` (security) and `vi-VN-HoaiMyNeural` (Vietnam).

Build-time flags (`fetch_security_news.py`, `fetch_vietnam_news.py`): **`--read-news-cloud-voice`**, **`--read-news-cloud-voice-fallback`**, **`--read-news-summary-model`**, **`--read-news-summary-fallback-model`**.

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

**Per-feed item count.** Add an optional `"max_items"` to any feed to cap how many of its items are read, overriding the global `--per-source` for that feed only:

```json
"nakedsecurity": {
  "title": "Naked Security (Sophos)",
  "feed_url": "https://nakedsecurity.sophos.com/feed/",
  "max_items": 3
}
```

**Excluding items by keyword.** Set a top-level `"exclude_keywords"` list in the config; any item whose **title or summary** contains one of the words (case-insensitive substring) is dropped before the per-feed cap is applied:

```json
{
  "exclude_keywords": ["sponsored", "webinar", "giveaway"],
  "feeds": { "…": {} }
}
```

You can also pass keywords per run with `--exclude-keywords sponsored,webinar` (comma-separated); CLI keywords are **merged with** the config list. Both scripts (`fetch_security_news.py`, `fetch_vietnam_news.py`) support `max_items` and `exclude_keywords`.

Many sites expose `/feed/`, `/rss`, or FeedBurner URLs. Prefer **official RSS/Atom** links; scraping HTML is out of scope for this script.

**SC Media / SC World:** scripted access to scworld.com is often blocked or not real RSS. If you get a stable feed URL, add it to the JSON like any other source.

## Vietnam news digest

[`scripts/fetch_vietnam_news.py`](scripts/fetch_vietnam_news.py) aggregates **Vietnamese press RSS** feeds (see [`config/vietnam_news_feeds.json`](config/vietnam_news_feeds.json)), optionally calls **Groq (an LLM)** to write a **Vietnamese summary** and assign one of a fixed set of **categories** (Thời sự, Kinh tế, Thế giới, …). Output: **HTML** and/or **JSON**; UI strings are Vietnamese.

**Ordering:** the digest follows the **feed sequence in [`config/vietnam_news_feeds.json`](config/vietnam_news_feeds.json)**, then **newest-first within each feed**. Because [tuổi trẻ.vn](https://tuoitre.vn/), [thanhnien.vn](https://thanhnien.vn/), and [dantri.com.vn](https://dantri.com.vn/) are listed first in the config, they lead the digest and Tuổi Trẻ items form the highlighted **Tin nổi bật** section. Reorder feeds in the config to change the display order. On a duplicate URL, the earlier feed in the config keeps the article.

### Setup

```bash
pip install -r requirements-vietnam-news.txt
```

### Local run

```bash
python scripts/fetch_vietnam_news.py --limit 15
GROQ_API_KEY=... python scripts/fetch_vietnam_news.py --summarize --days 2 --html output/vietnam/index.html
```

Without **`--summarize`**, the digest keeps the RSS blurb as summary and sets category **`Chưa phân loại`**. Such cards are flagged with a **“Chưa tóm tắt”** badge and a **“Mô tả (RSS)”** heading (vs. “Tóm tắt” for AI-summarized ones). Summary flags mirror the security script (`--summary-model`, `--gemini-chunk-size`, etc.).

### GitHub Actions (daily 5:00 Vietnam)

Workflow: [`.github/workflows/vietnam-news-daily.yml`](.github/workflows/vietnam-news-daily.yml).

- **Cron:** `0 22 * * *` **UTC** → **05:00** on the **next calendar day** in **Vietnam** (ICT, **UTC+7**). GitHub Actions cron is always UTC; [scheduled runs](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule) may slip slightly.
- **GitHub Pages:** builds **only** the Vietnam HTML (`vietnam/index.html`) into the `gh-pages` working copy (which already holds the security page), saves a dated snapshot under `archive/vietnam/`, then **deploys** the whole site to Pages. After the first run, open **Settings → Pages** (or the **`github-pages`** environment URL on the run) and use **`/vietnam/`** for the Vietnamese digest.
- **Artifact:** each run still uploads **`vietnam-news`** (zip with the Vietnam `index.html`) for offline download.

### Other scripts

- `scripts/fetch_vietnam_news.py` — Vietnam RSS digest with optional Groq summaries and categories ([Vietnam news digest](#vietnam-news-digest)).
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

Create the empty repository first in the GitHub UI (**New repository**), then run the commands above. Do not commit API keys; use **GitHub Actions secrets** (e.g. `GROQ_API_KEY`) for Groq summaries. See GitHub docs: **[Using secrets in Actions](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions)**.

## GitHub Pages (public URL)

The workflows [`.github/workflows/security-news-daily.yml`](.github/workflows/security-news-daily.yml) and [`.github/workflows/vietnam-news-daily.yml`](.github/workflows/vietnam-news-daily.yml) each build **only their own** digest — security → `index.html`, Vietnam → `vietnam/index.html` — to keep each run's Groq token usage separate.

**Site storage + history (`gh-pages` branch):** the published site lives on the **`gh-pages`** branch, which each run clones into `./site`, updates with its own page, and pushes back — so the branch is a persistent store that also gives full **history** (every deploy is a commit). Each run additionally saves a dated snapshot under **`archive/<digest>/YYYY-MM-DD.html`** and rebuilds **`archive/index.html`**, so past digests stay browsable at `…/archive/` (linked as **🕘 History** from each page). The whole `./site` tree is then deployed to Pages via the Actions artifact (Pages **Source: GitHub Actions** — no branch-source setting needed). `main` stays **source-only** (generated HTML is git-ignored). The two workflows share the `pages` concurrency group so runs never overlap.

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

The security digest is at the site root (**`index.html`**). The Vietnam digest is at **`vietnam/index.html`** (for a project repo `https://user.github.io/reports/`, open **`https://user.github.io/reports/vietnam/`** or **`.../vietnam/index.html`**).

After a successful run, open **Actions** → latest workflow → **deploy** job → **github-pages** environment link, or check **Settings → Pages** for the live URL.

**Private repositories:** GitHub Pages for private repos requires a **paid** plan in many setups; for a **free** public digest URL, use a **public** repository (or keep using workflow artifacts only).

### Schedule and workflow artifact

- **Schedule:** `01:00` UTC daily (`cron` in the workflow file); [scheduled runs](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule) can be delayed slightly on GitHub’s side.
- **Manual run:** **Actions** → **Security news digest** → **Run workflow**.
- **Artifact:** each run uploads **`security-news`** (zip with `index.html`) for offline download. The deployed site also includes **`vietnam/index.html`** when the Vietnam workflow (or this workflow’s Vietnam build step) has run.

Edit the workflow YAML to change `--days`, `--limit`, `--per-source`, or `cron`.

## Run daily on your own machine

Use **cron** (Linux/macOS) or **Task Scheduler** (Windows) to run the same `python scripts/fetch_security_news.py ... --html ...` command once a day inside your local venv.
