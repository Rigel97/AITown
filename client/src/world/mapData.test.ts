// mapData 单元测试：folk2 帧号计算 + 对话距离判定（与后端同几何）+ v3 阻挡网格合并
import { describe, expect, it } from "vitest";
import {
  FOLK_FRAME_H,
  FOLK_FRAME_W,
  PLAYER_CHARACTER,
  blockedRuns,
  idleFrame,
  walkFrames,
  withinChebyshevTiles,
} from "./mapData";

describe("walkFrames（folk2：每角 7 帧，帧 0 = idle，1-6 = walk）", () => {
  it("玩家（index 7）四方向各 6 帧连续帧号，且不含 idle 帧", () => {
    for (const dir of ["down", "left", "right", "up"] as const) {
      const frames = walkFrames(PLAYER_CHARACTER, dir);
      expect(frames).toHaveLength(6);
      for (let i = 1; i < frames.length; i++) {
        expect(frames[i] - frames[i - 1]).toBe(1);
      }
      expect(frames[0]).toBe(idleFrame(PLAYER_CHARACTER, dir) + 1);
    }
  });

  it("帧号始终落在精灵表内（非负）且角色×方向间不重叠", () => {
    const all: number[] = [];
    for (let i = 0; i <= 7; i++) {
      for (const dir of ["down", "left", "right", "up"] as const) {
        all.push(idleFrame(i, dir));
        for (const f of walkFrames(i, dir)) {
          expect(f).toBeGreaterThanOrEqual(0);
          all.push(f);
        }
      }
    }
    // 8 角色 × 4 方向 × 7 帧互不重叠
    expect(new Set(all).size).toBe(all.length);
    expect(all.length).toBe(8 * 4 * 7);
  });
});

describe("blockedRuns（阻挡网格按行合并，v3 物理体来源）", () => {
  it("同一行的连续阻挡格合并为一个矩形", () => {
    // 第 0 行：前 3 格阻挡 → 一个 w=3 的矩形
    const runs = blockedRuns([
      [false, false, false, true, true],
      [true, true, true, true, true],
    ]);
    expect(runs).toEqual([{ col: 0, row: 0, w: 3, h: 1 }]);
  });

  it("同一行多段阻挡各自成矩形，顺序自左向右", () => {
    const runs = blockedRuns([[false, true, false, false, true, false]]);
    expect(runs).toEqual([
      { col: 0, row: 0, w: 1, h: 1 },
      { col: 2, row: 0, w: 2, h: 1 },
      { col: 5, row: 0, w: 1, h: 1 },
    ]);
  });

  it("全可走 / 全阻挡网格的边界情况", () => {
    expect(blockedRuns([[true, true], [true, true]])).toEqual([]);
    expect(blockedRuns([[false, false]])).toEqual([{ col: 0, row: 0, w: 2, h: 1 }]);
  });

  it("行尾阻挡也能闭合（越界哨兵触发的收尾分支）", () => {
    const runs = blockedRuns([[true, false, false]]);
    expect(runs).toEqual([{ col: 1, row: 0, w: 2, h: 1 }]);
  });
});

describe("withinChebyshevTiles（与服务端 CHAT_RANGE_TILES 同几何）", () => {
  const T = 32;
  const RANGE = 3;

  it("同格与邻近格在范围内", () => {
    expect(withinChebyshevTiles(100, 100, 100, 100, T, RANGE)).toBe(true);
    expect(withinChebyshevTiles(100, 100, 100 + 3 * T, 100, T, RANGE)).toBe(true);
    // 斜向：切比雪夫取两轴最大值
    expect(withinChebyshevTiles(100, 100, 100 + 2 * T, 100 + 3 * T, T, RANGE)).toBe(true);
  });

  it("超出的轴距离判不在范围", () => {
    expect(withinChebyshevTiles(100, 100, 100 + 4 * T, 100, T, RANGE)).toBe(false);
    expect(withinChebyshevTiles(100, 100, 100, 100 + 4 * T, T, RANGE)).toBe(false);
  });

  it("同格内的像素偏移不跨格（对齐服务端 to_tile 的取整语义）", () => {
    // 16 与 20 都在格 0 内
    expect(withinChebyshevTiles(16, 16, 20, 20, T, RANGE)).toBe(true);
    // 31 在格 0、33 在格 1：跨了一格但仍在 3 格内
    expect(withinChebyshevTiles(31, 16, 33, 16, T, RANGE)).toBe(true);
    // 同格内的像素偏移不跨格：31 在格 0、33 在格 1 → 差 1 格
    expect(withinChebyshevTiles(31, 16, 33, 16, T, RANGE)).toBe(true);
    // 恰好 3 格差（格 0 vs 格 3，x=96/97 都在第 3 格）→ 允许；第 4 格（x=128）→ 拒绝
    expect(withinChebyshevTiles(0, 0, 97, 0, T, RANGE)).toBe(true);
    expect(withinChebyshevTiles(0, 0, 128, 0, T, RANGE)).toBe(false);
  });
});

describe("素材常量（folk2 表几何，与 assets/v2/folk2.png 对齐）", () => {
  it("帧尺寸 32×64（角色 1 格宽 2 格高）", () => {
    expect(FOLK_FRAME_W).toBe(32);
    expect(FOLK_FRAME_H).toBe(64);
  });
});