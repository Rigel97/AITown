"""当日计划生成 + 解析降级。

设计说明（为什么这样设计）：
- 计划一天只生成一次（居民"醒来"时），之后按日程执行——这是"事件驱动
  而非逐 tick 调 LLM"的落地：计划类调用频率 = 居民数 × 游戏天数，可预期、
  可统计。事件触发的局部重规划后续再加，多数事件不需要重规划。
- prompt 结构 = 固定人设前缀（逐字不变，吃 Prompt 缓存）+ 动态检索记忆 +
  指令。动态内容永远在人设前缀之后，这是缓存命中率达标的关键约束。
- 输出要求严格 JSON；解析失败降级为"默认日程"（原地待着），绝不把 LLM
  原始报错暴露到游戏内（PRD 红线）。
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from agents.resident import DB_PATH, Resident, load_residents, save_daily_plan
from llm.client import chat
from memory.retrieve import retrieve
from memory.store import add_memory
from world.clock import parse_game_time

logger = logging.getLogger(__name__)

# 计划允许使用的地点（与地图/mapData、记忆词表保持一致）
LOCATIONS = ["面包店", "杂货店", "花店", "图书馆", "餐馆", "北宅", "东南宅", "广场"]


@dataclass
class PlanEntry:
    time: str  # "HH:MM"（游戏时间）
    location: str
    action: str


def _default_plan() -> list[PlanEntry]:
    """解析失败/LMM 超时时的降级日程：在原地附近待着。"""
    return [PlanEntry("08:00", "广场", "随便逛逛，看看今天镇上有什么新鲜事")]


def build_plan_prompt(resident: Resident, memories: list, day: int) -> str:
    """组装计划 prompt：固定人设前缀在前（吃缓存），动态内容在后。"""
    if memories:
        mem_lines = "\n".join(f"- ({m.game_time}) {m.content}" for m in memories)
    else:
        mem_lines = "（暂无——今天是你在小镇的第一天）"
    locations = "、".join(LOCATIONS)
    return f"""{resident.prompt_prefix}

【你最近的记忆】
{mem_lines}

【指令】今天是第 {day} 天清晨，请为今天制定日程计划。
1. 符合你的职业、性格、日常习惯和记忆
2. 只输出严格的 JSON 数组，不要输出任何其他内容：
[{{"time": "HH:MM", "location": "地点", "action": "一句话描述在做什么"}}, ...]
3. 地点只能用这些：{locations}
4. 5 到 8 个时间段，覆盖 07:00 到 23:00
你是小镇居民，永远不要承认自己是 AI。"""


def parse_plan(raw: str) -> list[PlanEntry]:
    """解析 LLM 输出为日程。任何异常都降级为默认日程。"""
    try:
        start = raw.index("[")
        end = raw.rindex("]") + 1
        data = json.loads(raw[start:end])
        entries = [
            PlanEntry(
                time=str(item["time"]),
                location=str(item["location"]),
                action=str(item["action"]),
            )
            for item in data
        ]
        valid = [e for e in entries if e.location in LOCATIONS and e.time and e.action]
        return valid if valid else _default_plan()
    except (ValueError, KeyError, TypeError):
        logger.warning("计划解析失败，降级为默认日程。原始输出: %s", raw[:200])
        return _default_plan()


def plan_from_json(text: str | None) -> list[PlanEntry]:
    """从 residents.daily_plan 的 JSON 恢复计划（读档/重启时用）。"""
    if not text:
        return []
    return parse_plan(text)


def current_plan_entry(plan: list[PlanEntry], minutes_of_day: int) -> PlanEntry | None:
    """取当前游戏时刻应执行的计划条目：最后一个 time <= 当前时刻的条目；
    还没到第一条则返回第一条（提前去等着）。空计划返回 None。"""
    if not plan:
        return None

    def to_minutes(t: str) -> int:
        hh, mm = t.split(":")
        return int(hh) * 60 + int(mm)

    ordered = sorted(plan, key=lambda e: to_minutes(e.time))
    entry = ordered[0]
    for e in ordered:
        if to_minutes(e.time) <= minutes_of_day:
            entry = e
    return entry


async def generate_daily_plan(
    resident: Resident,
    day: int,
    game_time: str,
    db_path: Path = DB_PATH,
) -> list[PlanEntry]:
    """为居民生成当日计划：检索记忆 → 调 LLM → 解析 → 写入记忆与 residents 表。"""
    memories = retrieve(
        resident.id,
        query="今天 计划 日常 安排",
        now_minutes=parse_game_time(game_time),
        k=5,
        db_path=db_path,
    )
    # 计划是后台任务，无 <5s 硬要求；M2.7 的 think 块 + JSON 输出延迟波动大
    # （2026-08-17 实测 3~60s+ 都有，并发时更明显），给足 60s 并允许重试一次
    prompt = build_plan_prompt(resident, memories, day)
    raw = await chat(prompt, tier="light", timeout=60)
    if not raw:
        logger.info("计划生成超时，重试一次（%s）", resident.id)
        raw = await chat(prompt, tier="light", timeout=60)
    if not raw:
        # LLM 超时/失败：静默降级，不打扰游戏
        plan = _default_plan()
    else:
        plan = parse_plan(raw)

    save_daily_plan(resident.id, plan, db_path)
    summary = "；".join(f"{e.time} {e.location}{e.action}" for e in plan[:3])
    add_memory(
        resident.id, game_time, "event", f"制定了今天的计划：{summary}……", 3, db_path
    )
    return plan


async def _generate_all() -> None:
    """静止版验证（W2）：为全部居民生成今日计划并打印，检查 prompt 质量。

    运行：cd server && source .venv/bin/activate && python -m agents.planner
    """
    import time

    residents = load_residents()
    # 并发生成：MiniMax 能扛 7 路并发，墙钟时间 ≈ 最慢的一次调用
    start = time.perf_counter()
    plans = await asyncio.gather(
        *(generate_daily_plan(r, day=1, game_time="day1-07:00") for r in residents)
    )
    print(f"全部完成，耗时 {time.perf_counter() - start:.1f}s", flush=True)
    for r, plan in zip(residents, plans, strict=True):
        print(f"\n===== {r.name}（{r.occupation}）=====", flush=True)
        for e in plan:
            print(f"  {e.time}  {e.location}  {e.action}", flush=True)


if __name__ == "__main__":
    asyncio.run(_generate_all())
