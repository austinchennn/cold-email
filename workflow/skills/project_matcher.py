"""
Project Matching Skill
======================
Given a professor's tech stack + keywords, find the most relevant projects
from latex/project_pool/ using TF-IDF cosine similarity.

No heavy embedding model required — scikit-learn is sufficient.

Usage
-----
  matcher  = ProjectMatcherSkill()
  projects = matcher.match(
      tech_stack     = ["PyTorch", "HuggingFace"],
      keywords       = ["NLP", "low-resource", "transformer"],
      sub_directions = ["multilingual NLP", "token classification"],
      top_k          = 3,
  )
  # → [{rank, path, title, score, metadata, content}, ...]

Project .tex metadata format (comment lines at file top)
---------------------------------------------------------
  % PROJECT_TITLE:    Multilingual NER
  % PROJECT_KEYWORDS: NLP, NER, multilingual, transformer
  % PROJECT_TECH:     PyTorch, HuggingFace, Python
  % PROJECT_DURATION: 2023.09 -- 2024.05
  % PROJECT_ROLE:     Lead Researcher
"""

import re
import logging
from pathlib import Path
from typing import Dict, List, Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config.settings import PROJECT_POOL_DIR, TOP_K_PROJECTS

logger = logging.getLogger(__name__)


class ProjectMatcherSkill:
    """
    Loads every .tex / .md file from project_pool/ once, builds a TF-IDF
    matrix, then answers cosine-similarity match queries.
    """

    def __init__(self, project_pool_dir: Path = PROJECT_POOL_DIR):
        self.project_pool_dir = project_pool_dir
        self._projects: List[Dict] = []      # [{path, text, metadata}]
        self._vectorizer: Optional[TfidfVectorizer] = None
        self._matrix = None                  # sparse TF-IDF matrix
        self._loaded = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def match(
        self,
        tech_stack: List[str],
        keywords: List[str],
        sub_directions: Optional[List[str]] = None,
        top_k: int = TOP_K_PROJECTS,
    ) -> List[Dict]:
        """
        Return the top-K most relevant project dicts.

        Each result dict contains:
          rank     : int   (1 = best)
          path     : str   (absolute path to the .tex file)
          title    : str
          score    : float (cosine similarity)
          metadata : dict  (extracted from comment headers)
          content  : str   (raw file content, truncated to 2000 chars)
        """
        self._ensure_loaded()
        if not self._projects:
            logger.warning("No projects in project_pool — returning empty list.")
            return []

        query_terms = list(tech_stack) + list(keywords) + list(sub_directions or [])
        query_text  = " ".join(query_terms)

        query_vec = self._vectorizer.transform([query_text])  # type: ignore[union-attr]
        scores    = cosine_similarity(query_vec, self._matrix).flatten()

        top_indices = scores.argsort()[::-1][:top_k]

        results: List[Dict] = []
        for rank, idx in enumerate(top_indices, 1):
            proj = self._projects[idx]
            result = {
                "rank":     rank,
                "path":     str(proj["path"]),
                "title":    proj["metadata"].get("title", proj["path"].stem),
                "score":    float(scores[idx]),
                "metadata": proj["metadata"],
                "content":  proj["raw"][:2000],
            }
            results.append(result)
            logger.info(
                f"  match #{rank}  {proj['path'].name}  score={scores[idx]:.3f}"
            )
        return results

    # ── Loading & Vectorisation ────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_projects()
        if self._projects:
            self._vectorize()
        self._loaded = True

    def _load_projects(self) -> None:
        files: List[Path] = []
        for pattern in ("*.tex", "*.md"):
            files.extend(self.project_pool_dir.glob(pattern))

        if not files:
            logger.warning(f"No .tex/.md files found in {self.project_pool_dir}")
            return

        for f in sorted(files):
            raw      = f.read_text(encoding="utf-8", errors="ignore")
            metadata = self._extract_metadata(raw)
            plain    = self._strip_latex(raw)
            # Boost metadata keywords into the searchable corpus
            meta_text = " ".join(
                [
                    metadata.get("title", ""),
                    metadata.get("keywords", ""),
                    metadata.get("tech", ""),
                ]
            )
            self._projects.append(
                {
                    "path":     f,
                    "text":     f"{meta_text} {plain}",
                    "raw":      raw,
                    "metadata": metadata,
                }
            )
        logger.info(
            f"ProjectMatcherSkill: loaded {len(self._projects)} projects "
            f"from {self.project_pool_dir}"
        )

    def _vectorize(self) -> None:
        corpus = [p["text"] for p in self._projects]
        self._vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            max_df=0.95,
            min_df=1,
            stop_words="english",
            sublinear_tf=True,
        )
        self._matrix = self._vectorizer.fit_transform(corpus)

    # ── Text Utilities ─────────────────────────────────────────────────────────

    @staticmethod
    def _strip_latex(text: str) -> str:
        """Remove LaTeX markup and return readable plain text."""
        text = re.sub(r"%.*",              "",  text)            # strip comments
        text = re.sub(                                            # keep arg content
            r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])*\{([^}]*)\}", r"\1", text
        )
        text = re.sub(r"\\[a-zA-Z]+\*?",  " ", text)            # remaining commands
        text = re.sub(r"[{}]",             " ", text)            # braces
        return re.sub(r"\s+",              " ", text).strip()    # collapse whitespace

    @staticmethod
    def _extract_metadata(tex_content: str) -> Dict:
        """
        Read structured metadata from % KEY: value lines at the file top.
        """
        meta: Dict[str, str] = {}
        mapping = {
            "PROJECT_TITLE":    "title",
            "PROJECT_KEYWORDS": "keywords",
            "PROJECT_TECH":     "tech",
            "PROJECT_DURATION": "duration",
            "PROJECT_ROLE":     "role",
        }
        for line in tex_content.splitlines():
            line = line.strip()
            if not line.startswith("%"):
                continue
            for key, field in mapping.items():
                m = re.match(rf"^%\s*{key}\s*:\s*(.+)", line)
                if m:
                    meta[field] = m.group(1).strip()
        return meta
