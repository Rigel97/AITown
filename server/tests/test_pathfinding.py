"""A* 寻路与计划条目选择的测试。

寻路用真实的 town_map.json（与前端渲染同源），锁定"居民不穿墙"这条
玩家可感知的正确性；计划条目选择是计划执行循环的核心分支逻辑。

坐标基于 mage3 地图（50×50）的站位点（见 world/locations.py）：
面包店门口 (17,29)、广场 (22,14)、花店 (4,19)、餐馆 (36,11)、
图书馆 (22,7)、杂货店 (10,9)、东南宅 (44,43)。
"""

from agents.planner import PlanEntry, current_plan_entry, plan_from_json
from world.mapdata import is_walkable
from world.pathfinding import find_path

# 面包店门口 (17,29) → 广场 (22,14)：斜穿镇中心
BAKERY = (17, 29)
PLAZA = (22, 14)
# 花店门口 (4,19) → 餐馆门口 (36,11)：横穿全图
FLOWER_SHOP = (4, 19)
RESTAURANT = (36, 11)


def test_path_short_hop() -> None:
    path = find_path(BAKERY, PLAZA)
    assert path, "面包店到广场应该有路"
    assert path[-1] == PLAZA
    assert len(path) <= 25  # 曼哈顿距离 20，绕路也不该太离谱


def test_path_never_crosses_walls() -> None:
    """全程每一格都必须可行走，且相邻步四连通。"""
    path = find_path(FLOWER_SHOP, RESTAURANT)
    assert path, "花店到餐馆应该有路"
    prev = FLOWER_SHOP
    for tile in path:
        assert is_walkable(*tile), f"路径踩到不可走的格子 {tile}"
        assert abs(tile[0] - prev[0]) + abs(tile[1] - prev[1]) == 1, (
            f"路径在 {tile} 处断开"
        )
        prev = tile


def test_all_location_spots_reachable() -> None:
    """所有地点站位点必须互相可达——mage3 的房屋内部是封闭孤岛，
    站位点若落在孤岛上，居民会被困死（2026-08-19 换图踩过的坑）。"""
    from world.locations import LOCATION_SPOTS

    anchors = [spots[0] for spots in LOCATION_SPOTS.values()]
    for start in anchors:
        for goal in anchors:
            if start == goal:
                continue
            path = find_path(start, goal)
            assert path, f"{start} → {goal} 不可达！站位点必须都在主连通区"


def test_path_same_point_and_unreachable() -> None:
    assert find_path(PLAZA, PLAZA) == []
    # 目标不可走（面包店屋内的 obj 障碍格）→ 空列表，不抛异常
    assert find_path(PLAZA, (17, 20)) == []
    # 目标可走但在封闭孤岛上（面包店屋内，被 obj 层围死）→ 同样空列表
    assert find_path(PLAZA, (14, 24)) == []


def test_current_plan_entry_picks_latest_past() -> None:
    plan = [
        PlanEntry("12:00", "餐馆", "吃午饭"),
        PlanEntry("07:00", "面包店", "开门"),
        PlanEntry("18:00", "餐馆", "喝一杯"),
    ]
    assert (
        current_plan_entry(plan, 6 * 60).location == "面包店"
    )  # 还没到第一条 → 第一条
    assert current_plan_entry(plan, 7 * 60).location == "面包店"
    assert current_plan_entry(plan, 13 * 60).location == "餐馆"
    assert current_plan_entry(plan, 13 * 60).action == "吃午饭"
    assert current_plan_entry(plan, 22 * 60).action == "喝一杯"


def test_current_plan_entry_empty() -> None:
    assert current_plan_entry([], 800) is None


def test_plan_from_json_roundtrip() -> None:
    text = '[{"time": "07:00", "location": "面包店", "action": "开门"}]'
    plan = plan_from_json(text)
    assert plan[0].location == "面包店"
    assert plan_from_json(None) == []
