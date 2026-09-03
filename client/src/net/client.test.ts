import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import { NetClient } from "./client";

/**
 * 用注入的假 WebSocket 测网络层行为（不真连服务器）。
 * 锁定审查 C3 的修复：指数退避、destroy 终止、防僵尸连接。
 */

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  readonly readyState = WebSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  sent: string[] = [];
  closed = false;

  constructor() {
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
  }

  // 测试辅助：触发事件
  fireOpen(): void {
    this.onopen?.();
  }

  fireClose(): void {
    this.onclose?.();
  }
}

describe("NetClient", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("断线重连按指数退避：1s → 2s → 4s", () => {
    vi.useFakeTimers();
    const nc = new NetClient("ws://x", () => {});
    nc.connect();
    const first = FakeWebSocket.instances[0];
    first.fireClose(); // 第 1 次断线 → 1s 后重连
    expect(vi.getTimerCount()).toBe(1);
    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances.length).toBe(2);
    FakeWebSocket.instances[1].fireClose(); // 第 2 次 → 2s
    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances.length).toBe(2); // 1s 不够
    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances.length).toBe(3);
    FakeWebSocket.instances[2].fireClose(); // 第 3 次 → 4s
    vi.advanceTimersByTime(2000);
    expect(FakeWebSocket.instances.length).toBe(3);
    vi.advanceTimersByTime(2000);
    expect(FakeWebSocket.instances.length).toBe(4);
    nc.destroy();
    vi.useRealTimers();
  });

  it("重连成功后退避归零：下次断线又从 1s 起步", () => {
    vi.useFakeTimers();
    const nc = new NetClient("ws://x", () => {});
    nc.connect();
    FakeWebSocket.instances[0].fireClose();
    vi.advanceTimersByTime(1000); // 重连了（实例 2）
    FakeWebSocket.instances[1].fireOpen(); // 成功 → 退避归零
    FakeWebSocket.instances[1].fireClose();
    vi.advanceTimersByTime(1000); // 归零后应 1s 就重连
    expect(FakeWebSocket.instances.length).toBe(3);
    nc.destroy();
    vi.useRealTimers();
  });

  it("destroy：断开当前连接、取消挂起重连、后续不再重连", () => {
    vi.useFakeTimers();
    const nc = new NetClient("ws://x", () => {});
    nc.connect();
    const first = FakeWebSocket.instances[0];
    first.fireClose();
    expect(vi.getTimerCount()).toBe(1); // 有挂起重连
    nc.destroy();
    expect(vi.getTimerCount()).toBe(0); // 重连已取消
    expect(first.closed).toBe(true); // 旧连接已关
    // destroy 后再触发 close 事件（回调已解绑）与再调 connect 都不应建新连接
    first.fireClose();
    nc.connect();
    expect(FakeWebSocket.instances.length).toBe(1);
    vi.useRealTimers();
  });

  it("connect 前关闭旧连接：不累积僵尸 WebSocket", () => {
    const nc = new NetClient("ws://x", () => {});
    nc.connect();
    const first = FakeWebSocket.instances[0];
    nc.connect(); // 场景重建等场景下的重复 connect
    expect(FakeWebSocket.instances.length).toBe(2);
    expect(first.closed).toBe(true); // 旧实例被关闭
    nc.destroy();
  });

  it("消息校验：坏 JSON 与非协议消息被忽略，合法消息回调", () => {
    const onMessage = vi.fn();
    const nc = new NetClient("ws://x", onMessage);
    nc.connect();
    const ws = FakeWebSocket.instances[0];
    ws.onmessage?.({ data: "{not json" });
    ws.onmessage?.({ data: JSON.stringify({ type: 1, payload: {} }) }); // type 非字符串
    ws.onmessage?.({
      data: JSON.stringify({ type: "world_state", payload: { x: 1 } }),
    });
    expect(onMessage).toHaveBeenCalledTimes(1);
    expect(onMessage.mock.calls[0][0]).toEqual({
      type: "world_state",
      payload: { x: 1 },
    });
    nc.destroy();
  });
});
