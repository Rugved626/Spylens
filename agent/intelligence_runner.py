"""
Intelligence Pipeline Orchestrator
------------------------------------
Runs the full "Analyze" pipeline for one competitor:
  1. Finding Company...
  2. Finding Website...
  3. Finding GitHub...
  4. Analyzing Website...
  5. Analyzing GitHub...
  6. Gathering Market Intelligence... (news + community discussions)
  7. Generating AI Insights...
  8. Creating PDF...

Progress is written to the `analysis_status` table after each stage so the
frontend can poll `/analysis_status/<id>` and show the exact stage labels
requested. The final bundle is written to `intelligence_reports`.

Designed to be run in a background thread (see app.py `/analyze/<id>`),
matching the existing pattern already used by APScheduler in this codebase.
"""

import json
import traceback

from database import get_db
from agent.discovery import discover_company
from agent.website_intelligence import collect_website_intelligence
from agent.github_intelligence import collect_github_intelligence
from agent.competitive_intelligence import generate_competitive_intelligence
from agent.fallback_intelligence import collect_public_signals, AI_UNAVAILABLE_MESSAGE
from agent.pdf_report import generate_pdf_report

STAGES = [
    "Finding Company...",
    "Finding Website...",
    "Finding GitHub...",
    "Analyzing Website...",
    "Analyzing GitHub...",
    "Gathering Market Intelligence...",
    "Generating AI Insights...",
    "Creating PDF...",
]


def _set_status(competitor_id: int, stage: str, done: bool = False, error: str = None):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO analysis_status (competitor_id, stage, done, error, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(competitor_id) DO UPDATE SET
            stage = excluded.stage,
            done = excluded.done,
            error = excluded.error,
            updated_at = excluded.updated_at
    """, (competitor_id, stage, 1 if done else 0, error))
    conn.commit()
    conn.close()


def get_status(competitor_id: int) -> dict:
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        "SELECT stage, done, error FROM analysis_status WHERE competitor_id = ?",
        (competitor_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"stage": None, "done": False, "error": None, "started": False}
    return {"stage": row["stage"], "done": bool(row["done"]), "error": row["error"], "started": True}


def get_latest_report(competitor_id: int) -> dict:
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        "SELECT * FROM intelligence_reports WHERE competitor_id = ?",
        (competitor_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "github_data": json.loads(row["github_data"]) if row["github_data"] else None,
        "website_data": json.loads(row["website_data"]) if row["website_data"] else None,
        "ai_data": json.loads(row["ai_data"]) if row["ai_data"] else None,
        "pdf_path": row["pdf_path"],
        "created_at": row["created_at"],
    }


def _save_report(competitor_id: int, github_data: dict, website_data: dict, ai_data: dict, pdf_path: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO intelligence_reports (competitor_id, github_data, website_data, ai_data, pdf_path, created_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(competitor_id) DO UPDATE SET
            github_data = excluded.github_data,
            website_data = excluded.website_data,
            ai_data = excluded.ai_data,
            pdf_path = excluded.pdf_path,
            created_at = excluded.created_at
    """, (competitor_id, json.dumps(github_data, default=str), json.dumps(website_data, default=str),
          json.dumps(ai_data, default=str), pdf_path))
    conn.commit()
    conn.close()


def run_full_analysis(competitor_id: int):
    """
    Executes all 8 stages for one competitor and persists the result.
    Safe to call from a background thread — opens its own DB connections.
    """
    try:
        conn = get_db()
        c = conn.cursor()
        row = c.execute("SELECT * FROM competitors WHERE id = ?", (competitor_id,)).fetchone()
        conn.close()

        if not row:
            _set_status(competitor_id, "Error", done=True, error="Competitor not found.")
            return

        competitor = dict(row)

        # --- Stage 1: Finding Company ---
        _set_status(competitor_id, STAGES[0])
        name = competitor.get("name", "")

        # --- Stage 2: Finding Website ---
        _set_status(competitor_id, STAGES[1])
        # --- Stage 3: Finding GitHub ---
        _set_status(competitor_id, STAGES[2])
        if not competitor.get("website_url") or not competitor.get("github_repo"):
            # Backfill via discovery if the original add somehow missed it
            # (e.g. Clearbit/GitHub were briefly unreachable at add-time).
            rediscovered = discover_company(name)
            conn = get_db()
            c = conn.cursor()
            c.execute("""
                UPDATE competitors SET
                    website_url = COALESCE(NULLIF(website_url, ''), ?),
                    github_repo = COALESCE(NULLIF(github_repo, ''), ?),
                    github_org = COALESCE(NULLIF(github_org, ''), ?),
                    logo_url = COALESCE(NULLIF(logo_url, ''), ?),
                    description = COALESCE(NULLIF(description, ''), ?),
                    linkedin_url = COALESCE(NULLIF(linkedin_url, ''), ?),
                    twitter_url = COALESCE(NULLIF(twitter_url, ''), ?)
                WHERE id = ?
            """, (
                rediscovered.get("website_url"), rediscovered.get("github_repo"),
                rediscovered.get("github_org"), rediscovered.get("logo_url"),
                rediscovered.get("description"), rediscovered.get("linkedin_url"),
                rediscovered.get("twitter_url"), competitor_id,
            ))
            conn.commit()
            row = c.execute("SELECT * FROM competitors WHERE id = ?", (competitor_id,)).fetchone()
            conn.close()
            competitor = dict(row)

        # --- Stage 4: Analyzing Website ---
        _set_status(competitor_id, STAGES[3])
        website_data = collect_website_intelligence(competitor)

        # --- Stage 5: Analyzing GitHub ---
        _set_status(competitor_id, STAGES[4])
        github_data = collect_github_intelligence(competitor)

        # --- Stage 6: Gathering Market Intelligence (news + community) ---
        _set_status(competitor_id, STAGES[5])
        public_signals = collect_public_signals(competitor.get("name", ""))

        # --- Stage 7: Generating AI Insights ---
        _set_status(competitor_id, STAGES[6])
        ai_data = generate_competitive_intelligence(competitor.get("name", ""), github_data, website_data, public_signals)

        # --- Stage 8: Creating PDF ---
        _set_status(competitor_id, STAGES[7])
        pdf_path = generate_pdf_report(competitor, github_data, website_data, ai_data)

        _save_report(competitor_id, github_data, website_data, ai_data, pdf_path)
        _set_status(competitor_id, "Complete", done=True)

    except Exception as e:
        # Log the full traceback server-side for debugging, but never expose
        # raw exception/API error text to the user-facing status field.
        print(f"[intelligence_runner] analysis failed for competitor {competitor_id}: {e}\n{traceback.format_exc()}")
        _set_status(
            competitor_id, "Error", done=True,
            error="Analysis could not be completed due to a temporary issue. Please try again."
        )