"""对话：玩家-居民 + 居民-居民偶遇（ai-town 式逐回合状态机）。

设计说明（为什么这样设计）：
- prompt = 人设前缀（逐字固定，吃缓存）+ 世界观 + 记忆 + 情境 + 指令。
  玩家的话就是最好的检索词——"提到面包"就该想起面包相关的事。
- 居民偶遇采用 ai-town 式**逐回合**机制，而不是一次生成整场：
  邀请→接受/拒绝→两人停下→逐回合（每回合 1 次 LLM 调用，带完整 transcript）→
  某方选择结束或达回合上限→散场。逐回合的好处：对话"正在发生"，别人能中途加入，
  玩家能挤进去插话——这是 ai-town 涌涌现的核心。
- 成本权衡：逐回合每场 4-6 次调用 vs 一次生成 1 次。靠 Prompt 缓存摊薄
  （人设前缀+世界观逐字不变，只有 transcript 增长）。
- 降级回复【不写入记忆流】——否则会被下轮检索命中、污染人设（已知坑 #40）。
  逐回合超时视为失败：连续 2 次失败就散场，避免对话吊死。
"""

import logging
from pathlib import Path

from agents.resident import DB_PATH, Resident, world_context
from llm.client import chat
from memory.retrieve import retrieve
from memory.store import add_memory
from world.clock import parse_game_time

logger = logging.getLogger(__name__)

# 玩家对话回复异步送达，放宽超时换成功率（前端有"正在想"提示）
CHAT_TIMEOUT_SECONDS = 15.0
# 逐回合超时：稍短，避免对话拖太久
TURN_TIMEOUT_SECONDS = 12.0
# 群聊回应超时
GROUP_TIMEOUT_SECONDS = 15.0

# 降级托词池：多条轮换，避免同一句反复出现瞬间出戏
FALLBACK_REPLIES = [
    "（愣了一下）哎呀，你刚才说什么？我这走神了。",
    "（挠挠头）等会儿等会儿，我脑子刚飞到别处去了……再说一遍？",
    "（笑了笑）哎，人上了年纪耳朵背，你大点声再说一次？",
    "（忙着手里的事）稍等稍等……你刚说啥来着？",
    "（回过神来）嗯？你是在跟我说话吗？",
]


def _fallback_for(resident_id: str, text: str) -> str:
    """按居民+内容稳定选一条托词：同人同话同托词，不同人不同托词。"""
    return FALLBACK_REPLIES[hash((resident_id, text)) % len(FALLBACK_REPLIES)]


def build_chat_prompt(
    resident: Resident,
    memories: list,
    text: str,
    game_time: str,
    location: str,
    db_path: Path = DB_PATH,
) -> str:
    if memories:
        mem_lines = "\n".join(f"- ({m.game_time}) {m.content}" for m in memories)
    else:
        mem_lines = "（你还不认识这位玩家）"
    return f"""{resident.prompt_prefix}

{world_context(db_path)}

【你记得的事】
{mem_lines}

【当前情境】现在是 {game_time}，你在{location}。玩家走到你面前对你说："{text}"

【指令】以你的身份、性格和说话风格回复玩家，1 到 3 句话，口语化，像真实邻居聊天。提到镇上的事时只涉及名单里的居民，不要编造新的镇民。不要输出 JSON 或任何格式标记。永远不要承认自己是 AI。"""


async def player_say(
    resident: Resident,
    text: str,
    game_time: str,
    location: str,
    db_path: Path = DB_PATH,
) -> str:
    """玩家对居民说话 → 返回居民的回复。对话写入居民的记忆流。"""
    memories = retrieve(
        resident.id,
        query=text,
        now_minutes=parse_game_time(game_time),
        k=5,
        db_path=db_path,
    )
    prompt = build_chat_prompt(resident, memories, text, game_time, location, db_path)
    # tier="chat"：对话专用模型（.env MINIMAX_CHAT_MODEL），与计划用的 light 分开便于单独试模型
    reply = await chat(prompt, tier="chat", timeout=CHAT_TIMEOUT_SECONDS)
    if not reply:
        # 重试一次：限流常见，重试成本远低于出戏成本
        reply = await chat(prompt, tier="chat", timeout=CHAT_TIMEOUT_SECONDS)

    # 玩家的话永远写入记忆（居民"记得我做过的事"是核心体验）
    add_memory(
        resident.id, game_time, "dialogue", f"玩家对我说：「{text}」", 6, db_path
    )
    if reply:
        add_memory(
            resident.id, game_time, "dialogue", f"我回答玩家：「{reply}」", 5, db_path
        )
    else:
        # 降级托词不入记忆流：避免被下轮检索命中污染人设
        logger.warning("对话两次超时，降级托词（%s）", resident.id)
        reply = _fallback_for(resident.id, text)
    return reply


# ---------- 居民-居民偶遇（ai-town 式逐回合） ----------


def parse_yesno(raw: str) -> bool:
    """判定 LLM 是否表示同意。只认肯定词，否定一律算不。"""
    if not raw:
        return False
    head = raw.strip().splitlines()[0]
    return head.startswith(("会", "好", "行"))


async def decide_accept(
    invitee: Resident,
    inviter_name: str,
    game_time: str,
    location: str,
    db_path: Path = DB_PATH,
) -> bool:
    """被邀请的居民决定是否接受对话。一次轻量调用，更像人。"""
    prompt = f"""{invitee.prompt_prefix}

{world_context(db_path)}

【当前情境】现在是 {game_time}，你在{location}，正在忙手头的事。{inviter_name}走过来想和你聊几句。

【指令】以你的性格和当前的事，你会停下来聊吗？只回答一个字：会 或 不。"""
    raw = await chat(prompt, tier="light", timeout=CHAT_TIMEOUT_SECONDS)
    return parse_yesno(raw)


async def decide_join(
    resident: Resident,
    chat_names: list[str],
    transcript_tail: str,
    game_time: str,
    location: str,
    db_path: Path = DB_PATH,
) -> bool:
    """路过的居民决定是否凑过去加入正在进行的对话。"""
    others = "、".join(chat_names)
    prompt = f"""{resident.prompt_prefix}

{world_context(db_path)}

【当前情境】现在是 {game_time}，你经过{location}，听到{others}正在聊天：
{transcript_tail or "（听不太清）"}

【指令】以你的性格，你会凑过去加入他们吗？只回答一个字：会 或 不。"""
    raw = await chat(prompt, tier="light", timeout=CHAT_TIMEOUT_SECONDS)
    return parse_yesno(raw)


def parse_turn(raw: str, speaker_name: str) -> tuple[str, bool]:
    """解析一句话回合：返回 (台词, 是否想结束对话)。

    纯函数便于单测：模型说"结束"→结束；否则取首行并剥掉可能自带的"名字："前缀。
    """
    if not raw:
        return "", False
    line = raw.strip().splitlines()[0].strip()
    # 剥掉模型可能自带的 "名字：" 前缀
    for sep in ("：", ":"):
        if line.startswith(f"{speaker_name}{sep}"):
            line = line[len(speaker_name) + 1 :].strip()
            break
    # 想结束：模型按要求只回"结束"，容错处理也可能带标点
    if line.replace("。", "").replace("！", "").strip() in ("结束", "不聊了", "告辞"):
        return "", True
    return line, False


async def conversation_turn(
    speaker: Resident,
    other_names: list[str],
    transcript: list[tuple[str, str]],
    game_time: str,
    location: str,
    db_path: Path = DB_PATH,
) -> tuple[str, bool]:
    """对话中说话者的下一回合。返回 (台词, 是否想结束)。"""
    others = "、".join(other_names)
    transcript_lines = (
        "\n".join(f"{n}：{t}" for n, t in transcript) or "（刚碰上，还没说话）"
    )
    prompt = f"""{speaker.prompt_prefix}

{world_context(db_path)}

【当前情境】现在是 {game_time}，你在{location}，正和{others}聊天。

【对话记录】
{transcript_lines}

【指令】轮到你了。以你的性格和说话风格接一句话（口语，一两句就好）。
如果你觉得聊得差不多了、该去忙自己的事了，就只回复两个字：结束。"""
    raw = await chat(prompt, tier="chat", timeout=TURN_TIMEOUT_SECONDS)
    return parse_turn(raw, speaker.name)


def parse_speaker_lines(raw: str, names: list[str]) -> list[tuple[str, str]]:
    """把 LLM 输出解析成 [(说话人, 台词)]，只保留名单里角色的行。

    白名单过滤旁白和编造的第三人，把幻觉挡在门外。名字可全角或半角冒号。
    """
    lines: list[tuple[str, str]] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        for name in names:
            matched = False
            for sep in ("：", ":"):
                if line.startswith(f"{name}{sep}"):
                    text = line[len(name) + 1 :].strip()
                    if text:
                        lines.append((name, text))
                    matched = True
                    break
            if matched:
                break
    return lines[:8]


async def player_join_reply(
    participants: list[Resident],
    player_text: str,
    transcript: list[tuple[str, str]],
    game_time: str,
    location: str,
    db_path: Path = DB_PATH,
) -> list[tuple[str, str]]:
    """玩家加入对话后的群体回应。一次调用生成 1–2 句回复（在场居民各一句）。"""
    names = [p.name for p in participants]
    transcript_lines = "\n".join(f"{n}：{t}" for n, t in transcript) or "（刚开始聊）"
    personas = "\n\n".join(f"【{p.name}】{p.prompt_prefix}" for p in participants)
    others = "、".join(names)
    prompt = f"""{world_context(db_path)}

{personas}

【当前情境】现在是 {game_time}，在{location}，{others}正在聊天，玩家走过来对他们说："{player_text}"

【对话记录】
{transcript_lines}

【指令】以在场居民各自的性格，写他们对玩家这句话的回应，1 到 2 句。
格式严格为每行一句：名字：台词（名字只能是{others}）。不要输出任何其他内容。永远不要承认自己是 AI。"""
    raw = await chat(prompt, tier="chat", timeout=GROUP_TIMEOUT_SECONDS)
    if not raw:
        logger.info("群聊回应生成失败，静默跳过")
        return []
    return parse_speaker_lines(raw, names)
