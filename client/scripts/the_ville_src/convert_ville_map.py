"""the_ville 裁剪转换管线（V3 Phase B 核心）：maze.json + tilemap.json → town_map_v3.json。

设计说明（为什么这样设计）：
- 单一源头：maze.json（碰撞 + sector 地址，原作者手工标注，权威）与 tilemap.json
  （Tiled 视觉图层）都不改动，所有转换在这里一次完成——改窗口/改映射 = 改本脚本重跑。
- 裁剪窗口 x[14,135] × y[6,40]（122×35）：左边界 14 是合租公寓外墙列（x=16 会切掉
  拉托亚/弗朗西斯科的房间），草案的 x[16,135] 经探测修正。
- sector 同名多处复用（"主人房"在全图出现 3 次）→ 必须先做 4-连通分量切分再命名，
  否则"咖啡馆楼上"和"酒吧后"会混成一个房间。
- gid 保留 Tiled 原值（含翻转标志位）+ tilesets 表带 firstgid，并补齐 layer
  width/height、tileset imagewidth/imageheight 等 Tiled JSON 标准字段（Phase C）——
  产物能直接被 Phaser 的 load.tilemapTiledJSON 解析（多 tileset 归属 + gid 翻转
  都是引擎原生行为），本脚本零二次映射。
- 站位点（Phase C）：每个 sector 从自己的可走格里确定性采样 5 个分散点，另加
  室外 POI "主街"（两店门前的横街，老版"广场"聚集点的对应物）。地名/站位从此
  也是地图数据的一部分——后端 locations.py / planner 白名单直接读，不再手抄。
- 家具交互点（Phase D）：maze.json 的 address 第 3 层就是 game_object 名
  （窗口内 239 个交互格 100% 覆盖，如 ["公寓","浴室","花洒"]）——连通簇按
  多数派物体名自动命名，再经 OBJECT_RENAME 翻译到 V3 世界观；每簇另派生
  一个相邻可走格作「使用点」（spot），后端据此做站位引导与细粒度感知。
- 输出坐标全部重定原点到裁剪窗口左上角（0-based），与现有 town_map.json 约定一致。

运行：python3 client/scripts/the_ville_src/convert_ville_map.py
输出：client/public/assets/town_map_v3.json + 终端校验报告
"""

import json
from collections import Counter, defaultdict, deque
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parent
OUT = SRC.parents[1] / "public" / "assets" / "town_map_v3.json"
PUBLIC = SRC.parents[1] / "public"

# ── 裁剪窗口（源地图坐标，闭区间）──
X0, X1, Y0, Y1 = 14, 135, 6, 40
COLS, ROWS = X1 - X0 + 1, Y1 - Y0 + 1
SRC_W = 140
GID_MASK = 0x1FFFFFFF  # 剥 Tiled 翻转标志（渲染层数据保留原始 flag，此处仅统计用）

# 渲染层（按 tilemap.json 中的顺序，剔除 blocks 元数据层）
META_LAYERS = {
    "Collisions", "Object Interaction Blocks", "Arena Blocks",
    "Sector Blocks", "World Blocks", "Spawning Blocks", "Special Blocks Registry",
}

# ── sector 重命名映射（草案《房间分配》拍板版）──
# 简单名：该 sector 在窗口内只有一个连通分量，直接改
RENAME_SIMPLE = {
    "咖啡馆": "青梧咖啡",
    "酒吧": "九号酒馆",
    "图书馆": "小镇图书馆",
    "教室": "教室",  # 草案：保留/改名待定 → 先保留
    "花园": "镇花园",
    "走廊": "走廊",
    "公共休息室": "合租公寓客厅",
    "厨房": "合租公寓厨房",
    # 五间卧室（草案分配 4 间，窗口内实际 5 间，多一间同样留空做剧情钩子）
    "拉托亚的房间": "周星星的房间",   # 合租 1 号（左上）
    "拉吉夫的房间": "李算的房间",     # 合租 2 号（中上）
    "阿比盖尔的房间": "高新的房间",   # 合租 3 号（右上）
    "海莉的房间": "空卧室",           # 合租 4 号（中下）
    "弗朗西斯科的房间": "空卧室 2",   # 多出的第 5 间（左下）
    # 浴室跟着卧室主人走
    "拉托亚的浴室": "周星星的浴室",
    "拉吉夫的浴室": "李算的浴室",
    "阿比盖尔的浴室": "高新的浴室",
    "海莉的浴室": "空卧室浴室",
    "弗朗西斯科的浴室": "空卧室 2 浴室",
}
# 主人房在窗口内实际有 5 个连通分量（草案只预期 2 个）——按源坐标质心 x 归属：
# x[53,59] 酒吧正楼上→慕容瑾；x[65,69] 酒吧与咖啡馆之间→郑巧（草案的东南独立小屋
# 在裁剪窗口外，改分这套中心位置的公寓）；x[72,80] 咖啡馆正楼上→沈青梧；
# x[86,90] 咖啡馆东邻→空套间（剧情钩子）；x[93,97] 图书馆旁→吴文（草案原定）。
MASTER_BEDROOM_RULES = [
    ("慕容瑾的房间", lambda cx, cy: cx < 62),
    ("郑巧的房间", lambda cx, cy: 62 <= cx < 71),
    ("沈青梧的房间", lambda cx, cy: 71 <= cx < 84),
    ("空套间", lambda cx, cy: 84 <= cx < 92),
    ("吴文的套间", lambda cx, cy: cx >= 92),
]

# ── 物体名翻译（the_ville 源名 → V3 世界观名）──
# 为什么不全保留源名：部分源名内嵌了旧世界建筑名（"咖啡馆柜台后面"/
# "宿舍花园"），而 sector 已改叫"青梧咖啡"/"镇花园"——感知文案里会出现
# "青梧咖啡的咖啡馆柜台后面"这种新旧世界缝合怪。翻译原则：去旧世界专名、
# 口语化（"烹饪区"→"灶台"）、贴合 V3 人设（"麦克风"→"驻唱台"，高新驻唱）。
OBJECT_RENAME = {
    "咖啡馆柜台后面": "咖啡柜台",
    "咖啡馆顾客座位": "咖啡馆座位",
    "钢琴": "钢琴",
    "吧台后面": "吧台",
    "酒吧顾客座位": "吧台座位",
    "麦克风": "驻唱台",
    "图书馆桌子": "阅读桌",
    "图书馆沙发": "阅览沙发",
    "书架": "书架",
    "黑板": "黑板",
    "教室讲台": "讲台",
    "教室学生座位": "课桌",
    "宿舍花园": "花园座椅",
    "公共休息室沙发": "客厅沙发",
    "公共休息室桌子": "客厅茶几",
    "厨房水槽": "水槽",
    "冰箱": "冰箱",
    "烹饪区": "灶台",
    "烤箱": "烤箱",
    "床": "床",
    "壁橱": "衣柜",
    "书桌": "书桌",
    "架子": "置物架",
    "电脑桌": "电脑桌",
    "画架": "画架",
    "吉他": "吉他",
    "花洒": "淋浴",
    "浴室洗手池": "洗手池",
    "厕所": "马桶",
}
# 浴室三件套（淋浴/洗手池/马桶）在源数据里常常相邻连通成簇——多数派命名会
# 随机偏向某一件，统一叫"淋浴区"更符合感知语义（"在浴室的淋浴区"）。
BATHROOM_OBJECTS = {"淋浴", "洗手池", "马桶"}
BATHROOM_MIX_NAME = "淋浴区"

# 玩家出生点目标（重定原点后坐标）：主街 y=23 上、酒吧与咖啡馆之间的居中位置。
# maze 的 Spawning Blocks 是 16 个原居民的卧室出生点，不适合玩家——玩家该出生在街上。
SPAWN_TARGET = (61, 23)

# 室外 POI 窗口（重定原点后坐标）：两店门前的横街 y[23,25]，白天人流走廊，
# 也是傍晚自发聚集的场所（对应老版地图的"广场"）。
STREET_WINDOW = (52, 23, 70, 25)
STREET_NAME = "主街"
# 每个地点的站位点数：engine 按居民序号 index % len(spots) 分配
SPOTS_PER_LOCATION = 5


def load():
    maze = json.load(open(SRC / "maze.json"))
    tiled = json.load(open(SRC / "tilemap.json"))
    return maze, tiled


def crop_layers(tiled):
    """裁剪渲染图层，保留原始 gid（含翻转 flag）；tileset 补齐 Tiled 标准字段。"""
    tilesets = sorted(tiled["tilesets"], key=lambda ts: ts["firstgid"])
    used_names = set()
    layers = []
    for lay in tiled["layers"]:
        if lay.get("type") != "tilelayer" or lay["name"].strip() in META_LAYERS:
            continue
        data = []
        for y in range(Y0, Y1 + 1):
            row_start = y * SRC_W
            data.extend(lay["data"][row_start + X0: row_start + X1 + 1])
        for raw in data:
            gid = raw & GID_MASK
            if gid:
                for ts in tilesets:
                    if gid >= ts["firstgid"]:
                        owner = ts["name"]
                    else:
                        break
                used_names.add(owner)
        # width/height/type 是 Tiled JSON 必需字段（Phaser 解析层尺寸用）
        layers.append({
            "name": lay["name"].strip(),
            "type": "tilelayer",
            "width": COLS,
            "height": ROWS,
            "data": data,
        })
    out_tilesets = []
    for ts in tilesets:
        if ts["name"] not in used_names:
            continue
        image = f"assets/ville/{Path(ts['image']).name}"
        # 声明真实 PNG 尺寸（个别素材有半行余量，如 interiors_pt3 高 10032 ≠
        # tilecount 推算的 10016——按真实尺寸声明，gid→格的行列换算只依赖 columns）
        with Image.open(PUBLIC / image) as img:
            w, h = img.size
        out_tilesets.append({
            "name": ts["name"],
            "firstgid": ts["firstgid"],
            "columns": ts["columns"],
            "tilecount": ts["tilecount"],
            "tilewidth": 32,
            "tileheight": 32,
            "margin": 0,
            "spacing": 0,
            "imagewidth": w,
            "imageheight": h,
            "image": image,
        })
    return layers, out_tilesets


def build_walkable(maze):
    """maze.json collision 为权威；未覆盖的格默认可走并计数告警。"""
    collision = {}
    for t in maze["tiles"]:
        x, y = t["coord"]
        collision[(x, y)] = bool(t.get("collision", False))
    missing = 0
    walk = []
    for y in range(Y0, Y1 + 1):
        row = []
        for x in range(X0, X1 + 1):
            if (x, y) in collision:
                row.append(not collision[(x, y)])
            else:
                row.append(True)
                missing += 1
        walk.append(row)
    return walk, missing


def connected_components(cells):
    """4-连通分量切分。cells: set[(x,y)] → list[set]"""
    rest = set(cells)
    comps = []
    while rest:
        seed = rest.pop()
        comp = {seed}
        q = deque([seed])
        while q:
            x, y = q.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = (x + dx, y + dy)
                if nb in rest:
                    rest.remove(nb)
                    comp.add(nb)
                    q.append(nb)
        comps.append(comp)
    return comps


def build_sectors(maze):
    """窗口内 sector → 连通分量 → 重命名（坐标重定原点）。"""
    sec_cells = defaultdict(set)
    for t in maze["tiles"]:
        addr = t.get("address", [])
        if len(addr) >= 2:
            x, y = t["coord"]
            if X0 <= x <= X1 and Y0 <= y <= Y1:
                sec_cells[addr[1]].add((x, y))

    sectors = []
    unnamed_idx = 0
    for src_name, cells in sorted(sec_cells.items()):
        comps = connected_components(cells)
        # 大分量在前，小碎片（<4 格）视作边角余料丢弃
        comps = [c for c in sorted(comps, key=len, reverse=True) if len(c) >= 4]
        for comp in comps:
            xs = [c[0] for c in comp]
            ys = [c[1] for c in comp]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            if src_name == "主人房":
                for new_name, rule in MASTER_BEDROOM_RULES:
                    if rule(cx, cy):
                        name = new_name
                        break
            elif src_name in RENAME_SIMPLE:
                name = RENAME_SIMPLE[src_name]
                if len(comps) > 1:
                    # 同名 sector 多分量：按质心从左到右编号
                    idx = sorted(comps, key=lambda c: sum(p[0] for p in c) / len(c)).index(comp)
                    name = f"{RENAME_SIMPLE[src_name]} {idx + 1}"
            elif src_name == "浴室":
                name = "浴室"  # 占位，下面按最近的主人房归属
            else:
                unnamed_idx += 1
                name = f"{src_name}"
            sectors.append({
                "name": name,
                "source": src_name,
                "bbox": [min(xs) - X0, min(ys) - Y0, max(xs) - X0, max(ys) - Y0],
                "centroid": [round(cx - X0, 1), round(cy - Y0, 1)],
                "_src_centroid": [cx, cy],
                "cells": sorted([[x - X0, y - Y0] for x, y in comp]),
            })

    # 通用浴室 → 归属最近的主人房套间，命名「<主人>的浴室」
    masters = [s for s in sectors if s["source"] == "主人房"]
    for sec in sectors:
        if sec["source"] == "浴室":
            cx, cy = sec["_src_centroid"]
            owner = min(masters, key=lambda m: (m["_src_centroid"][0] - cx) ** 2 + (m["_src_centroid"][1] - cy) ** 2)
            # 「郑巧的房间的浴室」太绕口——剥掉「的房间/的套间」再拼
            short = owner["name"].replace("的房间", "").replace("的套间", "")
            sec["name"] = f"{short}的浴室"
    for sec in sectors:
        del sec["_src_centroid"]
    # 重名检查（sector 名会被后端当地名用，必须唯一）
    names = [s["name"] for s in sectors]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise SystemExit(f"!! sector 重名: {dup}")
    return sectors


def build_interaction_blocks(maze, tiled, sectors):
    """Object Interaction Blocks 层 → 连通簇 → 多数派物体名 → 挂到重叠最多的 sector。

    命名规则（确定性）：
    1. 簇内格的 address[2]（game_object 名）经 OBJECT_RENAME 翻译后取多数派；
       平票取 Unicode 码点最小者（min(key=(-count, name))）。
    2. 浴室三件套混合簇 → "淋浴区"。
    3. 完全没有物体名的簇（理论上不出现，窗口内实测 100% 覆盖）保留 None，
       main() 里会告警。
    """
    obj_at = {}
    for t in maze["tiles"]:
        addr = t.get("address", [])
        if len(addr) >= 3:
            obj_at[tuple(t["coord"])] = OBJECT_RENAME.get(addr[2], addr[2])
    lay = next(l for l in tiled["layers"] if l["name"] == "Object Interaction Blocks")
    cells = set()
    for y in range(Y0, Y1 + 1):
        for x in range(X0, X1 + 1):
            if (lay["data"][y * SRC_W + x] & GID_MASK) != 0:
                cells.add((x, y))
    blocks = []
    for i, comp in enumerate(connected_components(cells)):
        # 找重叠最多的 sector
        best, best_n = None, 0
        rebased = {(x - X0, y - Y0) for x, y in comp}
        for sec in sectors:
            n = len(rebased & {(c[0], c[1]) for c in sec["cells"]})
            if n > best_n:
                best, best_n = sec["name"], n
        names = Counter(obj_at[c] for c in comp if c in obj_at)
        if not names:
            name = None
        elif len(names) > 1 and set(names) <= BATHROOM_OBJECTS:
            name = BATHROOM_MIX_NAME
        else:
            name = min(names, key=lambda n: (-names[n], n))
        blocks.append({
            "id": f"ib{i:02d}",
            "name": name,
            "sector": best,
            "cells": sorted([list(c) for c in rebased]),
        })
    return blocks


def attach_block_spots(blocks, walk):
    """每个交互簇派生「使用点」：离簇最近的可走格（BFS，可穿阻挡格扩散）。

    为什么需要：站位引导（engine 按 action 把居民领到家具旁）和细粒度感知
    （"在书架旁"）都需要一个居民真能站的位置。确定性：种子按行列序入队、
    4 邻域固定方向序，同一输入永远同一输出。孤岛已在 close_islands 封死，
    BFS 找到的可走格必在主连通区。
    """
    for b in blocks:
        seeds = sorted((c[1], c[0]) for c in b["cells"])  # (row, col) 行列序
        # 交互格本身可走（个别可踩家具）时，最近的那个种子就是使用点
        spot = next(([c, r] for r, c in seeds if walk[r][c]), None)
        q = deque(seeds)
        seen = {(c, r) for r, c in seeds}
        while q and spot is None:
            r, c = q.popleft()
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = r + dr, c + dc
                if not (0 <= nr < ROWS and 0 <= nc < COLS) or (nc, nr) in seen:
                    continue
                seen.add((nc, nr))
                if walk[nr][nc]:
                    spot = [nc, nr]
                    break
                q.append((nr, nc))
        b["spot"] = spot


def _spread_spots(cells: list[tuple[int, int]], n: int) -> list[tuple[int, int]]:
    """确定性采样 n 个相互分散的点：种子 = 最接近质心的格，之后贪心选"离已选
    集合最远"的格（farthest-point sampling）。同一输入永远同一输出。"""
    if not cells:
        return []
    cx = sum(c[0] for c in cells) / len(cells)
    cy = sum(c[1] for c in cells) / len(cells)
    first = min(cells, key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)
    chosen = [first]
    want = min(n, len(cells))
    while len(chosen) < want:
        best, best_d = None, -1.0
        for c in cells:
            if c in chosen:
                continue
            d = min((c[0] - s[0]) ** 2 + (c[1] - s[1]) ** 2 for s in chosen)
            if d > best_d:
                best, best_d = c, d
        chosen.append(best)  # type: ignore[arg-type]
    return sorted(chosen)


def build_locations(sectors, walk):
    """地名 → 站位点列表：每个 sector 的可走格里采样 5 点 + 室外 POI 主街。

    站位点只从「可走且属主连通区」的格里选——validate 之后的 walk 已封孤岛，
    但主连通区校验仍再做一遍（双保险，防未来改 close_islands 顺序）。"""
    locations: dict[str, list[list[int]]] = {}
    for sec in sectors:
        cells = [(c[0], c[1]) for c in sec["cells"] if walk[c[1]][c[0]]]
        if not cells:
            raise SystemExit(f"!! sector {sec['name']} 没有可走格，无法派生站位点")
        spots = _spread_spots(cells, SPOTS_PER_LOCATION)
        locations[sec["name"]] = [[x, y] for x, y in spots]
    # 室外 POI：主街（两店门前横街）
    x0, y0, x1, y1 = STREET_WINDOW
    street = [
        (x, y)
        for y in range(y0, y1 + 1)
        for x in range(x0, x1 + 1)
        if walk[y][x]
    ]
    if len(street) < SPOTS_PER_LOCATION:
        raise SystemExit(f"!! 主街窗口 {STREET_WINDOW} 可走格不足: {len(street)}")
    locations[STREET_NAME] = [[x, y] for x, y in _spread_spots(street, SPOTS_PER_LOCATION)]
    return locations


def pick_spawn(walk, sectors):
    """离 SPAWN_TARGET 最近的「可走 + 不属于任何 sector」的室外格。"""
    sec_cells = {(c[0], c[1]) for sec in sectors for c in sec["cells"]}
    tx, ty = SPAWN_TARGET
    best = None
    for y in range(ROWS):
        for x in range(COLS):
            if walk[y][x] and (x, y) not in sec_cells:
                d = (x - tx) ** 2 + (y - ty) ** 2
                if best is None or d < best[0]:
                    best = (d, x, y)
    if best is None:
        raise SystemExit("!! 找不到可用的室外出生点")
    return {"col": best[1], "row": best[2]}


def close_islands(walk, spawn):
    """从出生点 BFS 主连通区，不在主连通区的可走格一律封死（裁剪边缘切断的
    北部草地带/围栏后院等视觉装饰区，角色本就到不了，封死让寻路数据干净）。"""
    seen = [[False] * COLS for _ in range(ROWS)]
    q = deque([(spawn["col"], spawn["row"])])
    seen[spawn["row"]][spawn["col"]] = True
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < COLS and 0 <= ny < ROWS and walk[ny][nx] and not seen[ny][nx]:
                seen[ny][nx] = True
                q.append((nx, ny))
    closed = 0
    for y in range(ROWS):
        for x in range(COLS):
            if walk[y][x] and not seen[y][x]:
                walk[y][x] = False
                closed += 1
    return closed


def validate(walk, sectors, spawn):
    """孤岛封闭后校验：各 sector 的可走格必须全部仍在主连通区。"""
    seen = [[False] * COLS for _ in range(ROWS)]
    q = deque([(spawn["col"], spawn["row"])])
    seen[spawn["row"]][spawn["col"]] = True
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < COLS and 0 <= ny < ROWS and walk[ny][nx] and not seen[ny][nx]:
                seen[ny][nx] = True
                q.append((nx, ny))
    total_walk = sum(sum(1 for v in row if v) for row in walk)
    reached = sum(sum(1 for v in row if v) for row in seen)
    problems = []
    for sec in sectors:
        walk_cells = [(x, y) for x, y in (tuple(c) for c in sec["cells"]) if walk[y][x]]
        if not walk_cells:
            problems.append(f"sector {sec['name']} 无可走格")
            continue
        unreached = [(x, y) for x, y in walk_cells if not seen[y][x]]
        if unreached:
            problems.append(
                f"sector {sec['name']} {len(unreached)}/{len(walk_cells)} 可走格不在主连通区"
            )
    return total_walk, reached, seen, problems


def main():
    maze, tiled = load()
    layers, tilesets = crop_layers(tiled)
    walk, missing = build_walkable(maze)
    sectors = build_sectors(maze)
    blocks = build_interaction_blocks(maze, tiled, sectors)
    spawn = pick_spawn(walk, sectors)
    closed = close_islands(walk, spawn)
    total_walk, reached, seen, problems = validate(walk, sectors, spawn)
    locations = build_locations(sectors, walk)
    attach_block_spots(blocks, walk)

    # 站位点连通性终检：所有点必须可走且在主连通区（换图必踩的坑：站位落在
    # 建筑足迹/孤岛上，居民会被困死）
    for name, spots in locations.items():
        for x, y in spots:
            if not walk[y][x] or not seen[y][x]:
                problems.append(f"站位点 {name} ({x},{y}) 不可走/不在主连通区")
    unnamed = [b["id"] for b in blocks if not b["name"]]
    if unnamed:
        problems.append(f"交互簇未命名: {unnamed}")
    bad_spot = [b["id"] for b in blocks if not b["spot"] or not walk[b["spot"][1]][b["spot"][0]]]
    if bad_spot:
        problems.append(f"交互簇使用点不可用: {bad_spot}")

    data = {
        # —— Tiled JSON 标准字段（Phaser load.tilemapTiledJSON 直接解析）——
        "orientation": "orthogonal",
        "renderorder": "right-down",
        "infinite": False,
        "tilewidth": 32,
        "tileheight": 32,
        "width": COLS,
        "height": ROWS,
        # —— 项目自有字段（version 是项目格式号；Tiled 解析器不依赖它）——
        "version": 3,
        "cols": COLS,
        "rows": ROWS,
        "tileDim": 32,
        "sourceWindow": {"x0": X0, "y0": Y0, "x1": X1, "y1": Y1},
        "tilesets": tilesets,
        "layers": layers,
        "walkable": walk,
        "sectors": sectors,
        "interactionBlocks": blocks,
        "locations": locations,
        "playerSpawn": spawn,
        "attribution": (
            "the_ville map & tiles: joonspk-research/generative_agents (Apache-2.0), "
            "via x-glacier/GenerativeAgentsCN (Apache-2.0). Cropped to window; see assets/ville/LICENSE."
        ),
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"✓ town_map_v3.json: {COLS}×{ROWS}（窗口 x[{X0},{X1}] y[{Y0},{Y1}]）")
    print(f"  渲染层 {len(layers)} 层，tileset {len(tilesets)} 个")
    print(f"  可行走 {total_walk}/{COLS * ROWS} 格（封掉孤岛 {closed} 格），主连通区覆盖 {reached}/{total_walk}")
    print(f"  maze 未覆盖格（默认可走）: {missing}")
    print(f"  出生点: ({spawn['col']},{spawn['row']})（主街室外格，目标 {SPAWN_TARGET}）")
    print(f"  地点 {len(locations)} 个（{len(sectors)} sector + 主街），每地最多 {SPOTS_PER_LOCATION} 站位点")
    print(f"  交互簇 {len(blocks)} 个（物体名 {len({b['name'] for b in blocks})} 种）：")
    by_sector = defaultdict(list)
    for b in blocks:
        by_sector[b["sector"]].append(b["name"])
    for sec in sorted(by_sector):
        print(f"    {sec}: {'、'.join(by_sector[sec])}")
    if problems:
        print("\n!! 连通性问题：")
        for p in problems:
            print("   ", p)
        raise SystemExit(1)
    print("\n✓ 全部 sector 可走格与站位点均在主连通区")


if __name__ == "__main__":
    main()
