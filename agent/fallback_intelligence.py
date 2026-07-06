"""
Fallback Intelligence
-----------------------
Ensures SpyLens NEVER produces an empty or broken-looking report just
because one data source (most commonly GitHub) is unavailable.

Provides:
  1. Extra free/keyless public data collectors — News (Google News RSS) and
     Community Discussions (Hacker News via Algolia + Reddit search) — used
     as substitutes for GitHub signal when no repository is found, per the
     priority order: Website > GitHub > Docs > Blog > Careers > News >
     Reviews > Community > Market Trends > AI Summary.
  2. `compute_confidence(...)` — a 0-100 confidence score per report
     section, computed from how much real data backs it (not from the AI).
  3. `build_data_sources(...)` — the ✓ / ✗ checklist of which sources were
     actually used, shown near the top of the report.
  4. Rule-based fallback generators — deterministic, template-based content
     for every AI-derived field, used only when the AI call itself fails
     (network/quota/parse errors). This guarantees a section is never
     "No Data Available"; worst case it's a clearly-labeled rule-based
     paragraph built from whatever public data *was* collected.

No paid APIs. Google News RSS and the HN Algolia Search API are both
public and keyless. Reddit's public JSON search endpoint is used with a
normal browser User-Agent (no auth) — if Reddit rate-limits or blocks a
request, that source is simply marked unavailable rather than failing
the whole pipeline.
"""

import re
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0 (compatible; SpyLens-Intelligence/1.0)"}

NO_GITHUB_EXPLANATION = (
    "No verified public GitHub repository was found. This company may use "
    "private repositories or closed-source development."
)

PUBLIC_SOURCE_DISCLAIMER = (
    "Since no verified public GitHub repository was available, the following "
    "insights are based on public customer reviews, industry trends, "
    "community discussions and official company information."
)

AI_UNAVAILABLE_MESSAGE = (
    "AI analysis is temporarily unavailable. Rule-based intelligence has "
    "been used for this section."
)


# ---------------------------------------------------------------------------
# Public data collectors (News + Community Discussions)
# ---------------------------------------------------------------------------

def search_news(company_name: str, limit: int = 6) -> list:
    """Google News RSS search — free, keyless. Returns [] on any failure."""
    try:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(company_name)}&hl=en-US&gl=US&ceid=US:en"
        resp = requests.get(url, headers=UA, timeout=10)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        items = []
        for item in root.findall(".//item")[:limit]:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub_date = item.findtext("pubDate") or ""
            source_el = item.find("source")
            source = source_el.text if source_el is not None else ""
            items.append({"title": title.strip(), "link": link.strip(), "published": pub_date, "source": source})
        return items
    except Exception:
        return []


def search_hackernews(company_name: str, limit: int = 6) -> list:
    """HN Algolia Search API — free, keyless, no rate-limit issues."""
    try:
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": company_name, "tags": "story", "hitsPerPage": limit},
            headers=UA, timeout=10,
        )
        if resp.status_code != 200:
            return []
        hits = resp.json().get("hits", [])
        return [
            {
                "title": h.get("title"),
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "points": h.get("points", 0),
                "num_comments": h.get("num_comments", 0),
                "created_at": h.get("created_at"),
            }
            for h in hits if h.get("title")
        ]
    except Exception:
        return []


def search_reddit(company_name: str, limit: int = 6) -> list:
    """Reddit's public search JSON endpoint — keyless. Fails silently/gracefully."""
    try:
        resp = requests.get(
            "https://www.reddit.com/search.json",
            params={"q": company_name, "limit": limit, "sort": "relevance"},
            headers=UA, timeout=10,
        )
        if resp.status_code != 200:
            return []
        children = resp.json().get("data", {}).get("children", [])
        return [
            {
                "title": c["data"].get("title"),
                "subreddit": c["data"].get("subreddit"),
                "score": c["data"].get("score", 0),
                "num_comments": c["data"].get("num_comments", 0),
                "url": "https://reddit.com" + c["data"].get("permalink", ""),
            }
            for c in children if c.get("data", {}).get("title")
        ]
    except Exception:
        return []


def _sentiment_keyword_scan(texts: list) -> dict:
    """
    Very lightweight keyword-based sentiment signal over collected titles/
    comments — used only as a rule-based fallback input, not a replacement
    for the AI's own reading of the data.
    """
    positive_kw = ["love", "great", "excellent", "amazing", "best", "impressive", "recommend", "solid", "fast", "easy"]
    negative_kw = ["hate", "worst", "bug", "broken", "slow", "expensive", "disappointing", "issue", "problem", "avoid", "scam"]
    pos, neg = 0, 0
    blob = " ".join(t.lower() for t in texts if t)
    for kw in positive_kw:
        pos += blob.count(kw)
    for kw in negative_kw:
        neg += blob.count(kw)
    total = pos + neg
    if total == 0:
        return {"label": "neutral/insufficient signal", "positive_mentions": 0, "negative_mentions": 0}
    ratio = pos / total
    label = "mostly positive" if ratio > 0.6 else "mostly negative" if ratio < 0.4 else "mixed"
    return {"label": label, "positive_mentions": pos, "negative_mentions": neg}


def collect_public_signals(company_name: str) -> dict:
    """
    Collects News + Community Discussions (HN + Reddit) for a company name.
    Always returns a well-formed dict, even if every external call fails —
    downstream code checks the `available` flags rather than assuming data.
    """
    news, hn, reddit = [], [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        news_future = pool.submit(search_news, company_name)
        hn_future = pool.submit(search_hackernews, company_name)
        reddit_future = pool.submit(search_reddit, company_name)

        try:
            news = news_future.result()
        except Exception:
            pass
        try:
            hn = hn_future.result()
        except Exception:
            pass
        try:
            reddit = reddit_future.result()
        except Exception:
            pass

    community = hn + reddit
    community_titles = [c.get("title", "") for c in community]
    sentiment = _sentiment_keyword_scan(community_titles + [n.get("title", "") for n in news])

    return {
        "news": {"available": bool(news), "items": news},
        "community": {
            "available": bool(community),
            "hackernews_items": hn,
            "reddit_items": reddit,
            "total_items": len(community),
        },
        "sentiment_signal": sentiment,
    }


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def compute_confidence(website_intel: dict, github_intel: dict, public_signals: dict, ai_generation_failed: bool) -> dict:
    """
    Deterministic, data-driven confidence score (0-100) per major section.
    Never derived from the AI itself — this reflects how much real,
    verifiable public data actually backs each section.
    """
    scores = {}

    # --- Website Analysis ---
    if website_intel.get("available"):
        found = sum(
            1 for k in ("products", "features", "pricing", "blog", "documentation", "careers")
            if (website_intel.get(k) or {}).get("status") == "found"
        )
        base = 60 + found * 6  # 60 base + up to 36 for 6 subpages found
        scores["website_analysis"] = min(96, base)
    else:
        scores["website_analysis"] = 0

    # --- GitHub Analysis ---
    if github_intel.get("available"):
        signal_points = 0
        if github_intel.get("stars", 0) or github_intel.get("forks", 0):
            signal_points += 25
        if github_intel.get("languages"):
            signal_points += 20
        if github_intel.get("contributors"):
            signal_points += 20
        commit_activity = github_intel.get("commit_activity", {})
        if commit_activity.get("source") == "yearly_stats":
            signal_points += 20
        elif commit_activity:
            signal_points += 10
        if (github_intel.get("release_frequency") or {}).get("total_releases_seen"):
            signal_points += 15
        scores["github_analysis"] = min(96, 40 + signal_points)
    else:
        scores["github_analysis"] = None  # rendered as "Not Available"

    # --- Market Intelligence (news + community combined) ---
    news_count = len(public_signals.get("news", {}).get("items", []))
    community_count = public_signals.get("community", {}).get("total_items", 0)
    market_points = min(news_count, 6) * 6 + min(community_count, 6) * 6
    if news_count == 0 and community_count == 0:
        scores["market_intelligence"] = 35 if website_intel.get("available") else 15
    else:
        scores["market_intelligence"] = min(94, 40 + market_points)

    # --- Customer Sentiment ---
    sentiment = public_signals.get("sentiment_signal", {})
    signal_strength = sentiment.get("positive_mentions", 0) + sentiment.get("negative_mentions", 0)
    if signal_strength == 0:
        scores["customer_sentiment"] = 30 if website_intel.get("available") else 20
    else:
        scores["customer_sentiment"] = min(90, 45 + signal_strength * 5)

    # --- Predictions ---
    # Predictions are inherently uncertain; confidence scales with how much
    # underlying data (website + github + public signals) supports them.
    supporting_sources = sum([
        bool(website_intel.get("available")),
        bool(github_intel.get("available")),
        bool(news_count),
        bool(community_count),
    ])
    scores["predictions"] = 30 + supporting_sources * 15  # 30-90

    if ai_generation_failed:
        # Rule-based content is inherently less nuanced than AI synthesis —
        # reflect that honestly in every AI-derived section's confidence.
        for key in ("market_intelligence", "customer_sentiment", "predictions"):
            scores[key] = max(15, int(scores[key] * 0.6))

    # --- Overall ---
    numeric_scores = [v for v in scores.values() if isinstance(v, (int, float))]
    scores["overall"] = round(sum(numeric_scores) / len(numeric_scores)) if numeric_scores else 0

    return scores


# ---------------------------------------------------------------------------
# Data Sources checklist
# ---------------------------------------------------------------------------

def build_data_sources(website_intel: dict, github_intel: dict, public_signals: dict) -> dict:
    """Returns an ordered dict-like structure of source -> bool used, matching the priority order."""
    website_available = bool(website_intel.get("available"))
    docs_found = website_available and (website_intel.get("documentation") or {}).get("status") == "found"
    blog_found = website_available and (website_intel.get("blog") or {}).get("status") == "found"
    careers_found = website_available and (website_intel.get("careers") or {}).get("status") == "found"

    sources = [
        ("Official Website", website_available),
        ("GitHub Repository", bool(github_intel.get("available"))),
        ("Product Documentation", docs_found),
        ("Blog", blog_found),
        ("Careers Page", careers_found),
        ("News", bool(public_signals.get("news", {}).get("available"))),
        ("Community Discussions", bool(public_signals.get("community", {}).get("available"))),
    ]
    return {"order": sources}


# ---------------------------------------------------------------------------
# Rule-based fallback generation (used only if the AI call itself fails)
# ---------------------------------------------------------------------------

def rule_based_competitive_intelligence(company_name: str, github_intel: dict, website_intel: dict, public_signals: dict) -> dict:
    """
    Deterministic, template-based competitive intelligence, used only when
    the Groq call fails outright (network error, quota, unparseable
    response). Every field is populated from real collected data — never
    left as "No Data Available" — clearly framed as rule-based.
    """
    has_github = bool(github_intel.get("available"))
    has_website = bool(website_intel.get("available"))
    news_items = public_signals.get("news", {}).get("items", [])
    community = public_signals.get("community", {})
    sentiment = public_signals.get("sentiment_signal", {})

    # --- Executive summary ---
    parts = [f"{company_name} was analyzed using publicly available information."]
    if has_website:
        parts.append("An official website was identified and reviewed for product, pricing, and company details.")
    if has_github:
        parts.append(f"Public GitHub activity shows {github_intel.get('stars', 0)} stars and "
                      f"{github_intel.get('forks', 0)} forks on their most active repository.")
    else:
        parts.append(NO_GITHUB_EXPLANATION)
    if news_items:
        parts.append(f"{len(news_items)} recent news mentions were found.")
    if community.get("total_items"):
        parts.append(f"{community['total_items']} community discussions were identified on Hacker News and Reddit.")
    executive_summary = " ".join(parts)

    # --- Business overview ---
    if has_website:
        homepage = website_intel.get("homepage") or {}
        meta_desc = (website_intel.get("meta_information") or {}).get("meta_description") or homepage.get("meta_description")
        business_overview = (
            meta_desc or f"{company_name} operates a public-facing website; specific business model details "
                         f"were not explicitly published on the homepage."
        )
    else:
        business_overview = (
            f"No official website was verified for {company_name}, so business model details could not be "
            f"confirmed from company-owned sources."
        )

    # --- Product direction ---
    if has_website and (website_intel.get("blog") or {}).get("status") == "found":
        product_direction = ("Recent blog activity suggests the company is actively communicating product "
                              "updates, though specific roadmap details were not extracted automatically.")
    elif has_github:
        release_freq = github_intel.get("release_frequency") or {}
        if release_freq.get("total_releases_seen"):
            product_direction = (f"The tracked repository has shipped {release_freq['total_releases_seen']} "
                                  f"public releases, suggesting an active development cadence.")
        else:
            product_direction = "Development activity is visible on GitHub, though release cadence data was limited."
    else:
        product_direction = "Product direction could not be determined from available public sources."

    # --- Developer activity / Developer Ecosystem ---
    if has_github:
        developer_activity = (
            f"The tracked repository ({github_intel.get('tracked_repo', 'N/A')}) shows "
            f"{len(github_intel.get('contributors', []))} tracked contributors and "
            f"{(github_intel.get('commit_activity') or {}).get('commits_last_4_weeks', (github_intel.get('commit_activity') or {}).get('commits_last_7_days', 'N/A'))} "
            f"recent commits, indicating {'active' if github_intel.get('stars', 0) or github_intel.get('forks', 0) else 'limited'} open-source engagement."
        )
    else:
        developer_activity = NO_GITHUB_EXPLANATION + " " + PUBLIC_SOURCE_DISCLAIMER

    # --- SWOT (rule-based, from whatever is available) ---
    strengths, weaknesses, opportunities, threats = [], [], [], []
    if has_website:
        strengths.append("Maintains an active official web presence for customers and prospects.")
    if has_github:
        strengths.append(f"Public GitHub presence with {github_intel.get('stars', 0)} stars signals developer visibility.")
    else:
        weaknesses.append("No public GitHub repository verified — engineering activity is not independently visible.")
    if community.get("total_items"):
        strengths.append("Company or product is actively discussed in developer/community forums.")
    else:
        weaknesses.append("Limited visible community discussion was found on Hacker News or Reddit.")
    if not news_items:
        weaknesses.append("Limited recent public news coverage was found.")
    else:
        opportunities.append("Recent news coverage could be leveraged for competitive positioning research.")
    if sentiment.get("negative_mentions", 0) > sentiment.get("positive_mentions", 0):
        threats.append("Community sentiment signals skew negative in the sampled discussions.")
    else:
        opportunities.append("Community sentiment signals are neutral-to-positive in the sampled discussions.")
    opportunities.append("Deeper manual research (reviews, analyst reports) could refine this analysis further.")
    threats.append("Competitive landscape data beyond public web/GitHub/community sources was not assessed.")

    # --- Hiring trends ---
    if has_website and (website_intel.get("careers") or {}).get("status") == "found":
        hiring_trends = "An active careers page was found, suggesting the company is currently hiring."
    else:
        hiring_trends = "No careers page was verified publicly; hiring activity could not be confirmed."

    # --- Scores ---
    innovation_base = 50
    if has_github:
        innovation_base += 15
    if has_website:
        innovation_base += 10
    innovation_score = min(85, innovation_base)

    overall_base = 45
    if has_website:
        overall_base += 15
    if has_github:
        overall_base += 15
    if news_items or community.get("total_items"):
        overall_base += 10
    overall_rating = min(85, overall_base)

    # --- Predictions ---
    future_predictions = [
        "Continued investment in public-facing content is likely if current web/community activity persists.",
        "Competitive pressure will depend on factors outside this report's public data scope.",
    ]
    if has_github:
        future_predictions.append("Open-source activity is likely to continue at a similar cadence based on recent history.")
    if not has_github:
        future_predictions.append("If development becomes public in the future, engineering velocity could be tracked directly via GitHub.")

    # --- Recommendations ---
    recommendations = ["Monitor the official website and blog for product and pricing changes."]
    if has_github:
        recommendations.append("Track the GitHub repository's release cadence and contributor growth over time.")
    else:
        recommendations.append("Watch for any future public GitHub presence, which would enable deeper engineering analysis.")
    if community.get("total_items"):
        recommendations.append("Review ongoing Hacker News / Reddit discussions for emerging customer concerns or praise.")
    recommendations.append("Supplement this automated report with manual review of analyst coverage and paid review platforms (G2, Capterra) for a fuller picture.")

    # --- Customer Sentiment (rule-based) ---
    sentiment_label = sentiment.get("label", "neutral/insufficient signal")
    if sentiment.get("positive_mentions", 0) or sentiment.get("negative_mentions", 0):
        customer_sentiment = (
            f"Community discussion sentiment appears {sentiment_label} "
            f"({sentiment.get('positive_mentions', 0)} positive vs {sentiment.get('negative_mentions', 0)} "
            f"negative keyword signals across sampled Hacker News/Reddit discussions)."
        )
    else:
        customer_sentiment = (
            "Insufficient public community discussion volume was found to assess customer sentiment reliably."
        )

    # --- Market Position (rule-based) ---
    market_signals = []
    if has_website:
        market_signals.append("an active official website")
    if news_items:
        market_signals.append(f"{len(news_items)} recent news mentions")
    if community.get("total_items"):
        market_signals.append(f"{community['total_items']} community discussions")
    if market_signals:
        market_position = (
            f"{company_name} shows public visibility through " + ", ".join(market_signals) +
            ", suggesting an active but not independently benchmarked market presence."
        )
    else:
        market_position = (
            f"Limited public market signal was found for {company_name}; market position could not be "
            f"reliably estimated from available sources."
        )

    return {
        "executive_summary": executive_summary,
        "technology_stack": list((github_intel.get("languages") or {}).keys()) if has_github else (website_intel.get("tech_stack") or []),
        "business_overview": business_overview,
        "product_direction": product_direction,
        "developer_activity": developer_activity,
        "developer_ecosystem": developer_activity,
        "customer_sentiment": customer_sentiment,
        "market_position": market_position,
        "swot": {
            "strengths": strengths or ["No verified strengths could be determined from public data."],
            "weaknesses": weaknesses or ["No verified weaknesses could be determined from public data."],
            "opportunities": opportunities,
            "threats": threats,
        },
        "hiring_trends": hiring_trends,
        "innovation_score": {"score": innovation_score, "reasoning": "Estimated from available public data using rule-based scoring."},
        "overall_rating": {"score": overall_rating, "reasoning": "Estimated from available public data using rule-based scoring."},
        "future_predictions": future_predictions,
        "recommendations": recommendations,
        "ai_generation_failed": True,
        "ai_unavailable_message": AI_UNAVAILABLE_MESSAGE,
    }