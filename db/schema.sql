-- qqwarframe-bot 全量数据库 schema（SQLite）
--
-- 设计原则
--   1. 一份真相：原来散在 data/*.json 的十几份字典，全部落成关系表；
--      导入脚本可反复执行（幂等），JSON 退化为「导入源」。
--   2. 不改动 bot 运行时：bot 仍读 JSON，数据库是只读的查询/分析副本，
--      跑稳后再逐模块切换。
--   3. 每个来源都记到 sources 表，能追溯「这行数据是什么时候、从哪来的」。
--
-- 约定
--   * 时间列统一用 REAL（Unix 时间戳，秒）。
--   * 布尔用 INTEGER 0/1。
--   * 主键尽量用业务键，方便 upsert。

PRAGMA foreign_keys = ON;

-- ============================================================
-- 0. 元信息 / 数据溯源
-- ============================================================

CREATE TABLE IF NOT EXISTS meta (
    key     TEXT PRIMARY KEY,
    value   TEXT,
    updated REAL
);

-- 每个导入源的文件、行数、抓取时间
CREATE TABLE IF NOT EXISTS sources (
    name       TEXT PRIMARY KEY,   -- items_cache / aliases / names_zh ...
    path       TEXT,               -- 相对仓库根目录的文件路径
    kind       TEXT,               -- json / api
    rows       INTEGER,            -- 导入的行数
    fetched_at REAL,               -- 源数据自带的时间戳（没有则留空）
    imported_at REAL,              -- 本次导入的时间
    note       TEXT
);

-- ============================================================
-- 1. 物品（warframe.market 物品表）
-- ============================================================

CREATE TABLE IF NOT EXISTS items (
    slug         TEXT PRIMARY KEY,   -- warframe.market 的 url_name
    name_zh      TEXT,               -- 中文名（接口 zh-hans）
    name_en      TEXT,               -- 英文名
    item_id      TEXT,               -- Mongo ObjectId
    ducats       INTEGER DEFAULT 0,  -- 杜卡特（0 = 不能换）
    mastery_rank INTEGER DEFAULT 0,
    trading_tax  INTEGER DEFAULT 0,
    thumb        TEXT,               -- 图标相对路径
    description  TEXT,
    wiki_url     TEXT,
    tradable     INTEGER DEFAULT 1,
    set_root     INTEGER DEFAULT 0,  -- 是否是整套
    max_rank     INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_items_name_zh ON items(name_zh);
CREATE INDEX IF NOT EXISTS idx_items_name_en ON items(name_en);
CREATE INDEX IF NOT EXISTS idx_items_ducats  ON items(ducats);

-- 物品标签（多对多）：mod / prime / weapon / relic / arcane_enhancement ...
CREATE TABLE IF NOT EXISTS item_tags (
    slug TEXT NOT NULL REFERENCES items(slug) ON DELETE CASCADE,
    tag  TEXT NOT NULL,
    PRIMARY KEY (slug, tag)
);

CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag);

-- 套装 → 部件关系（由 *_set 与同前缀的部件 slug 自动派生）
CREATE TABLE IF NOT EXISTS item_relations (
    parent_slug TEXT NOT NULL REFERENCES items(slug) ON DELETE CASCADE,
    child_slug  TEXT NOT NULL REFERENCES items(slug) ON DELETE CASCADE,
    relation    TEXT NOT NULL DEFAULT 'component',
    position    INTEGER DEFAULT 0,
    PRIMARY KEY (parent_slug, child_slug, relation)
);

CREATE INDEX IF NOT EXISTS idx_item_relations_parent
    ON item_relations(parent_slug, position);
CREATE INDEX IF NOT EXISTS idx_item_relations_child
    ON item_relations(child_slug);

-- ============================================================
-- 2. 中英译名（多来源）
-- ============================================================

-- source  : names_zh / wiki_zh
-- category: warframe / weapon / mod / zaw / arcane / resource / fish
CREATE TABLE IF NOT EXISTS zh_names (
    source   TEXT NOT NULL,
    category TEXT NOT NULL,
    name_en  TEXT NOT NULL,
    name_zh  TEXT NOT NULL,
    PRIMARY KEY (source, category, name_en)
);

CREATE INDEX IF NOT EXISTS idx_zh_names_en ON zh_names(name_en);
CREATE INDEX IF NOT EXISTS idx_zh_names_zh ON zh_names(name_zh);

-- 虚空商人（Baro）物品：唯一名（/Lotus/...） → 中文名
CREATE TABLE IF NOT EXISTS baro_names (
    unique_name TEXT PRIMARY KEY,
    name_zh     TEXT NOT NULL
);

-- 战甲黑话：英文名 → 国服玩家常用叫法（warframe_alias.json）
CREATE TABLE IF NOT EXISTS frame_alias_zh (
    name_en  TEXT PRIMARY KEY,
    alias_zh TEXT NOT NULL
);

-- 战甲俗名：中文俗名 → 战甲英文名（frame_nicknames.json）
-- priority=1 表示该俗名和市场物品重名，查价时应优先按战甲解释
CREATE TABLE IF NOT EXISTS frame_nicknames (
    nickname TEXT PRIMARY KEY,
    name_en  TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    note     TEXT
);

-- ============================================================
-- 3. 别名（玩家叫法 → 物品 slug）
-- ============================================================

-- kind: general(aliases.json) / frame(frame_aliases.json) / component(component_aliases.json)
CREATE TABLE IF NOT EXISTS item_aliases (
    alias  TEXT NOT NULL,
    slug   TEXT NOT NULL,
    kind   TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (alias, kind)
);

CREATE INDEX IF NOT EXISTS idx_item_aliases_slug ON item_aliases(slug);

-- ============================================================
-- 4. 星图
-- ============================================================

CREATE TABLE IF NOT EXISTS sol_nodes (
    node_id      TEXT PRIMARY KEY,   -- SolNode0
    name         TEXT,               -- Galatea (Neptune)
    enemy        TEXT,               -- Corpus / Grineer / Sentient ...
    mission_type TEXT                -- Capture / Mobile Defense ...
);

CREATE INDEX IF NOT EXISTS idx_sol_nodes_enemy ON sol_nodes(enemy);
CREATE INDEX IF NOT EXISTS idx_sol_nodes_type  ON sol_nodes(mission_type);

-- ============================================================
-- 5. 赏金（Bounty）
-- ============================================================

CREATE TABLE IF NOT EXISTS bounty_locations (
    location_key TEXT PRIMARY KEY,   -- Ostrons / Solaris United / The Holdfasts ...
    label_zh     TEXT,               -- 希图斯 / 福尔图娜 / 扎里曼 ...
    npc          TEXT,
    source       TEXT,               -- 数据出处说明
    refresh_hours REAL
);

-- 每个地点固定的任务池（bounty_static）
CREATE TABLE IF NOT EXISTS bounty_location_missions (
    location_key TEXT NOT NULL REFERENCES bounty_locations(location_key) ON DELETE CASCADE,
    mission_zh   TEXT NOT NULL,
    position     INTEGER,
    PRIMARY KEY (location_key, mission_zh)
);

-- 档位（tier 用 TEXT：官方是 5-15 这种区间，三处固定点是 1..5）
CREATE TABLE IF NOT EXISTS bounty_tiers (
    location_key TEXT NOT NULL REFERENCES bounty_locations(location_key) ON DELETE CASCADE,
    tier         TEXT NOT NULL,
    level_range  TEXT,
    reward_text  TEXT,
    position     INTEGER,
    PRIMARY KEY (location_key, tier)
);

CREATE TABLE IF NOT EXISTS bounty_tier_missions (
    location_key TEXT NOT NULL,
    tier         TEXT NOT NULL,
    mission_zh   TEXT NOT NULL,
    position     INTEGER,
    PRIMARY KEY (location_key, tier, mission_zh)
);

-- 赏金目标：官方 jobType 路径 → 中英文名
CREATE TABLE IF NOT EXISTS bounty_objectives (
    job_type TEXT PRIMARY KEY,
    name_en  TEXT,
    name_zh  TEXT
);

-- 赏金奖励池：集团 + 档位 → 奖励条目（按原始顺序存 position）
CREATE TABLE IF NOT EXISTS bounty_rewards (
    syndicate TEXT NOT NULL,
    tier      TEXT NOT NULL,
    position  INTEGER NOT NULL,
    reward    TEXT NOT NULL,
    PRIMARY KEY (syndicate, tier, position)
);

CREATE INDEX IF NOT EXISTS idx_bounty_rewards_syn ON bounty_rewards(syndicate);

-- ============================================================
-- 6. 紫卡（Riven）
-- ============================================================

-- 紫卡武器表（/v2/riven/weapons，需要联网；离线导入时为空）
CREATE TABLE IF NOT EXISTS riven_weapons (
    slug         TEXT PRIMARY KEY,
    name_zh      TEXT,
    name_en      TEXT,
    riven_type   TEXT,               -- rifle / pistol / melee / archgun / companion ...
    group_name   TEXT,               -- primary / secondary / melee / archgun / companion
    disposition  REAL,               -- 倾向（0.5 ~ 1.5）
    mastery_rank INTEGER,
    thumb        TEXT,
    icon_url     TEXT,
    raw          TEXT                -- 原始 JSON（字段变动时兜底）
);

CREATE INDEX IF NOT EXISTS idx_riven_weapons_group ON riven_weapons(group_name);
CREATE INDEX IF NOT EXISTS idx_riven_weapons_disp  ON riven_weapons(disposition);

-- 紫卡词条表（/v2/riven/attributes，需要联网）
CREATE TABLE IF NOT EXISTS riven_attributes (
    url_name TEXT PRIMARY KEY,       -- critical_damage
    name_zh  TEXT,
    name_en  TEXT,
    raw      TEXT
);

-- 词条别名：玩家叫法 → 词条 url_name（来自 bot/riven.py 的 ATTRIBUTE_GROUPS）
-- kind: group（词条组）/ negative（负任意这类）/ positive（无负面这类）
CREATE TABLE IF NOT EXISTS riven_attribute_aliases (
    word     TEXT NOT NULL,
    url_name TEXT NOT NULL,
    kind     TEXT NOT NULL,
    PRIMARY KEY (word, url_name, kind)
);

CREATE INDEX IF NOT EXISTS idx_riven_attr_alias_url ON riven_attribute_aliases(url_name);

-- 拍卖接口里的变体写法（和官方表不一致，例如 fire_rate_/_attack_speed）
CREATE TABLE IF NOT EXISTS riven_attribute_variants (
    variant  TEXT PRIMARY KEY,
    name_zh  TEXT NOT NULL
);

-- ============================================================
-- 7. 行情快照（后台扫描出来的价格榜）
-- ============================================================

-- kind: junk（垃圾部件）/ frame（战甲整套）
CREATE TABLE IF NOT EXISTS price_quotes (
    slug      TEXT NOT NULL,
    kind      TEXT NOT NULL,
    price     INTEGER DEFAULT 0,    -- 最低售价
    buy_price INTEGER DEFAULT 0,    -- 最高收购价
    orders    INTEGER DEFAULT 0,    -- 挂单数
    updated   REAL,
    PRIMARY KEY (slug, kind)
);

CREATE INDEX IF NOT EXISTS idx_price_quotes_kind ON price_quotes(kind);

-- 紫卡：每把武器的行情价（只算在线/游戏中卖家，取第 3~8 便宜的均价）
CREATE TABLE IF NOT EXISTS riven_market (
    slug           TEXT PRIMARY KEY,
    value          REAL,            -- 行情价
    samples        INTEGER,         -- 参与计算的挂单数
    metric_version INTEGER,         -- 口径版本（bot.board.RIVEN_METRIC_VERSION）
    legacy         INTEGER DEFAULT 0,  -- 老缓存待重扫
    updated        REAL
);

-- 紫卡挂单（扫描时留存的候选，用来挑代表卡 / 排查钓鱼价）
CREATE TABLE IF NOT EXISTS riven_listings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT NOT NULL,
    position   INTEGER,
    price      INTEGER,
    rank       INTEGER,             -- 等级 0~8
    seller     TEXT,
    attributes TEXT,                -- 词条文本（JSON 数组）
    flags      INTEGER,
    updated    REAL
);

CREATE INDEX IF NOT EXISTS idx_riven_listings_slug ON riven_listings(slug);

-- ============================================================
-- 8. 轮换商人（虚空商人 / 格拉斯特 / 科达 / 埃莉诺 ...）
-- ============================================================

CREATE TABLE IF NOT EXISTS rotation_vendors (
    vendor   TEXT PRIMARY KEY,      -- acrithis / glast / coda / eleanor ...
    observed TEXT,                  -- 观察到的轮换日期
    updated  REAL,
    payload  TEXT                   -- 原始 JSON
);

-- 轮换的商品条目（武器类：名字 + 元素 + 百分比）
CREATE TABLE IF NOT EXISTS rotation_items (
    vendor   TEXT NOT NULL,
    rotation TEXT NOT NULL,         -- A/B/C 或 '-'（无轮换区分）
    position INTEGER NOT NULL,
    item     TEXT,                  -- 物品名（可能是英文）
    element  TEXT,                  -- 元素/属性
    percent  REAL,
    PRIMARY KEY (vendor, rotation, position)
);

-- ============================================================
-- 9. 术语 / 工具字典
-- ============================================================

-- 回廊（科研）术语：dict_key = englishIndex / labels / missionTypes / personal / risks / variants
CREATE TABLE IF NOT EXISTS conquest_terms (
    dict_key TEXT NOT NULL,
    term_en  TEXT NOT NULL,
    term_zh  TEXT NOT NULL,
    PRIMARY KEY (dict_key, term_en)
);

-- 繁体 → 简体（t2s.json）
CREATE TABLE IF NOT EXISTS t2s (
    traditional TEXT PRIMARY KEY,
    simplified  TEXT NOT NULL
);

-- ============================================================
-- 10. 常用视图
-- ============================================================

-- 物品 + 中文名 + 全部标签（tag 用逗号串起来）
DROP VIEW IF EXISTS v_item_detail;
CREATE VIEW v_item_detail AS
SELECT i.slug,
       i.name_zh,
       i.name_en,
       i.ducats,
       i.mastery_rank,
       i.thumb,
       i.tradable,
       (SELECT group_concat(t.tag, ',')
          FROM (SELECT tag FROM item_tags WHERE slug = i.slug ORDER BY tag) t) AS tags,
       (SELECT group_concat(a.alias, ' / ')
          FROM (SELECT DISTINCT alias FROM item_aliases
                 WHERE slug = i.slug ORDER BY alias) a) AS aliases
  FROM items i;

-- 套装 → 部件（带部件中英文名）
DROP VIEW IF EXISTS v_set_parts;
CREATE VIEW v_set_parts AS
SELECT r.parent_slug,
       r.child_slug,
       r.position,
       i.name_zh AS child_name_zh,
       i.name_en AS child_name_en
  FROM item_relations r
  LEFT JOIN items i ON i.slug = r.child_slug
 WHERE r.relation = 'component';

-- 垃圾（能换杜卡特的 Prime 部件）+ 当前地板价
DROP VIEW IF EXISTS v_junk;
CREATE VIEW v_junk AS
SELECT i.slug,
       i.name_zh,
       i.ducats,
       q.price,
       q.orders,
       q.updated,
       CASE i.ducats WHEN 100 THEN '金垃圾'
                     WHEN 65  THEN '假金垃圾'
                     WHEN 45  THEN '银垃圾'
                     WHEN 25  THEN '假银垃圾'
                     WHEN 15  THEN '铜垃圾'
                     ELSE '其他' END AS label
  FROM items i
  LEFT JOIN price_quotes q ON q.slug = i.slug AND q.kind = 'junk'
 WHERE i.ducats > 0;

-- 紫卡榜：武器行情价 + 倾向 + 中文名
DROP VIEW IF EXISTS v_riven_rank;
CREATE VIEW v_riven_rank AS
SELECT m.slug,
       COALESCE(w.name_zh, m.slug) AS name_zh,
       w.name_en,
       w.group_name,
       w.disposition,
       m.value,
       m.samples,
       m.metric_version,
       m.updated
  FROM riven_market m
  LEFT JOIN riven_weapons w ON w.slug = m.slug
 WHERE m.value > 0
 ORDER BY m.value DESC;
