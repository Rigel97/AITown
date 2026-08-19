# Product Requirements Document: AI 小镇 MVP

## Overview

**Product Name:** AI 小镇（AI Town，暂定名）
**Problem Statement:** 现有 AI 陪伴产品（Character.AI 类）是"一对一聊天框"，缺乏世界感和涌现叙事；Smallville 类学术 demo 只能旁观、无法参与。AI 小镇的定位是两者的交集——**一个 AI 居民自主生活、社交的 2D 像素小镇，玩家以居民身份走进小镇，与居民自由对话并影响小镇发展**。世界在你离开时也活着，居民记得你做过的事，故事是涌现而非剧本。
**MVP Goal:** 1 个月内做出一个**自己愿意每天打开玩**、且能向朋友演示并讲清架构的多智能体作品集项目（学习/作品集型目标，不追求用户量与收入，远期可能商业化）
**Target Launch:** 2026-09-14（自 2026-08-14 起 4 周）

## Target Users

### Primary User Profile
**Who:** 开发者本人（Level C：有 Python 基础、正在学习全栈 + AI 开发）
**Problem:** 想要一个能玩、有趣、有陪伴感的小镇；现有产品要么只能旁观、要么只是聊天框
**Current Solution:** Character.AI 类一对一聊天产品 + 观看 Smallville 类演示视频
**Why They'll Switch:** 不是"切换"而是"自建"——市面上没有"消费级、可参与、有持续运转世界"的 AI 小镇产品

### User Persona: 小陈（开发者本人，假设性命名）
- **Demographics:** 独立开发者，利用业余时间做项目，每周可投入 15–20 小时
- **Tech Level:** Intermediate（有 Python 基础，前端和 AI 应用开发仍在学习）
- **Goals:** 玩到涌现的 AI 故事；掌握多智能体应用开发；产出可写进简历的作品集
- **Frustrations:** 现有 AI 产品无世界感、无长期记忆、无法参与一个活着的世界

### 验证期用户（第二阶段）
朋友 / 独立游戏 & AI 爱好者：技术中高水平，自己打开链接或本地跑起来即可，**不需要新手引导**；目前用 Character.AI / 星野等聊天类产品满足类似需求。远期用户（星露谷玩家 × Character.AI 用户交集）不在 MVP 考虑范围。

## User Journey

### The Story
小陈结束一天的工作，打开浏览器进入 AI 小镇。俯瞰视角下，像素小镇温馨治愈：面包师一早就开了店，两个居民在广场偶遇聊了起来，头顶冒出对话气泡。他用方向键操控自己的小人在镇上逛，走近面包师，敲下一句"今天做了什么好吃的？"——面包师还记得他上周帮忙试吃了新配方，热情地聊了起来。离开时他看到事件日志："花匠和图书管理员因为昨天的争论和好了"。明天他想再来看看，小镇又发生了什么。

### Key Touchpoints
1. **Discovery:** MVP 期无获客——开发者自己用 + 直接向朋友演示/分享
2. **First Contact:** 打开页面即进入小镇俯瞰视图，无注册无引导
3. **Onboarding:** 方向键移动小人，走近居民即可对话（自由文本输入）
4. **Core Loop:** 观察小镇生活 → 与居民互动 → 影响居民与事件 → 写入记忆
5. **Retention:** "小镇是活的，居民还记得我"——想回来看看小镇在自己离开后的变化

## MVP Features

### Core Features (Must Have)

#### 1. 像素小镇地图 + 玩家移动
- **Description:** Phaser v4 加载瓦片像素地图（星露谷式温馨治愈风），玩家用方向键控制角色在小镇中行走，含基础碰撞
- **User Value:** 世界感的载体——一切体验的地基
- **Success Criteria:**
  - 玩家可用方向键在地图上平滑移动，不能穿墙
  - 地图包含至少 4–5 个可辨识的功能区域（如面包店、广场、住宅等）
  - 桌面 Chrome/Safari 流畅运行
- **Priority:** Critical

#### 2. AI 居民（5–10 个，有独立人设与性格）
- **Description:** 每个居民有姓名、职业、性格、背景故事等固定人设（写入 prompt 前缀以吃 LLM 缓存），作为自主行为与对话的根基
- **User Value:** 居民个性鲜明是"涌现故事"和情感联结的来源
- **Success Criteria:**
  - 每个居民有完整人设定档（存数据库，可调可扩展）
  - 同一件事不同居民的反应符合各自性格（可感知差异）
  - 人设固定前缀复用，prompt 缓存命中率可统计
- **Priority:** Critical

#### 3. 居民自主行为（符合人设的日常循环）
- **Description:** 简化 Smallville 架构：感知（周边事件/玩家动作）→ 记忆检索 → 计划（事件驱动，早晨生成当日计划、事件触发时局部重规划）→ 行动（移动/互动）；**事件驱动而非逐 tick 调用，没事干不调 LLM**
- **User Value:** 小镇"活着"的核心——你不在时他们也在过自己的生活
- **Success Criteria:**
  - 居民按当日计划自主移动、做事，行为与人设一致
  - 居民相遇会触发对话，对话写入双方记忆
  - 无事发生时不产生 LLM 调用（成本可控）
- **Priority:** Critical

#### 4. 玩家与居民自由文本对话（有记忆）
- **Description:** 玩家走近居民触发对话，**自由文本输入**（非预设选项）；对话时检索该居民相关记忆（含与玩家的过往互动）注入 prompt；对话内容写入双方记忆流
- **User Value:** 参与感的核心——"居民记得我做过的事"
- **Success Criteria:**
  - 玩家可输入任意文本并得到符合人设的回应
  - 居民能在后续对话中提及此前与玩家的互动
  - 单次对话响应时间 < 5 秒（MiniMax M2.7，假设性目标）
- **Priority:** Critical

#### 5. 记忆流（SQLite）
- **Description:** 所有事件、对话、观察以记忆条目存入 SQLite，含时间戳、重要性评分；检索用简化版三要素：时间近因 + 重要性 + 关键词匹配（MVP 不上向量数据库）
- **User Value:** 一切"记得"与"演变"的基础设施
- **Success Criteria:**
  - 对话、移动、事件均产生记忆条目并持久化
  - 检索接口按近因+重要性+关键词返回 Top-K 相关记忆
  - 老记忆定期摘要压缩，控制 prompt 长度
- **Priority:** Critical

#### 6. 每日简化反思（性格演变的引擎）
- **Description:** 每个游戏日结束，把居民当天记忆压缩成 1–2 条高层认知写入记忆流（如"玩家总喜欢来面包店"），影响后续行为与态度；**性格可随事件演变**，但 MVP 只做每日一次的轻量版（不做论文的递归式反思树）
- **User Value:** 长期陪伴感的来源——居民不是静态 NPC，会被你的行为改变
- **Success Criteria:**
  - 每日反思产出高层认知条目并可被后续检索命中
  - 持续特定互动后，居民对玩家的态度/行为有可感知的变化
  - 反思可用旗舰模型 M3 少量调用（成本可控）
- **Priority:** Critical

#### 7. 存档/读档
- **Description:** 小镇完整状态（居民位置/状态/记忆流/游戏时间）可保存与恢复，SQLite 单文件天然支持
- **User Value:** 持续世界的承诺——关掉浏览器小镇也不会丢
- **Success Criteria:**
  - 可随时存档，重新打开后世界状态完整恢复
  - 读档后居民行为与记忆连续，不出现状态错乱
- **Priority:** Critical

### Nice to Have（时间允许才做）
- **昼夜循环 + 居民作息**：白天活动晚上睡觉，增强世界感
- **居民主动发起话题**：对玩家说"上次你说的那件事……"

## Out of Scope (Not in MVP)
| Feature | Why Wait | Planned For |
|---------|----------|-------------|
| 向量记忆检索 | MVP 关键词检索够用；MiniMax 已确认无 embedding 接口 ✅，向量方案需本地模型，复杂度高 | Version 2 |
| 居民间好感度/关系系统 | 先用记忆与反思近似表达关系变化 | Version 2 |
| 建造/经营玩法 | 偏离"可参与小镇"的核心验证目标 | Version 2+ |
| 多人同镇 | 架构与成本复杂度大增，单机先验证趣味 | Version 2+ |
| 公网部署上线 | 作品集阶段本地演示即可；商业化前再做 | 商业化阶段 |
| 用户系统/付费/增长机制 | 与"作品集 + 自己玩"的目标无关 | 商业化阶段 |
| 移动端适配 | 键盘操控 + 1 个月周期，性价比低 | 视反馈决定 |
| 论文级反思树 / 25 个居民 / 评估实验 | 5–10 个居民 + 每日一次反思足够出效果 | 视效果决定 |

*Why we're waiting: 保持 MVP 聚焦、可在 4 周内做完；先把"活着的小镇 + 可参与"这一核心体验验证扎实。*

## Success Metrics

### Primary Metrics
1. **自己连续 7 天主动打开玩** by 上线后 2 周内（2026-09-28 前）
   - How to measure: 自我记录 + 本地游玩日志
   - Why it matters: 这是"自己愿意玩"目标最直接的证据——不好玩一切免谈

2. **能给 3 个朋友完整演示并讲清架构** by 上线后 2 周内
   - How to measure: 实际完成 3 次演示（现场或录屏）
   - Why it matters: 验证作品集价值——讲得清楚 = 真的掌握了多智能体开发

### Secondary Metrics
- 3 个月内：5–10 个朋友试玩过，收到 ≥ 3 条具体反馈
- 单次游戏日 API 成本 ≤ $0.30（≈¥2.1；按 MiniMax 已核实单价需 ~70%+ 缓存命中率方可达标，假设性目标，W4 实测校准）
- 涌现故事产出：每周至少 1 个"没想到居民会这样"的时刻（自我记录）

## UI/UX Direction

**Design Feel:** 星露谷式温馨治愈像素风
**Inspiration:** Stardew Valley（视觉氛围）、Smallville / a16z AI Town（多智能体小镇形态）

### Key Screens
1. **小镇主视图（游戏画面）**
   - Purpose: 核心体验界面，俯瞰整个小镇
   - Key Elements: 像素地图、玩家角色、居民精灵、对话气泡、相机跟随
   - User Actions: 方向键移动、走近居民发起对话、打开事件日志

2. **对话框（气泡 + 自由输入）**
   - Purpose: 与居民交流
   - Key Elements: 居民头顶气泡展示其发言、底部输入框供玩家自由输入、对话历史滚动区
   - User Actions: 输入任意文本、发送、结束对话离开

3. **小镇事件日志流**
   - Purpose: 了解小镇在自己没盯着时发生了什么（"小镇播报"）
   - Key Elements: 按时间倒序的事件列表（谁做了什么、谁和谁聊了什么）
   - User Actions: 浏览、折叠/展开

### Design Principles
- **温馨治愈优先**：配色、精灵、动画节奏都服务于"慢节奏、有情感联结"的氛围
- **世界感大于 UI**：UI 尽量轻量，让小镇本身成为主角；气泡优先于弹窗
- **涌现可见**：居民间互动、事件要有可观察的外在表现（气泡、日志），让"涌现"被看见

## Technical Considerations

**Platform:** Web（Phaser v4 前端 + FastAPI 后端）
**Responsive:** 仅桌面浏览器，不做移动端适配
**Performance Goals:**
- 页面加载 < 3 秒
- 小镇渲染流畅（目标 60fps）
- 对话响应 < 5 秒（假设性目标）

**Security/Privacy:** MVP 本地运行、单人使用，无敏感数据；MiniMax API key 仅存本地环境变量，不进代码库
**Scalability:** 无并发要求（单玩家）；架构上游戏主循环与 AI 循环解耦，为未来多人留余地

**Browser/Device Support:**
- Chrome、Safari 最新版（桌面）
- 不做移动端、不做平板优化

**已定技术栈（来自研究报告；2026-08-14 更新：LLM 由 DeepSeek 切换为 MiniMax API）：**
| 层 | 选型 |
|---|------|
| 前端 | Phaser v4 + TypeScript + Vite |
| 后端 | FastAPI (Python) + WebSocket |
| 数据库 | SQLite |
| LLM | MiniMax API：轻量层 MiniMax-M2.7（对话/计划），旗舰层 MiniMax-M3（反思）✅ 已核实；支持 Prompt 缓存，兼容 OpenAI/Anthropic SDK |
| 记忆检索 | 近因 + 重要性 + 关键词（不上向量库） |

**成本控制策略（来自研究报告，已适配 MiniMax）：** 事件驱动 > 时钟驱动；人设前缀逐字固定放 prompt 开头吃 Prompt 缓存（已核实 ✅，缓存读取价 = 输入价 1/5，达标关键）；记忆摘要压缩；轻量模型 M2.7 干杂活、旗舰模型 M3 只做反思

## Constraints & Requirements

### Budget
- Development tools: $0/月（VS Code/CatPaw + 免费工具链）
- Hosting/Infrastructure: $0/月（本地运行）
- Third-party services: MiniMax API 按量付费（单价已核实 ✅）：运行期估算 ¥115–212/月（取决于缓存命中率；假设性估算：10 居民 × 40 调用/游戏日 × 60 游戏日/月），开发调试期 ¥20–60/月
- **Total:** ≈ **¥0 固定 + ¥20–212/月浮动**（假设性估算，W4 实测校准）

### Timeline
- MVP Development: 4 周（W1 骨架 → W2 居民过日子 → W3 社交参与 → W4 打磨成游戏）
- Beta Testing: 与 W4 合并（自己试玩 + 朋友演示）
- Launch Target: 2026-09-14

### Technical Constraints
- 开发时间每周 15–20 小时（业余时间）
- 向量检索后置（MiniMax 已确认无 embedding 接口 ✅，MVP 先用关键词检索）
- WebSocket 长连接 → MVP 本地运行规避免费托管层休眠问题
- MiniMax 计费已核实 ✅（按量计费 + Prompt 缓存，无峰谷计价）；成本压力大于原 DeepSeek 方案 → 缓存命中率是达标关键，必要时下调调用量假设

## Open Questions & Assumptions
- **Open question:** 像素美术素材来源——AI 文生图生成 sprite 后手工微调，还是使用免费素材包？（W1 需定）
- **Open question:** 小镇初始居民的具体人设名单（W2 前需定档，可用 LLM 网页版头脑风暴）
- **Open question:** 游戏内时间流速（现实 1 分钟 = 游戏多久？影响调用频率与成本）
- **Assumption:** 5–10 个居民足以产生可感知的涌现行为（研究报告推断，非实测）
- **Assumption:** MiniMax M2.7 的对话/计划质量足够支撑体验（待 W1 首次调用验证）
- **Assumption:** API 成本目标（单次游戏日 ≤ $0.30 ≈ ¥2.1）按已核实单价需 ~70%+ 缓存命中率方可达标，存在超标风险；W4 实测校准，必要时下调调用量假设或上调预算

## Quality Standards

**Code Quality:**
- Use TypeScript when possible — it catches errors early
- Handle errors explicitly — don't hide them
- Test the important paths before launch

**Design Quality:**
- Use consistent colors and spacing (design tokens)
- Check accessibility basics (contrast, labels)
- 像素素材风格统一，不混用不同画风

**What This Project Will NOT Accept:**
- Placeholder content ("Lorem ipsum") at launch
- Features that half-work — complete or cut
- 居民行为明显违反人设（如内向角色无故满场社交）
- 对话中出现"作为 AI 语言模型……"类出戏回复

## Risk Mitigation

| Risk | Impact | Mitigation Strategy |
|------|--------|-------------------|
| LLM 成本超预期（已核实单价下达标需 ~70%+ 缓存命中率） | Medium→High | 事件驱动架构；人设前缀逐字固定吃缓存；W1 验证缓存命中率；W4 实测校准；设月度预算上限 |
| 轻量模型质量不够（行为出戏） | High | W1 首次调用即验证；反思等关键任务升级旗舰模型；提示词迭代 |
| 4 周做不完 7 个 Must 功能 | High | 严格按 W1–W4 路线图；Nice-to-have 全砍；先保"对话+记忆"核心 |
| 涌现行为不明显、小镇显得死板 | High | 人设差异化设计；事件日志让涌现可见；居民相遇对话优先做 |
| Phaser 学习曲线拖慢 W1 | Medium | 官方教程 + Game Agent/MCP 辅助；先用免费素材跑通再美化 |
| 单人项目中途失去动力 | Medium | 每周有可玩产出（W1 就能走动）；把"连续 7 天主动玩"作为北极星 |

## MVP Completion Checklist

### Development Complete
- [ ] 7 个 Must-have 功能全部可用
- [ ] Basic error handling（LLM 调用失败/超时降级）
- [ ] Chrome/Safari 桌面端测试通过
- [ ] 居民行为符合人设抽查通过

### Launch Ready
- [ ] 存档/读档验证通过（关掉重开世界连续）
- [ ] 单次游戏日 API 成本实测 ≤ $0.30
- [ ] 可离线演示（录屏备份，防现场翻车）

### Quality Checks
- [ ] 完整用户旅程走通：进镇 → 逛 → 对话 → 离开 → 次日回来看变化
- [ ] 一个涌现故事产生（如居民间自发互动形成的趣事）
- [ ] 能向朋友讲清架构并现场演示
- [ ] 无 critical bug

## Next Steps

1. **Immediate:** Review and approve this PRD
2. **Next:** Create Technical Design Document (Part 3)——细化感知→检索→计划→行动循环、prompt 结构、SQLite schema、WebSocket 协议
3. **Then:** Set up development environment（Vite + Phaser + FastAPI + MiniMax key）
4. **Build:** 按 W1–W4 路线图实现
5. **Test:** 自己试玩 + 3 个朋友演示
6. **Launch:** 本地"上线"，开始连续 7 天主动玩的验证

---
*Created: 2026-08-14*
*Status: Ready for Technical Design*
*Questions? 项目所有者（开发者本人）*

---
## Handoff Context
<!-- Machine-readable summary for the next workflow step. Do not delete; the next prompt in the workflow reads this block. -->
- Stage: prd
- App name: AI Town (working title)
- User level: C  (A = vibe coder, B = developer, C = in-between)
- Target platform: web (desktop only)
- Budget: 本地运行 ¥0 固定 + MiniMax API 按量付费（单价已核实，估算 ¥20–212/月）
- Timeline: 1 month MVP (launch target 2026-09-14)
- Source files: research-AITown.md → PRD-AITown-MVP.md
---
