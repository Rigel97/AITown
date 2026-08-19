// 地图导出脚本：把 mapData.ts 生成的小镇地图写成前后端共享的 JSON。
// 用法（在 client/ 下）：
//   npx tsc src/world/mapData.ts --outDir /tmp/mapout --module esnext --target es2020 --skipLibCheck --ignoreConfig
//   node scripts/export_map.mjs
// 服务端 world/mapdata.py 与前端 TownScene 共读产物 public/assets/town_map.json。
import { writeFileSync } from "node:fs";
import { buildTownMap, MAP_COLS, MAP_ROWS, WALKABLE_TILES } from "/tmp/mapout/mapData.js";

const out = {
  cols: MAP_COLS,
  rows: MAP_ROWS,
  tiles: buildTownMap(),
  walkable: WALKABLE_TILES,
};
writeFileSync(new URL("../public/assets/town_map.json", import.meta.url), JSON.stringify(out));
console.log(`exported ${MAP_COLS}x${MAP_ROWS} map -> public/assets/town_map.json`);
