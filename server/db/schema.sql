-- AI 小镇 SQLite 表结构（核心三表）
-- 设计说明：一切皆记忆——对话/移动/事件/反思统一进 memories 表，
-- 检索接口统一，世界观一致性自然涌现。改动本文件前必须先备份 aitown.db。

-- 居民定档：人设与当前状态
CREATE TABLE IF NOT EXISTS residents (
    id TEXT PRIMARY KEY,           -- 'baker_lin'
    name TEXT NOT NULL,            -- '林师傅'
    occupation TEXT NOT NULL,
    personality TEXT NOT NULL,     -- 性格描述（写入 prompt 前缀）
    backstory TEXT NOT NULL,
    prompt_prefix TEXT NOT NULL,   -- 组装好的固定人设前缀（逐字固定，改动会毁掉 Prompt 缓存命中率）
    current_location TEXT,         -- 地图坐标/区域
    current_action TEXT,           -- 当前正在做什么
    daily_plan TEXT                -- 当日计划 JSON
);

-- 记忆流：一切事件/对话/观察/反思的统一存储
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id TEXT NOT NULL REFERENCES residents(id),
    game_time TEXT NOT NULL,       -- 游戏内时间戳
    type TEXT NOT NULL,            -- 'observation'|'dialogue'|'event'|'reflection'
    content TEXT NOT NULL,
    importance INTEGER NOT NULL,   -- 1-10
    keywords TEXT,                 -- 空格分隔，供关键词检索
    embedding BLOB,                -- V2 预留，MVP 恒为 NULL，不得删改
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_memories_resident_time ON memories(resident_id, game_time DESC);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(resident_id, importance DESC);

-- 存档：世界状态快照
CREATE TABLE IF NOT EXISTS saves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    saved_at TEXT DEFAULT CURRENT_TIMESTAMP,
    game_time TEXT NOT NULL,
    world_state TEXT NOT NULL      -- 居民位置/状态/时钟的 JSON 快照
);
