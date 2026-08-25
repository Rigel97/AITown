"""世界主循环：游戏时钟 + 计划执行 + 状态广播。

设计说明（为什么这样设计）：
- 游戏时钟与现实时间解耦：GAME_MINUTES_PER_REAL_SECOND 一个旋钮控制流速。
  流速直接决定居民活动节奏与 LLM 调用频率（成本变量），必须集中可调。
- 时钟与居民状态活在服务端（权威），前端只渲染——多标签/重连不会状态分裂。
- 事件驱动：时钟每拍只做"查计划表 + 沿路径走一步"，无事发生不调 LLM。
  LLM 只在两个时刻被调用：每天 07:00 生成当日计划（每居民 1 次）、
  玩家/居民对话（W3）。
- 居民状态是内存权威（ResidentRuntime），DB 用于持久化计划与位置。
"""

import asyncio
import logging
import time
from collections import deque
from typing import Any

from fastapi import WebSocket

from agents.dialogue import (
    _fallback_for,
    conversation_turn,
    decide_accept,
    decide_join,
    player_join_reply,
    player_say,
)
from agents.planner import (
    PlanEntry,
    current_plan_entry,
    generate_daily_plan,
    plan_from_json,
)
from agents.reflection import reflect
from agents.resident import Resident, load_residents
from memory.store import add_memory
from world.chronicle import record
from world.clock import WorldClock
from world.locations import LOCATION_SPOTS
from world.mapdata import (
    SPAWN_COL,
    SPAWN_ROW,
    is_walkable,
    nearest_walkable,
    to_pixel_center,
    to_tile,
)
from world.pathfinding import find_path
from world.persistence import (
    SAVE_ERRORS,
    SAVE_VERSION,
    load_world,
    save_world,
)

logger = logging.getLogger(__name__)

# 状态广播间隔（现实秒）。3 拍/秒：每拍走 1 格（仍是 3 格/秒），
# 客户端拿到的是逐格目标点——插值不切墙角、转向精确（2026-08-19 视觉优化）。
# 时钟节奏不变：clock.tick(1/3) × 3 次 = 旧版 tick(1) 的 1 游戏分钟/现实秒。
BROADCAST_INTERVAL_SECONDS = 1 / 3
# 每天生成计划的游戏时刻（居民"醒来"）
PLAN_HOUR = 7
# 居民每拍移动格数（瓦片/拍）。与广播间隔相乘 = 移动速度：
# 1/3 秒 × 1 格 = 3 格/秒 ≈ 96px/s，比玩家慢一点，符合小镇慢节奏
MOVE_TILES_PER_TICK = 1
# 玩家对话的最大距离（瓦片）——走近才能说话，服务端校验
CHAT_RANGE_TILES = 3
# 播报里对话内容的截断长度
EVENT_TEXT_MAX = 40
# 同一对居民偶遇对话的冷却（游戏分钟）：防刷屏 + 控制 LLM 调用成本
ENCOUNTER_COOLDOWN_MINUTES = 30
# 相遇判定的最大距离（瓦片）
ENCOUNTER_RANGE_TILES = 3
# 逐回合对话：每多少游戏分钟一回合
CONVERSATION_TURN_INTERVAL_MINUTES = 5
# 单场对话回合上限
CONVERSATION_MAX_TURNS = 6
# 连续多少回合失败就散场（避免 LLM 连续超时时对话吊死）
CONVERSATION_MAX_FAILURES = 2
# 对话结束后，参与者之间也设冷却，避免一聊完马上又聊
POST_CONVERSATION_COOLDOWN_MINUTES = 20
# 每日反思触发小时（午夜，居民“睡前”总结今天）
REFLECTION_HOUR = 23
# 小镇播报的环形缓冲长度（断线重连/新开标签页时补发最近 N 条，与前端 HUD
# 的 MAX_EVENTS 对齐）
RECENT_EVENT_BUFFER = 50
# 玩家对同一居民的发言冷却（现实秒）：手滑连发会并发打满 LLM 链路
# （每句都是 15s 超时×重试），还会连烧熔断器的失败计数——连续 5 败会把
# 全镇对话一起熔断 60s，正常玩家跟着遭殃
CHAT_COOLDOWN_SECONDS = 2.5
# 玩家单聊时居民驻足的游戏分钟数：等待 LLM 回复（最多 15s×2）期间人不能
# 按计划走远——否则回复到达时人已出对话范围，第二句必然 too_far
PLAYER_CHAT_PAUSE_MINUTES = 10
# 单个订阅者的发送超时（现实秒）：浏览器标签被挂起时 TCP 缓冲写满，
# send_json 会 await 阻塞——逐个顺序发送的世界 tick 会被一个死订阅者拖住
SEND_TIMEOUT_SECONDS = 1.0
# 自动存档间隔（现实秒）：SQLite 单行写 <1ms，60s 足够细——最坏只丢
# 一分钟的世界；停服时另有一笔补偿存档（见 stop）
SAVE_INTERVAL_SECONDS = 60.0


def _truncate(text: str) -> str:
    return text if len(text) <= EVENT_TEXT_MAX else text[:EVENT_TEXT_MAX] + "…"


async def _safe_send(ws: WebSocket, message: dict[str, Any]) -> bool:
    """带超时的发送：超时/异常视同订阅者已死（挂起的标签页），移除并不再打扰。

    没有这层保护时，一个写不进 TCP 缓冲的死连接会把逐个顺序 await 的
    broadcast/事件推送无限挂起——时钟、居民移动、对话推进全部冻结。
    """
    try:
        await asyncio.wait_for(ws.send_json(message), timeout=SEND_TIMEOUT_SECONDS)
        return True
    except Exception:
        logger.warning("推送超时/失败，移除订阅者", exc_info=True)
        return False


class Conversation:
    """一场进行中的对话状态机（ai-town 式逐回合）。

    participants 暂停移动（计划执行跳过），逐回合生成台词，别人能中途加入。
    transcript 是对话全文，供新加入者和反思层检索上下文。
    """

    _next_id = 1

    def __init__(
        self, participant_ids: list[str], location: str, now_minutes: int
    ) -> None:
        self.id = Conversation._next_id
        Conversation._next_id += 1
        self.participant_ids: list[str] = list(participant_ids)
        self.location = location
        self.transcript: list[tuple[str, str]] = []
        self.turn = 0
        self.next_turn_at = now_minutes + 1  # 第一回合很快开始，让对话“正在发生”
        self.failures = 0
        self.busy = False  # 正在生成某回合，防重入


class ResidentRuntime:
    """居民的运行时状态（内存权威）：实体数据 + 当日计划 + 寻路路径。"""

    def __init__(self, info: Resident, index: int = 0) -> None:
        self.info = info
        self.index = index  # 加载序号：用于同地点站位点分配，避免重叠
        self.plan: list[PlanEntry] = plan_from_json(info.daily_plan)
        self.planned_day = 0  # 0 = 今天还没生成计划
        self.path: list[tuple[int, int]] = []
        self.current_action = info.occupation  # 默认动作文案
        self.current_location: str | None = None  # 到达过的最近计划地点
        self.conversation_id: int | None = None  # 正在进行的对话 id（None=没在聊）
        self.reflected_day = 0  # 已反思到第几天（0=还没反思过）
        # 玩家单聊驻足到（游戏分钟）：一对一对话没有 Conversation 对象，
        # 用这个字段让居民“停下来听”——过了这个点恢复执行计划
        self.pause_until_minutes = 0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.info.id,
            "name": self.info.name,
            "x": self.info.x,
            "y": self.info.y,
            "action": self.current_action,
            # 职业也下发：前端动作用不了关键词时，头顶 emoji 回退到职业默认
            "occupation": self.info.occupation,
            "chatting": self.conversation_id is not None,
        }


def _sanitize_xy(x: int, y: int, fallback: tuple[int, int]) -> tuple[int, int]:
    """坐标净化：不可走（越界/落在建筑水面）时投射到最近可走格。

    为什么在 import_state 需要：换地图后旧存档坐标可能超出新世界边界或
    落在新建筑上一一不净化的话居民/玩家会卡在不可走点，寻路起点非法。
    fallback = 投射失败时的兑底（出生点 / seed 坐标）。
    """
    col, row = to_tile(x), to_tile(y)
    if is_walkable(col, row):
        return x, y
    near = nearest_walkable(col, row)
    if near is None:
        return fallback
    return to_pixel_center(near[0]), to_pixel_center(near[1])


class WorldEngine:
    """世界主循环。单例，由 main.py 在 lifespan 里启动/停止。"""

    def __init__(self) -> None:
        self.clock = WorldClock()
        # 玩家出生点读共享地图数据（与前端同源，换地图不用改代码）
        self.player: dict[str, int] = {
            "x": to_pixel_center(SPAWN_COL),
            "y": to_pixel_center(SPAWN_ROW),
        }
        self.residents: dict[str, ResidentRuntime] = {}
        self._subscribers: set[WebSocket] = set()
        self._task: asyncio.Task[None] | None = None
        self._planning = False  # 防止计划生成并发重入
        self._reflecting = False  # 防止反思任务并发重入（同 _planning 的教训）
        self._pending_events: list[str] = []  # 待广播的小镇播报
        # 最近播报的环形缓冲：断线重连/新开标签页补发"离开期间发生了什么"
        self._recent_events: deque[dict[str, str]] = deque(maxlen=RECENT_EVENT_BUFFER)
        # 偶遇对话冷却：key = 排序后的居民 id 对，value = 上次对话的游戏分钟数
        self._encounter_cd: dict[tuple[str, str], int] = {}
        # 玩家发言节流：resident_id → 上次真正发起对话的 time.monotonic()
        self._chat_last_at: dict[str, float] = {}
        # 上次自动存档时刻（time.monotonic()）：存档成功/失败都刷新（失败也
        # 刷：防止磁盘故障时每拍重试 hammering，下个周期再试）。初值取构造
        # 时刻而非 0——不经 start() 的裸引擎（如测试直接跑 run()）不会在
        # 第一拍就触发存档、把测试数据写进真实存档槽
        self._last_save_at = time.monotonic()
        # 进行中的对话：key = conversation id
        self.conversations: dict[int, Conversation] = {}

    def snapshot(self) -> dict[str, Any]:
        return {
            "player": self.player,
            "residents": [rt.public() for rt in self.residents.values()],
            "conversations": [
                {
                    "id": c.id,
                    "names": [
                        self.residents[pid].info.name
                        for pid in c.participant_ids
                        if pid in self.residents
                    ],
                }
                for c in self.conversations.values()
            ],
            "game_time": self.clock.label(),
        }

    def subscribe(self, ws: WebSocket) -> None:
        self._subscribers.add(ws)

    def unsubscribe(self, ws: WebSocket) -> None:
        self._subscribers.discard(ws)

    def set_player(self, x: int, y: int) -> None:
        self.player = {"x": x, "y": y}

    # ---------- 计划 ----------

    async def _ensure_plans(self) -> None:
        """每天 07:00 后为还没有计划的居民生成当日计划（并发，每居民一次 LLM 调用）。

        return_exceptions：单个居民的意外异常（DB 故障等）不能让整轮 gather
        作废——否则赋值全部中止，下一拍又整轮重调 LLM（成本事故）。失败者
        今天跳过、沿用旧计划，明天 07:00 自然重试（重试循环会每拍烧钱，
        成本红线优先）。
        """
        day = self.clock.day
        need = [rt for rt in self.residents.values() if rt.planned_day < day]
        if not need:
            return
        logger.info("为 %d 个居民生成第 %d 天计划…", len(need), day)
        results = await asyncio.gather(
            *(generate_daily_plan(rt.info, day, self.clock.label()) for rt in need),
            return_exceptions=True,
        )
        for rt, res in zip(need, results, strict=True):
            if isinstance(res, BaseException):
                rt.planned_day = day  # 标记今天已处理：不重试，明天再来
                logger.warning(
                    "居民 %s 计划生成异常，今天沿用旧计划: %r", rt.info.id, res
                )
                continue
            rt.plan = res
            rt.planned_day = day
            rt.path = []  # 计划变了，重算路径
        logger.info("第 %d 天计划就绪", day)

    # ---------- 计划执行（每拍，纯本地计算，不调 LLM） ----------

    def _spot_for(self, rt: ResidentRuntime, location: str) -> tuple[int, int] | None:
        """按居民序号从该地点的站位点列表里分一个，避免同地点重叠。"""
        spots = LOCATION_SPOTS.get(location)
        if not spots:
            return None
        return spots[rt.index % len(spots)]

    def _step_resident(self, rt: ResidentRuntime) -> None:
        # 正在对话中的居民不动、不执行计划（“停下来聊”）
        if rt.conversation_id is not None:
            rt.current_action = "聊天中"
            rt.path = []
            return
        # 玩家单聊驻足：同样停下来——否则等 LLM 回复的十几秒里居民已走出
        # 对话范围，回复到了人却走远了（2026-08-22 优化 A1）
        if self._now_game_minutes() < rt.pause_until_minutes:
            rt.current_action = "聊天中"
            rt.path = []
            return
        entry = current_plan_entry(rt.plan, int(self.clock.minutes))
        if entry is None:
            return
        rt.current_action = entry.action

        target = self._spot_for(rt, entry.location)
        if target is None:
            logger.warning("计划含未知地点 %s（%s）", entry.location, rt.info.id)
            return

        current_tile = (to_tile(rt.info.x), to_tile(rt.info.y))
        if current_tile == target:
            rt.path = []
            if rt.current_location != entry.location:
                rt.current_location = entry.location
                self._pending_events.append(f"{rt.info.name}来到了{entry.location}")
                self._check_encounter(rt)
            return
        if not rt.path or rt.path[-1] != target:
            rt.path = find_path(current_tile, target)
            if not rt.path:
                logger.warning(
                    "找不到路径 %s -> %s（%s）", current_tile, target, rt.info.id
                )
                return

        for _ in range(MOVE_TILES_PER_TICK):
            if not rt.path:
                break
            col, row = rt.path.pop(0)
            rt.info.x = to_pixel_center(col)
            rt.info.y = to_pixel_center(row)

    # ---------- 居民相遇（事件驱动：只在“到达”时检测，不逐 tick 扫描） ----------

    def _now_game_minutes(self) -> int:
        return (self.clock.day - 1) * 24 * 60 + int(self.clock.minutes)

    def _check_encounter(self, rt: ResidentRuntime) -> None:
        """居民到达新地点时：
        1) 若附近有【进行中的对话】，问该居民是否加入（LLM 决定）。
        2) 否则若附近有【空闲】居民且过了冷却 → 发起对话邀请。
        冷却按“居民对”计算，控制成本与刷屏。
        """
        my_tile = (to_tile(rt.info.x), to_tile(rt.info.y))
        for other in self.residents.values():
            if other.info.id == rt.info.id:
                continue
            other_tile = (to_tile(other.info.x), to_tile(other.info.y))
            if (
                max(abs(my_tile[0] - other_tile[0]), abs(my_tile[1] - other_tile[1]))
                > ENCOUNTER_RANGE_TILES
            ):
                continue
            # 情况一：对方正在进行对话 → 问 rt 是否加入
            if other.conversation_id is not None:
                conv = self.conversations.get(other.conversation_id)
                if (
                    conv is not None
                    and rt.info.id not in conv.participant_ids
                    and not conv.busy
                ):
                    # 同一居民对同一对话只问一次（用冷却键标记）
                    key = tuple(sorted((rt.info.id, f"conv{conv.id}")))
                    if (
                        self._now_game_minutes() - self._encounter_cd.get(key, -(10**9))
                        < ENCOUNTER_COOLDOWN_MINUTES
                    ):
                        continue
                    self._encounter_cd[key] = self._now_game_minutes()
                    asyncio.create_task(self._maybe_join(rt, other, conv))
                    return
            # 情况二：对方空闲且都未在对话 → 发起邀请
            if other.path or other.conversation_id is not None:
                continue
            pair = tuple(sorted((rt.info.id, other.info.id)))
            if (
                self._now_game_minutes() - self._encounter_cd.get(pair, -(10**9))
                < ENCOUNTER_COOLDOWN_MINUTES
            ):
                continue
            self._encounter_cd[pair] = self._now_game_minutes()
            asyncio.create_task(self._invite(rt, other))
            return  # 一次到达最多触发一场

    async def _invite(self, inviter: ResidentRuntime, invitee: ResidentRuntime) -> None:
        """邀请流程：inviter 向 invitee 发起对话，invitee LLM 决定接受/拒绝。"""
        location = inviter.current_location or invitee.current_location or "路上"
        # 邀请期间任一方走开就取消
        if inviter.conversation_id is not None or invitee.conversation_id is not None:
            return
        accepted = await decide_accept(
            invitee.info, inviter.info.name, self.clock.label(), location
        )
        # decide_accept 是一次 LLM 调用，等待期间双方状态可能已变——不重验会把
        # 人从另一场对话里"抢走"，造成一人双开对话（2026-08-21 深检发现）
        if inviter.conversation_id is not None or invitee.conversation_id is not None:
            return
        if not accepted:
            record(
                "invite",
                self.clock.label(),
                {
                    "inviter": inviter.info.name,
                    "invitee": invitee.info.name,
                    "location": location,
                    "accepted": False,
                },
            )
            self._pending_events.append(
                f"{invitee.info.name}朝{inviter.info.name}摆摆手，继续忙自己的"
            )
            return
        # 双方都停下，进入对话状态机
        conv = Conversation(
            [inviter.info.id, invitee.info.id], location, self._now_game_minutes()
        )
        self.conversations[conv.id] = conv
        inviter.conversation_id = conv.id
        invitee.conversation_id = conv.id
        record(
            "invite",
            self.clock.label(),
            {
                "inviter": inviter.info.name,
                "invitee": invitee.info.name,
                "location": location,
                "accepted": True,
            },
        )
        self._pending_events.append(
            f"{inviter.info.name}和{invitee.info.name}在{location}聊了起来"
        )

    async def _maybe_join(
        self, joiner: ResidentRuntime, participant: ResidentRuntime, conv: Conversation
    ) -> None:
        """路过的居民问要不要加入正在进行的对话。"""
        if conv.id not in self.conversations or joiner.conversation_id is not None:
            return
        chat_names = [
            self.residents[pid].info.name
            for pid in conv.participant_ids
            if pid in self.residents
        ]
        tail = "\n".join(f"{n}：{t}" for n, t in conv.transcript[-3:])
        will_join = await decide_join(
            joiner.info, chat_names, tail, self.clock.label(), conv.location
        )
        # decide_join 是一次 LLM 调用，等待期间对话可能已散场——不重验就把
        # conversation_id 指向已删除的对话，居民会永久卡在"聊天中"且无自愈
        # 路径（2026-08-21 深检实证的 join 竞态）
        if (
            not will_join
            or joiner.conversation_id is not None
            or conv.id not in self.conversations
        ):
            return
        conv.participant_ids.append(joiner.info.id)
        joiner.conversation_id = conv.id
        record(
            "join",
            self.clock.label(),
            {
                "joiner": joiner.info.name,
                "participants": chat_names,
                "location": conv.location,
            },
        )
        self._pending_events.append(f"{joiner.info.name}凑过去加入了他们的聊天")

    def _tick_conversations(self) -> None:
        """主循环每拍调用：到点的对话推进一回合。"""
        now = self._now_game_minutes()
        for conv in list(self.conversations.values()):
            if conv.busy or now < conv.next_turn_at:
                continue
            conv.busy = True
            asyncio.create_task(self._run_turn(conv))

    async def _run_turn(self, conv: Conversation) -> None:
        """生成一回合台词，推进对话；触发结束条件时散场。"""
        try:
            if conv.id not in self.conversations:
                return
            speaker_id = conv.participant_ids[conv.turn % len(conv.participant_ids)]
            speaker = self.residents.get(speaker_id)
            if speaker is None or speaker.conversation_id != conv.id:
                conv.failures += 1
            else:
                others = [
                    self.residents[pid].info
                    for pid in conv.participant_ids
                    if pid != speaker_id and pid in self.residents
                ]
                other_names = [o.name for o in others]
                text, want_end = await conversation_turn(
                    speaker.info,
                    other_names,
                    conv.transcript,
                    self.clock.label(),
                    conv.location,
                )
                if text:
                    conv.transcript.append((speaker.info.name, text))
                    self._pending_events.append(
                        f"{speaker.info.name}：「{_truncate(text)}」"
                    )
                    conv.failures = 0
                else:
                    conv.failures += 1
                if want_end:
                    await self._end_conversation(conv, reason="聊完了")
                    return
            conv.turn += 1
            conv.next_turn_at = (
                self._now_game_minutes() + CONVERSATION_TURN_INTERVAL_MINUTES
            )
            if conv.turn >= CONVERSATION_MAX_TURNS:
                await self._end_conversation(conv, reason="聊够了")
                return
            if conv.failures >= CONVERSATION_MAX_FAILURES:
                await self._end_conversation(conv, reason="对话没接上")
        finally:
            conv.busy = False

    async def _end_conversation(self, conv: Conversation, reason: str) -> None:
        """散场：恢复参与者移动、写记忆摘要、设互间冷却。"""
        if conv.id not in self.conversations:
            return
        names = []
        now = self._now_game_minutes()
        ids = list(conv.participant_ids)
        for pid in ids:
            rt = self.residents.get(pid)
            if rt and rt.conversation_id == conv.id:
                rt.conversation_id = None
                names.append(rt.info.name)
        # 写记忆摘要（不额外调 LLM）：取首尾各 2 句——只取头 4 句会把
        # 6 回合对话的后半场丢掉，而收尾才是关系走向所在（谁约了谁、谁拒了谁）
        transcript = conv.transcript
        picked = transcript[:2] + transcript[-2:] if len(transcript) > 4 else transcript
        digest = "；".join(f"{n}说「{t}」" for n, t in picked)
        for pid in ids:
            rt = self.residents.get(pid)
            if rt is None:
                continue
            others = "、".join(
                self.residents[oid].info.name
                for oid in ids
                if oid != pid and oid in self.residents
            )
            self._write_memory(
                rt.info.id,
                self.clock.label(),
                "dialogue",
                f"我在{conv.location}和{others}聊天：{digest}",
                5,
            )
        # 参与者之间互设冷却，避免一聊完马上又聊
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                self._encounter_cd[tuple(sorted((a, b)))] = now
        # 清掉"是否加入这场对话"的询问冷却键：对话已散，键再无意义——
        # 不清的话 dict 随对话数无限增长（慢性泄漏，2026-08-22 三轮 F4）
        conv_key = f"conv{conv.id}"
        self._encounter_cd = {
            k: v for k, v in self._encounter_cd.items() if conv_key not in k
        }
        # 编年史：逐句全文落盘（给"人"回看的档案；上面的记忆摘要才是给
        # "机器"检索用的——两者职责不同，见 world/chronicle.py）
        record(
            "conversation",
            self.clock.label(),
            {
                "participants": [
                    self.residents[pid].info.name
                    for pid in ids
                    if pid in self.residents
                ],
                "location": conv.location,
                "reason": reason,
                "transcript": [{"speaker": n, "text": t} for n, t in conv.transcript],
            },
        )
        self._pending_events.append(f"{'、'.join(names)}聊完散了（{reason}）")
        self.conversations.pop(conv.id, None)

    def _write_memory(
        self,
        resident_id: str,
        game_time: str,
        mtype: str,
        content: str,
        importance: int,
    ) -> None:
        """engine 层写记忆的薄封装：统一入口便于测试拦截（monkeypatch 一处生效）。"""
        add_memory(resident_id, game_time, mtype, content, importance)

    # ---------- 存档（Phase 3：关掉重开世界连续） ----------

    def export_state(self) -> dict[str, Any]:
        """世界快照（供存档）：时钟/玩家/居民运行态。

        瞬态不入档：进行中的对话（中断即散场是可接受的叙事断点）、冷却、
        节流时间戳、播报缓冲（编年史才是完整档案）——见 persistence.py 说明。
        """
        return {
            "version": SAVE_VERSION,
            "clock": {"day": self.clock.day, "minutes": self.clock.minutes},
            "player": {"x": self.player["x"], "y": self.player["y"]},
            "residents": [
                {
                    "id": rt.info.id,
                    "x": rt.info.x,
                    "y": rt.info.y,
                    "planned_day": rt.planned_day,
                    "reflected_day": rt.reflected_day,
                    "current_location": rt.current_location,
                    "current_action": rt.current_action,
                }
                for rt in self.residents.values()
            ],
        }

    def import_state(self, state: dict[str, Any]) -> None:
        """从快照恢复运行态。顶层键缺失抛 KeyError（由 _load_from_save 兜底
        判为新世界）；单居民字段宽松容错（部分旧档/已删人设不炸读档）。"""
        clock = state["clock"]
        player = state["player"]
        self.clock.day = int(clock["day"])
        self.clock.minutes = float(clock["minutes"])
        # 换图后的旧坐标可能越界/落在建筑上：投射到最近可走格（见 _sanitize_xy）
        self.player["x"], self.player["y"] = _sanitize_xy(
            int(player["x"]),
            int(player["y"]),
            (to_pixel_center(SPAWN_COL), to_pixel_center(SPAWN_ROW)),
        )
        for r in state["residents"]:
            rt = self.residents.get(str(r.get("id")))
            if rt is None:
                continue  # 存档里有但当前定档没有（人设已删）——跳过
            rt.info.x, rt.info.y = _sanitize_xy(
                int(r["x"]),
                int(r["y"]),
                (rt.info.x, rt.info.y),
            )
            rt.planned_day = int(r.get("planned_day", 0))
            rt.reflected_day = int(r.get("reflected_day", 0))
            rt.current_location = r.get("current_location")
            rt.current_action = str(r.get("current_action", rt.current_action))
            rt.path = []  # 旧寻路路径作废，下一拍按当前计划重算

    def _load_from_save(self) -> bool:
        """启动时读 autosave：恢复时钟/玩家/居民运行态。

        无档/坏档/版本不符一律 False（新世界路径）——存档是增强不是依赖。
        """
        state = load_world()
        if state is None:
            return False
        if state.get("version") != SAVE_VERSION:
            logger.warning(
                "存档版本不符（%r != %r），按新世界启动",
                state.get("version"),
                SAVE_VERSION,
            )
            return False
        try:
            self.import_state(state)
        except (KeyError, TypeError, ValueError):
            logger.warning("存档恢复失败，按新世界启动", exc_info=True)
            return False
        return True

    def save_now(self) -> str:
        """立即存档（autosave 覆盖）。成功返回 game_time 标签，失败返回空串。

        失败也刷新 _last_save_at：磁盘故障时下个自动存档周期再试，
        而不是每拍重试 hammering。存档是旁路，任何失败不打断游戏。
        """
        state = self.export_state()
        try:
            save_world(state)
        except SAVE_ERRORS:
            logger.exception("存档失败（下个周期重试）")
            return ""
        finally:
            self._last_save_at = time.monotonic()
        return self.clock.label()

    def _autosave_if_due(self) -> None:
        """主循环每拍调用：到间隔就存一笔。"""
        if time.monotonic() - self._last_save_at < SAVE_INTERVAL_SECONDS:
            return
        if self.save_now():
            logger.debug("自动存档完成：%s", self.clock.label())

    # ---------- 主循环 ----------

    async def broadcast(self) -> None:
        if not self._subscribers:
            return
        message = {"type": "world_state", "payload": self.snapshot()}
        for ws in list(self._subscribers):
            if not await _safe_send(ws, message):
                self._subscribers.discard(ws)

    async def run(self) -> None:
        """主循环：走时钟 → 触发计划/反思（事件）→ 执行计划 → 推进对话 → 广播。

        每拍整体 try/except：run() 是裸 asyncio task，异常会被静默吞掉——
        没有隔离时任何一拍抛异常 = 时钟/广播/居民全部冻结且几乎没有日志
        （2026-08-21 深检实证的"世界静默停摆"，一个畸形计划时间就够）。
        广播放在隔离段之外：就算本拍炸了，客户端也持续收到世界状态。
        """
        while True:
            await asyncio.sleep(BROADCAST_INTERVAL_SECONDS)
            try:
                prev_day = self.clock.day
                self.clock.tick(BROADCAST_INTERVAL_SECONDS)
                self._maybe_start_planning()
                self._maybe_start_reflections(prev_day)
                self._step_all_residents()
                self._tick_conversations()
            except Exception:
                logger.exception("主循环本拍异常，跳过本拍继续")
            await self.broadcast()
            await self._flush_events()
            self._autosave_if_due()

    def _maybe_start_planning(self) -> None:
        """到了 07:00 且有居民没计划 → 后台生成（_planning 防并发重入，不阻塞主循环）。"""
        if (
            self.clock.minutes >= PLAN_HOUR * 60
            and not self._planning
            and any(rt.planned_day < self.clock.day for rt in self.residents.values())
        ):
            self._planning = True
            asyncio.create_task(self._ensure_plans_guarded())

    def _maybe_start_reflections(self, day: int) -> None:
        """过了 23:00 且有人没反思今天 → 后台反思。

        _reflecting 防重入不可省：reflected_day 要等 M3 全部返回才更新，
        没有守卫时主循环每 1/3 秒都会再建一个全套反思任务——M3 延迟 5–60s
        意味着成本放大 15–30 倍（2026-08-21 深检实证的"反思风暴"）。
        """
        if (
            self.clock.minutes >= REFLECTION_HOUR * 60
            and not self._reflecting
            and any(rt.reflected_day < day for rt in self.residents.values())
        ):
            self._reflecting = True
            asyncio.create_task(self._run_reflections_guarded(day))

    def _step_all_residents(self) -> None:
        """逐居民执行计划。单居民异常只跳过本人——一个脏计划不能冻结全镇。"""
        for rt in self.residents.values():
            try:
                self._step_resident(rt)
            except Exception:
                logger.exception("居民 %s 计划执行异常，跳过", rt.info.id)

    async def _flush_events(self) -> None:
        """把本拍产生的小镇播报推给所有客户端，并写入环形缓冲。

        缓冲无条件写入（哪怕当前没人订阅）：断线重连/新开标签页时补发最近
        事件，玩家才看得到"离开期间小镇发生了什么"（2026-08-21 深检：
        旧版无人订阅时直接丢弃）。
        """
        if not self._pending_events:
            return
        for text in self._pending_events:
            entry = {"game_time": self.clock.label(), "text": text}
            self._recent_events.append(entry)
            if not self._subscribers:
                continue
            message = {"type": "event_log", "payload": entry}
            for ws in list(self._subscribers):
                if not await _safe_send(ws, message):
                    self._subscribers.discard(ws)
        self._pending_events.clear()

    def recent_events(self) -> list[dict[str, str]]:
        """最近播报的快照（连接建立时补发给客户端）。"""
        return list(self._recent_events)

    # ---------- 玩家对话 ----------

    async def _run_reflections(self, day: int) -> None:
        """为全体居民生成当日反思（每居民 1 次 M3 调用，并发）。"""
        need = [rt for rt in self.residents.values() if rt.reflected_day < day]
        if not need:
            return
        logger.info("为 %d 个居民生成第 %d 天反思…", len(need), day)
        results = await asyncio.gather(
            *(reflect(rt.info, self.clock.label(), day) for rt in need),
            return_exceptions=True,
        )
        for rt, res in zip(need, results, strict=True):
            rt.reflected_day = day
            if isinstance(res, Exception):
                logger.warning("%s 反思失败: %s", rt.info.id, res)
            elif isinstance(res, int) and res:
                self._pending_events.append(f"{rt.info.name}回味着今天发生的事")
        logger.info("第 %d 天反思完成", day)

    # ---------- 玩家对话 ----------

    def player_chat_gated(
        self, resident_id: str, text: str
    ) -> tuple[list[tuple[str, str]] | None, str | None] | None:
        """消息到达即节流（main.py 收到 player_chat 时同步调用）。

        为什么不只在 player_chat 里查：WebSocket 端点串行处理消息，前一句的
        LLM 等待（最长 15s×2）会把后续消息推迟出冷却窗口——到达时判才能
        挡住"手滑连发排队烧钱"（2026-08-22 浏览器实测发现）。这里只查不写：
        时间戳仍由 player_chat 在距离校验通过后刷新（too_far 不占窗口）。
        """
        rt = self.residents.get(resident_id)
        if rt is None:
            return None  # 交给 player_chat 返回 unknown_resident
        if not text.strip():
            return None  # 同上，empty_text
        if time.monotonic() - self._chat_last_at.get(resident_id, 0.0) < (
            CHAT_COOLDOWN_SECONDS
        ):
            return None, "cooldown"
        return None  # 放行：正常路径交给 player_chat（含执行时兜底节流）

    async def player_chat(
        self, resident_id: str, text: str
    ) -> tuple[list[tuple[str, str]] | None, str | None]:
        """玩家对居民说话。

        - 若目标居民正在进行对话 → 加入群聊：玩家话进 transcript，生成在场居民的回应（1–2 句）。
        - 否则 → 一对一对话（原路径）。
        返回 (台词列表 [(说话人, 台词)], 错误码)；成功时错误码为 None。
        """
        rt = self.residents.get(resident_id)
        if rt is None:
            return None, "unknown_resident"
        if not text.strip():
            return None, "empty_text"
        # 节流（执行时兜底）：消息到达时已过 gate 检查，但排队等待期间前一句
        # 可能已刷新时间戳——这里再拦一次，双保险。
        # （时间戳只在通过全部校验、真正发起时刷新，too_far 的尝试不占冷却）
        if time.monotonic() - self._chat_last_at.get(resident_id, 0.0) < (
            CHAT_COOLDOWN_SECONDS
        ):
            return None, "cooldown"
        # 距离校验：走近才能说话（服务端权威）
        dx = abs(to_tile(rt.info.x) - to_tile(self.player["x"]))
        dy = abs(to_tile(rt.info.y) - to_tile(self.player["y"]))
        if max(dx, dy) > CHAT_RANGE_TILES:
            return None, "too_far"
        text = text.strip()
        game_time = self.clock.label()
        self._chat_last_at[resident_id] = time.monotonic()

        # 情况一：目标正在对话中 → 玩家加入群聊
        if rt.conversation_id is not None:
            conv = self.conversations.get(rt.conversation_id)
            if conv is None:
                return None, "no_conversation"
            conv.transcript.append(("玩家", text))
            self._pending_events.append(f"玩家凑过来对大家说：「{_truncate(text)}」")
            participants = [
                self.residents[pid].info
                for pid in conv.participant_ids
                if pid in self.residents
            ]
            lines = await player_join_reply(
                participants, text, conv.transcript, game_time, conv.location
            )
            degraded = (
                not lines
            )  # 生成失败（LLM 超时）→ 下面用托词补位（要在补位前判！）
            # 玩家的话永远写入在场居民的记忆（importance 6，与一对一路径同强度
            # ——群聊互动不该被"淡忘"，2026-08-21 深检：两条路径记忆强度曾不一致）
            for p in participants:
                self._write_memory(
                    p.id, game_time, "dialogue", f"玩家对我说：「{text}」", 6
                )
            if lines:
                # 真实回应各自入记忆（importance 5）；降级托词不入记忆
                # （known issue #56 同款规则：托词被检索命中会污染人设）
                speaker_ids = {p.name: p.id for p in participants}
                for speaker, line in lines:
                    speaker_id = speaker_ids.get(speaker)
                    if speaker_id is not None:
                        self._write_memory(
                            speaker_id,
                            game_time,
                            "dialogue",
                            f"我回答玩家：「{line}」",
                            5,
                        )
            else:
                # 超时降级：目标居民用托词回一句，不让玩家干等
                reply = _fallback_for(rt.info.id, text)
                lines = [(rt.info.name, reply)]
            for speaker, line in lines:
                conv.transcript.append((speaker, line))
                self._pending_events.append(f"{speaker}：「{_truncate(line)}」")
            record(
                "player_chat",
                game_time,
                {
                    "mode": "group",
                    "resident": rt.info.name,
                    "location": conv.location,
                    "player_text": text,
                    "degraded": degraded,
                    "replies": [
                        {"speaker": speaker, "text": line} for speaker, line in lines
                    ],
                },
            )
            return lines, None

        # 情况二：一对一对话
        location = rt.current_location or "路上"
        # 驻足：等回复期间不走远（见 ResidentRuntime.pause_until_minutes）
        rt.pause_until_minutes = self._now_game_minutes() + PLAYER_CHAT_PAUSE_MINUTES
        reply = await player_say(rt.info, text, game_time, location)
        degraded = not reply  # 两次超时：托词由引擎统一给出（编年史同步标记）
        if degraded:
            reply = _fallback_for(rt.info.id, text)
        record(
            "player_chat",
            game_time,
            {
                "mode": "solo",
                "resident": rt.info.name,
                "location": location,
                "player_text": text,
                "degraded": degraded,
                "replies": [{"speaker": rt.info.name, "text": reply}],
            },
        )
        self._pending_events.append(f"玩家对{rt.info.name}说：「{_truncate(text)}」")
        self._pending_events.append(f"{rt.info.name}：「{_truncate(reply)}」")
        return [(rt.info.name, reply)], None

    async def _run_reflections_guarded(self, day: int) -> None:
        try:
            await self._run_reflections(day)
        finally:
            self._reflecting = False

    async def _ensure_plans_guarded(self) -> None:
        try:
            await self._ensure_plans()
        finally:
            self._planning = False

    def start(self) -> None:
        # 从 residents 表加载居民（含已持久化的当日计划）
        for i, r in enumerate(load_residents()):
            self.residents[r.id] = ResidentRuntime(r, index=i)
        logger.info("loaded %d residents", len(self.residents))
        # 读档：世界从上次离开的地方继续（无档则新世界）——planned_day/
        # reflected_day 一并恢复，重启不再重烧当日计划与反思的 LLM 调用
        if self._load_from_save():
            logger.info("读档恢复：世界从 %s 继续", self.clock.label())
        else:
            logger.info("无有效存档，新世界从 %s 开始", self.clock.label())
        self._last_save_at = time.monotonic()
        self._task = asyncio.create_task(self.run())
        logger.info("world engine started")

    def stop(self) -> None:
        # 停服存档：关掉重开世界连续（kill 信号→lifespan teardown→这里）
        if self.save_now():
            logger.info("停服存档完成：%s", self.clock.label())
        if self._task:
            self._task.cancel()
            self._task = None
