"""世界状态存档：快照 JSON 读写收口（Phase 3 存档/读档）。

设计说明（为什么这样设计）：
- saves 表建库时就有（name/saved_at/game_time/world_state），本模块零 schema
  改动（AGENTS.md 保护区不动）；autosave 是单行覆盖语义——MVP 只有一个连续
  世界，未来要多存档位换 name 即可，AUTOINCREMENT id 天然保留历史可回溯。
- 存的是"运行态"：时钟/玩家坐标/居民运行时字段（位置/计划天/反思天/地点/动作）。
  residents 表仍是"定档"（人设+出生点，seed 数据），memories 与 chronicle 自带
  持久化——三类数据各归其位，读档 = 定档 + 记忆 + 快照的拼装，互不掺和。
- 瞬态不存：进行中的对话（≤6 回合，中断即散场是可接受的叙事断点）、
  冷却字典、节流时间戳、播报环形缓冲（编年史才是给人看的完整档案）。
  存它们只会让读档路径复杂、收益为零。
- 读档容错：无档/坏 JSON/非对象一律返回 None 走"新世界"路径，绝不炸启动——
  存档是增强不是依赖，损坏的存档不该带走整个游戏。
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from world.clock import MINUTES_PER_DAY, format_game_time

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[1] / "db" / "aitown.db"

# 存档槽名：单世界连续存档（覆盖式）。未来手动多存档位换 name 即可。
AUTOSAVE_NAME = "autosave"

# 快照格式版本：格式变更时递增，旧档读档直接拒绝走新世界（不做冒险迁移——
# 存档里只有一天以内的可丢运行态，重开损失的只是位置，记忆都在）
SAVE_VERSION = 1

# 存档写失败的异常类型（engine 捕获用，避免 engine 直接依赖 sqlite3）
SAVE_ERRORS = (OSError, sqlite3.Error)


def save_world(state: dict[str, Any], db_path: Path = DB_PATH) -> str:
    """把世界快照写入 autosave 槽（覆盖旧档）。返回快照对应的 game_time 标签。

    game_time 列由快照内的时钟算出（单一真相源，调用方不用重复传）。
    失败原样抛 OSError/sqlite3.Error，由调用方决定降级（存档是旁路）。
    """
    clock = state.get("clock", {})
    day = int(clock.get("day", 1))
    minutes = int(clock.get("minutes", 0))
    game_time = format_game_time((day - 1) * MINUTES_PER_DAY + minutes)
    payload = json.dumps(state, ensure_ascii=False)
    with sqlite3.connect(db_path) as conn:
        # 同一事务里先删后插：断电/中断要么留旧档要么留新档，不会出双胞胎
        conn.execute("DELETE FROM saves WHERE name = ?", (AUTOSAVE_NAME,))
        conn.execute(
            "INSERT INTO saves (name, game_time, world_state) VALUES (?, ?, ?)",
            (AUTOSAVE_NAME, game_time, payload),
        )
    return game_time


def load_world(db_path: Path = DB_PATH) -> dict[str, Any] | None:
    """读 autosave 槽最新快照；无档/坏档/非对象返回 None（走新世界路径）。"""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT world_state FROM saves WHERE name = ? ORDER BY id DESC LIMIT 1",
            (AUTOSAVE_NAME,),
        ).fetchone()
    if row is None:
        return None
    try:
        state = json.loads(row[0])
    except json.JSONDecodeError:
        logger.warning("存档 JSON 损坏，按新世界启动", exc_info=True)
        return None
    if not isinstance(state, dict):
        logger.warning("存档不是 JSON 对象（%r），按新世界启动", type(state).__name__)
        return None
    return state
