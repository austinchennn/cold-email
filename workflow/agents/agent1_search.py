"""
Agent 1 — Professor Search Agent
==================================
Input  : research domain string (from user)
Output : data/professors/raw_list.json

Each professor entry schema
---------------------------
{
  "name":           str,
  "email":          str,          # institutional email, "" if unknown
  "university":     str,
  "department":     str,
  "research_areas": [str, ...],   # 2-4 sub-directions
  "lab_url":        str,          # "" if unknown
  "profile_url":    str           # "" if unknown
}

Strategy
--------
1. Run a web search to gather real-time professor snippets as grounding context.
2. Send the domain + context to the LLM asking for a structured JSON professor list.
3. Validate / normalise each entry.
4. Write to raw_list.json and return the list.
"""

import json
import logging
from typing import Dict, List

from config.settings import PROFESSORS_DIR, MAX_PROFESSORS
from skills.llm_client import call_llm_json
from skills.web_search import WebSearchSkill
from skills.event_bus import bus, Event, EventType

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a research assistant helping a student find professors to cold-email
for PhD / research-internship opportunities.

Given a research domain, return a JSON object with key "professors" containing
an array of up to {max_count} professors who are currently active and accepting
students.

Each professor object MUST have these fields (use "" for unknowns):
  name           : full name
  email          : institutional email
  university     : university name
  department     : department name
  research_areas : array of 2-4 concise research sub-directions
  lab_url        : lab / group page URL
  profile_url    : faculty profile page URL

Prioritise well-known, actively publishing professors at top research universities.
Return ONLY valid JSON — no markdown fences, no commentary.\
"""


class Agent1Search:
    """Discover professors active in a given research domain."""

    AGENT_ID = 1

    def __init__(self):
        self.searcher = WebSearchSkill()

    def run(self, domain: str, max_count: int = MAX_PROFESSORS,
             user_context: str = "") -> List[Dict]:
        """
        Parameters
        ----------
        domain       : e.g. "NLP", "computer vision", "robotics"
        max_count    : maximum professors to find
        user_context : optional structured text from Agent0 intake profile

        Returns
        -------
        List of professor dicts. Also written to data/professors/raw_list.json.
        """
        logger.info(f"Agent1: searching professors  domain='{domain}'  max={max_count}")
        bus.post(Event(EventType.AGENT_START, self.AGENT_ID, {"domain": domain}))

        try:
            # 1. Web search → grounding context for the LLM
            bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID, {"step": "Web search"}))
            search_context = self._search_context(domain)

            # 2. LLM structured extraction
            bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID, {"step": "LLM extraction"}))
            system_prompt = _SYSTEM_PROMPT.format(max_count=max_count)
            user_prompt   = self._build_user_prompt(domain, search_context,
                                                     max_count, user_context)
            data          = call_llm_json(system_prompt, user_prompt,
                                          agent_id=self.AGENT_ID, step="LLM extraction")

            professors: List[Dict] = data.get("professors", [])
            if not professors:
                logger.warning("Agent1: LLM returned an empty professor list.")

            # 3. Normalise & cap
            bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID, {"step": "Normalise & save JSON"}))
            professors = [self._normalise(p) for p in professors[:max_count]]

            # 4. Persist
            out_path = PROFESSORS_DIR / "raw_list.json"
            out_path.write_text(
                json.dumps(professors, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info(f"Agent1: saved {len(professors)} professors \u2192 {out_path}")
            bus.post(Event(EventType.AGENT_COMPLETE, self.AGENT_ID,
                           {"count": len(professors)}))
            return professors

        except Exception as exc:
            bus.post(Event(EventType.AGENT_ERROR, self.AGENT_ID, {"error": str(exc)}))
            raise

    # ── Private helpers ────────────────────────────────────────────────────────

    def _search_context(self, domain: str) -> str:
        results = self.searcher.search(
            f'top professors "{domain}" research university faculty lab',
            num_results=8,
        )
        snippets = [
            f"[{r['title']}] {r['snippet']}"
            for r in results
            if r.get("snippet")
        ]
        return "\n".join(snippets[:6])  # keep token budget small

    @staticmethod
    def _build_user_prompt(domain: str, context: str, max_count: int,
                           user_context: str = "") -> str:
        parts = [
            f"Research domain: {domain}",
            f"Find up to {max_count} professors.",
        ]
        if user_context:
            parts.append(
                f"\nApplicant profile & preferences (use to refine search):\n{user_context}"
            )
        if context:
            parts.append(
                f"\nWeb search context (use as hints, verify with your training knowledge):\n{context}"
            )
        parts.append("\nReturn the list as JSON.")
        return "\n".join(parts)

    @staticmethod
    def _normalise(p: Dict) -> Dict:
        return {
            "name":           str(p.get("name",           "")).strip(),
            "email":          str(p.get("email",          "")).strip(),
            "university":     str(p.get("university",     "")).strip(),
            "department":     str(p.get("department",     "")).strip(),
            "research_areas": p.get("research_areas",     []),
            "lab_url":        str(p.get("lab_url",        "")).strip(),
            "profile_url":    str(p.get("profile_url",    "")).strip(),
        }
