#!/usr/bin/env python3
"""
Cold Email Workflow — Main Orchestrator
========================================

Usage
-----
  cd workflow
  python main.py

Execution order (see graph/pipeline.py for the LangGraph definition)
-------------------------------------------------------------------
  1. User inputs research domain (and optional professor count).
  2. search node        →  Agent1 discovers professors  →  data/professors/raw_list.json
  3. Per professor (fan-out branch):
       Agent2  →  deep research       →  data/professors/deep_research/{slug}_prof.json
       Agent3  →  tailored resume     →  outputs/tailored_resumes/{slug}_resume.tex
       Agent4  →  cold email draft    →  outputs/emails/{slug}_email.txt
       Agent5  →  send via Gmail      →  Gmail Sent + data/send_status.db

Prerequisites
-------------
  pip install -r requirements.txt
  cp .env.example .env   # fill in OPENAI_API_KEY / GEMINI_API_KEY
  # To actually send: set GMAIL_ENABLED=true, add config/gmail_credentials.json
"""

import logging
import sys
from pathlib import Path

from config.settings import MAX_PROFESSORS, TAILORED_RESUMES_DIR, EMAILS_DIR, GMAIL_ENABLED
from graph import run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(name)-22s]  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

# ─────────────────────────────────────────────────────────────────────────────
_SEP  = "─" * 62
_SEP2 = "═" * 62


def _rel(path: str) -> str:
    try:
        return str(Path(path).relative_to(Path(__file__).parent))
    except (ValueError, TypeError):
        return str(path)


def run_workflow(domain: str, max_professors: int = MAX_PROFESSORS) -> None:

    print(f"\n{_SEP}")
    print("  Cold-email pipeline  (LangGraph)")
    print(f"  Domain : {domain}   Max : {max_professors}")
    print(_SEP)

    results = run_pipeline("full", domain=domain, max_professors=max_professors)

    if not results:
        logger.error("No professors processed. Exiting.")
        return

    for idx, r in enumerate(results, 1):
        print(f"\n{_SEP}")
        print(f"  [{idx:02d}/{len(results):02d}]  {r.get('name', 'Unknown')}")
        if r.get("_error"):
            print(f"  x  research failed: {r['_error']}")
            continue
        print(f"  ok  Resume : {_rel(r.get('resume', ''))}")
        print(f"  ok  Email  : {_rel(r.get('email', ''))}")
        if r.get("gmail_id"):
            print(f"  ok  Sent   : gmail_id={r['gmail_id']}")

    # ── Summary ───────────────────────────────────────────────────────────────
    sent_count = sum(1 for r in results if r.get("gmail_id"))
    print(f"\n{_SEP2}")
    print(f"  DONE — processed {len(results)} professor(s)")
    print(f"  Resumes : {TAILORED_RESUMES_DIR}")
    print(f"  Emails  : {EMAILS_DIR}")
    if GMAIL_ENABLED:
        print(f"  Sent    : {sent_count}/{len(results)} emails dispatched via Gmail")
    else:
        print("  Send    : dry-run — set GMAIL_ENABLED=true to send real emails")
    print(_SEP2 + "\n")


def _prompt_int(prompt: str, default: int) -> int:
    raw = input(prompt).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"  Invalid input — using default ({default}).")
        return default


def main() -> None:
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║      Cold Email Workflow  ·  Multi-Agent AI Pipeline      ║")
    print("╚══════════════════════════════════════════════════════════╝\n")

    domain = input("请输入研究领域（e.g. NLP / computer vision / robotics）：").strip()
    if not domain:
        print("错误：请输入有效的研究领域。")
        sys.exit(1)

    max_count = _prompt_int(
        f"最多搜集几位导师？（直接回车 = {MAX_PROFESSORS}）：",
        default=MAX_PROFESSORS,
    )

    run_workflow(domain, max_professors=max_count)


if __name__ == "__main__":
    main()
