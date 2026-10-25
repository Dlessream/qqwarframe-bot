"""紫卡截图 OCR（可选）。

需要系统安装 Tesseract 与中文语言包：
  Debian/Ubuntu: tesseract-ocr tesseract-ocr-chi-sim
  Windows:       https://github.com/UB-Mannheim/tesseract/wiki
没安装时不影响文字版 #紫卡分析，只会在图片分析时提示。
"""

from __future__ import annotations

import io
import re
import shutil
from typing import List, Tuple

try:  # 可选依赖
    import pytesseract
except Exception:  # pragma: no cover - 环境未安装
    pytesseract = None

try:
    from PIL import Image
except Exception:  # pragma: no cover - 环境缺 Pillow
    Image = None

from .rivencalc import ALIASES, canonical_name


class OcrError(Exception):
    pass


def available() -> bool:
    return bool(pytesseract is not None and Image is not None
                and shutil.which("tesseract"))


def image_to_text(png: bytes) -> str:
    if not available():
        raise OcrError(
            "图片识别未启用：请安装 Tesseract OCR（含 chi_sim 中文包），"
            "Docker 镜像已内置；Windows 需单独安装。")
    try:
        image = Image.open(io.BytesIO(png))
        return pytesseract.image_to_string(
            image, lang="chi_sim+eng", config="--psm 6")
    except Exception as exc:
        raise OcrError("图片识别失败：%s" % exc)


def parse_rolls(text: str) -> List[Tuple[str, float]]:
    """从 OCR 文本中提取“+165.2% 伤害”这类词条。"""
    found: List[Tuple[str, float]] = []
    seen = set()
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.search(r"([+-]?)\s*(\d+(?:\.\d+)?)\s*[%％]", line)
        if not match:
            continue
        value = float(match.group(2))
        if match.group(1) == "-":
            value = -value
        canonical = canonical_name(line)
        if canonical not in ALIASES or canonical in seen:
            continue
        found.append((canonical, value))
        seen.add(canonical)
    return found
