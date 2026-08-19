"""记忆流 store/retrieve 的测试（用临时库，不碰真实存档）。

对应 PRD 验收思路：造一批记忆，验证 Top-K 检索符合三要素预期
（近因/重要性/关键词各测一组，再测综合排序）。
"""

import sqlite3
from pathlib import Path

import pytest

from memory.retrieve import retrieve
from memory.store import add_memory, extract_keywords, get_recent_memories
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
