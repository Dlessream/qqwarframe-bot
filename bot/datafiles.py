"""静态数据文件定位（含镜像内置副本兜底）。

`data/` 目录同时承载两类文件：

* **静态知识**：别名、赏金配置、遗物库、MOD 译名、译名表、星图……随代码发布；
* **运行时缓存**：items_cache / board_cache / subscriptions / warframe.db……部署后产生。

`docker-compose` 会把宿主机的 `./data` 挂到容器的 `/app/data`，宿主机目录里通常
只保留运行时文件（部署时 `data/` 往往不参与代码同步，避免覆盖线上数据）。
结果就是**新加的静态数据文件在容器里凭空消失**——`relics_local.json`
报 `[Errno 2] No such file or directory` 正是这个原因。

解析顺序：

1. `<项目根>/data/<name>`          宿主机挂载进来的文件（允许运维热改）
2. `<当前工作目录>/<rel>`          兼容从别的 cwd 启动
3. `<项目根>/builtin-data/<name>`  镜像构建时打包的静态数据副本（一定在）

模块只负责**读**的定位；需要写回的文件（例如 aliases.json）仍应使用 data/ 路径。
"""

from __future__ import annotations

import os
from typing import List

#: 项目根目录（`bot/` 的上一级）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 镜像内置静态数据目录（Dockerfile 构建时从 data/ 复制而来）
BUILTIN_DIR = os.path.join(ROOT, "builtin-data")


def candidates(rel: str) -> List[str]:
    """列出某个相对路径的所有候选位置（按优先级）。"""
    raw = str(rel or "").strip()
    if not raw:
        return []
    if os.path.isabs(raw):
        # 绝对路径也留一条内置副本的退路（读场景），写场景由调用方自己保证
        paths = [raw]
        name = os.path.basename(raw)
        if name:
            paths.append(os.path.join(BUILTIN_DIR, name))
        return paths
    paths = [os.path.join(ROOT, raw), os.path.abspath(raw)]
    name = os.path.basename(raw)
    if name:
        builtin = os.path.join(BUILTIN_DIR, name)
        if builtin not in paths:
            paths.append(builtin)
    return paths


def resolve(rel: str) -> str:
    """返回第一个实际存在的候选路径；都不存在时返回首选路径，由调用方报错。"""
    paths = candidates(rel)
    for path in paths:
        if os.path.isfile(path):
            return path
    return paths[0] if paths else str(rel)


def resolve_in(rel: str, directory: str) -> str:
    """在指定目录里找同名文件，找不到再退回 :func:`resolve`。

    用于「配置文件指定了目录，但该目录被挂载覆盖」的场景。
    """
    raw = str(rel or "").strip()
    if raw and directory and not os.path.isabs(raw):
        local = os.path.join(directory, raw)
        if os.path.isfile(local):
            return local
    return resolve(raw)
