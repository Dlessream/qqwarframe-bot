"""紫卡截图 OCR。

一张图会跑过几套预处理再各自识别，结果交给 `rivenocr` 合并投票：

- 先把鲜艳像素（元素图标 🔥 之类）涂掉——图标正好卡在数值和词条中间，
  不涂掉的话「火焰伤害」会被读成 `& RIBAS` 这种乱码；
- 灰度 → 4/6 倍放大 → 自动对比度 → 锐化 → 反色（浅色字压在深色底上，
  反色后 Tesseract 明显更准）→ 多档二值化；
- 另外单独截出下半部分的词条区再跑一遍，小字更清楚。

需要系统安装 Tesseract 与中文语言包：
  Debian/Ubuntu: tesseract-ocr tesseract-ocr-chi-sim
  Windows:       https://github.com/UB-Mannheim/tesseract/wiki
没安装时不影响文字版 #紫卡分析，只会在图片分析时提示。
"""

from __future__ import annotations

import io
import os
import shutil
from typing import List, Optional, Sequence, Tuple

try:  # 可选依赖
    import pytesseract
except Exception:  # pragma: no cover - 环境未安装
    pytesseract = None

try:
    from PIL import Image, ImageChops, ImageFilter, ImageOps
except Exception:  # pragma: no cover - 环境缺 Pillow
    Image = None
    ImageChops = None
    ImageFilter = None
    ImageOps = None


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


# ---------------------------------------------------------------------- 预处理
def _strip_icons(image: "Image.Image", sat: int = 88, bright: int = 70):
    """把高饱和的彩色像素（元素图标）涂成底色，只留下浅色文字。"""
    rgb = image.convert("RGB")
    red, green, blue = rgb.split()
    high = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    low = ImageChops.darker(ImageChops.darker(red, green), blue)
    chroma = ImageChops.subtract(high, low)
    mask = chroma.point(lambda value: 255 if value > sat else 0)
    mask = ImageChops.multiply(mask,
                               high.point(lambda value: 255 if value > bright else 0))
    background = Image.new("RGB", rgb.size, (8, 8, 10))
    return Image.composite(background, rgb, mask)


def _upscale(gray, scale: int, sharpen: bool = True):
    # 放大后长边不超过 4800：再大只是徒增内存，Tesseract 也不会更准
    longest = max(gray.width, gray.height)
    if longest and longest * scale > 4800:
        scale = max(1, 4800 // longest)
    size = (max(1, gray.width * scale), max(1, gray.height * scale))
    big = gray.resize(size, Image.LANCZOS)
    big = ImageOps.autocontrast(big)
    if sharpen:
        big = big.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    return big


_MAX_SIDE = 1200


def _prepare(image, max_side: int = _MAX_SIDE):
    """超大截图先缩到长边 1400——紫卡文字本来就大，缩完照样清楚；
    不缩的话 4 倍放大能把 4K 截图吃成几亿像素，容器直接 OOM。"""
    longest = max(image.width, image.height)
    if longest <= max_side:
        return image
    ratio = float(max_side) / float(longest)
    size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    return image.resize(size, Image.LANCZOS)


def _variants(image, angles: Sequence[float] = ()) -> List[Tuple[str, object, str]]:
    """(变体名, 图片, tesseract 参数)。"""
    source = _prepare(image)
    if angles:
        # 角度歪的截图：按 OSD 之外的几个候选角各转一次，交给投票挑选
        return [("rot%+d_4x_th160" % angle,
                 ImageOps.invert(_upscale(
                     _strip_icons(source.rotate(angle, expand=True,
                                                resample=Image.BICUBIC)
                                  ).convert("L"), 4)).point(
                     lambda value: 255 if value > 160 else 0),
                 "--psm 6")
                for angle in angles]

    width, height = source.size
    clean = _strip_icons(source)
    gray = clean.convert("L")
    jobs: List[Tuple[str, object, str]] = [("raw_psm6", source, "--psm 6")]

    plain4 = _upscale(gray, 4)
    jobs.append(("inv4x", ImageOps.invert(plain4), "--psm 6"))
    for threshold in (150, 180):
        jobs.append(("inv4x_th%d" % threshold,
                     ImageOps.invert(plain4).point(
                         lambda value, cut=threshold: 255 if value > cut else 0),
                     "--psm 6"))

    area = clean.crop((0, int(height * 0.36), width, height)).convert("L")
    jobs.append(("area_inv5x", ImageOps.invert(_upscale(area, 5)), "--psm 6"))

    jobs.append(("inv6x_th150",
                 ImageOps.invert(_upscale(gray, 6)).point(
                     lambda value: 255 if value > 150 else 0),
                 "--psm 6"))
    return jobs


def _run(image, config: str) -> str:
    try:
        return pytesseract.image_to_string(image, lang="chi_sim+eng", config=config)
    except Exception:  # pragma: no cover - 单次失败不影响其它变体
        return ""


def _rotate_hint(png: bytes) -> float:
    """用 Tesseract 的 OSD 猜一下整张图的旋转角，失败就当 0 度。"""
    if pytesseract is None:
        return 0.0
    try:
        image = Image.open(io.BytesIO(png)).convert("RGB")
        osd = pytesseract.image_to_osd(image)
    except Exception:
        return 0.0
    for line in str(osd).splitlines():
        if line.lower().startswith("rotate:"):
            try:
                angle = float(line.split(":")[1])
            except ValueError:
                return 0.0
            return angle - 360.0 if angle > 180 else angle
    return 0.0


def extract_variants(png: bytes, angles: Sequence[float] = ()) -> List[str]:
    """同一张图的多次识别结果；角度变体用于歪图兜底。"""
    if not available():
        raise OcrError(
            "图片识别未启用：请安装 Tesseract OCR（含 chi_sim 中文包），"
            "Docker 镜像已内置；Windows 需单独安装。")
    try:
        image = Image.open(io.BytesIO(png)).convert("RGB")
    except Exception as exc:
        raise OcrError("图片打不开：%s" % exc)
    texts: List[str] = []
    for _tag, prepared, config in _variants(image, angles):
        text = _run(prepared, config)
        if text.strip():
            texts.append(text)
    if not texts:
        raise OcrError("图片识别失败。")
    return texts


def image_to_text(png: bytes) -> str:
    """兼容旧接口：把多套识别结果拼起来。"""
    return "\n".join(extract_variants(png))


# ---------------------------------------------------------------------- 兼容接口
def parse_rolls(text: str) -> List[Tuple[str, float]]:
    """单份文本 → [(词条, 数值)]；保留给旧调用方与测试。"""
    from .rivenocr import parse_text

    return parse_text(text)
