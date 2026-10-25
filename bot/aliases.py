"""别名表：内置常用部件别名 + 可编辑的 data/aliases.json。"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
from typing import Dict, List, Optional

# 这些别名即使运行中的 data/aliases.json 没有，也会生效。
DEFAULT_ALIASES = {
    "电男": "volt_prime_set",
    "电男蓝图": "volt_prime_blueprint",
    "电男头": "volt_prime_neuroptics_blueprint",
    "电男机体": "volt_prime_chassis_blueprint",
    "电男系统": "volt_prime_systems_blueprint",
}

_ALIAS_RE = re.compile(r"^[^\s_].{0,39}$")


class AliasStore:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self._lock = threading.Lock()

    def _read_raw(self) -> Dict[str, str]:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(key): str(value) for key, value in raw.items()}

    def load(self) -> Dict[str, str]:
        result = dict(DEFAULT_ALIASES)
        for key, value in self._read_raw().items():
            if not key.startswith("_") and value:
                result[key] = value
        return result

    def entries(self) -> List[Dict[str, str]]:
        result = []
        for key, value in self.load().items():
            result.append({
                "alias": key,
                "slug": value,
                "builtin": key in DEFAULT_ALIASES,
            })
        result.sort(key=lambda item: item["alias"])
        return result

    def ensure_frame_defaults(self) -> int:
        """把随包附带的战甲别名补进现有 data/aliases.json（只补一次）。"""
        defaults_path = os.path.join(os.path.dirname(self.path), "frame_aliases.json")
        if not os.path.isfile(defaults_path):
            return 0
        try:
            with open(defaults_path, encoding="utf-8") as handle:
                defaults = json.load(handle)
        except (OSError, ValueError):
            return 0
        if not isinstance(defaults, dict):
            return 0
        with self._lock:
            raw = self._read_raw()
            added = 0
            changed = False
            if str(raw.get("_frame_aliases_v1") or "") != "1":
                for key, value in defaults.items():
                    if str(key).startswith("_") or not value:
                        continue
                    if str(key) not in raw:
                        raw[str(key)] = str(value)
                        added += 1
                raw["_frame_aliases_v1"] = "1"
                changed = True
            if str(raw.get("_frame_aliases_v2") or "") != "1":
                for key, value in defaults.items():
                    if str(key).startswith("_") or not value:
                        continue
                    if not str(value).endswith("_prime_set"):
                        continue
                    blueprint = str(value)[:-len("_set")] + "_blueprint"
                    if raw.get(str(key)) == blueprint:
                        raw[str(key)] = str(value)
                        changed = True
                    blueprint_alias = str(key) + "蓝图"
                    if blueprint_alias not in raw:
                        raw[blueprint_alias] = blueprint
                        added += 1
                        changed = True
                raw["_frame_aliases_v2"] = "1"
                changed = True
            if not changed:
                return 0
            try:
                self._write(raw)
            except OSError:
                return 0
            return added

    def replace_for_slug(self, slug: str, aliases: List[str]) -> None:
        """替换某个目标下的可编辑别名；内置别名保留。"""
        slug = str(slug or "").strip()
        if not re.fullmatch(r"[a-z0-9_]{2,120}", slug):
            raise ValueError("目标 slug 格式不正确。")
        names = []
        for alias in aliases:
            alias = str(alias or "").strip()
            if not alias:
                continue
            if not _ALIAS_RE.match(alias):
                raise ValueError("别名不能为空、不能以下划线开头，最长 40 个字符。")
            if alias not in names:
                names.append(alias)
        with self._lock:
            raw = self._read_raw()
            for key in list(raw):
                if key.startswith("_"):
                    continue
                if raw[key] == slug and key not in DEFAULT_ALIASES:
                    del raw[key]
            for alias in names:
                raw[alias] = slug
            self._write(raw)

    def ensure_component_defaults(self) -> int:
        """把随包附带的全部物品部件别名补进现有 alias 文件（只补一次）。"""
        defaults_path = os.path.join(os.path.dirname(self.path),
                                     "component_aliases.json")
        if not os.path.isfile(defaults_path):
            return 0
        try:
            with open(defaults_path, encoding="utf-8") as handle:
                defaults = json.load(handle)
        except (OSError, ValueError):
            return 0
        if not isinstance(defaults, dict):
            return 0
        with self._lock:
            raw = self._read_raw()
            if str(raw.get("_component_aliases_v1") or "") == "1":
                return 0
            added = 0
            for key, value in defaults.items():
                if str(key).startswith("_") or not value:
                    continue
                if str(key) not in raw:
                    raw[str(key)] = str(value)
                    added += 1
            raw["_component_aliases_v1"] = "1"
            try:
                self._write(raw)
            except OSError:
                return 0
            return added

    def save(self, alias: str, slug: str) -> None:
        alias = str(alias or "").strip()
        slug = str(slug or "").strip()
        if not _ALIAS_RE.match(alias):
            raise ValueError("别名不能为空、不能以下划线开头，最长 40 个字符。")
        if not re.fullmatch(r"[a-z0-9_]{2,120}", slug):
            raise ValueError("目标 slug 格式不正确。")
        with self._lock:
            raw = self._read_raw()
            raw[alias] = slug
            self._write(raw)

    def delete(self, alias: str) -> bool:
        alias = str(alias or "").strip()
        if alias in DEFAULT_ALIASES:
            raise ValueError("这是内置别名，不能删除；可以改成别的目标覆盖它。")
        with self._lock:
            raw = self._read_raw()
            if alias not in raw:
                return False
            raw.pop(alias)
            self._write(raw)
            return True

    def _write(self, raw: Dict[str, str]) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {}
        if "_说明" not in raw:
            payload["_说明"] = ("键=玩家常说的叫法，值=warframe market 物品 slug。"
                               "可自行增删；匹配时忽略大小写和空格。")
        payload.update(raw)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        try:
            if os.path.isfile(self.path):
                shutil.copymode(self.path, tmp)
        except OSError:
            pass
        os.replace(tmp, self.path)
