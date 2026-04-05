#!/usr/bin/env python3
"""
Intake + Research Runner  (Agent 0 → Agent 1 → Agent 2)
=========================================================
先通过交互式对话采集用户信息，然后自动调用 Agent1+2 进行教授搜索与深度调研。

用法:
-----
  cd workflow

  # 交互模式（推荐）— 从问答开始
  python run_intake.py

  # 跳过采集，复用已有档案
  python run_intake.py --reuse

  # 只做采集，不跑 research
  python run_intake.py --intake-only

  # 完整参数
  python run_intake.py --reuse --max 5 --out results.json
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agents.agent0_intake import Agent0Intake, build_search_context, PROFILE_PATH
from agents.agent1_search  import Agent1Search
from agents.agent2_research import Agent2Research
from config.settings import MAX_PROFESSORS, PROFESSORS_DIR, DEEP_RESEARCH_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(name)-22s]  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_intake")

_SEP  = "─" * 62
_SEP2 = "═" * 62


def main() -> None:
    parser = argparse.ArgumentParser(
        description="交互式采集用户信息 → 搜索教授 → 深度调研",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--reuse", "-r", action="store_true",
                        help="复用已有的 user_profile.json，跳过采集问答")
    parser.add_argument("--intake-only", action="store_true",
                        help="只做信息采集，不运行 Agent1/2 搜索")
    parser.add_argument("--max", "-n", type=int, default=0,
                        help="最多调研几位教授（0=使用档案中的值或默认值）")
    parser.add_argument("--out", "-o", type=str, default="",
                        help="可选：将汇总 JSON 写入此文件路径")
    args = parser.parse_args()

    # ── Step 1: Intake ────────────────────────────────────────────────────────
    intake = Agent0Intake()

    if args.reuse:
        profile = intake.load()
        if not profile:
            print(f"  ⚠  未找到已有档案 ({PROFILE_PATH})，将进入采集流程。\n")
            result = intake.run_interactive()
            profile = result["profile"]
        elif not intake.is_profile_complete():
            print(f"\n  📂 已有档案但必填信息不完整，进入补充流程。")
            result = intake.run_interactive()
            profile = result["profile"]
        else:
            print(f"\n  📂 复用已有档案: {PROFILE_PATH}")
            intake._print_profile()
            confirm = input("\n  继续使用此档案？(Y/n): ").strip().lower()
            if confirm in ("n", "no"):
                result = intake.run_interactive()
                profile = result["profile"]
    else:
        result = intake.run_interactive()
        action = result.get("_action", "quit")
        profile = result["profile"]
        if action == "quit":
            return

    if args.intake_only:
        print("\n  ✅ 信息采集完成。使用 --reuse 运行搜索：")
        print(f"     python run_intake.py --reuse")
        return

    # ── Step 2: Research (Agent1 + Agent2) ────────────────────────────────────
    domain = profile.get("research_domain", "")
    if not domain:
        print("  ⚠  未指定研究领域，无法进行搜索。")
        return

    max_prof = args.max or profile.get("max_professors", MAX_PROFESSORS)
    search_context = build_search_context(profile)

    # ── Agent 1 ───────────────────────────────────────────────────────────────
    print(f"\n{_SEP}")
    print(f"  AGENT 1 — 教授发现")
    print(f"  领域: {domain}   最多: {max_prof} 位")
    if profile.get("target_regions"):
        print(f"  地区: {', '.join(profile['target_regions'])}")
    if profile.get("target_universities"):
        print(f"  目标院校: {', '.join(profile['target_universities'])}")
    print(_SEP)

    agent1 = Agent1Search()
    professors = agent1.run(domain, max_count=max_prof,
                            user_context=search_context)

    if not professors:
        logger.error("未找到任何教授，请检查网络或 API Key。")
        return

    print(f"  ✓ 发现 {len(professors)} 位教授\n")

    # ── Agent 2 ───────────────────────────────────────────────────────────────
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
            research = Agent2Research().run(prof, user_context=search_context)
            all_research.append(research)
            slug = research.get("slug", "?")
            print(f"  ✓ 调研完成 → deep_research/{slug}_prof.json")
        except Exception as exc:
            logger.error(f"Agent2 处理 {name} 时出错: {exc}")
            all_research.append({"name": name, "slug": "", "_error": str(exc), **prof})

    # ── 汇总 ──────────────────────────────────────────────────────────────────
    out_path = Path(args.out) if args.out else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(all_research, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n  汇总结果已写入: {out_path}")

    print(f"\n{_SEP2}")
    print(f"  调研完成 — 共处理 {len(all_research)} 位教授")
    print(f"  用户档案 : {PROFILE_PATH}")
    print(f"  原始列表 : {PROFESSORS_DIR / 'raw_list.json'}")
    print(f"  深度档案 : {DEEP_RESEARCH_DIR}/")
    print(_SEP2 + "\n")

    _print_summary(all_research)


def _print_summary(results: list[dict]) -> None:
    print(f"{'序号':>3}  {'姓名':<22}  {'大学':<24}  {'研究方向 (前 3)'}")
    print("─" * 90)
    for i, r in enumerate(results, 1):
        name  = r.get("name", "?")[:22]
        univ  = r.get("university", "?")[:24]
        dirs  = ", ".join(r.get("sub_directions", [])[:3])[:40]
        err   = "  ⚠ 调研出错" if r.get("_error") else ""
        print(f"{i:>3}  {name:<22}  {univ:<24}  {dirs}{err}")
    print()


if __name__ == "__main__":
    main()
