"""A* 寻路（4 方向，曼哈顿启发）。

设计说明（为什么这样设计）：
- 网格来自共享的 town_map.json（见 mapdata.py），服务端不依赖 Phaser。
- 80×50 小网格 + 每居民每天几次寻路，A* 性能完全过剩——重点是正确性：
  居民不能穿墙穿房（玩家盯着看，出戏成本很高）。
- 找不到路返回空列表，由调用方决定降级（原地待着），不抛异常。
"""

import heapq

from world.mapdata import COLS, ROWS, is_walkable


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _neighbors(tile: tuple[int, int]) -> list[tuple[int, int]]:
    col, row = tile
    candidates = [(col - 1, row), (col + 1, row), (col, row - 1), (col, row + 1)]
    return [
        (c, r)
        for c, r in candidates
        if 0 <= c < COLS and 0 <= r < ROWS and is_walkable(c, r)
    ]


def find_path(start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]]:
    """返回从 start 到 goal 的瓦片路径（不含起点，含终点）。找不到返回 []。"""
    if start == goal:
        return []
    if not is_walkable(*goal):
        return []

    frontier: list[tuple[int, tuple[int, int]]] = [(0, start)]
    came_from: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    cost_so_far: dict[tuple[int, int], int] = {start: 0}

    while frontier:
        _, current = heapq.heappop(frontier)
        if current == goal:
            break
        for nxt in _neighbors(current):
            new_cost = cost_so_far[current] + 1
            if nxt not in cost_so_far or new_cost < cost_so_far[nxt]:
                cost_so_far[nxt] = new_cost
                priority = new_cost + _manhattan(nxt, goal)
                heapq.heappush(frontier, (priority, nxt))
                came_from[nxt] = current

    if goal not in came_from:
        return []

    path: list[tuple[int, int]] = []
    cur: tuple[int, int] | None = goal
    while cur is not None and cur != start:
        path.append(cur)
        cur = came_from[cur]
    path.reverse()
    return path
