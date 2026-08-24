// 世界静态数据：角色精灵表布局与角色分配。
//
// 2026-08-18 起：地图数据不再由此文件代码生成，改由
// scripts/convert_aitown_map.mjs 从 ai-town mage3 源数据产出 town_map.json
// （前端渲染与服务端寻路共读）。本文件只留"角色 ↔ folk 精灵表"的映射。
//
// folk 精灵表（32x32folk.png，ai-town 素材，384×256 = 12 列 × 8 行 32px 帧）：
// 8 个角色各占 3 列 × 4 行（96×128），块内 4 行依次为 down/left/right/up，
// 每行 3 帧行走动画。角色 i 的块位置：col = i % 4，row = floor(i / 4)。

export const FOLK_SHEET = "assets/sprites/32x32folk.png";
export const FOLK_FRAME = 32; // 单帧边长
const SHEET_COLS = 384 / FOLK_FRAME; // 12

/** 居民 id → folk 角色序号（f1–f7）；玩家用 f8 */
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

/** 角色 charIndex 朝 dir 方向的行走帧序列（3 帧） */
export function walkFrames(charIndex: number, dir: Direction): number[] {
  const blockCol = charIndex % 4;
  const blockRow = Math.floor(charIndex / 4);
  const base = (blockRow * 4 + DIR_ROW[dir]) * SHEET_COLS + blockCol * 3;
  return [base, base + 1, base + 2];
}

/**
 * 瓦片切比雪夫距离 ≤ range：与后端 world.mapdata.to_tile + engine.CHAT_RANGE_TILES
 * 同几何。对话距离判定前后端必须一致——旧版前端用像素欧氏距离，边界处
 * 会出现"有提示却 too_far"的错位（2026-08-21 深检发现）。
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
