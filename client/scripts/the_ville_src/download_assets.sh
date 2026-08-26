#!/bin/bash
# 下载 the_ville 裁剪窗口用到的 13 张 tileset PNG + Apache-2.0 LICENSE
# 源：x-glacier/GenerativeAgentsCN（Apache-2.0，已核验）
set -e
BASE="https://raw.githubusercontent.com/x-glacier/GenerativeAgentsCN/main/generative_agents/frontend/static/assets/village/tilemap"
OUT="/Users/chenzhongshu/Downloads/my_project/AITown/client/public/assets/ville"
mkdir -p "$OUT"

FILES=(
  "CuteRPG_Field_B.png"
  "CuteRPG_Field_C.png"
  "CuteRPG_Village_B.png"
  "CuteRPG_Forest_B.png"
  "CuteRPG_Forest_C.png"
  "CuteRPG_Desert_B.png"
  "CuteRPG_Desert_C.png"
  "CuteRPG_Mountains_B.png"
  "Room_Builder_32x32.png"
  "interiors_pt1.png"
  "interiors_pt2.png"
  "interiors_pt3.png"
  "interiors_pt5.png"
)

for f in "${FILES[@]}"; do
  name=$(basename "$f")
  if [ -s "$OUT/$name" ]; then
    echo "跳过（已存在）: $name"
  else
    code=$(curl -sL --max-time 60 -o "$OUT/$name" -w "%{http_code}" "$BASE/$f")
    echo "$code $name ($(du -h "$OUT/$name" | cut -f1))"
  fi
done

curl -sL --max-time 20 -o "$OUT/LICENSE" "https://raw.githubusercontent.com/x-glacier/GenerativeAgentsCN/main/LICENSE"
echo "LICENSE 已下载"
ls -la "$OUT"
