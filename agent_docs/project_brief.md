# Project Brief

- **Product vision:** 一个 AI 居民自主生活、社交的 2D 像素小镇——玩家以居民身份走进小镇，与居民自由对话并影响小镇发展；世界在你离开时也活着，居民记得你做过的事，故事是涌现而非剧本。
- **Target Audience:** 首要用户是开发者本人（学习/作品集型项目：玩 + 掌握多智能体开发 + 简历素材）；第二阶段是朋友与独立游戏 & AI 爱好者（自己打开链接或本地跑起来即可，无需新手引导）。

## Conventions
- **Naming:** Python 模块与函数 snake_case；TypeScript 类/场景 PascalCase（`TownScene.ts`）、其余文件 kebab-case；常量与环境变量 UPPER_SNAKE_CASE。
- **File Structure:** 前后端分离单仓库——`client/`（Phaser 前端）、`server/`（FastAPI 后端，内含 `agents/` `memory/` `llm/` `world/` `db/`）、`saves/`（存档）、`docs/`（PRD/TechDesign/研究报告）；后端测试放 `server/tests/`，前端测试与源码同目录（`X.ts` 旁放 `X.test.ts`）。

## Key Principles
- Ship the simplest possible solution that solves the user story.
- If a simpler low-code integration exists (e.g. using pre-built Stripe Checkout instead of a custom form), use it.
- **学习约定：** AI 写代码但必须向开发者解释关键设计（尤其 `agents/` 与 `memory/`）——"能向朋友讲清架构"是项目目标本身。
- **成本意识：** 事件驱动优先；人设前缀逐字固定吃 Prompt 缓存；M2.7 干杂活、M3 只做反思；非延迟敏感任务不开 priority 计费。
- **温馨治愈优先：** 配色、精灵、动画节奏服务于"慢节奏、有情感联结"的氛围；涌现必须可见（气泡、事件日志）。
