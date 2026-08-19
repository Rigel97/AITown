// actionEmoji 单测：关键词命中、优先级排序、职业回退、兜底。
// 为什么值得测：头顶 emoji 是"世界活着"的第一视觉信号，映射错乱
// （烤面包显示成睡觉）比没有 emoji 更伤观感——锁死排序语义。

import { describe, expect, it } from "vitest";
import { actionEmoji } from "./actionEmoji";

describe("actionEmoji 关键词命中", () => {
  it("动作文本命中关键词", () => {
    expect(actionEmoji("在面包房烤面包")).toBe("🥐");
    expect(actionEmoji("给门口的花圃浇水")).toBe("🌸"); // 花 优先于 浇
    expect(actionEmoji("给菜园浇水")).toBe("💧"); // 没有"花"时才落到 浇
    expect(actionEmoji("整理书架")).toBe("📖"); // 书 优先于 整理
    expect(actionEmoji("打扫厨房")).toBe("🧹"); // 多字短语"打扫"优先于单字"厨"
  });

  it("聊天中是最高优先级外显信号", () => {
    expect(actionEmoji("聊天中")).toBe("💬");
    expect(actionEmoji("和林师傅聊天")).toBe("💬");
  });

  it("吃饭类动作统一 🍚", () => {
    expect(actionEmoji("吃午饭")).toBe("🍚");
    expect(actionEmoji("去红姐餐馆吃晚饭")).toBe("🍚");
  });
});

describe("actionEmoji 回退链", () => {
  it("动作未命中 → 职业默认", () => {
    expect(actionEmoji("忙自己的事", "面包师")).toBe("🥐");
    expect(actionEmoji("忙自己的事", "图书管理员")).toBe("📖");
    expect(actionEmoji("忙自己的事", "退休邮差")).toBe("✉️");
    expect(actionEmoji("忙自己的事", "餐馆老板娘")).toBe("🍳");
    expect(actionEmoji("忙自己的事", "杂货店主兼木匠")).toBe("📦");
  });

  it("动作与职业都未命中 → 🚶 兜底", () => {
    expect(actionEmoji("某某事")).toBe("🚶");
    expect(actionEmoji("某某事", "无匹配职业")).toBe("🚶");
    expect(actionEmoji("")).toBe("🚶");
  });
});
