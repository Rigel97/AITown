"""记忆检索：近因 + 重要性 + 关键词 三要素加权 Top-K。

设计说明（为什么这样设计）：
- 对应 Smallville 检索三要素（近因/重要性/相关性）的 MVP 简化版：
  相关性用关键词命中率近似（词表见 store.py）——MiniMax 已确认无 embedding
  接口，且小镇实体有限，词表匹配的精度损失可接受（V2 换本地向量模型，
  memories.embedding 字段已预留）。
- 评分 = W_RECENCY·exp(-Δt/τ) + W_IMPORTANCE·(importance/10) + W_KEYWORD·命中率
  τ = 24 游戏小时：今天的事清晰，昨天的还记得，三天前除非很重要（重要性顶格）
  否则基本沉底。调参只改这里三个权重，不用动 SQL。
- SQL 只取候选（该居民最近 N 条），打分在 Python 内存做：每居民几百条的
  数据量，不值得把公式塞进 SQL，也好单测。
"""

import math
from pathlib import Path

from memory.store import DB_PATH, Memory, extract_keywords, get_recent_memories
from world.clock import parse_game_time

# 三要素权重（W3 联调时按"对话里该记起的没记起"案例调）
W_RECENCY = 1.0
W_IMPORTANCE = 1.0
W_KEYWORD = 1.0
# 近因衰减时间常数（游戏小时）：exp(-24/24) ≈ 0.37，一天前的普通记忆权重已不足四成
TAU_GAME_HOURS = 24.0


def score_memory(memory: Memory, query_keywords: list[str], now_minutes: int) -> float:
    """三要素加权评分。now_minutes 为当前游戏分钟数（world.clock.now_minutes）。"""
    age_hours = max(0, now_minutes - parse_game_time(memory.game_time)) / 60
    recency = math.exp(-age_hours / TAU_GAME_HOURS)
    importance = memory.importance / 10
    if query_keywords:
        hits = sum(1 for kw in query_keywords if kw in memory.keywords)
        keyword = hits / len(query_keywords)
    else:
        keyword = 0.0
    return W_RECENCY * recency + W_IMPORTANCE * importance + W_KEYWORD * keyword


def retrieve(
    resident_id: str,
    query: str,
    now_minutes: int,
    k: int = 5,
    db_path: Path = DB_PATH,
    candidate_limit: int = 100,
) -> list[Memory]:
    """以 query 为线索，从该居民记忆流中取 Top-K 相关记忆。"""
    candidates = get_recent_memories(
        resident_id, limit=candidate_limit, db_path=db_path
    )
    query_keywords = extract_keywords(query)
    ranked = sorted(
        candidates,
        key=lambda m: score_memory(m, query_keywords, now_minutes),
        reverse=True,
    )
    return ranked[:k]
