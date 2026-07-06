"""
GitHub Intelligence
--------------------
Given a competitor's discovered GitHub organization + tracked repo, collects
a broad intelligence snapshot: stars, forks, watchers, languages, inferred
frameworks/topics, contributors, commit activity, release frequency,
repository growth signal, and the org's most active repository. Reuses
`agent.github_tracker.get_recent_commits` for the existing weekly-commit
feed instead of re-implementing it.

No paid APIs. Uses the public GitHub REST API (keyless, rate-limited to
60 req/hr unless GH_TOKEN/GITHUB_TOKEN is set in .env — same env vars
already used by agent/github_tracker.py).
"""

import os
import requests
from datetime import datetime, timezone

from agent.github_tracker import get_recent_commits
from agent.summarizer import GROQ_API_KEY
import requests as _requests

GH_TOKEN = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")


def _gh_headers():
    headers = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    return headers


def _get(url, params=None, timeout=10):
    try:
        return requests.get(url, headers=_gh_headers(), params=params, timeout=timeout)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Individual collectors
# ---------------------------------------------------------------------------

def get_repo_stats(github_repo: str) -> dict:
    """Stars, forks, watchers, open issues, primary language, size, age."""
    r = _get(f"https://api.github.com/repos/{github_repo}")
    if not r or r.status_code != 200:
        return {"error": f"repo lookup failed ({r.status_code if r else 'no response'})"}
    d = r.json()
    return {
        "full_name": d.get("full_name"),
        "description": d.get("description"),
        "stars": d.get("stargazers_count", 0),
        "forks": d.get("forks_count", 0),
        "watchers": d.get("subscribers_count", d.get("watchers_count", 0)),
        "open_issues": d.get("open_issues_count", 0),
        "primary_language": d.get("language"),
        "topics": d.get("topics", []),
        "created_at": d.get("created_at"),
        "pushed_at": d.get("pushed_at"),
        "size_kb": d.get("size", 0),
        "license": (d.get("license") or {}).get("spdx_id") if d.get("license") else None,
    }


def get_languages(github_repo: str) -> dict:
    """Language -> byte count, used to derive language share percentages."""
    r = _get(f"https://api.github.com/repos/{github_repo}/languages")
    if not r or r.status_code != 200:
        return {}
    data = r.json() or {}
    total = sum(data.values()) or 1
    return {lang: round(bytes_ * 100 / total, 1) for lang, bytes_ in data.items()}


def infer_frameworks(repo_stats: dict, languages: dict) -> list:
    """
    Heuristic framework/tooling detection — GitHub doesn't expose this
    directly, so we infer from repo topics (maintainers often tag these
    explicitly) plus common language-to-framework associations.
    """
    topics = [t.lower() for t in repo_stats.get("topics", [])]
    known_framework_topics = {
        "react", "vue", "angular", "nextjs", "next-js", "svelte", "django",
        "flask", "fastapi", "express", "nestjs", "spring", "spring-boot",
        "rails", "laravel", "tensorflow", "pytorch", "langchain", "langgraph",
        "kubernetes", "docker", "graphql", "grpc", "tailwindcss", "vite",
        "webpack", "electron", "flutter", "react-native",
    }
    detected = sorted({t for t in topics if t in known_framework_topics})

    # Fallback language-based guess if no explicit topic tags exist
    if not detected:
        lang_guess = {
            "Python": ["Flask/Django/FastAPI (unconfirmed — inferred from language)"],
            "JavaScript": ["Node.js/React (unconfirmed — inferred from language)"],
            "TypeScript": ["Node.js/React/Angular (unconfirmed — inferred from language)"],
            "Go": ["Go standard toolchain"],
            "Rust": ["Cargo/Rust ecosystem"],
        }
        primary = repo_stats.get("primary_language")
        detected = lang_guess.get(primary, [])

    return detected or list(topics[:5])


def get_contributors(github_repo: str, limit: int = 10) -> list:
    r = _get(f"https://api.github.com/repos/{github_repo}/contributors",
             params={"per_page": limit, "anon": "false"})
    if not r or r.status_code != 200:
        return []
    return [
        {
            "login": c.get("login"),
            "contributions": c.get("contributions", 0),
            "avatar_url": c.get("avatar_url"),
            "profile_url": c.get("html_url"),
        }
        for c in (r.json() or [])[:limit]
    ]


def get_commit_activity(github_repo: str) -> dict:
    """
    Weekly commit counts for the last year via the stats endpoint. GitHub
    computes this async on first request (may return 202 while it caches) —
    we fall back to the existing 7-day tracker if that happens.
    """
    r = _get(f"https://api.github.com/repos/{github_repo}/stats/commit_activity")
    if r and r.status_code == 200 and isinstance(r.json(), list) and r.json():
        weeks = r.json()
        total_year = sum(w.get("total", 0) for w in weeks)
        last_4_weeks = sum(w.get("total", 0) for w in weeks[-4:])
        prev_4_weeks = sum(w.get("total", 0) for w in weeks[-8:-4]) or 1
        trend = round(((last_4_weeks - prev_4_weeks) / prev_4_weeks) * 100, 1)
        return {
            "source": "yearly_stats",
            "total_commits_last_year": total_year,
            "commits_last_4_weeks": last_4_weeks,
            "trend_pct_vs_prior_4_weeks": trend,
        }

    # Fallback: reuse the existing weekly tracker (already in the codebase)
    recent = get_recent_commits(github_repo, days=7)
    return {
        "source": "recent_7_days_fallback",
        "commits_last_7_days": recent.get("total_commits", 0),
        "note": "Yearly stats not yet cached by GitHub; showing last 7 days instead.",
    }


def get_release_frequency(github_repo: str) -> dict:
    r = _get(f"https://api.github.com/repos/{github_repo}/releases", params={"per_page": 30})
    if not r or r.status_code != 200:
        return {"total_releases_seen": 0}
    releases = r.json() or []
    if not releases:
        return {"total_releases_seen": 0}

    dates = []
    for rel in releases:
        ts = rel.get("published_at") or rel.get("created_at")
        if ts:
            try:
                dates.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
            except Exception:
                pass
    dates.sort(reverse=True)

    avg_days_between = None
    if len(dates) >= 2:
        gaps = [(dates[i] - dates[i + 1]).days for i in range(len(dates) - 1)]
        avg_days_between = round(sum(gaps) / len(gaps), 1)

    return {
        "total_releases_seen": len(releases),
        "latest_release": releases[0].get("tag_name") if releases else None,
        "latest_release_date": dates[0].isoformat() if dates else None,
        "avg_days_between_releases": avg_days_between,
    }


def get_repository_growth(github_org: str, current_repo_stats: dict) -> dict:
    """
    GitHub's REST API has no historical star-count endpoint, so true growth
    curves aren't available without a paid service (e.g. star-history.com's
    scraper, GH Archive). As a free proxy, we compute:
      - stars-per-day-since-creation (velocity)
      - how many public repos the org has created per year (org expansion)
    """
    velocity = None
    created_at = current_repo_stats.get("created_at")
    if created_at:
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age_days = max((datetime.now(timezone.utc) - created).days, 1)
            velocity = round(current_repo_stats.get("stars", 0) / age_days, 3)
        except Exception:
            pass

    repos_by_year = {}
    if github_org:
        r = _get(f"https://api.github.com/orgs/{github_org}/repos",
                 params={"per_page": 100, "type": "public"})
        if r and r.status_code == 200:
            for repo in (r.json() or []):
                created = repo.get("created_at", "")
                year = created[:4] if created else "unknown"
                repos_by_year[year] = repos_by_year.get(year, 0) + 1

    return {
        "star_velocity_per_day": velocity,
        "public_repos_created_by_year": dict(sorted(repos_by_year.items())),
        "note": "GitHub's API has no historical star endpoint; velocity is a proxy (current stars / repo age).",
    }


def get_most_active_repo(github_org: str, fallback_repo: str) -> dict:
    """
    Cross-check the org's most recently pushed public, non-fork repo.
    Discovery already selects this repo as `github_repo` for the competitor,
    so this mainly re-confirms/labels it rather than re-deriving it.
    """
    if not github_org:
        return {"full_name": fallback_repo}
    r = _get(f"https://api.github.com/orgs/{github_org}/repos",
             params={"sort": "pushed", "direction": "desc", "per_page": 10, "type": "public"})
    if r and r.status_code == 200:
        repos = [x for x in (r.json() or []) if not x.get("fork") and not x.get("archived")]
        if repos:
            top = repos[0]
            return {
                "full_name": top.get("full_name"),
                "stars": top.get("stargazers_count", 0),
                "pushed_at": top.get("pushed_at"),
            }
    return {"full_name": fallback_repo}


# ---------------------------------------------------------------------------
# AI summary
# ---------------------------------------------------------------------------

def summarize_github_intelligence(competitor_name: str, data: dict) -> str:
    """Groq-generated narrative summary of the collected GitHub signals."""
    import json as _json
    from agent.fallback_intelligence import AI_UNAVAILABLE_MESSAGE

    if not GROQ_API_KEY:
        return AI_UNAVAILABLE_MESSAGE

    prompt = f"""
You are a technical analyst reviewing open-source/engineering activity for
the company "{competitor_name}". Based on this GitHub data, write a concise
summary (5-7 sentences, no headers) covering: overall engineering momentum,
what the language/framework mix suggests about their stack, how active
development currently is, and one notable signal a competitor should watch.

DATA:
{_json.dumps(data, indent=2, default=str)}
"""
    try:
        resp = _requests.post(
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
        # Never surface raw API error codes to the report.
        return AI_UNAVAILABLE_MESSAGE
    except Exception:
        return AI_UNAVAILABLE_MESSAGE


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def collect_github_intelligence(competitor: dict) -> dict:
    """
    competitor: dict-like row with at least `name`, `github_org`, `github_repo`.
    Returns the full GitHub intelligence bundle, or a clear "no data" bundle
    if no GitHub org/repo was discovered for this competitor.
    """
    github_repo = competitor.get("github_repo")
    github_org = competitor.get("github_org")
    name = competitor.get("name", "")

    if not github_repo:
        from agent.fallback_intelligence import NO_GITHUB_EXPLANATION
        return {
            "available": False,
            "reason": NO_GITHUB_EXPLANATION,
        }

    repo_stats = get_repo_stats(github_repo)
    if "error" in repo_stats:
        return {
            "available": False,
            "reason": f"GitHub API error: {repo_stats['error']}",
        }

    languages = get_languages(github_repo)
    frameworks = infer_frameworks(repo_stats, languages)
    contributors = get_contributors(github_repo)
    commit_activity = get_commit_activity(github_repo)
    release_frequency = get_release_frequency(github_repo)
    repository_growth = get_repository_growth(github_org, repo_stats)
    most_active_repo = get_most_active_repo(github_org, github_repo)

    bundle = {
        "available": True,
        "github_org": github_org,
        "tracked_repo": github_repo,
        "stars": repo_stats.get("stars"),
        "forks": repo_stats.get("forks"),
        "watchers": repo_stats.get("watchers"),
        "open_issues": repo_stats.get("open_issues"),
        "languages": languages,
        "frameworks": frameworks,
        "contributors": contributors,
        "commit_activity": commit_activity,
        "release_frequency": release_frequency,
        "repository_growth": repository_growth,
        "most_active_repository": most_active_repo,
        "repo_details": repo_stats,
    }
    bundle["ai_summary"] = summarize_github_intelligence(name, bundle)
    return bundle