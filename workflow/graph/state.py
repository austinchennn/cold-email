"""
Shared state for the cold-email LangGraph pipeline.

The graph fans out over professors: the `search` (or `load_research`) node
produces `professors`, then one branch per professor runs the remaining agents.
Each branch only ever writes `results`, which is reduced with list concat, so
the branches never collide on a shared key.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, TypedDict


class PipelineState(TypedDict, total=False):
    # ── inputs ───────────────────────────────────────────────────────────────
    domain: str
    user_context: str
    max_professors: int
    slug_filter: str            # email mode: only process this slug prefix

    # ── produced by the search / load_research node ──────────────────────────
    professors: List[Dict[str, Any]]

    # ── per-professor fan-out payload (set by Send, read inside the branch) ──
    professor: Dict[str, Any]

    # ── accumulated across all branches ─────────────────────────────────────
    results: Annotated[List[Dict[str, Any]], operator.add]
