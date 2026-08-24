"""世界存档的测试（临时库 + monkeypatch，不碰真实存档）。

覆盖：persistence 读写往返/覆盖语义/坏档容错；engine 快照导出导入往返；
启动读档恢复与时钟/居民字段；自动存档的时间触发逻辑；存档失败不打断。
"""

import sqlite3
import time
from pathlib import Path

import pytest

from agents.resident import Resident
from world import engine as we
from world.engine import ResidentRuntime, WorldEngine
from world.persistence import (
    AUTOSAVE_NAME,
    SAVE_VERSION,
    load_world,
    save_world,
)

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA.read_text())
    return db


def _resident(rid: str, name: str) -> Resident:
    return Resident(
        id=rid, name=name, occupation="测试", prompt_prefix="前缀", x=16, y=16
    )


def _snapshot(day: int = 3, minutes: float = 600.0) -> dict:
    """一份合法快照（version/clock/player/residents 齐全）。"""
    return {
        "version": SAVE_VERSION,
        "clock": {"day": day, "minutes": minutes},
        "player": {"x": 100, "y": 200},
        "residents": [
            {
                "id": "a",
                "x": 300,
                "y": 400,
                "planned_day": 3,
                "reflected_day": 2,
                "current_location": "面包店",
                "current_action": "揉面",
            }
        ],
    }


# ---------- persistence 读写 ----------


def test_save_load_roundtrip(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    state = _snapshot()
    game_time = save_world(state, db_path=db)
    assert game_time == "day3-10:00"  # game_time 列由快照时钟算出
    assert load_world(db_path=db) == state


def test_save_overwrites_previous(tmp_path: Path) -> None:
    """autosave 是单行覆盖语义：旧档被顶掉，不留双胞胎。"""
    db = _fresh_db(tmp_path)
    save_world(_snapshot(day=1), db_path=db)
    save_world(_snapshot(day=5), db_path=db)
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM saves").fetchone()[0]
    assert count == 1
    assert load_world(db_path=db)["clock"]["day"] == 5


def test_load_absent_returns_none(tmp_path: Path) -> None:
    assert load_world(db_path=_fresh_db(tmp_path)) is None


def test_load_corrupt_json_returns_none(tmp_path: Path) -> None:
    """坏档不炸启动：返回 None 走新世界路径（存档是增强不是依赖）。"""
    db = _fresh_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO saves (name, game_time, world_state) VALUES (?, ?, ?)",
            (AUTOSAVE_NAME, "day1-08:00", "{broken json"),
        )
    assert load_world(db_path=db) is None


def test_load_non_dict_returns_none(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO saves (name, game_time, world_state) VALUES (?, ?, ?)",
            (AUTOSAVE_NAME, "day1-08:00", "[1, 2, 3]"),
        )
    assert load_world(db_path=db) is None


# ---------- engine 快照导出/导入 ----------


def test_export_import_roundtrip() -> None:
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    rb = ResidentRuntime(_resident("b", "乙"))
    eng.residents.update(a=ra, b=rb)
    eng.clock.day, eng.clock.minutes = 7, 930.0
    eng.player = {"x": 752, "y": 208}
    ra.info.x, ra.info.y = 300, 400
    ra.planned_day, ra.reflected_day = 7, 6
    ra.current_location, ra.current_action = "面包店", "揉面"
    ra.path = [(1, 1)]

    state = eng.export_state()

    eng2 = WorldEngine()
    eng2.residents.update(
        a=ResidentRuntime(_resident("a", "甲")),
        b=ResidentRuntime(_resident("b", "乙")),
    )
    eng2.import_state(state)
    assert eng2.clock.label() == "day7-15:30"
    assert eng2.player == {"x": 752, "y": 208}
    rt = eng2.residents["a"]
    assert (rt.info.x, rt.info.y) == (300, 400)
    assert rt.planned_day == 7 and rt.reflected_day == 6
    assert rt.current_location == "面包店" and rt.current_action == "揉面"
    assert rt.path == []  # 旧寻路路径作废，下一拍重算
    # 没进快照的居民（b）保持默认
    assert eng2.residents["b"].planned_day == 0


def test_import_skips_unknown_residents() -> None:
    """快照里有但当前定档没有的居民（人设已删）——跳过，不炸读档。"""
    eng = WorldEngine()
    ra = ResidentRuntime(_resident("a", "甲"))
    eng.residents["a"] = ra
    state = _snapshot()
    state["residents"].append({"id": "ghost", "x": 1, "y": 2})
    eng.import_state(state)
    assert "ghost" not in eng.residents
    assert (ra.info.x, ra.info.y) == (300, 400)


# ---------- 启动读档 ----------


def test_load_from_save_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = WorldEngine()
    eng.residents["a"] = ResidentRuntime(_resident("a", "甲"))
    monkeypatch.setattr(we, "load_world", lambda *args, **kwargs: _snapshot())
    assert eng._load_from_save() is True
    assert eng.clock.label() == "day3-10:00"
    assert eng.player == {"x": 100, "y": 200}
    assert eng.residents["a"].planned_day == 3  # 重启不重烧当日计划


def test_load_from_save_rejects_wrong_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """版本不符直接拒绝：旧格式字段含义可能已变，冒险迁移不如重开。"""
    eng = WorldEngine()
    bad = _snapshot()
    bad["version"] = 99
    monkeypatch.setattr(we, "load_world", lambda *args, **kwargs: bad)
    assert eng._load_from_save() is False
    assert eng.clock.label() == "day1-08:00"  # 新世界默认，未被半恢复污染


def test_load_from_save_tolerates_missing_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺顶层键（结构损坏）→ 新世界，不炸启动。"""
    eng = WorldEngine()
    monkeypatch.setattr(
        we, "load_world", lambda *args, **kwargs: {"version": SAVE_VERSION}
    )
    assert eng._load_from_save() is False


# ---------- 存档触发与失败降级 ----------


def test_save_now_persists_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = WorldEngine()
    eng.residents["a"] = ResidentRuntime(_resident("a", "甲"))
    captured: dict[str, object] = {}

    def fake_save(state: dict, *args: object, **kwargs: object) -> str:
        captured["state"] = state
        return "day1-08:00"

    monkeypatch.setattr(we, "save_world", fake_save)
    assert eng.save_now() == "day1-08:00"
    state = captured["state"]
    assert state["version"] == SAVE_VERSION
    assert state["residents"][0]["id"] == "a"
    assert eng._last_save_at > 0


def test_save_now_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """存档失败不抛、返回空串、时间戳照刷新（防每拍重试 hammering）。"""

    def boom(state: dict, *args: object, **kwargs: object) -> str:
        raise OSError("disk full")

    eng = WorldEngine()
    monkeypatch.setattr(we, "save_world", boom)
    assert eng.save_now() == ""
    assert eng._last_save_at > 0


def test_autosave_if_due_triggers_only_when_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_save(state: dict, *args: object, **kwargs: object) -> str:
        calls.append(state)
        return "day1-08:00"

    eng = WorldEngine()
    monkeypatch.setattr(we, "save_world", fake_save)
    eng._last_save_at = time.monotonic()  # 刚存过：未到期
    eng._autosave_if_due()
    assert calls == []
    eng._last_save_at -= we.SAVE_INTERVAL_SECONDS + 1  # 拨回过期
    eng._autosave_if_due()
    assert len(calls) == 1
