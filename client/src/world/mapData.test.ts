// mapData 单元测试：帧号计算 + 对话距离判定（与后端同几何，2026-08-21 深检 G 修复）
import { describe, expect, it } from "vitest";
import { PLAYER_CHARACTER, walkFrames, withinChebyshevTiles } from "./mapData";

describe("walkFrames", () => {
  it("玩家（f8，index 7）四方向各 3 帧连续帧号", () => {
    for (const dir of ["down", "left", "right", "up"] as const) {
      const frames = walkFrames(PLAYER_CHARACTER, dir);
      expect(frames).toHaveLength(3);
      expect(frames[1] - frames[0]).toBe(1);
      expect(frames[2] - frames[1]).toBe(1);
    }
  });

  it("帧号始终落在精灵表内（非负）且方向间不重叠", () => {
    const all: number[] = [];
    for (let i = 0; i <= 7; i++) {
      for (const dir of ["down", "left", "right", "up"] as const) {
        for (const f of walkFrames(i, dir)) {
          expect(f).toBeGreaterThanOrEqual(0);
          all.push(f);
        }
      }
    }
    expect(new Set(all).size).toBe(all.length); // 8 角色 × 4 方向 × 3 帧互不重叠
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
