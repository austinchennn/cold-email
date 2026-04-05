"""
Agent 0 — LLM-Driven Intake / Gateway Agent
===============================================
用户直接和 LLM 对话，LLM 自动：
  1. 识别意图（新信息 / 重复信息 / 运行指令 / 闲聊）
  2. 提取并存储字段到 user_profile.json
  3. 路由到不同 workflow（run_all / run_research / run_email）

与旧版"Python 逐题提问"不同——这是一个自由对话 agent，
每条消息都经 LLM 意图识别 + 信息抽取。

Input  : 用户自然语言消息（终端 / Dashboard 聊天窗口）
Output : data/user_profile.json  (持续更新)

UserProfile schema
------------------
{
  "name":                  str,
  "email":                 str,
  "current_school":        str,
  "current_degree":        str,
  "major":                 str,
  "gpa":                   str,
  "target_degree":         str,
  "research_domain":       str,
  "sub_interests":         [str],
  "target_regions":        [str],
  "target_universities":   [str],
  "target_labs":           [str],
  "skills":                [str],
  "research_experience":   str,
  "publications":          [str],
  "timeline":              str,
  "language_scores":       str,
  "additional_notes":      str,
  "max_professors":        int,
}
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import DATA_DIR, MAX_PROFESSORS
from skills.llm_client import call_llm_chat
from skills.intent_router import classify_intent
from skills.event_bus import bus, Event, EventType

logger = logging.getLogger(__name__)

PROFILE_PATH = DATA_DIR / "user_profile.json"

# 必填字段 — 缺任何一个都要强制 interview
REQUIRED_FIELDS = [
    "name", "email", "current_school", "target_degree",
    "research_domain", "skills",
]


# ── 空白 profile 模板 ────────────────────────────────────────────────────────

def _blank_profile() -> Dict[str, Any]:
    return {
        "name":                "",
        "email":               "",
        "current_school":      "",
        "current_degree":      "",
        "major":               "",
        "gpa":                 "",
        "target_degree":       "",
        "research_domain":     "",
        "sub_interests":       [],
        "target_regions":      [],
        "target_universities": [],
        "target_labs":         [],
        "skills":              [],
        "research_experience": "",
        "publications":        [],
        "timeline":            "",
        "language_scores":     "",
        "additional_notes":    "",
        "max_professors":      MAX_PROFESSORS,
    }


# ── LLM 对话系统 prompt ──────────────────────────────────────────────────────

_CHAT_SYSTEM = """\
你是 ColdEmail 智能套磁助手 (Agent 0)。你的职责是通过自然对话收集用户信息，
帮助用户找到合适的教授并发送套磁邮件。

## 你的行为准则

1. **信息采集阶段**: 你需要收集以下信息，但不要像问卷一样逐项提问。
   而是像一个经验丰富的留学顾问一样，自然地在对话中引导用户提供信息。
   每轮对话可以多聊几个相关的点，而不是死板地一个一个问。

   必填信息:
   - 姓名 (name)
   - 联系邮箱 (email)
   - 当前学校 (current_school)
   - 目标学位 (target_degree): PhD / 硕士 / 暑研 / RA
   - 研究方向 (research_domain)
   - 技术栈 (skills)

   可选但有帮助的信息:
   - 学位/年级 (current_degree)
   - 专业 (major)
   - GPA (gpa)
   - 细分兴趣方向 (sub_interests)
   - 目标地区 (target_regions)
   - 目标院校 (target_universities)
   - 目标实验室/教授 (target_labs)
   - 研究经历 (research_experience)
   - 论文 (publications)
   - 入学时间 (timeline)
   - 语言成绩 (language_scores)
   - 搜索教授数量 (max_professors, 默认 {max_professors})
   - 补充说明 (additional_notes)

2. **对话风格**: 友好、专业。用中文交流。适当使用 emoji 增加亲和力。
   一次可以问 2-3 个相关的问题，不要一个一个问。

3. **进度追踪**: 在内心追踪已收集和未收集的信息，
   当关键信息差不多了，主动提醒用户"基本信息已收集完毕，
   你可以输入 /run 开始搜索教授"。

4. **重复信息**: 如果用户提供的信息和已存档的完全一样，简短确认即可。

## 当前存档状态

{profile_status}

## 未收集的必填信息

{missing_fields}

请根据以上状态继续对话。如果有很多必填字段缺失，先从最重要的开始问起。
如果必填信息已经齐全，可以聊可选信息，或者提醒用户可以开始搜索了。
"""


class Agent0Intake:
    """LLM-driven conversational intake agent."""

    AGENT_ID = 0

    def __init__(self):
        self._profile: Dict[str, Any] = self.load() or _blank_profile()
        self._history: List[Dict[str, str]] = []

    # ── Profile persistence ───────────────────────────────────────────────────

    @staticmethod
    def load() -> Optional[Dict[str, Any]]:
        """Load existing profile if present."""
        if PROFILE_PATH.exists():
            try:
                return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def save(self) -> None:
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_PATH.write_text(
            json.dumps(self._profile, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @property
    def profile(self) -> Dict[str, Any]:
        return self._profile

    @property
    def history(self) -> List[Dict[str, str]]:
        return self._history

    # ── Profile completeness check ────────────────────────────────────────────

    def missing_required(self) -> List[str]:
        """Return list of required fields that are still empty."""
        missing = []
        for f in REQUIRED_FIELDS:
            val = self._profile.get(f)
            if not val or (isinstance(val, list) and len(val) == 0):
                missing.append(f)
        return missing

    def is_profile_complete(self) -> bool:
        """True if ALL required fields are populated."""
        return len(self.missing_required()) == 0

    def auto_fill_missing(self) -> Dict[str, Any]:
        """Use LLM to infer reasonable defaults for missing required fields."""
        missing = self.missing_required()
        if not missing:
            return {}

        history_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in self._history[-10:]
        ) or "(无对话历史)"

        known = {k: v for k, v in self._profile.items() if v and v != []}
        known_text = json.dumps(known, ensure_ascii=False, indent=2) if known else "(空)"

        prompt = (
            "根据对话历史和已有信息，为缺失字段推断合理默认值。\n\n"
            f"## 已有信息\n{known_text}\n\n"
            f"## 对话历史\n{history_text}\n\n"
            f"## 需要补充的字段\n{', '.join(missing)}\n\n"
            "## 推断规则\n"
            "- name: 对话中未提及则用 \"User\"\n"
            "- email: 未提及则用 \"pending@example.com\"\n"
            "- target_degree: 默认 \"PhD\"\n"
            "- research_domain: 必须从对话推断，不可随意编造\n"
            "- skills: 可从研究方向推断常见技能\n"
            "- current_school: 未提及则用 \"未指定\"\n\n"
            "只返回 JSON，key 是字段名，value 是推断值。列表用数组。"
        )

        raw = call_llm_chat(
            [{"role": "system", "content": prompt}],
            json_mode=True,
            agent_id=self.AGENT_ID,
            step="auto_fill",
        )

        try:
            filled = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"Auto-fill JSON parse failed: {raw[:300]}")
            return {}

        changed = self._merge_fields(filled)
        if changed:
            self.save()
            logger.info(f"Agent0: auto-filled fields: {list(changed.keys())}")
        return changed

    # ── Core chat method ──────────────────────────────────────────────────────

    def chat(self, user_message: str) -> Dict[str, Any]:
        """
        Process one user message. Returns:
        {
            "reply":   str,       # LLM 的回复文本
            "intent":  str,       # 识别到的意图
            "fields":  dict,      # 本轮更新的字段 (可能为空)
            "profile": dict,      # 更新后的完整 profile
        }
        """
        bus.post(Event(EventType.AGENT_START, self.AGENT_ID,
                       {"step": "chat_turn"}))

        # 追加用户消息到历史
        self._history.append({"role": "user", "content": user_message})

        # ── Step 1: 意图识别 + 信息提取 ──────────────────────────────────────
        intent_result = classify_intent(self._history, self._profile)
        intent = intent_result["intent"]
        fields = intent_result.get("fields", {})
        llm_reply = intent_result.get("reply", "")

        # ── Step 2: 根据意图行动 ─────────────────────────────────────────────
        updated_fields = {}


        if fields:
            updated_fields = self._merge_fields(fields)
            if updated_fields:
                self.save()
                logger.info(f"Agent0: updated fields: {list(updated_fields.keys())}")

        # ── Step 3: 如果意图识别只返回了简短 reply，用对话 LLM 补充 ────────
        if not llm_reply or len(llm_reply) < 5:
            llm_reply = self._generate_reply()

        # 追加 assistant 回复到历史
        self._history.append({"role": "assistant", "content": llm_reply})

        bus.post(Event(EventType.AGENT_COMPLETE, self.AGENT_ID,
                       {"intent": intent,
                        "updated": list(updated_fields.keys())}))

        return {
            "reply":   llm_reply,
            "intent":  intent,
            "fields":  updated_fields,
            "profile": self._profile,
        }

    # ── Interview mode (forced at startup) ────────────────────────────────────

    def start_interview(self) -> str:
        """
        生成 interview 开场白。在 Dashboard 启动或 CLI 首次运行时调用。
        """
        missing = self.missing_required()

        if not missing:
            # 资料齐全
            return self._generate_reply_with_context(
                "用户资料已经完整。跟用户打个招呼，简要说明已有的信息，"
                "问问有没有要更新的，并提醒可以输入 /run 开始搜索教授。"
            )

        # 资料不完整 — 开始采集
        return self._generate_reply_with_context(
            "这是第一次使用，需要采集用户信息。先友好地打个招呼，然后自然地开始问"
            f"最重要的缺失信息：{', '.join(missing[:3])}。"
            "不要一口气全问，先从 2-3 个最关键的开始。"
        )

    # ── Terminal interactive loop ─────────────────────────────────────────────

    def run_interactive(self) -> Dict[str, Any]:
        """
        在终端中运行交互式对话。返回最终的 profile。
        输入 /run、/research、/email 触发对应 workflow；
        输入 /profile 查看档案；输入 /quit 退出。
        """
        print()
        print("=" * 62)
        print("  ColdEmail 智能套磁助手")
        print("  输入 /run 开始完整流程 | /research 只搜索")
        print("  输入 /profile 查看档案 | /quit 退出")
        print("=" * 62)

        # 开场白
        greeting = self.start_interview()
        print(f"\n  [Bot] {greeting}\n")
        self._history.append({"role": "assistant", "content": greeting})

        while True:
            try:
                user_input = input("  你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  再见！")
                break

            if not user_input:
                continue

            # 命令快捷键
            cmd = user_input.lower()
            if cmd in ("/quit", "/exit", "/q"):
                print("  再见！")
                break
            if cmd == "/profile":
                self._print_profile()
                continue
            if cmd in ("/run", "/start", "/run_all"):
                return {"_action": "run_all", "profile": self._profile}
            if cmd in ("/research", "/search"):
                return {"_action": "run_research", "profile": self._profile}
            if cmd in ("/email", "/send"):
                return {"_action": "run_email", "profile": self._profile}

            # 正常对话
            result = self.chat(user_input)
            reply = result["reply"]
            intent = result["intent"]
            fields = result["fields"]

            # 显示回复
            print(f"\n  [Bot] {reply}")
            if fields:
                print(f"     [Updated] {', '.join(fields.keys())}")

            # 如果 LLM 识别到运行意图
            if intent == "run_all":
                return {"_action": "run_all", "profile": self._profile}
            if intent == "run_research":
                return {"_action": "run_research", "profile": self._profile}
            if intent == "run_email":
                return {"_action": "run_email", "profile": self._profile}
            if intent == "show_profile":
                self._print_profile()

            print()

        self.save()
        return {"_action": "quit", "profile": self._profile}

    # ── Private helpers ───────────────────────────────────────────────────────

    def _merge_fields(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge extracted fields into profile.
        Returns dict of actually changed fields.
        """
        changed = {}
        for key, value in fields.items():
            if key not in self._profile:
                continue
            if not value:
                continue

            old = self._profile[key]

            # 类型对齐
            if isinstance(old, list) and isinstance(value, str):
                value = [s.strip() for s in value.replace("\uff0c", ",").split(",") if s.strip()]
            elif isinstance(old, int) and not isinstance(value, int):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    continue

            # 是否真的有变化
            if value != old:
                self._profile[key] = value
                changed[key] = value

        return changed

    def _generate_reply(self) -> str:
        """Generate a conversational reply using full history."""
        system = self._build_system_prompt()
        messages = [{"role": "system", "content": system}] + self._history
        return call_llm_chat(messages, agent_id=self.AGENT_ID, step="chat_reply")

    def _generate_reply_with_context(self, instruction: str) -> str:
        """Generate a reply with a specific instruction appended."""
        system = self._build_system_prompt() + f"\n\n## 本轮特别指令\n{instruction}"
        messages = [{"role": "system", "content": system}]
        if self._history:
            messages += self._history
        else:
            messages.append({"role": "user", "content": "(系统启动，请开始对话)"})
        return call_llm_chat(messages, agent_id=self.AGENT_ID, step="interview_start")

    def _build_system_prompt(self) -> str:
        """Build the system prompt with current profile status."""
        status_lines = []
        labels = {
            "name": "姓名", "email": "邮箱", "current_school": "学校",
            "current_degree": "学位", "major": "专业", "gpa": "GPA",
            "target_degree": "目标", "research_domain": "研究领域",
            "sub_interests": "细分方向", "target_regions": "目标地区",
            "target_universities": "目标院校", "target_labs": "目标实验室",
            "skills": "技术栈", "research_experience": "研究经历",
            "publications": "论文", "timeline": "时间线",
            "language_scores": "语言成绩", "max_professors": "搜索教授数",
            "additional_notes": "补充",
        }
        for key, label in labels.items():
            val = self._profile.get(key, "")
            if isinstance(val, list):
                val_str = ", ".join(val) if val else "未填写"
            elif not val:
                val_str = "未填写"
            else:
                val_str = str(val)
            status_lines.append(f"  {label}: {val_str}")

        profile_status = "\n".join(status_lines)

        missing = self.missing_required()
        if missing:
            missing_str = ", ".join(missing)
        else:
            missing_str = "全部必填信息已收集完毕！可以开始搜索教授了。"

        return _CHAT_SYSTEM.format(
            max_professors=MAX_PROFESSORS,
            profile_status=profile_status,
            missing_fields=missing_str,
        )

    def _print_profile(self) -> None:
        """Print current profile to terminal."""
        labels = {
            "name": "姓名", "email": "邮箱", "current_school": "学校",
            "current_degree": "学位", "major": "专业", "gpa": "GPA",
            "target_degree": "目标", "research_domain": "研究领域",
            "sub_interests": "细分方向", "target_regions": "目标地区",
            "target_universities": "目标院校", "target_labs": "目标实验室",
            "skills": "技术栈", "research_experience": "研究经历",
            "publications": "论文", "timeline": "时间线",
            "language_scores": "语言成绩", "max_professors": "搜索教授数",
            "additional_notes": "补充",
        }
        print("\n  Current Profile:")
        print("  " + "-" * 50)
        for key, label in labels.items():
            val = self._profile.get(key, "")
            if isinstance(val, list):
                val = ", ".join(val) if val else "(none)"
            elif not val:
                val = "(none)"
            print(f"    {label:<12}: {val}")
        print("  " + "-" * 50)
        complete = "Complete" if self.is_profile_complete() else "Missing required fields"
        print(f"    Status: {complete}\n")


def build_search_context(profile: Dict) -> str:
    """
    将 UserProfile 转化为一段文本，供 Agent1 的 LLM prompt 使用。
    让搜索更精准：考虑地区偏好、细分方向、目标院校等。
    """
    parts = []

    if profile.get("research_domain"):
        parts.append(f"Primary research domain: {profile['research_domain']}")

    if profile.get("sub_interests"):
        parts.append(f"Specific sub-interests: {', '.join(profile['sub_interests'])}")

    if profile.get("target_regions"):
        parts.append(f"Preferred regions/countries: {', '.join(profile['target_regions'])}")

    if profile.get("target_universities"):
        parts.append(f"Target universities (prioritise): {', '.join(profile['target_universities'])}")

    if profile.get("target_labs"):
        parts.append(f"Already interested in these labs/professors: {', '.join(profile['target_labs'])}")

    if profile.get("target_degree"):
        parts.append(f"Seeking: {profile['target_degree']}")

    if profile.get("skills"):
        parts.append(f"Applicant's technical skills: {', '.join(profile['skills'])}")

    if profile.get("research_experience"):
        parts.append(f"Applicant's research background: {profile['research_experience']}")

    if profile.get("timeline"):
        parts.append(f"Target start date: {profile['timeline']}")

    return "\n".join(parts)
