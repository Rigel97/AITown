"""人设种子与居民加载的测试（用临时库，不碰真实存档）。

为什么测 prefix 内容：prompt_prefix 是 LLM 行为的地基，
"人设完整 + 含关系网"直接决定涌现质量，值得锁定。
"""

import sqlite3
from pathlib import Path

from agents.resident import load_residents
from db.seed import seed

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA.read_text())
    return db


def test_seed_writes_all_residents(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    assert seed(db) == 7
    residents = load_residents(db)
    assert {r.id for r in residents} == {
        "baker_lin",
        "librarian_su",
        "florist_mo",
        "lao_zhou",
        "hong_jie",
        "xiao_dou",
        "lao_song",
    }
    for r in residents:
        assert r.prompt_prefix.startswith("你是 AI 小镇的居民")
        assert len(r.prompt_prefix) > 100  # 人设足够具体
        assert r.x > 0 and r.y > 0  # 有合法初始位置


def test_seed_is_idempotent(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    seed(db)
    seed(db)  # 再跑一遍不报错、不产生重复
    assert len(load_residents(db)) == 7


def test_prompt_prefix_contains_relations(tmp_path: Path) -> None:
    """关系网是涌现的种子——每个居民的 prefix 都必须提到至少一个其他居民。"""
    db = _fresh_db(tmp_path)
    seed(db)
    residents = load_residents(db)
    names = {r.name for r in residents}
    for r in residents:
        others = names - {r.name}
        assert any(n in r.prompt_prefix for n in others), f"{r.id} 的 prefix 缺少关系网"
