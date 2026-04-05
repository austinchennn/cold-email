"""
Agent 2 — Professor Deep Research Agent
=========================================
Input  : one professor dict from raw_list.json
Output : data/professors/deep_research/{slug}_prof.json

Output schema
-------------
{
  "name":               str,
  "slug":               str,   # snake_case identifier used as filename stem
  "university":         str,
  "exact_department":   str,
  "sub_directions":     [str, ...],   # 3-5 specific sub-topics
  "tech_stack":         [str, ...],   # frameworks / tools the professor uses
  "keywords":           [str, ...],   # 5-10 technical keywords
  "recent_papers":      [str, ...],   # 2-3 representative paper titles
  "interest_paragraph": str           # first-person paragraph (applicant's voice)
}

Strategy
--------
1. Scrape the professor's lab_url and profile_url (if available).
2. Fallback: web-search for their name + recent publications.
3. Send everything to the LLM for structured extraction + interest paragraph.
4. Write to deep_research/{slug}_prof.json and return the dict.
"""

import json
import logging
import re
from typing import Dict, List

from config.settings import DEEP_RESEARCH_DIR
from skills.llm_client import call_llm_json
from skills.web_search import WebSearchSkill
from skills.event_bus import bus, Event, EventType

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a technical research analyst helping a PhD applicant understand a
potential supervisor deeply.

Given a professor's basic info and optionally their lab/profile page content,
return a JSON object with exactly these fields:

  name              : professor's full name
  slug              : lowercase snake_case ID from the name, e.g. "alice_johnson"
  university        : university name
  exact_department  : precise department, e.g. "Electrical Engineering & CS"
  sub_directions    : array of 3-5 specific research sub-topics
  tech_stack        : array of frameworks/tools the professor's group actively uses
  keywords          : array of 5-10 technical keywords that characterise their work
  recent_papers     : array of 2-3 recent representative paper titles (best guesses OK)
  interest_paragraph: 2-3 sentence paragraph, FIRST PERSON (applicant's voice),
                      explaining WHY the applicant is specifically interested in THIS
                      professor's work. Reference real sub-directions, tools, or
                      specific paper topics. Avoid generic phrases like "I am very
                      interested in your fascinating research."

Return ONLY valid JSON — no markdown fences, no commentary.\
"""


class Agent2Research:
    """Deep-dive research on one professor."""

    AGENT_ID = 2

    def __init__(self):
        self.searcher = WebSearchSkill()

    def run(self, professor: Dict, user_context: str = "") -> Dict:
        """
        Parameters
        ----------
        professor    : dict from raw_list.json
        user_context : optional structured text from Agent0 intake profile

        Returns
        -------
        Enriched research dict. Also written to deep_research/{slug}_prof.json.
        """
        name = professor.get("name", "Unknown")
        logger.info(f"Agent2: deep research on  {name}")

        # 1. Gather page content
        page_context = self._gather_context(professor)

        # 2. LLM extraction
        user_prompt = self._build_user_prompt(professor, page_context, user_context)
        data        = call_llm_json(_SYSTEM_PROMPT, user_prompt)

        # 3. Ensure slug present
        if not data.get("slug"):
            data["slug"] = _make_slug(name)

        # 4. Persist
        slug     = data["slug"]
        out_path = DEEP_RESEARCH_DIR / f"{slug}_prof.json"
        out_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info(f"Agent2: saved → {out_path}")
        return data

    # ── Private helpers ────────────────────────────────────────────────────────

    def _gather_context(self, professor: Dict) -> str:
        """
        Try lab_url and profile_url first.
        If both fail, fall back to a web search for the professor's name.
        """
        texts: List[str] = []

        for url_key in ("lab_url", "profile_url"):
            url = professor.get(url_key, "")
            if url:
                content = self.searcher.fetch_page(url)
                if content:
                    texts.append(f"[Source: {url}]\n{content}")

        if not texts:
            name    = professor.get("name", "")
            results = self.searcher.search(
                f'"{name}" professor lab publications research topics',
                num_results=5,
            )
            texts = [
                f"[{r['title']}] {r['snippet']}"
                for r in results
                if r.get("snippet")
            ]

        return "\n\n".join(texts)[:6000]  # keep within LLM context budget

    @staticmethod
    def _build_user_prompt(professor: Dict, page_context: str,
                           user_context: str = "") -> str:
        basic = json.dumps(professor, ensure_ascii=False, indent=2)
        parts = [f"Professor basic info:\n{basic}"]
        if user_context:
            parts.append(
                f"\nApplicant profile (tailor the interest_paragraph to this person):\n"
                f"{user_context}"
            )
        if page_context:
            parts.append(f"\nScraped web context:\n{page_context}")
        parts.append("\nReturn the enriched research profile as JSON.")
        return "\n".join(parts)


def _make_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_")
