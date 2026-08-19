"""WebSocket 契约的集成测试（W1 最小闭环）。

覆盖 docs/TechDesign 协议表中 W1 已实现的部分：
world_state 初始下发、player_move 回环、未实现类型的降级 error。
"""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_world_state_on_connect_and_player_move_roundtrip() -> None:
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "world_state"
        assert "player" in first["payload"]

        ws.send_json({"type": "player_move", "payload": {"x": 123, "y": 456}})
        second = ws.receive_json()
        assert second["type"] == "world_state"
        assert second["payload"]["player"] == {"x": 123, "y": 456}


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
