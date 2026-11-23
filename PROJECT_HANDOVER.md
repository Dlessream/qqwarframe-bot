# qqwarframe-bot 项目交接文档

> 用途：把本项目完整上下文交给另一位 AI / 开发者继续开发。
> 生成时间：2026-11-01。原作者已完成的全部需求与当前「全量数据库」建设进度都在下面。
> 原始对话记录（机器格式，很大）：
> `C:\Users\Administrator\.workbuddy\projects\c-Users-Administrator-WorkBuddy-2026-10-26-19-40-07\38cbf8c6-234f-40b9-85d4-04418bd0315a.jsonl`

---

## 0. 项目是什么

一个 **QQ 机器人**（基于 QQ 开放平台官方机器人：WebSocket 反向连接 / Webhook 正向回调），
核心功能是查 Warframe（星际战甲）的 warframe.market 市场行情：物品价格、战甲/部件、
紫卡（Riven）、垃圾杜卡特价、赏金、星图等。技术栈：Python 3，标准库 + Pillow（图片渲染）
+ cryptography（Webhook 验签）。本地开发，Docker 部署到一台云服务器。

仓库根目录：`C:\Users\Administrator\Documents\deepseek-harness\default-workspace\qqwarframe-bot`

---

## 1. 服务器与部署（重要，照做不会踩坑）

| 项 | 值 |
|----|----|
| 服务器 IP | `124.220.229.109` |
| SSH 用户/密码 | `root` / `Zm962889842` |
| bot 容器名 | `qqwarframe-bot` |
| **生产 compose 文件** | **`docker-compose.full.yml`**（必用这个） |
| 网络 | bridge 网络，官方机器人主动出网连 WSS；`http://xray:10809`（维基/世界状态代理） |
| 容器内源码 | `/app`；配置 `/app/config/config.json` |

**部署方式（GitHub 在服务器不通，走 git bundle + SFTP）：**

```
# 1. 本地打包（base 填上一个已部署的 commit，从头打就用 --all）
git bundle create C:/Users/AppData/Local/Temp/qqwf.bundle <base>..HEAD

# 2. SFTP 上传到服务器 /root/qqwf.bundle
# 3. 服务器上合并（fast-forward）
ssh root@124.220.229.109
cd /root/qqwarframe-bot
git fetch /root/qqwf.bundle HEAD:tmp-main
git merge --ff-only tmp-main
# 注意：aliases.json 在服务器有独有条目（"猴"=wukong_prime_blueprint），
#       ff 合并后要把服务器独有条目补回，否则丢数据。

# 4. 用 full compose 重建容器
docker compose -f docker-compose.full.yml up -d --build bot
```

⚠️ **坑**：官方机器人不需要 NapCat；`docker-compose.full.yml` 里已移除 napcat 容器
与 `QQWF_WS_URL=ws://napcat:3001`。旧版本误用 `docker-compose.yml`（host 网络、
`ws://127.0.0.1:3001`）重建会导致连接失败。**一律用 `docker-compose.full.yml`**，
并在 `.env` 或 Web 控制台填 `QQWF_APP_ID` / `QQWF_APP_SECRET` 后重启 bot。

---

## 2. 已完成的需求清单（按时间）

1. **改端口位置** —— 各端口在 `bot/*.py` 与 `config/config.json`，按需查。
2. **战甲别名改造** —— 总图/蓝图都能识别；中文别名/英文名都能识别；部件同理（commit `c4b362e`）。
3. **「电男图」识别成蓝图**（`cd0a8a4`）。
4. **紫卡分析按武器组分** —— 主武器/副武器/近战/空战/同伴；自制武器除近战按副武器算（`b047106`）。
5. **服务器同步** —— 见第 1 节。
6. **按 PDF 更新战甲别名**（`7130494`）。
7. **「双子」指战甲**（不是市场武器「双子恶行」，`d393831`）。
8. **赋能必须用前缀** —— 带「赋能」前缀才识别，否则不识别（`e324df2`）。
9. **#wr 紫卡市场（重点）**：
   - `wr 翁` → 该武器**最便宜 9 张**紫卡 + 游戏同款卡面图（**3×3 排布**）；
   - 卡面要素：卡名、`RIVEN MOD`、正负词条与数值（负面红）、**极性图标**（取自灰机 wiki，
     不写中文）、`MR n`、`n 洗`、8 个等级点，卡下方报价条（武器名/价格/白金符号/卖家/状态）；
   - **中文词条筛选**：`wr 翁 攻击速度 范围 暴击伤害 负任意` 能解析成 4 个条件；
     38 组玩家叫法（攻速/射速、爆伤、爆率、多重、触发、范围、装填、毒电火冰、切割冲击穿刺、
     对 G/C/I/O 系…）；`负任意`要带负面、`无负`只纯正面；认不出的词条单独提示；
     **没命中退回最便宜 9 张并说明原因**；
   - 对派系伤害按游戏卡面写成倍率 `x1.43`（不是百分比）。
10. **紫卡排行改版**：
    - 之前是「全部在售均价前 30」→ 改为「**只算游戏中/在线卖家，取第 3~8 便宜的均价，取前 20 把武器**」；
    - 口径函数 `board.riven_value()`；口径版本 `RIVEN_METRIC_VERSION=2`（标志位 bit2 起存版本，旧缓存自动判待补扫）；
    - 文字列前 10，11–20 走图片（序号+武器图标+武器名+行情价+白金符号，前三名金色）；
    - 缓存 `data/board_cache.json` 是紧凑列表格式（v3：时间/候选/标志位/均价/样本数），需要落盘（`_save` 已处理）。

---

## 3. 当前任务：全量数据库（进行中）

### 已交付文件

| 文件 | 状态 | 说明 |
|------|------|------|
| `db/schema.sql` | ✅ 完成 | 377 行，10 大类表 + 3 个视图。PRAGMA foreign_keys=ON |
| `db/build.py` | ✅ 完成 | 幂等导入脚本，把全部 JSON 灌入 SQLite；联网部分失败只跳过不中断 |
| `data/warframe.db` | ✅ 已生成（8.7MB） | 本地 JSON 全量导入成功 |

### 本地已导入的 19 个数据源（sources 表）

```
items_cache          3893   warframe.market 物品表
names_zh             535    中译名（战甲/武器）
wiki_zh              983    维基中译名
baro_names_zh       29676   虚空商人物品唯一名→中文
warframe_alias        48    战甲黑话（Mesa→女枪）
frame_nicknames       17    战甲俗名
aliases              906    通用别名
frame_aliases        302    战甲别名
component_aliases    588    部件别名
sol_nodes            452    星图节点
bounty_static          3    固定赏金档位
bounty_objectives     56    赏金目标
bounty_rewards       445    赏金奖励池
riven_attribute_aliases  211  紫卡词条玩家叫法
riven_attribute_variants   17  拍卖接口变体写法
rotation_cache         3    轮换商人
conquest_zh          187    回廊术语
t2s                 3221    繁→简
price_quotes           5    垃圾/战甲地板价（来自本地空壳缓存，见下）
```

### 表清单（28 张表 + 3 视图）

- `meta` / `sources`（溯源）
- `items` / `item_tags`
- `zh_names` / `baro_names` / `frame_alias_zh` / `frame_nicknames`
- `item_aliases`（kind: general/frame/component）
- `sol_nodes`
- `bounty_locations` / `bounty_location_missions` / `bounty_tiers` / `bounty_tier_missions` / `bounty_objectives` / `bounty_rewards`
- `riven_weapons` / `riven_attributes` / `riven_attribute_aliases` / `riven_attribute_variants`
- `price_quotes` / `riven_market` / `riven_listings`
- `rotation_vendors` / `rotation_items`
- `conquest_terms` / `t2s`
- 视图：`v_item_detail` / `v_junk` / `v_riven_rank`

### 还缺什么（接手者要做的）

1. **紫卡武器表 / 词条表为空**（`riven_weapons=0`、`riven_attributes=0`）：
   - 这两张表来自 `https://api.warframe.market/v2/riven/weapons` 和 `.../v2/riven/attributes`；
   - **本机连不上 warframe.market**（超时），必须走服务器容器内代理 `http://xray:10809`；
   - 解法：在服务器容器内跑 `python db/build.py --online`，或把服务器拉取的 JSON 落盘后本地 build。
2. **行情快照多半为空**（`riven_market=0`、`price_quotes` 只有 5 行）：
   - 这些来自 `data/board_cache.json`，是 **bot 后台扫描产物**；
   - 开发机上的 `board_cache.json` 是空壳（272 字节）；服务器跑过扫描后才有完整数据；
   - 接手者应在服务器上把真实 `board_cache.json` 拉回本地再 build，或直接在服务器 build。
3. **`db/query.py` 还没写** —— 查询 CLI + 完整性校验（外键孤儿、重复别名、缺中文名等）。
4. **`tests/test_database.py` 还没写** —— 覆盖建表/导入/查询。
5. **`docs/DATABASE.md` 还没写** —— 表结构说明 + 重建方法。
6. **bot 代码未切换** —— 目前 bot 仍读 JSON，数据库是只读查询副本（符合最初计划「先建库，跑稳后再切」）。

### 怎么跑（已验证）

```bash
# 本地只导 JSON（已验证通过，8.7MB）
python db/build.py

# 联网补紫卡表（需能连 warframe.market，本机不行，放服务器跑）
python db/build.py --online
```

---

## 4. 关键坑（别再踩）

- **warframe.market 本机连不上**，接口调试/拉取必须走服务器容器内 xray 代理。
- **429 限流**：批量请求拍卖接口会被限流（探测时只扫了 25 把）。扫描间隔 `board.request_interval`
  默认 5s，**别调低**——调到 1s 反而触发 429，13 分钟只校准 16 把。
- **服务器部署用 `docker-compose.full.yml`**，不是 `docker-compose.yml`。
- **git bundle 同步**时，服务器 `aliases.json` 有独有条目「猴」，ff 合并后必须补回。
- **紫卡均价缓存落盘**：`board._save` 原本只存紧凑列表，均价字段（v3 标志位 bit2）要显式写进
  `_save` / `_load`，否则重启后排行回退到旧口径（已经修过一次）。
- **paramiko heredoc 坑**：多行 python 脚本在 git-bash heredoc 里 `\n` 会被 shell 变成 `/n`，
  务必先把 .py 文件 SFTP 上传再用 `python xxx.py` 执行。
- **极性图标**：取自灰机 wiki「文本图标·极性」的白色透明 PNG（原名 `Madurai_Pol_W.png` 等），
  原图地址 `https://huiji-public.huijistatic.com/warframe/uploads/<a>/<ab>/<Name>_Pol_W.png`
  （`huiji-thumb.../thumb/` 只预生成 21px，96px 会 NoSuchKey；`Any_Pol_w.png` 的 w 是小写）。
  9 个图标已放 `bot/assets/polarity/*.png`（随包发布）。

---

## 5. 给接手 AI 的具体下一步

1. 在服务器容器内执行 `python db/build.py --online`，把紫卡武器/词条表灌进 `data/warframe.db`
   （或拉回本地 build）；
2. 把服务器真实 `data/board_cache.json` 拉回，重新 `python db/build.py` 把行情快照灌入；
3. 写 `db/query.py`：封装常用查询（查物品/别名/紫卡榜/赏金/统计）+ 完整性自检；
4. 写 `tests/test_database.py` 和 `docs/DATABASE.md`；
5. （可选，后续）评估把 `bot/market.py` / `bot/riven.py` / `bot/board.py` 的 JSON 加载切换成查库。

---

## 6. 相关文件路径速查

```
bot/riven.py        紫卡武器/词条解析、中文筛选、ATTRIBUTE_GROUPS、riven_value()
bot/board.py        行情扫描、riven_value()、RIVEN_METRIC_VERSION、缓存落盘
bot/imagecard.py    紫卡卡面渲染、极性图标 _polarity_image()、排行图
bot/present.py      #wr / 紫卡排行回复文案
bot/commands.py    命令路由、RIVEN_MARKET_LIMIT=9
bot/assets/polarity/*.png   9 个极性图标
data/*.json        各来源字典（aliases/frame_aliases/names_zh/wiki_zh/sol_nodes/bounty_*/…）
data/board_cache.json  运行时行情快照（服务器才有完整数据）
db/schema.sql / db/build.py  本次数据库建设产物
```

---

## 7. 2026-11-23：QQ 官方机器人改造（代码与部署配置完成，待填凭证上线）

- 已**完全替换** OneBot/NapCat：删除 `bot/onebot.py`，`bot/ws.py` 保留用于连接官方 WSS 网关。
- 新增 `bot/qqofficial.py`（Access Token、WSS 网关、REST 发消息、富媒体图片上传）、
  `bot/qqevent.py`（官方事件归一化）、`bot/qqsignature.py`（Webhook Ed25519 验签）。
- 配置段由 `onebot` 改为 `qqbot`：`transport=websocket|webhook`、`app_id`、`app_secret`、
  `env=prod|sandbox`、`callback_path`、`intents`（默认 `1<<25`）。凭证目前为空，**线上不要直接部署**，
  否则官方通道不会连接；先把 AppID/AppSecret 填进配置。
- 网页控制台新增「QQ 官方机器人」面板：`bot/web.py` 增加 `/api/qqbot/{status,config,events,debug,send_test}`
  和免鉴权的 `/qq/callback`（op=13 回调验证 + op=0 验签后分发）；前端 `web/index.html`/`app.js`。
- 群策略白名单现在同时接受数字群号和 16–64 位 openid；`allow_group` 一键加白仍可用。
- 群 @ 消息由 `GROUP_AT_MESSAGE_CREATE` 触发；若要裸词唤醒，群主要开启「接收全部消息」；
  主动推送要开启「允许主动发送」。
- 部署层同步改造：`docker-compose.full.yml` / `docker-compose.yml` 删除 napcat 容器与
  `QQWF_WS_URL=ws://napcat:3001` / `NAPCAT_WS_TOKEN`，新增 `QQWF_APP_ID`、
  `QQWF_APP_SECRET`、`QQWF_TRANSPORT`、`QQWF_QQBOT_ENV`、`QQWF_CALLBACK_PATH`、
  `QQWF_SELF_ID`；`deploy/oneclick.sh` 生成新 `.env` 与上线提示，
  `deploy/baremetal.sh` 改用 `--app-id/--app-secret/--transport`，
  `install.sh` 与 `deploy/install_napcat.sh`（标为历史脚本）同步。
- 验证：`pytest -q` 894 passed（6 条既有 DeprecationWarning）、`bash -n` 四个脚本通过、
  `node --check web/app.js` 通过。
- 2026-11-23 已部署到服务器（commit `c6f7219`）：备份旧状态到
  `/root/qqwf-backup-20261123`，快进合并后重放服务器独有修改（aliases「猴」、
  市场/仲裁代理 none、db 8090 对外、entrypoint 可执行位），写入空值官方环境变量，
  用 `docker-compose.full.yml` 重建 bot 容器。napcat 容器保留未删（回滚余地）。
- 部署后验证：`/api/status`、`/api/qqbot/{status,config,events}` 带令牌返回正常；
  容器内 `qqofficial.py` 存在、`onebot.py` 已删、「猴」别名在；回调 `/qq/callback` 返回
  `{"op":12}`（签名缺失，符合预期）；bot 启动日志与物品索引正常。
- **当前线上状态**：AppID/AppSecret 仍为空，官方通道未连接，机器人不收发消息。
  这是唯一剩余前置。在 Web 控制台「QQ 官方机器人」面板填入即可即时生效（无需重启），
  或写入服务器 `.env` 后 `docker compose -f docker-compose.full.yml up -d bot`。
  websocket 模式不需要公网回调；webhook 模式另需公网 HTTPS（80/443/8080/8443）
  与 443→8080 反代。凭证到位前不要删 napcat 回滚容器。
- 2026-11-23 用户已在控制台填入凭证（AppID `1905720698`），网关 ONLINE。

## 8. 2026-11-23 补记：发送测试 HTTP 400 修复（已部署，commit `9c874cd`）

- 现象：控制台填好凭证、网关 ONLINE，但「发送测试」报 `HTTP 400 Bad Request`。
- 根因：官方返回体 `{"message":"请求的资源不存在(用户/群已注销)","err_code":40011028}`
  被本地错误层丢弃；用户填的 `12396883208788349398` 是机器人自身 ID 不是群 openid；
  订阅/白名单仍是旧数字 QQ 群号。
- 修复：`bot/qqofficial.py` 加 `_api_error_message()` 解析 body；`bot/app.py` 加
  `_check_test_target()` 拦截自身 ID/旧群号；前端提示文案同步。测试 897 passed。
- 关键：官方机器人只认 `group_openid`。让用户在群里 @机器人，网关事件带 group_openid，
  被白名单拦下后记录到控制台「群策略」blocked 列表，一键加白并复制作为发送目标。
  旧数字群号不会自动迁移。
- 服务器到 GitHub 一度不稳定，改用本地 `git bundle` 上传合并；合并前必须重放 4 处
  服务器独有修改（aliases「猴」、市场/仲裁代理 none、db 8090 对外、entrypoint 可执行位），
  备份在 `/root/qqwf-backup-20261123*`。

## 9. 2026-11-23：帮助菜单重排 + 实时动态背景（已部署，commit `c3ad406`）

- 需求：帮助菜单太单调，重做排版并加「像 Warframe 开始游戏界面」的背景，
  且随游戏内变化实时更新。
- 实现：
  - `bot/helpbg.py`（新增）：从世界状态 news 的 `imageLink` 取官方 key art
    （`www-static.warframe.com` 等白名单域名），经 worldstate 的代理下载，
    缩放后按首图 URL 哈希做缓存 key —— 新闻一变 key 就变，背景自动更换。
    磁盘缓存 `data/helpbg/`（最多 8 张），失败静默降级。
  - `bot/imagecard.py` 重写 `help_card`：分 5 组（市场行情 / 紫卡与遗物 /
    世界状态 / 仲裁排期 / 订阅与工具），命令金色 + 说明浅色，半透明行。
    背景铺底 + 上暗下亮的深色蒙版（`_help_canvas`），半透明元素先画到
    overlay 再 `alpha_composite`（避开低版 Pillow RGBA 不混合的问题），
    文字最后画保证最上层。无背景时降级为纯色渐变。
  - `bot/present.py` `help_reply()` 加可选 `bg_provider`；
    `bot/commands.py` 构造 `HelpBackground`（复用 worldstate.http），
    help 分支注入 `_help_bg_provider`（先 fetch news，回退 supplement 文件）。
  - `bot/config.py` 加 `helpbg` 默认段；`.gitignore` 忽略 `data/helpbg/`。
- 测试：新增 `tests/test_helpbg.py`（8 项）；完整 `pytest` 905 passed。
- 部署：本地→GitHub 用 `http.proxy=127.0.0.1:10808` 推送成功；
  服务器到 GitHub 不稳定，用 bundle `9c874cd..c3ad406` 上传合并，
  重放 4 处独有修改后重建 bot。线上实测：7 条 news→6 张 key art→
  下载 1000x562→生成 1.24MB 带背景卡片，缓存落盘 `/app/data/helpbg`。
  网关仍 ONLINE。
- 说明：背景图来自官方新闻 key art，游戏内新闻更新后随下一次 worldstate
  缓存刷新自动换图（缓存 key 变化触发重新下载），无需改代码或重启。
