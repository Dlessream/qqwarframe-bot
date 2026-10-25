"""短 TTL 缓存：给实时数据源做一层很短的缓存，减少重复请求。

只缓存“短时间内不会变”的结果：
  - 市场订单（默认 60 秒）
  - 市场行情统计（默认 15 分钟）
  - 物品详情（默认 1 小时）
  - 世界状态（默认 60 秒）
  - 维基词条（默认 10 分钟）
  - 紫卡拍卖（默认 5 分钟）
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

_MISS = object()


class TTLCache:
    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 enabled: Optional[bool] = None, prefix: str = ""):
        config = config or {}
        if enabled is None:
            # 不传配置时默认关闭（直接构造客户端的脚本/测试不受影响）
            enabled = bool(config.get("enabled", True)) if config else False
        self.enabled = bool(enabled)
        self.prefix = prefix
        self.default_ttl = max(0.001, float(config.get("default_seconds", 60)))
        self._data: Dict[Any, Tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _key(self, key: Any) -> Any:
        return (self.prefix, key) if self.prefix else key

    def get(self, key: Any, default: Any = None) -> Any:
        if not self.enabled:
            return default
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(self._key(key))
            if entry is None:
                self.misses += 1
                return default
            expires, value = entry
            if expires <= now:
                del self._data[self._key(key)]
                self.misses += 1
                return default
            self.hits += 1
            return value

    def set(self, key: Any, value: Any, ttl: Optional[int] = None) -> None:
        if not self.enabled:
            return
        seconds = self.default_ttl if ttl is None else float(ttl)
        if seconds <= 0:
            return
        with self._lock:
            self._data[self._key(key)] = (time.monotonic() + seconds, value)

    def get_or_set(self, key: Any, factory: Callable[[], Any],
                   ttl: Optional[int] = None) -> Any:
        if not self.enabled:
            return factory()
        value = self.get(key, _MISS)
        if value is not _MISS:
            return value
        value = factory()
        self.set(key, value, ttl)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
