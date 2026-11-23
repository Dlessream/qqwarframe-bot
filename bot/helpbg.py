"""帮助卡片动态背景。

从世界状态的 news 里取官方 key art（游戏启动界面轮播的那种宣传图），
下载、缩放、缓存到本地。新闻变化时首图 URL 变化，缓存 key 随之变化，
背景自动跟着游戏内更新而更换。

设计约束：
- 纯标准库 + Pillow；Pillow 缺失时返回 None，上层降级到纯色卡片。
- 只认官方域名，避免把任意 URL 当图片下载。
- 磁盘缓存 + 内存缓存双层：同一张图不重复下载；跨进程重启也不丢。
- 任何异常都吞掉并返回 None，绝不让「帮助」命令因为背景失败而报错。
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import datafiles

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - 环境缺 Pillow
    Image = None  # type: ignore
    PIL_AVAILABLE = False

# 官方图床：只接受这些域名的图片
_ALLOWED_HOSTS = (
    "www-static.warframe.com",
    "static.warframe.com",
    "cdn.warframestat.us",
    "content.warframe.com",
)
# 占位图 / 非横图比例的图片不适合做背景
_PLACEHOLDER_RE = re.compile(r"placeholder|default|fallback", re.IGNORECASE)

CACHE_DIR = os.path.join("data", "helpbg")
MAX_IMAGES = 8           # 磁盘最多保留几张，防止长期堆积
TARGET_W = 1000          # 背景缩放到这个宽度即可，卡片宽 860
TARGET_H = 1500          # 高度上限，避免超高图占内存
DISK_TTL = 7 * 24 * 3600  # 磁盘缓存 7 天，过期重下


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def official_image_urls(news: Any) -> List[str]:
    """从 news 列表里挑出可用于背景的官方图片 URL（按原始顺序）。

    过滤掉占位图、非官方域名、重复链接。保留顺序是为了让「当前首图」
    对应游戏里最新的一条新闻。
    """
    result: List[str] = []
    seen = set()
    for item in _as_list(news):
        if not isinstance(item, dict):
            continue
        url = str(item.get("imageLink") or item.get("image") or "").strip()
        if not url or url in seen:
            continue
        if _PLACEHOLDER_RE.search(url):
            continue
        if not _is_official(url):
            continue
        seen.add(url)
        result.append(url)
    return result


def _is_official(url: str) -> bool:
    """校验 URL 是否为允许的官方图床。"""
    lowered = url.lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    rest = lowered.split("://", 1)[1]
    host = rest.split("/", 1)[0].split(":", 1)[0]
    return any(host == h or host.endswith("." + h) for h in _ALLOWED_HOSTS)


def background_key(news: Any) -> Optional[str]:
    """根据当前 news 算出背景缓存 key。新闻没变则 key 稳定。"""
    urls = official_image_urls(news)
    if not urls:
        return None
    return hashlib.sha256(urls[0].encode("utf-8")).hexdigest()[:16]


class HelpBackground:
    """官方 key art 缓存与加载器。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 http: Any = None, logger: Any = None):
        cfg = config or {}
        self.cfg = cfg
        self.http = http
        self.log = logger
        self.enabled = bool(cfg.get("enabled", True))
        self.timeout = float(cfg.get("timeout_seconds", 12))
        self._cache: Dict[str, Any] = {}  # key -> PIL.Image（已缩放）
        self._current_key: Optional[str] = None
        self._cache_dir = os.path.join(
            datafiles.ROOT, CACHE_DIR) if getattr(datafiles, "ROOT", "") else CACHE_DIR

    # ------------------------------------------------------------------ 路径
    def _path_for(self, key: str) -> str:
        return os.path.join(self._cache_dir, "bg_%s.jpg" % key)

    def _ensure_dir(self) -> bool:
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------ 下载
    def _download(self, url: str) -> Optional[bytes]:
        """下载图片字节。优先复用传入的 HttpClient，否则用标准库直连。"""
        if self.http is not None:
            try:
                raw = self.http.request(url)
                if raw:
                    return raw
            except Exception as exc:
                self._warn("背景图下载失败（HttpClient）：%s", exc)
            return None
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return resp.read()
        except Exception as exc:
            self._warn("背景图下载失败：%s", exc)
            return None

    def _warn(self, fmt: str, *args: Any) -> None:
        if self.log and hasattr(self.log, "warning"):
            self.log.warning(fmt, *args)

    # ------------------------------------------------------------------ 处理
    @staticmethod
    def _prepare(raw: bytes) -> Optional[Any]:
        """字节 -> 缩放后的 RGB 图片。纯函数，便于测试。"""
        if not PIL_AVAILABLE or not raw:
            return None
        try:
            image = Image.open(io.BytesIO(raw))
            image = image.convert("RGB")
            w, h = image.size
            if w < 320 or h < 180:
                return None
            ratio = TARGET_W / float(w)
            new_w = TARGET_W
            new_h = max(1, int(h * ratio))
            if new_h > TARGET_H:
                new_w = max(1, int(w * TARGET_H / float(h)))
                new_h = TARGET_H
            return image.resize((new_w, new_h), Image.LANCZOS)
        except Exception:
            return None

    def _save(self, key: str, raw: bytes) -> None:
        if not self._ensure_dir():
            return
        path = self._path_for(key)
        try:
            tmp = path + ".tmp"
            with open(tmp, "wb") as handle:
                handle.write(raw)
            os.replace(tmp, path)
            self._prune()
        except OSError as exc:
            self._warn("背景图缓存写入失败：%s", exc)

    def _load_disk(self, key: str) -> Optional[bytes]:
        path = self._path_for(key)
        try:
            if not os.path.isfile(path):
                return None
            if time.time() - os.path.getmtime(path) > DISK_TTL:
                return None
            with open(path, "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def _prune(self) -> None:
        """磁盘只保留最近 MAX_IMAGES 张背景。"""
        try:
            files = [os.path.join(self._cache_dir, name)
                     for name in os.listdir(self._cache_dir)
                     if name.startswith("bg_") and name.endswith(".jpg")]
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for path in files[MAX_IMAGES:]:
                try:
                    os.remove(path)
                except OSError:
                    pass
        except OSError:
            pass

    # ------------------------------------------------------------------ 对外
    def get(self, news: Any) -> Optional[Any]:
        """返回当前新闻对应的背景 PIL.Image；拿不到时返回 None。"""
        if not self.enabled or not PIL_AVAILABLE:
            return None
        key = background_key(news)
        if not key:
            return None
        urls = official_image_urls(news)
        self._current_key = key

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        raw = self._load_disk(key)
        if raw is None:
            raw = self._download(urls[0])
            if raw:
                self._save(key, raw)
        if raw is None:
            return None

        image = self._prepare(raw)
        if image is not None:
            # 内存里最多留几张，避免长期运行累积
            if len(self._cache) >= MAX_IMAGES:
                oldest = next(iter(self._cache), None)
                if oldest is not None:
                    self._cache.pop(oldest, None)
            self._cache[key] = image
        return image

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "current_key": self._current_key or "",
            "memory_images": len(self._cache),
            "cache_dir": self._cache_dir,
            "pillow": PIL_AVAILABLE,
        }
