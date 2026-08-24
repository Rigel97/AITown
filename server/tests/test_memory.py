"""记忆流 store/retrieve 的测试（用临时库，不碰真实存档）。

对应 PRD 验收思路：造一批记忆，验证 Top-K 检索符合三要素预期
（近因/重要性/关键词各测一组，再测综合排序）。
"""

import sqlite3
from pathlib import Path

import pytest

from memory.retrieve import retrieve
from memory.store import (
    IMPORTANT_MEMORY_THRESHOLD,
    add_memory,
    extract_keywords,
    get_important_memories,
    get_recent_memories,
)
from world.clock import parse_game_time

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
NOW = parse_game_time("day5-12:00")


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA.read_text())
    return db


# ---------- store ----------


def test_extract_keywords_hits_vocabulary() -> None:
    assert extract_keywords("在红姐的餐馆吃了饭") == ["红姐", "餐馆"]
    assert extract_keywords("今天什么也没发生") == []


def test_add_and_get_recent_roundtrip(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    add_memory("baker_lin", "day1-08:00", "observation", "清晨开门烤面包", 3, db)
    add_memory("baker_lin", "day1-09:00", "dialogue", "玩家在面包店夸面包好吃", 7, db)

    memories = get_recent_memories("baker_lin", db_path=db)
    assert len(memories) == 2
    assert memories[0].game_time == "day1-09:00"  # 新的在前
    # keywords 写入时自动从词表抽取
    assert set(memories[0].keywords) == {"玩家", "面包店", "面包"}


def test_importance_out_of_range_raises(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    with pytest.raises(ValueError, match="importance"):
        add_memory("baker_lin", "day1-08:00", "event", "x", 11, db)


# ---------- retrieve 三要素 ----------


def test_recency_orders_newer_first(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    add_memory("baker_lin", "day1-08:00", "observation", "开门营业", 5, db)
    add_memory("baker_lin", "day5-11:00", "observation", "开门营业", 5, db)

    top = retrieve("baker_lin", query="", now_minutes=NOW, k=1, db_path=db)
    assert top[0].game_time == "day5-11:00"


def test_importance_orders_higher_first(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    add_memory("baker_lin", "day5-11:00", "observation", "普通的一天", 2, db)
    add_memory("baker_lin", "day5-11:00", "event", "玩家说要把小镇推荐给朋友", 9, db)

    top = retrieve("baker_lin", query="", now_minutes=NOW, k=1, db_path=db)
    assert top[0].importance == 9


def test_keyword_match_wins(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    add_memory("baker_lin", "day5-11:00", "observation", "在广场看到玩家", 5, db)
    add_memory("baker_lin", "day5-11:30", "observation", "老周来买面包", 5, db)

    top = retrieve(
        "baker_lin", query="玩家在广场做了什么", now_minutes=NOW, k=1, db_path=db
    )
    assert "广场" in top[0].content


def test_important_old_memory_still_surfaces(tmp_path: Path) -> None:
    """重要性顶格的老记忆，应该赢过新鲜但琐碎的记忆（长期陪伴感的来源）。"""
    db = _fresh_db(tmp_path)
    add_memory(
        "baker_lin", "day1-08:00", "event", "玩家第一次来面包店，说以后会常来", 9, db
    )
    add_memory("baker_lin", "day5-11:59", "observation", "擦了擦柜台", 1, db)

    top = retrieve("baker_lin", query="玩家", now_minutes=NOW, k=1, db_path=db)
    assert "第一次" in top[0].content


def test_retrieve_returns_at_most_k(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    for i in range(10):
        add_memory("baker_lin", f"day5-08:{i:02d}", "observation", f"琐事{i}", 3, db)

    assert len(retrieve("baker_lin", query="", now_minutes=NOW, k=3, db_path=db)) == 3


# ---------- 检索双通道（2026-08-21 深检：近因窗口曾把老玩家互动永久埋掉） ----------


def test_get_important_memories_filters_by_threshold(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    add_memory(
        "baker_lin", "day1-08:00", "dialogue", "玩家对我说：「常来买面包」", 6, db
    )
    add_memory("baker_lin", "day1-09:00", "reflection", "今天心情不错", 8, db)
    add_memory("baker_lin", "day1-10:00", "observation", "擦了擦柜台", 5, db)
    add_memory("baker_lin", "day1-11:00", "event", "制定了今天的计划", 3, db)

    important = get_important_memories("baker_lin", db_path=db)
    assert {m.importance for m in important} == {6, 8}
    assert IMPORTANT_MEMORY_THRESHOLD == 6  # 玩家互动（6）是最低门槛


def test_retrieve_finds_old_player_memory_beyond_recent_window(tmp_path: Path) -> None:
    """Bug E：早期与玩家的互动（importance 6）被大量琐事刷出近因窗口后，
    仍必须能被检索到——"居民记得玩家"是核心体验，不能只活在最近 100 条里。"""
    db = _fresh_db(tmp_path)
    # 第 1 天：玩家说了句重要的话（带可检索关键词）
    add_memory(
        "baker_lin",
        "day1-08:00",
        "dialogue",
        "玩家对我说：「以后每天都来买面包」",
        6,
        db,
    )
    # 第 5 天：150 条琐碎日常把近因窗口（默认 100 条）全部占满
    for i in range(150):
        add_memory(
            "baker_lin", f"day5-08:{i % 60:02d}", "observation", f"琐事{i}", 1, db
        )

    top = retrieve("baker_lin", query="面包", now_minutes=NOW, k=3, db_path=db)
    assert any("玩家" in m.content for m in top), "老玩家互动被近因窗口永久埋掉了"
