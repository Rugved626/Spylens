import requests
import os
from datetime import datetime, timedelta

GH_TOKEN = os.environ.get("GH_TOKEN", "")

def get_recent_commits(github_repo: str, days: int = 7) -> dict:
    """
    Fetch commits from a GitHub repo in the last N days.
    github_repo format: 'owner/repo' e.g. 'langchain-ai/langchain'
    """
    headers = {}
<<<<<<< HEAD
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    elif GH_TOKEN:
=======
    if GH_TOKEN:
>>>>>>> a5dd5cdc1f1b83663f80ea51989a0f0dfd9737b2
        headers["Authorization"] = f"Bearer {GH_TOKEN}"

    since = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"

    url = f"https://api.github.com/repos/{github_repo}/commits"
    params = {"since": since, "per_page": 20}

    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        return {
            "repo": github_repo,
            "error": f"GitHub API error: {response.status_code}",
            "commits": []
        }

    commits = response.json()

    commit_list = []
    for c in commits:
        commit_list.append({
            "message": c["commit"]["message"].split("\n")[0],  # first line only
            "author": c["commit"]["author"]["name"],
            "date": c["commit"]["author"]["date"],
            "url": c["html_url"]
        })

    return {
        "repo": github_repo,
        "total_commits": len(commit_list),
        "commits": commit_list,
        "period_days": days
    }
