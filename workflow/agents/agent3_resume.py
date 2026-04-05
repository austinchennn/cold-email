"""
Agent 3 — Resume Adaptation Agent
====================================
Two-step execution:

  Step 1 — Match  : Call ProjectMatcherSkill to find the top-K projects from
                    latex/project_pool/ most relevant to the professor's work.

  Step 2 — Fill   : Send matched project content + professor context to the LLM.
                    LLM rewrites bullet points to highlight relevant skills, then
                    we inject the result into the LaTeX resume template.

Input  : professor research dict from Agent2
Output : outputs/tailored_resumes/{slug}_resume.tex
"""

import logging
import re
from pathlib import Path
from typing import Dict, List

from config.settings import RESUME_TEMPLATE_DIR, TAILORED_RESUMES_DIR, TOP_K_PROJECTS
from skills.llm_client import call_llm
from skills.project_matcher import ProjectMatcherSkill
from skills.latex_utils import LatexUtils
from skills.event_bus import bus, Event, EventType

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an expert LaTeX resume writer for a PhD/research-internship applicant.

You will receive:
  1. A professor's research context (directions, tech stack, keywords).
  2. Raw LaTeX content for several of the applicant's projects.

Your task:
  - Rewrite EACH project's \\resumeItem bullet points to emphasise the skills,
    tools, and methods that directly align with the professor's research.
  - Do NOT invent new facts. Only re-emphasise and re-word existing content.
  - Use active verbs and quantify impact wherever numbers already exist.
  - Preserve the exact LaTeX command structure:
      \\resumeProjectHeading{\\textbf{Title} $|$ \\emph{Tech}}{Duration}
        \\resumeItemListStart
          \\resumeItem{...}
          ...
        \\resumeItemListEnd
  - If you are rewriting multiple projects, separate them with a single blank line.
  - Return ONLY raw LaTeX code — no markdown code fences, no explanations.\
"""


class Agent3Resume:
    """Match projects and produce a tailored LaTeX resume for a specific professor."""

    AGENT_ID = 3

    def __init__(self):
        self.matcher = ProjectMatcherSkill()
        self.latex   = LatexUtils()

    def run(self, professor_research: Dict) -> str:
        """
        Parameters
        ----------
        professor_research : enriched dict from Agent2

        Returns
        -------
        Absolute path (str) to the generated .tex resume file.
        """
        slug = professor_research.get("slug", "unknown")
        name = professor_research.get("name", slug)
        logger.info(f"Agent3: tailoring resume for  {name}")

        # ── Step 1: Project matching ─────────────────────────────────────────
        matched = self.matcher.match(
            tech_stack     = professor_research.get("tech_stack", []),
            keywords       = professor_research.get("keywords", []),
            sub_directions = professor_research.get("sub_directions", []),
            top_k          = TOP_K_PROJECTS,
        )

        if not matched:
            logger.warning(
                "Agent3: no matching projects found — "
                "resume projects section will be empty."
            )

        # ── Step 2: LLM rewrites project bullets ─────────────────────────────
        project_blocks = self._rewrite_projects(professor_research, matched)

        # ── Step 3: Inject blocks into the LaTeX template ───────────────────
        template = self.latex.read_template(RESUME_TEMPLATE_DIR)
        filled   = self.latex.inject_projects(template, project_blocks)

        # ── Step 4: Persist ──────────────────────────────────────────────────
        out_path = TAILORED_RESUMES_DIR / f"{slug}_resume.tex"
        out_path.write_text(filled, encoding="utf-8")
        logger.info(f"Agent3: saved resume → {out_path}")
        return str(out_path)

    # ── Private helpers ────────────────────────────────────────────────────────

    def _rewrite_projects(
        self,
        professor_research: Dict,
        matched_projects: List[Dict],
    ) -> List[str]:
        if not matched_projects:
            return ["% No projects matched for this professor."]

        # Professor context block fed to the LLM
        prof_ctx = (
            f"Professor:           {professor_research.get('name')}\n"
            f"Sub-directions:      {', '.join(professor_research.get('sub_directions', []))}\n"
            f"Tech stack:          {', '.join(professor_research.get('tech_stack', []))}\n"
            f"Keywords:            {', '.join(professor_research.get('keywords', []))}"
        )

        # Raw project content (capped per project to stay within token limits)
        raw_projects = "\n\n---PROJECT SEPARATOR---\n\n".join(
            f"Project #{p['rank']}  ({p['title']})\n{p['content'][:1800]}"
            for p in matched_projects
        )

        user_prompt = (
            f"Professor research context:\n{prof_ctx}\n\n"
            f"Applicant's raw project content:\n{raw_projects}\n\n"
            "Rewrite the projects as LaTeX resume blocks targeting this professor's focus."
        )

        latex_output = call_llm(_SYSTEM_PROMPT, user_prompt, temperature=0.35,
                                 agent_id=self.AGENT_ID, step="LLM rewrite bullets")

        # Projects may be returned separated by the separator or just blank lines
        raw_blocks = re.split(
            r"\n---PROJECT SEPARATOR---\n|\n{2,}(?=\\resumeProjectHeading)",
            latex_output,
        )
        blocks = [b.strip() for b in raw_blocks if b.strip()]
        return blocks if blocks else [latex_output]
