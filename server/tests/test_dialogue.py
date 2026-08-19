"""对话解析函数与 prompt 组装的测试（用临时库，不碰真实存档）。

为什么测解析纯函数：LLM 输出不可控，但解析逻辑可控——白名单过滤、
结束判定、托词轮换这些纯函数是防幻觉/防卡死的最后一道闸，必须锁定。

为什么测 world_context：它是防幻觉的关键——没有"镇上只有这些人"的约束，
LLM 会编造不存在的镇民（2026-08-17 用户实测：老宋要给不存在的人修椅子）。
世界观块必须逐字稳定（吃 Prompt 缓存），所以缓存与稳定性都要锁定。
"""

import sqlite3
from pathlib import Path

from agents.dialogue import (
    build_chat_prompt,
    parse_speaker_lines,
    parse_turn,
    parse_yesno,
)
from agents.resident import load_residents, world_context
from db.seed import seed

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA.read_text())
    seed(db)
    return db


# ---------- 世界观 ----------


def test_world_context_lists_all_residents_and_places(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    block = world_context(db)
    for name in ("林师傅", "苏晚", "阿茉", "老周", "红姐", "小豆子", "老宋"):
        assert name in block
    for place in ("面包店", "餐馆", "广场"):
        assert place in block
    assert "不要编造" in block


def test_world_context_is_byte_stable(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    assert world_context(db) == world_context(db)


def test_chat_prompt_contains_world_context(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    resident = load_residents(db)[0]
    prompt = build_chat_prompt(resident, [], "你好", "第1天 08:00", "面包店", db)
    assert "【小镇】" in prompt
    assert prompt.index("【小镇】") < prompt.index("【你记得的事】")
    assert "不要编造新的镇民" in prompt
    assert prompt.startswith(resident.prompt_prefix)


# ---------- 逐回合对话解析 ----------


def test_parse_speaker_lines_keeps_only_whitelisted() -> None:
    raw = """林师傅：红姐，昨晚那锅汤真香！
红姐：那是，也不看是谁炖的。
（两人笑了起来）
路人甲：我也想吃。
林师傅: 半角冒号也算。"""
    lines = parse_speaker_lines(raw, ["林师傅", "红姐"])
    assert lines == [
        ("林师傅", "红姐，昨晚那锅汤真香！"),
        ("红姐", "那是，也不看是谁炖的。"),
        ("林师傅", "半角冒号也算。"),
    ]


def test_parse_speaker_lines_caps_length() -> None:
    raw = "\n".join(f"林师傅：第{i}句" for i in range(10))
    assert len(parse_speaker_lines(raw, ["林师傅", "红姐"])) == 8


def test_parse_speaker_lines_empty_on_garbage() -> None:
    assert parse_speaker_lines("没有名字开头的行", ["林师傅", "红姐"]) == []
    assert parse_speaker_lines("", ["林师傅", "红姐"]) == []


def test_parse_turn_strips_speaker_prefix() -> None:
    text, want_end = parse_turn("林师傅：今天面包卖得不错。", "林师傅")
    assert text == "今天面包卖得不错。"
    assert want_end is False


def test_parse_turn_detects_end() -> None:
    text, want_end = parse_turn("结束", "林师傅")
    assert text == ""
    assert want_end is True
    # 带标点的也算
    text, want_end = parse_turn("结束。", "林师傅")
    assert want_end is True


def test_parse_turn_empty_returns_no_end() -> None:
    text, want_end = parse_turn("", "林师傅")
    assert text == ""
    assert want_end is False  # 空串算失败不算结束，交给引擎累计失败次数


def test_parse_yesno_accepts_affirmative() -> None:
    assert parse_yesno("会") is True
    assert parse_yesno("好") is True
    assert parse_yesno("不行") is False
    assert parse_yesno("") is False
    # 否定一律算不
    assert parse_yesno("不") is False
