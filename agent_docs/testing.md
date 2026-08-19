# Testing Strategy

## Frameworks
- **Unit Tests:** 后端 pytest（`server/tests/`）；前端 Vitest（与源码同目录 `*.test.ts`）
- **E2E Tests:** MVP 以**手动浏览器走查**为主（Chrome/Safari 桌面最新版）：每个前端功能必须实际玩过才算完成；完整用户旅程（进镇→逛→对话→离开→次日回来看变化）在 W4 走通。Playwright 属可选后置项，MVP 不强制。

## Rules & Requirements
- **Coverage:** 核心路径（`server/agents/`、`server/memory/`、`server/llm/`）目标 70%+；前端以"能玩、不穿墙、有回应"为准，不追覆盖率数字。
- **Before Commit:** Always run `cd server && pytest` 与 `cd client && npm test` before verifying a task is complete.
- **Failures:** NEVER skip tests or mock out assertions to make a pipeline pass without Human approval. If an Agent breaks a test, the Agent must fix it.
- **LLM 相关测试用 mock：** 单测不得真实调用 MiniMax API（成本 + 不确定性）；`llm/client.py` 注入假客户端。真实调用的验证（延迟、缓存命中率）走 W1/W4 的手动实测。
- **关键手动验收项（来自 PRD/TechDesign）：**
  - 方向键平滑移动、不穿墙；地图含 4–5 个可辨识功能区
  - 同一件事问两个性格相反的居民，回应差异可感知
  - 挂机 10 分钟：居民按计划行动，且 API 调用次数与事件数吻合（无事无调用）
  - 告诉面包师一个秘密 → 离开 → 回来再聊，他能提起
  - 造 50 条记忆，检索 Top-5 与人工判断基本一致
  - 玩到一半关掉重开读档，世界状态完全连续

## Execution
- Command to run all tests: `cd server && pytest`；`cd client && npm test`
- Command to run a single test file: `cd server && pytest tests/test_retrieve.py`；`cd client && npx vitest run src/net/client.test.ts`
