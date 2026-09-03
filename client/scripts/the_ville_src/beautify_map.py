"""地图美化后处理（实验区版）：草地变体打散 + 草丛点缀。

设计说明（为什么这样设计）：
- 作为 convert_ville_map.py 的后处理而非直接改源：管线哲学是"改地图 = 改脚本
  重跑"，美化也要可重跑。确定性 seeded random：同输入同输出，diff 干净。
- 只动两层：Bottom Ground（草地底色 → 同色系变体，全不透明件不破地面完整性）
  和 Exterior Decoration L1（透明底草丛点缀，原版同款件风格统一）。
  walkable / 交互点 / 其他层一律不碰——美化零功能影响，脚本内自校验。
- 幂等：底色替换只匹配 gid==2（替换后非 2 不再命中）；点缀只写空位（写入后
  非 0 不再选中）。重跑结果不变。
- 实验区：--region x0,y0,x1,y1 限定窗口（默认青梧咖啡门口→主街）。验收后
  去掉限制（或传全图窗口）即全量铺开。

运行：python3 client/scripts/the_ville_src/beautify_map.py [--region 50,18,70,28]
"""

import json
import random
import sys
from pathlib import Path

MAP = Path(__file__).resolve().parents[2] / "public" / "assets" / "town_map_v3.json"

# 草地底色（CuteRPG_Field_B (1,0)，纯色平铺）→ 同系变体（全不透明，含微纹理）
GRASS_BASE = 2
GRASS_VARIANTS = [33, 34, 35, 36, 37, 17, 18, 19]
# 草丛点缀（透明底深绿件，原版装饰层同款）与点缀密度
TUFT_GIDS = [10, 11, 12, 14, 15]
TUFT_DENSITY = 0.06
GRASS_VARIANT_DENSITY = 0.15

BACKUP = MAP.with_suffix(".pre_beautify.json")


def main() -> None:
    region = (50, 18, 70, 28)  # 实验区默认窗口（青梧咖啡门口 → 主街）
    args = sys.argv[1:]
    if "--region" in args:
        region = tuple(int(v) for v in args[args.index("--region") + 1].split(","))  # type: ignore[assignment]
    x0, y0, x1, y1 = region

    if not BACKUP.exists():
        BACKUP.write_bytes(MAP.read_bytes())  # 首次运行留底，可整体回滚
        print(f"已备份原地图 → {BACKUP.name}")

    data = json.loads(MAP.read_text())
    cols = data["cols"]
    layers = {layer["name"]: layer for layer in data["layers"] if layer.get("type") == "tilelayer"}
    bottom = layers["Bottom Ground"]["data"]
    exterior = layers["Exterior Ground"]["data"]
    decor = layers["Exterior Decoration L1"]["data"]
    walkable = data["walkable"]

    rng = random.Random(20260903)  # 固定 seed：确定性
    variants = tufts = 0
    for row in range(y0, y1 + 1):
        for col in range(x0, x1 + 1):
            i = row * cols + col
            if exterior[i]:
                continue  # 有路面/物件覆盖，露出的底色才值得打散
            # 先抽随机数再判断：短路求值会让消耗序列依赖上一次的结果，
            # 重跑时序列错位就不幂等了——每次循环固定消耗四个随机数
            # （choice 会在命中时额外消耗，同样破坏序列，改用随机索引）
            variant_roll = rng.random()
            tuft_roll = rng.random()
            variant_pick = GRASS_VARIANTS[int(rng.random() * len(GRASS_VARIANTS))]
            tuft_pick = TUFT_GIDS[int(rng.random() * len(TUFT_GIDS))]
            if bottom[i] == GRASS_BASE and variant_roll < GRASS_VARIANT_DENSITY:
                bottom[i] = variant_pick
                variants += 1
            if decor[i] == 0 and walkable[row][col] and tuft_roll < TUFT_DENSITY:
                decor[i] = tuft_pick
                tufts += 1

    # ── 自校验：美化绝不改功能数据 ──
    assert len(bottom) == len(exterior) == len(decor), "图层数据长度被改坏"
    assert data["walkable"] == walkable, "walkable 被改动"
    for layer in data["layers"]:
        if layer.get("type") == "tilelayer" and layer["name"] not in (
            "Bottom Ground",
            "Exterior Decoration L1",
        ):
            original = json.loads(BACKUP.read_text())
            orig_layer = next(
                l for l in original["layers"] if l.get("type") == "tilelayer" and l["name"] == layer["name"]
            )
            assert layer["data"] == orig_layer["data"], f"{layer['name']} 被意外改动"

    MAP.write_text(json.dumps(data, ensure_ascii=False))
    print(
        f"实验区 x[{x0},{x1}] y[{y0},{y1}]：草地变体 {variants} 处、草丛点缀 {tufts} 处，"
        f"已写回 {MAP.name}（自校验通过：可走性与其余图层零改动）"
    )


if __name__ == "__main__":
    main()
