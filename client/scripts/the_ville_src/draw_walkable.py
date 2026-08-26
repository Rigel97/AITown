"""可视化 town_map_v3.json：可走格/主连通区/sector 分布 ASCII 图（每格 1 字符，122 宽）。"""
import json
from collections import deque
from pathlib import Path

d = json.load(open(Path(__file__).resolve().parents[2] / "public" / "assets" / "town_map_v3.json"))
COLS, ROWS = d["cols"], d["rows"]
walk = d["walkable"]
spawn = d["playerSpawn"]

# 主连通区
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

# sector 覆盖（取名字首字，撞名加编号）
sec_grid = {}
for i, sec in enumerate(d["sectors"]):
    ch = sec["name"][0]
    for x, y in sec["cells"]:
        sec_grid[(x, y)] = ch

lines = []
for y in range(ROWS):
    row = []
    for x in range(COLS):
        if (x, y) == (spawn["col"], spawn["row"]):
            row.append("出")
        elif not walk[y][x]:
            row.append("#")
        elif not seen[y][x]:
            row.append("?")  # 可走但不在主连通区
        elif (x, y) in sec_grid:
            row.append(sec_grid[(x, y)])
        else:
            row.append(".")
    lines.append("".join(row))

print("\n".join(lines))
print("\n图例: #=不可走 .=主连通区室外 ?=孤岛（可走但不连通） 出=出生点")
print("      首字=sector（沈/慕/周/李/高/空/青=青梧咖啡/九=九号酒馆/小=小镇图书馆/教/镇=镇花园/走=走廊/合=合租/浴）")

# 孤岛统计：按连通分量聚合
rest = {(x, y) for y in range(ROWS) for x in range(COLS) if walk[y][x] and not seen[y][x]}
islands = []
while rest:
    seed = rest.pop()
    comp = {seed}
    qq = deque([seed])
    while qq:
        x, y = qq.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (x + dx, y + dy)
            if nb in rest:
                rest.remove(nb)
                comp.add(nb)
                qq.append(nb)
    islands.append(comp)
islands.sort(key=len, reverse=True)
print(f"\n孤岛 {len(islands)} 个（前 10）:")
for comp in islands[:10]:
    xs = [c[0] for c in comp]
    ys = [c[1] for c in comp]
    print(f"  {len(comp)}格 bbox=x[{min(xs)},{max(xs)}] y[{min(ys)},{max(ys)}]")
