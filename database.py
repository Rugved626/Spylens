import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "spylens.db")

def get_db():
<<<<<<< HEAD
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
=======
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
>>>>>>> a5dd5cdc1f1b83663f80ea51989a0f0dfd9737b2
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    # competitors table
    c.execute('''
        CREATE TABLE IF NOT EXISTS competitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            website_url TEXT,
            github_repo TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # reports table - stores weekly digest per competitor
    c.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER,
            report_type TEXT,
            summary TEXT,
            raw_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (competitor_id) REFERENCES competitors(id)
        )
    ''')

<<<<<<< HEAD
    # company_discovery_cache table - caches auto-discovered company info
    # so repeated lookups for the same company name skip external API calls.
    c.execute('''
        CREATE TABLE IF NOT EXISTS company_discovery_cache (
            name_lower TEXT PRIMARY KEY,
            data TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # analysis_status table - tracks the live progress of the full
    # intelligence pipeline (website + github + AI + PDF) per competitor,
    # so the frontend can poll and show staged loading messages.
    c.execute('''
        CREATE TABLE IF NOT EXISTS analysis_status (
            competitor_id INTEGER PRIMARY KEY,
            stage TEXT,
            done INTEGER DEFAULT 0,
            error TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (competitor_id) REFERENCES competitors(id)
        )
    ''')

    # intelligence_reports table - stores the latest full intelligence
    # bundle (website, github, AI competitive analysis) + generated PDF
    # path per competitor. Overwritten on each new "Full Analysis" run.
    c.execute('''
        CREATE TABLE IF NOT EXISTS intelligence_reports (
            competitor_id INTEGER PRIMARY KEY,
            github_data TEXT,
            website_data TEXT,
            ai_data TEXT,
            pdf_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (competitor_id) REFERENCES competitors(id)
        )
    ''')

    conn.commit()

    # ---- Migration: add discovery-related columns to existing competitors
    # tables created before this feature existed. SQLite has no
    # "ADD COLUMN IF NOT EXISTS", so we check pragma info first.
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(competitors)").fetchall()}
    new_columns = {
        "github_org": "TEXT",
        "logo_url": "TEXT",
        "description": "TEXT",
        "linkedin_url": "TEXT",
        "twitter_url": "TEXT",
        "verified_website": "INTEGER DEFAULT 0",
        "verified_github": "INTEGER DEFAULT 0",
    }
    for col_name, col_type in new_columns.items():
        if col_name not in existing_cols:
            c.execute(f"ALTER TABLE competitors ADD COLUMN {col_name} {col_type}")

=======
>>>>>>> a5dd5cdc1f1b83663f80ea51989a0f0dfd9737b2
    conn.commit()
    conn.close()
    print("DB initialized.")
