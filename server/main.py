"""FastAPI 入口 + WebSocket 端点。

设计说明（为什么这样设计）：
- 本文件只负责消息收发与按 type 分发，业务逻辑（感知/检索/计划/反思）全部
  活在 agents/、memory/、world/ 里。换传输层或加新消息类型都不用动核心逻辑。
- 单条 WebSocket 长连接，消息均为 JSON，`type` 字段区分（完整契约见
  docs/TechDesign-AITown-MVP.md 的 "WebSocket 协议" 一节）。
- 世界状态的唯一权威是 world/engine.py 的 WorldEngine（时钟、玩家、居民）；
  端点只做转发，不自己持有状态。
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from world.engine import WorldEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

engine = WorldEngine()


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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    engine.subscribe(websocket)
    logger.info("client connected")
    try:
        # 连接即下发一次全量世界状态，让前端立即有东西可渲染
        await websocket.send_json({"type": "world_state", "payload": engine.snapshot()})
        while True:
            message = await websocket.receive_json()
            msg_type = message.get("type")
            payload = message.get("payload", {})

            if msg_type == "player_move":
                # W1：直接信任客户端坐标；碰撞校验收归服务端是 W2 的事
                engine.set_player(payload.get("x", 0), payload.get("y", 0))
                await websocket.send_json(
                    {"type": "world_state", "payload": engine.snapshot()}
                )
            elif msg_type == "player_chat":
                reply, error = await engine.player_chat(
                    str(payload.get("resident_id", "")),
                    str(payload.get("text", "")),
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
            elif msg_type in ("save", "load"):
                # W4 的功能占位：明确回复未实现，而不是静默丢弃
                await websocket.send_json(
                    {
                        "type": "error",
                        "payload": {
                            "code": "not_implemented",
                            "message": f"{msg_type} 将在后续阶段实现",
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
    except WebSocketDisconnect:
        logger.info("client disconnected")
    finally:
        engine.unsubscribe(websocket)
