import { describe, expect, it } from "vitest";

import {
  FALLBACK_COLOR,
  PLAYER_COLOR,
  PORTRAIT_DIR,
  SPEAKER_COLORS,
  portraitUrl,
  speakerColor,
} from "./speakerStyle";

/** 与 server/db/seed.py 的居民 id 保持同步（漏一个 = 那位居民没有名牌专属色） */
const SEEDED_RESIDENT_IDS = [
  "baker_lin",
  "librarian_su",
  "florist_mo",
  "lao_zhou",
  "hong_jie",
  "xiao_dou",
  "lao_song",
] as const;

describe("speakerStyle", () => {
  it("每个种子居民都有专属色（且互不相同，群聊切换才认得出）", () => {
    const colors = SEEDED_RESIDENT_IDS.map((id) => speakerColor(id));
    expect(colors.every((c) => c !== FALLBACK_COLOR)).toBe(true);
    expect(new Set(colors).size).toBe(SEEDED_RESIDENT_IDS.length);
  });

  it("玩家（null）用玩家色，未知 id 用兜底色", () => {
    expect(speakerColor(null)).toBe(PLAYER_COLOR);
    expect(speakerColor("nobody")).toBe(FALLBACK_COLOR);
  });

  it("立绘 URL = 目录/{居民id}.png（文件名即 id，零映射）", () => {
    expect(portraitUrl("baker_lin")).toBe(`${PORTRAIT_DIR}/baker_lin.png`);
  });

  it("配色表键集与种子居民一致（防止改名漏改）", () => {
    expect(Object.keys(SPEAKER_COLORS).sort()).toEqual([...SEEDED_RESIDENT_IDS].sort());
  });
});
