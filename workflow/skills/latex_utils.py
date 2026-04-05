"""
LaTeX Utilities
===============
Helpers for reading the resume template and injecting matched project blocks.

Injection protocol
------------------
The resume template must contain exactly these two marker lines:

  %%PROJECTS_BEGIN%%
  %%PROJECTS_END%%

Agent3 replaces everything between the markers with the rewritten project
\resumeProjectHeading{...}{...} blocks it received from the LLM.

Placeholder substitution
------------------------
{{STUDENT_NAME}}, {{EMAIL}}, etc. in the template are replaced via
replace_placeholders() using a simple dict lookup.
"""

import re
import logging
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

PROJECT_START_MARKER = "%%PROJECTS_BEGIN%%"
PROJECT_END_MARKER   = "%%PROJECTS_END%%"


class LatexUtils:

    @staticmethod
    def read_template(template_dir: Path) -> str:
        """Return the content of the first .tex file found in template_dir."""
        candidates = sorted(template_dir.glob("*.tex"))
        if not candidates:
            raise FileNotFoundError(f"No .tex template found in {template_dir}")
        return candidates[0].read_text(encoding="utf-8")

    @staticmethod
    def inject_projects(template: str, project_latex_blocks: List[str]) -> str:
        """
        Replace everything between %%PROJECTS_BEGIN%% and %%PROJECTS_END%%
        with the supplied LaTeX project entry blocks.
        """
        joined  = "\n\n".join(project_latex_blocks)
        pattern = (
            rf"({re.escape(PROJECT_START_MARKER)})"
            rf".*?"
            rf"({re.escape(PROJECT_END_MARKER)})"
        )
        # Use a lambda so backslashes in LaTeX content are never misinterpreted
        # as regex backreferences (e.g. \emph, \resumeItem, etc.)
        result = re.sub(
            pattern,
            lambda m: m.group(1) + "\n" + joined + "\n" + m.group(2),
            template,
            flags=re.DOTALL,
        )

        if result == template:
            logger.warning(
                f"Markers not found in template. "
                f"Appending projects at end of document."
            )
            result = template + "\n\n% Injected Projects\n" + joined

        return result

    @staticmethod
    def replace_placeholders(template: str, replacements: Dict[str, str]) -> str:
        """Replace {{KEY}} style placeholders with provided values."""
        for key, value in replacements.items():
            template = template.replace(f"{{{{{key}}}}}", value)
        return template

    @staticmethod
    def strip_latex(text: str) -> str:
        """Return plain text with LaTeX markup removed."""
        text = re.sub(r"%.*",              "",  text)
        text = re.sub(
            r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])*\{([^}]*)\}", r"\1", text
        )
        text = re.sub(r"\\[a-zA-Z]+\*?",  " ", text)
        text = re.sub(r"[{}]",             " ", text)
        return re.sub(r"\s+",              " ", text).strip()
