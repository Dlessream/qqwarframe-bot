"""运行时可编辑的命令菜单。

只保存「禁用哪些命令」和「额外别名」，不改源码；配置落盘到
`data/commands.json`，Web 控制台可以随时添加/移除。
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence


class CommandStore:
    def __init__(self, path: str, valid_commands: Sequence[str],
                 logger=None):
        self.path = str(path or "data/commands.json")
        self.valid_commands = set(valid_commands)
        self.log = logger
        self._lock = threading.RLock()
        self._disabled: set = set()
        self._aliases: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        disabled = raw.get("disabled") or []
        if isinstance(disabled, list):
            self._disabled = {str(item).strip() for item in disabled
                              if str(item).strip() in self.valid_commands}
        aliases = raw.get("aliases") or {}
        if isinstance(aliases, dict):
            self._aliases = {
                str(alias).strip().lower(): str(command).strip()
                for alias, command in aliases.items()
                if str(alias).strip() and str(command).strip() in self.valid_commands
            }

    def _save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload = {"disabled": sorted(self._disabled),
                   "aliases": dict(sorted(self._aliases.items()))}
        temp = self.path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp, self.path)

    def disabled(self) -> set:
        with self._lock:
            return set(self._disabled)

    def aliases(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._aliases)

    def set_enabled(self, command: str, enabled: bool) -> Dict[str, Any]:
        command = str(command or "").strip()
        if command not in self.valid_commands:
            raise ValueError("未知命令：%s" % command)
        with self._lock:
            if enabled:
                self._disabled.discard(command)
            else:
                self._disabled.add(command)
            self._save()
            return self.snapshot()

    def add_alias(self, alias: str, command: str) -> Dict[str, Any]:
        alias = str(alias or "").strip()
        command = str(command or "").strip()
        key = alias.lower()
        if not alias or len(alias) > 40:
            raise ValueError("别名长度需在 1~40 个字符之间")
        if any(ch.isspace() for ch in alias):
            raise ValueError("别名不能包含空格")
        if command not in self.valid_commands:
            raise ValueError("未知命令：%s" % command)
        with self._lock:
            if self._builtin_exists(key) or key in self._aliases:
                raise ValueError("别名「%s」已存在" % alias)
            self._aliases[key] = command
            self._save()
            return self.snapshot()

    def remove_alias(self, alias: str) -> Dict[str, Any]:
        key = str(alias or "").strip().lower()
        with self._lock:
            if key not in self._aliases:
                raise ValueError("没有这个自定义别名：%s" % alias)
            self._aliases.pop(key, None)
            self._save()
            return self.snapshot()

    def set_builtin_lookup(self, lookup) -> None:
        """由 CommandRouter 注入内置别名查询，避免循环 import。"""
        self._builtin_lookup = lookup

    def _builtin_exists(self, key: str) -> bool:
        lookup = getattr(self, "_builtin_lookup", None)
        return bool(lookup(key)) if lookup else False

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            aliases = dict(self._aliases)
            disabled = set(self._disabled)
        by_command: Dict[str, List[str]] = {}
        for alias, command in aliases.items():
            by_command.setdefault(command, []).append(alias)
        commands = []
        for command in sorted(self.valid_commands):
            commands.append({
                "command": command,
                "enabled": command not in disabled,
                "custom_aliases": sorted(by_command.get(command, [])),
            })
        return {
            "path": self.path,
            "disabled": sorted(disabled),
            "aliases": aliases,
            "commands": commands,
        }
