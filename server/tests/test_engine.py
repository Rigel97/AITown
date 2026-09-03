"""world/engine.py 的单元测试。

时钟部分：流速公式（现实 1 秒 = 游戏 1 分钟）是 W2 居民作息和
LLM 调用频率的基础变量，必须锁死行为，防止后续改动破坏节奏。

引擎部分（2026-08-21 深检回归）：对话状态机的 await 竞态、反思防重入、
主循环异常隔离——这四个 bug 都是"LLM 调用期间世界状态已变/单点异常炸
全世界"类问题，测试用假 LLM（monkeypatch）确定性复现。
"""

import asyncio
import contextlib
import sqlite3
import time

import pytest

from agents.planner import PlanEntry
from agents.resident import Resident
from world import engine as we
from world.clock import WorldClock, format_game_time, parse_game_time
from world.engine import Conversation, ResidentRuntime, WorldEngine
from world.locations import LOCATION_SPOTS
from world.mapdata import to_pixel_center, to_tile


def test_clock_starts_at_day1_0800() -> None:
    assert WorldClock().label() == "day1-08:00"


def test_clock_tick_advances_minutes() -> None:
    clock = WorldClock()
    clock.tick(30)  # 现实 30 秒 = 游戏 30 分钟
    assert clock.label() == "day1-08:30"


def test_clock_day_rollover() -> None:
    clock = WorldClock(day=1, minutes=23 * 60 + 59)
    clock.tick(2)  # 跨过午夜
    assert clock.day == 2
    assert clock.label() == "day2-00:01"


def test_game_time_parse_format_roundtrip() -> None:
    """解析/格式化互逆（记忆检索的近因计算依赖 parse_game_time）。"""
    assert parse_game_time("day1-00:00") == 0
    assert parse_game_time("day2-00:00") == 24 * 60
    for label in ("day1-08:00", "day3-23:59", "day10-00:01"):
        assert format_game_time(parse_game_time(label)) == label


# ---------- 引擎：对话状态机竞态与防重入（2026-08-21 深检回归） ----------


def _resident(rid: str, name: str) -> Resident:
    return Resident(
        id=rid, name=name, occupation="测试", prompt_prefix="前缀", x=16, y=16
    )


@pytest.mark.asyncio
async def test_join_after_conversation_ended_does_not_stick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug C：decide_join 等待期间对话散场 → 加入者不得永久卡在"聊天中"。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    rb = ResidentRuntime(_resident("b", "乙"))
    eng.residents.update(a=ra, b=rb)
    conv = Conversation(["a"], "广场", 0)
    eng.conversations[conv.id] = conv
    ra.conversation_id = conv.id
    # 隔离副作用：不写真实 DB / 编年史
    monkeypatch.setattr(we, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(we.WorldEngine, "_write_memory", lambda *args, **kwargs: None)

    async def slow_decide_join(*args: object, **kwargs: object) -> bool:
        await asyncio.sleep(0.05)  # 模拟 LLM 决策耗时
        return True

    monkeypatch.setattr(we, "decide_join", slow_decide_join)

    task = asyncio.create_task(eng._maybe_join(rb, ra, conv))
    await asyncio.sleep(0.01)  # decide_join 进行中
    await eng._end_conversation(conv, reason="聊完了")  # 等待期间对话散场
    await task

    assert rb.conversation_id is None  # 关键：没有悬空的对话 id
    eng._step_resident(rb)
    assert rb.current_action != "聊天中"  # 不再被判"聊天中"，可正常行动


@pytest.mark.asyncio
async def test_invite_aborts_if_party_became_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug D：decide_accept 等待期间任一方被拉进别的对话 → 不得把人"抢走"。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    rb = ResidentRuntime(_resident("b", "乙"))
    eng.residents.update(a=ra, b=rb)
    monkeypatch.setattr(we, "record", lambda *args, **kwargs: None)

    async def slow_decide_accept(*args: object, **kwargs: object) -> bool:
        await asyncio.sleep(0.05)
        return True

    monkeypatch.setattr(we, "decide_accept", slow_decide_accept)

    task = asyncio.create_task(eng._invite(ra, rb))
    await asyncio.sleep(0.01)
    other = Conversation(["b"], "餐馆", 0)
    eng.conversations[other.id] = other
    rb.conversation_id = other.id  # 乙在等待期间加入了另一场对话
    await task

    assert rb.conversation_id == other.id  # 没被新对话覆盖
    assert ra.conversation_id is None
    assert not any(
        set(c.participant_ids) == {"a", "b"} for c in eng.conversations.values()
    )  # 也没有创建双开的新对话


@pytest.mark.asyncio
async def test_reflections_guard_prevents_task_storm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug B：反思防重入——reflected_day 更新前不得重复建任务。

    旧版每 1/3 秒重建一套全套 M3 反思任务，成本放大 15–30 倍。
    """
    eng = WorldEngine()
    rt = ResidentRuntime(_resident("t1", "测试"))
    eng.residents[rt.info.id] = rt
    eng.clock.day = 1
    eng.clock.minutes = 23 * 60
    calls = 0

    async def slow_reflect(*args: object, **kwargs: object) -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.2)  # 模拟 M3 延迟
        return 0

    monkeypatch.setattr(we, "reflect", slow_reflect)

    for _ in range(5):  # 主循环在反思进行中连拍 5 次
        eng._maybe_start_reflections(eng.clock.day)
    await asyncio.sleep(0.05)  # 让第一个任务真正跑起来
    assert calls == 1  # 守卫生效：没有任务风暴
    await asyncio.sleep(0.3)  # 等反思完成（reflected_day 已更新）
    assert calls == 1
    eng._maybe_start_reflections(eng.clock.day)
    assert calls == 1  # 已反思过，不再触发


@pytest.mark.asyncio
async def test_ensure_plans_isolates_per_resident_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug M：计划 gather 容错——单居民异常不拖垮整轮，失败者今天跳过。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    rb = ResidentRuntime(_resident("b", "乙"))
    eng.residents.update(a=ra, b=rb)
    eng.clock.day = 2

    async def gen_a(*args: object, **kwargs: object) -> list[PlanEntry]:
        raise sqlite3.OperationalError("disk I/O error")

    async def gen_b(*args: object, **kwargs: object) -> list[PlanEntry]:
        return [PlanEntry("08:00", "广场", "闲逛")]

    def route(info: Resident, *args: object, **kwargs: object) -> object:
        return gen_a() if info.id == "a" else gen_b()

    monkeypatch.setattr(we, "generate_daily_plan", route)
    await eng._ensure_plans()

    assert rb.plan[0].location == "广场"  # 正常居民拿到了计划
    assert rb.planned_day == 2
    assert ra.planned_day == 2  # 失败者标记今天已处理（不重试烧钱，明天再来）
    assert ra.plan == []  # 旧计划保持原状


def test_step_all_residents_isolates_single_resident_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug L（居民级）：单居民执行异常只跳过本人，不冻结其他人。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    rb = ResidentRuntime(_resident("b", "乙"))
    eng.residents.update(a=ra, b=rb)

    ran: list[str] = []

    def flaky(rt: ResidentRuntime) -> None:
        ran.append(rt.info.id)
        if rt.info.id == "a":
            raise RuntimeError("脏计划")

    monkeypatch.setattr(eng, "_step_resident", flaky)
    eng._step_all_residents()  # 不抛
    assert ran == ["a", "b"]  # a 炸了，b 照常执行


@pytest.mark.asyncio
async def test_run_loop_survives_tick_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug L（主循环级）：单拍抛异常，循环继续活着（旧版世界静默停摆）。"""
    eng = WorldEngine()
    rt = ResidentRuntime(_resident("t1", "测试"))
    rt.planned_day = 1  # 跳过计划生成（避免真实 LLM 调用）
    eng.residents[rt.info.id] = rt
    eng.clock.day = 1

    ticks = {"n": 0}

    def flaky_step() -> None:
        ticks["n"] += 1
        if ticks["n"] <= 2:
            raise RuntimeError("脏计划炸了这一拍")

    monkeypatch.setattr(eng, "_step_all_residents", flaky_step)

    task = asyncio.create_task(eng.run())
    await asyncio.sleep(1.6)  # 约 4 拍（给时序边界留余量）
    with contextlib.suppress(asyncio.CancelledError):
        task.cancel()
        await task

    assert ticks["n"] >= 3  # 前两拍都炸了，循环仍在继续


# ---------- 群聊记忆与事件缓冲（2026-08-21 深检） ----------


@pytest.mark.asyncio
async def test_player_chat_group_writes_player_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug H：群聊里玩家的话必须写入在场居民的记忆（与一对一同样强度），
    各自的回应也入本人记忆。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    rb = ResidentRuntime(_resident("b", "乙"))
    eng.residents.update(a=ra, b=rb)
    conv = Conversation(["a", "b"], "广场", 0)
    eng.conversations[conv.id] = conv
    ra.conversation_id = conv.id
    rb.conversation_id = conv.id
    # 玩家与甲同格、乙邻格（均在 3 格对话范围内）
    eng.player = {"x": to_pixel_center(10), "y": to_pixel_center(10)}
    ra.info.x, ra.info.y = to_pixel_center(10), to_pixel_center(10)
    rb.info.x, rb.info.y = to_pixel_center(11), to_pixel_center(10)

    written: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        eng,
        "_write_memory",
        lambda rid, gt, mtype, content, imp: written.append((rid, content, imp)),
    )
    monkeypatch.setattr(we, "record", lambda *args, **kwargs: None)

    async def fake_reply(*args: object, **kwargs: object) -> list[tuple[str, str]]:
        return [("甲", "好呀，说定了"), ("乙", "算我一个")]

    monkeypatch.setattr(we, "player_join_reply", fake_reply)

    lines, error = await eng.player_chat("a", "明天供货日你们去吗")
    assert error is None
    assert len(lines) == 2
    # 玩家的话写入两位在场居民（importance 6，与一对一路径同强度）
    player_mem = [w for w in written if "玩家对我说" in w[1]]
    assert {w[0] for w in player_mem} == {"a", "b"}
    assert all(w[2] == 6 for w in player_mem)
    # 各自的回应写入本人（importance 5）
    reply_mem = [w for w in written if "我回答玩家" in w[1]]
    assert {w[0] for w in reply_mem} == {"a", "b"}


@pytest.mark.asyncio
async def test_player_chat_group_fallback_writes_no_reply_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug H 补充：群聊降级托词不入记忆（与一对一路径同规则），
    但玩家的话仍然要写（玩家真的说过）。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    rb = ResidentRuntime(_resident("b", "乙"))
    eng.residents.update(a=ra, b=rb)
    conv = Conversation(["a", "b"], "广场", 0)
    eng.conversations[conv.id] = conv
    ra.conversation_id = conv.id
    rb.conversation_id = conv.id
    eng.player = {"x": to_pixel_center(10), "y": to_pixel_center(10)}
    ra.info.x, ra.info.y = to_pixel_center(10), to_pixel_center(10)
    rb.info.x, rb.info.y = to_pixel_center(11), to_pixel_center(10)

    written: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        eng,
        "_write_memory",
        lambda rid, gt, mtype, content, imp: written.append((rid, content, imp)),
    )
    monkeypatch.setattr(we, "record", lambda *args, **kwargs: None)

    async def no_reply(*args: object, **kwargs: object) -> list[tuple[str, str]]:
        return []  # 模拟 LLM 超时

    monkeypatch.setattr(we, "player_join_reply", no_reply)

    lines, error = await eng.player_chat("a", "在忙吗")
    assert error is None
    assert len(lines) == 1  # 降级托词回了一句
    assert not any("我回答玩家" in w[1] for w in written)  # 托词没写记忆
    assert {w[0] for w in written} == {"a", "b"}  # 但玩家的话写了


@pytest.mark.asyncio
async def test_events_buffered_without_subscribers() -> None:
    """Bug P：无人订阅时事件进环形缓冲（重连补发看得到），且缓冲有上限。"""
    eng = WorldEngine()
    eng._pending_events.append("甲来到了广场")
    await eng._flush_events()
    assert eng.recent_events() == [
        {"game_time": eng.clock.label(), "text": "甲来到了广场"}
    ]
    # 缓冲会滚动：只留最近 RECENT_EVENT_BUFFER 条
    for i in range(we.RECENT_EVENT_BUFFER + 10):
        eng._pending_events.append(f"事件{i}")
        await eng._flush_events()
    events = eng.recent_events()
    assert len(events) == we.RECENT_EVENT_BUFFER
    assert events[-1]["text"] == f"事件{we.RECENT_EVENT_BUFFER + 9}"


# ---------- 2026-08-22 优化轮：驻足/节流/背压/摘要 ----------


@pytest.mark.asyncio
async def test_player_chat_solo_pauses_resident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A1：一对一聊天时居民驻足（无 Conversation 对象），pause 过期后恢复执行计划。

    旧版等 LLM 回复的十几秒里居民照常赶路（3 格/秒），回复到达时人已
    走出对话范围，第二句必然 too_far。
    """
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    ra.plan = [PlanEntry("00:00", "主街", "闲逛")]
    eng.residents["a"] = ra
    # 居民放在主街站位旁一格（index 0 → spots[0]=(52,23)，驻足解除后一步可达）
    ra.info.x, ra.info.y = to_pixel_center(53), to_pixel_center(23)
    eng.player = {"x": to_pixel_center(53), "y": to_pixel_center(23)}
    monkeypatch.setattr(we, "record", lambda *args, **kwargs: None)

    async def slow_reply(*args: object, **kwargs: object) -> str:
        await asyncio.sleep(0.05)  # 模拟 LLM 延迟，期间主循环在走
        return "嗯"

    monkeypatch.setattr(we, "player_say", slow_reply)

    assert ra.pause_until_minutes == 0  # 驻足前为 0
    lines, error = await eng.player_chat("a", "在吗")
    assert error is None
    assert lines == [("甲", "嗯")]
    # day1 08:00 → now=480，驻足到 490（10 游戏分钟）
    assert ra.pause_until_minutes == 480 + we.PLAYER_CHAT_PAUSE_MINUTES
    # 驻足期间：不动、不执行计划，动作文案变“聊天中”
    eng._step_resident(ra)
    assert ra.current_action == "聊天中"
    assert (to_tile(ra.info.x), to_tile(ra.info.y)) == (53, 23)
    # pause 过期（时间快进 10+ 游戏分钟）：恢复执行计划
    eng.clock.tick(we.PLAYER_CHAT_PAUSE_MINUTES + 1)
    eng._step_resident(ra)
    assert ra.current_action == "闲逛"
    assert (to_tile(ra.info.x), to_tile(ra.info.y)) != (53, 23)


@pytest.mark.asyncio
async def test_player_chat_cooldown_throttle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B2：同一居民冷却窗口内连发直接拒（不烧 LLM）；不同居民互不影响；
    too_far 的尝试不占冷却窗口。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    rb = ResidentRuntime(_resident("b", "乙"))
    eng.residents.update(a=ra, b=rb)
    # 三人同格，距离校验全通过
    eng.player = {"x": to_pixel_center(10), "y": to_pixel_center(10)}
    ra.info.x, ra.info.y = to_pixel_center(10), to_pixel_center(10)
    rb.info.x, rb.info.y = to_pixel_center(10), to_pixel_center(10)
    monkeypatch.setattr(we, "record", lambda *args, **kwargs: None)

    async def ok_reply(*args: object, **kwargs: object) -> str:
        return "嗯"

    monkeypatch.setattr(we, "player_say", ok_reply)

    _, err = await eng.player_chat("a", "第一句")
    assert err is None
    # 冷却窗口内对同一居民再发 → 节流拒绝
    _, err = await eng.player_chat("a", "手滑连发")
    assert err == "cooldown"
    # 不同居民不受 a 的冷却影响
    _, err = await eng.player_chat("b", "你好")
    assert err is None


@pytest.mark.asyncio
async def test_player_chat_too_far_does_not_consume_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B2 补充：距离不够的尝试被拒后，走近立刻发话应该正常（不算 cooldown）。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    eng.residents["a"] = ra
    ra.info.x, ra.info.y = to_pixel_center(10), to_pixel_center(10)
    eng.player = {"x": to_pixel_center(40), "y": to_pixel_center(40)}  # 远离 30 格
    monkeypatch.setattr(we, "record", lambda *args, **kwargs: None)

    async def ok_reply(*args: object, **kwargs: object) -> str:
        return "嗯"

    monkeypatch.setattr(we, "player_say", ok_reply)

    _, err = await eng.player_chat("a", "够不着")
    assert err == "too_far"
    # 立刻走近发话：too_far 不该占用冷却窗口
    eng.player = {"x": to_pixel_center(10), "y": to_pixel_center(10)}
    _, err = await eng.player_chat("a", "现在够着了")
    assert err is None


@pytest.mark.asyncio
async def test_chat_gate_rejects_immediately_while_previous_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B2 关键：消息到达即节流（gate）——WebSocket 端点串行处理，前一句的
    LLM 等待会把后续消息排队推出冷却窗口；只靠 player_chat 内部检查时
    排队消息真正执行时窗口已过，节流形同虚设（浏览器实测发现）。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    eng.residents["a"] = ra
    ra.info.x, ra.info.y = to_pixel_center(10), to_pixel_center(10)
    eng.player = {"x": to_pixel_center(10), "y": to_pixel_center(10)}
    monkeypatch.setattr(we, "record", lambda *args, **kwargs: None)

    async def slow_reply(*args: object, **kwargs: object) -> str:
        await asyncio.sleep(0.05)  # 模拟 LLM 慢（消息在 handler 里排队）
        return "嗯"

    monkeypatch.setattr(we, "player_say", slow_reply)

    task = asyncio.create_task(eng.player_chat("a", "第一句"))
    await asyncio.sleep(0.01)  # 第一句已发起（时间戳已刷新），LLM 等待中
    # 第二句到达（还在排队）：gate 此刻就应拒绝
    assert eng.player_chat_gated("a", "手滑连发") == (None, "cooldown")
    await task

    # gate 放行路径：窗口外 / 未知居民 / 空文本均不代答（交回 player_chat）
    eng._chat_last_at.clear()
    assert eng.player_chat_gated("a", "正常发言") is None
    assert eng.player_chat_gated("nobody", "x") is None
    assert eng.player_chat_gated("a", "   ") is None


@pytest.mark.asyncio
async def test_broadcast_survives_stalled_subscriber() -> None:
    """C1：挂起的标签页（TCP 缓冲写满）不能拖住主循环——发送超时后移除订阅者，
    正常订阅者照常收到状态。"""

    class StalledWS:
        async def send_json(self, message: object) -> None:
            await asyncio.sleep(30)  # 模拟写不进的死连接

    class HealthyWS:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send_json(self, message: dict) -> None:
            self.sent.append(message)

    eng = WorldEngine()
    stalled, healthy = StalledWS(), HealthyWS()
    eng._subscribers.update({stalled, healthy})  # type: ignore[arg-type]

    start = time.monotonic()
    await eng.broadcast()
    elapsed = time.monotonic() - start

    assert elapsed < 3.0  # 超时 1s 就该返回，绝不能等 30s
    assert healthy.sent and healthy.sent[0]["type"] == "world_state"
    assert stalled not in eng._subscribers  # 死订阅者被移除


@pytest.mark.asyncio
async def test_end_conversation_digest_keeps_head_and_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D5：散场摘要取首尾各 2 句——6 回合对话的后半场（关系走向所在）不能丢。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    rb = ResidentRuntime(_resident("b", "乙"))
    eng.residents.update(a=ra, b=rb)
    conv = Conversation(["a", "b"], "广场", 0)
    eng.conversations[conv.id] = conv
    ra.conversation_id = conv.id
    rb.conversation_id = conv.id
    conv.transcript = [
        ("甲", "第1句"),
        ("乙", "第2句"),
        ("甲", "第3句"),
        ("乙", "第4句"),
        ("甲", "第5句"),
        ("乙", "第6句"),
    ]
    written: list[str] = []
    monkeypatch.setattr(
        eng,
        "_write_memory",
        lambda rid, gt, mtype, content, imp: written.append(content),
    )
    monkeypatch.setattr(we, "record", lambda *args, **kwargs: None)

    await eng._end_conversation(conv, reason="聊完了")

    digest = "".join(written)
    assert "第1句" in digest and "第2句" in digest  # 首部保留
    assert "第5句" in digest and "第6句" in digest  # 尾部保留（旧版只取前4句会丢）
    assert "第3句" not in digest and "第4句" not in digest  # 中段被截掉


@pytest.mark.asyncio
async def test_end_conversation_cleans_conv_cooldown_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F4：散场时清理"是否加入这场对话"的询问冷却键——对话没了键就没
    意义了，不清则 dict 随对话数无限增长；居民对冷却（防重聊）必须保留。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    rb = ResidentRuntime(_resident("b", "乙"))
    eng.residents.update(a=ra, b=rb)
    conv = Conversation(["a", "b"], "广场", 0)
    eng.conversations[conv.id] = conv
    ra.conversation_id = conv.id
    rb.conversation_id = conv.id
    join_key = tuple(sorted(("a", f"conv{conv.id}")))  # 曾询问过是否加入
    pair_key = tuple(sorted(("a", "b")))
    eng._encounter_cd[join_key] = 100
    eng._encounter_cd[pair_key] = 100
    monkeypatch.setattr(we, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(eng, "_write_memory", lambda *args, **kwargs: None)

    await eng._end_conversation(conv, reason="聊完了")

    assert join_key not in eng._encounter_cd  # 对话键被清
    assert pair_key in eng._encounter_cd  # 居民对冷却保留（散场后一段时间不重聊）


# ---------- 家具交互点：站位引导与细粒度感知（V3 Phase D） ----------


def _shelf_block():
    from world.objects import BLOCKS_BY_SECTOR

    return next(b for b in BLOCKS_BY_SECTOR["小镇图书馆"] if b.name == "书架")


def test_spot_for_guides_to_matched_furniture() -> None:
    """站位引导：action 提到书架 → 站到书架使用点，而不是普通站位点。"""
    eng = WorldEngine()
    rt = ResidentRuntime(_resident("a", "甲"))
    eng.residents.update(a=rt)
    shelf = _shelf_block()
    target = eng._spot_for(rt, "小镇图书馆", "把书架上的书摆整齐")
    assert target == (shelf.col, shelf.row)


def test_spot_for_falls_back_when_furniture_taken() -> None:
    """使用点被占（其他居民 ≤1 格）→ 回退普通站位点，不与家具旁的人重叠。"""
    from world.locations import LOCATION_SPOTS

    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    rb = ResidentRuntime(_resident("b", "乙"))
    eng.residents.update(a=ra, b=rb)
    shelf = _shelf_block()
    rb.info.x, rb.info.y = to_pixel_center(shelf.col), to_pixel_center(shelf.row)
    target = eng._spot_for(ra, "小镇图书馆", "整理书架")
    assert target != (shelf.col, shelf.row)
    assert target in LOCATION_SPOTS["小镇图书馆"]


def test_arrival_event_mentions_furniture() -> None:
    """到达事件带家具语义（"来到了小镇图书馆，在书架旁"）——涌现叙事的可见性。"""
    eng = WorldEngine()
    rt = ResidentRuntime(_resident("a", "甲"))
    eng.residents.update(a=rt)
    shelf = _shelf_block()
    rt.plan = [PlanEntry("08:00", "小镇图书馆", "整理书架")]
    rt.info.x, rt.info.y = to_pixel_center(shelf.col), to_pixel_center(shelf.row)
    eng._step_resident(rt)
    assert any("在书架旁" in e for e in eng._pending_events)


def test_public_derives_near_object_when_stationary() -> None:
    """public() 细粒度感知：站定在家具旁 + action 相关 + 地点一致 → 下发
    near_object；走路中不下发（防"在冰箱旁赶路"的路过误报）。"""
    eng = WorldEngine()
    rt = ResidentRuntime(_resident("a", "甲"))
    eng.residents.update(a=rt)
    shelf = _shelf_block()
    rt.info.x, rt.info.y = to_pixel_center(shelf.col), to_pixel_center(shelf.row)
    rt.current_location = "小镇图书馆"
    rt.current_action = "在书架前看书"
    d = rt.public()
    assert d.get("near_object") == "书架"
    # 走路中：同位置但 path 非空 → 不报
    rt.path = [(shelf.col + 1, shelf.row)]
    assert "near_object" not in rt.public()


# ---------- 站位防撞（2026-09-03：V3 七人在主街撞同一像素的名牌重影） ----------


def test_spot_for_seven_residents_no_duplicate_station(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """7 % 5 = 2：吴文(index 5)/郑巧(index 6)的序号位与沈青梧/慕容瑾相撞。
    序号位被占时必须按序顺延找空位——前 5 人占满 5 个站位且互不相同。"""
    monkeypatch.setattr(we, "preferred_block", lambda *args, **kwargs: None)
    eng = WorldEngine()
    for i in range(7):
        eng.residents.update(
            {f"r{i}": ResidentRuntime(_resident(f"r{i}", f"居民{i}"), index=i)}
        )
    picked: list[tuple[int, int]] = []
    for i in range(7):
        rt = eng.residents[f"r{i}"]
        spot = eng._spot_for(rt, "主街")
        assert spot is not None
        picked.append(spot)
        rt.target_tile = spot  # 模拟每拍执行：选中后写入目标格
    assert len(set(picked)) == 7  # 5 个站位 + 2 个邻近扩散格：7 人互不重叠
    # 超出站位数的第 6/7 人由邻近扩散兜底：不离自己的首选位太远
    for i in range(5, 7):
        last = eng._spot_for(eng.residents[f"r{i}"], "主街")
        first_i = LOCATION_SPOTS["主街"][i % 5]
        assert max(abs(last[0] - first_i[0]), abs(last[1] - first_i[1])) <= 2


def test_spot_for_respects_other_resident_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """序号位被其他居民的目标格占据时顺延：后选者不得与先选者同格。"""
    monkeypatch.setattr(we, "preferred_block", lambda *args, **kwargs: None)
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"), index=0)
    rb = ResidentRuntime(_resident("b", "乙"), index=1)
    eng.residents.update(a=ra, b=rb)
    ra.target_tile = (52, 23)  # 甲已占主街首选站位
    spot_b = eng._spot_for(rb, "主街")
    assert spot_b is not None and spot_b != (52, 23)
