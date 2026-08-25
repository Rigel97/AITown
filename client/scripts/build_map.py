"""小镇地图 v2 生成器：40×40 紧凑温馨小镇（Serene Village / Modern Farm 素材）。

设计目标（为什么这样设计）：
- 40×40 = 旧图 64% 面积，8 个场所更紧凑 → 相遇密度高（"小镇活"的关键）
- 星形路网：主街横贯 + 三纵一横，所有场所门朝路，路径自然
- 中央广场 8×8 + 水井/公告牌 = 全镇聚集点（红姐餐馆为第二聚集点）
- 建筑用整Sprite（不再瓦片拼墙）：depth=y 排序让角色能走到树冠/屋顶后面
- walkable 由道具足迹推导，前后端共读同一 JSON（寻路权威不变）

运行：python3 client/scripts/build_map.py
输出：
- client/public/assets/town_map.json（v2 格式，含 props）
- 终端打印：locations.py 的 LOCATION_SPOTS 片段 + 校验报告
"""

import json
from collections import deque
from pathlib import Path

COLS, ROWS, T = 40, 40, 32
OUT = Path(__file__).resolve().parents[1] / "public" / "assets" / "town_map.json"

# 瓦片 id（与 build_assets.py 的 tiles_index.json 一致）
GRASS, GRASS2, GRASSV, DIRT, PATH, ROAD_TL, ROAD_TR, ROAD_BL, ROAD_BR, \
    WATER, SAND, PLAZA, DECK, FLOOR, WALL, DOOR = range(16)

# ── 道具定义：img → (视觉宽px, 阻挡足迹 宽×高 tile；0 = 不阻挡的贴花) ──
PROPS_SPEC = {
    "cottage_red": (112, 4, 2), "cottage_green": (112, 4, 2), "cottage_blue": (112, 4, 2),
    "cottage_hq": (140, 5, 2), "manor_red": (144, 5, 2), "manor_green": (144, 5, 2),
    "manor_blue": (144, 5, 2), "house2_a": (144, 5, 2), "house2_b": (144, 5, 2),
    "tower_red": (80, 3, 2), "hedge": (64, 2, 2), "well": (64, 2, 2),
    "stall": (96, 3, 1), "crate": (36, 1, 1), "crate_stack": (36, 1, 1),
    "signpost": (48, 1, 1), "rock": (48, 1, 1), "scarecrow": (80, 1, 1),
    "law_plot": (182, 6, 4),
    "oak_s": (96, 1, 1), "oak_m": (128, 2, 1), "oak_l": (160, 2, 1),
    "flowerbed": (28, 0, 0), "pebbles": (32, 0, 0),
}

# ── 布局（全部手定，确定性输出）──
# 建筑/大件：(img, 足迹左上col, 足迹左上row)；足迹尺寸查 PROPS_SPEC
PLACEMENTS = [
    # 北区（主街 rows 15-16 以北）
    ("manor_blue", 18, 10),    # 图书馆（正对中央纵路）
    ("cottage_blue", 4, 9),    # 杂货店
    ("cottage_hq", 24, 9),     # 景观老宅（花店旁）
    ("cottage_green", 31, 9),  # 花店
    # 西南区
    ("cottage_red", 3, 21),    # 面包店
    ("stall", 7, 23),          # 面包店门前早市摊
    ("crate", 7, 21),          # 面包店侧箱
    ("house2_a", 11, 29),      # 北宅
    # 东南区
    ("manor_red", 31, 21),     # 餐馆
    ("crate_stack", 30, 23),   # 餐馆侧叠箱
    ("house2_b", 25, 30),      # 东南宅
    ("tower_red", 22, 29),     # 广场南地标塔
    # 广场
    ("well", 19, 22),          # 水井（广场中心）
    ("signpost", 22, 23),      # 公告牌（老周据点，井东南侧）
    ("hedge", 16, 20), ("hedge", 22, 20),  # 广场北口两侧树篱
    ("rock", 16, 26), ("rock", 23, 26),
    # 菜园（阿茉供菜设定的公用菜园）
    ("law_plot", 32, 31),
    ("scarecrow", 37, 30),
]

# 树：边框环 + 角簇 + 散树（避开路/建筑，构建器校验冲突）
def trees():
    out = []
    for c in range(1, COLS - 1, 2):  # 上下边框（隔一棵放，树冠视觉上连成墙）
        out.append(("oak_m", c, 1))
        out.append(("oak_m", c, ROWS - 2))
    for r in range(3, ROWS - 3, 2):
        out.append(("oak_m", 1, r))
        out.append(("oak_m", COLS - 2, r))
    # 四角簇（西南角避水塘/菜园）
    for c, r in [(3, 3), (5, 3), (3, 5), (35, 3), (37, 3), (35, 5),
                 (3, 30), (36, 35)]:
        out.append(("oak_l" if (c + r) % 3 == 0 else "oak_s", c, r))
    # 散树（手工挑的安全点）
    for c, r in [(3, 17), (13, 18), (25, 17), (35, 18), (14, 25), (5, 27),
                 (27, 26), (35, 27), (11, 6), (28, 12), (16, 6), (33, 17)]:
        out.append(("oak_s", c, r))
    return out

PLACEMENTS += trees()

# 贴花（不阻挡）：花坛/碎石
DECALS = [
    ("flowerbed", 17, 27), ("flowerbed", 22, 27), ("flowerbed", 16, 25), ("flowerbed", 23, 25),
    ("flowerbed", 30, 11), ("flowerbed", 36, 11), ("flowerbed", 35, 10),
    ("pebbles", 10, 16), ("pebbles", 26, 16), ("pebbles", 12, 32), ("pebbles", 20, 31),
]

# ── 地面 ──
base = [[GRASS] * COLS for _ in range(ROWS)]
decor = [[-1] * COLS for _ in range(ROWS)]


def fill(layer, c0, r0, c1, r1, v):
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            layer[r][c] = v


# 主街（横贯）+ 纵路 + 南环
fill(base, 1, 15, COLS - 2, 16, PATH)
fill(base, 19, 17, 20, 33, PATH)   # 中央纵路（穿广场向南）
fill(base, 8, 17, 8, 31, PATH)     # 西纵路
fill(base, 30, 17, 30, 31, PATH)   # 东纵路
fill(base, 8, 32, 30, 32, PATH)    # 南横路
# 广场
fill(base, 16, 20, 23, 27, PLAZA)
# 餐馆前木平台
fill(base, 31, 23, 35, 24, DECK)
# 水塘（西南角）+ 沙滩环
fill(base, 3, 33, 8, 37, WATER)
for r in range(32, 39):
    for c in range(2, 10):
        if 3 <= c <= 8 and 33 <= r <= 37:
            continue
        if 0 <= r < ROWS and 0 <= c < COLS and base[r][c] in (GRASS, GRASS2):
            base[r][c] = SAND
# 草地变种（确定性撒点）
for r in range(ROWS):
    for c in range(COLS):
        if base[r][c] == GRASS:
            k = (c * 7 + r * 13) % 29
            if k == 0:
                base[r][c] = GRASS2
            elif k in (5, 11):
                decor[r][c] = GRASSV

# ── 阻挡推导 ──
walk = [[True] * COLS for _ in range(ROWS)]
for r in range(ROWS):
    for c in range(COLS):
        if base[r][c] == WATER:
            walk[r][c] = False

prop_records = []
conflicts = []
for img, pc, pr in PLACEMENTS:
    w, bw, bh = PROPS_SPEC[img]
    c0 = pc + (bw - 1) // 2 - (bw - 1)  # 足迹左 = pc（PLACEMENTS 直接给左上角）
    # 说明：PLACEMENTS 给的是足迹左上角 → 遮挡格 = [pc, pc+bw) × [pr, pr+bh)
    for r in range(pr, pr + bh):
        for c in range(pc, pc + bw):
            if 0 <= r < ROWS and 0 <= c < COLS:
                if not walk[r][c]:
                    conflicts.append(f"{img}@({pc},{pr}) 压到不可走格/重叠 ({c},{r})")
                walk[r][c] = False
    # 前端渲染锚点：足迹中列、底行
    anchor_col = pc + bw // 2
    anchor_row = pr + bh - 1
    prop_records.append({"img": img, "col": anchor_col, "row": anchor_row, "bw": bw, "bh": bh})

for img, pc, pr in DECALS:
    prop_records.append({"img": img, "col": pc, "row": pr, "bw": 0, "bh": 0})

# ── 站位点（8 地名 × 5 点；首点最贴门）──
LOCATION_SPOTS = {
    "图书馆": [(19, 14), (20, 14), (18, 14), (21, 14), (19, 13)],
    "面包店": [(4, 24), (5, 24), (3, 24), (6, 24), (4, 25)],
    "餐馆": [(32, 26), (33, 26), (31, 26), (34, 26), (32, 25)],
    "花店": [(32, 12), (33, 12), (31, 12), (34, 12), (33, 13)],
    "杂货店": [(5, 12), (6, 12), (4, 12), (7, 12), (5, 13)],
    "北宅": [(13, 32), (12, 32), (14, 32), (11, 32), (13, 33)],
    "东南宅": [(27, 32), (26, 32), (28, 32), (25, 32), (27, 33)],
    "广场": [(18, 25), (21, 25), (18, 21), (21, 21), (19, 26)],
}
SPAWN = (20, 28)

# ── 校验 ──
# 1. 站位点全部可走
for name, spots in LOCATION_SPOTS.items():
    for c, r in spots:
        if not (0 <= c < COLS and 0 <= r < ROWS and walk[r][c]):
            conflicts.append(f"站位点 {name}({c},{r}) 不可走")
if not walk[SPAWN[1]][SPAWN[0]]:
    conflicts.append(f"出生点 {SPAWN} 不可走")

# 2. BFS：出生点到全部站位点连通
seen = [[False] * COLS for _ in range(ROWS)]
q = deque([SPAWN])
seen[SPAWN[1]][SPAWN[0]] = True
while q:
    c, r = q.popleft()
    for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nc, nr = c + dc, r + dr
        if 0 <= nc < COLS and 0 <= nr < ROWS and walk[nr][nc] and not seen[nr][nc]:
            seen[nr][nc] = True
            q.append((nc, nr))
for name, spots in LOCATION_SPOTS.items():
    for c, r in spots:
        if not seen[r][c]:
            conflicts.append(f"站位点 {name}({c},{r}) 与出生点不连通")

if conflicts:
    print("!! 布局冲突：")
    for x in conflicts:
        print("   ", x)
    raise SystemExit(1)

# ── 输出 ──
data = {
    "version": 2,
    "cols": COLS, "rows": ROWS, "tileDim": T,
    "tileset": "assets/v2/tiles.png", "tilesetCols": 16,
    "bgLayers": [base, decor],
    "objLayers": [],
    "props": prop_records,
    "walkable": walk,
    "playerSpawn": {"col": SPAWN[0], "row": SPAWN[1]},
}
OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
walkable_count = sum(sum(1 for v in row if v) for row in walk)
print(f"✓ town_map.json 已生成：{COLS}×{ROWS}，可行走 {walkable_count}/{COLS*ROWS} 格")
print(f"  道具 {len(PLACEMENTS)} 件 + 贴花 {len(DECALS)} 件")
print("\n── locations.py 片段（抄录到 server/world/locations.py）──")
print("LOCATION_SPOTS: dict[str, list[tuple[int, int]]] = {")
for name, spots in LOCATION_SPOTS.items():
    print(f'    "{name}": {spots},')
print("}")
print("\n── seed.py 坐标（像素，col*32+16, row*32+16）──")
seed_base = {"baker_lin": (4, 24), "xiao_dou": (5, 24), "librarian_su": (19, 13),
             "florist_mo": (32, 12), "lao_zhou": (21, 21), "hong_jie": (32, 25),
             "lao_song": (5, 12)}
for rid, (c, r) in seed_base.items():
    print(f'  {rid}: "{c*32+16},{r*32+16}",')
