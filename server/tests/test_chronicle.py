"""world/chronicle.py 编年史的单元测试 + engine 落盘接线测试。

为什么值得测：编年史是"居民全部交互"的唯一全文档案，漏写任何一类
（邀请/加入/散场全文/玩家对话）都会让历史悄悄缺一块——每类至少一个用例锁死。
所有集成用例都 monkeypatch 掉记忆写入与 LLM，绝不污染真实 aitown.db。
"""

import asyncio
import json
from pathlib import Path

import memory.store
import world.engine as engine_module
from agents.resident import Resident
from world import chronicle
from world.engine import Conversation, ResidentRuntime, WorldEngine


def _make_resident(rid: str, name: str, x: int = 560, y: int = 944) -> Resident:
    return Resident(
        id=rid, name=name, occupation="测试职业", prompt_prefix="p", x=x, y=y
    )


# ---------- 模块单测 ----------


def test_record_writes_valid_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "saves" / "chronicle.jsonl"
    chronicle.record(
        "conversation", "day1-08:00", {"participants": ["林师傅"]}, path=path
    )
    chronicle.record("invite", "day1-08:05", {"accepted": False}, path=path)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "conversation"
    assert first["game_time"] == "day1-08:00"
    assert first["real_time"]  # 现实时间戳存在（双时间戳）
    assert json.loads(lines[1])["accepted"] is False


def test_record_keeps_chinese_readable(tmp_path: Path) -> None:
    """ensure_ascii=False：中文原样落盘，人能直接打开读。"""
    path = tmp_path / "c.jsonl"
    chronicle.record("join", "day1-08:00", {"joiner": "老宋"}, path=path)
    assert "老宋" in path.read_text(encoding="utf-8")  # 而非 \uXXXX 转义


def test_record_never_raises(tmp_path: Path) -> None:
    """编年史是旁路档案：落盘失败（路径被目录占住）只告警，绝不抛错打断游戏。"""
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    chronicle.record("invite", "day1-08:00", {"accepted": True}, path=blocker)
    assert blocker.is_dir()  # 没抛错，也没破坏现场


# ---------- engine 接线（monkeypatch 记忆写入 + 编年史路径，零 DB / 零 LLM） ----------


def _setup_engine(tmp_path: Path, monkeypatch) -> WorldEngine:
    chronicle_path = tmp_path / "chronicle.jsonl"
    monkeypatch.setattr(chronicle, "CHRONICLE_PATH", chronicle_path)
    # 记忆写入换成空操作：测试不污染真实 aitown.db（MEMORY 里的老教训）
    monkeypatch.setattr(memory.store, "add_memory", lambda *args, **kwargs: 0)
    return WorldEngine()


def test_end_conversation_writes_full_transcript(tmp_path: Path, monkeypatch) -> None:
    engine = _setup_engine(tmp_path, monkeypatch)
    lin, dou = (
        _make_resident("baker_lin", "林师傅"),
        _make_resident("xiao_dou", "小豆子"),
    )
    rt_lin, rt_dou = ResidentRuntime(lin), ResidentRuntime(dou)
    engine.residents["baker_lin"] = rt_lin
    engine.residents["xiao_dou"] = rt_dou
    conv = Conversation(["baker_lin", "xiao_dou"], "面包店", 0)
    conv.transcript = [("林师傅", "面团要这样揉"), ("小豆子", "原来如此！")]
    rt_lin.conversation_id = conv.id
    rt_dou.conversation_id = conv.id
    engine.conversations[conv.id] = conv

    asyncio.run(engine._end_conversation(conv, reason="聊完了"))

    data = json.loads(
        (tmp_path / "chronicle.jsonl").read_text(encoding="utf-8").strip()
    )
    assert data["kind"] == "conversation"
    assert data["participants"] == ["林师傅", "小豆子"]
    assert data["location"] == "面包店"
    assert data["reason"] == "聊完了"
    assert data["transcript"] == [
        {"speaker": "林师傅", "text": "面团要这样揉"},
        {"speaker": "小豆子", "text": "原来如此！"},
    ]
    # 散场流程本身未被编年史影响
    assert conv.id not in engine.conversations
    assert rt_lin.conversation_id is None and rt_dou.conversation_id is None


def test_invite_rejection_is_recorded(tmp_path: Path, monkeypatch) -> None:
    """拒绝的邀约不产生对话、不写记忆——编年史是它唯一的留痕处。"""

    async def fake_decline(info, inviter_name, game_time, location):
        return False

    monkeypatch.setattr(engine_module, "decide_accept", fake_decline)
    engine = _setup_engine(tmp_path, monkeypatch)
    rt_lin = ResidentRuntime(_make_resident("baker_lin", "林师傅"))
    rt_dou = ResidentRuntime(_make_resident("xiao_dou", "小豆子"))
    rt_lin.current_location = "面包店"
    engine.residents["baker_lin"] = rt_lin
    engine.residents["xiao_dou"] = rt_dou

    asyncio.run(engine._invite(rt_lin, rt_dou))

    data = json.loads(
        (tmp_path / "chronicle.jsonl").read_text(encoding="utf-8").strip()
    )
    assert data["kind"] == "invite"
    assert data["accepted"] is False
    assert data["inviter"] == "林师傅" and data["invitee"] == "小豆子"
    assert engine.conversations == {}  # 确实没成局


def test_player_chat_records_full_text(tmp_path: Path, monkeypatch) -> None:
    """玩家一对一对话：玩家原话 + 居民回复逐字全文落盘（播报里是截断的）。"""

    async def fake_reply(info, text, game_time, location):
        return "好嘞，刚出炉的法棍！"

    monkeypatch.setattr(engine_module, "player_say", fake_reply)
    engine = _setup_engine(tmp_path, monkeypatch)
    lin = _make_resident("baker_lin", "林师傅")
    engine.residents["baker_lin"] = ResidentRuntime(lin)
    engine.player = {"x": lin.x, "y": lin.y}  # 同格 → 距离校验必过

    lines, error = asyncio.run(engine.player_chat("baker_lin", "来条法棍"))

    assert error is None
    assert lines == [("林师傅", "好嘞，刚出炉的法棍！")]
    data = json.loads(
        (tmp_path / "chronicle.jsonl").read_text(encoding="utf-8").strip()
    )
    assert data["kind"] == "player_chat"
    assert data["mode"] == "solo"
    assert data["player_text"] == "来条法棍"
    assert data["replies"] == [{"speaker": "林师傅", "text": "好嘞，刚出炉的法棍！"}]
