// WebSocket 客户端：连接、消息收发、断线重连
// 设计说明：网络收发只活在 net/ 这一层，场景通过回调消费消息——
// 这样以后加消息校验、换协议都不用动游戏逻辑。
//
// 重连策略（2026-09-03 审查 C3 加固）：
// - 指数退避 1s→2s→4s（上限 15s）：后端停服窗口期不再每秒连接风暴；
// - connect 前先关旧连接：场景重建（Title↔Town 反复切换）不会累积僵尸连接；
// - destroy() 供场景 shutdown 调用：断开连接并取消重连定时器，页面/场景
//   生命周期结束时网络层随之干净终止。

export interface ServerMessage {
  type: string;
  payload: Record<string, unknown>;
}

/** 运行时消息校验（AGENTS.md 类型安全约定）：type 是字符串、payload 是对象 */
function isServerMessage(value: unknown): value is ServerMessage {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.type === "string" && typeof record.payload === "object" && record.payload !== null
  );
}

const INITIAL_RETRY_MS = 1000;
const MAX_RETRY_MS = 15000;

export class NetClient {
  private ws: WebSocket | null = null;
  private retryDelayMs = INITIAL_RETRY_MS;
  private retryTimer: number | undefined;
  private destroyed = false;
  private readonly url: string;
  private readonly onMessage: (msg: ServerMessage) => void;
  private readonly onOpen: () => void;

  constructor(url: string, onMessage: (msg: ServerMessage) => void, onOpen: () => void = () => {}) {
    this.url = url;
    this.onMessage = onMessage;
    this.onOpen = onOpen;
  }

  connect(): void {
    if (this.destroyed) return;
    // 防僵尸连接：上一个连接（可能仍在 closing 中）先显式解绑并关闭
    if (this.ws) {
      this.teardownSocket(this.ws);
    }
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      // 连接成功：退避归零，下次断线从初始间隔重试
      this.retryDelayMs = INITIAL_RETRY_MS;
      this.onOpen();
    };
    this.ws.onmessage = (event) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(event.data as string);
      } catch {
        console.warn("收到无法解析的服务器消息，已忽略");
        return;
      }
      if (!isServerMessage(parsed)) {
        console.warn("收到不符合协议的消息，已忽略:", parsed);
        return;
      }
      this.onMessage(parsed);
    };
    this.ws.onclose = () => {
      if (this.destroyed) return;
      // 指数退避重连：本地开发 1s 起步，服务长时间不可达时逐步放缓。
      // 用全局 setTimeout（测试 node 环境无 window）；返回值类型在两环境一致
      this.retryTimer = setTimeout(() => this.connect(), this.retryDelayMs);
      this.retryDelayMs = Math.min(this.retryDelayMs * 2, MAX_RETRY_MS);
    };
  }

  /** 场景销毁时调用：断开连接、取消挂起的重连，网络层彻底终止 */
  destroy(): void {
    this.destroyed = true;
    if (this.retryTimer !== undefined) {
      clearTimeout(this.retryTimer);
      this.retryTimer = undefined;
    }
    if (this.ws) {
      this.teardownSocket(this.ws);
      this.ws = null;
    }
  }

  send(type: string, payload: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type, payload }));
    }
  }

  /** 解绑回调并关闭（onclose 已解绑，不会再触发重连定时器） */
  private teardownSocket(ws: WebSocket): void {
    ws.onopen = null;
    ws.onmessage = null;
    ws.onclose = null;
    ws.close();
  }
}
