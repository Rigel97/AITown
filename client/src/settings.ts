// 游戏设置：数据 + localStorage 持久化 + 面板 DOM 控制。
//
// 设计说明（为什么这样设计）：
// - 数据与 DOM 分层：getSettings/updateSettings/subscribe 是纯数据层（可单测），
//   面板渲染与按钮绑定收在本模块的 initSettingsPanel——hud/场景只碰数据层。
// - 场景用 subscribe 订阅变更（TownScene 必须在 shutdown 时退订，否则
//   TitleScene 反复进出会重复注册 listener）。
// - localStorage 读写全程 try/catch：隐私模式/禁存储时设置退化为内存态，不崩。

export type TextSpeed = "slow" | "normal" | "instant";

export interface GameSettings {
  /** 台词打字机速度：slow 慢速 / normal 标准 / instant 直接全文 */
  textSpeed: TextSpeed;
  /** 昼夜光照滤镜开关（B 键手动切换同一状态） */
  lightEffects: boolean;
}

const STORAGE_KEY = "aitown.settings";
const DEFAULTS: GameSettings = { textSpeed: "normal", lightEffects: true };

let cached: GameSettings | null = null;
const listeners = new Set<(s: GameSettings) => void>();

export function getSettings(): GameSettings {
  if (cached) return cached;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    cached = raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<GameSettings>) } : { ...DEFAULTS };
  } catch {
    cached = { ...DEFAULTS };
  }
  return cached;
}

export function updateSettings(patch: Partial<GameSettings>): GameSettings {
  const next = { ...getSettings(), ...patch };
  cached = next;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // 存储不可用：本次会话内仍生效（内存态），不阻塞
  }
  for (const fn of listeners) fn(next);
  renderPanel(next);
  return next;
}

/** 打字机倍率：instant 返回 0（调用方直接全文显示） */
export function typeSpeedMultiplier(): number {
  const v = getSettings().textSpeed;
  return v === "instant" ? 0 : v === "slow" ? 1.8 : 1;
}

export function subscribe(fn: (s: GameSettings) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// ---------- 面板 DOM（⚙ 按钮 + #settings-panel） ----------

let panelInited = false;

/** 绑定设置面板交互（main.ts bootstrap 时调用一次；DOM 在 main.ts 之前就绪） */
export function initSettingsPanel(): void {
  if (panelInited) return;
  panelInited = true;
  document
    .getElementById("settings-toggle")
    ?.addEventListener("click", () => toggleSettingsPanel());
  const speedSeg = document.getElementById("seg-text-speed");
  speedSeg?.addEventListener("click", (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-v]");
    if (!btn) return;
    updateSettings({ textSpeed: btn.dataset.v as TextSpeed });
  });
  document
    .getElementById("light-toggle")
    ?.addEventListener("click", () =>
      updateSettings({ lightEffects: !getSettings().lightEffects }),
    );
  renderPanel(getSettings());
}

export function toggleSettingsPanel(): void {
  document.getElementById("settings-panel")?.classList.toggle("hidden");
}

function renderPanel(s: GameSettings): void {
  // 无 DOM 环境（vitest node 模式）：只改数据不动面板，数据层保持可单测
  if (typeof document === "undefined") return;
  const seg = document.getElementById("seg-text-speed");
  seg?.querySelectorAll("button[data-v]").forEach((b) => {
    b.classList.toggle("selected", (b as HTMLElement).dataset.v === s.textSpeed);
  });
  const light = document.getElementById("light-toggle");
  if (light) {
    light.textContent = s.lightEffects ? "开" : "关";
    light.classList.toggle("off", !s.lightEffects);
  }
}
