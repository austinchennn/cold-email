"""
Agent 0 intake — one conversation turn as an explicit LangGraph state machine.

    classify → apply_fields → (needs_reply?) → generate_reply → END
                                            └───────────────→ END

`Agent0Intake` owns the profile / history and the LCEL chains; this graph just
sequences one turn. The caller (CLI loop or dashboard) still owns stdin / the
chat widget and calls `agent.chat(msg)` once per user message.
"""
from __future__ import annotations

from typing import Any, Dict, TypedDict

from langgraph.graph import StateGraph, START, END


class TurnState(TypedDict, total=False):
    user_message: str
    intent: str
    fields: Dict[str, Any]          # fields the LLM extracted this turn
    updated_fields: Dict[str, Any]  # fields that actually changed
    reply: str


def build_intake_turn_graph(agent: "Agent0Intake"):  # noqa: F821 - avoid import cycle
    """Compile the per-turn graph, closing over the Agent0Intake instance."""

    def classify(state: TurnState) -> Dict:
        from skills.intent_router import classify_intent

        agent._history.append({"role": "user", "content": state["user_message"]})
        result = classify_intent(agent._history, agent._profile)
        return {
            "intent": result.get("intent", "chat"),
            "fields": result.get("fields", {}) or {},
            "reply": result.get("reply", "") or "",
        }

    def apply_fields(state: TurnState) -> Dict:
        updated = agent._merge_fields(state.get("fields", {}))
        if updated:
            agent.save()
        return {"updated_fields": updated}

    def generate_reply(state: TurnState) -> Dict:
        return {"reply": agent._generate_reply()}

    def finalize(state: TurnState) -> Dict:
        agent._history.append({"role": "assistant", "content": state.get("reply", "")})
        return {}

    def _needs_reply(state: TurnState) -> str:
        reply = state.get("reply", "")
        return "generate_reply" if not reply or len(reply) < 5 else "finalize"

    g = StateGraph(TurnState)
    g.add_node("classify", classify)
    g.add_node("apply_fields", apply_fields)
    g.add_node("generate_reply", generate_reply)
    g.add_node("finalize", finalize)

    g.add_edge(START, "classify")
    g.add_edge("classify", "apply_fields")
    g.add_conditional_edges("apply_fields", _needs_reply, ["generate_reply", "finalize"])
    g.add_edge("generate_reply", "finalize")
    g.add_edge("finalize", END)
    return g.compile()
