"""把 town_map_v3.json 渲染成 preview.png，目视校验裁剪 + gid→tileset 映射。
标准 Tiled 规则：gid 高三位是翻转标志（水平/垂直/对角）。
"""
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
d = json.load(open(ROOT / "public" / "assets" / "town_map_v3.json"))
T = d["tileDim"]
COLS, ROWS = d["cols"], d["rows"]

FLIP_H = 0x80000000
FLIP_V = 0x40000000
FLIP_D = 0x20000000
GID_MASK = 0x1FFFFFFF

tilesets = sorted(d["tilesets"], key=lambda ts: ts["firstgid"])
images = {}
for ts in tilesets:
    img_path = ROOT / "public" / ts["image"]
    images[ts["name"]] = Image.open(img_path).convert("RGBA")


def tile_image(gid):
    for ts in reversed(tilesets):  # 从大到小找 firstgid <= gid 的 tileset
        if gid >= ts["firstgid"]:
            local = gid - ts["firstgid"]
            img = images[ts["name"]]
            tx = (local % ts["columns"]) * T
            ty = (local // ts["columns"]) * T
            return img.crop((tx, ty, tx + T, ty + T))
    return None


canvas = Image.new("RGBA", (COLS * T, ROWS * T), (20, 20, 24, 255))
for lay in d["layers"]:
    for i, raw in enumerate(lay["data"]):
        if raw == 0:
            continue
        gid = raw & GID_MASK
        tile = tile_image(gid)
        if tile is None:
            continue
        # Tiled 翻转：先对角再水平/垂直
        if raw & FLIP_D:
            tile = tile.transpose(Image.ROTATE_90).transpose(Image.FLIP_LEFT_RIGHT)
        if raw & FLIP_H:
            tile = tile.transpose(Image.FLIP_LEFT_RIGHT)
        if raw & FLIP_V:
            tile = tile.transpose(Image.FLIP_TOP_BOTTOM)
        x, y = (i % COLS) * T, (i // COLS) * T
        canvas.alpha_composite(tile, (x, y))

out = Path(__file__).resolve().parent / "preview.png"
canvas.save(out)
print(f"✓ {out}  ({canvas.width}×{canvas.height})")
