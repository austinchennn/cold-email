"""
Intent Recognition Skill
===========================
用 LLM 对用户的自然语言消息做意图分类，返回结构化的 intent + slots。

Intents
-------
  update_profile   — 用户提供了新的个人信息（姓名/学校/方向 等）
  run_all          — 用户想完整跑一遍 workflow（1→5）
  run_research     — 只跑 Agent 1+2（搜索+深度调研）
  run_email        — 只跑 Agent 3+4+5（简历+邮件+发送）
  show_profile     — 查看当前存储的档案
  edit_profile     — 修改某个字段
  chat             — 闲聊 / 提问 / 不属于以上的任何内容
"""

import json
import logging
from typing import Any, Dict

from skills.llm_client import call_llm_chat
from skills.event_bus import bus, Event, EventType

logger = logging.getLogger(__name__)

# 所有合法 intent 名
VALID_INTENTS = frozenset({
    "update_profile",
    "run_all",
    "run_research",
    "run_email",
    "show_profile",
    "edit_profile",
    "chat",
})

_INTENT_SYSTEM = """\
You are an intent-recognition and information-extraction engine for a cold-email
assistant that helps students contact professors.

Given the conversation history, classify the user's LATEST message into ONE intent
and extract any relevant slots.

## Possible intents

| intent          | description                                                      |
|-----------------|------------------------------------------------------------------|
| update_profile  | The user is providing NEW personal info (name, school, major,    |
|                 | GPA, skills, research experience, target degree, research        |
|                 | domain, sub-interests, target regions, target universities,      |
|                 | target labs, publications, timeline, language scores,            |
|                 | max_professors, email, additional_notes).                        |
| run_all         | The user wants to run the FULL pipeline (search → research →    |
|                 | resume → email → send).                                         |
| run_research    | The user wants to run ONLY the research part (Agent 1 + 2).     |
| run_email       | The user wants to run ONLY email part (Agent 3 + 4 + 5), i.e.  |
|                 | generate resumes and emails from existing research data.         |
| show_profile    | The user wants to SEE / review their current stored profile.     |
| edit_profile    | The user wants to CHANGE a specific field of their profile.      |
| chat            | Anything else — greetings, questions, general conversation.      |

## Slot extraction

When intent = "update_profile" or "edit_profile", extract the changed fields
into a "fields" dict. Use the EXACT keys from the schema:
  name, email, current_school, current_degree, major, gpa,
  target_degree, research_domain, sub_interests (list), target_regions (list),
  target_universities (list), target_labs (list), skills (list),
  research_experience, publications (list), timeline, language_scores,
  additional_notes, max_professors (int)

Only include fields that the user ACTUALLY mentioned — do NOT hallucinate.

## Current profile snapshot

{profile_snapshot}

## Rules
- If a field the user provides ALREADY EXISTS and is IDENTICAL in the profile,
  set intent = "chat" and reply normally mentioning the info is already recorded.
- If the info is NEW or DIFFERENT, use intent = "update_profile".
- Return ONLY valid JSON. No markdown fences.

## Output format
{{
  "intent": "<one of the intents above>",
  "fields": {{ ... }},          // only for update_profile / edit_profile
  "reply":  "<natural language reply to the user in 中文, 1-3 sentences>"
}}
"""


def classify_intent(
    messages: list[Dict[str, str]],
    current_profile: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Classify the latest user message's intent.

    Parameters
    ----------
    messages        : full conversation history (system + user/assistant turns)
    current_profile : current user_profile.json contents (or blank)

    Returns
    -------
    {"intent": str, "fields": dict, "reply": str}
    """
    # Build a compact profile snapshot for the system prompt
    snapshot_lines = []
    for k, v in current_profile.items():
        if isinstance(v, list):
            v_str = ", ".join(v) if v else "(empty)"
        elif not v:
            v_str = "(empty)"
        else:
            v_str = str(v)
        snapshot_lines.append(f"  {k}: {v_str}")
    snapshot = "\n".join(snapshot_lines)

    system = _INTENT_SYSTEM.replace("{profile_snapshot}", snapshot)

    # Build message list: system + conversation history
    llm_messages = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] != "system":
            llm_messages.append(m)

    raw = call_llm_chat(
        llm_messages,
        json_mode=True,
        agent_id=0,
        step="intent_classify",
    )

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"Intent classification JSON parse failed: {raw[:300]}")
        return {"intent": "chat", "fields": {}, "reply": raw[:200]}

    # Validate intent
    intent = result.get("intent", "chat")
    if intent not in VALID_INTENTS:
        intent = "chat"
    result["intent"] = intent
    result.setdefault("fields", {})
    result.setdefault("reply", "")

    return result
