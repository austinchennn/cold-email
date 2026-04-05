"""
Thread-safe singleton event bus
================================
Agents post events here; the dashboard subscribes and renders them.

Usage
-----
  from skills.event_bus import bus, Event, EventType

  bus.post(Event(EventType.AGENT_STEP, agent_id=2, data={"step": "Scrape page"}))

  q = bus.subscribe()          # returns a queue.Queue
  event = q.get(timeout=0.1)  # blocks up to 100 ms
  bus.unsubscribe(q)
"""
from __future__ import annotations

import queue as _stdlib_queue
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EventType(str, Enum):
    # Workflow-level
    WORKFLOW_START   = "workflow_start"
    WORKFLOW_DONE    = "workflow_done"
    WORKFLOW_ERROR   = "workflow_error"
    PROFESSOR_START  = "professor_start"

    # Agent lifecycle
    AGENT_START      = "agent_start"
    AGENT_STEP       = "agent_step"
    AGENT_COMPLETE   = "agent_complete"
    AGENT_ERROR      = "agent_error"

    # LLM interactions
    LLM_CALL         = "llm_call"
    LLM_RESPONSE     = "llm_response"


@dataclass
class Event:
    type:     EventType
    agent_id: int                              # 1-4; 0 = workflow-level
    data:     Dict[str, Any] = field(default_factory=dict)


class EventBus:
    """Singleton event bus — always import the module-level `bus` instance."""

    _instance: Optional["EventBus"] = None
    _class_lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "EventBus":
        with cls._class_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._lock         = threading.Lock()
                inst._initialized  = True
                inst._history: List[Event]                    = []
                inst._subscribers: List[_stdlib_queue.Queue] = []
                cls._instance = inst
        return cls._instance

    def __init__(self) -> None:
        pass  # all init is in __new__ to survive repeated calls

    # ── Publisher ─────────────────────────────────────────────────────────────

    def post(self, event: Event) -> None:
        with self._lock:
            self._history.append(event)
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except _stdlib_queue.Full:
                    pass  # drop silently — never block the workflow thread

    # ── Subscriber ────────────────────────────────────────────────────────────

    def subscribe(self) -> _stdlib_queue.Queue:
        """Return a new queue that will receive all future events."""
        q: _stdlib_queue.Queue = _stdlib_queue.Queue(maxsize=4000)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: _stdlib_queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    # ── Inspection ────────────────────────────────────────────────────────────

    def history(self, agent_id: Optional[int] = None) -> List[Event]:
        with self._lock:
            if agent_id is None:
                return list(self._history)
            return [e for e in self._history if e.agent_id == agent_id]

    def reset(self) -> None:
        with self._lock:
            self._history.clear()
            self._subscribers.clear()


# Module-level singleton – import this everywhere
bus = EventBus()
