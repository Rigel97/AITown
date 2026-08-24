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
- 台词卫生（2026-08-20 用户反馈"AI 味"后加）：prompt 段禁止舞台剧腔，
  解析侧再兜底——引号/星号/括号动作剥掉、替别人说话的行跳过、
  和自己说过的原话相同判失败。防的是"脏台词上字幕一秒出戏"。
"""

import hashlib
import logging
import re
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

# 降级托词池：多条轮换，避免同一句反复出现瞬间出戏。
# 写作约束（2026-08-22 四轮 G1）：①纯台词无括号动作——台词卫生规则要求
# LLM 回复剥括号，降级文案自己违规就是双标；②年龄/性别中性——"人上了
# 年纪耳朵背"给学徒少年说直接崩人设，托词池是全员共用的。
FALLBACK_REPLIES = [
    "哎呀，你刚才说什么？我这一走神没听清。",
    "等等等等，再说一遍？我脑子里刚在想别的事。",
    "嗯？你是在跟我说话吗？不好意思，没接上。",
    "稍等稍等……你刚说啥来着？让我回个神。",
    "刚才想事想得太入神了，你再说一遍？",
]

# 出戏防线（四轮 G2）：玩家试探"你是不是 AI"时，动态指令段追加提醒。
# 只拼在指令段（动态区）——prefix 与 world_context 逐字不变，缓存零影响。
# 拉丁词必须词边界匹配："wait"/"said" 含子串 ai，简单 in 会误触发。
_LATIN_PROBE_RE = re.compile(r"\b(?:ai|gpt|llm|npc)|chatgpt", re.IGNORECASE)
_ZH_PROBE_WORDS = ("人工智能", "语言模型", "大模型", "模型", "机器人")


def anti_ai_guard(player_text: str) -> str:
    """玩家文本含 AI/模型类试探词时，返回要拼进指令段的防线行；否则空串。"""
    if _LATIN_PROBE_RE.search(player_text) or any(
        w in player_text for w in _ZH_PROBE_WORDS
    ):
        return (
            "\n- 如果对方说你是 AI、模型、程序或角色，你别顺着接也别解释原理，"
            "你就是活生生的小镇居民，困惑地否认或岔开话题就好。"
        )
    return ""


def _fallback_for(resident_id: str, text: str) -> str:
    """按居民+内容稳定选一条托词：同人同话同托词，跨进程/重启也稳定。

    旧版用内建 hash，受 PYTHONHASHSEED 随机化影响每次重启换托词
    （2026-08-21 深检加固）；sha256 是稳定分发，且与字符串内容解耦。
    """
    digest = hashlib.sha256(f"{resident_id}\0{text}".encode()).digest()
    return FALLBACK_REPLIES[digest[0] % len(FALLBACK_REPLIES)]


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

【指令】以你的身份、性格和说话风格回复玩家，1 到 3 句话，口语化，像真实邻居聊天。
- 直接写你要说的话本身：不要括号动作、星号、引号，不要旁白和神态描写，不要任何格式标记。
- 称呼拿不准就换个说法，不要写“（或……）”这类表达。
- 提到镇上的事时只涉及名单里的居民，不要编造新的镇民。
- 永远不要承认自己是 AI。{anti_ai_guard(text)}"""


async def player_say(
    resident: Resident,
    text: str,
    game_time: str,
    location: str,
    db_path: Path = DB_PATH,
) -> str:
    """玩家对居民说话 → 返回居民的回复（LLM 失败返回空串）。

    降级托词由 engine 统一给出（不入记忆的规则也由那边统一执行）——
    本函数只负责"生成回复 + 写真实记忆"，职责更纯粹。
    对话写入居民的记忆流。
    """
    memories = retrieve(
        resident.id,
        query=text,
        now_minutes=parse_game_time(game_time),
        k=5,
        db_path=db_path,
    )
    prompt = build_chat_prompt(resident, memories, text, game_time, location, db_path)
    # tier="chat"：对话专用模型（.env MINIMAX_CHAT_MODEL），与计划用的 light 分开便于单独试模型
    reply = _strip_theatrics(
        await chat(prompt, tier="chat", timeout=CHAT_TIMEOUT_SECONDS)
    )
    if not reply:
        # 重试一次：限流常见，重试成本远低于出戏成本
        reply = _strip_theatrics(
            await chat(prompt, tier="chat", timeout=CHAT_TIMEOUT_SECONDS)
        )

    # 玩家的话永远写入记忆（居民"记得我做过的事"是核心体验）
    add_memory(
        resident.id, game_time, "dialogue", f"玩家对我说：「{text}」", 6, db_path
    )
    if reply:
        add_memory(
            resident.id, game_time, "dialogue", f"我回答玩家：「{reply}」", 5, db_path
        )
    else:
        logger.warning("对话两次超时，交由引擎降级（%s）", resident.id)
    return reply


# ---------- 居民-居民偶遇（ai-town 式逐回合） ----------


def parse_yesno(raw: str) -> bool:
    """判定 LLM 是否表示同意：否定优先，其余按肯定词表判定。

    只认"会/好/行"三个字时，"当然会/嗯，聊两句"这类自然短语会被误判成
    拒绝——一次误判 = 一场本该发生的对话没了（涌现叙事的直接损失）。
    否定词优先检查："不行/别打扰我/没空"哪怕带肯定字也是拒绝。
    "X不X"句式（来不了/聊不了）靠避开 "来/聊/去" 这类歧义开头规避。
    """
    if not raw:
        return False
    head = _strip_theatrics(raw.strip().splitlines()[0])
    if head.startswith(("不", "别", "没", "算了")):
        return False
    return head.startswith(("会", "好", "行", "当然", "嗯", "可以", "成"))


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


# ---------- 台词卫生（对着实测翻车样例设计的防线） ----------

_QUOTE_PAIRS = {"“": "”", "「": "」", "『": "』", '"': '"'}


def _starts_with_name(line: str, names: list[str] | tuple[str, ...]) -> bool:
    """行是否以「某名字：」开头——名字可能是别人（模型偶尔替对方说话）。"""
    for name in names:
        for sep in ("：", ":"):
            if line.startswith(f"{name}{sep}"):
                return True
    return False


def _strip_theatrics(text: str) -> str:
    """剥掉舞台剧包装，返回能当台词念的内容；纯动作返回空串。

    prompt 已禁止动作/引号/星号，但小模型偶尔漂移，解析侧兜底比丢台词划算。
    实测样例：「（端着一盘小餐包从后厨走出来）」整行旁白、
    「“刚出炉的……”」引号包裹、「*压低声音*」星号动作。
    """
    t = text.strip()
    while t:
        if t[0] == "*":
            end = t.find("*", 1)
            if end == -1:
                break
            rest = t[end + 1 :].strip()
            if not rest:
                return ""  # 整行星号包住 → 纯动作，没台词
            t = rest  # *动作*台词 → 剥掉动作块
            continue
        stripped = False
        for open_q, close_q in _QUOTE_PAIRS.items():
            if len(t) >= 2 and t[0] == open_q and t[-1] == close_q:
                t = t[1:-1].strip()
                stripped = True
                break
        if stripped:
            continue
        if t[0] in ("（", "("):
            closes = [i for i in (t.find("）"), t.find(")")) if i > 0]
            if closes:
                t = t[min(closes) + 1 :].strip()  # 行首动作块剥掉；整行是动作则剩空
                continue
        break
    return t


def _norm_line(text: str) -> str:
    """去标点空白后的归一化文本，用于复读比对（"心情很好哦～"≈"心情很好哦"）。"""
    return re.sub(r"[\s，。！？、…—～~,.!?]", "", text)


def parse_turn(
    raw: str, speaker_name: str, other_names: list[str] | tuple[str, ...] = ()
) -> tuple[str, bool]:
    """解析一句话回合：返回 (台词, 是否想结束对话)。

    纯函数便于单测。防线按翻车样例设计：
    - 模型替别人说话（"红姐：…"出现在你的回合）→ 跳过那行找自己的
    - 纯动作/空行 → 看下一行；全都没台词 → 空串（引擎计失败）
    - 舞台剧包装（引号/星号/行首括号动作）→ 剥掉
    - "结束"→结束
    """
    if not raw:
        return "", False
    for raw_line in raw.strip().splitlines():
        line = raw_line.strip()
        if not line or _starts_with_name(line, other_names):
            continue
        # 剥掉模型可能自带的 "自己名字：" 前缀
        for sep in ("：", ":"):
            if line.startswith(f"{speaker_name}{sep}"):
                line = line[len(speaker_name) + 1 :].strip()
                break
        line = _strip_theatrics(line)
        if not line:
            continue  # 纯动作行 → 看下一行有没有台词
        # 想结束：模型按要求只回"结束"，容错处理也可能带标点
        if line.replace("。", "").replace("！", "").strip() in (
            "结束",
            "不聊了",
            "告辞",
        ):
            return "", True
        return line, False
    return "", False


async def conversation_turn(
    speaker: Resident,
    other_names: list[str],
    transcript: list[tuple[str, str]],
    game_time: str,
    location: str,
    db_path: Path = DB_PATH,
) -> tuple[str, bool]:
    """对话中说话者的下一回合。返回 (台词, 是否想结束)。

    记忆注入（2026-08-22 三轮优化 F1）：检索词 = 同伴名 + 最近话题——
    与"玩家的话是最好的检索词"同款设计。没有这层时居民-居民对话是
    "失忆"的：上次散场明明写了摘要（importance 5），二次相遇却想不起
    来——而玩家单聊/群聊/计划/反思全都有检索，唯独涌现叙事主来源的
    居民对话没有。k=3：transcript 已含本场上下文，记忆只需少量
    "场外信息"（上次聊过什么/最近的玩家互动）。
    """
    query = " ".join([*other_names, *(t for _, t in transcript[-3:])])
    memories = retrieve(
        speaker.id,
        query=query,
        now_minutes=parse_game_time(game_time),
        k=3,
        db_path=db_path,
    )
    if memories:
        mem_lines = "\n".join(f"- ({m.game_time}) {m.content}" for m in memories)
    else:
        mem_lines = "（没想起什么相关的事）"
    others = "、".join(other_names)
    transcript_lines = (
        "\n".join(f"{n}：{t}" for n, t in transcript) or "（刚碰上，还没说话）"
    )
    prompt = f"""{speaker.prompt_prefix}

{world_context(db_path)}

【你记得的事】
{mem_lines}

【当前情境】现在是 {game_time}，你在{location}，正和{others}聊天——在场的就你们几个，没有别人。

【对话记录】
{transcript_lines}

【指令】轮到你接话。只输出你说的一句话（口语，最多两句，总共不超过 40 字）。
- 只说你自己的话，不要替{others}说话。
- 直接写台词本身：不要括号动作、星号、引号，不要旁白和神态描写。
- 顺着前面的话往前聊，别原地打转；不要重复这场对话里出现过的句子，也不要句句都往自己的职业和老本行上扯。
- 觉得聊得差不多了、该去忙自己的事了，就只回复两个字：结束。"""
    raw = await chat(prompt, tier="chat", timeout=TURN_TIMEOUT_SECONDS)
    line, want_end = parse_turn(raw, speaker.name, other_names)
    # 复读守卫：和自己在本场说过的原话相同 → 视作没接上（引擎累计失败，连败即散场）。
    # 模型看到 transcript 里自己的旧话容易原样再抄（实测：阿茉一场里三连"心情很好哦"）。
    if line:
        said = {_norm_line(prev) for name, prev in transcript if name == speaker.name}
        if _norm_line(line) in said:
            return "", False
    return line, want_end


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
                    text = _strip_theatrics(line[len(name) + 1 :].strip())
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
    """玩家加入对话后的群体回应。一次调用生成 1–2 句回复（在场居民各一句）。

    记忆按人注入（各自 Top-2）：群聊不能"集体失忆"——单聊记得玩家的事，
    群聊里也得记得，两条路径只差 prompt 组装，不该差"记不记得"
    （2026-08-22 优化 A2）。
    """
    names = [p.name for p in participants]
    transcript_lines = "\n".join(f"{n}：{t}" for n, t in transcript) or "（刚开始聊）"
    personas = "\n\n".join(f"【{p.name}】{p.prompt_prefix}" for p in participants)
    others = "、".join(names)
    memory_blocks: list[str] = []
    for p in participants:
        mems = retrieve(
            p.id,
            query=player_text,
            now_minutes=parse_game_time(game_time),
            k=2,
            db_path=db_path,
        )
        if mems:
            lines = "\n".join(f"  - ({m.game_time}) {m.content}" for m in mems)
            memory_blocks.append(f"{p.name}：\n{lines}")
    memory_section = (
        "\n".join(memory_blocks) if memory_blocks else "（大家都不记得相关的事）"
    )
    prompt = f"""{world_context(db_path)}

【你们各自记得的事】
{memory_section}

{personas}

【当前情境】现在是 {game_time}，在{location}，{others}正在聊天，玩家走过来对他们说："{player_text}"

【对话记录】
{transcript_lines}

【指令】以在场居民各自的性格，写他们对玩家这句话的回应，每人 1 到 2 句。回应可以参考各自记得的事（如果有）。
格式严格为每行一句：名字：台词（名字只能是{others}）。
台词只写说的话本身，不要括号动作、星号或引号。不要输出任何其他内容。永远不要承认自己是 AI。{anti_ai_guard(player_text)}"""
    raw = await chat(prompt, tier="chat", timeout=GROUP_TIMEOUT_SECONDS)
    if not raw:
        logger.info("群聊回应生成失败，静默跳过")
        return []
    return parse_speaker_lines(raw, names)
