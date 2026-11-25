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

## 10. 2026-11-23：启动器风格帮助 + 游戏场景推送背景 + 消息按钮（已部署，commit `7bef93c`）

- 需求澄清：帮助菜单要的是 Warframe 客户端「开始游戏」界面那种启动器主视觉，
  不是普通 key art 铺底；裂缝 / 九重天 / 仲裁 / 希图斯 / 夜灵等推送图改用
  游戏内任务场景；仲裁其余功能、wm 的价格走势与对话文本改为按钮并反馈。
- 帮助菜单：`bot/imagecard.py` 的 `help_card()` 重写为启动器风格——顶栏
  （最新动态 / PRIME ACCESS / 版本说明 / 简体中文）、中央 Tenno 徽记 +
  WARFRAME 大字 Logo + 中文新闻标题副标题、左下「检查新内容……」、
  右下红色斜切「开始游戏」按钮，下半区才是指令面板（5 分组不变）。
  `bot/helpbg.py` 新增从 news 的 `message` / `translations.zh` 提取中文标题
  （优先含中文的那条），随新闻实时更新，仍沿用 URL 哈希缓存。
- 场景背景：用图片渲染工具生成 5 张 Warframe 风格任务场景（裂缝 / 九重天 /
  仲裁 / 希图斯 / 夜灵），**右下角 AI 水印已用同图上方区域羽化覆盖去除**，
  以 JPEG 存在 `bot/assets/scenes/`（约 1.9MB）。`bot/scenebg.py` 负责读取缓存；
  `imagecard._scene_canvas()` 给表格卡铺底 + 深色蒙版；`present.py` 给
  `_world_reply` 加 `scene` 参数，并给裂缝 / 风暴 / 仲裁（含评级、排期、周表）/
  希图斯 / 夜灵卡片接入对应场景。任何读取失败自动降级纯色渐变。
- 消息按钮（QQ 官方 InlineKeyboard）：
  - `bot/keyboards.py` 构造按钮 JSON；仲裁卡挂 6 个按钮（谷拦 / 镜防 / 高效 /
    今日 / 仲裁表 / 仲裁评级），wm 卡挂 2 个按钮（价格走势 / 对话文本）。
  - `bot/replies.py` 的 `Reply` 新增 `keyboard` 字段；`qqofficial.py` 支持
    文本 / 富媒体带 keyboard 发送，并新增 `respond_interaction()`（PUT
    `/interactions/{id}`，3 秒内回应避免客户端 loading）。
  - `bot/qqevent.py` 把 `INTERACTION_CREATE`（type=11）归一化为 `post_type=interaction`；
    `app.py` 先回应互动再执行 `router.handle_button()`，用 `event_id` 被动回复结果。
  - `commands.py` 存 30 分钟有效的按钮上下文 token：`a:命令` 直接复用命令路由，
    `wm:trend/desc:<token>` 返回 `present.price_trend_reply()` /
    `item_desc_reply()`（对话文本卡参考 warframe.market 物品页说明）。
  - **intents 必须含 `1<<26`**：默认与线上配置均为 `100663296`
    （`1<<25 | 1<<26`），否则收不到按钮点击。
- 线上已实测：文本+keyboard、图片+keyboard 两种消息官方均返回消息 ID（无 400）；
  5 张场景图容器内均可加载；互动事件归一化正确；网关 ONLINE。
- 测试：新增 `tests/test_buttons.py`（11 项），完整 pytest **916 passed**。
  部署：`671077b` 主体 + `b268731` / `7bef93c` 两个标题修复，均 bundle 上传
  合并后重建容器；GitHub 最终推送成功（`be87a0c..7bef93c`）。
- 注意：去水印采用「同图上方画面羽化覆盖」而非 OpenCV（本机 pip 装不上 cv2），
  放大检查右下角已无「AI生成 / WORKBUDDY」字样。

## 星图节点译名（2026-11-25）

- **普通星图节点**：译名来自 DE 公开导出（`content.warframe.com/PublicExport/`
  的 `ExportRegions_{en,zh}.json`，按 `uniqueName` 对齐），即**官方简体中文
  客户端里显示的名字**。已落到 `data/node_names_zh.json`（76 条，只收
  「中文名≠英文名」的节点），由 `tools/build_node_names.py` 生成，
  `python tools/build_node_names.py --check` 可校验是否需要更新。
- **注意**：官方中文客户端里**大部分节点仍然显示英文原名**（Tessera、Venera、
  Nabuk、Cambria…），这些不要去音译，否则卡片会和游戏内对不上。真正汉化过的
  例如 Apollodorus→阿波罗多罗斯撞击坑、Mithra→光理塔、Tharsis→萨西斯高原、
  Tuvul Commons→涂沃主厅。
- **航道星舰（九重天）节点**：官方导出不含比邻星域，译名手工维护在
  `bot/terms.py` 的 `STORM_NODES`（来源：官方更新公告 + 灰机 wiki DropTable
  词表，如「金星比邻星域：全新的生存任务节点，无垠华盖」）。整体 57 条，
  形状为 `地球比邻星域 - 克姆地带`。
- 实现：`terms.localize_node()` 拆「名字（括号地点）」→ 名字查
  `STORM_NODES` / `_node_names_zh()`，地点查 `PLANETS`（普通节点）或
  `STORM_REGIONS`（九重天节点，按名字是否在 `STORM_NODES` 判定）。
  `data/node_names_zh.json` 已加入 Dockerfile 的 `builtin-data` 打包清单
  （宿主机 `./data` 挂载缺文件时的兜底）。

## 响应速度优化（2026-11-25）

实测环境：腾讯云 124.220.229.109（4C/3.6G，带宽小），容器 `qqwarframe-bot`。
结论：**慢不在 CPU/渲染，而在"请求数 × 每次握手的跨境延迟"和"图片 base64 上传"**。

| 项 | 实测 |
| --- | --- |
| 服务器负载 | load 0.08，容器 CPU 0.03%，内存 135MB（不是瓶颈） |
| 市场接口单请求 | /top 只有 5KB 也要 1.5~4s，TLS 握手 0.6~1.6s（跨境抖动大） |
| 整包订单 /v2/orders/item/{slug} | 60~700KB，慢网下 1~3s |
| 图片上传 | QQ 接口把图 base64 塞 JSON，640KB 载荷 2.26s、933KB 3.34s |
| 世界状态主源 warframestat | 对本机机房 IP 一律 403，经 xray 更慢（2.3s） |

已做的改动：
1. `bot/net.py`：强制直连时用 `http.client` 长连接池（按线程隔离，复用被服务端关掉会自动重连），
   去掉每请求一次 TLS 握手；403/407 的代理记 15 分钟，`_try_order` 把它排到最后。
2. `bot/market.py`：`/top` 两边各有 3 单就不再补拉整包订单（原来 limit=7/8 必然触发）。
3. `bot/commands.py`：查价的「详情 / 订单 / 48h 行情」并发；帮助背景先用本地 news 快照，
   取新闻限时 3s（中转慢时原实现能拖到 76s）。
4. `bot/imagecard.py` + `bot/nodeboard.py`：超过 200KB 的卡片转 256 色调色板 PNG
   （文字仍锐利），裂缝 433→194KB、九重天 607→189KB，上传省 1~3s。
5. `bot/worldstate.py`：主源 403 直接按 6 小时冷却，不再每 10 分钟白撞一次。
6. 启动时预热帮助卡背景（`warm_help_background`）。

`#查` 由 8.0s → 2~3s，`#战甲` 6.1s → 1.3~2.9s，`#帮助` 首次 7.5s/76s → 2.6s（预热后只差渲染）。
冷缓存下 `#遗物` 仍要 4~7s（6 个奖励各一次 /top，受 3 rps 限流约束），`#仲裁` 首轮 ~9s（官方 + 中转）。

## wm 卡片改版：行情按钮 + 对话文本（2026-11-25）

- **卡片瘦身**：`imagecard.item_card` 删掉「行情」三宫格与 15 天明细表，
  只留在售 / 收购两列报价 + 一行按钮提示 + 一句 wiki 说明。原来的
  `_stat_block` / `_trend_table` 一并删除（已无调用）。
- **按钮**（`bot/keyboards.py` 的 `wm_keyboard`）：
  `行情` → `wm:trend:<token>`，`对话文本` → `wm:chat:<token>`。
  旧的 `wm:desc:` 仍按对话文本处理，历史消息里的按钮点了也不会失效。
- **行情 = 天梯图**：`imagecard.price_trend_card` + `_ladder_chart`
  把近 15 天日均价画成阶梯折线（横向段=当天价位、竖线=换天跳变），
  背后叠当日最高/最低区间竖条、阶梯下方金色渐变填充，右侧气泡标最新价。
  刻度用 `_nice_ticks` 取 1/2/2.5/5×10^n 的整数价格，涨跌沿用国内涨红跌绿。
- **对话文本 = 私聊话术**：`present.whisper_reply` 按订单生成
  `/w 玩家 Hi! I wtb <英文物品名> for <价>p`（在售=我想买）与
  `wts`（收购=我想卖），可直接复制到游戏聊天框。
- 按钮上下文（`commands._store_button_context`）额外存 `sell` / `buy`；
  子卡片回传同一套按钮，行情↔话术可反复切换，30 分钟内有效。

## 按钮为什么不显示：富媒体带不了键盘（2026-11-25 修复）

**现象**：查价卡片底部写了「点下方按钮」，实际客户端一条按钮都没有。

**根因**（官方文档明确）：`msg_type=7` 富媒体消息只认 `media.file_info`，
`keyboard` 字段**被平台静默忽略**——接口返回 200 不报错，按钮不会渲染。
第三方适配器文档也写明「富媒体消息(msg_type: 7)无法携带原生 Markdown/按钮」。
`msg_type=8` 的图文卡片要求 `pic_url` / `url` 必须是 https 外链（我们没有公网图床），
`msg_type=2` Markdown 也需要图床且要 Markdown 权限，都不适合本项目的本地生成图片。

**修复**：图片与按钮拆成两条消息发。
- `bot/app.py::_deliver`：先发图片（不再传 keyboard），图片成功后再发一条短文本，
  文本里挂 `keyboard`，`reply.button_hint` 决定这行字（wm 用「▼ 行情走势 · 私聊话术」，
  其他卡片默认「▼ 选择下方操作」）。按钮消息失败只记日志，不重复整条。
- `bot/qqofficial.py`：`send_group_image` / `send_private_image` 去掉 keyboard 参数，
  免得再有人以为挂上就能用。
- **`msg_seq` 必须每条递增**：同一条被动消息回多条（图 + 按钮条 + 长文分段）时，
  平台按 `msg_id + msg_seq` 去重，重复会报 `40054005 消息被去重`。
  现在 `reply_context()` 只快照 `msg_id`（`msg_seq=0`），真正发送时在
  `_reply_fields()` 里分配下一个序号；`remember_reply_context()` 的老路径同样递增。
- 群聊被动回复上限是「每条用户消息 5 次（60 分钟内）」，图 + 按钮条用掉 2 次，够用。

**排查要点**：客户端不显示按钮但接口 200，先看是不是富媒体；不要在 `msg_type=7`
里调 keyboard。日志里 `图片发送失败（含按钮时）` 那条是历史代码的回退分支，已删除。

## 改用官方素材 + Markdown：一条消息放图放按钮（2026-11-25 22:40）

上一版把图片和按钮拆成两条消息只是**兜底**，不是最优解。官方真正的正规路径是
「素材管理」+ Markdown：

1. `POST /v2/media-images/prepare`（要 file_name / file_size / content_type / md5 / sha1）
   → 拿到 `upload_id` + COS 预签名 URL
2. `PUT <presigned_url>` 直传 PNG（Content-Type 必须与 prepare 声明一致，否则 COS 403）
3. `POST /v2/media-images/complete`（upload_id + md5）→ 返回
   `url: qqbot-media://image/v1/<token>` 与 `image_id`
4. 发 `msg_type=2` Markdown：正文 `![卡片 #860px #Hpx](qqbot-media://…)` + `keyboard`

这样图片和按钮在**同一条消息**里，且全部是官方支持的字段（Markdown 能力 2026/04/23
起对所有机器人在单聊/群聊开放，不再需要模板权限）。

实现（`bot/qqofficial.py`）：
- `_upload_material` / `_material_flow`：三步上传，PNG 宽高直接从 IHDR 读（16~24 字节）。
- `send_group_card` / `send_private_card`：一条 Markdown 消息，图 + 可选 caption + 键盘。
- **配额**：单机器人 100 张、90 天滑动过期。发送成功后把 `image_id` 丢给后台
  `_material_reaper` 线程，**延迟 15 秒删除**（平台在发送时已转存副本，删了不影响
  已发消息，但能保住配额）；`complete` 返回 40035007 时 `_purge_materials()` 清空
  本机器人素材再重试一次（10 分钟冷却）。
- **回退**：`app._deliver` 里 Markdown 卡片抛 `QQOfficialError` 就退回
  「富媒体图片 + 带键盘短文本」，保证按钮不会因为一次接口抖动整条丢光。

实测开销（境外到 api.bot.qq.com）：prepare 0.63s + PUT 0.20s + complete 0.65s
≈ **1.5s/张**，比富媒体上传（~0.3s）多 1.2s。DELETE 0.4s 在后台，不占回复耗时。

## 按钮回执：3 秒窗口与「请求第三方失败」（2026-11-25 22:55）

现象：点按钮后客户端转圈几秒，弹「请求第三方失败」，随后结果消息还是到了。

排查结论：
- `PUT /interactions/{id}` 的 code=0 只是「已收到」回执；客户端在 3 秒内拿不到就弹上面那句。
- **`api.bot.qq.com` 对 `/interactions/{id}` 也返回 `200 {}`**（拿假 UUID 试也是 `{}`，
  而未知路由会 404），也就是说打错域名不报错、只是不生效。
  回执统一改走 `api.sgroup.qq.com`（`INTERACTION_API` 常量，可用 `qqbot.interaction_api` 覆盖）。
- 回执从「事件提交线程池后执行」改成**网关读循环里收包就地发**（`ack_interaction`），
  不再排队等图片渲染/网络请求。日志会打「按钮回执：投递延迟 X，回执耗时 Y」。
- `app._on_interaction` 里不再重复回执（同一 interaction_id 只能回一次）。

如果真机日志显示「投递延迟 + 回执耗时 > 3 秒」，说明是平台投递延迟吃掉了窗口，
那就得把按钮换成 `action.type=2`（指令按钮：点击后把 `@机器人 命令` 填进输入框，
群里要用户自己按发送；单聊可 `enter=true` 自动发），这条路不产生 INTERACTION_CREATE，
不会有 loading 和超时提示。

## 按钮回执真正的原因：ID 取错了层（2026-11-25 22:56）

上一版把回执改走 `api.sgroup.qq.com` 并就地发送后，真机依旧转圈后弹「请求第三方失败」。
日志显示回执本身只花 **0.30s**，远低于 3 秒窗口，所以不是超时，是**回执打到了一个
平台不认识的 ID**——`api.bot.qq.com` / `sgroup` 对 `/interactions/{任意假 ID}` 都返回
`200 {}`，所以「不报错 ≠ 生效」。

**根因**：`qqevent.interaction_event()` 里 `qq_interaction_id` 取的是**网关信封**的
`payload["id"]`（形如 `INTERACTION_CREATE:xxxx`），而官方文档明确「interaction_id
从 INTERACTION_CREATE 事件的 `id` 字段获取」，指的是事件体 `d.id`。两者不是一回事。

**修复**：
- `qq_interaction_id` 改为 `d.id`，缺失才回退信封 id；另外保留 `qq_envelope_id`，
  回执首次失败时用它再试一次（回执只有 3 秒，最多重试一次）。
- 同一个 id 也用于被动回复（`app._on_interaction` 的 `reply_ctx={"event_id": id}`），
  所以之前「结果还是出来了」其实是被动回复失败后回退成主动消息。
- 日志加 `id=<前 8 位>`；首次收到互动事件时打一条「互动事件字段：envelope=[…] d=[…]」，
  以后再有人怀疑字段层级，看这条就知道。
- `_event_delay` 支持毫秒时间戳（`d.timestamp` 可能是数字），投递延迟不再显示「未知」。

**如果修完还弹**：说明平台把事件投递到服务端本身就吃掉 3 秒（跨境），callback 按钮
在群里无解，届时把「行情 / 对话文本」换成 `action.type=2` 指令按钮（点击后把
`@机器人 #走势 …` 填进输入框，用户按发送；不产生 INTERACTION_CREATE，不会 loading/超时）。

## 按钮链路端到端验证（2026-11-25 22:57）

回执修好后（`53c8c36`）在容器内直接跑命令层验证，绕开 QQ 点击，确认三条分支都对：

| 分支 | 结果 |
|---|---|
| `#查 Mesa Prime 一套` 主卡 | 1 条回复，2.65s，`button_hint=▼ 行情走势 · 私聊话术`，keyboard 里两个按钮 `wm:trend:<token>` / `wm:chat:<token>` |
| `wm:trend:<token>` | 0.00s（数据已在上下文里），文本「均价 54.3p ｜ 最高 120p ｜ 最低 35p ｜ 变化 -1.0%」，天梯图 PNG 46KB，带同款按钮 |
| `wm:chat:<token>` | 0.00s，纯文本：卖家 5 条 `/w xxx Hi! I wtb Mesa Prime Set for 49p`、买家 5 条 `wts`，带同款按钮（可切回行情） |
| `wm:trend:deadbeef` | 「按钮已过期，请重新发送 wm 查询。」 |

- 两个子卡都复用同一个 token（`_button_contexts` 30 分钟过期），来回切换不重新查市场。
- 文字路径（话术）也会把键盘挂在**最后一段**（`app._deliver` 1742 行），所以纯文本同样有按钮。
- 卡片发送走「素材 + Markdown」，`_deliver` 返回 True 即图文与按钮同一条消息；素材路径失败才退回
  「富媒体图片 + 带键盘短文本」。
- 复现脚本：`probe_wm_buttons.py`（命令层 + 渲染）、`send_real_wm_card.py`（往测试群真发一张卡）。

### 按钮往返点击验证（2026-11-25 23:00）

走**真实调用点** `app._on_interaction`（不是直调 `handle_button`），只把 `_deliver` 换成记录器：

```
主卡 #查 Mesa Prime 一套  3.09s  82,804 字节  按钮 True  hint=▼ 行情走势 · 私聊话术
1. 行情     0.10s  图 46,278  按钮 True  同 token  被动回复 id 正确
2. 对话文本 0.00s  纯文本     按钮 True  同 token  被动回复 id 正确
3. 行情     0.09s  图 46,278  按钮 True  同 token  被动回复 id 正确
4. 对话文本 0.00s  纯文本     按钮 True  同 token  被动回复 id 正确
四次形态：['图+按钮', '纯文本+按钮', '图+按钮', '纯文本+按钮']，token 全程一致
```

结论：两个按钮可无限次来回切，token 不消耗、子卡都带全套按钮，纯文本回复（话术）也会把键盘
挂在最后一段；`reply_ctx["event_id"]` 就是本次 interaction_id（被动回复不会串）。

**踩坑备忘（下次别再被误导）**：`BotApp` 默认读的是 `config.json` 里的**真实白名单**，
脚本里改 `blacklist_groups` 不够，`whitelist_groups` 非空时测试群 ID 会被 `_group_allowed`
拦掉、表现为「点了没任何回复」。探测脚本要同时清空白名单或直接换成配置里的群 ID。

**已知限制**：按钮上下文 token 存在**进程内存**（`_button_contexts`，30 分钟过期）。
从一次性脚本发的卡片（如 `send_real_wm_card.py`）token 随进程消失，真机点会提示
「按钮已过期」——这是预期行为，不是 bug。真机验证要让**在线机器人**发卡：群里发
`#查 Mesa Prime 一套`，再点按钮。若要让旧卡片/重启后仍可点，需要把 token 做成自包含
（slug + platform，点击时重新查市场）。

## 点按钮「不反馈图片」：被动回复字段填错（2026-11-25 23:05）

真机点击后客户端毫无反应，日志立刻给了答案：

```
22:56:39 按钮回执：id=3ad554aa… 投递延迟 1.40s，回执耗时 0.34s
22:56:39 WARNING 发送消息失败：/v2/groups/{openid}/messages 失败：
         请求参数event_id无效 / 错误码 40034025（HTTP 400）
```

- **回执没问题**（0.34s，客户端不再转圈），坏在**结果消息**发不出去：
  互动事件 id 在官方文档里是「用于被动消息发送和互动回调」，被动回复的字段名是
  **`msg_id`**；我们上一版填进了 `event_id`，平台直接 400。
- 于是「点了按钮什么都不出」= 结果消息全军覆没，`_deliver` 只记了一条 WARNING 就返回 False。

**修复**（`bot/app.py`）：
1. `_on_interaction` 的 `reply_ctx` 改成 `{"msg_id": interaction_id}`。
2. `_deliver` 增加**被动→主动**兜底：`_passive_rejected(exc)` 命中 `40034025 /
   event_id / msg_id / msg_seq / 40054005 / 被动消息` 任一标记时，清空 `reply_ctx`
   用主动消息重发一次。原因：互动事件不一定开被动回复窗口，`msg_id` 也可能被判无效；
   有这条兜底，无论平台认哪条路，用户都能拿到图。
   （实现上是把 `_deliver` 里的发送调用收敛成 `send_card / send_image / send_text`
   三个闭包，统一经 `call()` 走「先被动、被拒则主动」。）
3. 主动消息有频次限制，所以仍然**优先被动**，只在被拒时退化。

**新增测试**：`test_passive_rejected_retries_as_active`（客户端对带 reply_ctx 的发送
抛 40034025，断言会不带上下文重发一次）、`test_button_reply_uses_msg_id`（按钮回复的
上下文必须是 `{"msg_id": interaction_id}`，且不能出现 `event_id`）。

**排查口诀**：按钮点了没反应，先看 `发送消息失败` 后面的错误码——
`40034025` 是回复字段填错，`40054005` 是 msg_seq 重复，`305007` 是键盘结构错。

### 真平台验证兜底（2026-11-25 23:08）

用进程内真 token + 假 interaction id 走 `_on_interaction`，看下行链路是否一定能落地：

```
token: c563af36cd
被动回复被拒（/v2/groups/{openid}/messages 失败：请求参数msg_id无效或越权 /
            错误码 40034024（HTTP 400）），改为主动消息重发
_on_interaction 耗时 4.76s
```

- 兜底生效：被动被拒后自动用主动消息重发，没有异常抛出（否则 `_on_interaction` 会崩）。
- 4.76s 的构成：卡片渲染 ~0.4s + 素材上传 ~1.5s + 一次被拒的被动尝试 ~0.4s + 主动重发
  （含再次素材上传 ~1.5s）+ 发送。真机上如果 `msg_id`（interaction id）合法，第一次就成功，
  会比这快，日志里也不会出现「被动回复被拒」。
- **错误码对照**：`40034024 msg_id 无效或越权` / `40034025 event_id 无效`。
  前者说明互动事件不提供被动窗口（或 id 不对），必须走主动消息；后者是字段填错了名字。

## 按钮结果发不出去（二）：互动事件没有被动窗口 + msg_seq 撞号（2026-11-25 23:12）

上一版改成 `msg_id=interaction_id` 后，真机点击仍然没东西，事件流给出的序列：

```
23:07:24 in  INTERACTION_CREATE
23:07:25 out 文本：按钮已过期…（成功，走了「最近一条用户消息」的被动回复）
23:07:32 in  GROUP_MESSAGE_CREATE / "wm 电男"
23:07:37 out 图文卡片 79651 字节（含按钮，seq=1）
23:07:40 in  INTERACTION_CREATE
         被动回复被拒 40034024 msg_id无效或越权   ← 用 interaction id 当 msg_id
         再按最近消息重发 → 40054005 消息被去重    ← seq 又算出 1，和卡片撞了
```

两条结论：
1. **互动事件不提供被动回复窗口**：真实 interaction id 当 `msg_id` 也会 `40034024`。
   所以按钮结果只能「回复该会话最近一条用户消息」，或发主动消息。
2. 去重是因为 **两套序号计数器**：卡片走 `reply_ctx={"msg_id":…}` 分支用
   `_ctx_seq[chat/msg_id]`，回退走 `_reply_ctx[chat]["seq"]`，各算各的 → 都从 1 开始。

**修复**：
- `bot/app.py::_on_interaction` 不再把 interaction id 塞进回复上下文，改为 `None`
  （交给「最近用户消息」逻辑），并打一条 `按钮点击：id=… data=…`。
- `bot/app.py::_deliver` 的发送收敛成三级降级：`指定上下文 → None（最近用户消息）→ {}
  （主动消息）`，每级只在 `_passive_rejected` 命中时继续降级。
- `bot/qqofficial.py`：新增 `_next_seq()`，**所有路径共用一张 `_ctx_seq` 序号表**；
  `_reply_fields` 现在区分三态——dict=指定被动、`None`=最近消息、`{}`=明确主动。
- `PASSIVE_WINDOW` 3500ms → **280s**（平台是 5 分钟，3.5 秒会把按钮场景的窗口白扔掉，
  导致每条按钮结果都被迫走主动消息）。
- 新增日志 `发送被拒（…），换发送方式重试`，以后再出问题能直接看出降到了第几级。

## 紫卡：改名「紫卡倾向」+ 新增截图分析「紫卡分析」（2026-11-25 23:50）

用户要求：①把原来的「紫卡分析」（按武器组看范围）改名「紫卡倾向」；
②新增「紫卡分析」= 传紫卡截图做识别与评分；OCR **直接用 riven.mashiro.games 那个软件**。

### 1) 改名

- `command_meta.riven_group` → 「紫卡倾向」；`COMMANDS` 新增 `紫卡倾向` 别名，把
  `紫卡分析` 让给新命令 `riven_scan`（`紫卡识别 / 识图 / 分析紫卡 / zkocr` 同义）。
- 帮助卡、render 文本、README、present 文案同步改成「紫卡倾向」。

### 2) riven.mashiro.games 到底是什么（关键结论）

那个站点是**浏览器扩展**（Chrome/Edge/Firefox zip，v0.2.20），识别在**本机**用
PaddleOCR PP-OCRv5（onnxruntime-web/WASM）跑，**没有服务端 API 可调**。
所以「直接用它」的落地方式是：把它扩展包里的模型与参数搬到服务端：

- `vendor/ppocr/ch_PP-OCRv5_det_mobile.onnx`（4.8MB）
- `vendor/ppocr/ch_PP-OCRv5_rec_mobile.onnx`（16.6MB）
- `vendor/ppocr/ppocrv5_dict.txt`（18383 字）
- 预处理/后处理照 `paddle-ocr.js`：det 长边 960、32 对齐、mean(0.485/0.456/0.406)、
  std(0.229/0.224/0.225)、BGR、阈值 0.3 / box 0.5 / unclip 1.65；
  rec 高 48、宽按比例（≤2048）、`(x/255-0.5)/0.5`、CTC 去重 + 置信度 0.45。

模型放 `data/ppocr/`（构建时复制到 `builtin-data/ppocr/`），依赖 `numpy` + `onnxruntime`。
单张截图 ~0.9s（首次加载模型 1.5s，`app._prewarm` 会预热）。

**踩坑**：rec 输出是 `[T, C]`（C=18385=字典+blank+空格）。一开始按「两个维度谁大」
判断要不要转置，T(59) < C(18385) 被误转置 → 输出全是「厂乙入」这种部首乱码。
正确判断是「最后一维是否等于 字典长度+2」。

### 3) 功能实现

- `bot/ppocr.py`：det/rec 推理 + 连通域框 + 行聚类（同 y 中心 0.55 倍高度内合并）。
  缺 numpy/onnxruntime 或模型时 `available()` 为 False，命令降级提示，不影响其它功能。
- `bot/rivenscan.py`：解析层沿用旧 Tesseract 时代的踩坑成果（词条别名匹配、最近数字
  取值、卡面符号优先、2+/2+1/3+/3+1 配置搜索、Lv.0~8 ↔ 1/9~9/9 换算、兜不住就直说），
  只把 OCR 入口换成 `ppocr.recognize`；新增 `guess_weapon`（精确→包含→difflib 模糊）
  与 `scan()` 高层入口。
- `bot/commands.py::_riven_scan` + 命令分发；`bot/app.py` 恢复图片下载：
  只对 `riven_scan` 下载图片（别的命令不花这次跨境请求）；**纯图片消息**
  （群里 @机器人 发图）也当成紫卡分析，文字部分当武器名提示。
- `bot/present.py`：`riven_scan_help` / `riven_scan_reply`；
  `bot/imagecard.py`：`riven_scan_card`（词条 / 卡面 / 满级区间 / 位置 / 评价 五列）。
- 测试：`tests/test_rivenscan.py`（解析、配置识别、未升满、模糊武器名、端到端 mock、
  命令层、模型文件存在），并同步改了 `test_commands` / `test_riven_group` 里的旧断言。

### 4) 部署注意

`data/ppocr/*.onnx` 入库（21MB），服务器 `data/ppocr` 已手工同步过，
`git merge` 前要先 `rm -rf data/ppocr`（否则未跟踪文件冲突），重建镜像会装 numpy/onnxruntime。
