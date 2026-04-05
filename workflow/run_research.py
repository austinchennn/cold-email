#!/usr/bin/env python3
"""
Research-Only Runner  (Agents 1 & 2)
======================================
运行 Agent1（教授发现）+ Agent2（深度调研），将结果保存到 JSON 后退出。
后续步骤（简历 / 邮件 / 发送）完全不会触发。

用法:
-----
  cd workflow

  # 交互模式
  python run_research.py

  # 命令行模式
  python run_research.py --domain "NLP" --max 5

  # 指定输出文件（可选）
  python run_research.py --domain "computer vision" --max 3 --out my_research.json

输出:
-----
  data/professors/raw_list.json          ← Agent1 原始列表（由 Agent1 自动写入）
  data/professors/deep_research/*.json   ← Agent2 每位教授的深度档案
  <out>.json                             ← 本脚本额外汇总的完整结果（可选）
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# 确保包根在 sys.path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import MAX_PROFESSORS, PROFESSORS_DIR, DEEP_RESEARCH_DIR
from agents.agent1_search   import Agent1Search
from agents.agent2_research import Agent2Research

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(name)-22s]  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_research")

_SEP  = "─" * 62
_SEP2 = "═" * 62


def run_research(domain: str, max_professors: int = MAX_PROFESSORS,
                 out_path: Path | None = None) -> list[dict]:
    """
    运行 Agent1 + Agent2，返回所有深度调研结果列表。

    Parameters
    ----------
    domain         : 研究方向，例如 "NLP"、"robotics"
    max_professors : 最多调研多少位教授
    out_path       : 可选，将汇总 JSON 写入此路径

    Returns
    -------
    list of research dicts (Agent2 输出格式)
    """

    # ── Agent 1 ──────────────────────────────────────────────────────────────
    print(f"\n{_SEP}")
    print(f"  AGENT 1 — 教授发现")
    print(f"  领域: {domain}   最多: {max_professors} 位")
    print(_SEP)

    professors = Agent1Search().run(domain, max_count=max_professors)

    if not professors:
        logger.error("未找到任何教授，请检查网络或 API Key。")
        return []

    print(f"  ✓ 发现 {len(professors)} 位教授\n")

    # ── Agent 2 ──────────────────────────────────────────────────────────────
    print(f"{_SEP}")
    print(f"  AGENT 2 — 深度调研")
    print(_SEP)

    all_research: list[dict] = []

    for idx, prof in enumerate(professors, 1):
        name = prof.get("name", "Unknown")
        univ = prof.get("university", "")
        dept = prof.get("department", "")
        print(f"\n  [{idx:02d}/{len(professors):02d}]  {name}")
        print(f"          {univ}  ·  {dept}")

        try:
            research = Agent2Research().run(prof)
            all_research.append(research)
            slug = research.get("slug", "?")
            print(f"  ✓ 调研完成 → deep_research/{slug}_prof.json")
        except Exception as exc:
            logger.error(f"Agent2 处理 {name} 时出错: {exc}")
            # 保留基础信息，不中断后续
            all_research.append({"name": name, "slug": "", "_error": str(exc), **prof})

    # ── 汇总输出 ──────────────────────────────────────────────────────────────
    if out_path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(all_research, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n  汇总结果已写入: {out_path}")

    print(f"\n{_SEP2}")
    print(f"  调研完成 — 共处理 {len(all_research)} 位教授")
    print(f"  原始列表 : {PROFESSORS_DIR / 'raw_list.json'}")
    print(f"  深度档案 : {DEEP_RESEARCH_DIR}/")
    print(_SEP2 + "\n")

    # ── 打印摘要表格 ──────────────────────────────────────────────────────────
    _print_summary(all_research)

    return all_research


def _print_summary(results: list[dict]) -> None:
    """Pretty-print a quick summary table."""
    print(f"{'序号':>3}  {'姓名':<22}  {'大学':<24}  {'研究方向 (前 3)'}") 
    print("─" * 90)
    for i, r in enumerate(results, 1):
        name  = r.get("name", "?")[:22]
        univ  = r.get("university", "?")[:24]
        dirs  = ", ".join(r.get("sub_directions", [])[:3])[:40]
        err   = "  ⚠ 调研出错" if r.get("_error") else ""
        print(f"{i:>3}  {name:<22}  {univ:<24}  {dirs}{err}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="运行 Agent1 + Agent2（不发邮件）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  python run_research.py --domain 'NLP' --max 3",
    )
    parser.add_argument("--domain", "-d", type=str, default="",
                        help="研究方向，例如 NLP、computer vision、robotics")
    parser.add_argument("--max", "-n", type=int, default=MAX_PROFESSORS,
                        help=f"最多调研几位教授（默认 {MAX_PROFESSORS}）")
    parser.add_argument("--out", "-o", type=str, default="",
                        help="可选：将汇总 JSON 写入此文件路径")
    args = parser.parse_args()

    domain = args.domain.strip()
    if not domain:
        domain = input("请输入研究方向（例如 NLP / robotics / RL）: ").strip()
    if not domain:
        print("未输入研究方向，退出。")
        sys.exit(1)

    out_path = Path(args.out) if args.out else None

    run_research(domain=domain, max_professors=args.max, out_path=out_path)


if __name__ == "__main__":
    main()
