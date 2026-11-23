"""节点 -> 游戏内任务场景图（tile set）映射。

依据 WARFRAME Wiki「Tile Sets」页：星图共 25 个官方 tile set，节点复用场景。
每个 tile set 按其环境（星球）+ 建筑风格（派系）生成一张实机风格背景，
共 22 张（覆盖所有主要星球×派系组合），存于 bot/assets/tilesets/。

节点映射规则：
  节点名括号里的星球 + 节点 enemy 派系 -> scene key；
  无匹配时按 (派系 -> 默认场景) 或 (星球 -> 默认场景) 回退。

本模块只提供图片资源选择，不改文本/按钮/文本回复。
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional

_ASSET_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "assets", "tilesets")

# 星球标准名（英文，来自节点名括号）-> 归一化 key
_PLANET_ALIAS = {
    "earth": "earth", "venus": "venus", "mercury": "mercury", "mars": "mars",
    "phobos": "phobos", "ceres": "ceres", "jupiter": "jupiter",
    "europa": "europa", "saturn": "saturn", "uranus": "uranus",
    "neptune": "neptune", "pluto": "pluto", "eris": "eris",
    "sedna": "sedna", "void": "void", "lua": "lua", "deimos": "deimos",
    "kuva fortress": "kuva_fortress", "veil": "veil", "zariman": "zariman",
    "duviri": "duviri",
    # 中文本地化名（节点显示名如 'Galatea（海王星）'）
    "地球": "earth", "金星": "venus", "水星": "mercury", "火星": "mars",
    "火卫一": "phobos", "谷神星": "ceres", "木星": "jupiter",
    "木卫二": "europa", "土星": "saturn", "天王星": "uranus",
    "海王星": "neptune", "冥王星": "pluto", "阋神星": "eris",
    "塞德娜": "sedna", "虚空": "void", "月球": "lua", "火卫二": "deimos",
    "库瓦要塞": "kuva_fortress", "帷幕": "veil", "扎里曼": "zariman",
    "杜维里": "duviri",
}

# 派系英文 -> 归一化 key（含中文/别名）
_FACTION_ALIAS = {
    "grineer": "grineer", "克隆尼": "grineer",
    "corpus": "corpus", "科普斯": "corpus", "科波斯": "corpus",
    "infested": "infested", "infection": "infested", "感染者": "infested",
    "orokin": "orokin", "奥罗金": "orokin",
    "corrupted": "orokin", "堕落者": "orokin",
    "sentient": "sentient", "感知者": "sentient", "灵煞": "sentient",
    "the murmur": "murmur", "murmur": "murmur", "低语者": "murmur",
    "crossfire": "crossfire", "anarch": "anarch", "tenno": "tenno",
}

# (星球, 派系) -> 场景图 key（22 张现有图，全部经去水印转存）
_PAIR_SCENE = {
    ("venus", "corpus"): "venus_corpus",
    ("mars", "grineer"): "mars_grineer",
    ("void", "orokin"): "void_orokin",
    ("eris", "infested"): "eris_infested",
    ("saturn", "grineer"): "saturn_grineer",
    ("neptune", "corpus"): "neptune_corpus",
    ("pluto", "corpus"): "pluto_corpus",
    ("jupiter", "corpus"): "jupiter_corpus",
    ("earth", "grineer"): "earth_grineer",
    ("sedna", "grineer"): "sedna_grineer",
    ("uranus", "grineer"): "uranus_grineer",
    ("ceres", "grineer"): "ceres_grineer",
    ("europa", "corpus"): "europa_corpus",
    ("phobos", "corpus"): "phobos_corpus",
    ("mercury", "grineer"): "mercury_grineer",
    ("deimos", "infested"): "deimos_infested",
    ("kuva_fortress", "grineer"): "kuva_fortress_grineer",
    ("veil", "grineer"): "veil_grineer",
    ("deimos", "murmur"): "deimos_murmur",
    ("veil", "corpus"): "veil_corpus",
    ("zariman", "crossfire"): "zariman_crossfire",
    ("lua", "corpus"): "lua_corpus",
}

# 星球 -> 默认场景（该星球派系缺失时回退）
_PLANET_DEFAULT = {
    "venus": "venus_corpus", "mars": "mars_grineer", "void": "void_orokin",
    "eris": "eris_infested", "saturn": "saturn_grineer",
    "neptune": "neptune_corpus", "pluto": "pluto_corpus",
    "jupiter": "jupiter_corpus", "earth": "earth_grineer",
    "sedna": "sedna_grineer", "uranus": "uranus_grineer",
    "ceres": "ceres_grineer", "europa": "europa_corpus",
    "phobos": "phobos_corpus", "mercury": "mercury_grineer",
    "deimos": "deimos_infested", "kuva_fortress": "kuva_fortress_grineer",
    "veil": "veil_grineer", "zariman": "zariman_crossfire",
    "lua": "lua_corpus", "duviri": "zariman_crossfire",
}

# 派系 -> 默认场景（星球未知时回退）
_FACTION_DEFAULT = {
    "grineer": "mars_grineer", "corpus": "venus_corpus",
    "infested": "eris_infested", "orokin": "void_orokin",
    "sentient": "void_orokin", "murmur": "deimos_murmur",
    "crossfire": "zariman_crossfire", "anarch": "deimos_murmur",
    "tenno": "lua_corpus",
}

_FALLBACK = "void_orokin"
_SCENE_CACHE: Dict[str, object] = {}


def planet_of(node_name: str) -> str:
    """从节点显示名里提取星球 key，支持中英文括号。

    'Galatea (Neptune)' / 'Galatea（海王星）' -> 'neptune'
    """
    name = str(node_name or "")
    m = re.search(r"[\(（]([^\)）]+)[\)）]\s*$", name)
    if m:
        raw = m.group(1).strip().lower()
        return _PLANET_ALIAS.get(raw, "")
    # 直接就是星球名（如 'Veil'）或下划线 key
    return _PLANET_ALIAS.get(name.strip().lower().replace(" ", "_"), "")


def scene_key(node_name: str, faction: str = "") -> str:
    """按节点的星球+派系返回场景图 key。"""
    planet = planet_of(node_name)
    fac = _FACTION_ALIAS.get(str(faction or "").strip().lower(), "")
    if planet and fac:
        key = _PAIR_SCENE.get((planet, fac))
        if key:
            return key
    if planet:
        key = _PLANET_DEFAULT.get(planet)
        if key:
            return key
    if fac:
        return _FACTION_DEFAULT.get(fac, _FALLBACK)
    return _FALLBACK


def scene_image(key: str):
    """加载场景图（PIL.Image），带缓存；文件缺失返回 None。"""
    key = str(key or "")
    if key in _SCENE_CACHE:
        return _SCENE_CACHE[key]
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return None
    path = os.path.join(_ASSET_ROOT, "scene_" + key + ".jpg")
    if not os.path.isfile(path):
        path = os.path.join(_ASSET_ROOT, key + ".jpg")
    if not os.path.isfile(path):
        return None
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return None
    _SCENE_CACHE[key] = img
    return img


def all_keys():
    """返回所有可用场景 key。"""
    if not os.path.isdir(_ASSET_ROOT):
        return []
    out = []
    for name in os.listdir(_ASSET_ROOT):
        if name.startswith("scene_") and name.endswith(".jpg"):
            out.append(name[6:-4])
    return sorted(out)
