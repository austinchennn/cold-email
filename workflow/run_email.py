#!/usr/bin/env python3
"""
Email-Only Runner  (Agents 3, 4 & 5)
======================================
读取已有的 Agent2 深度调研 JSON，依次执行：
  Agent3 — 定制简历（LaTeX → PDF 可选）
  Agent4 — 撰写冷邮件
  Agent5 — Gmail 发送（GMAIL_ENABLED=false 时为 dry-run）

前提: 已运行过 run_research.py，deep_research/*.json 文件存在。

用法:
-----
  cd workflow

  # 处理所有已调研的教授
  python run_email.py

  # 只处理某一个 slug（精确匹配文件名前缀）
  python run_email.py --slug alice_johnson

  # 指定调研目录（默认 data/professors/deep_research/）
  python run_email.py --research-dir /path/to/my_research.json

注意:
-----
  发送前请确认 .env 中 GMAIL_ENABLED=true，否则仅生成文件不发送。
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    DEEP_RESEARCH_DIR, TAILORED_RESUMES_DIR, EMAILS_DIR, GMAIL_ENABLED,
)
from agents.agent3_resume import Agent3Resume
from agents.agent4_email  import Agent4Email
from agents.agent5_send   import Agent5Send

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(name)-22s]  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_email")

_SEP  = "─" * 62
_SEP2 = "═" * 62


def run_email(research_list: list[dict]) -> list[dict]:
    """
    对 research_list 中的每位教授执行 Agent3-4-5。

    Returns
    -------
    同 research_list，每项追加 resume / email / gmail_id 字段。
    """
    send_label = "LIVE SEND" if GMAIL_ENABLED else "dry-run（GMAIL_ENABLED=false）"
    results = []

    for idx, research in enumerate(research_list, 1):
        name = research.get("name", "Unknown")
        print(f"\n{_SEP}")
        print(f"  [{idx:02d}/{len(research_list):02d}]  {name}")
        print(_SEP)

        # Agent 3 — 定制简历
        print("  → Agent 3 : 定制简历 …")
        try:
            resume_path = Agent3Resume().run(research)
        except Exception as exc:
            logger.error(f"Agent3 失败: {exc}")
            results.append({**research, "_error_agent3": str(exc)})
            continue

        # Agent 4 — 撰写冷邮件
        print("  → Agent 4 : 撰写冷邮件 …")
        try:
            email_path = Agent4Email().run(research, resume_path)
        except Exception as exc:
            logger.error(f"Agent4 失败: {exc}")
            results.append({**research, "resume": str(resume_path), "_error_agent4": str(exc)})
            continue

        # Agent 5 — Gmail 发送
        print(f"  → Agent 5 : Gmail 发送（{send_label}）…")
        try:
            gmail_id = Agent5Send().run(research, email_path)
        except Exception as exc:
            logger.error(f"Agent5 失败: {exc}")
            gmail_id = None

        rel_resume = _rel(resume_path)
        rel_email  = _rel(email_path)
        print(f"  ✓  简历  : {rel_resume}")
        print(f"  ✓  邮件  : {rel_email}")
        if gmail_id:
            print(f"  ✓  已发送: gmail_id={gmail_id}")

        results.append({**research, "resume": str(rel_resume),
                        "email": str(rel_email), "gmail_id": gmail_id})

    # ── 汇总 ─────────────────────────────────────────────────────────────────
    sent  = sum(1 for r in results if r.get("gmail_id"))
    print(f"\n{_SEP2}")
    print(f"  完成 — 处理 {len(results)} 位教授")
    print(f"  简历目录 : {TAILORED_RESUMES_DIR}")
    print(f"  邮件目录 : {EMAILS_DIR}")
    if GMAIL_ENABLED:
        print(f"  发送结果 : {sent}/{len(results)} 封邮件已通过 Gmail 发出")
    else:
        print("  发送模式 : dry-run — 设置 GMAIL_ENABLED=true 以真实发送")
    print(_SEP2 + "\n")

    return results


def _rel(path: str) -> Path:
    try:
        return Path(path).relative_to(Path(__file__).parent)
    except ValueError:
        return Path(path)


def _load_research(research_dir: Path, slug_filter: str) -> list[dict]:
    """从 deep_research/ 目录加载已有调研 JSON。"""
    if not research_dir.exists():
        logger.error(f"调研目录不存在: {research_dir}")
        logger.error("请先运行 python run_research.py")
        sys.exit(1)

    files = sorted(research_dir.glob("*_prof.json"))
    if not files:
        logger.error(f"在 {research_dir} 中未找到任何 *_prof.json 文件。")
        logger.error("请先运行 python run_research.py")
        sys.exit(1)

    if slug_filter:
        files = [f for f in files if f.name.startswith(slug_filter)]
        if not files:
            logger.error(f"未找到 slug 包含 '{slug_filter}' 的调研文件。")
            logger.error(f"可用文件: {[f.name for f in sorted(research_dir.glob('*_prof.json'))]}")
            sys.exit(1)

    result = []
    for f in files:
        try:
            result.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning(f"跳过无法解析的文件 {f.name}: {exc}")

    print(f"  载入 {len(result)} 份调研档案（来自 {research_dir}）\n")
    return result


# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="读取 Agent2 调研结果，运行 Agent3+4+5（简历/邮件/发送）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  python run_email.py --slug alice_johnson",
    )
    parser.add_argument("--slug", "-s", type=str, default="",
                        help="只处理指定 slug 前缀的教授（默认处理全部）")
    parser.add_argument("--research-dir", type=str,
                        default=str(DEEP_RESEARCH_DIR),
                        help=f"调研 JSON 目录（默认 {DEEP_RESEARCH_DIR}）")
    args = parser.parse_args()

    research_dir = Path(args.research_dir)
    research_list = _load_research(research_dir, args.slug.strip())
    run_email(research_list)


if __name__ == "__main__":
    main()
