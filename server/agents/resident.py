"""居民实体与数据访问。

设计说明（为什么这样设计）：
- Resident 是智能体循环的核心实体：人设（prompt_prefix）+ 状态（位置/动作）。
  W2 的感知→检索→计划→行动循环都围绕它展开。
- 数据访问收口在这一个文件：residents 表由 db/seed.py 写入、这里读出，
  路由层和 engine 都不直接拼 SQL。
"""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

DB_PATH = Path(__file__).resolve().parents[1] / "db" / "aitown.db"


@dataclass
class Resident:
    id: str
    name: str
    occupation: str
    prompt_prefix: str
    x: int
    y: int
    daily_plan: str | None = None  # JSON 字符串（当日计划），由 planner 写入

    def public(self) -> dict[str, object]:
        """广播给前端的精简视图——人设细节（prompt_prefix）不下发。"""
        return {"id": self.id, "name": self.name, "x": self.x, "y": self.y}


class _PlanEntryLike(Protocol):
    """结构化约束：save_daily_plan 接受任何有这三个字段的对象（如 planner.PlanEntry）。

    为什么用 Protocol 而不是直接 import PlanEntry：避免 agents 包内
    resident ↔ planner 的循环依赖。
    """

    time: str
    location: str
    action: str


def save_daily_plan(
    resident_id: str, plan: list[_PlanEntryLike], db_path: Path = DB_PATH
) -> None:
    """把当日计划序列化为 JSON 存入 residents.daily_plan。"""
    payload = json.dumps(
        [{"time": e.time, "location": e.location, "action": e.action} for e in plan],
        ensure_ascii=False,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE residents SET daily_plan = ? WHERE id = ?",
            (payload, resident_id),
        )


def load_residents(db_path: Path = DB_PATH) -> list[Resident]:
    """从 residents 表加载全部居民。current_location 为像素坐标 "x,y"。"""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, occupation, prompt_prefix, current_location, daily_plan "
            "FROM residents ORDER BY rowid"
        ).fetchall()
    residents: list[Resident] = []
    for rid, name, occupation, prefix, loc, plan in rows:
        x_str, y_str = (loc or "0,0").split(",")
        residents.append(
            Resident(
                id=rid,
                name=name,
                occupation=occupation,
                prompt_prefix=prefix,
                x=int(x_str),
                y=int(y_str),
                daily_plan=plan,
            )
        )
    return residents


# ---------- 小镇世界观（防幻觉） ----------

# 模块级缓存：世界观块必须逐字稳定（紧跟人设前缀，吃 Prompt 缓存），
# 居民名单从 DB 构建后按 id 排序，进程内不变。
_world_context_cache: dict[str, str] = {}


def world_context(db_path: Path = DB_PATH) -> str:
    """小镇世界观块：完整的镇民名单 + 场所清单 + 禁止编造约束。

    为什么需要：人设前缀只写了"我是谁、我认识谁"，没写"镇上只有这些人"——
    LLM 会顺着人设自由发挥（实测老宋编了个不存在的镇民要给人修椅子，
    2026-08-17 用户反馈）。把这个块固定在每个 prompt 的人设前缀之后，
    既补全世界观，又保持前缀逐字稳定、不破坏 Prompt 缓存。
    """
    key = str(db_path)
    if key in _world_context_cache:
        return _world_context_cache[key]

    from world.locations import LOCATION_SPOTS  # 延迟导入避免循环依赖

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name, occupation FROM residents ORDER BY id"
        ).fetchall()
    roster = "、".join(f"{name}（{occupation}）" for name, occupation in rows)
    places = "、".join(sorted(LOCATION_SPOTS.keys()))

    block = f"""【小镇】镇上只有这些居民：{roster}，以及最近刚搬来的新邻居（就是会来找你搭话的玩家；玩家不在场时，和你聊天的都是名单上的老街坊）。镇上的地方有：{places}；南边的小路通往县城（每周三县城商队来供货）。除了名单上的人，镇上没有其他居民——不要编造不存在的镇民。"""
    _world_context_cache[key] = block
    return block
