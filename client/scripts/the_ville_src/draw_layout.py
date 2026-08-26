"""画 the_ville 的房间布局 ASCII 图（140x100 → 每 2 格采样）。
输出到 layout.txt 方便阅读。
"""
import json
from collections import defaultdict

d = json.load(open("maze.json"))

sec_cells = defaultdict(set)
for t in d["tiles"]:
    addr = t.get("address", [])
    if len(addr) >= 2:
        x, y = t["coord"]
        sec_cells[addr[1]].add((x, y))

# 房间名太长，取 2 字符缩写
ABBR = {
    "主人房": "主", "花园": "园", "公共休息室": "休", "商店": "店", "浴室": "浴",
    "供应店": "供", "公园": "公", "咖啡馆": "咖", "教室": "教", "走廊": "廊",
    "酒吧": "酒", "图书馆": "书", "厨房": "厨", "男卫生间": "男", "女卫生间": "女",
}
def abbr(name):
    if name in ABBR:
        return ABBR[name]
    if "的房间" in name or "卧室" in name:
        return "床"  # 私人卧室统一标"床"
    if "浴室" in name:
        return "浴"
    return "·"

# 网格（原尺寸）
grid = {}
for name, cells in sec_cells.items():
    a = abbr(name)
    for (x, y) in cells:
        grid[(x, y)] = a

lines = []
header = "    " + "".join(str((x // 10) % 10) if x % 10 == 0 else " " for x in range(0, 140, 2))
lines.append(header)
for y in range(0, 100, 2):
    row = []
    for x in range(0, 140, 2):
        row.append(grid.get((x, y), "."))
    lines.append(f"{y:3d} " + "".join(row))

out = "\n".join(lines)
with open("layout.txt", "w") as f:
    f.write(out)
print(out)
print("\n图例: 床=私人卧室 主=主人房 园=花园 休=公共休息室 店=商店 浴=浴室")
print("      供=供应店 公=公园 咖=咖啡馆 教=教室 廊=走廊 酒=酒吧 书=图书馆 厨=厨房")
print("      男/女=卫生间 .=室外/无名区")
