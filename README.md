# 🏘️ AI 小镇（AI Town）

> 一个 AI 居民自主生活、社交的 2D 像素小镇。你以居民身份走进小镇，与 7 位有独立人设的 AI 居民自由对话，影响小镇的发展——**世界在你离开时也活着，居民记得你做过的事，故事是涌现而非剧本**。

![游戏画面](docs/screenshot.png)

*7 位 AI 居民按各自的日程在镇上自主活动；走近居民按 Enter 即可开始自由对话。*

![Phaser](https://img.shields.io/badge/Phaser-4.2-7b4dff?logo=phaser)
![TypeScript](https://img.shields.io/badge/TypeScript-6.0-3178c6?logo=typescript&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12+-3776ab?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-记忆流-003b57?logo=sqlite&logoColor=white)

不只是旁观 AI 社会的模拟，而是**亲自住进去**。与 Character.AI 类"一对一聊天框"的区别在于**世界感**——居民有自己的日程、会在路上偶遇聊天、会记得上周你帮忙试吃过新配方面包，第二天在广场和别的居民聊起你。

## ✨ 核心特性

| 特性 | 说明 |
|---|---|
| 🤖 **自主行为循环** | 感知 → 检索记忆 → 计划 → 行动。每天 07:00 每位居民生成当日计划，A\* 寻路自主移动，行为符合各自人设 |
| 💬 **相遇涌现对话** | 居民相遇自动触发对话：一方邀请 → 另一方 LLM 决定接受/拒绝 → 逐回合状态机推进，路过的居民可以申请加入 |
| 🧠 **记忆流** | 一切皆记忆（对话/移动/事件/反思统一入库）。检索用三要素加权：时间近因 + 重要性 + 关键词命中，不上向量库 |
| 🌙 **每日反思** | 每天 23:00 用旗舰层模型复盘当日记忆，生成 1–2 条高层认知写回记忆流——关系与性格随时间自然生长 |
| 🎮 **玩家自由对话** | 走近居民输入任意文本（非预设选项），回应带记忆上下文；可插话进行中的群聊 |
| 📜 **小镇播报 + 编年史** | 实时事件日志 UI 让"涌现"被看见；全部交互逐句全文落盘 `saves/chronicle.jsonl` |
| 💰 **成本第一原则** | **事件驱动，居民无事可做时零 LLM 调用**；人设前缀逐字固定以吃满 Prompt 缓存（缓存命中价 = 输入价 1/5） |

### 小镇的居民

7 位居民各有姓名、职业、性格与关系网，人设定档入库（`residents` 表）：

- **林师傅**（面包师）× **小豆子**（面包店学徒）——清晨面包店早市的师徒
- **苏晚**（图书管理员）——毒舌但心软
- **阿茉**（花匠）——屋后菜园直供红姐的餐馆
- **红姐**（餐馆老板娘）——夜晚全镇的聚集点
- **老周**（退休邮差）——全镇的移动信息流
- **老宋**（杂货店主兼木匠）——慢条斯理，一诺千金

每周三供货日，南道的货车会带来面粉、花种和消息——这是小镇与外界联系的命脉。

## 🏗️ 架构

```
┌──────────────┐  WebSocket (world_state / player_move / player_chat / event_log)
│  Phaser 4 前端 │◄────────────────────────────────────────────►┌─────────────┐
│  TownScene    │                                              │   FastAPI    │
│  插值渲染/y-sort│                                              │   main.py    │
└──────────────┘                                              └──────┬──────┘
                                                                     │
                                                        ┌────────────▼─────────────┐
                                                        │      WorldEngine 主循环     │
                                                        │   （游戏时钟 1 现实秒=1 游戏分）│
                                                        └────────────┬─────────────┘
                             事件驱动：无事发生 = 零 LLM 调用                │
          ┌──────────────────────┬───────────────────────┬──────────────┴─────┐
          ▼                      ▼                       ▼                    ▼
    07:00 计划生成          居民相遇/玩家对话           23:00 每日反思      小镇编年史
    agents/planner.py      agents/dialogue.py        agents/reflection.py  world/chronicle.py
    （LLM 轻量层）          （对话状态机+LLM）          （LLM 旗舰层）       （纯落盘，零 LLM）
          │                      │                       │
          └──────────────────────┴───────────┬───────────┘
                                             ▼
                                  🧠 记忆流 memory/（SQLite）
                              store：一切皆记忆    retrieve：三要素加权 Top-K
                                             │
                                             ▼
                                  LLM 网关 llm/client.py
                            统一 chat(prompt, tier) 接口（MiniMax，OpenAI SDK 兼容）
                          轻量层 M2.7（对话/计划）· 旗舰层 M3（反思）· Prompt 缓存友好
```

**几个关键设计决策**（完整版见 [docs/TechDesign-AITown-MVP.md](docs/TechDesign-AITown-MVP.md)）：

1. **事件驱动而非逐 tick 调 LLM** —— 只有"到了生成计划的时刻 / 居民相遇 / 玩家说话 / 到了反思的时刻"这些离散事件才触发 LLM 调用。居民在地图上走路、播放动画全是确定性的本地逻辑，**成本下限是结构保证的，不是祈祷出来的**。
2. **人设前缀逐字固定** —— 每位居民的 prompt 以固定人设前缀开头（存 `residents.prompt_prefix`），保证 Prompt 缓存命中（MiniMax 缓存读取价 = 输入价 1/5）。改一个字都会毁掉缓存命中率。
3. **一切皆记忆** —— 对话摘要、移动观察、事件、反思统一进 `memories` 一张表，检索接口统一；而**逐句全文**另有编年史 `chronicle.jsonl` 落盘——摘要给"机器"检索用，全文给"人"回看用，职责分离。
4. **游戏主循环与 AI 循环解耦** —— 引擎每 1/3 秒一拍（移动/碰撞/广播），LLM 调用全部是后台 asyncio task，任何模型超时都有符合人设的降级托词兜底，游戏永不因 LLM 卡住。
5. **地图前后端共读** —— `town_map.json` 是唯一事实源：前端渲染与后端 A\* 寻路共读同一份碰撞数据，永远不会出现"前端看见的路，后端认为走不通"。

## 🚀 快速开始

**前置要求**：Node.js ≥ 20、Python ≥ 3.12、[MiniMax 开放平台](https://platform.minimaxi.com/) API Key。仅桌面浏览器（Chrome/Safari），本地运行，不需要公网部署。

```bash
# 1. 克隆
git clone https://github.com/Rigel97/AITown.git
cd AITown

# 2. 配置 API Key（项目根目录创建 .env，已被 gitignore 永不入库）
echo "MINIMAX_API_KEY=你的key" > .env

# 3. 安装依赖
cd client && npm install && cd ..
cd server && pip install -r requirements.txt && cd ..

# 4. 一键启动（后端 :8000 + 前端 :5173）
./start.sh
```

打开 http://localhost:5173 即可进入小镇。

## 🎮 操作

| 按键 | 功能 |
|---|---|
| 方向键 | 移动 |
| `Enter` | 走近居民对话 / 插话进行中的群聊 |
| `Esc` | 关闭对话面板 |
| `1` / `2` / `3` | 缩放 ×2 / ×1.5 / ×1 |
| `B` | 暖色滤镜开关 |

其他交互：屏幕右侧「📜 小镇播报」实时滚动事件日志；居民头顶名牌显示当前动作 emoji。

## 📁 项目结构

```
AITown/
├── client/                  # Phaser 4 + Vite + TypeScript 前端
│   ├── src/scenes/          #   TownScene：渲染/插值/碰撞/相机
│   ├── src/ui/              #   HUD（对话面板/事件日志，DOM 层）
│   ├── src/net/             #   WebSocket 客户端（消息显式类型+运行时校验）
│   ├── src/world/           #   地图数据/动作 emoji 映射
│   └── scripts/             #   地图转换管线（ai-town 源数据 → 前后端共读 JSON）
├── server/                  # FastAPI 后端
│   ├── agents/              #   智能体循环：planner / dialogue / reflection / resident
│   ├── memory/              #   记忆流：store（写入）/ retrieve（三要素检索）
│   ├── llm/                 #   LLM 网关：统一 chat(prompt, tier)，换 provider 只改这里
│   ├── world/               #   engine（主循环）/ clock / pathfinding / chronicle
│   ├── db/                  #   schema.sql + seed.py（7 居民人设定档）
│   └── tests/               #   57 个 pytest 用例
├── docs/                    # PRD / TechDesign / 研究报告
└── saves/                   # 玩家存档区（gitignore）：记忆库 + chronicle.jsonl 编年史
```

## ✅ 测试

```bash
cd server && pytest          # 57 个用例：记忆检索/对话解析/寻路/引擎/WebSocket 集成
cd client && npm test        # vitest：动作 emoji 映射等前端单元
```

覆盖原则：记忆流三要素排序、对话解析白名单（挡旁白和 LLM 编造的第三人）、全站位点 A\* 互通、编年史接线（每类交互至少一个用例锁死）。

## 🙏 素材致谢

- 城镇瓦片地图与角色精灵：[a16z ai-town](https://github.com/a16z-infra/ai-town)（mage3 城镇图 + 32x32folk，MIT）
- 部分装饰素材：[Kenney](https://kenney.nl/)（CC0）
- 架构灵感：Generative Agents 论文 *Generative Agents: Interactive Simulacra of Human Behavior*

## 🗺️ 路线图

- [x] Phase 1 骨架：地图/移动/时钟/WebSocket/MiniMax 打通
- [x] Phase 2 核心特性：自主行为循环、记忆流、相遇对话、玩家对话、播报与编年史
- [ ] Phase 3 打磨：存档/读档、出戏防护、成本实测校准（目标单游戏日 ≤ ¥2.1）
- [ ] Phase 4 "上线"：朋友演示 + 连续 7 天主动玩验证

---

*这是个学习/作品集型项目——用最小可行架构验证"AI 居民自主生活"这件事。欢迎 Issue 交流。*
