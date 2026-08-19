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
  private thinkingEl: HTMLElement | null = null;

  constructor(cb: HudCallbacks) {
    this.chatInput.addEventListener("keydown", (e) => {
      // 阻止冒泡，避免打字触发游戏按键
      e.stopPropagation();
      if (e.key === "Enter") {
        const text = this.chatInput.value.trim();
        if (text && this.currentId) {
          this.addChatLine(this.currentId, "你", text);
          this.showThinking();
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

  /** 追加一条对话记录；若正好在和这位居民聊，立即渲染 */
  addChatLine(residentId: string, who: string, text: string): void {
    const lines = this.histories.get(residentId) ?? [];
    lines.push({ who, text });
    this.histories.set(residentId, lines);
    if (residentId === this.currentId && this.isChatOpen) {
      this.hideThinking();
      this.appendLine(who, text);
      this.chatHistory.scrollTop = this.chatHistory.scrollHeight;
    }
  }

  /** "对方正在想…"占位行（回复到达时由 addChatLine 移除） */
  showThinking(): void {
    this.hideThinking();
    const el = document.createElement("div");
    el.className = "line thinking";
    el.textContent = "（对方正在想…）";
    this.chatHistory.appendChild(el);
    this.chatHistory.scrollTop = this.chatHistory.scrollHeight;
    this.thinkingEl = el;
  }

  hideThinking(): void {
    this.thinkingEl?.remove();
    this.thinkingEl = null;
  }

  private renderHistory(): void {
    this.chatHistory.innerHTML = "";
    this.thinkingEl = null;
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
    if (text) {
      this.hint.textContent = text;
      this.hint.classList.remove("hidden");
    } else {
      this.hint.classList.add("hidden");
    }
  }
}
