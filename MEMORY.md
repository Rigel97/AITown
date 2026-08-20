# System Memory & Context 🧠
<!--
AGENTS: Update this file after every major milestone, structural change, or resolved bug.
DO NOT delete historical context if it is still relevant. Compress older completed items.
-->

## 🏗️ Active Phase & Goal
**Current Task:** README + 实际游戏截图已上线 GitHub（github.com/Rigel97/AITown，公开仓）✅
**Next Steps:**
1. 存档/读档（Phase 3）
2. 成本实测校准（W4）
3. 出戏防护与降级打磨（Phase 3）

## 📂 Architectural Decisions
*(Log specific choices made during the build here so future agents respect them)*
- 2026-08-14 — 前端选 Phaser v4：瓦片地图/相机/输入/碰撞开箱即用，1 个月 MVP 少造轮子；Smallville 原作者验证过"Python 后端 + Phaser 前端"路线。a16z ai-town 只读不 fork。
- 2026-08-14 — 后端选 FastAPI (Python)：项目灵魂（智能体循环）在后端，用开发者最熟的语言写；接口契约用 WebSocket 消息类型显式定义。
- 2026-08-14 — LLM 选 MiniMax API（用户指定）：轻量层 M2.7（对话/计划/感知判断）+ 旗舰层 M3（每日反思），已核实支持 Prompt 缓存（缓存读取价 = 输入价 1/5）、兼容 OpenAI SDK、无 embedding 接口、无峰谷计价。调用统一走 `llm/client.py` 的 `chat(prompt, tier)`，换 provider 只改适配文件。
- 2026-08-14 — 记忆检索用"近因 + 重要性 + 关键词"三要素加权，不上向量库（MVP 够用；MiniMax 无 embedding 接口，V2 只能走本地模型）；`memories` 表预留 `embedding` 字段（恒为 NULL）以便 V2 无迁移升级。
- 2026-08-14 — 事件驱动而非逐 tick 调用 LLM：居民无事可做时零 API 调用，这是成本控制的第一原则；游戏主循环（60fps 渲染）与 AI 循环（慢速事件驱动）解耦。
- 2026-08-14 — 一切皆记忆：对话/移动/事件/反思统一写入 `memories` 表，检索接口统一。
- 2026-08-14 — 本地运行不公网部署（PRD Out of Scope）；演示 = 本机跑 + 录屏备份。
- 2026-08-14 — 像素素材决策（用户拍板）：**免费素材包先行**（Kenney Tiny Town，CC0），AI 文生图美化留到 W4；色块占位地图将被瓦片地图替换。（2026-08-19 更新：底图已换为 ai-town 的 mage3 城镇图 + 32x32folk 角色精灵表，见 Completed Phases）
- 2026-08-14 — 小镇要素分析结论：小镇“活”的关键是相遇密度与聚集点（学星露谷酒吧的夜间聚集设计），不是功能建筑数量。
- 2026-08-14 — 供给链设定（用户确认方向）：小镇不封闭——南道通县城，**每周三供货日**（林师傅面粉/红姐肉菜/阿茉花种都靠它，杂货店是最大收货方，小豆子帮卸货，老周全镇广播）；镇内小循环：阿茉屋后菜园供红姐青菜；餐馆=全镇食堂。供货日 = W3 候选周期事件源（合理的事件驱动触发点）。
- 2026-08-14 — 第 7 位居民：**老宋（杂货店主兼木匠，55 岁，慢性子少言但一诺千金）**——杂货店是周三供货最大收货方，木匠身份让他有理由走进每栋楼（第三移动元素）；与老周是“一个说一个听”的老搭档。建筑：杂货店在商店街中段（花店与面包店之间，三家连排）。精灵：111。
- 2026-08-14 — 人设定档（用户授权“最优最小”重构）：6 人 / 6 建筑——林师傅（面包师）/ 苏晚（图书管理员）/ 阿茉（花匠）/ 老周（退休邮差，移动信息流）/ **红姐（餐馆老板娘，补夜间聚集点）** / **小豆子（面包店学徒，补代际+第二移动元素）**。关系网按“日常循环”编织：清晨面包店早市 → 白天各守店+老周流动+小豆子跑腿 → 夜晚红姐餐馆聚集。新建筑：餐馆（东道沿线，图书馆隔壁）。精灵：100/98/109/99/110/112。prefix 逐字固定在 `db/seed.py`，改人设=改它+重跑 seed+重估成本。
- 2026-08-19 — **视觉优化五件套**（对标斯坦福 Smallville 观感差距分析落地，全部零 LLM 成本）：①居民移动插值——服务端广播改为 **1/3 秒/拍 × 每拍 1 格**（等速 3 格/秒，时钟节奏不变 tick(1/3)×3），客户端"按时到位"插值（速度=剩余距离×3/s，恰在下一拍到达，无脉冲感、不切墙角、转向精确）；②头顶动作 emoji（`world/actionEmoji.ts` 关键词映射+职业回退，`public()` 补发 occupation）；③zoom 键 1/2/3 切 ×2/×1.5/×1；④角色 y-sorting（depth=y，玩家/居民/名牌统一）；⑤暖色 ColorMatrix 滤镜（B 键开关）。
- 2026-08-19 — **为什么不做前景遮挡层（walk-behind）**：几何核算——32px 角色配全尺寸物理体 + obj 层全部阻挡，角色与物件瓦片**零像素重叠**（Smallville 角色是 1.25 格放大才需要前景层）。本地图真正可见的遮挡问题是角色互相遮挡（聚集点），y-sorting 已解决；未来若放大角色尺寸需重评。
- 2026-08-19 — **小镇编年史选 JSONL 文件而非改 schema**：居民全部交互（邀请/加入/散场全文/玩家对话）逐句全文写 `saves/chronicle.jsonl`（每行一个 JSON，双时间戳 game_time+real_time）。理由：①全文给"人"回看、摘要给"机器"检索——职责不同所以不进 memories 表（全文入表会撑爆检索候选集和 Prompt 成本）；②JSONL 追加安全、中文原样可读、日后可机器解析（故事线 UI / 小镇周报的现成数据源），Markdown 排版可随时从 JSONL 生成，反向不行；③saves/ 已 gitignore 且零 schema 迁移（AGENTS.md 保护区内不动库）。写入失败只告警不抛错——旁路档案绝不打断游戏（`world/chronicle.py`）。

## 🐛 Known Issues & Quirks
*(Log current bugs or weird workarounds here)*
- **GitHub 工作流注意**（2026-08-19 建仓）：仓库 github.com/Rigel97/AITown（公开）；推送凭证用 `gh auth`（设备码登录已完成，`gh auth setup-git` 可让 git push 永久免密）；提交身份临时用 `-c user.name/user.email` 注入（Rigel97 + noreply 邮箱），没改全局 git 配置；后续改动记得 commit+push。
- 成本达标风险：按已核实单价，单次游戏日 ≤ ¥2.1 目标需 ~70%+ Prompt 缓存命中率，存在超标风险 → W1 实测命中率，W4 校准，必要时下调调用量假设或上调预算。
- Phaser v4 较新：AI 可能给出 v3 语法的答案 → 提问时明确"Phaser v4"，收到代码先核对版本 API。
- 人设前缀必须逐字固定放 prompt 开头：任何变动都会毁掉缓存命中率，改人设=改 `residents.prompt_prefix` 后重新评估成本。
- MiniMax 是否有新用户免费额度未确认（定价页未提及）→ 注册控制台时确认。
- **M2.7 输出带 `<think>` 思考块**（2026-08-14 实测）→ 已在 `llm/client.py` 的 `_strip_think` 统一剥离（思考 token 仍计费，prompt 要短 + 靠缓存摊薄）；且模型有自称"语言模型"的出戏倾向 → 人设前缀 + "永不承认自己是 AI"指令不可省。
- **Phaser v4 quirk：`setScrollFactor(0)` 与 camera zoom 组合行为不可靠**（2026-08-14 实测 HUD 文字不渲染）→ HUD 用每帧 `camera.worldView` 对齐 + `setScale(1/zoom)` 的方案，见 TownScene。
- **Phaser tween 的 `delay` + `from/to` 组合不可靠**（2026-08-17 用户实测气泡一闪而过）→ "停留 N 秒后淡出"要用 `time.delayedCall` 到点再起 fade tween，不要把 delay 放在 fade tween 上。
- 自动化测试键盘输入：合成 `KeyboardEvent` 必须带 `keyCode`（Phaser 按 keyCode 映射）；`agent-browser press` 是瞬时按键，对按住移动类游戏测不出位移，需合成长按。
- **engine.residents 只在启动时读一次 DB**（2026-08-14 踩坑：重跑 seed 后忘记重启后端，前端看不到新居民）——W2 智能体循环落地时要改成内存态权威 + DB 持久化，居民位置/状态更新走 engine，不再依赖启动加载。
- **M2.7 延迟波动巨大**（2026-08-17 实测：同 prompt 3s~60s+，并发时更明显）→ `chat()` 加了 timeout 参数：对话保持 4.5s（<5s 目标），计划/反思类后台任务传 60s + 重试一次。降级路径全程兜住了（超时→默认日程），游戏不会因 LLM 卡住。
- **降级文案会污染记忆流**（2026-08-17 发现）：首轮超时写入的默认日程记忆被下轮检索命中，LLM 直接照抄进新计划——证明记忆注入有效，但也说明测试垃圾要及时清。
- **mage3 地图有 4 个坏瓦片 id**（2026-08-19 发现）：源数据 assettool 生成缺陷，底行有 251259 这种两个 id 拼接值，超瓦片集范围（352 块）——转换脚本 convert_aitown_map.mjs 已统一清洗为 -1。
- **站位点必须锁定主连通区**（2026-08-19 换图踩坑）：mage3 房屋内部是被 obj 层围死的封闭孤岛（可走但进不去），初版面包店/杂货店/东南宅点位落在屋内，居民被困死。转换脚本现在会从出生点 BFS 主连通区，孤岛候选点自动挪到最近可达格；test_pathfinding 新增全站位点互通测试。
- **agent-browser 的 eval 变量会跨调用残留**（2026-08-19 实测：`const s` 报 Identifier already declared）→ 每次用 IIFE 包裹 `(() => {...})()`，或换变量名。
- **图片预览工具读不了本机截图/PNG**（read_file 报 Image is not supported）→ 验证渲染用像素采样分析（PIL 统计色彩多样性）替代目视，或直接查 DOM/Phaser 对象树。
- **Phaser v4 无 postFX**（2026-08-19 核实）：FX 系统在 v4 重构为 **Filters**——`camera.filters.internal.addColorMatrix()` 返回带 `.colorMatrix` 的控制器，链式 `brightness/saturate/hue(value, multiply=true)` 叠加。**`brightness` 是乘法（0=黑，1=原图），提亮要传 >1**——传 0.1 画面全黑（实测踩坑，像素分析抓出）。
- **玩家走路动画 NaN 帧历史 bug**（2026-08-19 修复）：玩家精灵从未 `setData("charIndex")`，playAnim 算出 `walk-undefined-left`+NaN 帧——玩家自 mage3 迁移起就没有走路动画。修复：create 里补 setData。教训：动画 key 出现 undefined 要立刻断言。
- **headless 浏览器测渲染两坑**（2026-08-19 实测）：① SwiftShader 软渲染下 zoom=1（4× 像素填充）仅 4fps——真实 Chrome 有 GPU 无此问题，别误判为游戏 bug；② 游戏 tab 被切后台会被节流（fps 掉 4、定时器停摆、agent-browser "daemon busy"），症状像页面挂死。另：`agent-browser press` 字母键触发不了 Phaser keydown（数字键可以），字母键用合成 `new KeyboardEvent("keydown", {keyCode: 66, bubbles: true})` dispatch 到 window。
- **前端测试基建补齐**（2026-08-19）：装 vitest + `npm test` 脚本（AGENTS.md 约定过但从未配置）；`actionEmoji.test.ts` 5 测锁定关键词优先级/职业回退/兑底。npm 缓存权限问题用 `--cache /tmp/npm-cache-aitown` 绕过。
- **对话降级托词三原则**（2026-08-17 用户实测反馈后修复）：①超时放宽到 15s（M2.7 延迟 3–60s，4.5s 铁定频繁超时）+ 失败重试一次；②托词多条轮换（同一句反复出现瞬间出戏）；③降级回复【不写入记忆流】（否则被检索命中污染人设）。
- **对话历史必须按居民隔离**（2026-08-17 用户反馈）：HUD 对话面板曾全局共用一份历史，换人就看到上一个人的记录 → `histories: Map<residentId, lines>`，打开谁渲染谁。
- **对话面板标题曾重复拼接**（2026-08-19 用户反馈修复）：`tryOpenChat` 传的已是完整标题，`Hud.openChat` 又包一层 `和 X 聊天` → 显示"和 和 小豆子 聊天 聊天"。修复：`openChat(residentId, title)` 直接收完整标题——标题组装只发生在调用方（单聊/群聊语境在场景层才知道），HUD 只负责渲染。
- **小镇播报要带对话内容**（2026-08-17 用户反馈）：只报"聊了起来"等于没报 → engine 推送玩家原话与居民回复（截断 40 字）。
- **对话模型拆出独立 tier**（2026-08-17）：`llm/client.py` 新增 `chat` tier，模型名读 `MINIMAX_CHAT_MODEL`（默认回退 LIGHT_MODEL）——对话与计划分开配置，试新模型不影响计划生成、出问题好归因。当前试用 `MiniMax-M2.7-highspeed`（冒烟实测 6.25s 含首次建连，回复质量正常）。
- **人设前缀 ≠ 世界观**（2026-08-17 用户实测：老宋编造不存在的镇民要给人修椅子）：prompt_prefix 只写"我是谁、我认识谁"，没写"镇上只有这些人" → LLM 自由发挥。修复：`agents/resident.py` 新增 `world_context()`——从 DB 构建完整镇民名单+场所清单+禁止编造约束，逐字固定插在对话 prompt 的人设前缀之后（缓存友好）；`test_dialogue.py` 锁定名单完整性/逐字稳定性/prompt 组装顺序。实测修复后老宋回复贴人设且不编人。

## 📜 Completed Phases
- [x] Initial scaffold（2026-08-14：client/ Vite+Phaser 4.2.1，server/ FastAPI+uv 虚拟环境；`start.sh` 一键启动；3 个 WebSocket 集成测试全绿）
- [x] Database schema creation（2026-08-14：`residents` / `memories` / `saves` 三表已建入 `server/db/aitown.db`）
- [x] MiniMax 首次调用验证（2026-08-14 实测：M2.7 延迟 2.73s/3.81s < 5s 目标 ✅；**Prompt 缓存确认命中** `cached_tokens=14`（测试 prompt 仅 54 token，真人设前缀 200–400 token 后才能看真实命中率）；OpenAI SDK 兼容 ✅）
- [x] 瓦片小镇地图（2026-08-14：Kenney Tiny Town CC0 素材，代码生成 80×50 布局 `mapData.ts`，4 个功能区 + 广场 + 土路十字；Arcade 碰撞 + 相机 zoom=2 跟随；**自动化实测：玩家推墙停在 y=184（理论 176+8），碰撞精确**）
- [x] 游戏时钟 + 状态广播（2026-08-14：`world/engine.py` WorldEngine 单例，流速**现实 1 秒 = 游戏 1 分钟**（24 分钟 = 1 游戏日），服务端权威每秒广播 world_state；3 个时钟单测锁定流速公式；实测 7 分钟从 08:00 走到 14:46 ✓）
- [x] 建筑可辨识化（2026-08-14：门口装饰——面包店木箱+招牌、花店红蘑菇+灌木、图书馆石屋+木桶+招牌、住宅招牌）
- [x] 地图重排为星露谷式布局（2026-08-14 用户反馈原十字对称布局太假）：广场居中为心脏、建筑门朝南临街（商店街=花店+面包店临西道、图书馆临东道）、道路有转折（东道折南通东南宅、西南乡间小路）、树木四簇成林（cluster() 只落草地不会盖路）
- [x] 计划执行循环（2026-08-17：engine 每拍查计划表 + A* 寻路移动，事件驱动零 LLM；07:00 自动并发生成当日计划；实测两个时刻快照全部居民位置随时间合理变化，午饭点 3 人聚到餐馆。**地图改为前后端共享**：`client/src/world/mapData.ts` 是唯一源头 → `client/scripts/export_map.mjs` 导出 `public/assets/town_map.json` → 前端渲染与服务端 `world/mapdata.py` 寻路共读，改布局必须重新导出）
- [x] 智能体计划层 planner（2026-08-17：`agents/planner.py` 静止版实测通过——7 人计划全部符合人设且关系网生效（红姐去花店取阿茉的花、老周广播供货日、小豆子往花店跑）；prompt=固定人设前缀+检索记忆+JSON 指令，解析失败降级默认日程；`save_daily_plan` 用 Protocol 避免循环依赖）
- [x] 记忆流 store/retrieve（2026-08-14：`memory/store.py` 读写 + 词表关键词抽取（写入时生成、确定性好调试）；`memory/retrieve.py` 三要素加权评分 W_R·exp(-Δt/24h) + W_I·imp/10 + W_K·命中率，SQL 只取候选、Python 打分；game_time 解析归 `world/clock.py`。7 个单测覆盖三要素各自排序 + 老的重要记忆能浮现 + Top-K 截断）
- [x] 玩家对话 + 对话面板 + 事件日志（2026-08-17：`agents/dialogue.py` 玩家对话→LLM 回复→记忆写入；`engine.player_chat()` 带距离校验（CHAT_RANGE_TILES=3）；`main.py` 发 `chat_reply` 进对话面板（头顶气泡方案已按用户要求移除——对话只走 DOM 面板）；`event_log` 广播（到达/聊天事件，含对话内容）；前端 HUD DOM 层（对话面板按居民隔离历史+"📜 小镇播报"按钮可折叠事件日志+走近提示）；同地点站位点分散（`LOCATION_SPOTS` 多点位，`index % len` 分配）。tsc 零错误 + 38 测试全绿）
- [x] 居民相遇触发对话（2026-08-17：事件驱动——只在居民**到达**新地点时检测 3 格内的空闲居民，不逐 tick 扫描；同对冷却 30 游戏分钟、一次到达最多触发一场，防连锁爆炸；`dialogue.resident_chat()` 一次 LLM 调用生成 2–4 回合整场对话，解析白名单只认两人名字开头的行（挡旁白和编造的第三人）；写入双方记忆（下次见面检索命中"上次聊过"）；台词逐句进小镇播报。实测：阿茉买面包、林师傅×苏晚（苏晚毒舌梗"面包的良心"）、红姐×老宋（木匠修椅子梗）全部符合人设。3 个解析单测）
- [x] ai-town 式对话状态机（2026-08-17：把"一次生成整场"升级为**逐回合状态机**——邀请→LLM 接受/拒绝→双方停下→每 5 游戏分钟 1 回合（带完整 transcript，说话者轮换）→某方说"结束"或达 6 回合上限散场。`Conversation` 状态对象；参与者暂停移动（`_step_resident` 跳过）；散场写记忆摘要+互设冷却。**居民可加入**：路过的居民 LLM 决定加入/离开（`decide_join`），加入则进入轮换。**玩家可加入**：走近进行中的对话按 Enter → 玩家话进 transcript → `player_join_reply` 生成在场居民回应。`chat_reply` 改为 `lines: [[speaker,text],...]` 支持群聊多句。前端：💬 名牌标记 + "加入对话"提示。实测：林师傅×小豆子 6 回合后散场、老宋路过拒绝加入、老周×红姐聊到"新搬来的人家"。成本：逐回合每场 4-6 次调用 vs 一次 1 次，靠 Prompt 缓存摊薄。）
- [x] 反思层（2026-08-17：`agents/reflection.py` 每日 23:00 定时触发，每居民 1 次 M3 调用：读当日全部记忆→生成 1-2 条高层认知（importance 8）写回记忆流。`get_memories_of_day` 按 `dayN-` 前缀取当日记忆。反思会进后续检索，关系随时间自然生长。`parse_reflections` 剥编号/限条数。）
- [x] mage3 城镇地图替换（2026-08-19：底图从代码生成的 Kenney 色块图换为 ai-town 的 mage3 石质城镇（50×50、32px、MIT）：`client/scripts/convert_aitown_map.mjs` 转换管线——列主序转行主序 + 坏 id 清洗 + 主连通区校验，产出 town_map.json 前后端共读；前端 4 层渲染（2 bg + 2 obj，碰撞挂 obj 层 `setCollisionByExclusion([-1])`，与 ai-town blockedWithPositions 语义一致）；角色换 32x32folk 动画精灵表（每角色 96×128 块，四方向各 3 帧行走，玩家 f8/居民 f1-f7，帧号 mapData.walkFrames 计算，动画按需创建）；站位点/出生点/seed 坐标全部重映射并锁定主连通区；engine 出生点改读共享数据。实测：像素分析 540 色、玩家碰撞停在墙边 x=752、居民按计划自主移动（阿茉广场浇花、红姐回餐馆备料、老周去杂货店打听消息）、对话状态机正常（林师傅×小豆子聊天中、老宋拒绝苏晚邀请）、玩家对话回复贴人设、Esc 关面板、事件日志滚动 50 条。tsc 零错误 + 51 测试全绿。）
- [x] 视觉观感优化五件套（2026-08-19：对标斯坦福观感差距分析落地，全部零 LLM 成本。**插值**——服务端 1/3 秒/拍×1 格（等速），客户端"按时到位"插值，96 秒采样实测最大相邻位移 13.3px/100ms（旧版每秒跳 96px）；**emoji 外显**——头顶名牌"名字+动作 emoji"（林师傅🥐/阿茉🌸/红姐🍳职业回退/聊天中💬），走近提示附完整动作文本；**zoom 切换**——键 1/2/3 = ×2/×1.5/×1；**y-sorting**——玩家/居民/名牌 depth=y；**暖色滤镜**——v4 Filters ColorMatrix（提亮 1.12×+饱和 1.2+色相-12°），B 键开关，像素统计亮度 0.56→0.67/饱和 0.24→0.33/暖度 r-b 18→39。顺手修两个 bug：brightness 乘法语义踩坑（首版画面全黑）、玩家走路动画 NaN 帧（历史遗留）。验证：tsc 零错误 + vitest 5 测 + pytest 51 测 + 浏览器实测（插值采样/emoji 对象树断言/zoom 逐键/滤镜 A/B 像素对比/玩家移动碰撞/console 零错误）；新增 vitest 基建补齐 `npm test`。）
- [x] 小镇编年史（2026-08-19：`server/world/chronicle.py` + engine 四节点接线——邀请接受/拒绝、路入加入、散场逐句全文、玩家单聊/群聊原话与回复，全部全文落盘 `saves/chronicle.jsonl`（JSONL、双时间戳、写失败只告警）。拒绝的邀约、被拒的加入这类"未成局"交互不写记忆、不入摘要，编年史是其唯一留痕处。零 LLM 成本、零 schema 改动。新增 6 测：JSONL 格式/中文原样/写失败不抛错 + 三个接线测（monkeypatch 记忆写入与 LLM，不污染真实 DB），57 测全绿 + ruff 干净。）
- [x] 居民人设定档入库（2026-08-14：`db/seed.py` 幂等写入 **6 居民**，`agents/resident.py` 收口数据访问；engine 启动时加载并广播；前端动态加载精灵+头顶名牌；3 个 seed 测试锁定 prefix 完整性/幂等/关系网）
- 备注：uv/pip 直连 PyPI 极慢，用 `--default-index https://pypi.tuna.tsinghua.edu.cn/simple` 解决；瓦片索引速查：草地 1/2、土路 25、广场 109、木屋 52-54/72-75/84-88、石屋 48-50/60-62/76-79、树 4/5、路灯 92、招牌 83、木箱 107、木桶 128、蘑菇 29、灌木 30
