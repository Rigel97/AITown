"""world/objects.py 的测试：家具交互点（V3 Phase D）。

数据完整性：105 个交互簇必须全部有名字、使用点可走且互相可达（站位引导
会把居民领到使用点，掉进孤岛 = 困死，与站位点同级别的回归锁）。
查询语义：preferred_block 两级匹配（物体名精确 > 关键词桥）与
nearest_block 的"action 相关优先于距离更近"排序键。
"""

from collections import deque

from world.locations import LOCATION_SPOTS
from world.mapdata import SPAWN_COL, SPAWN_ROW
from world.objects import (
    BLOCKS,
    BLOCKS_BY_SECTOR,
    nearest_block,
    preferred_block,
    validate_blocks,
)


def test_all_blocks_named_and_walkable() -> None:
    """转换管线的命名/使用点质量锁：无空名、无不可走使用点。"""
    assert len(BLOCKS) >= 100, f"交互簇应约 105 个，实际 {len(BLOCKS)}"
    assert validate_blocks() == []


def test_all_block_spots_in_main_region() -> None:
    """使用点必须与出生点连通（孤岛上的家具居民到不了，站位引导会困死人）。"""
    seen = {(SPAWN_COL, SPAWN_ROW)}
    q = deque(seen)
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            from world.mapdata import is_walkable

            nx, ny = x + dx, y + dy
            if (nx, ny) not in seen and is_walkable(nx, ny):
                seen.add((nx, ny))
                q.append((nx, ny))
    for b in BLOCKS:
        assert (b.col, b.row) in seen, f"{b.id}({b.name}) 使用点不在主连通区"


def test_key_sectors_have_expected_objects() -> None:
    """命名结果的世界观锚点：昼夜双聚集点 + 图书馆 + 花园的标志性家具。"""
    names = {b.name for b in BLOCKS_BY_SECTOR["青梧咖啡"]}
    assert {"咖啡柜台", "咖啡馆座位"} <= names
    assert "驻唱台" in {b.name for b in BLOCKS_BY_SECTOR["九号酒馆"]}
    assert "书架" in {b.name for b in BLOCKS_BY_SECTOR["小镇图书馆"]}
    assert "花园座椅" in {b.name for b in BLOCKS_BY_SECTOR["镇花园"]}


def test_preferred_block_exact_name_match() -> None:
    """物体名直接出现在 action 里 → 精确匹配（"整理书架"→书架）。"""
    blk = preferred_block("小镇图书馆", "把书架上的书摆整齐")
    assert blk is not None and blk.name == "书架"


def test_preferred_block_keyword_bridge() -> None:
    """action 只写活动不写物体 → 关键词桥（连续子串匹配）。"""
    blk = preferred_block("小镇图书馆", "看书消磨一下午")
    assert blk is not None and blk.name in ("书架", "阅读桌")
    blk = preferred_block("小镇图书馆", "泡杯茶慢慢读书")
    assert blk is not None and blk.name in ("书架", "阅读桌")
    blk = preferred_block("九号酒馆", "排练今晚要唱的新歌")
    assert blk is not None and blk.name == "驻唱台"


def test_preferred_block_no_match_or_unknown_sector() -> None:
    """不相关的 action / 未知地点 → None（engine 回退普通站位点）。"""
    assert preferred_block("小镇图书馆", "发呆") is None
    assert preferred_block("主街", "随便逛逛") is None
    assert preferred_block("不存在的地点", "看书") is None


def test_nearest_block_prefers_action_relevant_over_closer() -> None:
    """排序键 -matched 优先于 dist：被站位引导领到书架旁的居民，身边 1 格
    就算有更近的书桌，感知也必须报 action 相关的书架。"""
    shelf = next(b for b in BLOCKS_BY_SECTOR["小镇图书馆"] if b.name == "书架")
    blk = nearest_block(shelf.col, shelf.row, action="在书架前看书")
    assert blk is not None and blk.name == "书架"


def test_nearest_block_out_of_range() -> None:
    """范围外 → None（主街出生点离任何家具都远）。"""
    assert nearest_block(SPAWN_COL, SPAWN_ROW, max_dist=2) is None


def test_spot_guidance_reachable_from_street() -> None:
    """站位引导可达性：代表性家具使用点从主街都能走到（全量连通已由
    main-region 测试覆盖，这里抽公共聚集点的几个点做寻路级验证）。"""
    from world.pathfinding import find_path

    street = LOCATION_SPOTS["主街"][0]
    for sector, want in (("青梧咖啡", "咖啡柜台"), ("九号酒馆", "驻唱台")):
        blk = next(b for b in BLOCKS_BY_SECTOR[sector] if b.name == want)
        assert find_path(street, (blk.col, blk.row)), f"{sector}的{want}从主街不可达"
