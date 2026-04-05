"""
Agent 4 — Cold Email Writing Agent
=====================================
Input  : professor research dict (from Agent2) + path to tailored resume (Agent3)
Output : outputs/emails/{slug}_email.txt

The output file starts with a "SUBJECT: ..." line, followed by the email body.
"""

import logging
from pathlib import Path
from typing import Dict

from config.settings import EMAILS_DIR
from skills.llm_client import call_llm
from skills.event_bus import bus, Event, EventType

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an expert at writing cold outreach emails for PhD / research-internship
applications.

Guidelines
----------
Subject line
  - Concise; reference the professor's specific research area or a paper title.
  - Do NOT start with "I am writing to express my interest in..."

Opening (1 sentence)
  - Demonstrate genuine familiarity with their work:
    cite a specific paper, technique, or sub-direction — NOT generic praise.

Body (2-3 short paragraphs)
  1. Who you are + your single most relevant project/experience (2-3 sentences).
  2. Concrete alignment: link YOUR skills/projects to THEIR current research
     using specific technical terms from their work.
  3. Clear ask: express intent to join their group (PhD / internship) and
     mention a specific upcoming application deadline if known.

Closing
  - Professional.
  - One sentence noting that resume is attached as a PDF.
  - Leave a "[Your Name]" placeholder for the signature.

Tone   : confident, respectful, technically fluent — not sycophantic.
Length : ≤ 300 words for the body. Do NOT exceed this.

Output format
  SUBJECT: <subject line>

  <email body>

  [Your Name]
  [Your Email]
  [GitHub / Website]\
"""


class Agent4Email:
    """Write a personalised cold email for a specific professor."""

    AGENT_ID = 4

    def run(self, professor_research: Dict, resume_path: str) -> str:
        """
        Parameters
        ----------
        professor_research : enriched dict from Agent2
        resume_path        : path to the tailored resume .tex file (Agent3 output)

        Returns
        -------
        Absolute path (str) to the generated email .txt file.
        """
        slug = professor_research.get("slug", "unknown")
        name = professor_research.get("name", slug)
        logger.info(f"Agent4: writing cold email for  {name}")
        bus.post(Event(EventType.AGENT_START, self.AGENT_ID, {"professor": name}))

        try:
            bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID,
                           {"step": "Build professor context"}))
            user_prompt = self._build_user_prompt(professor_research, resume_path)

            bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID,
                           {"step": "Generate cold email (LLM)"}))
            email_text = call_llm(_SYSTEM_PROMPT, user_prompt, temperature=0.55,
                                  agent_id=self.AGENT_ID, step="Generate cold email")

            bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID,
                           {"step": "Save email .txt"}))
            out_path = EMAILS_DIR / f"{slug}_email.txt"
            out_path.write_text(email_text, encoding="utf-8")
            logger.info(f"Agent4: saved email \u2192 {out_path}")
            bus.post(Event(EventType.AGENT_COMPLETE, self.AGENT_ID, {"path": str(out_path)}))
            return str(out_path)

        except Exception as exc:
            bus.post(Event(EventType.AGENT_ERROR, self.AGENT_ID, {"error": str(exc)}))
            raise

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _build_user_prompt(research: Dict, resume_path: str) -> str:
        papers_block = "\n".join(
            f"  - {p}" for p in research.get("recent_papers", [])
        ) or "  (not available)"

        return f"""\
Professor details
  Name:             {research.get("name")}
  University:       {research.get("university")}
  Department:       {research.get("exact_department", research.get("department", ""))}
  Sub-directions:   {", ".join(research.get("sub_directions", []))}
  Tech stack:       {", ".join(research.get("tech_stack", []))}
  Keywords:         {", ".join(research.get("keywords", []))}
  Recent papers:
{papers_block}

Applicant interest paragraph (from research analysis):
{research.get("interest_paragraph", "")}

Attached resume filename: {Path(resume_path).stem}.pdf

Write the cold email now.\
"""
