"""地名 → 站立点列表（瓦片坐标）。

设计说明：计划、记忆、对话里出现的都是中文地名，这里是地名到地图的唯一
映射。每个地点配多个站立点，engine 按居民序号分配不同站位——避免居民
聚到同一地点时完全重叠（玩家可感知）。

2026-08-26 起（v3 the_ville 地图）：站位点不再手抄——转换管线
convert_ville_map.py 从每个 sector 的可走格里确定性采样 5 个分散点
（另加室外 POI 主街）写进 town_map_v3.json 的 locations 字段，本文件
只做读取与结构转换。改地名/站位 = 改转换脚本重跑，这里零维护。
"""

from world.mapdata import MAP_DATA

# 地名 → [(col, row), ...]（转换管线已校验：全部可走且与出生点连通）
LOCATION_SPOTS: dict[str, list[tuple[int, int]]] = {
    name: [(int(c), int(r)) for c, r in spots]
    for name, spots in MAP_DATA["locations"].items()
}

if "主街" not in LOCATION_SPOTS:
    # planner 的默认降级日程固定去主街（见 agents/planner._default_plan），
    # 地名缺失会在运行期变成"未知地点"警告——启动期就拦下，给出修复指令
    raise RuntimeError(
        "地图数据缺少「主街」站位点（planner 默认日程依赖它）。"
        "请重跑 client/scripts/the_ville_src/convert_ville_map.py"
    )
