# 🔍 SpyLens — Competitor Intelligence for Indian Startups

SpyLens is an agentic AI tool that automatically monitors your competitors every week — tracking GitHub commits and website changes — and delivers a clear, actionable intelligence digest.

---

## What it does

Add any competitor → SpyLens scans their:
- **GitHub** — what are they shipping this week?
- **Website** — how is their positioning changing?

Then Groq AI summarizes everything into a clean weekly report with **opportunities for you**.

---

## Tech Stack

- **Python + Flask** — backend + dashboard
- **Groq (LLaMA 3)** — AI summarization
- **GitHub REST API** — commit tracking
- **BeautifulSoup** — website scraping
- **APScheduler** — weekly auto-scans
- **SQLite** — data storage
- **Docker** — containerized deployment

---

## Run Locally

```bash
git clone https://github.com/Rugved626/spylens
cd spylens

cp .env.example .env
# Add your GROQ_API_KEY and GH_TOKEN in .env

pip install -r requirements.txt
python app.py
```

Open: `http://localhost:5000`

---

## Run with Docker

```bash
docker build -t spylens .
docker run -p 5000:5000 \
  -e GROQ_API_KEY=your_key \
  -e GH_TOKEN=your_token \
  spylens
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | Get free at console.groq.com |
| `GH_TOKEN` | GitHub personal access token |
| `SECRET_KEY` | Any random string |
| `PORT` | Default 5000 |

---

Built by Rugved — exploring Agentic AI for real startup problems.
