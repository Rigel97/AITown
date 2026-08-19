"""小镇编年史：居民全部交互的逐句全文落盘。

设计说明（为什么这样设计）：
- memories 表是给"机器"的（检索用，存摘要），编年史是给"人"的（回看用，
  存全文）——两者职责不同所以分开放：摘要进 DB 参与检索，全文进 JSONL
  档案。全文若进 memories 会撑爆检索候选集和 Prompt 成本。
- 格式选 JSONL 而非 Markdown：追加安全（多进程/重开不破坏结构）、中文
  原样可读（ensure_ascii=False）、日后能直接机器解析（W4 想做"故事线
  UI"或让 LLM 写小镇周报时是现成数据源）；漂亮排版随时可以从 JSONL
  生成，反向不行。
- 落盘失败只告警、永不抛错：编年史是旁路档案，绝不能因为它打断对话
  或主循环（与游戏主流程解耦）。
- 位置在 saves/（gitignore 的玩家存档区）——它就是世界历史的一部分。
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CHRONICLE_PATH = Path(__file__).resolve().parents[2] / "saves" / "chronicle.jsonl"

# 追加写加锁：engine 主循环与 websocket 端点可能并发写（虽是同一事件循环，
# 但 sqlite 同款防御性处理不亏）
_lock = threading.Lock()


def record(
    kind: str,
    game_time: str,
    payload: dict[str, Any],
    path: Path | None = None,
) -> None:
    """追加一条编年史记录。

    kind ∈ {invite, join, conversation, player_chat}；
    每行一个 JSON 对象，附 game_time（游戏内）与 real_time（现实时间）双时间戳。
    """
    entry = {
        "kind": kind,
        "game_time": game_time,
        "real_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        **payload,
    }
    try:
        target = path or CHRONICLE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        with _lock, open(target, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        # 编年史是旁路：写不进去只告警，不影响游戏
        logger.warning("编年史写入失败", exc_info=True)
