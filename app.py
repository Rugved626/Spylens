from dotenv import load_dotenv
load_dotenv()
<<<<<<< HEAD
from flask import Flask, render_template, jsonify, request, redirect, url_for, send_file
=======
from flask import Flask, render_template, jsonify, request, redirect, url_for
>>>>>>> a5dd5cdc1f1b83663f80ea51989a0f0dfd9737b2
from database import init_db, get_db
from agent.runner import run_agent_for_competitor
from apscheduler.schedulers.background import BackgroundScheduler
from agent.runner import run_all_competitors
<<<<<<< HEAD
from agent.discovery import discover_company
from agent.intelligence_runner import run_full_analysis, get_status, get_latest_report
import os
import threading
=======
import os
>>>>>>> a5dd5cdc1f1b83663f80ea51989a0f0dfd9737b2
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "spylens-secret")

# ---- Routes ----

@app.route("/")
def home():
    conn = get_db()
    c = conn.cursor()
    competitors = c.execute("SELECT * FROM competitors ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template("index.html", competitors=competitors)

@app.route("/add_competitor", methods=["POST"])
def add_competitor():
    name = request.form.get("name", "").strip()
<<<<<<< HEAD

    if not name:
        return jsonify({"error": "Company name is required"}), 400

    # Auto-discover website, GitHub org/repo, logo, description, and socials
    # from the company name alone. Cached internally so repeat lookups are fast.
    discovered = discover_company(name)

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO competitors (
            name, website_url, github_repo, github_org,
            logo_url, description, linkedin_url, twitter_url,
            verified_website, verified_github
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        name,
        discovered.get("website_url"),
        discovered.get("github_repo"),
        discovered.get("github_org"),
        discovered.get("logo_url"),
        discovered.get("description"),
        discovered.get("linkedin_url"),
        discovered.get("twitter_url"),
        1 if discovered.get("verified_website") else 0,
        1 if discovered.get("verified_github") else 0,
    ))
=======
    website_url = request.form.get("website_url", "").strip()
    github_repo = request.form.get("github_repo", "").strip()

    if not name:
        return jsonify({"error": "Name is required"}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO competitors (name, website_url, github_repo) VALUES (?, ?, ?)",
        (name, website_url, github_repo)
    )
>>>>>>> a5dd5cdc1f1b83663f80ea51989a0f0dfd9737b2
    conn.commit()
    conn.close()
    return redirect(url_for("home"))

<<<<<<< HEAD

@app.route("/api/discover", methods=["POST"])
def api_discover():
    """Preview discovery results for a company name without saving them."""
    name = request.form.get("name", "")
    if not name and request.is_json:
        name = (request.get_json(silent=True) or {}).get("name", "")
    name = (name or "").strip()

    if not name:
        return jsonify({"error": "Company name is required"}), 400
    return jsonify(discover_company(name))

=======
>>>>>>> a5dd5cdc1f1b83663f80ea51989a0f0dfd9737b2
@app.route("/delete_competitor/<int:comp_id>", methods=["POST"])
def delete_competitor(comp_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM competitors WHERE id = ?", (comp_id,))
    c.execute("DELETE FROM reports WHERE competitor_id = ?", (comp_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("home"))

@app.route("/scan/<int:comp_id>", methods=["POST"])
def scan_competitor(comp_id):
    conn = get_db()
    c = conn.cursor()
    comp = c.execute("SELECT * FROM competitors WHERE id = ?", (comp_id,)).fetchone()
    conn.close()

    if not comp:
        return jsonify({"error": "Competitor not found"}), 404

    summary = run_agent_for_competitor(dict(comp))
    return jsonify({"summary": summary})

@app.route("/reports/<int:comp_id>")
def get_reports(comp_id):
    conn = get_db()
    c = conn.cursor()
    reports = c.execute(
        "SELECT * FROM reports WHERE competitor_id = ? ORDER BY created_at DESC LIMIT 5",
        (comp_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in reports])

@app.route("/api/stats")
def stats():
    conn = get_db()
    c = conn.cursor()
    total_competitors = c.execute("SELECT COUNT(*) FROM competitors").fetchone()[0]
    total_reports = c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    conn.close()
    return jsonify({
        "total_competitors": total_competitors,
        "total_reports": total_reports
    })

<<<<<<< HEAD

# ---- Intelligence Pipeline ----
# Full website + GitHub + AI competitive analysis + PDF report generation.
# Runs in a background thread so the frontend can poll for staged progress
# ("Finding Company...", "Analyzing GitHub...", etc.) instead of blocking
# on one long request.

@app.route("/analyze/<int:comp_id>", methods=["POST"])
def analyze_competitor(comp_id):
    conn = get_db()
    c = conn.cursor()
    comp = c.execute("SELECT * FROM competitors WHERE id = ?", (comp_id,)).fetchone()
    conn.close()

    if not comp:
        return jsonify({"error": "Competitor not found"}), 404

    # Avoid starting a second run if one is already in progress for this competitor
    current = get_status(comp_id)
    if current["started"] and not current["done"]:
        return jsonify({"started": True, "already_running": True})

    thread = threading.Thread(target=run_full_analysis, args=(comp_id,), daemon=True)
    thread.start()
    return jsonify({"started": True, "already_running": False})


@app.route("/analysis_status/<int:comp_id>")
def analysis_status(comp_id):
    return jsonify(get_status(comp_id))


@app.route("/analysis_result/<int:comp_id>")
def analysis_result(comp_id):
    report = get_latest_report(comp_id)
    if not report:
        return jsonify({"error": "No analysis found for this competitor yet"}), 404
    return jsonify(report)


@app.route("/download_report/<int:comp_id>")
def download_report(comp_id):
    report = get_latest_report(comp_id)
    if not report or not report.get("pdf_path") or not os.path.exists(report["pdf_path"]):
        return jsonify({"error": "No PDF report available for this competitor yet"}), 404
    return send_file(report["pdf_path"], as_attachment=True)

=======
>>>>>>> a5dd5cdc1f1b83663f80ea51989a0f0dfd9737b2
# ---- Scheduler ----

def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_all_competitors,
        trigger="cron",
        day_of_week="mon",
        hour=9,
        minute=0
    )
    scheduler.start()
    print("Scheduler started — runs every Monday 9am")

if __name__ == "__main__":
    init_db()
    start_scheduler()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
