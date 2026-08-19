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
from world.chronicle import record
from world.clock import WorldClock
from world.locations import LOCATION_SPOTS
from world.mapdata import SPAWN_COL, SPAWN_ROW, to_pixel_center, to_tile
from world.pathfinding import find_path

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


def _truncate(text: str) -> str:
    return text if len(text) <= EVENT_TEXT_MAX else text[:EVENT_TEXT_MAX] + "…"


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
        self._pending_events: list[str] = []  # 待广播的小镇播报
        # 偶遇对话冷却：key = 排序后的居民 id 对，value = 上次对话的游戏分钟数
        self._encounter_cd: dict[tuple[str, str], int] = {}
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
        """每天 07:00 后为还没有计划的居民生成当日计划（并发，每居民一次 LLM 调用）。"""
        day = self.clock.day
        need = [rt for rt in self.residents.values() if rt.planned_day < day]
        if not need:
            return
        logger.info("为 %d 个居民生成第 %d 天计划…", len(need), day)
        plans = await asyncio.gather(
            *(generate_daily_plan(rt.info, day, self.clock.label()) for rt in need)
        )
        for rt, plan in zip(need, plans, strict=True):
            rt.plan = plan
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
        if not will_join or joiner.conversation_id is not None:
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
        # 写记忆摘要（不额外调 LLM，直接从 transcript 摘几条）
        digest = "；".join(f"{n}说「{t}」" for n, t in conv.transcript[:4])
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
        """engine 层写记忆的薄封装，避免在 engine 里直接 import memory.store。"""
        from memory.store import add_memory

        add_memory(resident_id, game_time, mtype, content, importance)

    # ---------- 主循环 ----------

    async def broadcast(self) -> None:
        if not self._subscribers:
            return
        message = {"type": "world_state", "payload": self.snapshot()}
        for ws in list(self._subscribers):
            try:
                await ws.send_json(message)
            except Exception:
                logger.warning("broadcast 失败，移除订阅者", exc_info=True)
                self._subscribers.discard(ws)

    async def run(self) -> None:
        """主循环：走时钟 → 触发计划/反思（事件）→ 执行计划 → 推进对话 → 广播。"""
        while True:
            await asyncio.sleep(BROADCAST_INTERVAL_SECONDS)
            prev_day = self.clock.day
            self.clock.tick(BROADCAST_INTERVAL_SECONDS)

            # 事件：到了 07:00 且有居民没计划 → 后台生成（不阻塞主循环）
            if (
                self.clock.minutes >= PLAN_HOUR * 60
                and not self._planning
                and any(
                    rt.planned_day < self.clock.day for rt in self.residents.values()
                )
            ):
                self._planning = True
                asyncio.create_task(self._ensure_plans_guarded())

            # 事件：过了 23:00 且有人没反思今天 → 后台生成反思
            if self.clock.minutes >= REFLECTION_HOUR * 60 and any(
                rt.reflected_day < prev_day for rt in self.residents.values()
            ):
                asyncio.create_task(self._run_reflections(prev_day))

            for rt in self.residents.values():
                self._step_resident(rt)
            self._tick_conversations()
            await self.broadcast()
            await self._flush_events()

    async def _flush_events(self) -> None:
        """把本拍产生的小镇播报推给所有客户端（事件日志流）。"""
        if not self._pending_events or not self._subscribers:
            self._pending_events.clear()
            return
        for text in self._pending_events:
            message = {
                "type": "event_log",
                "payload": {"game_time": self.clock.label(), "text": text},
            }
            for ws in list(self._subscribers):
                try:
                    await ws.send_json(message)
                except Exception:  # 发送失败只能丢弃，不能断主循环
                    logger.warning("event 推送失败，移除订阅者", exc_info=True)
                    self._subscribers.discard(ws)
        self._pending_events.clear()

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
        # 距离校验：走近才能说话（服务端权威）
        dx = abs(to_tile(rt.info.x) - to_tile(self.player["x"]))
        dy = abs(to_tile(rt.info.y) - to_tile(self.player["y"]))
        if max(dx, dy) > CHAT_RANGE_TILES:
            return None, "too_far"
        text = text.strip()
        game_time = self.clock.label()

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
            for speaker, line in lines:
                conv.transcript.append((speaker, line))
                self._pending_events.append(f"{speaker}：「{_truncate(line)}」")
            if not lines:
                # 超时降级：目标居民用托词回一句，不让玩家干等
                reply = _fallback_for(rt.info.id, text)
                lines = [(rt.info.name, reply)]
            record(
                "player_chat",
                game_time,
                {
                    "mode": "group",
                    "resident": rt.info.name,
                    "location": conv.location,
                    "player_text": text,
                    "replies": [
                        {"speaker": speaker, "text": line} for speaker, line in lines
                    ],
                },
            )
            return lines, None

        # 情况二：一对一对话
        location = rt.current_location or "路上"
        reply = await player_say(rt.info, text, game_time, location)
        record(
            "player_chat",
            game_time,
            {
                "mode": "solo",
                "resident": rt.info.name,
                "location": location,
                "player_text": text,
                "replies": [{"speaker": rt.info.name, "text": reply}],
            },
        )
        self._pending_events.append(f"玩家对{rt.info.name}说：「{_truncate(text)}」")
        self._pending_events.append(f"{rt.info.name}：「{_truncate(reply)}」")
        return [(rt.info.name, reply)], None

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
        self._task = asyncio.create_task(self.run())
        logger.info("world engine started")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
