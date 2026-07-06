"""
Company Discovery Service
--------------------------
Given only a company name, this module auto-discovers:
  - Official website
  - Official GitHub organization (org-level, not a random repository)
  - Company logo
  - Short description
  - LinkedIn URL
  - Twitter/X URL

Confidence-based, multi-source, cross-verified pipeline. Priority order:
  1. Official Website        (Clearbit -> DuckDuckGo -> Wikidata)
  2. GitHub Organization      (multi-variant GitHub search -> user search ->
     slug guessing -> website cross-link -> search engine -> repo fallback)
  3. Website metadata         (meta description / OG tags / social links)
  4. Organization README/bio  (org "description"/"bio" + "blog" fields)
  5. Wikipedia                (summary + Wikidata official-website claim)
  6. LinkedIn                 (homepage scrape -> DuckDuckGo site: search)
  7. Public company directories (not implemented — no free/keyless API
     exists for Crunchbase/similar; documented limitation, see TODO.md)

No paid API keys required anywhere in this pipeline. Website discovery and
GitHub discovery run in parallel (ThreadPoolExecutor), and every GitHub
candidate-gathering strategy (org search variants, user search, slug
guessing, website cross-link, search engine) also runs concurrently and is
merged into ONE scoring pool — no strategy is gated behind another failing
first, which was the root cause of misses like "Hugging Face" (see
discover_github_org's docstring for the specific bug this fixes). A short
cross-verification pass then checks website and GitHub against each other
(e.g. does the homepage link to the discovered org, does the org's `blog`
field match the discovered domain) before anything is accepted. Every
accepted field gets a 0-100 confidence score; an org/repo is only accepted
if it passes a similarity/verification threshold — otherwise SpyLens
explains why rather than guessing.

Results are cached in the `company_discovery_cache` table (see database.py)
so repeated lookups for the same company name don't re-hit external APIs.
"""

import os
import re
import json
import time
import logging
import difflib
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from database import get_db

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

GH_TOKEN = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
UA = {"User-Agent": "Mozilla/5.0 (compatible; SpyLens-Discovery/1.0)"}

# ---------------------------------------------------------------------------
# TEMPORARY debug instrumentation for the GitHub discovery pipeline.
# Toggle with the DISCOVERY_DEBUG env var (default ON while we're
# diagnosing unreliable org discovery). Set DISCOVERY_DEBUG=0 to silence
# once things are confirmed working, or remove this block entirely later —
# nothing else in the module depends on it.
# ---------------------------------------------------------------------------
logger = logging.getLogger("spylens.discovery")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[DISCOVERY DEBUG] %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.DEBUG if os.environ.get("DISCOVERY_DEBUG", "1") != "0" else logging.INFO)
logger.propagate = False

if GH_TOKEN:
    logger.debug(f"GH_TOKEN loaded (len={len(GH_TOKEN)}, prefix={GH_TOKEN[:4]}***) — "
                 f"authenticated GitHub requests: 5000/hr core, 30/min search.")
else:
    logger.warning("No GH_TOKEN/GITHUB_TOKEN found in environment — GitHub API calls are "
                   "UNAUTHENTICATED (60 req/hr total, ~10 req/min for search). A single "
                   "discover_company() call can use 20-30+ GitHub API requests (search "
                   "variants + candidate verification + org-wide stats), so this limit can "
                   "be exhausted after only 1-2 lookups. This is a very likely cause of "
                   "'GitHub organization not found' results that are actually rate-limit "
                   "failures rather than genuine no-match cases. Set GH_TOKEN in .env to fix.")

# Domains that are never accepted as a company's "official website" even if
# they rank first in a search — they're the OTHER sources we look for.
NON_WEBSITE_DOMAINS = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "wikipedia.org",
    "en.wikipedia.org", "crunchbase.com", "g2.com", "instagram.com",
    "youtube.com", "github.com", "reddit.com", "medium.com", "duckduckgo.com",
    "capterra.com", "producthunt.com", "glassdoor.com", "indeed.com",
}

NO_WEBSITE_EXPLANATION = (
    "We could not confidently verify an official website after checking "
    "multiple public sources."
)
NO_GITHUB_ORG_EXPLANATION = (
    "We could not confidently verify an official GitHub organization after "
    "searching multiple public sources."
)


# ---------------------------------------------------------------------------
# Text normalization + similarity (stdlib only, no extra dependency)
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, alphanumeric-only — for comparing 'LangChain' vs 'langchain-ai' etc."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _similarity(a: str, b: str) -> float:
    """0-100 fuzzy similarity between two strings after normalization."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    if na in nb or nb in na:
        # Substring match (e.g. "langchain" in "langchainai") is a strong signal
        shorter, longer = sorted([na, nb], key=len)
        return 70.0 + 30.0 * (len(shorter) / len(longer))
    return difflib.SequenceMatcher(None, na, nb).ratio() * 100


def _domain_root(domain: str) -> str:
    """example.com -> example (drop TLD/subdomain for loose matching)."""
    if not domain:
        return ""
    parts = domain.lower().replace("www.", "").split(".")
    return parts[0] if parts else domain.lower()


# ---------------------------------------------------------------------------
# Cache helpers (unchanged mechanism — still one JSON blob per company name)
# ---------------------------------------------------------------------------

def _cache_get(name_lower: str):
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        "SELECT data, updated_at FROM company_discovery_cache WHERE name_lower = ?",
        (name_lower,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        data = json.loads(row["data"])
        if time.time() - data.get("_cached_at", 0) > CACHE_TTL_SECONDS:
            return None
        return data
    except Exception:
        return None


def _cache_set(name_lower: str, data: dict):
    payload = dict(data)
    payload["_cached_at"] = time.time()
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO company_discovery_cache (name_lower, data, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(name_lower) DO UPDATE SET
            data = excluded.data,
            updated_at = excluded.updated_at
    """, (name_lower, json.dumps(payload)))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Shared web helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, timeout=10):
    try:
        return requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    except Exception:
        return None


def _extract_meta(html: str):
    title, desc = "", ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']', html, re.I)
    if m:
        desc = m.group(1).strip()
    return title, desc


def _extract_social_links(html: str):
    linkedin, twitter, github_link = None, None, None
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
        h = href.lower()
        if not linkedin and "linkedin.com/company" in h:
            linkedin = href
        if not twitter and ("twitter.com/" in h or "x.com/" in h) and "share" not in h and "intent" not in h:
            twitter = href
        if not github_link and re.search(r"github\.com/[a-z0-9\-]+/?($|[\"'])", h):
            github_link = href
    return linkedin, twitter, github_link


def _duckduckgo_search(query: str, max_results: int = 5) -> list:
    """
    DuckDuckGo's HTML endpoint — free, keyless, no official API required.
    Used as a general-purpose "Google search results" stand-in per the
    discovery priority order. Returns a list of {title, url}. Fails
    silently (returns []) since it's a fallback layer, not a hard dependency.
    """
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query}, headers=UA, timeout=10,
        )
        if resp.status_code != 200:
            return []
        results = []
        for m in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            resp.text, re.I | re.S
        ):
            url = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            # DuckDuckGo's HTML endpoint wraps result URLs in a redirect;
            # unwrap the real target if present.
            uddg = re.search(r"uddg=([^&]+)", url)
            if uddg:
                from urllib.parse import unquote
                url = unquote(uddg.group(1))
            results.append({"title": title, "url": url})
            if len(results) >= max_results:
                break
        return results
    except Exception:
        return []


def _wikidata_official_website(name: str):
    """
    Wikidata is free/keyless and has a structured "official website" (P856)
    claim for most notable companies — a strong verification-independent
    source distinct from a search engine guess.
    """
    try:
        search_resp = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbsearchentities", "search": name, "language": "en", "format": "json", "limit": 3},
            headers=UA, timeout=10,
        )
        if search_resp.status_code != 200:
            return None, None
        candidates = search_resp.json().get("search", [])
        if not candidates:
            return None, None
        entity_id = candidates[0]["id"]
        wiki_label = candidates[0].get("label", "")

        claims_resp = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbgetclaims", "entity": entity_id, "property": "P856", "format": "json"},
            headers=UA, timeout=10,
        )
        if claims_resp.status_code != 200:
            return None, wiki_label
        claims = claims_resp.json().get("claims", {}).get("P856", [])
        if not claims:
            return None, wiki_label
        url = claims[0]["mainsnak"]["datavalue"]["value"]
        return url, wiki_label
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# 1. Official Website discovery (cascading, confidence-scored)
# ---------------------------------------------------------------------------

def _clearbit_suggest(name: str):
    try:
        r = requests.get(
            "https://autocomplete.clearbit.com/v1/companies/suggest",
            params={"query": name}, headers=UA, timeout=8
        )
        if r.status_code == 200:
            return r.json() or []
    except Exception:
        pass
    return []


def _pick_best_domain_match(name: str, suggestions: list):
    if not suggestions:
        return None, 0.0
    best, best_score = None, 0.0
    for s in suggestions:
        score = _similarity(name, s.get("name", ""))
        if score > best_score:
            best, best_score = s, score
    return best, best_score


def _verify_website_candidate(name: str, url: str):
    """
    Fetches a candidate homepage and scores how confident we are that it
    really belongs to `name`. Returns (verified, confidence, title, desc, html).
    """
    resp = _fetch(url)
    if resp is None or resp.status_code >= 400 or not resp.text:
        return False, 0.0, "", "", ""
    title, desc = _extract_meta(resp.text)
    name_score = max(_similarity(name, title), _similarity(name, desc))
    verified = name_score >= 40.0
    confidence = min(98.0, 50.0 + name_score * 0.5) if verified else name_score * 0.5
    return verified, confidence, title, desc, resp.text


def discover_website(name: str) -> dict:
    """
    Cascades: Clearbit -> DuckDuckGo -> Wikidata. Stops at the first
    candidate that verifies with reasonable confidence; otherwise returns
    the highest-confidence unverified candidate, or None with an honest
    explanation if nothing at all was found.
    """
    attempts = []  # for transparency/debugging: [(method, url_tried, result)]

    # --- Method 1: Clearbit Autocomplete ---
    suggestions = _clearbit_suggest(name)
    best, name_score = _pick_best_domain_match(name, suggestions)
    if best and best.get("domain"):
        domain = best["domain"]
        url = f"https://{domain}"
        verified, confidence, title, desc, html = _verify_website_candidate(name, url)
        attempts.append(("clearbit", url, verified))
        if verified or confidence >= 60:
            return {
                "url": url, "domain": domain, "confidence": round(confidence, 1),
                "method": "clearbit", "verified": verified, "title": title,
                "description": desc, "html": html,
                "logo_url": best.get("logo") or f"https://logo.clearbit.com/{domain}",
            }

    # --- Method 2: DuckDuckGo search ("Google search results" stand-in) ---
    results = _duckduckgo_search(f"{name} official website")
    for r in results:
        url = r.get("url", "")
        m = re.search(r"https?://(?:www\.)?([^/]+)", url)
        if not m:
            continue
        domain = m.group(1).lower()
        if any(bad in domain for bad in NON_WEBSITE_DOMAINS):
            continue
        verified, confidence, title, desc, html = _verify_website_candidate(name, url)
        attempts.append(("duckduckgo", url, verified))
        if verified:
            return {
                "url": url, "domain": domain, "confidence": round(min(confidence, 88.0), 1),
                "method": "duckduckgo_search", "verified": verified, "title": title,
                "description": desc, "html": html,
                "logo_url": f"https://logo.clearbit.com/{domain}",
            }

    # --- Method 3: Wikidata official-website claim ---
    wd_url, wiki_label = _wikidata_official_website(name)
    if wd_url:
        m = re.search(r"https?://(?:www\.)?([^/]+)", wd_url)
        domain = m.group(1).lower() if m else ""
        verified, confidence, title, desc, html = _verify_website_candidate(name, wd_url)
        attempts.append(("wikidata", wd_url, True))
        # Wikidata's structured claim is itself a strong signal even if our
        # own homepage-text check is inconclusive (thin homepages, JS apps).
        confidence = max(confidence, 75.0 if _similarity(name, wiki_label) >= 50 else 55.0)
        return {
            "url": wd_url, "domain": domain, "confidence": round(confidence, 1),
            "method": "wikidata", "verified": True, "title": title or wiki_label,
            "description": desc, "html": html,
            "logo_url": f"https://logo.clearbit.com/{domain}" if domain else None,
        }

    # --- Nothing found or nothing verified ---
    if best and best.get("domain"):
        # Clearbit had a guess but it never verified — return it as a very
        # low-confidence candidate rather than silently dropping it.
        domain = best["domain"]
        return {
            "url": f"https://{domain}", "domain": domain, "confidence": 25.0,
            "method": "clearbit_unverified", "verified": False, "title": "",
            "description": "", "html": "",
            "logo_url": best.get("logo") or f"https://logo.clearbit.com/{domain}",
        }

    return {
        "url": None, "domain": None, "confidence": 0.0, "method": None,
        "verified": False, "title": "", "description": "", "html": "",
        "logo_url": None, "reason": NO_WEBSITE_EXPLANATION,
    }


# ---------------------------------------------------------------------------
# 2. GitHub Organization discovery (org-first, repo fallback)
# ---------------------------------------------------------------------------

def _gh_headers():
    headers = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    return headers


def _gh_get(url, params=None, timeout=10):
    try:
        r = requests.get(url, headers=_gh_headers(), params=params, timeout=timeout)
    except Exception as e:
        logger.debug(f"GH GET {url} params={params} -> EXCEPTION: {e}")
        return None

    remaining = r.headers.get("X-RateLimit-Remaining")
    limit = r.headers.get("X-RateLimit-Limit")
    is_rate_limited = r.status_code == 403 and (
        remaining == "0" or "rate limit" in r.text.lower() or "secondary rate limit" in r.text.lower()
    )
    if is_rate_limited:
        logger.warning(f"GH GET {url} params={params} -> 403 RATE LIMITED "
                        f"(remaining={remaining}/{limit}). Falling back to non-API "
                        f"verification where possible.")
    else:
        logger.debug(f"GH GET {url} params={params} -> {r.status_code} "
                     f"(rate remaining={remaining}/{limit})")
    return r


def _github_search_orgs(name: str):
    """
    Runs several query variants concurrently and merges/dedupes results.

    The naive `"{name} in:login type:org"` query fails for multi-word
    company names whose login has no spaces (e.g. "Hugging Face" -> login
    "huggingface") because GitHub's `in:login` qualifier tokenizes the
    query against the login field only, and "Hugging" / "Face" as separate
    tokens don't match the concatenated login "huggingface". A quoted
    phrase search against the broader org profile (name/bio/login/email)
    without that restriction finds it correctly — verified live against
    the real API. We run both variants (plus a concatenated-login variant)
    so neither weakness silently loses a real match.
    """
    concat = re.sub(r"\s+", "", name.strip())
    queries = [
        f'{name} in:login type:org',
        f'"{name}" type:org',
    ]
    if concat.lower() != name.strip().lower():
        queries.append(f'{concat} in:login type:org')

    seen_logins = set()
    merged = []

    def _run(query):
        r = _gh_get("https://api.github.com/search/users", params={"q": query, "per_page": 8})
        if r and r.status_code == 200:
            items = r.json().get("items", [])
            logger.debug(f"org search variant '{query}' -> {len(items)} result(s): "
                         f"{[i.get('login') for i in items]}")
            return items
        logger.debug(f"org search variant '{query}' -> FAILED (status={r.status_code if r else 'no response'})")
        return []

    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        for items in pool.map(_run, queries):
            for item in items:
                login = item.get("login", "")
                if login and login not in seen_logins:
                    seen_logins.add(login)
                    merged.append(item)
    return merged


def _github_search_users(name: str):
    """
    Some companies (rarer, but happens) publish under a personal GitHub
    user account rather than a formal organization. Searched as a distinct,
    lower-weighted candidate pool per the discovery strategy.
    """
    r = _gh_get("https://api.github.com/search/users",
                params={"q": f'"{name}" type:user', "per_page": 5})
    if r and r.status_code == 200:
        return r.json().get("items", [])
    return []


def _github_search_repos_fallback(name: str):
    """
    Last-resort: search GitHub repositories directly by name and pick the
    best-matching, most-starred result. Used only when no organization or
    user account can be confidently identified — the discovery pipeline
    still needs *something* to analyze, and a well-known standalone repo
    (not owned by a dedicated org) is better than nothing, as long as it's
    clearly labeled as a repository-level fallback rather than an org.
    """
    r = _gh_get("https://api.github.com/search/repositories",
                params={"q": f'{name} in:name', "sort": "stars", "order": "desc", "per_page": 10})
    if not r or r.status_code != 200:
        return None
    repos = r.json().get("items", [])
    if not repos:
        return None
    best, best_score = None, 0.0
    for repo in repos:
        score = _similarity(name, repo.get("name", "")) * 0.6 + min(repo.get("stargazers_count", 0) / 1000, 30)
        if score > best_score:
            best, best_score = repo, score
    if not best:
        return None
    return {
        "full_name": best["full_name"],
        "score": min(best_score, 90.0),
        "description": best.get("description"),
        "owner_login": best.get("owner", {}).get("login"),
        "owner_avatar": best.get("owner", {}).get("avatar_url"),
    }


def _duckduckgo_github_search(name: str):
    """
    Agent-3-style strategy: search "<company> GitHub organization" /
    "<company> official GitHub" and pull a github.com/<login> URL out of
    the results as an additional candidate — independent of GitHub's own
    search ranking, which can occasionally miss smaller or newer orgs.
    """
    candidates = []
    for query in (f"{name} GitHub organization", f"{name} official GitHub"):
        for r in _duckduckgo_search(query, max_results=5):
            m = re.search(r"github\.com/([a-zA-Z0-9\-]+)/?(?:$|[?#])", r.get("url", ""))
            if m:
                login = m.group(1)
                if login.lower() not in ("orgs", "about", "features", "topics", "marketplace", "search"):
                    candidates.append(login)
    # de-dupe, preserve order
    seen = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]


def _github_org_details(login: str):
    r = _gh_get(f"https://api.github.com/orgs/{login}")
    if r and r.status_code == 200:
        data = r.json()
        data.setdefault("type", "Organization")
        return data
    return None


def _github_user_details(login: str):
    r = _gh_get(f"https://api.github.com/users/{login}")
    if r and r.status_code == 200:
        return r.json()
    return None


def _verify_github_login_via_html(login: str, name: str):
    """
    Fallback verification that does NOT touch api.github.com at all — it
    fetches the plain https://github.com/<login> profile page like a
    browser would. This exists specifically for when the GitHub REST API
    is rate-limited (very easy to hit unauthenticated: 60 req/hr total,
    and a single discover_company() call can burn 20-30+ requests on
    search + per-candidate detail lookups + org-wide stats). A rate-limited
    api.github.com/orgs/{login} call would otherwise silently make every
    candidate — even a correct one found via search-engine discovery —
    look like it "doesn't exist", when really we just couldn't check.

    Returns (exists, display_name_guess, avatar_url_guess) — best-effort,
    lower-confidence than a real API-verified match, but far better than
    discarding an otherwise-good candidate purely because the API quota
    ran out.
    """
    try:
        r = requests.get(f"https://github.com/{login}", headers=UA, timeout=8)
    except Exception as e:
        logger.debug(f"HTML fallback verification for '{login}' -> EXCEPTION: {e}")
        return False, "", None
    if r.status_code != 200:
        logger.debug(f"HTML fallback verification for '{login}' -> {r.status_code} (does not exist)")
        return False, "", None

    title_match = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    avatar_match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', r.text, re.I)
    avatar_url = avatar_match.group(1) if avatar_match else None
    logger.debug(f"HTML fallback verification for '{login}' -> 200 OK, title='{title}'")
    return True, title, avatar_url


def _guess_org_slugs(name: str) -> list:
    """Common slug patterns companies use for their GitHub org login."""
    norm = _normalize(name)
    words = re.findall(r"[a-z0-9]+", name.lower())
    guesses = [norm, "-".join(words), "".join(words)]
    if len(words) > 1:
        guesses.append(words[0])  # e.g. just "openai" from "OpenAI Inc"
    # de-dupe while preserving order
    seen = set()
    return [g for g in guesses if g and not (g in seen or seen.add(g))]


def discover_github_org(name: str, website_domain: str = None, website_html: str = "") -> dict:
    """
    Gathers candidates from EVERY strategy concurrently and always merges
    them into one scoring pool — this is the key fix over the previous
    version, which only tried slug-guessing / broader search as a fallback
    when the primary query returned literally zero results. That gating
    was the actual cause of misses like "Hugging Face": the primary query
    returned a few (wrong) candidates, so better strategies never got a
    chance to compete. Now every strategy always contributes candidates,
    and the single highest-scoring one wins:

      1. GitHub org search (multiple query variants, see _github_search_orgs)
      2. GitHub user search (companies publishing under a personal account)
      3. Direct slug guessing (openai, open-ai, "hugging"+"face" -> huggingface)
      4. A github.com/<org> link scraped from the company's own website
         (strongest independent signal — scored highest)
      5. DuckDuckGo "<company> GitHub organization" / "official GitHub"

    Only accepts a result above a similarity/verification threshold —
    otherwise falls back to the single best-matching repository (Agent-3
    strategy: search GitHub repositories directly), or gives up with a
    clear explanation. Never silently accepts a weak/unrelated match.

    NOTE ON RATE LIMITING (debugging round): live testing confirmed that
    the actual cause of "GitHub organization not found" results was very
    often NOT a scoring/threshold problem — it was api.github.com/orgs and
    /users detail-lookup calls silently failing due to unauthenticated
    rate limits (60 req/hr core API, easily exhausted by one discovery
    call's worth of candidate verifications), which made even a correctly
    *found* candidate look like it "doesn't exist". `_verify_github_login_via_html`
    below is the fix: a non-API fallback verification path.
    """
    domain_root = _domain_root(website_domain) if website_domain else ""

    website_github_org = None
    if website_html:
        m = re.search(r"github\.com/([a-zA-Z0-9\-]+)(?:/|\"|'|\s|$)", website_html)
        if m and m.group(1).lower() not in ("orgs", "about", "features", "topics", "marketplace"):
            website_github_org = m.group(1)

    # --- Gather every candidate login concurrently ---
    with ThreadPoolExecutor(max_workers=4) as pool:
        org_search_future = pool.submit(_github_search_orgs, name)
        user_search_future = pool.submit(_github_search_users, name)
        ddg_future = pool.submit(_duckduckgo_github_search, name)
        org_candidates = org_search_future.result()
        user_candidates = user_search_future.result()
        ddg_logins = ddg_future.result()

    candidate_logins = []  # (login, source_hint)
    if website_github_org:
        candidate_logins.append((website_github_org, "website_link"))
    for cand in org_candidates:
        candidate_logins.append((cand.get("login", ""), "org_search"))
    for cand in user_candidates:
        candidate_logins.append((cand.get("login", ""), "user_search"))
    for login in ddg_logins:
        candidate_logins.append((login, "search_engine"))
    for slug in _guess_org_slugs(name):
        candidate_logins.append((slug, "slug_guess"))

    # De-dupe while keeping the first (highest-priority) source hint for each login
    seen = {}
    for login, source in candidate_logins:
        if login and login not in seen:
            seen[login] = source
    candidate_logins = list(seen.items())
    logger.debug(f"discover_github_org('{name}'): {len(candidate_logins)} unique candidate(s) "
                 f"to evaluate: {candidate_logins}")

    # --- Fetch details + score every unique candidate concurrently ---
    api_lookup_failures = 0

    def _score_candidate(login, source):
        nonlocal api_lookup_failures
        details = _github_org_details(login)
        account_type = "Organization"
        verified_via = "api"
        if not details:
            details = _github_user_details(login)
            account_type = "User"
        if not details:
            # Both API lookups failed — could be a genuine 404, OR the API
            # being rate-limited (requirement #4/#8). Fall back to a plain
            # HTML page fetch, which doesn't touch api.github.com at all,
            # so it still works even when the REST API quota is exhausted.
            api_lookup_failures += 1
            exists, title_guess, avatar_guess = _verify_github_login_via_html(login, name)
            if not exists:
                logger.debug(f"candidate '{login}' (source={source}) -> REJECTED: not found via "
                             f"API or HTML fallback")
                return None
            details = {"name": title_guess, "blog": "", "bio": "", "description": None,
                       "avatar_url": avatar_guess, "public_repos": None}
            verified_via = "html_fallback"

        blog = (details.get("blog") or "").lower()
        bio = (details.get("bio") or "").lower()
        org_name = details.get("name") or login
        domain_match = bool(domain_root) and (domain_root in blog or domain_root in bio)
        name_score = _similarity(name, org_name)
        login_score = _similarity(name, login)
        base_score = max(name_score, login_score)

        if source == "website_link":
            score = 95.0
        elif domain_match:
            score = 92.0
        elif source == "slug_guess":
            score = base_score * 0.85
        elif source == "search_engine":
            score = base_score * 0.75  # search-engine-derived guesses verified only by name similarity
        elif source == "user_search":
            score = base_score * 0.8  # personal accounts are a weaker signal than a dedicated org
        else:
            score = base_score * 0.9

        if verified_via == "html_fallback":
            # Lower ceiling — we couldn't cross-check blog/bio/description
            # via the API, so this is a weaker verification than normal.
            score = min(score, 65.0)

        method = "domain_match" if domain_match else source
        logger.debug(f"candidate '{login}' (source={source}, verified_via={verified_via}) -> "
                     f"name_score={name_score:.1f} login_score={login_score:.1f} "
                     f"domain_match={domain_match} -> FINAL SCORE={score:.1f}")
        return (login, details, score, method, account_type)

    scored = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_score_candidate, login, source) for login, source in candidate_logins]
        for future in as_completed(futures):
            result = future.result()
            if result:
                scored.append(result)

    if api_lookup_failures:
        logger.debug(f"discover_github_org('{name}'): {api_lookup_failures} candidate(s) needed "
                     f"the HTML fallback (API lookup failed — check rate limit warnings above).")

    if scored:
        scored.sort(key=lambda x: x[2], reverse=True)
        logger.debug(f"discover_github_org('{name}'): ranked candidates -> "
                     + ", ".join(f"{login}={score:.1f}%" for login, _, score, _, _ in scored))
        login, details, score, method, account_type = scored[0]

        if score >= 45.0:
            logger.debug(f"discover_github_org('{name}'): ACCEPTED '{login}' "
                         f"(score={score:.1f}%, method={method}, type={account_type})")
            return {
                "org": login,
                "account_type": account_type,
                "confidence": round(min(score, 98.0), 1),
                "method": method,
                "verified": score >= 70.0,
                "description": details.get("description") or details.get("bio"),
                "avatar_url": details.get("avatar_url"),
                "blog": details.get("blog"),
                "public_repos": details.get("public_repos"),
            }
        logger.debug(f"discover_github_org('{name}'): top candidate '{login}' scored {score:.1f}% "
                     f"— below the 45% acceptance threshold, REJECTED.")

    # --- Nothing confidently matched an org/user — try a repository-level
    # fallback before giving up entirely (Agent-3 strategy #4: search repos) ---
    repo_fallback = _github_search_repos_fallback(name)
    if repo_fallback and repo_fallback["score"] >= 40.0:
        logger.debug(f"discover_github_org('{name}'): no org/user matched; accepting repository "
                     f"fallback '{repo_fallback['full_name']}' (score={repo_fallback['score']:.1f}%)")
        return {
            "org": None,
            "account_type": None,
            "confidence": round(repo_fallback["score"], 1),
            "method": "repository_fallback",
            "verified": False,
            "description": repo_fallback.get("description"),
            "avatar_url": repo_fallback.get("owner_avatar"),
            "blog": None,
            "public_repos": None,
            "fallback_repo": repo_fallback["full_name"],
        }

    best_weak = max(scored, key=lambda x: x[2]) if scored else None
    reason = NO_GITHUB_ORG_EXPLANATION
    if best_weak:
        reason += f" (best candidate '{best_weak[0]}' scored only {best_weak[2]:.0f}% confidence.)"
    elif api_lookup_failures:
        reason += " (GitHub API calls failed for every candidate — this may be rate-limiting; see debug log.)"
    logger.debug(f"discover_github_org('{name}'): FINAL RESULT = no org/user/repo found. {reason}")
    return {
        "org": None, "account_type": None, "confidence": 0.0, "method": None, "verified": False,
        "description": None, "avatar_url": None, "reason": reason,
    }


# ---------------------------------------------------------------------------
# Org-level GitHub statistics (total repos/stars/forks, top repos, most
# active repo, aggregated languages, contributor & release activity)
# ---------------------------------------------------------------------------

def _org_repos(org: str, per_page: int = 100, account_type: str = "Organization") -> list:
    base = "orgs" if account_type == "Organization" else "users"
    r = _gh_get(f"https://api.github.com/{base}/{org}/repos",
                params={"per_page": per_page, "type": "public" if account_type == "Organization" else "owner",
                        "sort": "updated"})
    if r and r.status_code == 200:
        return r.json() or []
    return []


def _repo_languages(full_name: str) -> dict:
    r = _gh_get(f"https://api.github.com/repos/{full_name}/languages")
    if r and r.status_code == 200:
        return r.json() or {}
    return {}


def _repo_contributor_count(full_name: str) -> int:
    r = _gh_get(f"https://api.github.com/repos/{full_name}/contributors", params={"per_page": 100, "anon": "false"})
    if r and r.status_code == 200:
        return len(r.json() or [])
    return 0


def _repo_latest_release(full_name: str):
    r = _gh_get(f"https://api.github.com/repos/{full_name}/releases", params={"per_page": 1})
    if r and r.status_code == 200:
        releases = r.json() or []
        if releases:
            return releases[0].get("tag_name"), releases[0].get("published_at")
    return None, None


def collect_org_stats(org: str, account_type: str = "Organization") -> dict:
    """
    Org-wide (or user-wide, if the company publishes under a personal
    account) GitHub intelligence used to decide the "flagship" repo and to
    give a fuller picture than a single repository ever could. Deep-dive
    calls (languages/contributors/releases) are limited to the top 5
    starred repos and run in parallel to keep this fast (this module's
    "Performance" requirement) and to bound API usage.
    """
    repos = _org_repos(org, account_type=account_type)
    if not repos:
        return {
            "total_repos": 0, "total_stars": 0, "total_forks": 0,
            "top_repositories": [], "most_active_repository": None,
            "languages_used": {}, "contributor_activity": None,
            "release_activity": None,
        }

    non_fork = [r for r in repos if not r.get("fork") and not r.get("archived")]
    total_stars = sum(r.get("stargazers_count", 0) for r in repos)
    total_forks = sum(r.get("forks_count", 0) for r in repos)

    by_stars = sorted(non_fork, key=lambda r: r.get("stargazers_count", 0), reverse=True)
    top5 = by_stars[:5]
    top_repositories = [
        {"full_name": r["full_name"], "stars": r.get("stargazers_count", 0),
         "forks": r.get("forks_count", 0), "url": r.get("html_url")}
        for r in top5
    ]

    by_pushed = sorted(non_fork, key=lambda r: r.get("pushed_at", ""), reverse=True)
    most_active = by_pushed[0] if by_pushed else None
    most_active_repository = (
        {"full_name": most_active["full_name"], "pushed_at": most_active.get("pushed_at"),
         "stars": most_active.get("stargazers_count", 0)}
        if most_active else None
    )

    # Parallel deep-dive on the top 5 repos: languages, contributors, releases.
    languages_agg = {}
    contributor_total = 0
    release_count = 0
    latest_release_date = None

    with ThreadPoolExecutor(max_workers=8) as pool:
        lang_futures = {pool.submit(_repo_languages, r["full_name"]): r["full_name"] for r in top5}
        contrib_futures = {pool.submit(_repo_contributor_count, r["full_name"]): r["full_name"] for r in top5}
        release_futures = {pool.submit(_repo_latest_release, r["full_name"]): r["full_name"] for r in top5}

        for future in as_completed(lang_futures):
            for lang, bytes_ in (future.result() or {}).items():
                languages_agg[lang] = languages_agg.get(lang, 0) + bytes_
        for future in as_completed(contrib_futures):
            contributor_total += future.result() or 0
        for future in as_completed(release_futures):
            tag, published = future.result()
            if tag:
                release_count += 1
                if published and (latest_release_date is None or published > latest_release_date):
                    latest_release_date = published

    total_lang_bytes = sum(languages_agg.values()) or 1
    languages_used = {
        lang: round(b * 100 / total_lang_bytes, 1)
        for lang, b in sorted(languages_agg.items(), key=lambda x: x[1], reverse=True)
    }

    return {
        "total_repos": len(repos),
        "total_stars": total_stars,
        "total_forks": total_forks,
        "top_repositories": top_repositories,
        "most_active_repository": most_active_repository,
        "languages_used": languages_used,
        "contributor_activity": {"contributors_across_top_repos": contributor_total},
        "release_activity": {"releases_seen_in_top_repos": release_count, "latest_release_date": latest_release_date},
        "flagship_repo": top_repositories[0]["full_name"] if top_repositories else None,
    }


# ---------------------------------------------------------------------------
# Cross-verification between website and GitHub org
# ---------------------------------------------------------------------------

def _cross_verify(website: dict, github: dict):
    """
    Boosts confidence when the two independently-discovered sources agree
    (website links to the org, or org's blog field matches the domain).
    Mutates and returns the two dicts.
    """
    if not website.get("url") or not github.get("org"):
        return website, github

    domain_root = _domain_root(website.get("domain"))
    blog = (github.get("blog") or "").lower()
    agree = bool(domain_root) and domain_root in blog

    if not agree and website.get("html"):
        agree = bool(re.search(rf"github\.com/{re.escape(github['org'])}(?:/|\"|'|\s|$)", website["html"], re.I))

    if agree:
        website["confidence"] = round(min(98.0, website.get("confidence", 0) + 10), 1)
        github["confidence"] = round(min(98.0, github.get("confidence", 0) + 10), 1)
        website["cross_verified"] = True
        github["cross_verified"] = True
    return website, github


# ---------------------------------------------------------------------------
# LinkedIn fallback search (used when the homepage scrape finds nothing)
# ---------------------------------------------------------------------------

def _linkedin_fallback_search(name: str):
    results = _duckduckgo_search(f"site:linkedin.com/company {name}")
    for r in results:
        if "linkedin.com/company" in r.get("url", "").lower():
            return r["url"]
    return None


# ---------------------------------------------------------------------------
# Confidence aggregation
# ---------------------------------------------------------------------------

def _compute_overall_confidence(confidence: dict) -> float:
    """
    Weighted average across whichever fields were actually found — website
    and GitHub org matter most since everything else derives from them.
    """
    weights = {"website": 0.30, "github_org": 0.30, "logo": 0.10, "description": 0.15, "linkedin": 0.15}
    total_weight, total_score = 0.0, 0.0
    for key, weight in weights.items():
        score = confidence.get(key)
        if score is not None:
            total_score += score * weight
            total_weight += weight
    if total_weight == 0:
        return 0.0
    return round(total_score / total_weight, 1)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def discover_company(name: str) -> dict:
    """
    Given a company name, auto-discovers website, GitHub organization (with
    org-wide stats), logo, description, LinkedIn and Twitter/X — each with
    its own confidence score, plus an overall discovery confidence. Cached
    for CACHE_TTL_SECONDS.

    Backward-compatible keys (name, website_url, github_org, github_repo,
    logo_url, description, linkedin_url, twitter_url, verified_website,
    verified_github) are preserved so existing callers (app.py, the
    intelligence pipeline) keep working unchanged. `github_repo` is now the
    org's flagship (most-starred) repository rather than whichever repo
    happened to be pushed to most recently.
    """
    name = (name or "").strip()
    name_lower = name.lower()

    cached = _cache_get(name_lower)
    if cached:
        logger.debug(f"discover_company('{name}'): CACHE HIT — returning cached result "
                     f"(github_org={cached.get('github_org')}, github_repo={cached.get('github_repo')})")
        return cached
    logger.debug(f"discover_company('{name}'): cache miss, running full discovery pipeline")

    # --- Run website discovery and an initial (pre-cross-verification)
    # GitHub org discovery in parallel, per the Performance requirement. ---
    with ThreadPoolExecutor(max_workers=2) as pool:
        website_future = pool.submit(discover_website, name)
        github_future = pool.submit(discover_github_org, name, None, "")
        website = website_future.result()
        github = github_future.result()

    # If the parallel GitHub pass found nothing/low-confidence, retry once
    # now that we know the website domain and HTML — a website->org link or
    # domain/blog match often succeeds where a name-only search didn't.
    if not github.get("org") and (website.get("domain") or website.get("html")):
        github = discover_github_org(name, website.get("domain"), website.get("html", ""))

    website, github = _cross_verify(website, github)

    # --- Social links: prefer homepage scrape, fall back to search ---
    linkedin_url, twitter_url = None, None
    if website.get("html"):
        linkedin_url, twitter_url, _ = _extract_social_links(website["html"])
    linkedin_confidence = 90.0 if linkedin_url else None
    if not linkedin_url:
        linkedin_url = _linkedin_fallback_search(name)
        linkedin_confidence = 55.0 if linkedin_url else None

    # --- Description: homepage meta > org description > None ---
    description = website.get("description") or github.get("description")
    description_confidence = None
    if website.get("description"):
        description_confidence = website.get("confidence")
    elif github.get("description"):
        description_confidence = 70.0

    # --- Logo: website-derived Clearbit logo, fallback to GitHub org avatar ---
    logo_url = website.get("logo_url") or github.get("avatar_url")
    logo_confidence = 90.0 if website.get("logo_url") else (75.0 if github.get("avatar_url") else None)

    # --- Org-level GitHub stats + flagship repo selection ---
    org_stats = None
    github_repo = None
    if github.get("org"):
        org_stats = collect_org_stats(github["org"], account_type=github.get("account_type", "Organization"))
        github_repo = org_stats.get("flagship_repo")
    elif github.get("fallback_repo"):
        # No confident org/user found, but a well-matching standalone
        # repository was — track that directly (Agent-3 repository fallback).
        github_repo = github["fallback_repo"]

    confidence = {
        "website": website.get("confidence") if website.get("url") else None,
        "github_org": github.get("confidence") if github.get("org") else None,
        "logo": logo_confidence,
        "description": description_confidence,
        "linkedin": linkedin_confidence,
    }
    overall_confidence = _compute_overall_confidence(confidence)

    result = {
        "name": name,
        "website_url": website.get("url"),
        "github_org": github.get("org"),
        "github_repo": github_repo,
        "logo_url": logo_url,
        "description": description,
        "linkedin_url": linkedin_url,
        "twitter_url": twitter_url,
        "verified_website": bool(website.get("verified")),
        "verified_github": bool(github.get("verified")),
        "confidence": {
            "website": confidence["website"],
            "github_organization": confidence["github_org"],
            "logo": confidence["logo"],
            "linkedin": confidence["linkedin"],
            "description": confidence["description"],
            "overall": overall_confidence,
        },
        "org_stats": org_stats,
        "discovery_notes": {
            "website_method": website.get("method"),
            "github_method": github.get("method"),
            "website_reason": website.get("reason"),
            "github_reason": github.get("reason"),
        },
    }

    _cache_set(name_lower, result)
    logger.debug(
        f"discover_company('{name}'): FINAL RESULT — "
        f"website_url={result['website_url']!r} (confidence={confidence['website']}), "
        f"github_org={result['github_org']!r} (confidence={confidence['github_org']}), "
        f"github_repo={result['github_repo']!r}, overall_confidence={overall_confidence}"
    )
    if not result["github_org"] and not result["github_repo"]:
        logger.warning(
            f"discover_company('{name}'): NO GitHub organization or repository found. "
            f"Reason: {github.get('reason')}"
        )
    return result