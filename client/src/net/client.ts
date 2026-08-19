// WebSocket 客户端：连接、消息收发、断线重连
// 设计说明：网络收发只活在 net/ 这一层，场景通过回调消费消息——
// 这样以后加消息校验、换协议都不用动游戏逻辑。

export interface ServerMessage {
  type: string;
  payload: Record<string, unknown>;
}

export class NetClient {
  private ws: WebSocket | null = null;
  private readonly retryDelayMs = 1000;
  private readonly url: string;
  private readonly onMessage: (msg: ServerMessage) => void;
  private readonly onOpen: () => void;

  constructor(
    url: string,
    onMessage: (msg: ServerMessage) => void,
    onOpen: () => void = () => {},
  ) {
    this.url = url;
    this.onMessage = onMessage;
    this.onOpen = onOpen;
  }

  connect(): void {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => this.onOpen();
    this.ws.onmessage = (event) => {
      // TODO(W2): 加运行时消息校验（AGENTS.md 类型安全约定）
      const msg = JSON.parse(event.data as string) as ServerMessage;
      this.onMessage(msg);
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
