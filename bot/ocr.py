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
        # 歪掉的截图：抹掉图标 → 整图放大 4 倍 → 整张转正 → 反色二值化。
        # 顺序很重要：先转再放大会把插值出来的糊边一起放大，笔画糊成一团；
        # 先放大再转，重采样只作用一次。放大倍数按原图长边算，大图（手机
        # 拍的）自动降到 2 倍，免得一次识别就要十几秒。
        clean = _strip_icons(source).convert("L")
        longest = max(clean.size) or 1
        scale = max(2, min(4, int(round(2800.0 / longest))))
        big = _upscale(clean, scale)
        jobs: List[Tuple[str, object, str]] = []
        for angle in angles:
            rotated = big.rotate(angle, resample=Image.BICUBIC, expand=True,
                                 fillcolor=0)
            rotated = ImageOps.autocontrast(rotated).filter(
                ImageFilter.UnsharpMask(radius=2, percent=170, threshold=2))
            inverted = ImageOps.invert(rotated)
            jobs.append(("rot%+.1f_inv" % angle, inverted, "--psm 6"))
            for threshold in (140, 180):
                jobs.append(("rot%+.1f_inv_th%d" % (angle, threshold),
                             inverted.point(
                                 lambda value, cut=threshold: 255 if value > cut else 0),
                             "--psm 6"))
        return jobs

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


def _row_profile(image) -> List[float]:
    """把图缩成 1 像素宽：每个像素就是那一行的平均亮度。"""
    return list(image.resize((1, image.height), Image.BOX).getdata())


def _profile_spread(image) -> float:
    profile = _row_profile(image)
    if not profile:
        return 0.0
    mean = sum(profile) / float(len(profile))
    return sum((value - mean) ** 2 for value in profile) / float(len(profile))


def _skew_spread(image, angle: float, margin: float = 0.18) -> float:
    """转到 angle 度之后，每行亮度起伏有多大；转到文字水平时最大。

    转完要裁掉一圈：`rotate` 会在四角补黑，不裁的话越转黑边越多、
    起伏越大，结果永远顶在搜索区间的两端。
    """
    rotated = image.rotate(angle, resample=Image.BILINEAR, fillcolor=0)
    width, height = rotated.size
    rotated = rotated.crop((int(width * margin), int(height * margin),
                            int(width * (1 - margin)),
                            int(height * (1 - margin))))
    return _profile_spread(rotated)


def deskew_angle(image) -> float:
    """估计文字歪了多少度（逆时针为正），靠「转正后每行亮度起伏最大」。

    紫卡的文字区是十几行等距文字，转正时行与行的空隙最分明；歪一点点，
    每行就会糊在一起、起伏变小。先 2° 粗扫再 0.5° 细扫，纯 PIL 实现，
    不需要 numpy。只取下半部分计算：上半是原画，纹理噪声比文字大得多。
    """
    if Image is None:
        return 0.0
    gray = _prepare(image).convert("L")
    top = int(gray.height * 0.42)
    area = ImageOps.autocontrast(gray.crop((0, top, gray.width, gray.height)))
    area = ImageOps.invert(area)  # 浅色字 → 亮，行均值才有峰值
    longest = max(area.size)
    if longest > 420:
        ratio = 420.0 / longest
        area = area.resize((max(1, int(area.width * ratio)),
                            max(1, int(area.height * ratio))), Image.BILINEAR)

    best_angle, best_spread = 0.0, -1.0
    for step, span, centre in ((2.0, 20.0, 0.0), (0.5, 2.5, None)):
        if centre is None:
            centre = best_angle
        angle = centre - span
        while angle <= centre + span + 1e-9:
            spread = _skew_spread(area, angle)
            if spread > best_spread:
                best_angle, best_spread = angle, spread
            angle += step
    return best_angle


def retry_angles(image, span: float = 2.0) -> List[float]:
    """歪图兜底要试的角度：以检测到的倾斜角为主，再各偏一点防抖。"""
    hint = deskew_angle(image)
    if abs(hint) < 0.75:
        return [-5.0, 5.0]
    return [round(hint, 1), round(hint - span, 1), round(hint + span, 1)]


def load(png: bytes):
    """图片字节 → PIL 图；打不开时抛 OcrError。"""
    if Image is None:
        raise OcrError("缺少 Pillow，无法处理图片。")
    try:
        return Image.open(io.BytesIO(png)).convert("RGB")
    except Exception as exc:  # 空文件、截断、格式不支持等
        raise OcrError("图片打不开：%s" % exc)


def extract_variants(png: bytes, angles: Sequence[float] = ()) -> List[str]:
    """同一张图的多次识别结果；angles 用于歪图（转过再识别）。"""
    if not available():
        raise OcrError(
            "图片识别未启用：请安装 Tesseract OCR（含 chi_sim 中文包），"
            "Docker 镜像已内置；Windows 需单独安装。")
    image = load(png)
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
