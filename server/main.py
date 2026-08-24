"""FastAPI 入口 + WebSocket 端点。

设计说明（为什么这样设计）：
- 本文件只负责消息收发与按 type 分发，业务逻辑（感知/检索/计划/反思）全部
  活在 agents/、memory/、world/ 里。换传输层或加新消息类型都不用动核心逻辑。
- 单条 WebSocket 长连接，消息均为 JSON，`type` 字段区分（完整契约见
  docs/TechDesign-AITown-MVP.md 的 "WebSocket 协议" 一节）。
- 世界状态的唯一权威是 world/engine.py 的 WorldEngine（时钟、玩家、居民）；
  端点只做转发，不自己持有状态。
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from llm.client import usage_stats
from world.engine import WorldEngine
from world.mapdata import COLS, ROWS, TILE_SIZE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

engine = WorldEngine()

# 玩家发言长度上限：超长文本直进 LLM prompt，成本与超时风险都放大
# （前端 chat-input 的 maxlength 同款限制，正常聊天远用不到）
MAX_PLAYER_TEXT_CHARS = 200
_WORLD_MAX_X = COLS * TILE_SIZE
_WORLD_MAX_Y = ROWS * TILE_SIZE


def _valid_coord(value: object, max_value: int) -> int | None:
    """player_move 坐标校验：数字且落在世界范围内，返回整数像素；非法 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value <= max_value:
        return None
    return int(value)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    engine.start()
    yield
    engine.stop()


app = FastAPI(title="AI Town", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查：前端/启动脚本用它确认后端已就绪。"""
    return {"status": "ok"}


@app.get("/llm-stats")
async def llm_stats() -> dict[str, Any]:
    """LLM 用量统计：W4 成本校准看这里（缓存命中率 = cached/prompt tokens）。"""
    return usage_stats()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    engine.subscribe(websocket)
    logger.info("client connected")
    # 读循环独立成 task：处理循环会被慢 LLM（15s×2）阻塞，若在同一条循环里
    # 接收+判节流，积压消息的判定会被推迟到冷却窗口之外——手滑连发照样
    # 排队烧钱（2026-08-22 浏览器实测抓到）。reader 与处理循环并行，
    # 消息真正"到达即判"（gate 只查不写，时间戳由 engine 刷新）。
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def reader() -> None:
        try:
            while True:
                message = await websocket.receive_json()
                if isinstance(message, dict) and message.get("type") == "player_chat":
                    payload = message.get("payload")
                    if isinstance(payload, dict):
                        text = payload.get("text", "")
                        if isinstance(text, str) and text.strip():
                            gated = engine.player_chat_gated(
                                str(payload.get("resident_id", "")), text
                            )
                            if gated is not None:
                                await websocket.send_json(
                                    {
                                        "type": "error",
                                        "payload": {
                                            "code": gated[1],
                                            "message": gated[1],
                                        },
                                    }
                                )
                                continue
                await queue.put(message)
        except (WebSocketDisconnect, json.JSONDecodeError, RuntimeError):
            # 断连/非法 JSON/断连后再 receive：读循环结束，
            # 哨兵唤醒可能在等消息的处理循环
            logger.info("client disconnected (reader)")
        finally:
            await queue.put(None)

    reader_task = asyncio.create_task(reader())

    async def handle_message(message: object) -> None:
        """分发一条客户端消息。只做校验与转发，业务逻辑在 engine/agents 层。"""
        # 入口校验：协议约定消息必须是 JSON 对象（AGENTS.md 类型安全约定）
        if not isinstance(message, dict):
            await websocket.send_json(
                {
                    "type": "error",
                    "payload": {
                        "code": "invalid_payload",
                        "message": "消息必须是 JSON 对象",
                    },
                }
            )
            return
        msg_type = message.get("type")
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        if msg_type == "player_move":
            # W1：直接信任客户端坐标；碰撞校验收归服务端是 W2 的事。
            # 不回包：广播循环 3 次/秒已在推 world_state，逐次回包纯冗余
            # （旧版移动 100ms/次上报 × 全量快照回发 = 每秒 10 次冗余序列化）。
            x = _valid_coord(payload.get("x"), _WORLD_MAX_X)
            y = _valid_coord(payload.get("y"), _WORLD_MAX_Y)
            if x is None or y is None:
                await websocket.send_json(
                    {
                        "type": "error",
                        "payload": {
                            "code": "invalid_payload",
                            "message": "player_move 需要世界范围内的数字坐标",
                        },
                    }
                )
            else:
                engine.set_player(x, y)
        elif msg_type == "player_chat":
            text = payload.get("text", "")
            if not isinstance(text, str):
                await websocket.send_json(
                    {
                        "type": "error",
                        "payload": {
                            "code": "invalid_payload",
                            "message": "text 必须是字符串",
                        },
                    }
                )
                return
            if len(text) > MAX_PLAYER_TEXT_CHARS:
                await websocket.send_json(
                    {
                        "type": "error",
                        "payload": {
                            "code": "text_too_long",
                            "message": f"一句话最多 {MAX_PLAYER_TEXT_CHARS} 字",
                        },
                    }
                )
                return
            # 节流已在 reader（到达时）判过；这里直接处理，engine 内部
            # 另有执行时兜底（排队期间前一句刷新时间戳的窗口边界）
            reply, error = await engine.player_chat(
                str(payload.get("resident_id", "")),
                text,
            )
            if error:
                await websocket.send_json(
                    {"type": "error", "payload": {"code": error, "message": error}}
                )
            else:
                # 对话回复进对话面板：群聊时可能是多句（每位参与者一句）
                await websocket.send_json(
                    {
                        "type": "chat_reply",
                        "payload": {
                            "resident_id": payload.get("resident_id"),
                            "lines": [[speaker, text] for speaker, text in reply],
                        },
                    }
                )
        elif msg_type == "save":
            # 即时存档（协议就位；前端按钮属后续 polish，autosave 之外
            # 的手动保存点）。失败也回 ack（ok=False），前端提示重试
            game_time = engine.save_now()
            await websocket.send_json(
                {
                    "type": "save_ack",
                    "payload": {"ok": bool(game_time), "game_time": game_time},
                }
            )
        elif msg_type == "load":
            # 读档在重启时自动进行（autosave）；会话内热读档语义复杂
            # （进行中的对话/冷却如何处置），MVP 明确不做
            await websocket.send_json(
                {
                    "type": "error",
                    "payload": {
                        "code": "not_implemented",
                        "message": "读档在重启时自动进行（autosave），暂不支持会话内读档",
                    },
                }
            )
        else:
            await websocket.send_json(
                {
                    "type": "error",
                    "payload": {
                        "code": "unknown_type",
                        "message": f"未知消息类型: {msg_type}",
                    },
                }
            )

    try:
        # 连接即下发一次全量世界状态，让前端立即有东西可渲染
        await websocket.send_json({"type": "world_state", "payload": engine.snapshot()})
        # 补发最近的小镇播报：断线重连/新开标签页能看到"离开期间发生了什么"
        for event in engine.recent_events():
            await websocket.send_json({"type": "event_log", "payload": event})
        while True:
            message = await queue.get()
            if message is None:
                break  # reader 已结束（客户端断开）
            # 单条消息异常隔离（错误处理补全）：sqlite 瞬时错误/未知 bug
            # 只丢这一条消息并回一个错误，不踢连接——玩家正在进行的会话
            # 不该被服务器内部错误打断（旧版任何一条消息抛异常即断连）。
            # 注意 RuntimeError 也吞：Starlette 断连后 send_json 抛的就是它
            # ——不 re-raise，reader 断连时必投 None 哨兵，循环自然干净退出；
            # 区分不了两种来源时，靠哨兵比靠异常类型可靠
            try:
                await handle_message(message)
            except Exception:
                logger.exception(
                    "消息处理异常（type=%r）",
                    message.get("type") if isinstance(message, dict) else message,
                )
                with suppress(Exception):
                    await websocket.send_json(
                        {
                            "type": "error",
                            "payload": {
                                "code": "internal",
                                "message": "服务器开小差了，请再试一次",
                            },
                        }
                    )
    except (WebSocketDisconnect, RuntimeError):
        # RuntimeError：客户端断连后 handler 里的 send_json 会抛（Starlette
        # 行为）——不捕获会冒泡成 uvicorn ERROR 日志；finally 清理不受影响
        logger.info("client disconnected")
    finally:
        engine.unsubscribe(websocket)
        reader_task.cancel()
        with suppress(asyncio.CancelledError):
            await reader_task
