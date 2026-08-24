// WebSocket 客户端：连接、消息收发、断线重连
// 设计说明：网络收发只活在 net/ 这一层，场景通过回调消费消息——
// 这样以后加消息校验、换协议都不用动游戏逻辑。

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

export class NetClient {
  private ws: WebSocket | null = null;
  private readonly retryDelayMs = 1000;
  private readonly url: string;
  private readonly onMessage: (msg: ServerMessage) => void;
  private readonly onOpen: () => void;

  constructor(url: string, onMessage: (msg: ServerMessage) => void, onOpen: () => void = () => {}) {
    this.url = url;
    this.onMessage = onMessage;
    this.onOpen = onOpen;
  }

  connect(): void {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => this.onOpen();
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
      // 简单重连：本地开发够用，断线 1 秒后重试
      setTimeout(() => this.connect(), this.retryDelayMs);
    };
  }

  send(type: string, payload: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type, payload }));
    }
  }
}
