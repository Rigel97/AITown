"""游戏内时间：时钟与 game_time 字符串的解析/格式化。

设计说明（为什么这样设计）：
- game_time 统一用 "dayN-HH:MM" 字符串（DB 里也存它）——人类可读，
  直接翻 SQLite 就看得懂；需要比较/计算时经 parse_game_time 转成游戏分钟数。
- 流速旋钮 GAME_MINUTES_PER_REAL_SECOND 只在这里定义，全世界引用同一个——
  流速直接决定居民活动节奏与 LLM 调用频率（成本变量），必须集中可调。
"""

from dataclasses import dataclass

# 流速：现实 1 秒 = 游戏 1 分钟（现实 24 分钟 = 游戏 1 天）
GAME_MINUTES_PER_REAL_SECOND = 1.0
MINUTES_PER_DAY = 24 * 60


def parse_game_time(label: str) -> int:
    """ "day1-08:30" → 自 day1 00:00 起算的游戏分钟数。"""
    day_part, time_part = label.split("-")
    day = int(day_part.removeprefix("day"))
    hh, mm = time_part.split(":")
    return (day - 1) * MINUTES_PER_DAY + int(hh) * 60 + int(mm)


def format_game_time(minutes: int) -> str:
    """游戏分钟数 → "dayN-HH:MM"。"""
    day = minutes // MINUTES_PER_DAY + 1
    rem = minutes % MINUTES_PER_DAY
    return f"day{day}-{rem // 60:02d}:{rem % 60:02d}"


@dataclass
class WorldClock:
    """游戏内时钟。从第 1 天 08:00 开始。"""

    day: int = 1
    minutes: float = 8 * 60

    def tick(self, real_seconds: float) -> None:
        self.minutes += real_seconds * GAME_MINUTES_PER_REAL_SECOND
        while self.minutes >= MINUTES_PER_DAY:
            self.minutes -= MINUTES_PER_DAY
            self.day += 1
            # TODO(W4): 游戏日切换 = 每日反思触发点

    def now_minutes(self) -> int:
        """当前时刻的游戏分钟数（供记忆检索的近因计算）。"""
        return (self.day - 1) * MINUTES_PER_DAY + int(self.minutes)

    def label(self) -> str:
        hour = int(self.minutes) // 60
        minute = int(self.minutes) % 60
        return f"day{self.day}-{hour:02d}:{minute:02d}"
