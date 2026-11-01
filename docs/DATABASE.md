# SQLite 数据库

项目从 `data/*.json` 汇总出一份 SQLite 只读数据库，用于查询、校验和后续把运行时从 JSON 切到 SQL。

> 当前 **bot 运行时仍读 JSON**，数据库是旁路副本；这样先保证建库稳定，再逐模块切换。

## 构建

```bash
cd qqwarframe-bot

# 只导本地 JSON（不需要网络）
python db/build.py

# 额外拉紫卡武器 / 词条表（需要能连 warframe.market）
python db/build.py --online

# 指定输出路径
python db/build.py --db data/warframe.db

# 少输出
python db/build.py --quiet
```

默认输出：`data/warframe.db`。

脚本是 **幂等** 的：同一个 source 重复导入会先清空旧行再写入，可以反复跑。

## 查询

```bash
python db/query.py stats
python db/query.py validate
python db/query.py sources

python db/query.py search 女枪
python db/query.py item mesa_prime_set
python db/query.py parts 女枪
python db/query.py alias 电男
python db/query.py bounty 希图斯
python db/query.py riven 翁
python db/query.py rank riven --limit 20
python db/query.py rank junk --limit 20
python db/query.py rank frames --limit 20
python db/query.py rotation
python db/query.py rotation 格拉斯

# 机器可读
python db/query.py --json stats
```

找不到数据库时先运行 `python db/build.py`。

## Web API

Web 控制台启动后（默认 `http://127.0.0.1:8080`），带 `X-Admin-Token` 可访问：

```text
GET /api/database                  数据库状态/行数/构建时间
GET /api/database/search?q=女枪&limit=10
GET /api/database/parts?q=女枪      套装 → 部件
GET /api/database/validate         完整性校验
```

`/api/status` 的返回里也带有 `database` 字段；没有建库时返回
`available: false`，不会影响机器人启动。

## 独立 HTTP 服务

数据库可以脱离 bot 单独运行：

```bash
python db/server.py --db data/warframe.db --host 127.0.0.1 --port 8090
```

接口：

```text
GET /                     网页管理界面
GET /health
GET /stats
GET /sources
GET /search?q=女枪&limit=10
GET /item?q=mesa_prime_set
GET /parts?q=女枪
GET /validate
```

浏览器直接打开 `http://127.0.0.1:8090/` 即可使用搜索、物品详情、套装部件、校验和数据源面板。

设置 token 后（`--token` 或 `DB_API_TOKEN`），除 `/health` 外需带
`X-API-Token` 或 `Authorization: Bearer`。

Docker：

```bash
docker compose -f docker-compose.db.yml up -d --build
```

systemd：

```bash
sudo cp deploy/qqwarframe-db.service /etc/systemd/system/
sudo systemctl enable --now qqwarframe-db
```

服务只依赖 `db/query.py`，不 import `bot/*`，也不启动 QQ/OneBot。
详见 [db/README.md](../db/README.md)。

## 表结构

### 元信息

- `meta`：schema 版本、构建时间
- `sources`：每个导入源的路径、行数、导入时间

### 物品 / 译名 / 别名

- `items`：warframe.market 物品（slug、中英文名、杜卡德、MR、thumb）
- `item_tags`：mod / prime / weapon / relic / arcane 等标签
- `item_relations`：套装 → 部件关系（由 `*_set` 与同前缀部件自动派生）
- `zh_names`：多来源中英译名
- `baro_names`：Baro 物品唯一名 → 中文
- `frame_alias_zh`：战甲英文名 → 国服黑话（如 Mesa → 女枪）
- `frame_nicknames`：中文俗名 → 英文名，带优先级
- `item_aliases`：玩家叫法 → 物品 slug，`kind=general/frame/component`

### 星图 / 赏金

- `sol_nodes`：452 个星图节点
- `bounty_locations` / `bounty_location_missions`
- `bounty_tiers` / `bounty_tier_missions`
- `bounty_objectives` / `bounty_rewards`

### 紫卡

- `riven_weapons`：需要 `--online`
- `riven_attributes`：需要 `--online`
- `riven_attribute_aliases`：38 组中文叫法
- `riven_attribute_variants`：拍卖接口的变体写法
- `riven_market`：每把武器的行情价
- `riven_listings`：扫描时的候选挂单

### 行情 / 轮换 / 字典

- `price_quotes`：垃圾/战甲的最低售价与收购价
- `rotation_vendors` / `rotation_items`：言录使/格拉斯/埃莉诺
- `conquest_terms`：回廊/科研术语
- `t2s`：繁体 → 简体

### 视图

- `v_item_detail`：物品 + 标签 + 别名
- `v_set_parts`：套装 → 部件
- `v_junk`：可换杜卡特的 Prime 部件 + 地板价
- `v_riven_rank`：紫卡行情排行

## 数据完整性

```bash
python db/query.py validate
```

会检查：

- SQLite `integrity_check`
- 外键
- `item_tags` / `item_aliases` / `price_quotes` 是否指向不存在的物品
- 关键表是否为空
- 紫卡表为空、行情快照过少时给出警告

当前本地构建结果（离线）：

```text
items             3893
item_tags        11629
item_aliases      1795
item_relations     857
baro_names       29676
t2s               3221
sol_nodes          452
bounty_rewards     445
rotation_items      24
riven_weapons        0   ← 需 --online
price_quotes         5   ← 本地 board_cache 只有样本
总行数          ~54,132
```

## 已知限制

1. 离线构建时 `riven_weapons` / `riven_attributes` 为空；需在能访问 warframe.market 的服务器执行 `--online`。
2. `price_quotes` / `riven_market` 来自运行时 `data/board_cache.json`；开发机缓存为空时会只有少量样本，需拉回服务器扫描后的缓存再构建。
3. `bot` 仍读 JSON；数据库目前是查询/分析副本，不是运行时主库。
4. `db/query.py` 只读查询，不建议修改数据库；写操作请走 `db/build.py`。
5. `data/warframe.db` 是生成物，默认不进 Git。

## 测试

```bash
python -m unittest tests.test_database -v
```

测试会在临时目录构建一份离线库，覆盖 schema、物品/别名、赏金、轮换、视图、校验、CLI 和幂等构建。

## 后续计划

1. 服务器 `--online` 补齐紫卡表；
2. 拉真实 `board_cache.json` 后重建行情；
3. 给 `query.py` 增加 JSON 输出分页/导出；
4. 逐模块把 bot 的 JSON 读取切到 SQLite（先别名/译名，再物品/紫卡）；
5. 保持 JSON 作为导入源和回滚路径。
