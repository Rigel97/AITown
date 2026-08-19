"""planner 的单元测试（mock chat，不真实调 LLM）。

锁定两条关键约束：
1. prompt 必须以人设前缀开头（Prompt 缓存命中的前提，前缀逐字固定）；
2. 解析对 LLM 的各种"不听话输出"都能容错降级，绝不向上抛异常。
"""

import sqlite3
from pathlib import Path

import pytest

from agents import planner
from agents.planner import PlanEntry, build_plan_prompt, generate_daily_plan, parse_plan
from agents.resident import Resident, load_residents

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


def test_prompt_starts_with_persona_prefix() -> None:
    """人设前缀必须是 prompt 的逐字开头——缓存命中的前提。"""
    prompt = build_plan_prompt(RESIDENT, memories=[], day=1)
    assert prompt.startswith(RESIDENT.prompt_prefix)
    assert "JSON" in prompt  # 指令段在动态内容之后


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
