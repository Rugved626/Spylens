import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

def summarize_with_groq(competitor_name: str, github_data: dict, website_data: dict) -> str:
    if not GROQ_API_KEY:
        commits_count = github_data.get("total_commits", 0) if isinstance(github_data, dict) else 0
        web_title = website_data.get("title", "N/A") if isinstance(website_data, dict) else "N/A"
        web_desc = website_data.get("meta_description", "N/A") if isinstance(website_data, dict) else "N/A"
        return f"## {competitor_name} — Weekly Intelligence Report\n\n### GitHub Activity\n- Total commits this week: {commits_count}\n- Signal: (AI analysis is unavailable; manual monitoring of repository recommended)\n\n### Website Changes\n- Current positioning: {web_title} - {web_desc}\n- Note: AI summarization is currently unconfigured."

    prompt = f"""
You are a competitive intelligence analyst for Indian startups.

Analyze this data about competitor "{competitor_name}" and write a clear, actionable weekly digest.

GITHUB DATA (last 7 days):
{json.dumps(github_data, indent=2)}

WEBSITE DATA:
Title: {website_data.get('title', 'N/A') if isinstance(website_data, dict) else 'N/A'}
Meta Description: {website_data.get('meta_description', 'N/A') if isinstance(website_data, dict) else 'N/A'}
Content Preview: {website_data.get('content_preview', 'N/A') if isinstance(website_data, dict) else 'N/A'}

Write the digest in this exact format:

## {competitor_name} — Weekly Intelligence Report

### GitHub Activity
- Total commits this week: X
- Key changes: (list top 3 commit messages and what they mean)
- Signal: (what are they focused on building?)

### Website Changes
- Current positioning: (what does their homepage say they do?)
- Any notable changes: (based on content)

### Opportunity for You
- (1-2 actionable insights based on what competitor is NOT doing or doing poorly)

Keep it concise, sharp, and useful for a startup founder.
"""

    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1000
        }
    )

    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"]
    else:
        return f"Groq API error: {response.status_code} — {response.text}"
