"""紫卡截图 OCR（可选）。

需要系统安装 Tesseract 与中文语言包：
  Debian/Ubuntu: tesseract-ocr tesseract-ocr-chi-sim
  Windows:       https://github.com/UB-Mannheim/tesseract/wiki
没安装时不影响文字版 #紫卡分析，只会在图片分析时提示。
"""

from __future__ import annotations

import io
import os
import re
import shutil
from typing import List, Tuple

try:  # 可选依赖
    import pytesseract
except Exception:  # pragma: no cover - 环境未安装
    pytesseract = None

try:
    from PIL import Image, ImageEnhance, ImageOps
except Exception:  # pragma: no cover - 环境缺 Pillow
    Image = None
    ImageOps = None
    ImageEnhance = None

from .rivencalc import ALIASES, canonical_name


class OcrError(Exception):
    pass


def _find_tesseract() -> str:
    override = os.environ.get("TESSERACT_CMD", "").strip()
    if override and os.path.isfile(override):
        return override
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


def available() -> bool:
    path = _find_tesseract()
    if pytesseract is None or Image is None or not path:
        return False
    pytesseract.pytesseract.tesseract_cmd = path
    return True


def image_to_text(png: bytes) -> str:
    if not available():
        raise OcrError(
            "图片识别未启用：请安装 Tesseract OCR（含 chi_sim 中文包），"
            "Docker 镜像已内置；Windows 需单独安装。")
    try:
        image = Image.open(io.BytesIO(png)).convert("RGB")
        # 正负词条颜色不同，红色负面在灰度图里容易消失；多跑一遍增强图再合并。
        gray = ImageOps.autocontrast(image.convert("L"))
        gray = gray.resize((max(1, gray.width * 2), max(1, gray.height * 2)))
        gray = ImageEnhance.Contrast(gray).enhance(1.8)
        texts = []
        for source in (image, gray):
            try:
                texts.append(pytesseract.image_to_string(
                    source, lang="chi_sim+eng", config="--psm 6"))
            except Exception:
                continue
        if not texts:
            raise OcrError("图片识别失败。")
        return "\n".join(texts)
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError("图片识别失败：%s" % exc)


def _normalize_signs(text: str) -> str:
    return (str(text or "")
            .replace("−", "-").replace("–", "-").replace("—", "-")
            .replace("－", "-").replace("﹣", "-")
            .replace("％", "%"))


def parse_rolls(text: str) -> List[Tuple[str, float]]:
    """从 OCR 文本中提取“+165.2% 伤害”这类词条。

    兼容红色负面词条常见的 OCR 问题：负号被识别成各种横线、
    负号单独一行、百分号漏识别。
    """
    found: List[Tuple[str, float]] = []
    index: dict = {}
    for line in _normalize_signs(text).splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.search(r"([+-]?)\s*(\d+(?:\.\d+)?)\s*%?", line)
        if not match:
            continue
        value = float(match.group(2))
        before = line[:match.start()]
        if match.group(1) == "-" or re.search(r"[-]", before):
            value = -value
        canonical = canonical_name(line)
        if canonical not in ALIASES:
            continue
        if canonical in index:
            position = index[canonical]
            if value < 0 < found[position][1]:
                found[position] = (canonical, value)
            continue
        index[canonical] = len(found)
        found.append((canonical, value))
    return found
