"""WebSocket 契约的集成测试。

覆盖：world_state 初始下发、player_move 状态更新（不回包）、播报补发、
入口校验（非法坐标/超长文本/非对象消息）、未实现类型的降级 error。
"""

from fastapi.testclient import TestClient

import main
from main import app
from world import engine as we

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_llm_stats_endpoint() -> None:
    """用量统计端点：W4 成本校准的取数口（缓存命中率 = cached/prompt）。"""
    resp = client.get("/llm-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert {
        "calls",
        "failures",
        "prompt_tokens",
        "cached_tokens",
        "completion_tokens",
        "by_tier",
    } <= set(data)


def test_player_move_updates_engine_without_echo() -> None:
    """player_move 只更新状态不回包（广播循环 3/s 已在推 world_state）。

    验证方式：发送后紧接一条必然回包的消息，收到的第一条回包不是 world_state
    ——否则说明 player_move 还在逐次回全量快照（每秒 10 次冗余序列化）。
    """
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # 初始 world_state

        ws.send_json({"type": "player_move", "payload": {"x": 123, "y": 456}})
        ws.send_json(
            {"type": "player_chat", "payload": {"resident_id": "nobody", "text": "hi"}}
        )
        msg = ws.receive_json()
        assert msg["type"] == "error"  # 第一条回包是 chat 错误：player_move 没回包
        assert msg["payload"]["code"] == "unknown_resident"
        assert main.engine.player == {"x": 123, "y": 456}  # 坐标已生效


def test_player_chat_unknown_resident_returns_error() -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # 丢掉初始 world_state

        ws.send_json(
            {"type": "player_chat", "payload": {"resident_id": "nobody", "text": "hi"}}
        )
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "unknown_resident"


def test_unknown_types_return_error() -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # 丢掉初始 world_state

        ws.send_json({"type": "nonsense", "payload": {}})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "unknown_type"


# ---------- 入口校验（2026-08-21 深检：J 防线，AGENTS.md 类型安全约定） ----------


def test_player_move_rejects_non_numeric_coords() -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        main.engine.set_player(10, 20)  # 已知初值，验证非法坐标不生效
        ws.send_json({"type": "player_move", "payload": {"x": "abc", "y": 456}})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "invalid_payload"
        assert main.engine.player == {"x": 10, "y": 20}  # 状态未被破坏


def test_player_move_rejects_out_of_world_coords() -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "player_move", "payload": {"x": 99999, "y": 0}})
        msg = ws.receive_json()
        assert msg["payload"]["code"] == "invalid_payload"


def test_player_chat_rejects_too_long_text() -> None:
    """超长文本直接拒掉：直进 LLM prompt 的成本与超时风险都不可控。"""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "player_chat",
                "payload": {"resident_id": "nobody", "text": "长" * 201},
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "text_too_long"


def test_non_object_message_returns_error() -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json([1, 2, 3])  # 不是 JSON 对象
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "invalid_payload"


# ---------- 存档协议（Phase 3） ----------


def test_save_message_returns_ack(monkeypatch) -> None:
    """save 消息 → 即时存档 + save_ack 回包（存档写入 monkeypatch 拦截，不碰真实库）。"""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        monkeypatch.setattr(we, "save_world", lambda *args, **kwargs: "day1-08:00")
        ws.send_json({"type": "save", "payload": {}})
        msg = ws.receive_json()
        assert msg["type"] == "save_ack"
        assert msg["payload"]["ok"] is True
        assert msg["payload"]["game_time"] == main.engine.clock.label()


def test_save_message_failure_still_acks(monkeypatch) -> None:
    """存档失败不炸连接：ok=False 回包，前端提示重试。

    引擎已是 conftest 的隔离替身，这里直接让替身的 save_now 抛错
    （原 monkeypatch 模块级 we.save_world 对替身无效，审查 A1 改造点）。"""

    def boom() -> str:
        raise OSError("disk full")

    monkeypatch.setattr(main.engine, "save_now", boom)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "save", "payload": {}})
        msg = ws.receive_json()
        assert msg["type"] == "save_ack"
        assert msg["payload"]["ok"] is False


def test_load_message_not_implemented() -> None:
    """读档 = 重启时自动（autosave）；会话内热读档 MVP 明确不做。"""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "load", "payload": {}})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "not_implemented"


def test_message_exception_does_not_kill_connection(monkeypatch) -> None:
    """错误处理补全（五轮 H1）：单条消息处理抛异常（如 sqlite 瞬时错误）
    只丢该条消息并回 internal 错误，连接保活——旧版任何一条消息异常
    即断连，玩家正在进行的会话会被服务器内部错误打断。"""

    async def boom(*args: object, **kwargs: object) -> tuple:
        raise RuntimeError("模拟业务层炸了")

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        monkeypatch.setattr(main.engine, "player_chat", boom)
        ws.send_json(
            {"type": "player_chat", "payload": {"resident_id": "x", "text": "你好"}}
        )
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "internal"
        # 连接仍在：后续消息照常处理（save 走另一条分支，不受影响）
        monkeypatch.setattr(we, "save_world", lambda *args, **kwargs: "day1-08:00")
        ws.send_json({"type": "save", "payload": {}})
        msg2 = ws.receive_json()
        assert msg2["type"] == "save_ack"
        assert msg2["payload"]["ok"] is True
