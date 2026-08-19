# Code Patterns

## Purpose
This file defines the implementation patterns the agent should follow for this project.
Prefer these patterns over inventing new ones. Fill in each section from the Technical Design document.

## Architecture Pattern
- **Primary pattern:** layered——后端按职责分层（`agents/` 感知→检索→计划→行动、`memory/` 记忆流读写与检索、`llm/` 模型适配、`world/` 时钟与事件队列）；前端按 Phaser 结构分层（`scenes/`、`sprites/`、`ui/`、`net/`）。
- **Rule:** Keep domain logic separate from transport/UI concerns.（智能体决策逻辑不得出现在 WebSocket 端点或 Phaser 场景里）
- **Rule:** Reuse existing modules before creating new abstractions.
- **Rule:** 游戏主循环与 AI 循环解耦——Phaser 60fps 渲染，AI 循环事件驱动慢速运转，互不阻塞；**无事发生不调 LLM**。

## Data Fetching
- **Primary approach:** direct server calls——单条 WebSocket 长连接，消息均为 JSON，`type` 字段区分（`player_move` / `player_chat` / `save` / `load` / `world_state` / `bubble` / `chat_reply` / `event_log` / `error`，完整契约见 TechDesign "WebSocket 协议"一节）。
- **Rule:** Do not assume a specific library. Check `tech_stack.md` for the project's chosen approach before fetching data.
- **Rule:** Keep fetch logic out of render functions unless the framework explicitly encourages it.（网络收发只在 `client/src/net/`，场景只消费状态）

## State Management
- **Server state:** FastAPI 世界主循环为唯一权威（居民位置/状态/游戏时间），SQLite 持久化；前端状态以服务端 `world_state` 推送为准。
- **Client state:** Phaser 场景内对象状态（精灵、气泡、日志面板），无额外状态库——MVP 不需要 Redux/Zustand。
- **Forms:** 对话输入框为 Phaser 场景内 UI，自由文本 → `player_chat` 消息。
- **Rule:** Prefer the simplest working approach for MVP scope. Do not add a state library if the framework's built-in state is sufficient.

## Error Handling
- Normalize errors at service/API boundaries — never let raw exceptions reach the UI.（LLM 原始报错绝不进游戏内文案，统一走 `llm/client.py` 降级）
- Never swallow errors silently; always log or surface them.
- Return user-safe messages in the UI; log developer context server-side.（对话超时 → 符合人设的托词；计划失败 → 继续当前动作；反思失败 → 跳过当日、次日重试）
- Use a consistent error shape across all API responses.（WebSocket 侧统一 `{"type": "error", "code", "message"}`）

## Validation
- Validate all external inputs (user forms, API payloads, environment variables).（玩家输入文本、WebSocket 消息、`.env` 都必须校验）
- Apply runtime validation at system boundaries; trust internal types inside those boundaries.
- Keep validation rules co-located with the relevant contract (e.g., next to the API route or form schema).（WebSocket 消息类型定义与校验放在一起）
- LLM 输出按 JSON schema 解析，解析失败降级为"继续原计划动作"。

## File and Naming Conventions
- **Files:** framework default——Python 模块 snake_case（`resident.py`、`retrieve.py`）；TypeScript 场景/类文件 PascalCase（`TownScene.ts`），其余 TS 文件 kebab-case。
- **Components / classes:** PascalCase
- **Functions / variables:** camelCase（Python 侧 snake_case，遵循各自语言惯例）
- **Constants / env vars:** UPPER_SNAKE_CASE（如 `MINIMAX_API_KEY`）

## Testing Pattern
- Add unit tests for pure logic and utility functions.（重点：`memory/retrieve.py` 三要素加权评分、`agents/` 的决策解析与降级）
- Add integration tests for API contracts and critical data flows.（WebSocket 消息契约、记忆流读写、存档/读档连续性）
- Add E2E tests only for the top user journeys the PRD marks as must-have.（MVP 以手动浏览器走查为主：进镇→逛→对话→离开→次日回来看变化）
- Run the test suite after every feature; fix failures before moving on.
- 前端功能必须浏览器实际验证（移动不穿墙、对话有回应、气泡/日志可见）才算完成。

## Change Discipline
- Prefer focused, minimal edits over large rewrites.
- Do not introduce new dependencies without checking the existing stack in `tech_stack.md` first.
- Do not change database migrations, infrastructure config, auth flows, or billing code without explicit approval.（含 `schema.sql` 与居民 `prompt_prefix`——后者变动会毁掉 Prompt 缓存命中率）
- One feature at a time — commit or checkpoint after each working feature.
