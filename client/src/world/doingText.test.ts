import { describe, expect, it } from "vitest";

import { doingText } from "./doingText";

describe("doingText", () => {
  it("action 为空时返回空串（无动作不硬拼）", () => {
    expect(doingText("", "书架")).toBe("");
  });

  it("无家具时保持原文案", () => {
    expect(doingText("看书", "")).toBe("（正在看书）");
  });

  it("action 不含物体名时拼出家具位置", () => {
    expect(doingText("看书", "书架")).toBe("（在书架旁，正在看书）");
  });

  it("action 已含物体名时去重（不在'整理书架'里再报书架）", () => {
    expect(doingText("整理书架", "书架")).toBe("（正在整理书架）");
  });
});
