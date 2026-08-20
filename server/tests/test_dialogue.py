"""对话解析函数与 prompt 组装的测试（用临时库，不碰真实存档）。

为什么测解析纯函数：LLM 输出不可控，但解析逻辑可控——白名单过滤、
结束判定、托词轮换这些纯函数是防幻觉/防卡死的最后一道闸，必须锁定。

为什么测 world_context：它是防幻觉的关键——没有"镇上只有这些人"的约束，
LLM 会编造不存在的镇民（2026-08-17 用户实测：老宋要给不存在的人修椅子）。
世界观块必须逐字稳定（吃 Prompt 缓存），所以缓存与稳定性都要锁定。
"""

import asyncio
import sqlite3
from pathlib import Path

from agents import dialogue
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


# ---------- 台词卫生（2026-08-20 实测翻车样例回归） ----------


def test_parse_turn_strips_wrapping_quotes() -> None:
    """模型把台词包在引号里（实测：林师傅 "“刚出炉的……”"）→ 剥引号留话。"""
    text, _ = parse_turn("“刚出炉的，尝尝！”", "林师傅")
    assert text == "刚出炉的，尝尝！"


def test_parse_turn_asterisk_action_only_is_failure() -> None:
    """整行星号动作（实测：苏晚“*轻轻抬眼看向老周*”）→ 没台词，算失败。"""
    text, want_end = parse_turn("*压低声音，眼睛瞟了瞟门口*", "老周")
    assert text == ""
    assert want_end is False


def test_parse_turn_asterisk_action_with_speech_keeps_speech() -> None:
    text, _ = parse_turn("*凑近了些*说吧，没人。", "老周")
    assert text == "说吧，没人。"


def test_parse_turn_leading_action_paren_keeps_speech() -> None:
    """行首（动作）+台词 → 剥动作留台词。"""
    text, _ = parse_turn("（点了点头）嗯，还行。", "老宋")
    assert text == "嗯，还行。"


def test_parse_turn_pure_stage_direction_is_failure() -> None:
    """整行括号动作（实测：老宋开场只写了端茶杯旁白）→ 算失败。"""
    text, want_end = parse_turn("（端着一盘刚出炉的小餐包从后厨走出来）", "林师傅")
    assert text == ""
    assert want_end is False


def test_parse_turn_falls_through_to_speech_line() -> None:
    """首行是动作、次行才是话 → 取到话，不误判失败。"""
    raw = "（端着一盘面包走出来）\n来了？想买点啥？"
    text, _ = parse_turn(raw, "林师傅")
    assert text == "来了？想买点啥？"


def test_parse_turn_skips_other_speakers_lines() -> None:
    """模型替对方说话（实测：老周回合输出“老宋：嗯，还行。”）→ 跳过对方行取自己的。"""
    raw = """红姐：老周你来了？
林师傅：哟，这就把话头抢了！"""
    text, _ = parse_turn(raw, "林师傅", other_names=["红姐"])
    assert text == "哟，这就把话头抢了！"


def test_parse_turn_all_other_lines_is_failure() -> None:
    """整段都在替别人说话 → 空台词（引擎累计失败散场），不再张冠李戴。"""
    text, want_end = parse_turn("老宋：嗯，还行。", "老周", other_names=["老宋"])
    assert text == ""
    assert want_end is False


def test_conversation_turn_rejects_repeated_line(tmp_path, monkeypatch) -> None:
    """复读守卫：和自己在场说过的原话相同（含标点差异）→ 视为没接上。"""
    db = _fresh_db(tmp_path)
    resident = load_residents(db)[0]  # 林师傅

    async def fake_chat(prompt: str, tier: str, timeout: float = 0) -> str:
        return "刚出炉的，尝尝！"

    monkeypatch.setattr(dialogue, "chat", fake_chat)
    transcript = [("林师傅", "刚出炉的，尝尝"), ("红姐", "好吃！")]
    text, want_end = asyncio.run(
        dialogue.conversation_turn(
            resident, ["红姐"], transcript, "day1-08:00", "面包店", db
        )
    )
    assert text == ""
    assert want_end is False


def test_player_say_strips_theatrics_from_reply(tmp_path, monkeypatch) -> None:
    """玩家对话回复同样剥舞台剧包装（实测出现过（拍拍面粉）开头的回复）。"""
    db = _fresh_db(tmp_path)
    resident = load_residents(db)[0]

    async def fake_chat(prompt: str, tier: str, timeout: float = 0) -> str:
        return "（拍拍手上的面粉）来了！要啥面包？"

    monkeypatch.setattr(dialogue, "chat", fake_chat)
    reply = asyncio.run(
        dialogue.player_say(resident, "买两个面包", "day1-08:00", "面包店", db)
    )
    assert reply == "来了！要啥面包？"
