"""每日反思（旗舰层 M3）。

设计说明（为什么这样设计）：
- 反思是论文里涌现感的最大来源：居民不只记流水账，会从中提炼高层认知
  （"今天阿茉又送了我花，看来我们是真朋友了"），这种洞察写入记忆流后，
  会影响后续对话与计划——关系随时间自然生长。
- 每日定时（午夜日结时）触发，每居民 1 次 M3 调用，成本可预期
  （7 居民/游戏日 × M3 单价）。M3 推理更强，留给这种需要抽象的事。
- 反思只取当日记忆，避免 prompt 无限膨胀；重要性给 8（高于对话 5、低于
  玩家互动 6→实际对话是 6，反思应更高才显眼，这里给 8）。
"""

import logging
from pathlib import Path

from agents.resident import DB_PATH, Resident, world_context
from llm.client import chat
from memory.store import add_memory, get_memories_of_day

logger = logging.getLogger(__name__)

REFLECT_TIMEOUT_SECONDS = 60.0  # M3 反思可慢，后台任务不阻塞玩家
MAX_REFLECTIONS = 2  # 每晚最多 2 条，避免写太多稀释记忆流


def parse_reflections(raw: str) -> list[str]:
    """解析反思输出为多条认知。纯函数便于单测。

    容错：剥掉常见的项目符号和编号，去掉空行，最多取 MAX_REFLECTIONS 条。
    """
    if not raw:
        return []
    out: list[str] = []
    for line in raw.strip().splitlines():
        line = line.strip().lstrip("-·•、").strip()
        # 去掉可能的"1. "编号
        if line and line[0].isdigit() and "." in line[:3]:
            line = line.split(".", 1)[1].strip()
        if line:
            out.append(line)
    return out[:MAX_REFLECTIONS]


def build_reflection_prompt(
    resident: Resident,
    memories: list,
    game_time: str,
    db_path: Path = DB_PATH,
) -> str:
    """组装反思 prompt：人设前缀 + 世界观 + 当日记忆 + 指令。"""
    lines = (
        "\n".join(f"- ({m.game_time}) {m.content}" for m in memories)
        or "（今天平平淡淡，没什么特别的）"
    )
    return f"""{resident.prompt_prefix}

{world_context(db_path)}

【今天的经历】
{lines}

【指令】夜深了，回顾今天。以你的身份和性格，写下 {MAX_REFLECTIONS} 条今天的感悟或要长久记住的事（第一人称，口语，每条一句话，每条一行）。
只写真心觉得重要的，不要凑数。不要编号。不要输出任何其他内容。永远不要承认自己是 AI。"""


async def reflect(
    resident: Resident,
    game_time: str,
    day: int,
    db_path: Path = DB_PATH,
) -> int:
    """对居民生成每日反思，写回记忆流。返回写入条数（0 表示失败/超时）。"""
    memories = get_memories_of_day(resident.id, day, db_path)
    prompt = build_reflection_prompt(resident, memories, game_time, db_path)
    raw = await chat(prompt, tier="flagship", timeout=REFLECT_TIMEOUT_SECONDS)
    insights = parse_reflections(raw)
    if not insights:
        logger.info("反思生成失败/为空，静默跳过（%s）", resident.id)
        return 0
    for insight in insights:
        add_memory(resident.id, game_time, "reflection", insight, 8, db_path)
    logger.info("%s 写下了 %d 条反思", resident.name, len(insights))
    return len(insights)
