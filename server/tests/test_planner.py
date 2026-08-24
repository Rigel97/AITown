"""planner 的单元测试（mock chat，不真实调 LLM）。

锁定两条关键约束：
1. prompt 必须以人设前缀开头（Prompt 缓存命中的前提，前缀逐字固定）；
2. 解析对 LLM 的各种"不听话输出"都能容错降级，绝不向上抛异常。
"""

import json
import sqlite3
from pathlib import Path

import pytest

from agents import planner
from agents.planner import (
    PlanEntry,
    build_plan_prompt,
    current_plan_entry,
    generate_daily_plan,
    parse_plan,
)
from agents.resident import Resident, load_residents
from db.seed import seed
from memory.store import add_memory

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"

RESIDENT = Resident(
    id="baker_lin",
    name="林师傅",
    occupation="面包师",
    prompt_prefix="你是 AI 小镇的居民「林师傅」。",
    x=456,
    y=344,
)

VALID_JSON = '[{"time": "07:00", "location": "面包店", "action": "开门烤面包"}, {"time": "18:00", "location": "餐馆", "action": "去喝一杯"}]'


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA.read_text())
    return db


def test_prompt_starts_with_persona_prefix(tmp_path: Path) -> None:
    """人设前缀必须是 prompt 的逐字开头——缓存命中的前提。"""
    prompt = build_plan_prompt(
        RESIDENT, memories=[], day=1, db_path=_fresh_db(tmp_path)
    )
    assert prompt.startswith(RESIDENT.prompt_prefix)
    assert "JSON" in prompt  # 指令段在动态内容之后


def test_prompt_contains_world_context(tmp_path: Path) -> None:
    """五轮 H3（B3 前缀统一）：计划 prompt 补 world_context，且位置与
    对话路径同款（prefix 之后、记忆之前）——①计划调用与对话调用共享
    prefix+world_context 最长公共前缀（缓存）；②计划只提名单居民与
    合法地名（防"编人修椅子"同款计划层幻觉）。"""
    db = _fresh_db(tmp_path)
    seed(db)
    resident = load_residents(db)[0]
    add_memory(resident.id, "day1-09:00", "event", "昨天的事", 3, db)
    prompt = build_plan_prompt(resident, memories=[], day=2, db_path=db)
    assert prompt.startswith(resident.prompt_prefix)
    assert "【小镇】" in prompt
    assert prompt.index("【小镇】") < prompt.index("【你最近的记忆】")


def test_parse_plan_valid_json() -> None:
    plan = parse_plan(VALID_JSON)
    assert plan[0] == PlanEntry("07:00", "面包店", "开门烤面包")
    assert len(plan) == 2


def test_parse_plan_tolerates_surrounding_text() -> None:
    """LLM 在 JSON 前后包了客套话也能解析。"""
    raw = f"好的，这是我今天的计划：\n{VALID_JSON}\n希望今天顺利！"
    assert len(parse_plan(raw)) == 2


def test_parse_plan_garbage_falls_back_to_default() -> None:
    plan = parse_plan("我完全不知道该怎么做")
    assert len(plan) == 1
    assert plan[0].location == "广场"  # 默认日程


def test_parse_plan_filters_illegal_locations() -> None:
    raw = '[{"time": "08:00", "location": "月球", "action": "看星星"}]'
    plan = parse_plan(raw)
    assert plan[0].location == "广场"  # 非法地点被过滤后降级


# ---------- 畸形时间防线（2026-08-21 深检：脏时间曾可炸死主循环） ----------


def test_parse_plan_rejects_time_with_seconds() -> None:
    """Bug A：LLM 输出 "07:00:00"（带秒）——旧版照单全收入库，
    current_plan_entry 的时间解包直接抛 ValueError，主循环曾因此静默停摆。"""
    raw = '[{"time": "07:00:00", "location": "广场", "action": "晨练"}]'
    plan = parse_plan(raw)
    assert plan[0].location == "广场"  # 降级默认日程


def test_parse_plan_rejects_non_numeric_time() -> None:
    for bad in ("7点", "早上", "", "25:00", "07:5", "12:60"):
        raw = json.dumps(
            [{"time": bad, "location": "广场", "action": "x"}], ensure_ascii=False
        )
        assert parse_plan(raw)[0].location == "广场", bad


def test_parse_plan_normalizes_unpadded_hour() -> None:
    """合法但不规范的时间（"7:05"）规范化为 "07:05"，方便排序与时钟对齐。"""
    raw = '[{"time": "7:05", "location": "面包店", "action": "开门"}]'
    assert parse_plan(raw)[0].time == "07:05"


def test_current_plan_entry_never_raises_on_any_parsed_plan() -> None:
    """解析产物喂给 current_plan_entry 的任何时刻都不抛异常（主循环安全）。"""
    for raw in (
        VALID_JSON,
        '[{"time": "07:00:00", "location": "广场", "action": "x"}]',
        '[{"time": "7点", "location": "火星", "action": "x"}]',
        "完全不是 JSON",
    ):
        plan = parse_plan(raw)
        for minutes in (0, 8 * 60, 23 * 60 + 59):
            current_plan_entry(plan, minutes)  # 不抛即通过


@pytest.mark.asyncio
async def test_generate_daily_plan_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mock LLM 返回固定 JSON：计划入库 + 记忆写入。"""
    db = _fresh_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO residents (id, name, occupation, personality, backstory, prompt_prefix, current_location) "
            "VALUES ('baker_lin', '林师傅', '面包师', '热情', '背景', '前缀', '456,344')"
        )

    async def fake_chat(
        prompt: str, tier: str = "light", timeout: float | None = None
    ) -> str:
        assert prompt.startswith(RESIDENT.prompt_prefix)
        assert tier == "light"  # 计划用轻量层
        return VALID_JSON

    monkeypatch.setattr(planner, "chat", fake_chat)
    plan = await generate_daily_plan(
        RESIDENT, day=1, game_time="day1-07:00", db_path=db
    )
    assert plan[0].location == "面包店"

    # daily_plan 已持久化
    residents = load_residents(db)
    assert residents[0].x == 456
    # 记忆流里留下了"制定计划"这条事件
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT content, type FROM memories WHERE resident_id = 'baker_lin'"
        ).fetchone()
    assert row[1] == "event"
    assert "计划" in row[0]


@pytest.mark.asyncio
async def test_generate_daily_plan_llm_failure_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM 超时（chat 返回空串）→ 静默降级为默认日程，不抛异常。"""
    db = _fresh_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO residents (id, name, occupation, personality, backstory, prompt_prefix, current_location) "
            "VALUES ('baker_lin', '林师傅', '面包师', '热情', '背景', '前缀', '456,344')"
        )

    async def fake_chat(
        prompt: str, tier: str = "light", timeout: float | None = None
    ) -> str:
        return ""

    monkeypatch.setattr(planner, "chat", fake_chat)
    plan = await generate_daily_plan(
        RESIDENT, day=1, game_time="day1-07:00", db_path=db
    )
    assert plan[0].location == "广场"


# ---------- Bug F：降级计划不得写记忆（known issue #47 在 planner 的复现） ----------


def _count_memories(db: Path) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]


def _insert_baker(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO residents (id, name, occupation, personality, backstory, prompt_prefix, current_location) "
            "VALUES ('baker_lin', '林师傅', '面包师', '热情', '背景', '前缀', '456,344')"
        )


@pytest.mark.asyncio
async def test_llm_timeout_writes_no_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM 超时降级：默认日程照常执行/持久化，但不写记忆——
    否则被下轮检索命中，居民会天天"广场随便逛逛"自我强化。"""
    db = _fresh_db(tmp_path)
    _insert_baker(db)

    async def no_reply(
        prompt: str, tier: str = "light", timeout: float | None = None
    ) -> str:
        return ""

    monkeypatch.setattr(planner, "chat", no_reply)
    plan = await generate_daily_plan(
        RESIDENT, day=1, game_time="day1-07:00", db_path=db
    )
    assert plan[0].location == "广场"  # 降级日程生效
    assert _count_memories(db) == 0  # 关键：没有记忆污染
    residents = load_residents(db)
    assert residents[0].daily_plan is not None  # 持久化照常


@pytest.mark.asyncio
async def test_unparseable_output_writes_no_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM 返回了非 JSON 垃圾：同样降级不写记忆。"""
    db = _fresh_db(tmp_path)
    _insert_baker(db)

    async def garbage(
        prompt: str, tier: str = "light", timeout: float | None = None
    ) -> str:
        return "今天不想制定计划，随便过吧"

    monkeypatch.setattr(planner, "chat", garbage)
    plan = await generate_daily_plan(
        RESIDENT, day=1, game_time="day1-07:00", db_path=db
    )
    assert plan[0].location == "广场"
    assert _count_memories(db) == 0
