// ai-town 地图转换脚本：把 ai-town 的地图数据（bgtiles/objmap，列主序 [x][y]，-1=空）
// 转成我们前后端共享的 town_map.json（行主序 [row][col]，含预计算的可行走网格）。
//
// 用法（在 client/ 下）：node scripts/convert_aitown_map.mjs
// 源数据：scripts/aitown_map_src.json（来自 ai-town 的 data 格式，MIT 协议）
// 产物：public/assets/town_map.json（前端渲染 + 服务端寻路共读，保持单一数据源）
//
// 同时校验 LOCATION_CANDIDATES（地点站位点候选）是否落在可行走格上，
// 落在障碍上会自动螺旋搜索最近可走格并打印最终坐标——把打印结果抄进
// server/world/locations.py。
import { readFileSync, writeFileSync } from "node:fs";

const src = JSON.parse(readFileSync(new URL("./aitown_map_src.json", import.meta.url), "utf8"));

// ai-town 格式：layer[x][y]（列主序），tile id 0 起，-1 = 空
const bgLayers = src.bgtiles;
const objLayers = src.objmap;
const TILE_DIM = src.tiledim;
const TILESET_COLS = src.tilesetpxw / TILE_DIM;
const TILESET_ROWS = (src.tilesetpxh ?? 1408) / TILE_DIM; // magecity 44 行（png 1450px 有半行余量，不完整行不可用）
const MAX_TILE_ID = TILESET_COLS * TILESET_ROWS - 1; // 351
const COLS = bgLayers[0].length;
const ROWS = bgLayers[0][0].length;

// 清洗：mage3 源数据有 4 个拼接坏 id（如 251259，assettool 生成缺陷，全在地图最底行）。
// 超出瓦片集范围的 id 若不清洗，前端渲染取图会越界、寻路碰撞也会误判。
const sanitize = (tid) => (tid >= 0 && tid <= MAX_TILE_ID ? tid : -1);

// 转置为行主序（顺带清洗坏 id）
const toRowMajor = (layers) =>
  layers.map((layer) =>
    Array.from({ length: ROWS }, (_, r) =>
      Array.from({ length: COLS }, (_, c) => sanitize(layer[c][r])),
    ),
  );

// 可行走 = 所有 obj 层该格均为空（-1）。
// 注意：mage3 的房屋内部（被 obj 层完全围住）会形成封闭孤岛——居民/玩家都
// 进不去（ai-town 语义即如此），站位点必须选在最大连通区，见下方候选表。
const walkable = Array.from({ length: ROWS }, (_, r) =>
  Array.from({ length: COLS }, (_, c) => objLayers.every((layer) => sanitize(layer[c][r]) === -1)),
);

// 地点站位点候选（均已在最大连通区核验）：[地名, col, row]
// 面包店/杂货店/东南宅的房屋内部是封闭孤岛，候选点必须选在门外主路：
//   面包店房屋 bbox≈(11..22,18..28)，门口选南侧横路 (16..18,29)
//   杂货店房屋 bbox≈(1..7,0..8)，门口选东南空地 (9..11,9)
//   东南宅房屋 bbox≈(41..46,36..41)，门口选南侧横路 (43..45,43)
const LOCATION_CANDIDATES = [
  ["图书馆", 22, 7], // 顶部大厅门口
  ["面包店", 17, 29], // 大屋南门横路（屋内不可进入）
  ["餐馆", 37, 12], // 东北市集桌椅区
  ["花店", 4, 19], // 西侧住宅二门口（带花园）
  ["杂货店", 10, 9], // 西北住宅一东南空地（屋内不可进入）
  ["北宅", 7, 34], // 西南雕像广场
  ["东南宅", 44, 43], // 东南住宅南门外横路（屋内不可进入）
  ["广场", 21, 15], // 中央雕像广场
];
// 玩家出生点：中央广场石板路上
const PLAYER_SPAWN = [25, 18];

function nearestWalkable(col, row) {
  if (walkable[row][col]) return [col, row];
  for (let radius = 1; radius <= 5; radius++) {
    for (let dr = -radius; dr <= radius; dr++) {
      for (let dc = -radius; dc <= radius; dc++) {
        if (Math.max(Math.abs(dr), Math.abs(dc)) !== radius) continue;
        const r = row + dr;
        const c = col + dc;
        if (r >= 0 && r < ROWS && c >= 0 && c < COLS && walkable[r][c]) return [c, r];
      }
    }
  }
  throw new Error(`no walkable tile near (${col},${row})`);
}

// 主连通区：从玩家出生点 BFS。房屋内部是被 obj 层围死的封闭孤岛（可走但进不去），
// 站位点若落在孤岛上，居民会被困死——所有点位必须锚定在出生点能走到的区域。
function componentOf(startCol, startRow) {
  const [sc, sr] = nearestWalkable(startCol, startRow);
  const comp = new Set([`${sc},${sr}`]);
  const queue = [[sc, sr]];
  while (queue.length) {
    const [c, r] = queue.pop();
    for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nc = c + dc;
      const nr = r + dr;
      const key = `${nc},${nr}`;
      if (nc >= 0 && nc < COLS && nr >= 0 && nr < ROWS && walkable[nr][nc] && !comp.has(key)) {
        comp.add(key);
        queue.push([nc, nr]);
      }
    }
  }
  return comp;
}

const [sc, sr] = nearestWalkable(...PLAYER_SPAWN);
const mainArea = componentOf(sc, sr);
console.log(`主连通区：${mainArea.size} 格（出生点 (${sc},${sr})）`);

console.log("== 地点站位点校验结果（抄进 server/world/locations.py）==");
const spots = {};
for (const [name, col, row] of LOCATION_CANDIDATES) {
  let [c, r] = nearestWalkable(col, row);
  if (!mainArea.has(`${c},${r}`)) {
    // 落在孤岛上：螺旋搜最近的主连通区格子（宁可离门口远一点，也不能困死居民）
    let fixed = null;
    for (let radius = 1; radius <= 8 && !fixed; radius++) {
      for (let dr = -radius; dr <= radius && !fixed; dr++) {
        for (let dc = -radius; dc <= radius && !fixed; dc++) {
          if (Math.max(Math.abs(dr), Math.abs(dc)) !== radius) continue;
          const rr = row + dr;
          const cc = col + dc;
          if (rr >= 0 && rr < ROWS && cc >= 0 && cc < COLS && mainArea.has(`${cc},${rr}`)) fixed = [cc, rr];
        }
      }
    }
    if (!fixed) throw new Error(`${name} 候选点 (${col},${row}) 附近 8 格内没有主连通区！`);
    console.warn(`  ⚠️ ${name} 候选 (${col},${row}) 落在孤岛/障碍上，已挪到主连通区 (${fixed[0]},${fixed[1]})`);
    [c, r] = fixed;
  }
  spots[name] = [c, r];
  console.log(`  "${name}": [(${c}, ${r})],`);
}
const [pc, pr] = [sc, sr];
console.log(`  玩家出生点: (${pc}, ${pr})`);

const out = {
  cols: COLS,
  rows: ROWS,
  tileDim: TILE_DIM,
  tileset: "assets/tiles/magecity.png",
  tilesetCols: TILESET_COLS,
  bgLayers: toRowMajor(bgLayers),
  objLayers: toRowMajor(objLayers),
  walkable,
  playerSpawn: { col: pc, row: pr },
  spots,
};
writeFileSync(new URL("../public/assets/town_map.json", import.meta.url), JSON.stringify(out));
console.log(`exported ${COLS}x${ROWS} map (tile ${TILE_DIM}px) -> public/assets/town_map.json`);
