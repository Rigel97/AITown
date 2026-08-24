"""LLM 调用统一接口（MiniMax 适配器）。

设计说明（为什么这样设计）：
- 全项目只允许通过 chat(prompt, tier) 调 LLM——换模型/换 provider 只改这一个文件，
  业务代码不感知任何 MiniMax SDK 细节。
- tier 分层是成本控制手段：light = MiniMax-M2.7 干杂活（对话/计划/感知判断），
  flagship = MiniMax-M3 只做每日反思。两者标准价相同，分层是为了控制旗舰层调用次数。
- 失败/超时返回空串而不抛异常：降级文案由调用方按人设生成（如"抱歉，我有点走神"），
  保证 LLM 原始报错永远不会进入游戏内文案（PRD 质量红线）。
- MiniMax 已核实兼容 OpenAI SDK，用 openai 包改 base_url 接入即可。
"""

import asyncio
import logging
import os
import re
import time
from typing import Any, Literal

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

logger = logging.getLogger(__name__)

LIGHT_MODEL = os.getenv("MINIMAX_LIGHT_MODEL", "MiniMax-M2.7")
FLAGSHIP_MODEL = os.getenv("MINIMAX_FLAGSHIP_MODEL", "MiniMax-M3")
# 玩家对话专用模型：与 light 分开是为了能单独试更快的模型（如 M2.7-highspeed）
# 而不影响计划生成，出问题好归因。默认回退到 LIGHT_MODEL。
CHAT_MODEL = os.getenv("MINIMAX_CHAT_MODEL", LIGHT_MODEL)
# 玩家对话目标 < 5 秒，留 0.5 秒余量给网络与解析
CHAT_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "4.5"))

_client = AsyncOpenAI(
    api_key=os.getenv("MINIMAX_API_KEY", ""),
    base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
)

# ---------- 熔断器（成本安全阀，2026-08-21 深检 T） ----------
# MiniMax 持续故障（key 失效/额度耗尽/服务不可用）时，无限重试只会白烧钱
# 且拖慢所有对话。连续失败达阈值 → 暂停调用一段时间，期间一切降级处理；
# 冷却后自动半开重试（成功则清零计数）。
BREAKER_FAILURE_THRESHOLD = 5
BREAKER_COOLDOWN_SECONDS = 60.0
_consecutive_failures = 0
_breaker_open_until = 0.0  # time.monotonic 时刻；在此之前所有调用直接降级

# ---------- 用量统计（W4 成本校准的数据源） ----------
# 目标"单游戏日 ≤ ¥2.1"的唯一度量：缓存命中率 = cached_tokens / prompt_tokens。
# 进程内存累计（重启归零）——MVP 不入库，够 W4 校准用。
_usage_totals: dict[str, Any] = {
    "calls": 0,
    "failures": 0,
    "prompt_tokens": 0,
    "cached_tokens": 0,
    "completion_tokens": 0,
    "by_tier": {},
}

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _strip_think(content: str) -> str:
    """剥掉 M2.7 输出里的 <think> 思考块（2026-08-14 实测发现）。

    为什么：思考块若混进对话气泡会瞬间出戏（PRD 红线）。
    注意：思考 token 仍计费，所以 prompt 要尽量短、靠缓存摊薄成本。
    """
    return _THINK_RE.sub("", content).strip()


def usage_stats() -> dict[str, Any]:
    """累计 LLM 用量快照（深拷贝，避免调用方篡改内部计数）。"""
    return {
        "calls": _usage_totals["calls"],
        "failures": _usage_totals["failures"],
        "prompt_tokens": _usage_totals["prompt_tokens"],
        "cached_tokens": _usage_totals["cached_tokens"],
        "completion_tokens": _usage_totals["completion_tokens"],
        "by_tier": {
            tier: dict(stat) for tier, stat in _usage_totals["by_tier"].items()
        },
    }


def _record_usage(tier: str, model: str, usage: Any) -> None:
    """累计一次成功调用的 token 用量；usage 缺字段时按 0 处理（兼容格式差异）。"""
    prompt = getattr(usage, "prompt_tokens", None) or 0
    completion = getattr(usage, "completion_tokens", None) or 0
    # cached_tokens 兼容两种返回格式：OpenAI 风格挂在 prompt_tokens_details，
    # 或直接挂在 usage 上（MiniMax 文档两种都出现过）
    cached = getattr(
        getattr(usage, "prompt_tokens_details", None), "cached_tokens", None
    )
    if cached is None:
        cached = getattr(usage, "cached_tokens", None)
    cached = cached or 0

    totals = _usage_totals
    totals["calls"] += 1
    totals["prompt_tokens"] += prompt
    totals["cached_tokens"] += cached
    totals["completion_tokens"] += completion
    tier_stat = totals["by_tier"].setdefault(
        tier,
        {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0},
    )
    tier_stat["calls"] += 1
    tier_stat["prompt_tokens"] += prompt
    tier_stat["cached_tokens"] += cached
    tier_stat["completion_tokens"] += completion
    logger.info(
        "LLM usage: tier=%s model=%s prompt=%d cached=%d completion=%d",
        tier,
        model,
        prompt,
        cached,
        completion,
    )


async def chat(
    prompt: str,
    tier: Literal["light", "flagship", "chat"] = "light",
    timeout: float | None = None,
) -> str:
    """统一 LLM 调用入口。失败返回空串，由调用方决定降级文案。

    timeout 默认 CHAT_TIMEOUT_SECONDS（4.5s，保玩家对话 < 5s 目标）；
    后台任务（计划/反思）无硬延迟要求，调用方应显式传更长的值——
    M2.7 带 think 块，长输出在 4.5s 内铁定超时（2026-08-17 实测）。
    熔断打开期间直接返回空串（成本安全阀，见 BREAKER_* 注释）。
    """
    global _consecutive_failures, _breaker_open_until
    if time.monotonic() < _breaker_open_until:
        return ""
    model = {"light": LIGHT_MODEL, "flagship": FLAGSHIP_MODEL, "chat": CHAT_MODEL}[tier]
    try:
        resp = await _client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout if timeout is not None else CHAT_TIMEOUT_SECONDS,
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            _record_usage(tier, model, usage)
        _consecutive_failures = 0
        return _strip_think(resp.choices[0].message.content or "")
    except Exception:
        _usage_totals["failures"] += 1
        _consecutive_failures += 1
        if _consecutive_failures >= BREAKER_FAILURE_THRESHOLD:
            _breaker_open_until = time.monotonic() + BREAKER_COOLDOWN_SECONDS
            logger.error(
                "LLM 连续失败 %d 次，熔断 %0.0f 秒（期间全部调用直接降级）",
                _consecutive_failures,
                BREAKER_COOLDOWN_SECONDS,
            )
        logger.exception("LLM call failed (tier=%s, model=%s)", tier, model)
        return ""


async def _smoke_test() -> None:
    """W1 最小验证：打通第一次调用，实测延迟并打印 usage（看缓存命中）。

    连续发两次相同 prompt：第二次若命中 Prompt 缓存，usage 里应能看到
    缓存读取的 token 数（缓存价 = 输入价 1/5，是成本达标的关键指标）。
    """
    if not os.getenv("MINIMAX_API_KEY"):
        raise SystemExit("请先在 .env 里填入 MINIMAX_API_KEY")

    prompt = "你是 AI 小镇的居民。请用一句话介绍自己。"
    for i in (1, 2):
        start = time.perf_counter()
        model = LIGHT_MODEL
        resp = await _client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=CHAT_TIMEOUT_SECONDS,
        )
        elapsed = time.perf_counter() - start
        print(f"--- 第 {i} 次调用（{model}）---")
        print(f"延迟: {elapsed:.2f}s")
        print(f"回复: {resp.choices[0].message.content}")
        print(f"usage: {resp.usage}")


if __name__ == "__main__":
    asyncio.run(_smoke_test())
