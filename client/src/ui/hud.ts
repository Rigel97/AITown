// HUD：对话面板 + 事件日志 + 提示条（DOM 层）
// 设计说明：文字 UI 用 DOM 而不是 Phaser 画布——文字清晰、输入框免造轮子、
// 中文字体随便用；Phaser 只负责像素世界。HUD 不直接碰网络，通过回调与场景解耦。
// 对话历史按居民 id 隔离：和谁说话就看到和谁的记录（2026-08-17 用户反馈修复）。

export interface HudCallbacks {
  onSend: (text: string) => void;
  onClose: () => void;
}

interface ChatLine {
  who: string;
  text: string;
}

const MAX_EVENTS = 50;

export class Hud {
  private chatPanel = document.getElementById("chat-panel")!;
  private chatTitle = document.getElementById("chat-title")!;
  private chatHistory = document.getElementById("chat-history")!;
  private chatInput = document.getElementById("chat-input") as HTMLInputElement;
  private hint = document.getElementById("hint")!;
  private eventLog = document.getElementById("event-log")!;
  private eventToggle = document.getElementById("event-log-toggle") as HTMLButtonElement;

  /** 每个居民一份对话历史（key = resident id） */
  private histories = new Map<string, ChatLine[]>();
  private currentId: string | null = null;
  /** 临时提示的生效截止（performance.now()）：期间 setHint(null) 不清 */
  private hintUntil = 0;
  /** 当前显示的 hint 文本（null=隐藏）：same-value 短路用 */
  private hintText: string | null = null;
  /** “正在想…”占位符按居民隔离：B 的回复到达不能误删 A 的占位
   * （旧版单元素设计：同时对两人说话时，先到的回复会把后一句的
   * 占位删掉，玩家误以为消息丢了） */
  private thinking = new Map<string, HTMLElement>();

  constructor(cb: HudCallbacks) {
    this.chatInput.addEventListener("keydown", (e) => {
      // 阻止冒泡，避免打字触发游戏按键
      e.stopPropagation();
      if (e.key === "Enter") {
        const text = this.chatInput.value.trim();
        if (text && this.currentId) {
          this.addChatLine(this.currentId, "你", text);
          this.showThinking(this.currentId);
          cb.onSend(text);
        }
        this.chatInput.value = "";
      } else if (e.key === "Escape") {
        this.closeChat();
      }
    });
    this.eventToggle.addEventListener("click", () => {
      this.eventLog.classList.toggle("hidden");
    });
  }

  get isChatOpen(): boolean {
    return !this.chatPanel.classList.contains("hidden");
  }

  /** 打开对话面板。title 由调用方组装完整文案（单聊/加入群聊语境在场景层才知道） */
  openChat(residentId: string, title: string): void {
    this.currentId = residentId;
    this.chatPanel.classList.remove("hidden");
    this.chatTitle.textContent = title;
    this.renderHistory();
    this.chatInput.focus();
  }

  closeChat(): void {
    this.chatPanel.classList.add("hidden");
    this.chatInput.blur();
  }

  /** 追加一条对话记录；若正好在和这位居民聊，立即渲染。
   * 居民的回复到达（who !== "你"）会移除该居民的“正在想”占位符 */
  addChatLine(residentId: string, who: string, text: string): void {
    const lines = this.histories.get(residentId) ?? [];
    lines.push({ who, text });
    this.histories.set(residentId, lines);
    if (who !== "你") this.hideThinking(residentId);
    if (residentId === this.currentId && this.isChatOpen) {
      this.appendLine(who, text);
      this.chatHistory.scrollTop = this.chatHistory.scrollHeight;
    }
  }

  /** “对方正在想…”占位行（该居民的回复/error 到达时自动移除） */
  showThinking(residentId: string): void {
    this.hideThinking(residentId); // 同目标重复发话：旧占位先清，不叠罗汉
    const el = document.createElement("div");
    el.className = "line thinking";
    el.textContent = "（对方正在想…）";
    this.chatHistory.appendChild(el);
    this.chatHistory.scrollTop = this.chatHistory.scrollHeight;
    this.thinking.set(residentId, el);
  }

  /** 移除占位符：指定居民只清本人；不传/传 null 清全部（重开面板时） */
  hideThinking(residentId?: string | null): void {
    if (residentId == null) {
      for (const el of this.thinking.values()) el.remove();
      this.thinking.clear();
      return;
    }
    this.thinking.get(residentId)?.remove();
    this.thinking.delete(residentId);
  }

  private renderHistory(): void {
    this.chatHistory.innerHTML = "";
    this.hideThinking(); // 重建 DOM 后旧占位元素已死，清掉悬挂引用
    if (!this.currentId) return;
    for (const line of this.histories.get(this.currentId) ?? []) {
      this.appendLine(line.who, line.text);
    }
    this.chatHistory.scrollTop = this.chatHistory.scrollHeight;
  }

  private appendLine(who: string, text: string): void {
    const line = document.createElement("div");
    line.className = "line";
    const whoEl = document.createElement("span");
    whoEl.className = "who";
    whoEl.textContent = `${who}：`;
    line.append(whoEl, document.createTextNode(text));
    this.chatHistory.appendChild(line);
  }

  addEvent(time: string, text: string): void {
    const item = document.createElement("div");
    item.className = "event";
    const timeEl = document.createElement("span");
    timeEl.className = "time";
    timeEl.textContent = time;
    item.append(timeEl, document.createTextNode(text));
    this.eventLog.prepend(item); // 新的在上面
    while (this.eventLog.childElementCount > MAX_EVENTS) {
      this.eventLog.lastElementChild?.remove();
    }
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
}
