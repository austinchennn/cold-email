#!/usr/bin/env python3
"""
Cold Email Workflow — Main Orchestrator
========================================

Usage
-----
  cd workflow
  python main.py

Linear execution order
----------------------
  1. User inputs research domain (and optional professor count).
  2. Agent1  →  discovers professors  →  data/professors/raw_list.json
  3. For each professor (one at a time):
       Agent2  →  deep research       →  data/professors/deep_research/{slug}_prof.json
       Agent3  →  tailored resume     →  outputs/tailored_resumes/{slug}_resume.tex
       Agent4  →  cold email draft    →  outputs/emails/{slug}_email.txt
       Agent5  →  send via Gmail      →  Gmail Sent + data/send_status.db

Prerequisites
-------------
  pip install -r requirements.txt
  cp .env.example .env   # fill in OPENAI_API_KEY
  # To actually send: set GMAIL_ENABLED=true, add config/gmail_credentials.json
"""

import logging
import sys
from pathlib import Path

from config.settings import MAX_PROFESSORS, TAILORED_RESUMES_DIR, EMAILS_DIR, GMAIL_ENABLED
from agents.agent1_search   import Agent1Search
from agents.agent2_research import Agent2Research
from agents.agent3_resume   import Agent3Resume
from agents.agent4_email    import Agent4Email
from agents.agent5_send     import Agent5Send

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(name)-22s]  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

# ─────────────────────────────────────────────────────────────────────────────
_SEP  = "─" * 62
_SEP2 = "═" * 62


def run_workflow(domain: str, max_professors: int = MAX_PROFESSORS) -> None:

    # ── Agent 1 ──────────────────────────────────────────────────────────────
    print(f"\n{_SEP}")
    print(f"  AGENT 1 — Professor Discovery")
    print(f"  Domain : {domain}   Max : {max_professors}")
    print(_SEP)

    professors = Agent1Search().run(domain, max_count=max_professors)
    print(f"  Found {len(professors)} professors.\n")

    if not professors:
        logger.error("No professors found. Exiting.")
        return

    results = []

    # ── Per-professor loop ────────────────────────────────────────────────────
    for idx, professor in enumerate(professors, 1):
        name = professor.get("name", "Unknown")

        print(f"\n{_SEP}")
        print(f"  [{idx:02d}/{len(professors):02d}]  {name}")
        print(f"  {professor.get('university', '')}  ·  {professor.get('department', '')}")
        print(_SEP)

        # Agent 2 — deep research
        print("  → Agent 2 : Deep research …")
        research = Agent2Research().run(professor)

        # Agent 3 — tailored resume  (internally calls ProjectMatcherSkill)
        print("  → Agent 3 : Tailoring resume …")
        resume_path = Agent3Resume().run(research)

        # Agent 4 — cold email
        print("  → Agent 4 : Writing cold email …")
        email_path  = Agent4Email().run(research, resume_path)

        # Agent 5 — Gmail send
        send_label = "LIVE SEND" if GMAIL_ENABLED else "dry-run (GMAIL_ENABLED=false)"
        print(f"  \u2192 Agent 5 : Gmail send ({send_label}) \u2026")
        gmail_id = Agent5Send().run(research, email_path)

        rel_resume = Path(resume_path).relative_to(Path(__file__).parent)
        rel_email  = Path(email_path).relative_to(Path(__file__).parent)
        print(f"  \u2713  Resume  : {rel_resume}")
        print(f"  \u2713  Email   : {rel_email}")
        if gmail_id:
            print(f"  \u2713  Sent    : gmail_id={gmail_id}")

        results.append(
            {"name": name, "resume": str(rel_resume),
             "email": str(rel_email), "gmail_id": gmail_id}
        )

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
