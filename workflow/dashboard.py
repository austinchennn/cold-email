#!/usr/bin/env python3
"""
Cold Email Workflow — Live Dashboard
======================================
Left panel:  Agent 0 chat window (38 %)
Right panel: 5 agent panels + summary (62 %)

Launch
------
  cd workflow
  python dashboard.py
"""
from __future__ import annotations

import queue as _stdlib_queue
import sys
import threading
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

# ── Textual ────────────────────────────────────────────────────────────────────
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, ScrollableContainer, Vertical
from textual.markup import escape as markup_escape
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Rule, Static

# ── Workflow ───────────────────────────────────────────────────────────────────
from skills.event_bus import Event, EventType, bus
from agents.agent0_intake import Agent0Intake, build_search_context, REQUIRED_FIELDS
import logging as _logging
_logger = _logging.getLogger("dashboard")
# ─────────────────────────────────────────────────────────────────────────────
# Agent meta-information
# ─────────────────────────────────────────────────────────────────────────────

AGENT_DEFS: Dict[int, Dict] = {
    1: {
        "label": "Professor Search",
        "icon":  "[ 1 ]",
        "steps": [
            "Web search",
            "LLM extraction",
            "Normalise & save JSON",
        ],
        "color": "cyan",
    },
    2: {
        "label": "Deep Research",
        "icon":  "[ 2 ]",
        "steps": [
            "Scrape lab / profile page",
            "Extract tech stack & keywords",
            "Write interest paragraph",
            "Save profile JSON",
        ],
        "color": "green",
    },
    3: {
        "label": "Resume Adaptation",
        "icon":  "[ 3 ]",
        "steps": [
            "TF-IDF project match",
            "LLM rewrite bullets",
            "Inject into LaTeX template",
            "Save .tex file",
        ],
        "color": "yellow",
    },
    4: {
        "label": "Email Writing",
        "icon":  "[ 4 ]",
        "steps": [
            "Build professor context",
            "Generate cold email (LLM)",
            "Save email .txt",
        ],
        "color": "magenta",
    },
    5: {
        "label": "Gmail Send",
        "icon":  "[ 5 ]",
        "steps": [
            "Parse email file",
            "Send via Gmail → professor",
            "Record in status DB",
        ],
        "color": "bright_red",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# In-memory state (owned by the App, updated from event bus poll)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LLMCall:
    label:    str
    system:   str
    user:     str
    response: str = ""


@dataclass
class AgentState:
    status:       str = "idle"     # idle | running | done | error
    professor:    str = ""
    current_step: str = ""
    steps_done:   List[str] = field(default_factory=list)
    llm_calls:    List[LLMCall] = field(default_factory=list)
    error_msg:    str = ""
    _pending:     Optional[LLMCall] = field(default=None, repr=False)

    def progress_pct(self, agent_id: int) -> int:
        steps = AGENT_DEFS[agent_id]["steps"]
        if not steps:
            return 0
        done = len(self.steps_done)
        if self.current_step and self.current_step in steps:
            done += 0.5         # in-progress gets half credit
        return min(int((done / len(steps)) * 100), 100)

# ─────────────────────────────────────────────────────────────────────────────
# Reasoning Modal
# ─────────────────────────────────────────────────────────────────────────────

class ReasoningModal(ModalScreen):
    """Full-screen overlay showing every LLM call for one agent."""

    BINDINGS = [("escape", "dismiss", "Close"), ("q", "dismiss", "Close")]

    CSS = """
    ReasoningModal {
        align: center middle;
    }
    #rm-container {
        width: 90%;
        height: 88%;
        background: #0e0e1a;
        border: double #4455ff;
        padding: 1 2;
        layout: vertical;
    }
    #rm-title {
        text-align: center;
        color: #aaaaff;
        text-style: bold;
        padding: 0 0 1 0;
    }
    #rm-scroll {
        height: 1fr;
        overflow-y: scroll;
    }
    #rm-body {
        padding: 0 1;
    }
    #rm-close {
        height: 3;
        margin-top: 1;
        width: 20;
        align-horizontal: center;
    }
    """

    def __init__(self, agent_id: int, state: AgentState, **kwargs):
        super().__init__(**kwargs)
        self._agent_id = agent_id
        self._state    = state

    def compose(self) -> ComposeResult:
        defn  = AGENT_DEFS[self._agent_id]
        title = (
            f"AGENT {self._agent_id}  ·  {defn['label']}"
            f"  —  Reasoning Inspector  ({len(self._state.llm_calls)} LLM calls)"
        )
        with Vertical(id="rm-container"):
            yield Static(title, id="rm-title", markup=True)
            yield Rule()
            with ScrollableContainer(id="rm-scroll"):
                yield Static(self._build_markup(), id="rm-body", markup=True)
            yield Button("[ ESC ]  Close", id="rm-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    def _build_markup(self) -> str:
        calls = self._state.llm_calls
        if not calls:
            return "[dim]No LLM calls recorded for this agent yet.[/dim]"

        lines: List[str] = []
        for i, c in enumerate(calls, 1):
            bar = "─" * 54
            lines += [
                f"[bold #4455ff]{bar}[/bold #4455ff]",
                f"[bold white]  CALL #{i}  ·  {markup_escape(c.label)}[/bold white]",
                f"[bold #4455ff]{bar}[/bold #4455ff]",
                "",
                "[bold yellow]╔══ SYSTEM PROMPT ════════════════════════════════════╗[/bold yellow]",
            ]
            for ln in markup_escape(c.system).splitlines():
                lines.append(f"[dim]║  {ln}[/dim]")
            lines += [
                "[bold yellow]╚═════════════════════════════════════════════════════╝[/bold yellow]",
                "",
                "[bold #44aaff]╔══ USER PROMPT ══════════════════════════════════════╗[/bold #44aaff]",
            ]
            for ln in markup_escape(c.user).splitlines():
                lines.append(f"║  {ln}")
            lines += [
                "[bold #44aaff]╚═════════════════════════════════════════════════════╝[/bold #44aaff]",
                "",
                "[bold #44ff88]╔══ MODEL RESPONSE ═══════════════════════════════════╗[/bold #44ff88]",
            ]
            resp = c.response if c.response else "(awaiting…)"
            for ln in markup_escape(resp).splitlines():
                lines.append(f"[green]║  {ln}[/green]")
            lines += [
                "[bold #44ff88]╚═════════════════════════════════════════════════════╝[/bold #44ff88]",
                "",
                "",
            ]
        return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# Agent Panel widget
# ─────────────────────────────────────────────────────────────────────────────

class AgentPanel(Vertical):
    """
    One workbench panel.  Contains a Static body (Rich markup) and a
    View Reasoning button at the bottom.
    """

    can_focus = True

    def __init__(self, agent_id: int, **kwargs):
        super().__init__(**kwargs)
        self.agent_id = agent_id
        self.defn     = AGENT_DEFS[agent_id]

    def compose(self) -> ComposeResult:
        yield Static("", id=f"ap-body-{self.agent_id}", markup=True)
        yield Button(
            f"  [View Reasoning]  0 calls",
            id=f"view-{self.agent_id}",
            classes="view-btn",
        )

    def refresh_state(self, state: AgentState) -> None:
        # Update body text
        body = self.query_one(f"#ap-body-{self.agent_id}", Static)
        body.update(self._build_markup(state))

        # Update button label
        nc  = len(state.llm_calls)
        btn = self.query_one(f"#view-{self.agent_id}", Button)
        btn.label = f"  [View Reasoning]  {nc} call{'s' if nc != 1 else ''}"

        # Swap CSS class for border colour
        for cls in ("idle", "running", "done", "error"):
            self.remove_class(cls)
        self.add_class(state.status)

    # ── Rich markup renderer ──────────────────────────────────────────────────

    def _build_markup(self, s: AgentState) -> str:
        c     = self.defn["color"]
        steps = self.defn["steps"]
        label = self.defn["label"]
        n     = self.agent_id

        STATUS_MAP = {
            "idle":    "[dim]  ○  IDLE[/dim]",
            "running": f"[bold {c}]  ▶  RUNNING[/bold {c}]",
            "done":    "[bold green]  ✓  DONE[/bold green]",
            "error":   "[bold red]  ✗  ERROR[/bold red]",
        }

        lines: List[str] = [
            f"[bold {c}]╔═ AGENT {n}  ·  {label} ══╗[/bold {c}]",
            "",
            STATUS_MAP.get(s.status, ""),
        ]

        if s.professor:
            lines.append(f"  [dim]prof:[/dim] {markup_escape(s.professor[:36])}")

        lines.append("")
        sep = f"  [dim]{'·' * 34}[/dim]"
        lines.append(sep)

        # Step list
        for step in steps:
            safe = markup_escape(step)
            if step in s.steps_done:
                lines.append(f"  [green]✓[/green] [dim]{safe}[/dim]")
            elif step == s.current_step:
                lines.append(f"  [bold {c}]▶[/bold {c}] {safe}")
            else:
                lines.append(f"  [dim]○ {safe}[/dim]")

        lines.append(sep)
        lines.append("")

        # ASCII progress bar (24 chars)
        pct    = s.progress_pct(n)
        filled = pct * 24 // 100
        bar    = "█" * filled + "░" * (24 - filled)
        lines.append(f"  [{c}]{bar}[/{c}]  {pct:3d}%")

        if s.error_msg:
            lines.append(f"\n  [bold red]⚠  {markup_escape(s.error_msg[:44])}[/bold red]")

        return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# Main App
# ─────────────────────────────────────────────────────────────────────────────

_TCSS = """
Screen {
    background: #0a0a0f;
}

Header {
    background: #111133;
    color: #aaaaff;
}

/* ── Main area: chat left + agents right ── */
#main-area {
    height: 1fr;
}
#chat-panel {
    width: 38%;
    border-right: solid #222244;
    background: #0a0a14;
}
#chat-title {
    text-align: center;
    color: #aaaaff;
    text-style: bold;
    padding: 1 0;
    background: #0f0f22;
    border-bottom: solid #222244;
    height: 3;
}
#chat-log {
    height: 1fr;
    padding: 0 1;
    background: #0a0a14;
}
#chat-input-bar {
    height: 3;
    padding: 0 1;
    background: #0f0f22;
    border-top: solid #222244;
}
#chat-input {
    width: 1fr;
    background: #111133;
    border: solid #333366;
    color: #ffffff;
}
#chat-send-btn {
    width: 10;
    background: #1a472a;
    border: solid #2d6a3f;
    color: #66ff88;
}

/* ── Right side ──────────────────────── */
#right-side {
    width: 62%;
}

/* ── 2×3 Agent Grid ──────────────────── */
#agent-grid {
    grid-size: 2 3;
    grid-gutter: 1 1;
    padding: 1;
    height: 1fr;
}

AgentPanel {
    padding: 1 1;
    border: solid #333344;
    height: 100%;
}
AgentPanel > Static {
    height: 1fr;
}
AgentPanel > .view-btn {
    height: 3;
    background: #111133;
    border: solid #334;
    color: #7788aa;
    text-align: center;
}

/* Border colours by status */
AgentPanel.idle    { border: solid #333344; }
AgentPanel.running { border: solid #00dd88; }
AgentPanel.done    { border: solid #4488ff; }
AgentPanel.error   { border: solid #ff4444; }

/* Summary panel (slot 6) */
#panel-summary {
    border: solid #222244;
    padding: 1 2;
    height: 100%;
    background: #0a0a18;
    color: #aaaacc;
}

/* ── Event log strip ─────────────────── */
#log-strip {
    height: 6;
    border-top: solid #222233;
    background: #060610;
    padding: 0 1;
}

Footer {
    background: #0f0f22;
    color: #555577;
}
"""


class DashboardApp(App):
    """Live dashboard with Agent 0 chat panel + 5 agent workbench."""

    TITLE         = "Cold Email Workflow"
    SUB_TITLE     = "Multi-Agent Live Dashboard"
    CSS           = _TCSS
    BINDINGS      = [
        Binding("q",     "quit",   "Quit"),
        Binding("1",     "view_1", "Agent 1"),
        Binding("2",     "view_2", "Agent 2"),
        Binding("3",     "view_3", "Agent 3"),
        Binding("4",     "view_4", "Agent 4"),
        Binding("5",     "view_5", "Agent 5"),
        Binding("v",     "view_focused", "View Reasoning"),
    ]

    _WORKFLOW_TIMEOUT = 20 * 60  # 20 min — auto-clear stale _running flag

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.states: Dict[int, AgentState] = {i: AgentState() for i in range(1, 6)}
        self._sub_q: Optional[_stdlib_queue.Queue] = None
        self._running = False
        self._workflow_thread: Optional[threading.Thread] = None
        self._workflow_start: float = 0.0
        self._agent0 = Agent0Intake()

    def _is_running(self) -> bool:
        """True only if workflow is actually still executing."""
        if not self._running:
            return False
        # If the worker thread is done, clear the flag
        if self._workflow_thread and not self._workflow_thread.is_alive():
            self._running = False
            self._workflow_thread = None
            return False
        # Auto-reset if stuck longer than timeout
        if _time.monotonic() - self._workflow_start > self._WORKFLOW_TIMEOUT:
            self._running = False
            self._workflow_thread = None
            self._log("[yellow]⚠️  Workflow timeout — auto-reset _running flag[/yellow]")
            return False
        return True

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="main-area"):
            # ── Left: Chat panel ──────────────────────────────────────────────
            with Vertical(id="chat-panel"):
                yield Static("💬 ColdEmail 智能助手", id="chat-title")
                yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True)
                with Horizontal(id="chat-input-bar"):
                    yield Input(
                        placeholder="输入消息... (/run /research /email /profile /reset /quit)",
                        id="chat-input",
                    )
                    yield Button("发送", id="chat-send-btn")

            # ── Right: Agent panels ───────────────────────────────────────────
            with Vertical(id="right-side"):
                with Grid(id="agent-grid"):
                    yield AgentPanel(1, id="panel-1")
                    yield AgentPanel(2, id="panel-2")
                    yield AgentPanel(3, id="panel-3")
                    yield AgentPanel(4, id="panel-4")
                    yield AgentPanel(5, id="panel-5")
                    yield Static("", id="panel-summary", markup=True)

        yield RichLog(id="log-strip", highlight=True, markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self._sub_q = bus.subscribe()
        for i in range(1, 6):
            self.query_one(f"#panel-{i}", AgentPanel).refresh_state(self.states[i])
        self.set_interval(0.08, self._poll_bus)
        self._log("[dim]Dashboard ready.[/dim]")
        # Start Agent0 interview in background thread
        self._start_interview()

    # ── Agent 0 interview on startup ──────────────────────────────────────────

    @work(thread=True)
    def _start_interview(self) -> None:
        """Generate the Agent0 greeting and display it."""
        try:
            greeting = self._agent0.start_interview()
            self.call_from_thread(self._chat_write, "🤖", greeting)
        except Exception as exc:
            self.call_from_thread(self._chat_write, "⚠️", f"Agent0 初始化失败: {exc}")

    # ── Chat input handling ───────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat-input":
            self._handle_chat_input()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "chat-send-btn":
            self._handle_chat_input()
        elif bid.startswith("view-"):
            self._open_reasoning(int(bid.split("-")[1]))

    def _handle_chat_input(self) -> None:
        inp = self.query_one("#chat-input", Input)
        text = inp.value.strip()
        if not text:
            return
        inp.value = ""

        # Show user message in chat log
        self._chat_write("你", text)

        # Slash commands processed locally
        cmd = text.lower()
        if cmd in ("/quit", "/exit", "/q"):
            self.exit()
            return
        if cmd in ("/reset", "/force"):
            self._running = False
            self._workflow_thread = None
            self._chat_write("🔄", "_running 已强制重置，可以重新运行了。")
            return
        if cmd == "/profile":
            self._show_profile_in_chat()
            return
        if cmd in ("/run", "/start", "/run_all"):
            self._prepare_and_run("run_all")
            return
        if cmd in ("/research", "/search"):
            self._prepare_and_run("run_research")
            return
        if cmd in ("/email", "/send"):
            self._prepare_and_run("run_email")
            return

        # Normal message → Agent0
        self._process_chat_message(text)

    @work(thread=True)
    def _process_chat_message(self, text: str) -> None:
        """Send user text to Agent0 and display the reply."""
        try:
            result = self._agent0.chat(text)
            reply = result["reply"]
            intent = result["intent"]
            fields = result.get("fields", {})

            # Show updated fields
            if fields:
                field_str = ", ".join(f"{k}={v}" for k, v in fields.items())
                self.call_from_thread(
                    self._chat_write, "📝", f"已更新: {field_str}"
                )

            # Auto-trigger workflow if intent is a run command
            if intent in ("run_all", "run_research", "run_email"):
                # Always show Agent0's reply first so user isn't left with silence
                self.call_from_thread(self._chat_write, "🤖", reply)
                missing = self._agent0.missing_required()
                if missing:
                    self.call_from_thread(
                        self._chat_write, "🔄",
                        f"自动补充缺失信息: {', '.join(missing)}"
                    )
                    filled = self._agent0.auto_fill_missing()
                    if filled:
                        fs = ", ".join(f"{k}={v}" for k, v in filled.items())
                        self.call_from_thread(self._chat_write, "📝", f"已自动补充: {fs}")
                    still_missing = self._agent0.missing_required()
                    if still_missing:
                        self.call_from_thread(
                            self._chat_write, "⚠️",
                            f"仍无法推断: {', '.join(still_missing)}\n请补充这些信息后重试。"
                        )
                    else:
                        self.call_from_thread(self._trigger_workflow, intent)
                else:
                    self.call_from_thread(self._trigger_workflow, intent)
            else:
                # Normal reply
                self.call_from_thread(self._chat_write, "🤖", reply)

            if intent == "show_profile":
                self.call_from_thread(self._show_profile_in_chat)

        except Exception as exc:
            self.call_from_thread(
                self._chat_write, "⚠️", f"处理消息出错: {exc}"
            )

    def _show_profile_in_chat(self) -> None:
        profile = self._agent0.profile
        lines = []
        labels = {
            "name": "姓名", "email": "邮箱", "current_school": "学校",
            "target_degree": "目标", "research_domain": "研究领域",
            "skills": "技术栈", "gpa": "GPA", "major": "专业",
        }
        for key, label in labels.items():
            val = profile.get(key, "")
            if isinstance(val, list):
                val = ", ".join(val) if val else "-"
            elif not val:
                val = "-"
            lines.append(f"  {label}: {val}")

        missing = self._agent0.missing_required()
        if missing:
            lines.append(f"\n  [red]缺失必填: {', '.join(missing)}[/red]")
        else:
            lines.append(f"\n  [green]✓ 必填信息完整[/green]")

        self._chat_write("📋", "\n".join(lines))

    # ── Workflow triggering ───────────────────────────────────────────────────

    @work(thread=True)
    def _prepare_and_run(self, action: str) -> None:
        """Auto-fill missing fields in a worker thread, then trigger workflow."""
        if self._is_running():
            self.call_from_thread(self._chat_write, "⚠️", "Workflow 正在运行中，请等待完成。或输入 /reset 强制重置。")
            return
        missing = self._agent0.missing_required()
        if missing:
            self.call_from_thread(
                self._chat_write, "🔄",
                f"自动补充缺失信息: {', '.join(missing)}"
            )
            filled = self._agent0.auto_fill_missing()
            if filled:
                fs = ", ".join(f"{k}={v}" for k, v in filled.items())
                self.call_from_thread(self._chat_write, "📝", f"已自动补充: {fs}")
            still_missing = self._agent0.missing_required()
            if still_missing:
                self.call_from_thread(
                    self._chat_write, "⚠️",
                    f"仍无法推断: {', '.join(still_missing)}\n请补充这些信息后重试。"
                )
                return
        self.call_from_thread(self._trigger_workflow, action)

    def _trigger_workflow(self, action: str) -> None:
        """Start the requested workflow (profile assumed complete)."""
        if self._is_running():
            self._chat_write("⚠️", "Workflow 正在运行中，请等待完成。或输入 /reset 强制重置。")
            return

        profile = self._agent0.profile
        user_context = build_search_context(profile)
        domain = profile.get("research_domain", "")

        bus.reset()
        for i in range(1, 6):
            self.states[i] = AgentState()
            self.query_one(f"#panel-{i}", AgentPanel).refresh_state(self.states[i])
        self._sub_q = bus.subscribe()

        self._running = True
        self._workflow_start = _time.monotonic()
        self._chat_write("🤖", f"开始 [{action}] — 领域: {domain}")
        self._log(f"[bold green]▶  Starting {action} — domain: {domain}[/bold green]")

        if action == "run_all":
            t = self._run_full_workflow(domain, user_context, profile)
        elif action == "run_research":
            t = self._run_research_only(domain, user_context, profile)
        elif action == "run_email":
            t = self._run_email_only(user_context, profile)
        else:
            self._running = False
            return
        # Capture the underlying thread so _is_running() can check liveness
        if hasattr(t, "_thread"):
            self._workflow_thread = t._thread
        else:
            self._workflow_thread = None

    @work(thread=True, exclusive=False)
    def _run_full_workflow(self, domain: str, user_context: str, profile: dict) -> None:
        try:
            from config.settings import MAX_PROFESSORS
            from agents.agent1_search   import Agent1Search
            from agents.agent2_research import Agent2Research
            from agents.agent3_resume   import Agent3Resume
            from agents.agent4_email    import Agent4Email
            from agents.agent5_send     import Agent5Send

            max_prof = profile.get("max_professors", MAX_PROFESSORS)
            bus.post(Event(EventType.WORKFLOW_START, 0, {"domain": domain}))

            professors = Agent1Search().run(domain, max_count=max_prof,
                                            user_context=user_context)
            for prof in professors:
                name = prof.get("name", "?")
                bus.post(Event(EventType.PROFESSOR_START, 0, {"name": name}))
                research    = Agent2Research().run(prof, user_context=user_context)
                resume_path = Agent3Resume().run(research)
                email_path  = Agent4Email().run(research, resume_path)
                Agent5Send().run(research, email_path)

            bus.post(Event(EventType.WORKFLOW_DONE, 0, {"count": len(professors)}))
        except Exception as exc:
            _logger.exception("Workflow error")
            bus.post(Event(EventType.WORKFLOW_ERROR, 0, {"error": str(exc)}))
        finally:
            self._running = False

    @work(thread=True, exclusive=False)
    def _run_research_only(self, domain: str, user_context: str, profile: dict) -> None:
        try:
            from config.settings import MAX_PROFESSORS
            from agents.agent1_search   import Agent1Search
            from agents.agent2_research import Agent2Research

            max_prof = profile.get("max_professors", MAX_PROFESSORS)
            bus.post(Event(EventType.WORKFLOW_START, 0, {"domain": domain}))

            professors = Agent1Search().run(domain, max_count=max_prof,
                                            user_context=user_context)
            for prof in professors:
                name = prof.get("name", "?")
                bus.post(Event(EventType.PROFESSOR_START, 0, {"name": name}))
                Agent2Research().run(prof, user_context=user_context)

            bus.post(Event(EventType.WORKFLOW_DONE, 0, {"count": len(professors)}))
        except Exception as exc:
            _logger.exception("Research workflow error")
            bus.post(Event(EventType.WORKFLOW_ERROR, 0, {"error": str(exc)}))
        finally:
            self._running = False

    @work(thread=True, exclusive=False)
    def _run_email_only(self, user_context: str, profile: dict) -> None:
        try:
            from agents.agent3_resume import Agent3Resume
            from agents.agent4_email  import Agent4Email
            from agents.agent5_send   import Agent5Send
            from config.settings import DEEP_RESEARCH_DIR
            import json as _json

            bus.post(Event(EventType.WORKFLOW_START, 0, {"action": "email_only"}))

            # Load existing research profiles
            research_files = sorted(DEEP_RESEARCH_DIR.glob("*_prof.json"))
            if not research_files:
                bus.post(Event(EventType.WORKFLOW_ERROR, 0,
                               {"error": "No research profiles found. Run research first."}))
                return

            for rf in research_files:
                research = _json.loads(rf.read_text(encoding="utf-8"))
                name = research.get("name", "?")
                bus.post(Event(EventType.PROFESSOR_START, 0, {"name": name}))
                resume_path = Agent3Resume().run(research)
                email_path  = Agent4Email().run(research, resume_path)
                Agent5Send().run(research, email_path)

            bus.post(Event(EventType.WORKFLOW_DONE, 0, {"count": len(research_files)}))
        except Exception as exc:
            _logger.exception("Email workflow error")
            bus.post(Event(EventType.WORKFLOW_ERROR, 0, {"error": str(exc)}))
        finally:
            self._running = False

    # ── Keyboard actions ──────────────────────────────────────────────────────

    def action_view_focused(self) -> None:
        f = self.focused
        if isinstance(f, AgentPanel):
            self._open_reasoning(f.agent_id)

    def action_view_1(self) -> None: self._open_reasoning(1)
    def action_view_2(self) -> None: self._open_reasoning(2)
    def action_view_3(self) -> None: self._open_reasoning(3)
    def action_view_4(self) -> None: self._open_reasoning(4)
    def action_view_5(self) -> None: self._open_reasoning(5)

    # ── Event bus polling ─────────────────────────────────────────────────────

    def _poll_bus(self) -> None:
        if self._sub_q is None:
            return
        processed = 0
        while processed < 40:
            try:
                ev: Event = self._sub_q.get_nowait()
            except _stdlib_queue.Empty:
                break
            self._dispatch(ev)
            processed += 1

    def _dispatch(self, ev: Event) -> None:
        aid = ev.agent_id
        d   = ev.data

        # ── Workflow-level events ────────────────────────────────────────────
        if ev.type == EventType.WORKFLOW_START:
            self._log(f"[bold]Workflow started  ·  domain=[/bold]{d.get('domain','')}")

        elif ev.type == EventType.WORKFLOW_DONE:
            self._log(
                f"[bold green]✓ Workflow complete — "
                f"{d.get('count','?')} professor(s) processed[/bold green]"
            )
            self._chat_write("✅", f"Workflow 完成！处理了 {d.get('count','?')} 位教授")
            self._update_summary()
        elif ev.type == EventType.WORKFLOW_ERROR:
            err = d.get('error', '')
            self._log(f"[bold red]✗ Workflow error: {err}[/bold red]")
            self._chat_write("❌", f"Workflow 出错: {err}")

        elif ev.type == EventType.PROFESSOR_START:
            name = d.get("name", "")
            self._log(f"\n[cyan]── Professor: {name} ──[/cyan]")
            self._chat_write("👤", f"开始处理教授: {name}")
            # Reset agents 2-5 for the new professor
            for i in range(2, 6):
                self.states[i] = AgentState(professor=name)
                self._refresh(i)

        # ── Agent lifecycle ──────────────────────────────────────────────────
        elif ev.type == EventType.AGENT_START and 1 <= aid <= 5:
            s = self.states[aid]
            s.status    = "running"
            s.professor = d.get("professor", s.professor)
            self._refresh(aid)
            label = AGENT_DEFS[aid]["label"]
            self._log(
                f"  [bold {AGENT_DEFS[aid]['color']}]"
                f"Agent {aid}[/bold {AGENT_DEFS[aid]['color']}] started"
            )
            self._chat_write(f"▶ A{aid}", f"{label} 开始运行…")

        elif ev.type == EventType.AGENT_STEP and 1 <= aid <= 5:
            step = d.get("step", "")
            s    = self.states[aid]
            if s.current_step and s.current_step not in s.steps_done:
                s.steps_done.append(s.current_step)
            s.current_step = step
            self._refresh(aid)
            self._log(f"    [dim]┆ Agent {aid}:[/dim] {step}")

        elif ev.type == EventType.AGENT_COMPLETE and 1 <= aid <= 5:
            s = self.states[aid]
            if s.current_step and s.current_step not in s.steps_done:
                s.steps_done.append(s.current_step)
            s.current_step = ""
            s.status       = "done"
            self._refresh(aid)
            nc = len(s.llm_calls)
            label = AGENT_DEFS[aid]["label"]
            call_txt = f"{nc} LLM call{'s' if nc != 1 else ''}" if nc else "完成"
            self._log(f"  [green]✓ Agent {aid} complete  ({call_txt})[/green]")
            self._chat_write(f"✅ A{aid}", f"{label} 完成 — {call_txt}")

        elif ev.type == EventType.AGENT_ERROR and 1 <= aid <= 5:
            s           = self.states[aid]
            s.status    = "error"
            s.error_msg = d.get("error", "Unknown error")[:80]
            self._refresh(aid)
            label = AGENT_DEFS[aid]["label"]
            self._log(f"  [red]✗ Agent {aid} error: {s.error_msg}[/red]")
            self._chat_write(f"❌ A{aid}", f"{label} 出错: {s.error_msg}")

        # ── LLM interactions ─────────────────────────────────────────────────
        elif ev.type == EventType.LLM_CALL and 1 <= aid <= 5:
            s           = self.states[aid]
            s._pending  = LLMCall(
                label  = d.get("step", s.current_step or "LLM call"),
                system = d.get("system", ""),
                user   = d.get("user",   ""),
            )
            self._refresh(aid)

        elif ev.type == EventType.LLM_RESPONSE and 1 <= aid <= 5:
            s = self.states[aid]
            if s._pending is not None:
                s._pending.response = d.get("response", "")
                s.llm_calls.append(s._pending)
                s._pending = None
            self._refresh(aid)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _refresh(self, agent_id: int) -> None:
        self.query_one(f"#panel-{agent_id}", AgentPanel).refresh_state(
            self.states[agent_id]
        )

    def _update_summary(self) -> None:
        """Update the bottom-right summary panel with send statistics."""
        try:
            from skills.send_tracker import SendTracker
            stats = SendTracker().stats()
            sent    = stats.get("sent",    0)
            replied = stats.get("replied", 0)
            failed  = stats.get("failed",  0)
            text = (
                "[bold white]Send Summary[/bold white]\n\n"
                f"  [green]\u2713 Sent    : {sent}[/green]\n"
                f"  [cyan]\u21ba Replied : {replied}[/cyan]\n"
                f"  [red]\u2717 Failed  : {failed}[/red]\n"
            )
        except Exception:
            text = "[dim](no send data yet)[/dim]"
        try:
            self.query_one("#panel-summary", Static).update(text)
        except Exception:
            pass

    def _open_reasoning(self, agent_id: int) -> None:
        self.push_screen(ReasoningModal(agent_id, self.states[agent_id]))

    def _chat_write(self, sender: str, text: str) -> None:
        """Write a message to the chat log panel."""
        chat = self.query_one("#chat-log", RichLog)
        safe = markup_escape(text)
        chat.write(f"[bold]{sender}[/bold]  {safe}\n")

    def _log(self, text: str) -> None:
        self.query_one("#log-strip", RichLog).write(text)

# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    DashboardApp().run()


if __name__ == "__main__":
    main()

