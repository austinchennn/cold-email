"""
Cold-email pipeline as a LangGraph StateGraph.

    build_pipeline("full")      search → [research → resume → email → send] per prof
    build_pipeline("research")  search → [research] per prof
    build_pipeline("email")     load_research → [resume → email → send] per prof

All four legacy entry points (main.py, run_research.py, run_email.py,
run_intake.py) and the dashboard drive the same graph via run_pipeline().

The agent classes are unchanged — they are the node implementations. Each
per-professor branch writes only `results` (list-concat reduced), so the
fan-out branches never collide.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from config.settings import MAX_PROFESSORS, DEEP_RESEARCH_DIR
from graph.state import PipelineState
from skills.event_bus import bus, Event, EventType

logger = logging.getLogger(__name__)

Mode = str  # "full" | "research" | "email"


# ── Nodes: entry ─────────────────────────────────────────────────────────────

def _search(state: PipelineState) -> Dict:
    from agents.agent1_search import Agent1Search

    professors = Agent1Search().run(
        state["domain"],
        max_count=state.get("max_professors", MAX_PROFESSORS),
        user_context=state.get("user_context", ""),
    )
    return {"professors": professors}


def _load_research(state: PipelineState) -> Dict:
    """Email mode: load Agent-2 research dicts from disk instead of searching."""
    if state.get("professors"):
        return {}                                 # caller supplied the list already
    slug_filter = state.get("slug_filter", "")
    files = sorted(DEEP_RESEARCH_DIR.glob("*_prof.json"))
    if slug_filter:
        files = [f for f in files if f.name.startswith(slug_filter)]

    research: List[Dict] = []
    for f in files:
        try:
            research.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Skipping unreadable research file {f.name}: {exc}")
    if not research:
        logger.error(f"No research profiles in {DEEP_RESEARCH_DIR} — run research first.")
    return {"professors": research}


# ── Nodes: per-professor fan-out ─────────────────────────────────────────────

def _fan_out(target: str):
    """Build the conditional-edge function that spawns one branch per professor."""
    def _dispatch(state: PipelineState) -> List[Send]:
        return [
            Send(target, {
                "professor": prof,
                "user_context": state.get("user_context", ""),
            })
            for prof in state.get("professors", [])
        ]
    return _dispatch


def _research_professor(state: PipelineState) -> Dict:
    from agents.agent2_research import Agent2Research

    prof = state["professor"]
    name = prof.get("name", "Unknown")
    bus.post(Event(EventType.PROFESSOR_START, 0, {"name": name}))
    try:
        research = Agent2Research().run(prof, user_context=state.get("user_context", ""))
        return {"results": [research]}
    except Exception as exc:                       # noqa: BLE001 — one bad prof must not kill the run
        logger.error(f"Agent2 failed for {name}: {exc}")
        return {"results": [{"name": name, "slug": "", "_error": str(exc), **prof}]}


def _resume_email_send(research: Dict) -> Dict:
    """Agent 3 → 4 → 5 for a single already-researched professor."""
    from agents.agent3_resume import Agent3Resume
    from agents.agent4_email import Agent4Email
    from agents.agent5_send import Agent5Send

    name = research.get("name", "Unknown")
    out = dict(research)
    try:
        resume_path = Agent3Resume().run(research)
        out["resume"] = str(resume_path)
    except Exception as exc:                       # noqa: BLE001
        logger.error(f"Agent3 failed for {name}: {exc}")
        out["_error_agent3"] = str(exc)
        return out
    try:
        email_path = Agent4Email().run(research, resume_path)
        out["email"] = str(email_path)
    except Exception as exc:                       # noqa: BLE001
        logger.error(f"Agent4 failed for {name}: {exc}")
        out["_error_agent4"] = str(exc)
        return out
    try:
        out["gmail_id"] = Agent5Send().run(research, email_path)
    except Exception as exc:                       # noqa: BLE001
        logger.error(f"Agent5 failed for {name}: {exc}")
        out["_error_agent5"] = str(exc)
    return out


def _process_professor(state: PipelineState) -> Dict:
    """Full pipeline for one professor: research → resume → email → send."""
    from agents.agent2_research import Agent2Research

    prof = state["professor"]
    name = prof.get("name", "Unknown")
    bus.post(Event(EventType.PROFESSOR_START, 0, {"name": name}))
    try:
        research = Agent2Research().run(prof, user_context=state.get("user_context", ""))
    except Exception as exc:                       # noqa: BLE001
        logger.error(f"Agent2 failed for {name}: {exc}")
        return {"results": [{"name": name, "slug": "", "_error": str(exc), **prof}]}
    return {"results": [_resume_email_send(research)]}


def _email_professor(state: PipelineState) -> Dict:
    """Email mode: the fan-out payload is already a research dict."""
    research = state["professor"]
    bus.post(Event(EventType.PROFESSOR_START, 0, {"name": research.get("name", "?")}))
    return {"results": [_resume_email_send(research)]}


# ── Graph builders ───────────────────────────────────────────────────────────

# mode → (entry node name, entry fn, branch node name, branch fn)
_ENTRY = {
    "full":     ("search",        _search,        "process_professor",  _process_professor),
    "research": ("search",        _search,        "research_professor", _research_professor),
    "email":    ("load_research", _load_research, "email_professor",    _email_professor),
}


def build_pipeline(mode: Mode = "full"):
    """Compile and return the pipeline graph for the given mode."""
    if mode not in _ENTRY:
        raise ValueError(f"Unknown pipeline mode: {mode!r} (use full/research/email)")
    entry_name, entry_fn, branch_name, branch_fn = _ENTRY[mode]

    g = StateGraph(PipelineState)
    g.add_node(entry_name, entry_fn)
    g.add_node(branch_name, branch_fn)
    g.add_edge(START, entry_name)
    g.add_conditional_edges(entry_name, _fan_out(branch_name), [branch_name])
    g.add_edge(branch_name, END)
    return g.compile()


def run_pipeline(
    mode: Mode = "full",
    *,
    domain: str = "",
    user_context: str = "",
    max_professors: int = MAX_PROFESSORS,
    slug_filter: str = "",
    professors: List[Dict] | None = None,
) -> List[Dict]:
    """
    Invoke the pipeline and return the per-professor result list.
    Emits WORKFLOW_START / WORKFLOW_DONE / WORKFLOW_ERROR for the dashboard.

    `professors` pre-seeds the fan-out (email mode: a list of research dicts),
    skipping the search / disk-load entry node.
    """
    graph = build_pipeline(mode)
    state: PipelineState = {
        "domain": domain,
        "user_context": user_context,
        "max_professors": max_professors,
        "slug_filter": slug_filter,
        "results": [],
    }
    if professors is not None:
        state["professors"] = professors
    bus.post(Event(EventType.WORKFLOW_START, 0, {"mode": mode, "domain": domain}))
    try:
        # Fan-out can spawn many branches in one super-step; lift the default cap.
        final = graph.invoke(state, config={"recursion_limit": 100})
    except Exception as exc:
        logger.exception("Pipeline error")
        bus.post(Event(EventType.WORKFLOW_ERROR, 0, {"error": str(exc)}))
        raise
    results = final.get("results", [])
    bus.post(Event(EventType.WORKFLOW_DONE, 0, {"count": len(results)}))
    return results
