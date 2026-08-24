# AGENTS.md — Master Plan for AI 小镇（AI Town）

<!--
Single source of truth for every AI coding assistant on this project.
Keep it lean — details live in the Context Files at the bottom. Update Current State and Roadmap as you build.
-->

## Project Overview & Stack
**App:** AI 小镇（AI Town，暂定名）
**Overview:** 一个 AI 居民自主生活、社交的 2D 像素小镇（星露谷式温馨治愈风）。玩家以居民身份走进小镇，与 5–10 个有独立人设的 AI 居民自由文本对话并影响小镇发展；世界在玩家离开时也活着，居民记得玩家做过的事，故事是涌现而非剧本。这是学习/作品集型项目（多智能体开发），MVP 目标 4 周内做出"自己愿意每天打开玩"的版本。
**Stack:** Phaser v4 + TypeScript + Vite（前端）/ FastAPI + WebSocket（Python 后端）/ SQLite（数据库 + 记忆流）/ MiniMax API（M2.7 轻量层对话计划 + M3 旗舰层反思，兼容 OpenAI SDK，支持 Prompt 缓存）
**Critical Constraints:** 仅桌面浏览器（Chrome/Safari，不做移动端）；本地运行、不公网部署；**事件驱动——居民无事可做时不产生任何 LLM 调用**（成本第一原则）；人设 prompt 前缀逐字固定以吃 Prompt 缓存（成本达标关键）；MiniMax API key 仅存 `.env`，永不入库；单玩家、无用户系统。

## Setup & Commands
Execute these commands for standard development workflows. Do not invent new package manager commands.
- **Setup:** `cd client && npm install`；`cd server && pip install -r requirements.txt`（用 uv 或 venv 建虚拟环境）
- **Development:** `./start.sh`（一键起前后端）；或手动：后端 `cd server && uvicorn main:app --reload`，前端 `cd client && npm run dev`
- **Testing:** `cd client && npm test`；`cd server && pytest`
- **Linting & Formatting:** `cd client && npm run lint`；`cd server && ruff check . && ruff format --check .`
- **Build:** `cd client && npm run build`（可选：FastAPI 托管静态产物单进程演示）

## Protected Areas 🛡️
Do NOT modify these without explicit human approval:
- **Secrets:** NEVER commit `.env` files or hardcode API keys, tokens, or passwords. Use environment variables and ask the human to set them up.（`MINIMAX_API_KEY` 只存 `.env`）
- **Infrastructure:** `infrastructure/`, Dockerfiles, and deployment workflows (`.github/workflows/`).
- **Database Migrations:** 既有 `server/db/schema.sql` 结构改动需先备份 `aitown.db` 并提出迁移方案；`memories.embedding` 字段为 V2 预留，MVP 恒为 NULL，不得删改。
- **存档数据:** `saves/` 与 `server/db/aitown.db` 是玩家的世界存档，任何破坏性操作前必须确认。

## Coding Conventions
- **Formatting:** 前端 ESLint + Prettier（新代码零警告）；后端 Ruff。
- **Architecture:** layered——服务端按职责分层（`agents/` 智能体循环、`memory/` 记忆流、`llm/` 模型适配、`world/` 主循环），前端按 Phaser 结构分层（`scenes/`、`sprites/`、`ui/`、`net/`）。
- **Testing:** All new utilities get unit tests. Core user flows get integration tests.
- **Type Safety:** Use strict typing. Avoid `any`; define precise interfaces or use `unknown`.（Python 侧函数签名全部带类型标注）

## How I Should Think 🧠
1. **Understand Intent First:** Identify what the user actually needs before answering.
2. **Ask If Unsure:** If critical information is missing, ask ONE specific question before proceeding.
3. **Plan Before Coding:** Propose a brief step-by-step plan and wait for approval before changing more than one file. (If your tool has a plan/reflect mode, use it.)
4. **Execute Incrementally:** Build one feature at a time. Prefer refactoring over rewriting large blocks.
5. **Verify After Changes:** Run tests/linters or manual checks after each logical change; fix failures before moving on (see `REVIEW-CHECKLIST.md`)。前端改动必须在浏览器里实际玩过才算完成。
6. **Explain Trade-offs:** When recommending something, briefly mention alternatives.**学习约定：每个关键模块（尤其 `server/agents/` 和 `server/memory/`）必须附带"为什么这样设计"的解释**——这是用户能向朋友讲清架构的前提。
7. **Remember in Files:** Write state and decisions to `MEMORY.md` instead of relying on chat history.
8. **Use Subagents If Available:** If your tool supports subagents or parallel agents, assign roles and require a plan before edits.

## What NOT To Do ⛔
- Do NOT delete files without explicit confirmation.
- Do NOT modify database schemas without a backup plan.
- Do NOT add features not in the current phase.
- Do NOT skip tests for "simple" changes.
- Do NOT bypass failing tests or pre-commit hooks.
- Do NOT use deprecated libraries or patterns.
- **项目特定：** 不得把智能体循环写成逐 tick 调用 LLM（必须事件驱动）；不得改动居民人设 `prompt_prefix` 的逐字内容（会毁掉 Prompt 缓存命中率）；不得让 LLM 原始报错进入游戏内文案；不得引入需要公网部署或向量数据库的方案（Out of Scope）。

## Engineering Constraints 🏗️
- **Type Safety:** The `any` type is forbidden — use `unknown` with type guards. All function parameters and returns are typed. Validate external input with a runtime schema（前端 WebSocket 消息用显式类型 + 运行时校验；后端用 Pydantic/类型标注）。
- **Architectural Sovereignty:** WebSocket 端点与 Phaser 场景只处理消息收发与渲染，业务逻辑（感知/检索/计划/反思）只活在 `server/agents/`、`server/memory/`；路由层不得直接操作数据库。游戏主循环与 AI 循环解耦，互不阻塞。
- **Library Governance:** Check `package.json` / `requirements.txt` before suggesting new dependencies. Prefer native APIs over libraries. LLM 调用只允许经过 `server/llm/client.py` 的统一 `chat(prompt, tier)` 接口。
- **Clear Communication:** State issues briefly and fix them — no apology loops or filler. If context is missing, ask ONE specific clarifying question. Phaser 相关回答必须按 v4 API，警惕 v3 旧语法。
- **Workflow Discipline:** Pre-commit hooks must pass before commits (or ask before bypassing). If verification fails, fix it before continuing.

## Current State 📍
**Last Updated:** 2026-08-22
**Working On:** Phase 4（Security pass → 朋友演示 → 7 天北极星验证）；Phase 3 全部完成 ✅
**Recently Completed:** Phase 2 全部；2026-08-21 深检修复 14 项；2026-08-22 五轮优化 22 项 + 成本校准实测 ¥0.76/游戏日（目标 ¥2.1 达标）+ 模型选型定案（chat 层回退 M2.7，三层：杂活/对话 M2.7 + 反思 M3），后端 130 测 + 前端 10 测 + lint 零违规，浏览器实测全闭环
**Blocked By:** 无（模型选型已定案，无待拍板项）

## Roadmap 🗺️

### Phase 1: Foundation（W1 骨架）——2026-08-14 全部完成 ✅
- [x] 环境搭建（Node + Vite + Phaser v4；uv + FastAPI；MiniMax key 入 `.env`）
- [x] MiniMax 首次调用打通（M2.7 延迟 2.7–3.8s；Prompt 缓存命中确认；think 标签统一剥离）
- [x] Phaser 加载瓦片地图，玩家方向键可走动（Kenney CC0 素材，碰撞自动化实测精确）
- [x] WebSocket 前后端连通（`world_state` / `player_move` 跑通，3 个集成测试全绿）
- [x] 像素素材来源决策（免费素材包先行：Kenney Tiny Town + Tiny Dungeon）

### Phase 2: Core Features（W2–W3，PRD 7 个 Must-have）
- [ ] 像素小镇地图 + 玩家移动（4–5 个可辨识功能区，不穿墙）
- [ ] AI 居民人设定档（5–10 个，含姓名/职业/性格/背景，入 `residents` 表）
- [ ] 居民自主行为循环（感知→检索→计划→行动，事件驱动，无事不调 LLM）
- [ ] 玩家与居民自由文本对话（有记忆，响应 < 5 秒）
- [ ] 记忆流（SQLite 三表，近因+重要性+关键词 Top-K 检索，老记忆摘要压缩）
- [ ] 居民相遇触发对话（涌现的最低成本来源，优先做）
- [ ] 小镇事件日志流 UI（让"涌现"被看见）

### Phase 3: Polish（W4 成为游戏）
- [ ] 每日简化反思（flagship 层 M3，1–2 条高层认知写回记忆流）
- [x] 存档/读档（关掉重开世界连续）（2026-08-22：autosave 60s + 停服存 + 启动读档 + WS save 协议；重启零 LLM 重烧，实测 kill→重启时钟/位置/计划全连续）
- [ ] 出戏防护与降级打磨（"永不承认自己是 AI"；超时降级为符合人设的托词）（2026-08-22：出戏防线 anti_ai_guard + 托词池年龄中性重写 + 反思重试已落地，实测"你是 AI 吗"→苏晚"什么AI？我就一整理书的。"；剩持续观察新人设极端试探）
- [x] 错误处理补全（LLM 失败/超时降级路径）（2026-08-22：五条 LLM 路径全部有重试/降级/熔断；WS 单消息异常隔离——一条坏消息只丢该条不再断连；重连清悬挂占位符）
- [x] 成本实测校准（目标单次游戏日 ≤ $0.30 ≈ ¥2.1）（2026-08-22：11.5 游戏小时空转实测外推 **¥0.76/游戏日**，达标 36%；缓存命中 26%（非假设的 70%）但事件驱动把调用量压到 ~110 calls/日才是达标主因；数据与发现详见 MEMORY.md 成本校准决策记录）

### Phase 4: Launch（本地"上线"）
- [ ] Security pass (see `REVIEW-CHECKLIST.md`)
- [ ] 3 个朋友完整演示并讲清架构（录屏备份防现场翻车）
- [ ] 开始"连续 7 天主动玩"北极星验证（2026-09-28 前）
- [ ] MVP Completion Checklist 全绿（见 PRD）

## Context Files 📚
Load these only when needed — progressive disclosure keeps context lean:
- `agent_docs/tech_stack.md` — Stack details, libraries, setup commands
- `agent_docs/code_patterns.md` — Architecture and code style rules
- `agent_docs/project_brief.md` — Product vision and conventions
- `agent_docs/product_requirements.md` — Feature list and user stories
- `agent_docs/testing.md` — Test strategy and commands
- `MEMORY.md` — Session memory: decisions, known issues, active goal
- `REVIEW-CHECKLIST.md` — Definition of done before marking work complete
- `docs/` — PRD、TechDesign、研究报告（完整上下文）
- `specs/` — Feature specs and handoff notes created during the build
