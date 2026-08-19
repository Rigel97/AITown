"""反思层的测试（用临时库，不碰真实存档）。

为什么测解析：反思输出要写回记忆流并影响后续行为，解析必须稳——
剥编号、去空行、限条数，都是防"反思写成流水账稀释记忆"的闸。
"""

import sqlite3
from pathlib import Path

from agents.reflection import build_reflection_prompt, parse_reflections
from agents.resident import load_residents
from db.seed import seed
from memory.store import add_memory, get_memories_of_day

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
