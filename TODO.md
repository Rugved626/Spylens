# TODO / Follow-ups

## Must-check before you deploy (Intelligence Pipeline)
- [ ] `api.groq.com` was unreachable from this sandbox (network allowlist),
      so AI summaries/insights/PDF text were tested with Groq mocked.
      Please run a real "Full Analysis" on Render/your machine to confirm
      the actual Groq output quality and JSON-parsing reliability on a few
      real companies.
- [ ] The GitHub REST API is rate-limited to 60 req/hr **unauthenticated**.
      One "Full Analysis" run makes ~6-8 GitHub calls (repo, languages,
      contributors, commit activity, releases, org repos x2). That's fine
      for occasional use, but if you're analyzing many competitors back to
      back, set `GH_TOKEN` (or `GITHUB_TOKEN`) in `.env` — both
      `discovery.py` and `github_intelligence.py` already read it
      automatically, no code changes needed.
- [ ] `stats/commit_activity` sometimes returns `202` (still computing) on
      a repo's *first-ever* request to that endpoint. The code falls back
      to the 7-day tracker automatically, but if the first "Full Analysis"
      of a brand-new repo looks sparse on commit activity, running it again
      a minute later usually gets the real yearly stats.
- [ ] `generated_reports/` is created next to `app.py` and grows over time
      (PDFs aren't deleted). Add it to `.gitignore` and consider a cleanup
      cron or size cap if disk space matters on your Render plan.

## Known limitations (by design, no paid APIs used)
- **Repository Growth** has no true historical curve — GitHub's API doesn't
  expose past star counts (that needs a paid service or a scraper like
  star-history.com). The report uses a documented proxy instead
  (stars-per-day-since-creation + repos-created-by-year) and labels it as
  such in both the JSON and the PDF.
- **Frameworks** are inferred from GitHub repo `topics`, not detected by
  parsing actual code/config files (e.g. `package.json`, `requirements.txt`).
  Maintainers who don't tag topics well will get a weaker/fallback guess.
- **Website tech-stack fingerprinting** is a ~20-signature regex/header
  check (React, WordPress, Shopify, Tailwind, etc.), not a full Wappalyzer
  database. Less common tools won't be detected.
- **Latest Updates** (blog headline scrape) is best-effort regex over
  `<h1>/<h2>/<h3>/<a>` tags — blogs with heavily JS-rendered content (client-side
  React blogs with no server-rendered HTML) may return nothing.
- **Report history**: `intelligence_reports` stores only the *latest* run
  per competitor (overwritten each time). If you want to compare "Full
  Analysis" results over time (e.g. track rising star count month to
  month), that needs a history table instead of an upsert — not built,
  since it wasn't asked for, but straightforward to add if useful.
- The pipeline runs in a plain Python `threading.Thread`. This works fine
  under Flask's dev server and under gunicorn with a **sync worker** (the
  default). If you ever switch to gunicorn's `gevent`/`eventlet` workers,
  double-check thread safety of the sqlite3 connections (each function
  already opens/closes its own connection, so it should be fine, but it's
  untested under those worker classes).

## Nice-to-haves (not required by the original ask, not built)
- [ ] A "Re-run Analysis" vs "View Last Analysis" distinction — right now
      clicking Full Analysis again always re-runs everything from scratch.
- [ ] Store the raw AI JSON response even when parsing fails (currently
      only the first 2000 chars are kept, in `ai_data.raw_response`) for
      easier debugging of Groq prompt issues.
- [ ] Embed the same charts used in the PDF into the inline HTML results
      (currently the HTML shows chips/lists; charts are PDF-only).
- [ ] Cross-competitor comparison view (put 2+ competitors' Overall
      Ratings/SWOTs side by side) — natural next step once report history
      exists.


## Must-check before you deploy
- [ ] Regenerate your **Groq** and any old **OpenRouter** keys if you haven't
      already (flagged in an earlier session) — unrelated to this change but
      still open.
- [ ] Re-test `discover_company()` on Render (or any environment with normal
      internet access) since this sandbox blocks `clearbit.com` in its egress
      allowlist. The GitHub Search API calls were verified live and work.
- [ ] Try a handful of real competitor names (e.g. "Notion", "Razorpay",
      "Zerodha") to see how well Clearbit + GitHub org matching perform on
      Indian/smaller startups — Clearbit's dataset skews toward larger/known
      companies, so obscure names may come back with `website_url: null`.

## Known limitations of the Discovery Service (by design, no paid APIs used)
- **LinkedIn/Twitter discovery** relies on scraping the homepage's HTML for
  `linkedin.com/company/...` and `twitter.com/` or `x.com/` links. If a
  company doesn't link socials in their homepage footer/nav, these will come
  back `null`. A paid enrichment API (Clearbit Enrichment, Proxycurl, etc.)
  would be more reliable but needs an API key + budget.
- **GitHub org verification** trusts the org's public `blog` field matching
  the discovered domain. Some orgs leave `blog` blank even when legitimate —
  in that case discovery falls back to an unverified "best guess" (the first
  search result), and the UI shows a `? unverified` badge so you can spot it.
- Discovery runs **synchronously** inside `/add_competitor` (matching the
  existing form-POST pattern). For company names where all 3 external calls
  are slow, adding a competitor could take 10-15 seconds. If this becomes
  annoying, consider:
  - Making `/add_competitor` an AJAX call (like `/scan/<id>` already is) with
    a spinner, instead of a full-page form POST, or
  - Running discovery in a background thread and showing "Discovering…" on
    the card until it's done.

## Nice-to-haves (not required by the original ask, not built)
- [ ] A "re-discover" button per competitor card, to refresh a company's info
      without deleting and re-adding it (the discovery cache TTL is 7 days,
      so this would force a fresh lookup).
- [ ] Use the new `/api/discover` preview endpoint to show a confirmation
      card ("Is this OpenAI? ✓ website ✓ github") before actually saving,
      instead of committing to the DB immediately on Analyze.
- [ ] Manual override fields (edit website/github) for the rare case
      discovery gets it wrong — currently the only fix is delete + re-add
      with a more specific name.
