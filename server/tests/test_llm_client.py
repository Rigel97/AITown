"""llm/client.py 的单元测试（不真实调用 API——用假客户端注入）。

为什么 mock：单测要确定性与零成本；真实调用的延迟/缓存验证走 W1/W4 手动实测。
"""

import asyncio

import pytest

from llm import client as llm_client
from llm.client import _strip_think


def test_strip_think_removes_block() -> None:
    raw = "<think>这是一段推理过程</think>\n\n你好呀，今天天气不错。"
    assert _strip_think(raw) == "你好呀，今天天气不错。"


def test_strip_think_passthrough_without_block() -> None:
    assert _strip_think("普通回复") == "普通回复"
    assert _strip_think("") == ""


class _FakeCompletions:
    """模拟 openai 的 chat.completions.create，返回带 <think> 的回复。"""

    async def create(self, **kwargs: object) -> object:
        class Message:
            content = "<think>想一下</think>正经回复"

        class Choice:
            message = Message()

        class Resp:
            def __init__(self) -> None:
                self.choices = [Choice()]

        return Resp()


@pytest.mark.asyncio
async def test_chat_strips_think_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        class chat:
            completions = _FakeCompletions()

    monkeypatch.setattr(llm_client, "_client", FakeClient())
    reply = await llm_client.chat("任意 prompt", tier="light")
    assert reply == "正经回复"


# ---------- 熔断器与用量统计（2026-08-21 深检 T） ----------


def _reset_state(
    monkeypatch: pytest.MonkeyPatch, cooldown: float = 1000.0
) -> dict[str, object]:
    """重置模块级状态（测试间隔离），返回新的用量计数表。"""
    monkeypatch.setattr(llm_client, "_consecutive_failures", 0)
    monkeypatch.setattr(llm_client, "_breaker_open_until", 0.0)
    monkeypatch.setattr(llm_client, "BREAKER_COOLDOWN_SECONDS", cooldown)
    totals: dict[str, object] = {
        "calls": 0,
        "failures": 0,
        "prompt_tokens": 0,
        "cached_tokens": 0,
        "completion_tokens": 0,
        "by_tier": {},
    }
    monkeypatch.setattr(llm_client, "_usage_totals", totals)
    return totals


class _FailingCompletions:
    async def create(self, **kwargs: object) -> object:
        raise RuntimeError("api down")


class _UsageCompletions:
    """带 usage 的假返回：OpenAI 风格 prompt_tokens_details.cached_tokens。"""

    async def create(self, **kwargs: object) -> object:
        class Message:
            content = "回复"

        class Choice:
            message = Message()

        class Details:
            cached_tokens = 80

        class Usage:
            prompt_tokens = 100
            completion_tokens = 20
            prompt_tokens_details = Details()

        class Resp:
            def __init__(self) -> None:
                self.choices = [Choice()]
                self.usage = Usage()

        return Resp()


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续失败达阈值后熔断：后续调用直接降级，不再打 API（成本安全阀）。"""
    create_calls = {"n": 0}

    class CountingFailing:
        async def create(self, **kwargs: object) -> object:
            create_calls["n"] += 1
            raise RuntimeError("api down")

    class FakeClient:
        class chat:
            completions = CountingFailing()

    monkeypatch.setattr(llm_client, "_client", FakeClient())
    _reset_state(monkeypatch, cooldown=1000.0)

    for _ in range(5):
        assert await llm_client.chat("p", tier="light") == ""
    assert create_calls["n"] == 5
    # 熔断已开：再调用不再打 API
    assert await llm_client.chat("p", tier="light") == ""
    assert create_calls["n"] == 5


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """冷却结束后自动恢复：换好客户端后调用成功且计数清零。"""

    class FakeBad:
        class chat:
            completions = _FailingCompletions()

    class FakeGood:
        class chat:
            completions = _FakeCompletions()

    monkeypatch.setattr(llm_client, "_client", FakeBad())
    _reset_state(monkeypatch, cooldown=0.01)  # 极短冷却，测试内自然过期
    for _ in range(5):
        await llm_client.chat("p", tier="light")
    assert llm_client._breaker_open_until > 0  # 熔断已打开

    await asyncio.sleep(0.05)  # 冷却已过
    monkeypatch.setattr(llm_client, "_client", FakeGood())
    assert await llm_client.chat("p", tier="light") == "正经回复"
    assert llm_client._consecutive_failures == 0  # 成功清零计数


@pytest.mark.asyncio
async def test_usage_stats_accumulates(monkeypatch: pytest.MonkeyPatch) -> None:
    """每次成功调用累计 token 用量（含缓存命中数）——W4 成本校准的数据源。"""

    class FakeClient:
        class chat:
            completions = _UsageCompletions()

    monkeypatch.setattr(llm_client, "_client", FakeClient())
    _reset_state(monkeypatch)

    await llm_client.chat("p1", tier="light")
    await llm_client.chat("p2", tier="chat")
    stats = llm_client.usage_stats()
    assert stats["calls"] == 2
    assert stats["prompt_tokens"] == 200
    assert stats["cached_tokens"] == 160
    assert stats["completion_tokens"] == 40
    assert stats["by_tier"]["light"]["calls"] == 1
    assert stats["by_tier"]["chat"]["cached_tokens"] == 80
    # 快照是深拷贝：外部修改不污染内部计数
    stats["by_tier"]["light"]["calls"] = 999
    assert llm_client.usage_stats()["by_tier"]["light"]["calls"] == 1
