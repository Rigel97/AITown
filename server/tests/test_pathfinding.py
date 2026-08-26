"""A* 寻路与计划条目选择的测试。

寻路用真实的 town_map_v3.json（与前端渲染同源），锁定"居民不穿墙"这条
玩家可感知的正确性；计划条目选择是计划执行循环的核心分支逻辑。

坐标从 world/locations 的派生站位点取（转换管线产出、启动校验过全部
可走且与出生点连通）：青梧咖啡/九号酒馆/主街/镇花园是 v3 地图上跨度
最大的几组点位。
"""

from agents.planner import PlanEntry, current_plan_entry, plan_from_json
from world.locations import LOCATION_SPOTS
from world.mapdata import WALKABLE, is_walkable
from world.pathfinding import find_path

CAFE = LOCATION_SPOTS["青梧咖啡"][0]  # 咖啡馆内（西北）
TAVERN = LOCATION_SPOTS["九号酒馆"][0]  # 酒馆内（西）
STREET = LOCATION_SPOTS["主街"][0]  # 两店门前主街
GARDEN = LOCATION_SPOTS["镇花园"][0]  # 镇花园（东南角，最远点）


def test_path_short_hop() -> None:
    """咖啡馆 → 门口主街：出门即到。"""
    path = find_path(CAFE, STREET)
    assert path, "青梧咖啡到主街应该有路"
    assert path[-1] == STREET
    manhattan = abs(CAFE[0] - STREET[0]) + abs(CAFE[1] - STREET[1])
    assert len(path) <= manhattan + 20  # 绕门口的墙不算离谱


def test_path_never_crosses_walls() -> None:
    """全程每一格都必须可行走，且相邻步四连通（酒馆 → 东南角镇花园，横穿全镇）。"""
    path = find_path(TAVERN, GARDEN)
    assert path, "九号酒馆到镇花园应该有路"
    prev = TAVERN
    for tile in path:
        assert is_walkable(*tile), f"路径踩到不可走的格子 {tile}"
        assert abs(tile[0] - prev[0]) + abs(tile[1] - prev[1]) == 1, (
            f"路径在 {tile} 处断开"
        )
        prev = tile


def test_all_location_spots_reachable() -> None:
    """所有地点站位点必须互相可达——站位点若落在建筑足迹/孤岛上，
    居民会被困死（换图必踩的坑；转换管线也有一层校验，这里是回归锁）。"""
    anchors = [spots[0] for spots in LOCATION_SPOTS.values()]
    for start in anchors:
        for goal in anchors:
            if start == goal:
                continue
            path = find_path(start, goal)
            assert path, f"{start} → {goal} 不可达！站位点必须都在主连通区"


def test_all_spots_walkable() -> None:
    """每个地点的每个站位点都必须落在可走格（engine 按序号取模分配，
    第 2-5 个点不常被测试命中，这里全量锁）。"""
    for name, spots in LOCATION_SPOTS.items():
        for spot in spots:
            assert is_walkable(*spot), f"{name} 的站位点 {spot} 不可走"


def test_path_same_point_and_unreachable() -> None:
    assert find_path(STREET, STREET) == []
    # 目标不可走（地图上第一个阻挡格）→ 空列表，不抛异常
    blocked = next(
        (c, r) for r, row in enumerate(WALKABLE) for c, v in enumerate(row) if not v
    )
    assert find_path(STREET, blocked) == []
    # 目标越界 → 同样空列表，不抛异常
    assert find_path(STREET, (999, 999)) == []


def test_current_plan_entry_picks_latest_past() -> None:
    plan = [
        PlanEntry("12:00", "九号酒馆", "吃午饭"),
        PlanEntry("07:00", "青梧咖啡", "开门"),
        PlanEntry("18:00", "九号酒馆", "喝一杯"),
    ]
    assert (
        current_plan_entry(plan, 6 * 60).location == "青梧咖啡"
    )  # 还没到第一条 → 第一条
    assert current_plan_entry(plan, 7 * 60).location == "青梧咖啡"
    assert current_plan_entry(plan, 13 * 60).location == "九号酒馆"
    assert current_plan_entry(plan, 13 * 60).action == "吃午饭"
    assert current_plan_entry(plan, 22 * 60).action == "喝一杯"


def test_current_plan_entry_empty() -> None:
    assert current_plan_entry([], 800) is None


def test_plan_from_json_roundtrip() -> None:
    text = '[{"time": "07:00", "location": "主街", "action": "开门"}]'
    plan = plan_from_json(text)
    assert plan[0].location == "主街"
    assert plan_from_json(None) == []


def test_plan_from_json_filters_unknown_place() -> None:
    """旧图地名（v2 的面包店）在新白名单下应被过滤 → 降级默认日程。

    换图后 residents.daily_plan 里残留的旧地名走的就是这条路径。"""
    text = '[{"time": "07:00", "location": "面包店", "action": "开门"}]'
    plan = plan_from_json(text)
    assert plan[0].location in LOCATION_SPOTS  # 落在当前地图的合法地名上
