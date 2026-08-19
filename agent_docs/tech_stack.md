# Tech Stack & Tools

- **Frontend:** Phaser v4 + TypeScript + Vite（`vanilla-ts` 模板初始化；瓦片地图/相机/输入/碰撞开箱即用。Phaser 问题认准 v4 API，警惕 v3 旧语法答案）
- **Backend:** FastAPI (Python) + WebSocket + uvicorn（智能体循环、记忆管理都在后端，Python 是开发者的优势语言）
- **Database:** SQLite（Python 标准库 `sqlite3`，无 ORM；单文件 `server/db/aitown.db`，表结构见 `server/db/schema.sql`，天然支持存档/读档）
- **Styling:** 像素画面由 Phaser 渲染（星露谷式温馨治愈风，素材风格必须统一）；对话气泡/输入框/事件日志为 Phaser 场景内 UI（`client/src/ui/`），UI 尽量轻量——世界感大于 UI
- **Authentication:** 无（MVP 本地单人使用，无用户系统；MiniMax key 仅存 `.env` 环境变量）

## LLM 调用层（本项目特有，务必遵守）
- 唯一入口：`server/llm/client.py` 的 `chat(prompt, tier: "light" | "flagship") -> str`
- `light` = MiniMax-M2.7（对话/计划/感知判断）；`flagship` = MiniMax-M3（每日反思）；型号经环境变量配置
- 接入方式：`openai` Python 包改 `base_url`（MiniMax 已核实兼容 OpenAI SDK）
- Prompt 结构 = 固定人设前缀（逐字固定，吃 Prompt 缓存）+ 半固定长期记忆摘要 + 动态 Top-K 检索记忆 + 动态当前情境 + 输出指令
- 输出统一 JSON：`{"action": "move|talk|interact|none", "target": "...", "content": "...", "importance": 1-10}`

## Error Handling Pattern
```python
# server/llm/client.py —— LLM 调用的标准降级模式，所有调用方必须复用
# 为什么：LLM 超时/报错绝不能把原始报错暴露到游戏内（PRD 红线），统一在这里降级。
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

async def chat(prompt: str, tier: Literal["light", "flagship"]) -> str:
    model = LIGHT_MODEL if tier == "light" else FLAGSHIP_MODEL  # 型号来自环境变量
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=CHAT_TIMEOUT_SECONDS,  # 玩家对话目标 < 5 秒
        )
        return resp.choices[0].message.content or ""
    except Exception:
        # 降级：返回符合人设的托词由调用方决定；这里只保证"不抛出、有日志"
        logger.exception("LLM call failed (tier=%s)", tier)
        return ""  # 调用方收到空串 → 用降级文案/继续原计划动作
```

## Styling & Component Examples
```tsx
// client/src/scenes/TownScene.ts —— 主小镇场景骨架（Phaser v4 写法）
// 为什么：前端只做渲染与输入，世界状态以后端 WebSocket 推送为权威。
export class TownScene extends Phaser.Scene {
  create() {
    // 1. 加载 Tiled 瓦片地图与碰撞层（面包店、广场、住宅等 4–5 个功能区）
    // 2. 创建玩家精灵，方向键输入，相机跟随
    // 3. 建立 WebSocket 连接，监听 world_state / bubble / chat_reply / event_log
  }

  update() {
    // 方向键 → 本地预测移动 + 发送 player_move（服务端校验碰撞为权威）
  }
}
```
