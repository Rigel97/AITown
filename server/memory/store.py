"""记忆流写入与读取。

设计说明（为什么这样设计）：
- 一切皆记忆：对话/移动/事件/反思统一进 memories 一张表，检索接口统一，
  世界观一致性自然涌现。写入路径挂在行动循环的每一步（行动结果即记忆），
  而不是事后补录——"记得"是默认行为，不会漏。
- keywords 写入时生成：用词表匹配（居民名 + 地名）从 content 抽取。
  小镇实体是有限的（7 人 + 7 建筑 + 玩家），词表匹配比通用中文分词更准、
  完全确定性、零依赖；检索侧只做集合命中，不做 NLP。
- store 只管存取（含"取候选"），打分排序在 retrieve.py——职责分离，
  调权重不用碰 SQL。
- 时间排序用自增 id 而非 game_time 字符串（"day10" 字典序小于 "day2"）；
  id 单调递增 ≈ 时间序，因为游戏时间只朝前走。
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DB_PATH = Path(__file__).resolve().parents[1] / "db" / "aitown.db"

MemoryType = Literal["observation", "dialogue", "event", "reflection"]

# 重要记忆门槛：玩家互动（importance 6）与反思（8）必须穿透近因窗口被检索到。
# 自己的日常回复/对话摘要（5）与计划（3）不进来——防刷屏。
# 为什么重要：retrieve 只在"最近 N 条"里打分，玩家连续聊几天后早期互动会
# 永久沉底、再也想不起来——"居民记得玩家做过的事"是核心体验（2026-08-21
# 深检：实测每人 ~950 条记忆，100 条窗口只能覆盖最近 2-3 个游戏日）。
IMPORTANT_MEMORY_THRESHOLD = 6

# 关键词词表：小镇的全部实体。新居民/新建筑定档时必须同步进来，
# 否则相关内容检索不到（test_seed 里有关系网断言兜底人设，词表靠这份清单维护）。
DEFAULT_VOCABULARY = [
    # 居民 + 玩家
    "林师傅",
    "小豆子",
    "苏晚",
    "阿茉",
    "老周",
    "红姐",
    "老宋",
    "玩家",
    # 地点
    "面包店",
    "杂货店",
    "花店",
    "图书馆",
    "餐馆",
    "北宅",
    "东南宅",
    "广场",
    "公告牌",
    # 事物与事件
    "面包",
    "花",
    "书",
    "供货日",
]


@dataclass
class Memory:
    id: int
    resident_id: str
    game_time: str
    type: str
    content: str
    importance: int
    keywords: list[str]


def extract_keywords(content: str, vocabulary: list[str] | None = None) -> list[str]:
    """用词表从文本里抽取关键词（子串命中）。"""
    vocab = vocabulary if vocabulary is not None else DEFAULT_VOCABULARY
    return [word for word in vocab if word in content]


def add_memory(
    resident_id: str,
    game_time: str,
    type: MemoryType,
    content: str,
    importance: int,
    db_path: Path = DB_PATH,
) -> int:
    """写入一条记忆，返回记忆 id。importance 取值 1–10。"""
    if not 1 <= importance <= 10:
        raise ValueError(f"importance 必须在 1-10 之间，收到 {importance}")
    keywords = " ".join(extract_keywords(content))
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO memories (resident_id, game_time, type, content, importance, keywords)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (resident_id, game_time, type, content, importance, keywords),
        )
        return cursor.lastrowid or 0


def _row_to_memory(row: tuple) -> Memory:
    """SELECT 结果行 → Memory（三个查询共用的映射，收口一处）。"""
    return Memory(
        id=row[0],
        resident_id=row[1],
        game_time=row[2],
        type=row[3],
        content=row[4],
        importance=row[5],
        keywords=(row[6] or "").split(),
    )


def get_memories_of_day(
    resident_id: str,
    day: int,
    db_path: Path = DB_PATH,
) -> list[Memory]:
    """取该居民某一天（game_time 前缀 dayN-）的全部记忆，按时间正序。供每日反思用。"""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, resident_id, game_time, type, content, importance, keywords
            FROM memories
            WHERE resident_id = ? AND game_time LIKE ?
            ORDER BY id
            """,
            (resident_id, f"day{day}-%"),
        ).fetchall()
    return [_row_to_memory(row) for row in rows]


def get_recent_memories(
    resident_id: str,
    limit: int = 100,
    db_path: Path = DB_PATH,
) -> list[Memory]:
    """取该居民最近的 limit 条记忆（新的在前），作为检索候选集。"""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, resident_id, game_time, type, content, importance, keywords
            FROM memories
            WHERE resident_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (resident_id, limit),
        ).fetchall()
    return [_row_to_memory(row) for row in rows]


def get_important_memories(
    resident_id: str,
    min_importance: int = IMPORTANT_MEMORY_THRESHOLD,
    limit: int = 500,
    db_path: Path = DB_PATH,
) -> list[Memory]:
    """取高重要度记忆（不限近因窗口）——检索双通道之一。

    idx_memories_importance 索引现成，成本近零。limit 只是防御性上限：
    打分在内存做、Prompt 只取 Top-K，候选集大小不影响调用成本。
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, resident_id, game_time, type, content, importance, keywords
            FROM memories
            WHERE resident_id = ? AND importance >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (resident_id, min_importance, limit),
        ).fetchall()
    return [_row_to_memory(row) for row in rows]
