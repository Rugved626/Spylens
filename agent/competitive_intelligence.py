"""
AI Competitive Intelligence
----------------------------
Combines GitHub Intelligence + Website Intelligence + public fallback
signals (news, community discussions) into a single structured competitive
analysis via Groq: executive summary, technology stack, business overview,
product direction, developer ecosystem, customer sentiment, market
position, SWOT, hiring trends, innovation score, overall rating, future
predictions, and recommendations.

Follows the priority order: Official Website > GitHub Organization >
Documentation > Blog > Careers > News > Community Discussions > Market
Trends > AI Summary. If GitHub is unavailable, the prompt explicitly asks
for a "Developer Ecosystem" explanation instead of an empty section, and
leans on website + public signals for everything else. If the AI call
itself fails, a full rule-based fallback (agent.fallback_intelligence)
takes over so a report section is never "No Data Available".

Also attaches a data-driven confidence score and a Data Sources checklist
to the final result, so the report can show exactly what was used and how
reliable each section is.
"""

import json
import re
import requests

from agent.summarizer import GROQ_API_KEY
from agent.fallback_intelligence import (
    collect_public_signals,
    compute_confidence,
    build_data_sources,
    rule_based_competitive_intelligence,
    NO_GITHUB_EXPLANATION,
    PUBLIC_SOURCE_DISCLAIMER,
    AI_UNAVAILABLE_MESSAGE,
)

SCHEMA_HINT = {
    "executive_summary": "2-3 sentence high-level summary of the company's overall position",
    "technology_stack": ["list", "of", "strings"],
    "business_overview": "2-3 sentences on what the company does and how it makes money",
    "product_direction": "2-3 sentences on where the product seems to be heading",
    "developer_ecosystem": "2-4 sentences on engineering/open-source activity, OR — if no GitHub was found — a professional explanation that no verified public repository exists plus what was used instead",
    "customer_sentiment": "2-3 sentences on how customers/community discuss this company, based on news and community discussion signals",
    "market_position": "2-3 sentences on the company's apparent position in its market, based on website, news and community signals",
    "swot": {
        "strengths": ["list", "of", "strings"],
        "weaknesses": ["list", "of", "strings"],
        "opportunities": ["list", "of", "strings"],
        "threats": ["list", "of", "strings"],
    },
    "hiring_trends": "1-2 sentences inferred from careers page presence/content, if any",
    "innovation_score": {"score": "integer 0-100", "reasoning": "1 sentence"},
    "overall_rating": {"score": "integer 0-100", "reasoning": "1 sentence"},
    "future_predictions": ["list", "of", "3-5", "short", "prediction", "strings"],
    "recommendations": ["list", "of", "3-5", "short", "actionable", "recommendation", "strings", "for", "a", "founder", "competing", "with", "them"],
}


def _extract_json(text: str) -> dict:
    """Groq sometimes wraps JSON in prose or code fences despite instructions."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return {}


def _ensure_all_fields(parsed: dict, fallback_source: dict) -> dict:
    """Fill in any field the model omitted using the rule-based fallback's values."""
    for key, default in fallback_source.items():
        if key in ("ai_generation_failed", "ai_unavailable_message"):
            continue
        parsed.setdefault(key, default)
    parsed.setdefault("swot", fallback_source.get("swot", {}))
    for k in ("strengths", "weaknesses", "opportunities", "threats"):
        parsed["swot"].setdefault(k, fallback_source.get("swot", {}).get(k, []))
    return parsed


def _attach_metadata(result: dict, website_intel: dict, github_intel: dict,
                      public_signals: dict, ai_generation_failed: bool) -> dict:
    """Attaches the Data Sources checklist and confidence scores every report needs."""
    result["data_sources"] = build_data_sources(website_intel, github_intel, public_signals)
    result["confidence"] = compute_confidence(website_intel, github_intel, public_signals, ai_generation_failed)
    result["github_available"] = bool(github_intel.get("available"))
    if not github_intel.get("available"):
        result["no_github_explanation"] = NO_GITHUB_EXPLANATION
        result["public_source_disclaimer"] = PUBLIC_SOURCE_DISCLAIMER
    return result


def generate_competitive_intelligence(competitor_name: str, github_intel: dict, website_intel: dict,
                                       public_signals: dict = None) -> dict:
    """
    public_signals: output of agent.fallback_intelligence.collect_public_signals(name).
    If not provided, it is collected here (kept optional so existing callers
    that don't pass it yet don't break).
    """
    if public_signals is None:
        public_signals = collect_public_signals(competitor_name)

    has_github = bool(github_intel.get("available"))

    # If Groq isn't configured at all, go straight to the rule-based path —
    # this is not an "error", just an unconfigured deployment, so no need
    # to attempt a network call first.
    if not GROQ_API_KEY:
        result = rule_based_competitive_intelligence(competitor_name, github_intel, website_intel, public_signals)
        return _attach_metadata(result, website_intel, github_intel, public_signals, ai_generation_failed=True)

    # Trim to keep the prompt a reasonable size.
    trimmed_github = {
        "available": has_github,
        "stars": github_intel.get("stars"),
        "forks": github_intel.get("forks"),
        "watchers": github_intel.get("watchers"),
        "languages": github_intel.get("languages"),
        "frameworks": github_intel.get("frameworks"),
        "num_contributors": len(github_intel.get("contributors", [])),
        "commit_activity": github_intel.get("commit_activity"),
        "release_frequency": github_intel.get("release_frequency"),
        "repository_growth": github_intel.get("repository_growth"),
        "most_active_repository": github_intel.get("most_active_repository"),
        "ai_summary": github_intel.get("ai_summary"),
    } if has_github else {"available": False, "reason": NO_GITHUB_EXPLANATION}

    trimmed_website = {
        "available": website_intel.get("available"),
        "homepage_title": (website_intel.get("homepage") or {}).get("title"),
        "meta_information": website_intel.get("meta_information"),
        "products_found": (website_intel.get("products") or {}).get("status"),
        "features_found": (website_intel.get("features") or {}).get("status"),
        "pricing_found": (website_intel.get("pricing") or {}).get("status"),
        "pricing_snippet": (website_intel.get("pricing") or {}).get("snippet"),
        "careers_found": (website_intel.get("careers") or {}).get("status"),
        "careers_snippet": (website_intel.get("careers") or {}).get("snippet"),
        "tech_stack": website_intel.get("tech_stack"),
        "latest_updates": website_intel.get("latest_updates"),
        "ai_summary": website_intel.get("ai_summary"),
    } if website_intel.get("available") else {"available": False}

    trimmed_public_signals = {
        "news_items": [n.get("title") for n in public_signals.get("news", {}).get("items", [])],
        "community_items": [
            c.get("title") for c in
            (public_signals.get("community", {}).get("hackernews_items", []) +
             public_signals.get("community", {}).get("reddit_items", []))
        ],
        "sentiment_signal": public_signals.get("sentiment_signal"),
    }

    github_instruction = (
        "GitHub data IS available — generate developer_ecosystem covering repository "
        "analysis, contributor activity, languages, releases and commit activity."
        if has_github else
        f'GitHub data is NOT available. For "developer_ecosystem", start with exactly this '
        f'sentence: "{NO_GITHUB_EXPLANATION}" — then continue with 2-3 more sentences noting '
        f'that the analysis instead relies on the website, news and community discussions below. '
        f'For every other field, explicitly ground your reasoning in the website/news/community '
        f'data provided rather than GitHub, and begin the customer_sentiment field with this '
        f'exact sentence: "{PUBLIC_SOURCE_DISCLAIMER}"'
    )

    prompt = f"""
You are a senior competitive intelligence analyst. Analyze this data about
the company "{competitor_name}" and produce a structured competitive
intelligence report for a startup founder deciding how to compete with them.

Follow this source priority order when reasoning: Official Website > GitHub
Organization > Product Documentation > Blog > Careers Page > News > Public
Customer Reviews > Community Discussions > Market Trends.

{github_instruction}

GITHUB INTELLIGENCE:
{json.dumps(trimmed_github, indent=2, default=str)}

WEBSITE INTELLIGENCE:
{json.dumps(trimmed_website, indent=2, default=str)}

NEWS & COMMUNITY SIGNALS:
{json.dumps(trimmed_public_signals, indent=2, default=str)}

Respond with ONLY valid JSON (no markdown fences, no commentary before or
after) matching exactly this shape:
{json.dumps(SCHEMA_HINT, indent=2)}

Rules:
- innovation_score and overall_rating "score" must be integers from 0 to 100.
- Never invent facts; reason only from the data given.
- Every section must contain real analysis — never write "No data available"
  or leave a field empty. If data is thin, say so professionally and reason
  from whatever IS available.
- Keep every list item short (under 15 words).
- Output must be parseable by json.loads with no trailing commentary.
"""

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1800,
                "temperature": 0.4,
            },
            timeout=45,
        )
    except Exception:
        # Network-level failure — never surface the raw exception to the report.
        result = rule_based_competitive_intelligence(competitor_name, github_intel, website_intel, public_signals)
        return _attach_metadata(result, website_intel, github_intel, public_signals, ai_generation_failed=True)

    if resp.status_code != 200:
        # Never surface raw API error codes (e.g. "Groq API Error 429") to the report.
        result = rule_based_competitive_intelligence(competitor_name, github_intel, website_intel, public_signals)
        result["ai_unavailable_message"] = AI_UNAVAILABLE_MESSAGE
        return _attach_metadata(result, website_intel, github_intel, public_signals, ai_generation_failed=True)

    raw_text = resp.json()["choices"][0]["message"]["content"]
    parsed = _extract_json(raw_text)

    if not parsed:
        # Model returned something unparseable — fall back to rule-based content
        # rather than an empty/broken section.
        result = rule_based_competitive_intelligence(competitor_name, github_intel, website_intel, public_signals)
        result["ai_unavailable_message"] = AI_UNAVAILABLE_MESSAGE
        return _attach_metadata(result, website_intel, github_intel, public_signals, ai_generation_failed=True)

    # Success — fill any gaps the model left using the rule-based generator's
    # values (never leaving a field blank), then attach metadata.
    fallback_source = rule_based_competitive_intelligence(competitor_name, github_intel, website_intel, public_signals)
    parsed = _ensure_all_fields(parsed, fallback_source)
    return _attach_metadata(parsed, website_intel, github_intel, public_signals, ai_generation_failed=False)