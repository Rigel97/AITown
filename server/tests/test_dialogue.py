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
    FALLBACK_REPLIES,
    _fallback_for,
    anti_ai_guard,
    build_chat_prompt,
    parse_speaker_lines,
    parse_turn,
    parse_yesno,
)
from agents.resident import load_residents, world_context
from db.seed import seed
from memory.store import add_memory

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
    for place in ("青梧咖啡", "九号酒馆", "主街"):
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


def test_parse_yesno_natural_phrases() -> None:
    """B4：自然短语不该被误杀——"当然会/嗯，聊两句"是同意，只认单字时
    会被判拒绝，一场本该发生的对话就没了。否定词优先：带“不/别/没”
    哪怕句式像肯定也是拒绝。"""
    # 肯定短语
    assert parse_yesno("当然会") is True
    assert parse_yesno("嗯，聊两句") is True
    assert parse_yesno("可以") is True
    assert parse_yesno("好啊") is True
    assert parse_yesno("行") is True
    # 否定短语（否定优先，哪怕带肯定字）
    assert parse_yesno("不行") is False
    assert parse_yesno("别打扰我") is False
    assert parse_yesno("没空") is False
    assert parse_yesno("算了") is False
    # 舞台剧包装先剥再判（"*摆摆手*不了" → "不了"）
    assert parse_yesno("*摆摆手*不了") is False


def test_fallback_reply_is_deterministic() -> None:
    """Bug K：同人同话同托词——跨进程/重启也稳定（旧版内建 hash 受
    PYTHONHASHSEED 随机化影响，每次重启换托词）。"""
    assert _fallback_for("lao_zhou", "你好") == _fallback_for("lao_zhou", "你好")
    assert _fallback_for("lao_zhou", "你好") in FALLBACK_REPLIES
    # 不同输入能分布到不同托词（轮换性：避免全员同一句）
    picks = {_fallback_for(rid, "你好") for rid in ("a", "b", "c", "d", "e", "f")}
    assert len(picks) > 1


def test_fallback_replies_are_clean_and_age_neutral() -> None:
    """四轮 G1 守卫：托词池全员共用，必须①无括号动作（台词卫生规则对
    LLM 生效，降级文案自己违规是双标）②无年龄/性别限定词（"人上了
    年纪耳朵背"给学徒少年说直接崩人设）。"""
    age_markers = ("年纪", "耳朵背", "老伴", "老了", "孙子")
    for reply in FALLBACK_REPLIES:
        assert "（" not in reply and "）" not in reply, reply
        assert "*" not in reply, reply
        assert not any(w in reply for w in age_markers), reply


# ---------- 出戏防线（四轮 G2） ----------


def test_anti_ai_guard_triggers_on_probe_words() -> None:
    """玩家试探 AI 身份时动态指令段追加防线；普通文本零改动（缓存安全）。"""
    for probe in (
        "你是 AI 吗",
        "你是不是人工智能",
        "chatgpt 写的？",
        "你是个 NPC 吧",
        "大模型还是语言模型",
    ):
        guard = anti_ai_guard(probe)
        assert "小镇居民" in guard, probe
    # 普通文本（含碰巧含字母 ai 的英文单词如 "said"/"wait"）不应触发
    for normal in ("今天面包多少钱", "wait a minute", "said something"):
        assert anti_ai_guard(normal) == ""


def test_chat_prompt_appends_guard_only_when_probing(
    tmp_path: Path, monkeypatch
) -> None:
    """防线行只出现在敏感词对话的 prompt 里；普通对话 prompt 逐字不变
    （指令段动态区的普通路径必须稳定，否则缓存命中率受损）。"""
    db = _fresh_db(tmp_path)
    resident = load_residents(db)[0]
    base = build_chat_prompt(resident, [], "你好呀", "day1-08:00", "面包店", db)
    probed = build_chat_prompt(resident, [], "你是 AI 吗", "day1-08:00", "面包店", db)
    assert anti_ai_guard("你好呀") == ""
    assert base.endswith("永远不要承认自己是 AI。")  # 普通路径末尾逐字不变
    assert "别顺着接" in probed  # 敏感词路径追加防线行


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


def test_conversation_turn_injects_memories(tmp_path, monkeypatch) -> None:
    """F1（2026-08-22 三轮）：居民-居民对话不再失忆——检索词 = 同伴名 +
    最近话题，命中相关记忆（如上次聊天的散场摘要）要注入 prompt。"""
    db = _fresh_db(tmp_path)
    residents = load_residents(db)
    speaker = next(r for r in residents if r.name == "林师傅")
    # 上次和红姐聊过供货日（散场摘要类记忆，importance 5）
    add_memory(
        speaker.id,
        "day1-09:00",
        "dialogue",
        "我在广场和红姐聊天：红姐说「明天供货日来新面粉」",
        5,
        db,
    )
    captured: dict[str, str] = {}

    async def fake_chat(prompt: str, tier: str, timeout: float = 0) -> str:
        captured["prompt"] = prompt
        return "那敢情好，新面粉烤出来的皮才脆。"

    monkeypatch.setattr(dialogue, "chat", fake_chat)
    text, want_end = asyncio.run(
        dialogue.conversation_turn(
            speaker,
            ["红姐"],
            [("红姐", "明天供货日的青菜也新鲜")],
            "day2-08:00",
            "广场",
            db,
        )
    )
    prompt = captured["prompt"]
    # 检索命中并注入：同伴名+话题做检索词，上次聊过的事被想起
    assert "【你记得的事】" in prompt
    assert "明天供货日来新面粉" in prompt
    # 结构与玩家对话同款：prefix 打头（吃缓存），世界观在前、记忆其后
    assert prompt.startswith(speaker.prompt_prefix)
    assert prompt.index("【小镇】") < prompt.index("【你记得的事】")
    assert prompt.index("【你记得的事】") < prompt.index("【当前情境】")
    # 解析行为不受记忆注入影响
    assert text == "那敢情好，新面粉烤出来的皮才脆。"
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


def test_player_say_returns_empty_when_llm_fails(tmp_path, monkeypatch) -> None:
    """D4 配套：LLM 两次都超时 → 返回空串（降级托词由 engine 统一给出），
    玩家的话仍写记忆、假回复不写。"""
    db = _fresh_db(tmp_path)
    resident = load_residents(db)[0]

    async def no_reply(prompt: str, tier: str, timeout: float = 0) -> str:
        return ""

    monkeypatch.setattr(dialogue, "chat", no_reply)
    reply = asyncio.run(
        dialogue.player_say(resident, "在吗", "day1-08:00", "面包店", db)
    )
    assert reply == ""
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT content FROM memories WHERE resident_id = ? ORDER BY id",
            (resident.id,),
        ).fetchall()
    contents = [r[0] for r in rows]
    assert any("玩家对我说：「在吗」" in c for c in contents)
    assert not any("我回答玩家" in c for c in contents)  # 没有假回复入记忆


def test_player_join_reply_injects_memories(tmp_path, monkeypatch) -> None:
    """A2：群聊不能"集体失忆"——各参与者的相关记忆按人注入 prompt
    （单聊记得玩家的事，群聊里也得记得）。"""
    db = _fresh_db(tmp_path)
    baker = load_residents(db)[0]  # 林师傅
    others = [r for r in load_residents(db) if r.id != baker.id]
    # 给林师傅写一条与"面包"相关的玩家互动记忆（importance 6，关键词命中）
    add_memory(
        baker.id, "day1-09:00", "dialogue", "玩家对我说：「上次买的面包真好吃」", 6, db
    )

    captured: dict[str, str] = {}

    async def fake_chat(prompt: str, tier: str, timeout: float = 0) -> str:
        captured["prompt"] = prompt
        return "林师傅：好嘞，还给你留着呢。"

    monkeypatch.setattr(dialogue, "chat", fake_chat)
    lines = asyncio.run(
        dialogue.player_join_reply(
            [baker, others[0]], "面包还有吗", [], "day2-08:00", "面包店", db
        )
    )
    prompt = captured["prompt"]
    # 记忆段存在且包含林师傅的那条玩家互动；无记忆的参与者不产生记忆块
    assert "【你们各自记得的事】" in prompt
    assert "上次买的面包真好吃" in prompt
    assert prompt.index("【你们各自记得的事】") < prompt.index("【林师傅】")
    # 解析结果正常（记忆注入不影响输出格式约束）
    assert lines == [("林师傅", "好嘞，还给你留着呢。")]
