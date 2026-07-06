# CHANGELOG — Intelligence Pipeline (GitHub + Website + AI + PDF)

*(Continues from the Company Discovery feature — see the entry below this one.)*

## What was added

### `agent/github_intelligence.py` (new)
`collect_github_intelligence(competitor)` — given the discovered
`github_org`/`github_repo`, collects:
- Stars, forks, watchers, open issues (`GET /repos/{repo}`)
- Language breakdown as % (`GET /repos/{repo}/languages`)
- Inferred **frameworks** — GitHub has no direct "framework" field, so this
  reads the repo's `topics` against a known list (react, django, langchain,
  etc.), falling back to a language-based guess if no topics match
- Top 10 **contributors** (`GET /repos/{repo}/contributors`)
- **Commit activity** — tries `GET /repos/{repo}/stats/commit_activity`
  (52-week histogram + a 4-week trend %); if GitHub hasn't cached those
  stats yet (202 response), falls back to the **existing**
  `github_tracker.get_recent_commits()` — reused, not reimplemented
- **Release frequency** — total releases seen + average days between them
- **Repository growth** — GitHub's API has no historical star-count
  endpoint (that requires a paid service), so this is a documented proxy:
  stars-per-day-since-creation, plus the org's public-repo count by
  creation year
- **Most active repository** — cross-confirms the org's most recently
  pushed public/non-fork repo (discovery already picks this as the tracked
  repo; this re-labels it for the report rather than re-deriving it)
- AI narrative summary via Groq

### `agent/website_intelligence.py` (new)
`collect_website_intelligence(competitor)` — given the discovered
`website_url`:
- **Homepage** — reuses the existing `website_tracker.get_website_snapshot()`
- **Products / Features / Pricing / Blog / Documentation / Careers** —
  probes common subpage paths (`/pricing`, `/blog`, `/careers`, etc.),
  records found/not-found + a text snippet per page
- **Tech stack** — no paid Wappalyzer-style API; a ~20-signature regex/header
  fingerprint (React, Next.js, WordPress, Shopify, Tailwind, GA/GTM, Stripe,
  Cloudflare, Vercel, Netlify, etc.) scanned against the homepage HTML +
  response headers
- **Latest updates** — best-effort headline scrape from the blog page if
  one was found
- **Meta information** — title, meta/OG description, OG image, canonical URL
- AI narrative summary via Groq

### `agent/competitive_intelligence.py` (new)
`generate_competitive_intelligence(name, github_intel, website_intel)` —
one Groq call combining both bundles, instructed to return **strict JSON**
(with a fallback brace-extraction parser for when the model adds stray
text) containing: executive summary, technology stack, business overview,
product direction, developer activity, SWOT (strengths/weaknesses/
opportunities/threats), hiring trends, innovation score (0-100 + reasoning),
overall rating (0-100 + reasoning), future predictions, and recommendations.

### `agent/pdf_report.py` (new)
`generate_pdf_report(competitor, github_intel, website_intel, ai_intel)` —
builds a multi-page A4 PDF with reportlab (Platypus) + matplotlib charts:
cover page (with fetched company logo), executive summary with score
gauges, website analysis (table of found/not-found subpages), GitHub
analysis (bar chart of stars/forks/watchers, language pie chart, metrics
table, org-growth-by-year chart), technology stack, a 2x2 colored SWOT
grid, AI insights/recommendations/opportunities/predictions, and a
conclusion page. Saved to `generated_reports/` and served via Flask's
`send_file` — downloadable, not just displayed.

### `agent/intelligence_runner.py` (new)
Orchestrates the 7 stages end-to-end and writes progress after each one:
`Finding Company...` → `Finding Website...` → `Finding GitHub...` →
`Analyzing Website...` → `Analyzing GitHub...` → `Generating AI Insights...`
→ `Creating PDF...` → `Complete`. If discovery somehow missed the website
or GitHub org at add-time, this re-runs `discover_company()` as a backfill
before analyzing (reuses the existing Discovery Service — no duplicate logic).

### `database.py`
Two new tables, added via the same safe migration pattern as before:
- `analysis_status` — one row per competitor, polled by the frontend for
  live stage text
- `intelligence_reports` — latest github/website/AI JSON bundle + PDF path
  per competitor (overwritten on each new "Full Analysis" run — see TODO
  for report history as a possible follow-up)

### `app.py`
Four new routes:
- `POST /analyze/<id>` — starts the pipeline in a background `threading.Thread`
  (same lightweight pattern APScheduler already uses in this codebase) and
  returns immediately; guards against double-starting if one's already running
- `GET /analysis_status/<id>` — polled by the frontend every 1.2s
- `GET /analysis_result/<id>` — full JSON bundle once done
- `GET /download_report/<id>` — serves the generated PDF as an attachment

`agent/runner.py`, `github_tracker.py`, `website_tracker.py`,
`summarizer.py` — **untouched**, all reused as-is.

### `templates/index.html`
- New **Full Analysis** button per competitor card, next to the existing
  Scan Now / Delete buttons
- Live staged loading text (pulsing dot + the exact stage strings from the
  spec) while polling
- Results render inline: executive summary, Overall Rating + Innovation
  Score chips, business overview/product direction/developer activity,
  GitHub stat chips, technology chips (languages/frameworks/web tech), a
  2x2 colored SWOT grid, hiring trends, recommendations, future
  predictions, and a **Download Full PDF Report** link

### `requirements.txt`
Added `reportlab` and `matplotlib` (PDF + chart generation).

## Testing performed
- Ran `run_full_analysis()` directly against a fake competitor with all
  GitHub/website/Groq calls mocked — confirmed the full 7-stage bundle,
  including a real 9-page, 68KB generated PDF (verified page count and
  section headers via `pypdf`).
- Ran all four new Flask routes (`/analyze`, `/analysis_status`,
  `/analysis_result`, `/download_report`) through Flask's test client with
  a synchronous thread stub — confirmed correct status transitions, JSON
  shape, and a real `application/pdf` download response.
- Re-verified the schema migration against the actual `spylens.db` — all 3
  existing competitor rows survived, two new tables (`analysis_status`,
  `intelligence_reports`) added cleanly alongside the earlier
  `company_discovery_cache` table.
- Confirmed the home page renders the new "Full Analysis" button and JS
  once at least one competitor exists (the button lives inside the
  competitor-card loop, so an empty tracker correctly shows nothing yet —
  this is expected, not a bug).

**Note on this sandbox:** `api.groq.com` is not in this sandbox's network
allowlist (same restriction noted for `clearbit.com` in the previous
CHANGELOG entry), so the AI-summary calls returned "Groq API error: 403"
during my local testing here — that's this sandbox blocking the domain,
not a code issue. It will call Groq normally on Render/your machine, where
`.env`'s `GROQ_API_KEY` is already configured and reachable.

---

# CHANGELOG — Company Discovery Feature

## Goal
Removed the "Website URL" and "GitHub Repo" fields from the Add Competitor form.
Users now only enter a **Company Name**; SpyLens auto-discovers everything else.

## What changed

### New file: `agent/discovery.py`
Company Discovery Service. Given a company name only, it:
- Looks up the official domain + logo via the free, keyless **Clearbit Autocomplete API**.
- Fetches the homepage itself to pull the meta description, title, and scrapes
  footer/nav links for LinkedIn (`linkedin.com/company/...`) and Twitter/X links.
- Verifies the website by checking the company name actually appears in the
  page's title/description (not just assumed from the domain match).
- Searches GitHub's Search API (keyless) for candidate organizations, and
  **verifies** the org by checking whether its public `blog` field contains
  the discovered domain (falls back to exact display-name match if not).
- Once a verified org is found, auto-picks that org's most recently active,
  non-fork, non-archived public repo as the `owner/repo` to hand to the
  existing commit tracker (`agent/github_tracker.py`) — so that module needed
  **no changes**.
- Caches results in `company_discovery_cache` (7-day TTL) so re-adding the
  same company later doesn't re-hit external services.

### `database.py`
- Added `company_discovery_cache` table (`name_lower`, `data` JSON blob, `updated_at`).
- Added migration logic: on startup, checks `PRAGMA table_info(competitors)`
  and `ALTER TABLE`s in any missing columns so your **existing `spylens.db`
  is upgraded in place, no data loss** — tested against your real DB file.
- New `competitors` columns: `github_org`, `logo_url`, `description`,
  `linkedin_url`, `twitter_url`, `verified_website`, `verified_github`.

### `app.py`
- `/add_competitor` now only reads `name` from the form. It calls
  `discover_company(name)` and stores all discovered fields.
- Added `/api/discover` (POST) — returns the discovery JSON for a name
  without saving it, in case you want a "preview before adding" UI later.
- `agent/runner.py`, `agent/github_tracker.py`, `agent/website_tracker.py`,
  `agent/summarizer.py` — **untouched**. They already consumed `website_url`
  / `github_repo` from the competitor row; those fields are just populated
  automatically now instead of by hand.

### `templates/index.html`
- Add-competitor form reduced to **Company Name** + **Analyze** button.
- Competitor cards now show: discovered logo, one-line description,
  ✓ verified / ? unverified badges next to the website and GitHub links,
  and LinkedIn/Twitter links when found.
- Added a small JS handler that disables the button and shows a
  "discovering…" message on submit, since discovery can take a few seconds
  (Clearbit + homepage fetch + GitHub search + repo lookup, done synchronously).

## Testing performed (see TODO.md for what's left)
- Verified schema migration against your actual `spylens.db` — 3 existing
  rows preserved, new columns added cleanly.
- Ran the discovery pipeline end-to-end (Clearbit mocked, since this sandbox's
  network egress blocks `clearbit.com`; GitHub API calls were real) —
  correctly resolved `OpenAI` → `openai.com`, org `openai`, repo `openai/codex`,
  both verified.
- Confirmed the cache prevents duplicate external calls on repeat lookups.
- Confirmed `/add_competitor` → renders the competitor card with all
  discovered fields and badges.
- Confirmed `agent/runner.py` (report generation) runs unmodified against
  discovery-populated fields.

**Note:** Clearbit's API is reachable from anywhere with normal internet
access (it's what will run on Render), it's only blocked in this sandbox's
restricted egress list — so re-test discovery once deployed to confirm live
results, particularly for less common company names.
