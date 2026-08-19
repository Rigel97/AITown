"""world/engine.py 游戏时钟的单元测试。

为什么单测时钟：流速公式（现实 1 秒 = 游戏 1 分钟）是 W2 居民作息和
LLM 调用频率的基础变量，必须锁死行为，防止后续改动破坏节奏。
"""

from world.clock import WorldClock, format_game_time, parse_game_time


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
