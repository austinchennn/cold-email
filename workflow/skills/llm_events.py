"""
LangChain → EventBus bridge
===========================
A callback handler that translates LangChain chat-model lifecycle events into
the project's own EventBus events (LLM_CALL / LLM_RESPONSE), so the Textual
dashboard keeps working no matter which LCEL chain issued the call.

One handler instance is created per logical call (see llm_client.run_chain) and
carries the agent_id / step that the dashboard groups events by.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

from skills.event_bus import bus, Event, EventType


class BusCallbackHandler(BaseCallbackHandler):
    """Posts LLM_CALL on model start and LLM_RESPONSE on model end."""

    def __init__(self, agent_id: int = 0, step: str = "") -> None:
        self.agent_id = agent_id
        self.step = step

    # ── model start ──────────────────────────────────────────────────────────
    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[BaseMessage]],
        *,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        turn = messages[0] if messages else []
        system = next((m.content for m in turn if m.type == "system"), "")
        user = next(
            (m.content for m in reversed(turn) if m.type == "human"), ""
        )
        model = (metadata or {}).get("ls_model_name", "")
        bus.post(Event(
            type=EventType.LLM_CALL,
            agent_id=self.agent_id,
            data={
                "step": self.step,
                "system": str(system)[:800],
                "user": str(user)[:800],
                "model": model,
            },
        ))

    # ── model end ────────────────────────────────────────────────────────────
    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        text = ""
        try:
            text = response.generations[0][0].text or ""
        except (IndexError, AttributeError):
            pass
        bus.post(Event(
            type=EventType.LLM_RESPONSE,
            agent_id=self.agent_id,
            data={"step": self.step, "response": text[:2000]},
        ))
