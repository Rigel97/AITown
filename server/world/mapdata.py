"""小镇地图数据（服务端视图）：读取前后端共享的 town_map.json。

设计说明（为什么这样设计）：
- 2026-08-18 起地图底图换为 ai-town 的 mage3 城镇图（MIT 协议）：
  源数据在 client/scripts/aitown_map_src.json，转换管线
  `cd client && node scripts/convert_aitown_map.mjs` 产出 town_map.json。
  改布局 = 改源数据/重选点 → 重跑转换脚本。
- 服务端要校验移动合法性（寻路不穿墙），必须与前端渲染用同一份数据，
  所以共读一个 JSON：可行走网格由转换脚本预计算（obj 层全空 = 可走）。
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

_data = json.loads(MAP_JSON.read_text())
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
