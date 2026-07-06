"""
Website Intelligence
---------------------
Given a competitor's discovered website, collects: homepage snapshot,
detected product/features/pricing/blog/docs/careers subpages, a lightweight
tech-stack fingerprint (no paid Wappalyzer-style API — regex/header
based), latest blog updates (best-effort), and full meta information.

Reuses `agent.website_tracker.get_website_snapshot` for the homepage instead
of re-implementing homepage fetching/parsing.
"""

import re
import requests
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin


from agent.website_tracker import get_website_snapshot
from agent.summarizer import GROQ_API_KEY

UA = {"User-Agent": "Mozilla/5.0 (compatible; SpyLens-Intelligence/1.0)"}

SUBPAGE_CANDIDATES = {
    "products": ["/products", "/product"],
    "features": ["/features"],
    "pricing": ["/pricing", "/plans"],
    "blog": ["/blog", "/news"],
    "documentation": ["/docs", "/documentation", "/developers"],
    "careers": ["/careers", "/jobs"],
}

TECH_SIGNATURES = {
    "React": ["react.development.js", "react-dom", "data-reactroot", "__reactContainer"],
    "Next.js": ["_next/static", "__next_data__", "__NEXT_DATA__"],
    "Vue.js": ["vue.js", "__vue__", "data-v-app"],
    "Angular": ["ng-version", "angular.js"],
    "Svelte": ["svelte-"],
    "WordPress": ["wp-content", "wp-includes", "wp-json"],
    "Shopify": ["cdn.shopify.com", "shopify.theme", "myshopify.com"],
    "Webflow": ["webflow.com", "data-wf-site", "data-wf-page"],
    "Squarespace": ["squarespace.com", "static1.squarespace.com"],
    "Wix": ["wix.com", "wixstatic.com"],
    "Tailwind CSS": ["tailwindcss", "tailwind.css"],
    "Bootstrap": ["bootstrap.min.css", "bootstrap.bundle"],
    "Google Analytics": ["googletagmanager.com/gtag", "google-analytics.com"],
    "Google Tag Manager": ["googletagmanager.com/gtm.js"],
    "HubSpot": ["js.hs-scripts.com", "hubspot.com"],
    "Intercom": ["widget.intercom.io"],
    "Segment": ["cdn.segment.com"],
    "Vercel": ["vercel.app", "x-vercel-id"],
    "Cloudflare": ["cf-ray", "cloudflare"],
    "Netlify": ["netlify.app", "x-nf-request-id"],
    "Stripe": ["js.stripe.com"],
}


def _fetch(url: str, timeout=10):
    try:
        return requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    except Exception:
        return None


def _probe_category(base_url: str, category: str, paths: list) -> tuple:
    for path in paths:
        url = urljoin(base_url, path)
        resp = _fetch(url, timeout=6)
        if resp is not None and resp.status_code < 400:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.I | re.S)
            title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
            text_snippet = re.sub(r"<[^>]+>", " ", resp.text)
            text_snippet = re.sub(r"\s+", " ", text_snippet).strip()[:400]
            return category, {
                "url": url,
                "status": "found",
                "title": title,
                "snippet": text_snippet,
            }
    return category, {"url": None, "status": "not_found"}


def _probe_subpages(base_url: str) -> dict:
    """
    Check common subpage paths concurrently across categories and return which exist, 
    with a short text snippet from each found page.
    """
    found = {}
    with ThreadPoolExecutor(max_workers=len(SUBPAGE_CANDIDATES)) as pool:
        futures = [
            pool.submit(_probe_category, base_url, category, paths)
            for category, paths in SUBPAGE_CANDIDATES.items()
        ]
        for future in futures:
            try:
                category, result = future.result()
                found[category] = result
            except Exception:
                pass

    # Ensure all categories are present in output
    for category in SUBPAGE_CANDIDATES.keys():
        found.setdefault(category, {"url": None, "status": "not_found"})
    return found



def _detect_tech_stack(html: str, headers: dict) -> list:
    html_lower = (html or "").lower()
    header_blob = " ".join(f"{k}:{v}" for k, v in (headers or {}).items()).lower()
    combined = html_lower + " " + header_blob

    detected = []
    for tech, signatures in TECH_SIGNATURES.items():
        if any(sig.lower() in combined for sig in signatures):
            detected.append(tech)
    return detected


def _extract_meta_info(html: str) -> dict:
    def _find(pattern):
        m = re.search(pattern, html, re.I)
        return m.group(1).strip() if m else None

    return {
        "title": _find(r"<title[^>]*>(.*?)</title>"),
        "meta_description": _find(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']'),
        "og_title": _find(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']'),
        "og_description": _find(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']'),
        "og_image": _find(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']*)["\']'),
        "canonical": _find(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']*)["\']'),
    }


def _extract_latest_updates(blog_info: dict) -> list:
    """Best-effort extraction of recent post-like headline text from a blog page."""
    if not blog_info or blog_info.get("status") != "found":
        return []
    url = blog_info["url"]
    resp = _fetch(url, timeout=8)
    if not resp or resp.status_code >= 400:
        return []

    candidates = re.findall(r"<(?:h1|h2|h3|a)[^>]*>([^<]{15,120})</(?:h1|h2|h3|a)>", resp.text, re.I)
    seen = set()
    updates = []
    for c in candidates:
        text = re.sub(r"\s+", " ", c).strip()
        low = text.lower()
        if text and low not in seen and not low.startswith(("home", "menu", "sign", "log in", "subscribe")):
            seen.add(low)
            updates.append(text)
        if len(updates) >= 5:
            break
    return updates


def summarize_website_intelligence(competitor_name: str, data: dict) -> str:
    import json as _json
    from agent.fallback_intelligence import AI_UNAVAILABLE_MESSAGE

    if not GROQ_API_KEY:
        return AI_UNAVAILABLE_MESSAGE

    prompt = f"""
You are a market analyst reviewing the public website of "{competitor_name}".
Based on this website data, write a concise summary (5-7 sentences, no
headers) covering: their apparent positioning/value proposition, what
product/pricing information is publicly visible, the tech stack they seem
to run on, and whether they appear to be actively publishing content/updates.

DATA:
{_json.dumps(data, indent=2, default=str)}
"""
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        return AI_UNAVAILABLE_MESSAGE
    except Exception:
        return AI_UNAVAILABLE_MESSAGE


def collect_website_intelligence(competitor: dict) -> dict:
    """
    competitor: dict-like row with at least `name`, `website_url`.
    Returns the full website intelligence bundle, or a clear "no data"
    bundle if no website was discovered.
    """
    website_url = competitor.get("website_url")
    name = competitor.get("name", "")

    if not website_url:
        return {
            "available": False,
            "reason": "No website was discovered for this company.",
        }

    homepage = get_website_snapshot(website_url)  # reused from website_tracker.py

    raw_resp = _fetch(website_url)
    html = raw_resp.text if raw_resp is not None else ""
    headers = dict(raw_resp.headers) if raw_resp is not None else {}

    subpages = _probe_subpages(website_url)
    tech_stack = _detect_tech_stack(html, headers)
    meta_info = _extract_meta_info(html)
    latest_updates = _extract_latest_updates(subpages.get("blog"))

    bundle = {
        "available": True,
        "homepage": homepage,
        "products": subpages.get("products"),
        "features": subpages.get("features"),
        "pricing": subpages.get("pricing"),
        "blog": subpages.get("blog"),
        "documentation": subpages.get("documentation"),
        "careers": subpages.get("careers"),
        "tech_stack": tech_stack,
        "latest_updates": latest_updates,
        "meta_information": meta_info,
    }
    bundle["ai_summary"] = summarize_website_intelligence(name, bundle)
    return bundle