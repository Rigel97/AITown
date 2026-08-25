// 世界静态数据：角色精灵表布局、地图 JSON v2 类型、道具足迹几何。
//
// 2026-08-25 视觉换装（v2 素材，LimeZu Modern Exteriors 系）：
// - 角色表 folk2.png（1792×256）：8 角色 × 4 方向行 × 7 帧（1 idle + 6 walk），
//   帧 32×64（角色 1 格宽 × 2 格高）。行序 down/left/right/up。
//   帧布局来源：Premade_Character 表（idle 行 y=64 / walk 行 y=128，
//   方向列 right@0-5/up@6-11/left@12-17/down@18-23，经肤色像素三重验证），
//   由 /tmp 工具链拼合，角色顺序 = CHARACTER_INDEX 值序。
// - 地图 v2：建筑/树木是整 Sprite（props）而非瓦片拼墙——角色可以走到
//   树冠/屋顶"后面"（depth=y 排序），阻挡走预计算足迹（与服务端同源）。
// - 地图数据唯一源头：client/scripts/build_map.py → town_map.json，
//   前端渲染与服务端寻路共读。

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

// ── 地图 JSON v2 ─────────────────────────────────────────────────

/** 地图道具：col/row = 锚点格（足迹中列 × 底行）；bw/bh = 阻挡足迹（0 = 贴花） */
export interface MapProp {
  img: string;
  col: number;
  row: number;
  bw: number;
  bh: number;
}

export interface TownMapData {
  version: number;
  cols: number;
  rows: number;
  tileDim: number;
  tileset: string;
  tilesetCols: number;
  /** 行主序图层，-1 = 空（bg0 地面 / bg1 地面贴花） */
  bgLayers: number[][][];
  objLayers: number[][][];
  props: MapProp[];
  walkable: boolean[][];
  playerSpawn: { col: number; row: number };
}

/** 道具阻挡足迹覆盖的格子范围（与 build_map.py 的推导同式：c0 = col - bw/2） */
export function propFootprint(p: MapProp): {
  c0: number;
  r0: number;
  c1: number;
  r1: number;
} | null {
  if (p.bw <= 0 || p.bh <= 0) return null;
  const c0 = p.col - Math.floor(p.bw / 2);
  return { c0, r0: p.row - p.bh + 1, c1: c0 + p.bw - 1, r1: p.row };
}

/** 道具渲染/碰撞锚点像素（足迹中列、底行下缘） */
export function propAnchorPx(p: MapProp, tileDim: number): { x: number; y: number } {
  return { x: p.col * tileDim + tileDim / 2, y: (p.row + 1) * tileDim };
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
