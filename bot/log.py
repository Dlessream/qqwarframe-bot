"""日志初始化。"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_FMT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _force_utf8_stdio() -> None:
    """Windows 控制台默认是 GBK，中文日志会乱码甚至抛异常。"""
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:  # pragma: no cover - 无控制台时忽略
            pass

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # pragma: no cover - 仅防御
                pass


def setup_logging(level: str = "INFO", file_path: str = "") -> logging.Logger:
    _force_utf8_stdio()
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover
            pass

    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if file_path:
        directory = os.path.dirname(os.path.abspath(file_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        file_handler = RotatingFileHandler(
            file_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # 第三方库的噪音降低
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logging.getLogger("qqwarframe")
