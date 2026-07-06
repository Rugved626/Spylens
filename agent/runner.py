import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.github_tracker import get_recent_commits
from agent.website_tracker import get_website_snapshot
from agent.summarizer import summarize_with_groq
from database import get_db
import json

def run_agent_for_competitor(competitor: dict) -> str:
    """
    Run full intelligence cycle for one competitor.
    Returns the AI summary.
    """
    name = competitor["name"]
    website_url = competitor["website_url"]
    github_repo = competitor["github_repo"]

    print(f"\n SpyLens scanning: {name}")

    # Step 1 — GitHub tracking
    github_data = {}
    if github_repo:
        print(f"  Fetching GitHub commits for {github_repo}...")
        github_data = get_recent_commits(github_repo)
        print(f"  Found {github_data.get('total_commits', 0)} commits")
    else:
        github_data = {"message": "No GitHub repo provided"}

    # Step 2 — Website tracking
    website_data = {}
    if website_url:
        print(f"  Fetching website snapshot for {website_url}...")
        website_data = get_website_snapshot(website_url)
        print(f"  Website status: {website_data.get('status')}")
    else:
        website_data = {"message": "No website URL provided"}

    # Step 3 — AI summarization
    print(f"  Generating AI summary...")
    summary = summarize_with_groq(name, github_data, website_data)

    # Step 4 — Save to DB
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO reports (competitor_id, report_type, summary, raw_data)
        VALUES (?, ?, ?, ?)
    ''', (
        competitor["id"],
        "weekly",
        summary,
        json.dumps({"github": github_data, "website": website_data})
    ))
    conn.commit()
    conn.close()

    print(f"  Report saved for {name}")
    return summary

def run_all_competitors():
    """
    Run agent for every competitor in the DB.
    Called by scheduler every week.
    """
    conn = get_db()
    c = conn.cursor()
    competitors = c.execute("SELECT * FROM competitors").fetchall()
    conn.close()

    if not competitors:
        print("No competitors found. Add some via the dashboard.")
        return

    for comp in competitors:
        run_agent_for_competitor(dict(comp))

if __name__ == "__main__":
    run_all_competitors()
