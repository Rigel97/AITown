// HUD：探出式一体化对话卡（立绘+名牌+台词+输入）+ 事件日志 + 提示条（DOM 层）
//
// 设计说明（为什么这样设计）：
// - 文字 UI 用 DOM 而不是 Phaser 画布——文字清晰、输入框免造轮子、中文字体随便用；
//   Phaser 只负责像素世界。HUD 不直接碰网络，通过回调与场景解耦。
// - 对话卡是"谁说话就展示谁"的一体化单元：大立绘骑在卡片左侧、头部探出卡片顶，
//   名牌用居民专属色、台词在立绘右侧——脸、名字、话绑定，群聊切换时 0.1 秒认人。
// - 群聊多句回复是"一批到达"（一条 WS 消息含多行台词），卡片用展示队列逐句播放
//   （每句停留 DWELL_MS），而不是一次性只留最后一句——涌现的群聊戏值得被看完。
// - 玩家发言不切立绘（立绘语义 = "你面前这位/最近发言的居民"），避免你说→TA 答
//   之间立绘来回闪切。
// - 对话历史按居民 id 隔离（和谁说话就看到和谁的记录），平时收起（按 H 展开）——
//   立绘时代画面空间宝贵，但"居民记得你"的配套记录功能不丢。
// - "正在想…"在卡片台词区呼吸显示；回复丢失（error/断线）时恢复上一句，不留白卡。

import { SPEAKER_COLORS, portraitUrl, speakerColor } from "./speakerStyle";
import { typeSpeedMultiplier } from "../settings";

export interface HudCallbacks {
  onSend: (text: string) => void;
  onClose: () => void;
  /** 台词说话人名字 → 居民 id（群聊台词只有名字，id 要靠场景的居民表反查） */
  resolveResident: (name: string) => string | null;
}

interface ChatLine {
  who: string;
  text: string;
}

const MAX_EVENTS = 50;

/** 事件分类（决定日志条目的左侧色条，不引 emoji）：对话/时刻/动态/其他 */
function eventCategory(text: string): string {
  if (/(聊天|聊了|谈起|搭话|说)/.test(text)) return "cat-dialogue";
  if (/(天亮|黎明|清晨|入夜|天黑|黄昏|深夜|白天|时刻)/.test(text)) return "cat-time";
  if (/(开始|前往|起身|走到|回家|出门|离开|回来)/.test(text)) return "cat-action";
  return "cat-misc";
} /** 群聊展示队列：每句停留时长（1–2 句台词的阅读时间，从打字完成后起算） */
const DWELL_MS = 1600;
/** 逐字打印：每字间隔（中文阅读舒适值）；标点后微顿更“像在说话” */
const TYPE_MS = 26;
const PUNCT_PAUSE_MS = 130;
const PUNCTUATION = "，。！？…；：、—“”";
/** 立绘切换：先淡出旧图再换 src 的间隔 */
const PORTRAIT_FADE_OUT_MS = 110;
/** 关闭动画时长（与 CSS chat-layer-out 一致） */
const CLOSE_ANIM_MS = 160;

export class Hud {
  private chatLayer = document.getElementById("chat-layer")!;
  private portraitImg = document.getElementById("portrait") as HTMLImageElement;
  private chatName = document.getElementById("chat-name")!;
  private chatText = document.getElementById("chat-text")!;
  private chatInput = document.getElementById("chat-input") as HTMLInputElement;
  private historyBtn = document.getElementById("chat-history-btn") as HTMLButtonElement;
  private historyPanel = document.getElementById("chat-history")!;
  private hint = document.getElementById("hint")!;
  private statusBar = document.getElementById("status-bar")!;
  private eventLog = document.getElementById("event-log")!;
  private eventToggle = document.getElementById("event-log-toggle") as HTMLButtonElement;

  private readonly cb: HudCallbacks;

  /** 每个居民一份对话历史（key = resident id） */
  private histories = new Map<string, ChatLine[]>();
  /** 当前对话的居民 id（历史归属、thinking 归属） */
  private currentId: string | null = null;
  /** 当前立绘显示的居民 id（群聊中随最后发言者切换） */
  private portraitResidentId: string | null = null;
  /** 正在等待回复的居民 id（"正在想"状态） */
  private thinkingId: string | null = null;
  /** 最近一次卡片展示的台词（thinking 被清空且无新台词时恢复它，不留白卡） */
  private lastView: ChatLine | null = null;
  /** 群聊多句的展示队列（逐句播放，见文件头说明） */
  private displayQueue: ChatLine[] = [];
  private queueTimer: number | undefined;
  private portraitTimer: number | undefined;
  private closeTimer: number | undefined;
  /** 逐字打印状态：进行中时点击台词区可立即补全 */
  private typeTimer: number | undefined;
  private typingFullText = "";
  private typingPos = 0;
  /** 临时提示的生效截止（performance.now()）：期间 setHint(null) 不清 */
  private hintUntil = 0;
  /** 当前显示的 hint 文本（null=隐藏）：same-value 短路用 */
  private hintText: string | null = null;

  constructor(cb: HudCallbacks) {
    this.cb = cb;
    this.chatInput.addEventListener("keydown", (e) => {
      // 阻止冒泡，避免打字触发游戏按键
      e.stopPropagation();
      if (e.key === "Enter") {
        const text = this.chatInput.value.trim();
        if (text && this.currentId) {
          this.addChatLine(this.currentId, "你", text);
          this.showThinking(this.currentId);
          this.cb.onSend(text);
        }
        this.chatInput.value = "";
      } else if (e.key === "Escape") {
        this.escape();
      }
    });
    this.historyBtn.addEventListener("click", () => this.toggleHistory());
    // 打字中点台词区 = 跳过动画立即补全（尊重玩家时间；点击是隐藏福利不加 pointer）
    this.chatText.addEventListener("click", () => this.completeTyping());
    this.eventToggle.addEventListener("click", () => {
      // toggle 返回"hidden 是否仍在"（true=加了 hidden 即已收起）
      const open = !this.eventLog.classList.toggle("hidden");
      this.eventToggle.classList.toggle("active", open);
      this.eventToggle.setAttribute("aria-expanded", String(open));
    });
    // 立绘加载失败（如新增居民忘放图）→ 隐藏占位破图；成功则恢复
    this.portraitImg.addEventListener("error", () => this.portraitImg.classList.add("no-art"));
    this.portraitImg.addEventListener("load", () => this.portraitImg.classList.remove("no-art"));
    // 预载全部立绘：切换时才不闪白
    this.preloadPortraits();
  }

  /** 预载全部居民立绘（本地资源，几百 KB×7，一次性；名单以配色表为准） */
  private preloadPortraits(): void {
    for (const id of Object.keys(SPEAKER_COLORS)) {
      const img = new Image();
      img.src = portraitUrl(id);
    }
  }

  get isChatOpen(): boolean {
    return !this.chatLayer.classList.contains("hidden");
  }

  get isHistoryOpen(): boolean {
    return !this.historyPanel.classList.contains("hidden");
  }

  /** 打开对话卡：立绘切到该居民，台词区显示最近一句（重开有连续感） */
  openChat(residentId: string, residentName: string): void {
    this.currentId = residentId;
    this.flushDisplayQueue();
    this.setPortrait(residentId);
    const lines = this.histories.get(residentId) ?? [];
    const last = lines[lines.length - 1] ?? null;
    this.setLineView(last ?? { who: residentName, text: "" }, true); // 重开恢复不重打字
    this.renderHistory();
    this.chatLayer.classList.remove("hidden", "closing");
    if (this.closeTimer !== undefined) {
      window.clearTimeout(this.closeTimer);
      this.closeTimer = undefined;
    }
    this.chatInput.focus();
  }

  /** Esc 分层：先关对话记录浮层，再关对话卡（输入框内 Esc 与全局 Esc 都走这里）。
   *  H 键只在输入框未聚焦时生效（打字优先），开记录主路径是 📜 按钮 */
  escape(): void {
    if (this.isHistoryOpen) {
      this.closeHistory();
      return;
    }
    this.closeChat();
  }

  closeChat(): void {
    this.flushDisplayQueue();
    this.completeTyping();
    this.thinkingId = null;
    this.closeHistory();
    if (this.closeTimer !== undefined) window.clearTimeout(this.closeTimer);
    // 先播关闭动画，动画结束再 display:none
    this.chatLayer.classList.add("closing");
    this.closeTimer = window.setTimeout(() => {
      this.chatLayer.classList.add("hidden");
      this.chatLayer.classList.remove("closing");
      this.closeTimer = undefined;
    }, CLOSE_ANIM_MS);
    this.chatInput.blur();
    this.cb.onClose();
  }

  /** 追加一条对话记录；正在和这位居民聊则同时更新卡片（群聊走展示队列） */
  addChatLine(residentId: string, who: string, text: string): void {
    const lines = this.histories.get(residentId) ?? [];
    lines.push({ who, text });
    this.histories.set(residentId, lines);
    if (who !== "你") this.hideThinking(residentId);
    if (residentId === this.currentId && this.isChatOpen) {
      this.enqueueView({ who, text });
      if (this.isHistoryOpen) this.appendHistoryLine(who, text);
    }
  }

  /** “对方正在想…”：卡片台词区呼吸显示（立绘保持，名牌保持） */
  showThinking(residentId: string): void {
    this.flushDisplayQueue();
    this.completeTyping();
    this.thinkingId = residentId;
    if (residentId === this.currentId && this.isChatOpen) {
      this.chatText.textContent = "……";
      this.chatText.classList.add("thinking");
    }
  }

  /** 结束“正在想”：指定居民只清本人；不传/传 null 清全部（重连场景）。
   *  回复没来（error/断线）时恢复上一句台词，不留白卡。 */
  hideThinking(residentId?: string | null): void {
    if (residentId != null && residentId !== this.thinkingId) return;
    if (this.thinkingId === this.currentId && this.isChatOpen) {
      this.chatText.classList.remove("thinking");
      this.chatText.textContent = this.lastView?.text ?? ""; // 错误恢复不走打字机
    }
    this.thinkingId = null;
  }

  /** 切换对话记录浮层（H 键 / 按钮） */
  toggleHistory(): void {
    if (!this.isChatOpen) return;
    if (this.isHistoryOpen) {
      this.closeHistory();
    } else {
      this.renderHistory();
      this.historyPanel.classList.remove("hidden");
      this.chatLayer.classList.add("history-open");
    }
  }

  private closeHistory(): void {
    this.historyPanel.classList.add("hidden");
    this.chatLayer.classList.remove("history-open");
  }

  addEvent(time: string, text: string): void {
    const item = document.createElement("div");
    item.className = `event ${eventCategory(text)}`;
    const timeEl = document.createElement("span");
    timeEl.className = "time";
    timeEl.textContent = time;
    item.append(timeEl, document.createTextNode(text));
    this.eventLog.prepend(item); // 新的在上面
    while (this.eventLog.childElementCount > MAX_EVENTS) {
      this.eventLog.lastElementChild?.remove();
    }
  }

  /** 状态栏（屏幕左上角固定，DOM 原生渲染）：same-value 短路防 60fps 重排 */
  setStatus(text: string): void {
    if (text === this.statusBar.textContent) return;
    this.statusBar.textContent = text;
  }

  setHint(text: string | null): void {
    // TTL 窗口内一切免打扰：临时提示（cooldown/too_far/存档确认）优先于
    // 每帧刷新的走近提示和 null 清除——玩家站在居民旁边时，错误/存档
    // 反馈不能被走近提示一帧就盖掉；TTL 过后下一帧自动回到正常逻辑
    if (performance.now() < this.hintUntil) return;
    // same-value 短路：update() 每帧传相同的 hint 文本，textContent 赋值
    // 每次都会重建文本节点并触发排版——60fps 下纯烧 DOM
    if (text === this.hintText) return;
    this.hintText = text;
    if (text) {
      this.hint.textContent = text;
      this.hint.classList.remove("hidden");
    } else {
      this.hint.classList.add("hidden");
    }
  }

  /** 临时提示：显示 ms 毫秒，期间 setHint 任何调用都不覆盖（错误/存档反馈用） */
  flashHint(text: string, ms: number): void {
    // 直接写入不走 setHint：TTL 检查会挡住自己（上一条 flash 可能还在期内）
    this.hintText = text;
    this.hint.textContent = text;
    this.hint.classList.remove("hidden");
    this.hintUntil = performance.now() + ms;
  }

  // ---------- 卡片内部 ----------

  /** 台词入队：玩家插话立即显示并清空队列；群聊逐句播放 */
  private enqueueView(line: ChatLine): void {
    if (line.who === "你") this.flushDisplayQueue();
    this.displayQueue.push(line);
    // 有待播节拍（正在展示某句）就排队等下一拍；没有才立即播。
    // 注意不能用 queue.length===1 判断：上一句已被 shift 消费、正往在
    // dwell 时队列就是空的，新句入队 length 又变 1，会立即插播顶掉它
    if (this.queueTimer === undefined) this.playNextInQueue();
  }

  private playNextInQueue(): void {
    const line = this.displayQueue.shift();
    if (!line) {
      this.queueTimer = undefined; // 队列枯竭：节拍链自然终止
      return;
    }
    this.setLineView(line);
    // 停留 = 打字时长 + 阅读时长：打完这一句再停 DWELL_MS 才切下一句
    const typeMs = Math.round(line.text.length * TYPE_MS * typeSpeedMultiplier());
    this.queueTimer = window.setTimeout(() => this.playNextInQueue(), typeMs + DWELL_MS);
  }

  private flushDisplayQueue(): void {
    if (this.queueTimer !== undefined) {
      window.clearTimeout(this.queueTimer);
      this.queueTimer = undefined;
    }
    this.displayQueue = [];
  }

  /** 更新卡片：名牌（专属色）+ 台词；立绘按说话人切换（玩家行保持当前立绘）。
   *  instant = 跳过打字机直接全文（重开卡恢复/错误恢复等“不该重播”的场景） */
  private setLineView(line: ChatLine, instant = false): void {
    this.lastView = line.text ? line : null;
    const residentId = this.cb.resolveResident(line.who);
    if (residentId !== null) this.setPortrait(residentId);
    this.chatName.textContent = line.who;
    this.chatName.style.background = speakerColor(residentId);
    this.chatText.classList.remove("thinking");
    this.typeLine(line.text, instant);
  }

  /** 逐字打印台词；返回总时长（ms，供展示队列调度 dwell 起点估算）。
   *  文字速度“即显”（倍率 0）直接全文；慢/标准按倍率缩放每字间隔 */
  private typeLine(text: string, instant = false): number {
    const mult = typeSpeedMultiplier();
    if (this.typeTimer !== undefined) window.clearTimeout(this.typeTimer);
    this.typeTimer = undefined;
    this.chatText.classList.remove("typing");
    if (instant || mult === 0 || text.length === 0) {
      this.chatText.textContent = text;
      return 0;
    }
    this.typingFullText = text;
    this.typingPos = 0;
    this.chatText.classList.add("typing");
    const tick = (): void => {
      this.typingPos++;
      this.chatText.textContent = this.typingFullText.slice(0, this.typingPos);
      if (this.typingPos >= this.typingFullText.length) {
        this.chatText.classList.remove("typing");
        this.typeTimer = undefined;
        return;
      }
      const ch = this.typingFullText[this.typingPos - 1] ?? "";
      const delay = Math.round((TYPE_MS + (PUNCTUATION.includes(ch) ? PUNCT_PAUSE_MS : 0)) * mult);
      this.typeTimer = window.setTimeout(tick, delay);
    };
    tick();
    return Math.round(text.length * TYPE_MS * mult);
  }

  /** 打字中点击台词区：立即补全当前句 */
  private completeTyping(): void {
    if (this.typeTimer === undefined) return;
    window.clearTimeout(this.typeTimer);
    this.typeTimer = undefined;
    this.chatText.classList.remove("typing");
    this.chatText.textContent = this.typingFullText;
  }

  /** 切换立绘：先淡出旧图（110ms）再换 src，避免加载闪白 */
  private setPortrait(residentId: string): void {
    if (this.portraitResidentId === residentId) return;
    this.portraitResidentId = residentId;
    if (this.portraitTimer !== undefined) window.clearTimeout(this.portraitTimer);
    this.portraitImg.classList.add("fading");
    this.portraitTimer = window.setTimeout(() => {
      this.portraitImg.src = portraitUrl(residentId);
      this.portraitImg.classList.remove("fading");
      this.portraitTimer = undefined;
    }, PORTRAIT_FADE_OUT_MS);
  }

  // ---------- 对话记录浮层 ----------

  private renderHistory(): void {
    this.historyPanel.innerHTML = "";
    if (!this.currentId) return;
    for (const line of this.histories.get(this.currentId) ?? []) {
      this.appendHistoryLine(line.who, line.text);
    }
    this.historyPanel.scrollTop = this.historyPanel.scrollHeight;
  }

  private appendHistoryLine(who: string, text: string): void {
    const line = document.createElement("div");
    line.className = "line";
    const whoEl = document.createElement("span");
    whoEl.className = "who";
    whoEl.textContent = `${who}：`;
    line.append(whoEl, document.createTextNode(text));
    this.historyPanel.appendChild(line);
    this.historyPanel.scrollTop = this.historyPanel.scrollHeight;
  }
}
