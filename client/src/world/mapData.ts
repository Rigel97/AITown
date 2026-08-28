// 世界静态数据：角色精灵表布局、地图 JSON v3 类型、阻挡网格几何。
//
// 2026-08-26 地图切 v3（the_ville 裁剪版，见 client/scripts/the_ville_src/）：
// - town_map_v3.json 同时是「Tiled 标准地图」（Phaser load.tilemapTiledJSON
//   原生解析多 tileset 归属 + gid 翻转标志）和「项目世界数据」（walkable/
//   sectors/locations/出生点，后端共读）——一份文件两种角色，改地图 = 改
//   转换脚本重跑，前后端自动同步。
// - 10 个渲染层按数组顺序自下而上：前 8 层永远在角色之下，Foreground
//   L1/L2（树冠/吧台等半透明前景遮挡）永远在角色之上——the_ville 是
//   娃娃房式敞开室内（数据验证：窗口内无不透明屋顶层），没有可淡出的屋顶，
//   “遮挡感”由前景层置顶实现（角色站在吧台/树冠后面被正确盖住）。
// - 阻挡不用 tile 碰撞而用预计算 walkable 网格：服务端寻路已把网格作为
//   唯一权威，前端把它按行合并成矩形静态体，物理与服务端永不打架。
// - 角色继续用 folk2 精灵表（1792×256）：8 角色 × 4 方向行 × 7 帧
//   （1 idle + 6 walk），帧 32×64。

export const FOLK_SHEET = "assets/v2/folk2.png";
export const FOLK_FRAME_W = 32;
export const FOLK_FRAME_H = 64; // 角色 1 格宽 × 2 格高
const FRAMES_PER_CHAR = 7; // 1 idle + 6 walk
const SHEET_COLS = 1792 / FOLK_FRAME_W; // 56

/** 居民 id → folk2 角色序号；玩家用 7 */
export const CHARACTER_INDEX: Record<string, number> = {
  baker_lin: 0,
  librarian_su: 1,
  florist_mo: 2,
  lao_zhou: 3,
  hong_jie: 4,
  xiao_dou: 5,
  lao_song: 6,
};
export const PLAYER_CHARACTER = 7;

export type Direction = "down" | "left" | "right" | "up";
const DIR_ROW: Record<Direction, number> = { down: 0, left: 1, right: 2, up: 3 };

/** 角色 charIndex 朝 dir 的行走帧序列（6 帧循环，不含 idle） */
export function walkFrames(charIndex: number, dir: Direction): number[] {
  const base = DIR_ROW[dir] * SHEET_COLS + charIndex * FRAMES_PER_CHAR;
  return [base + 1, base + 2, base + 3, base + 4, base + 5, base + 6];
}

/** 角色 charIndex 朝 dir 的站立帧 */
export function idleFrame(charIndex: number, dir: Direction): number {
  return DIR_ROW[dir] * SHEET_COLS + charIndex * FRAMES_PER_CHAR;
}

// ── 地图 JSON v3（the_ville 裁剪版，转换管线单一源头）────────────────────────────────────────────────

/** tileset 表项：firstgid 起 global id 连续段，columns 决定 gid→格的行列换算 */
export interface VilleTileset {
  name: string;
  firstgid: number;
  columns: number;
  tilecount: number;
  image: string;
}

/** 渲染层：data 是行主序原始 gid（含 Tiled 翻转标志位，Phaser 原生解析）。
 *  opacity/visible 是 Tiled 标准字段——Phaser v4 解析层 alpha 时直接乘
 *  curl.opacity，缺字段会得 NaN 导致整层瓦片全透明（不报错），勿删。 */
export interface VilleLayer {
  name: string;
  type: "tilelayer";
  width: number;
  height: number;
  opacity: number;
  visible: boolean;
  data: number[];
}

/** town_map_v3.json 项目自有字段（Tiled 标准字段以外部分） */
export interface VilleMapData {
  version: number;
  cols: number;
  rows: number;
  tileDim: number;
  tilesets: VilleTileset[];
  layers: VilleLayer[];
  /** 行主序可行走网格：walkable[row][col]，与服务端寻路同源 */
  walkable: boolean[][];
  playerSpawn: { col: number; row: number };
}

/** 渲染在角色之上的前景层（树冠/吧台等半透明遮挡） */
export const FOREGROUND_LAYER_NAMES = new Set(["Foreground L1", "Foreground L2"]);

/** 阻挡网格按行合并出的连续矩形（瓦片坐标，h 恒为 1——只做行内合并）。
 * v2 用逐道具碰撞体，v3 改为整网格派生：2332 个阻挡格 → ~271 个矩形，
 * 物理体数量减一个数量级且与服务端 walkable 严格一致。 */
export interface BlockedRun {
  col: number;
  row: number;
  w: number;
  h: number;
}

export function blockedRuns(walkable: boolean[][]): BlockedRun[] {
  const runs: BlockedRun[] = [];
  for (let row = 0; row < walkable.length; row++) {
    const cols = walkable[row]!;
    let start = -1;
    for (let col = 0; col <= cols.length; col++) {
      const blocked = col < cols.length && !cols[col];
      if (blocked && start < 0) start = col;
      else if (!blocked && start >= 0) {
        runs.push({ col: start, row, w: col - start, h: 1 });
        start = -1;
      }
    }
  }
  return runs;
}

/**
 * 瓦片切比雪夫距离 ≤ range：与后端 world.mapdata.to_tile + engine.CHAT_RANGE_TILES
 * 同几何。对话距离判定前后端必须一致——旧版前端用像素欧氏距离，边界处会
 * 出现"有提示却 too_far"的错位（2026-08-21 深检发现）。
 */
export function withinChebyshevTiles(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  tileDim: number,
  rangeTiles: number,
): boolean {
  const dx = Math.abs(Math.floor(x1 / tileDim) - Math.floor(x2 / tileDim));
  const dy = Math.abs(Math.floor(y1 / tileDim) - Math.floor(y2 / tileDim));
  return Math.max(dx, dy) <= rangeTiles;
}
