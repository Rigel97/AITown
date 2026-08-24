"""反思层的测试（用临时库，不碰真实存档）。

为什么测解析：反思输出要写回记忆流并影响后续行为，解析必须稳——
剥编号、去空行、限条数，都是防"反思写成流水账稀释记忆"的闸。
"""

import asyncio
import sqlite3
from pathlib import Path

from agents import reflection
from agents.reflection import (
    REFLECTION_MEMORY_LIMIT,
    _trim_for_reflection,
    build_reflection_prompt,
    parse_reflections,
    reflect,
)
from agents.resident import load_residents
from db.seed import seed
from memory.store import (
    IMPORTANT_MEMORY_THRESHOLD,
    Memory,
    add_memory,
    get_memories_of_day,
)

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA.read_text())
    seed(db)
    return db


def test_parse_reflections_strips_bullets_and_numbers() -> None:
    raw = """- 今天和阿茉聊了花的事，她真懂花
2. 红姐的牛腩越来越香了

"""
    out = parse_reflections(raw)
    assert out == [
        "今天和阿茉聊了花的事，她真懂花",
        "红姐的牛腩越来越香了",
    ]


def test_parse_reflections_caps_two() -> None:
    raw = "\n".join(f"感悟{i}" for i in range(5))
    assert parse_reflections(raw) == ["感悟0", "感悟1"]


def test_parse_reflections_empty() -> None:
    assert parse_reflections("") == []
    assert parse_reflections("  \n  ") == []


def test_reflection_prompt_contains_today_memories(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    resident = load_residents(db)[0]
    add_memory(resident.id, "day1-10:00", "dialogue", "玩家对我说：「你好」", 6, db)
    prompt = build_reflection_prompt(
        resident, get_memories_of_day(resident.id, 1, db), "day1-23:00", db
    )
    assert "玩家对我说" in prompt
    assert "【今天的经历】" in prompt
    assert prompt.startswith(resident.prompt_prefix)


def test_get_memories_of_day_filters_by_day(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    resident = load_residents(db)[0]
    add_memory(resident.id, "day1-10:00", "event", "第一天的事", 3, db)
    add_memory(resident.id, "day2-10:00", "event", "第二天的事", 3, db)
    day1 = get_memories_of_day(resident.id, 1, db)
    day2 = get_memories_of_day(resident.id, 2, db)
    assert len(day1) == 1 and day1[0].content == "第一天的事"
    assert len(day2) == 1 and day2[0].content == "第二天的事"


# ---------- B1：反思注入截断（2026-08-22 优化轮） ----------


def _mem(mid: int, importance: int, content: str = "") -> Memory:
    return Memory(
        id=mid,
        resident_id="t",
        game_time="day1-08:00",
        type="event",
        content=content or f"记忆{mid}",
        importance=importance,
        keywords=[],
    )


def test_trim_for_reflection_caps_and_keeps_important() -> None:
    """B1：全量注入会让 M3 prompt 无限膨胀——截断为"近因 tail ∪ 高重要度"。

    构造：头部 5 条 importance 6（玩家互动，落在近因窗口外）+ 50 条
    importance 3 日常项。期望：尾部 40 条 + 头部 5 条重要记忆，按 id 升序。
    """
    memories = (
        [_mem(i, IMPORTANT_MEMORY_THRESHOLD, f"玩家互动{i}") for i in range(1, 6)]
        + [_mem(i, 3) for i in range(6, 56)]  # 50 条日常
    )
    trimmed = _trim_for_reflection(memories)

    assert len(trimmed) == REFLECTION_MEMORY_LIMIT + 5
    ids = [m.id for m in trimmed]
    assert ids == sorted(ids)  # 时间序保留
    # 高重要度记忆全部穿透近因窗口保留（丢掉玩家的白天互动等于白反思）
    assert {m.id for m in trimmed if m.importance >= IMPORTANT_MEMORY_THRESHOLD} == {
        1,
        2,
        3,
        4,
        5,
    }
    # 近因 tail：保留的是最后 40 条（id 16–55）
    assert 15 not in ids and 16 in ids and 55 in ids


def test_trim_for_reflection_short_list_untouched() -> None:
    """B1：记忆不多时全部保留（截断只在大日子生效）。"""
    memories = [_mem(1, 3), _mem(2, 6, "玩家互动"), _mem(3, 3)]
    assert _trim_for_reflection(memories) == memories


# ---------- G3：反思重试（四轮，一晚只有一次机会） ----------


def test_reflect_retries_once_after_failure(tmp_path: Path, monkeypatch) -> None:
    """G3：首次超时（空串）重试一次；重试成功照常写回记忆。
    反思一晚只有一次机会，reflected_day 无论成败都会推进——不重试的
    话一次 60s 超时就永久丢掉这一天的高层认知。"""
    db = _fresh_db(tmp_path)
    resident = load_residents(db)[0]
    add_memory(resident.id, "day1-10:00", "event", "白天的事", 3, db)
    calls: list[str] = []

    async def flaky_chat(prompt: str, tier: str, timeout: float = 0) -> str:
        calls.append(prompt)
        return "" if len(calls) == 1 else "面包是有良心的，今天又验证了一遍"

    monkeypatch.setattr(reflection, "chat", flaky_chat)
    written = asyncio.run(reflect(resident, "day1-23:00", 1, db))
    assert len(calls) == 2  # 重试发生
    assert written == 1
    day1 = get_memories_of_day(resident.id, 1, db)
    assert any(m.type == "reflection" for m in day1)


def test_reflect_gives_up_after_two_failures(tmp_path: Path, monkeypatch) -> None:
    """G3：两次都失败 → 返回 0、不写记忆、不无限重试（成本防线）。"""
    db = _fresh_db(tmp_path)
    resident = load_residents(db)[0]
    calls: list[str] = []

    async def dead_chat(prompt: str, tier: str, timeout: float = 0) -> str:
        calls.append(prompt)
        return ""

    monkeypatch.setattr(reflection, "chat", dead_chat)
    assert asyncio.run(reflect(resident, "day1-23:00", 1, db)) == 0
    assert len(calls) == 2  # 恰好重试一次，不无限烧
