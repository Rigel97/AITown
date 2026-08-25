"""小镇地图数据（服务端视图）：读取前后端共享的 town_map.json。

设计说明（为什么这样设计）：
- 2026-08-25 起地图换为 v2 温馨小镇（LimeZu Modern Exteriors / Serene
  Village 系素材，40×40）：生成器 client/scripts/build_map.py 产出
  town_map.json（地面瓦片 + props 道具 + 预计算的 walkable 网格）。
  改布局 = 改该脚本重跑，并同步 server/world/locations.py 站位点。
- 服务端要校验移动合法性（寻路不穿墙），必须与前端渲染用同一份数据，
  所以共读一个 JSON：可行走网格由生成脚本预计算（水面 + 道具阻挡足迹）。
- nearest_walkable：换图后旧坐标投射，防居民卡死在阻挡格。
"""

import json
from pathlib import Path

MAP_JSON = (
    Path(__file__).resolve().parents[2]
    / "client"
    / "public"
    / "assets"
    / "town_map.json"
)

try:
    _data = json.loads(MAP_JSON.read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError) as exc:
    # 模块级读取：缺失时原本抛裸 FileNotFoundError，排查体验差。
    # 换地图/新环境克隆后最常见的问题，给出生成指令（2026-08-21 深检加固）。
    raise RuntimeError(
        f"共享地图数据不可用（{MAP_JSON}）。请先运行 "
        "`python3 client/scripts/build_map.py` 生成后重启后端"
    ) from exc
COLS: int = _data["cols"]
ROWS: int = _data["rows"]
TILE_SIZE: int = _data["tileDim"]
# 行主序可行走网格：walkable[row][col] = True/False
WALKABLE: list[list[bool]] = _data["walkable"]
# 玩家出生点（瓦片坐标），由转换脚本锚定在主连通区
SPAWN_COL: int = _data["playerSpawn"]["col"]
SPAWN_ROW: int = _data["playerSpawn"]["row"]


def is_walkable(col: int, row: int) -> bool:
    return 0 <= col < COLS and 0 <= row < ROWS and WALKABLE[row][col]


def to_tile(px: float) -> int:
    return int(px) // TILE_SIZE


def to_pixel_center(tile: int) -> int:
    return tile * TILE_SIZE + TILE_SIZE // 2


def nearest_walkable(col: int, row: int, max_radius: int = 12) -> tuple[int, int] | None:
    """从 (col,row) 逐环外扩找最近可行走格；超 max_radius 环仍无则 None。

    用途：换地图后旧存档/旧 seed 坐标可能越界或落在建筑/水面——
    投射到最近可走格，而不是让居民卡在不可走点上寻路失败。
    """
    if is_walkable(col, row):
        return col, row
    for radius in range(1, max_radius + 1):
        cands: list[tuple[int, int]] = []
        for dc in range(-radius, radius + 1):  # 上下边
            cands.append((col + dc, row - radius))
            cands.append((col + dc, row + radius))
        for dr in range(-radius + 1, radius):  # 左右边（角已含）
            cands.append((col - radius, row + dr))
            cands.append((col + radius, row + dr))
        for c, r in cands:
            if is_walkable(c, r):
                return c, r
    return None
