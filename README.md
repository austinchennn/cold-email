<p align="center">
  <img src="media/9e4decdd9a339a09d53083e4b1e750e9.jpg" width="200" alt="Cold Email Client Logo"/>
</p>

<h1 align="center">Cold Email Client</h1>

<p align="center">
  An AI-powered multi-agent pipeline that discovers professors, tailors your resume, and sends personalized cold emails — fully automated.
</p>

---

## Overview

Cold Email Client runs a 5-agent sequential workflow:

```
Research Domain (user input)
        ↓
[Agent 1] Web Search + LLM  →  professor list
        ↓
[Agent 2] Scrape + LLM      →  deep research profile (per professor)
        ↓
[Agent 3] TF-IDF + LLM      →  tailored LaTeX resume
        ↓
[Agent 4] LLM               →  personalized cold email
        ↓
[Agent 5] Gmail API         →  send + track in SQLite
```

A live terminal dashboard (built with [Textual](https://github.com/Textualize/textual)) shows real-time progress across all agents, with a built-in reasoning inspector for every LLM call.

---

## Features

- **Automated professor discovery** via Tavily search API (falls back to DuckDuckGo)
- **Deep research** — scrapes lab pages, extracts research directions, keywords, and tech stack via LLM
- **Resume tailoring** — TF-IDF cosine similarity selects best-matching projects; LLM rewrites bullet points to match professor's work
- **Personalized emails** — LLM composes cold emails grounded in the professor's actual research
- **Gmail integration** — OAuth 2.0 send with rate limiting, exponential backoff, and follow-up scheduling
- **Send tracker** — SQLite ledger prevents duplicate sends and tracks reply status
- **TUI dashboard** — real-time agent status, step progress, and LLM reasoning inspector

---

## Tech Stack

| Category | Library |
|---|---|
| LLM | `openai` (GPT-4o / Gemini) |
| Web search | Tavily API, `duckduckgo-search` |
| Scraping | `requests`, `beautifulsoup4` |
| NLP | `scikit-learn` (TF-IDF + cosine similarity) |
| Email | Gmail API (`google-api-python-client`) |
| Retry | `backoff` |
| TUI | `textual` |
| Storage | SQLite (`sqlite3`) |
| Config | `python-dotenv` |

---

## Project Structure

```
cold-email/
├── workflow/
│   ├── agents/          # Agent 0–5 pipeline logic
│   ├── skills/          # Reusable components (LLM, search, Gmail, etc.)
│   ├── config/          # Settings and domain config
│   ├── data/            # Professor profiles and user profile
│   ├── latex/           # Resume template and project pool (.tex)
│   ├── outputs/         # Generated emails and tailored resumes
│   ├── main.py          # Full pipeline (Agents 1–5)
│   ├── run_research.py  # Research only (Agents 1–2)
│   ├── run_email.py     # Email only (Agents 3–5)
│   ├── run_intake.py    # Interview + research (Agent 0 → 1 → 2)
│   └── dashboard.py     # Live TUI dashboard
└── tests/               # Unit + integration tests (131 tests)
```

---

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/austinchennn/cold-email.git
cd cold-email/workflow
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
OPENAI_API_KEY=sk-...          # required (or use GEMINI_API_KEY)
TAVILY_API_KEY=tvly-...        # optional, enables better search
GMAIL_ENABLED=false            # set true to actually send emails
```

### 3. Add your resume projects

Place your LaTeX project blocks in `latex/project_pool/project_01.tex`, `project_02.tex`, etc. Each file should contain one `\resumeProjectHeading{...}` block.

Add your resume template to `latex/resume_template/template.tex` with `%%PROJECTS_BEGIN%%` and `%%PROJECTS_END%%` markers.

### 4. (Optional) Set up Gmail sending

1. Create a Google Cloud project and enable the Gmail API
2. Download OAuth credentials and save as `workflow/config/credentials.json`
3. Set `GMAIL_ENABLED=true` in `.env`
4. On first run, a browser window will open for OAuth authorization

---

## Usage

### Full pipeline (interactive)

```bash
cd workflow
python run_intake.py
```

Starts a chat interview to collect your research interests, then runs the full pipeline.

### Research only

```bash
python run_research.py
# or with domain argument:
python run_research.py --domain "robot learning"
```

### Email generation + send (requires existing research data)

```bash
python run_email.py
# or for a specific professor:
python run_email.py --slug tim_barfoot
```

### Live dashboard

```bash
python dashboard.py
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | OpenAI API key (required unless using Gemini) |
| `GEMINI_API_KEY` | — | Google Gemini API key (takes priority over OpenAI) |
| `LLM_MODEL` | `gpt-4o` | Model name |
| `LLM_TEMPERATURE` | `0.3` | Generation temperature |
| `TAVILY_API_KEY` | — | Tavily search API key (optional) |
| `MAX_PROFESSORS` | `10` | Max professors to discover per run |
| `TOP_K_PROJECTS` | `3` | Number of resume projects to inject |
| `GMAIL_ENABLED` | `false` | Enable actual Gmail sending |
| `GMAIL_DAILY_LIMIT` | `50` | Max emails per day |
| `GMAIL_DELAY_MIN` | `60` | Min seconds between sends |
| `GMAIL_DELAY_MAX` | `120` | Max seconds between sends |
| `GMAIL_FOLLOW_UP_DAYS` | `3` | Days before follow-up |

---

## License

MIT
