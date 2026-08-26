"""家具交互点（V3 Phase D）：地名之下、像素之上的一层世界语义。

为什么需要这个模块：
- sector（地点）只回答"在哪个房间"，居民站在房间正中央说"在图书馆看书"
  是含糊的——世界模型知道图书馆里有书架/阅读桌/阅览沙发，感知就该用上。
- 斯坦福 Generative Agents 的计划带 game_object 级地址（["图书馆","书架"]）；
  我们的 MVP 计划只有 (time, location, action)，这里用 action 文案与家具的
  语义匹配补上这一层，零 LLM 成本、纯确定性计算。

数据来源（单一源头）：town_map_v3.json 的 interactionBlocks——转换管线
convert_ville_map.py 从 maze.json 的物体级地址自动命名并派生「使用点」
（居民能站的位置）。本模块只做读取与两个查询：
- preferred_block(sector, action)：站位引导——计划 action 与哪个家具相关，
  engine 把居民领到那个家具的使用点旁；
- nearest_block(col, row, action)：细粒度感知——居民身边 2 格内最近的家具，
  广播给前端（走近提示"在书架旁看书"）并注入对话 prompt（"你在图书馆的
  书架旁"）。
"""

from dataclasses import dataclass

from world.mapdata import MAP_DATA, is_walkable

# 与引擎对话/相遇检测同几何（切比雪夫），保持"感知范围"与"交互范围"一致的量级
NEAR_BLOCK_RANGE_TILES = 2


@dataclass(frozen=True)
class InteractionBlock:
    """一个家具交互簇：名字 + 所属地点 + 使用点（瓦片坐标）。"""

    id: str
    name: str
    sector: str
    col: int
    row: int


def _load() -> list[InteractionBlock]:
    blocks: list[InteractionBlock] = []
    for b in MAP_DATA.get("interactionBlocks", []):
        name, spot = b.get("name"), b.get("spot")
        # 命名/使用点是 Phase D 转换管线补的字段；旧版地图文件没有——跳过
        # 并保持其余功能照常（感知降级回 sector 级，不是错误）
        if not name or not spot:
            continue
        blocks.append(
            InteractionBlock(
                id=str(b["id"]),
                name=str(name),
                sector=str(b["sector"]),
                col=int(spot[0]),
                row=int(spot[1]),
            )
        )
    return blocks


BLOCKS: list[InteractionBlock] = _load()

BLOCKS_BY_SECTOR: dict[str, list[InteractionBlock]] = {}
for _b in BLOCKS:
    BLOCKS_BY_SECTOR.setdefault(_b.sector, []).append(_b)

# 动作文案关键词 → 偏好物体名（文案与家具的语义桥）。
# 为什么需要：LLM 的 action 常写"看书/排练/备料"而不提物体名（"整理书架"
# 这类自带物体名的走精确匹配）。纯数据、确定性遍历，保持短小——只收高频
# 活动，宁缺毋滥（错配比缺失更伤感知可信度）。
ACTION_OBJECT_HINTS: dict[str, tuple[str, ...]] = {
    "咖啡": ("咖啡柜台",),
    "烘豆": ("咖啡柜台",),
    "备料": ("吧台", "咖啡柜台", "灶台"),
    "调酒": ("吧台",),
    "擦杯子": ("吧台",),
    "看书": ("书架", "阅读桌"),
    "读书": ("书架", "阅读桌"),
    "翻书": ("书架", "阅读桌"),
    "阅读": ("阅读桌", "书架"),
    "借书": ("书架",),
    "整理书": ("书架",),
    "唱": ("驻唱台",),
    "排练": ("驻唱台", "钢琴"),
    "写歌": ("驻唱台",),
    "弹": ("钢琴", "吉他"),
    "琴": ("钢琴",),
    "画": ("画架",),
    "下棋": ("客厅茶几", "阅读桌", "吧台座位"),
    "做饭": ("灶台",),
    "做菜": ("灶台",),
    "煮": ("灶台",),
    "烧水": ("水槽", "灶台"),
    "洗澡": ("淋浴", "淋浴区"),
    "睡": ("床",),
}


def preferred_block(sector: str, action: str) -> InteractionBlock | None:
    """该地点里与 action 最相关的交互簇（站位引导用）。确定性：id 序。

    匹配两级：① 物体名直接出现在 action 里（"整理书架"→书架，精确）；
    ② 关键词桥（"看书"→书架）。都不中返回 None（engine 回退普通站位点）。
    """
    blocks = BLOCKS_BY_SECTOR.get(sector)
    if not blocks or not action:
        return None
    for b in blocks:
        if b.name in action:
            return b
    for keyword, names in ACTION_OBJECT_HINTS.items():
        if keyword in action:
            for n in names:
                for b in blocks:
                    if b.name == n:
                        return b
    return None


def nearest_block(
    col: int, row: int, action: str = "", max_dist: int = NEAR_BLOCK_RANGE_TILES
) -> InteractionBlock | None:
    """(col,row) 切比雪夫 ≤ max_dist 内最相关的交互簇（细粒度感知用）。

    排序键 = (-action 匹配, 距离, id)：与 action 相关的家具优先于更近但不
    相关的——居民被站位引导领到书架旁时，感知必须说"在书架旁"而不是旁边
    1 格的冰箱。全不相关时取最近。
    """
    best_key: tuple[int, int, str] | None = None
    best: InteractionBlock | None = None
    for b in BLOCKS:
        dist = max(abs(col - b.col), abs(row - b.row))
        if dist > max_dist:
            continue
        matched = 1 if (action and b.name in action) else 0
        key = (-matched, dist, b.id)
        if best_key is None or key < best_key:
            best_key, best = key, b
    return best


def validate_blocks() -> list[str]:
    """启动/测试期数据校验：名字非空、使用点可走。返回问题清单（空=健康）。"""
    problems: list[str] = []
    for b in BLOCKS:
        if not b.name:
            problems.append(f"{b.id}: 名字为空")
        if not is_walkable(b.col, b.row):
            problems.append(f"{b.id}({b.name}): 使用点 ({b.col},{b.row}) 不可走")
    return problems
