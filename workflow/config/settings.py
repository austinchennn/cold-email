"""
Global configuration — loaded once at import time.
All agents and skills import from here.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

# ── LLM ───────────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str  = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY: str  = os.getenv("GEMINI_API_KEY", "")  # takes priority if set
LLM_MODEL: str       = os.getenv("LLM_MODEL", "gpt-4o")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))
MAX_RETRIES: int     = 3

# ── Optional search APIs ───────────────────────────────────────────────────────
TAVILY_API_KEY: str  = os.getenv("TAVILY_API_KEY", "")

# ── Directory layout ───────────────────────────────────────────────────────────
DATA_DIR             = BASE_DIR / "data"
PROFESSORS_DIR       = DATA_DIR / "professors"
DEEP_RESEARCH_DIR    = PROFESSORS_DIR / "deep_research"

LATEX_DIR            = BASE_DIR / "latex"
RESUME_TEMPLATE_DIR  = LATEX_DIR / "resume_template"
PROJECT_POOL_DIR     = LATEX_DIR / "project_pool"

OUTPUTS_DIR          = BASE_DIR / "outputs"
TAILORED_RESUMES_DIR = OUTPUTS_DIR / "tailored_resumes"
EMAILS_DIR           = OUTPUTS_DIR / "emails"

# ── Agent behaviour ────────────────────────────────────────────────────────────
MAX_PROFESSORS: int  = int(os.getenv("MAX_PROFESSORS", "10"))
TOP_K_PROJECTS: int  = int(os.getenv("TOP_K_PROJECTS", "3"))
REQUEST_TIMEOUT: int = 15       # seconds for outbound HTTP
MAX_WEBPAGE_CHARS: int = 8_000  # chars fed to LLM per scraped page

# ── Gmail (Agent 5) ────────────────────────────────────────────────────────────
# Set GMAIL_ENABLED=true only when you are ready to actually send.
# Leave false (default) for a dry-run that generates files but sends nothing.
GMAIL_ENABLED: bool      = os.getenv("GMAIL_ENABLED", "false").lower() == "true"
GMAIL_CREDENTIALS_PATH   = BASE_DIR / os.getenv(
    "GMAIL_CREDENTIALS_PATH", "config/credentials.json"
)
GMAIL_TOKEN_PATH         = BASE_DIR / os.getenv(
    "GMAIL_TOKEN_PATH", "config/gmail_token.json"
)
# Safe daily send limit to avoid account penalties (Gmail free: ≤500; workspace: ≤2000)
GMAIL_DAILY_LIMIT: int   = int(os.getenv("GMAIL_DAILY_LIMIT", "50"))
# Random inter-email delay range (seconds) — reduces spam-filter risk
GMAIL_DELAY_MIN: float   = float(os.getenv("GMAIL_DELAY_MIN", "60"))
GMAIL_DELAY_MAX: float   = float(os.getenv("GMAIL_DELAY_MAX", "120"))
# Days before sending a follow-up if no reply (0 = no follow-up)
GMAIL_FOLLOW_UP_DAYS: int = int(os.getenv("GMAIL_FOLLOW_UP_DAYS", "3"))

# ── Send status tracker DB ─────────────────────────────────────────────────────
SEND_TRACKER_DB = DATA_DIR / "send_status.db"

# ── Auto-create writable directories ──────────────────────────────────────────
for _d in [PROFESSORS_DIR, DEEP_RESEARCH_DIR, TAILORED_RESUMES_DIR, EMAILS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)
