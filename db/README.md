# qqwarframe-db

独立的只读 SQLite HTTP 服务，不依赖 QQ、OneBot 或 bot 运行时。

```text
QQ Warframe Bot / SQLite
        │
        ├── db/build.py     构建数据库
        ├── db/query.py     本地 CLI
        └── db/server.py    独立 HTTP 服务
```

## 1. 构建数据库

在主项目根目录：

```bash
python db/build.py
# 联网补紫卡表（需要能访问 warframe.market）
python db/build.py --online
```

产物：

```text
data/warframe.db
```

## 2. 启动独立服务

```bash
python db/server.py --db data/warframe.db --host 127.0.0.1 --port 8090
```

默认只绑定 `127.0.0.1`，不对外网开放。

浏览器打开：

```text
http://127.0.0.1:8090/
```

页面包含数据库状态、物品搜索、物品详情、套装部件、完整性校验和数据源列表。

带 token：

```bash
python db/server.py --db data/warframe.db --token '你的token'
# 或
DB_API_TOKEN='你的token' python db/server.py --db data/warframe.db
```

## 3. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 网页管理界面 |
| GET | `/health` | 健康检查，不需要 token |
| GET | `/stats` | schema、行数、数据源 |
| GET | `/sources` | 导入源清单 |
| GET | `/search?q=女枪&limit=10` | 物品/别名搜索 |
| GET | `/item?q=mesa_prime_set` | 物品详情、标签、别名、部件 |
| GET | `/parts?q=女枪` | 套装 → 部件 |
| GET | `/validate` | 完整性与数据质量校验 |

设置 token 后，除 `/health` 外需要：

```http
X-API-Token: 你的token
```

或：

```http
Authorization: Bearer 你的token
```

示例：

```bash
curl -s http://127.0.0.1:8090/health
curl -s -H 'X-API-Token: 你的token' \
  'http://127.0.0.1:8090/search?q=%E5%A5%B3%E6%9E%AA'
curl -s -H 'X-API-Token: 你的token' \
  'http://127.0.0.1:8090/parts?q=mesa_prime_blueprint'
```

## 4. Docker

```bash
docker compose -f docker-compose.db.yml up -d --build
```

- 只把 `./data` 以只读方式挂进容器
- 宿主机端口默认绑定 `127.0.0.1:8090`
- 可选设置 `DB_API_TOKEN`

## 5. systemd

```bash
sudo cp deploy/qqwarframe-db.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qqwarframe-db
```

按实际路径修改 `WorkingDirectory`、`ExecStart` 和 `ReadOnlyPaths`。

## 6. 独立性

- 服务只 import 标准库 + `db/query.py`
- 不 import `bot/*`
- 不启动 OneBot、不需要 QQ
- 数据库是只读打开；写操作只能通过 `db/build.py` 重建
- 可以单独部署到另一台机器，只要把 `data/warframe.db` 放过去

## 7. 测试

```bash
python -m unittest tests.test_database_server -v
```

覆盖健康检查、鉴权、stats、search、item、parts、validate、404 和数据库缺失。
