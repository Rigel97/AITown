"""llm/client.py 的单元测试（不真实调用 API——用假客户端注入）。

为什么 mock：单测要确定性与零成本；真实调用的延迟/缓存验证走 W1/W4 手动实测。
"""

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
