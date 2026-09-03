import { describe, expect, it } from "vitest";

import {
  getSettings,
  subscribe,
  typeSpeedMultiplier,
  updateSettings,
  type GameSettings,
} from "./settings";

/** 每个用例自设初值（模块级 cached 会跨用例残留，不能依赖默认态） */
function seed(s: Partial<GameSettings>): void {
  updateSettings({ textSpeed: "normal", lightEffects: true, ...s });
}

describe("settings", () => {
  it("默认值：标准文字速度 + 光照开", () => {
    seed({});
    expect(getSettings()).toEqual({ textSpeed: "normal", lightEffects: true });
  });

  it("updateSettings 只合并传入的字段", () => {
    seed({ textSpeed: "slow" });
    expect(getSettings().lightEffects).toBe(true);
    expect(getSettings().textSpeed).toBe("slow");
  });

  it("文字速度倍率：即显=0 / 标准=1 / 慢=1.8", () => {
    seed({ textSpeed: "instant" });
    expect(typeSpeedMultiplier()).toBe(0);
    seed({ textSpeed: "normal" });
    expect(typeSpeedMultiplier()).toBe(1);
    seed({ textSpeed: "slow" });
    expect(typeSpeedMultiplier()).toBe(1.8);
  });

  it("subscribe 收到变更通知，退订后不再收", () => {
    seed({});
    const seen: GameSettings[] = [];
    const off = subscribe((s) => seen.push({ ...s }));
    updateSettings({ textSpeed: "slow" });
    off();
    updateSettings({ textSpeed: "instant" });
    // 退订后不再收：只有 slow 一条，instant 的变更没有推给已退订的监听者
    expect(seen).toEqual([{ textSpeed: "slow", lightEffects: true }]);
  });
});
