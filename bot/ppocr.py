"""PP-OCRv5 文字识别（与 riven.mashiro.games 紫卡识图助手同款模型与词典）。

模型就是那个扩展包里随包分发的两个 ONNX（`ch_PP-OCRv5_det_mobile` /
`ch_PP-OCRv5_rec_mobile`）与 `ppocrv5_dict.txt`，预处理 / 阈值 / CTC 解码
参数也与它的 `paddle-ocr.js` 一致，所以识别结果和用那个扩展本机识别同源。

设计要点：

* **可选依赖**：numpy + onnxruntime 任一缺失时 `available()` 返回 False，
  调用方降级成「手输词条」路径，不影响其它命令。
* **单例会话**：模型加载 ~1.5s、常驻内存 ~80MB，进程内只加载一次。
* **纯 CPU**：det 960 长边 + rec 48 高，单张紫卡截图 ~0.6~1.5s。
"""

from __future__ import annotations

import io
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

LOGGER = logging.getLogger("qqwarframe.ppocr")

DET_NAME = "ch_PP-OCRv5_det_mobile.onnx"
REC_NAME = "ch_PP-OCRv5_rec_mobile.onnx"
DICT_NAME = "ppocrv5_dict.txt"

#: 模型目录候选（data/ 优先，其次镜像内置副本）
DIR_CANDIDATES = ("data/ppocr", "builtin-data/ppocr", "bot/assets/ppocr")

#: det 后处理参数，与 paddle-ocr.js 完全一致
DET_MAX_SIDE = 960
DET_THRESH = 0.3
DET_BOX_THRESH = 0.5
DET_UNCLIP = 1.65
#: rec 输入高度与最大宽度
REC_HEIGHT = 48
REC_MAX_WIDTH = 2048
#: CTC 单字符置信度下限（低于这个值当噪声丢掉）
REC_MIN_SCORE = 0.45

_lock = threading.Lock()
_state = {"loaded": False, "error": "", "det": None, "rec": None, "dict": []}


@dataclass
class TextLine:
    """一行识别结果。`box` 是原图坐标 (x0, y0, x1, y1)。"""

    text: str
    confidence: float = 0.0
    box: Tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def center_y(self) -> float:
        return (self.box[1] + self.box[3]) / 2.0

    @property
    def height(self) -> int:
        return max(1, self.box[3] - self.box[1])


def _numpy():
    try:
        import numpy as np  # noqa: F401
        return np
    except Exception:  # noqa: BLE001 - 没装 numpy 就走降级路径
        return None


def _onnxruntime():
    try:
        import onnxruntime as ort  # noqa: F401
        return ort
    except Exception:  # noqa: BLE001
        return None


def model_dir() -> Optional[str]:
    """找到同时含有三个模型文件的目录。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seen = set()
    for rel in DIR_CANDIDATES:
        path = rel if os.path.isabs(rel) else os.path.join(root, rel)
        path = os.path.abspath(path)
        if path in seen:
            continue
        seen.add(path)
        if all(os.path.isfile(os.path.join(path, name))
               for name in (DET_NAME, REC_NAME, DICT_NAME)):
            return path
    return None


def available() -> bool:
    """依赖与模型都在才算可用（不触发加载）。"""
    if _state["loaded"]:
        return True
    if _numpy() is None or _onnxruntime() is None:
        return False
    return model_dir() is not None


def status() -> dict:
    """控制台展示用：是否可用 / 模型目录 / 失败原因。"""
    directory = model_dir()
    return {
        "available": available(),
        "models": directory or "",
        "numpy": _numpy() is not None,
        "onnxruntime": _onnxruntime() is not None,
        "error": _state.get("error", ""),
    }


def _ensure_loaded() -> bool:
    if _state["loaded"]:
        return True
    if _state.get("error"):
        return False
    with _lock:
        if _state["loaded"]:
            return True
        if _state.get("error"):
            return False
        np = _numpy()
        ort = _onnxruntime()
        directory = model_dir()
        if np is None or ort is None or not directory:
            _state["error"] = "缺少 numpy / onnxruntime 或模型文件"
            return False
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.graph_optimization_level = (
                ort.GraphOptimizationLevel.ORT_ENABLE_ALL)
            det = ort.InferenceSession(os.path.join(directory, DET_NAME),
                                       options, providers=["CPUExecutionProvider"])
            rec = ort.InferenceSession(os.path.join(directory, REC_NAME),
                                       options, providers=["CPUExecutionProvider"])
            with open(os.path.join(directory, DICT_NAME), "r",
                      encoding="utf-8") as handle:
                chars = handle.read().splitlines()
            # 字典最后可能有一行空行（文件以换行结尾）
            if chars and chars[-1] == "":
                chars.pop()
            _state.update({"det": det, "rec": rec, "dict": chars,
                           "loaded": True, "error": ""})
            LOGGER.info("PP-OCRv5 已加载：%s（字典 %d 字）", directory, len(chars))
        except Exception as exc:  # noqa: BLE001 - 加载失败就永久降级
            _state["error"] = "模型加载失败：%s" % exc
            LOGGER.warning("PP-OCRv5 加载失败：%s", exc)
            return False
    return True


# ---------------------------------------------------------------- 图像基础操作
def _to_array(png: bytes):
    """PNG/JPEG → RGB numpy 数组。识别只需要 RGB，不保留 alpha。"""
    from PIL import Image

    image = Image.open(io.BytesIO(png))
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image


def _resize(image, width: int, height: int):
    from PIL import Image

    if image.size == (width, height):
        return image
    return image.resize((width, height), Image.BILINEAR)


def _det_tensor(np, image, max_side: int = DET_MAX_SIDE):
    width, height = image.size
    ratio = min(1.0, max_side / max(width, height)) if max(width, height) else 1.0
    dst_w = max(32, int(round(width * ratio / 32)) * 32)
    dst_h = max(32, int(round(height * ratio / 32)) * 32)
    resized = _resize(image, dst_w, dst_h)
    arr = np.asarray(resized, dtype=np.float32)
    # paddle-ocr.js 里是 BGR 顺序（tensor[0] 取蓝色分量）
    bgr = arr[:, :, ::-1]
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    tensor = (bgr / 255.0 - mean) / std
    tensor = np.transpose(tensor, (2, 0, 1))[None, ...]
    return np.ascontiguousarray(tensor, dtype=np.float32), dst_w, dst_h


def _det_boxes(np, prob, dst_w: int, dst_h: int,
               orig_w: int, orig_h: int) -> List[Tuple[float, ...]]:
    """连通域 + 外扩，参数照 paddle-ocr.js 的 detPostprocess。"""
    mask = prob > DET_THRESH
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return []
    pending = set(zip(ys.tolist(), xs.tolist()))
    boxes = []
    sx = orig_w / float(dst_w)
    sy = orig_h / float(dst_h)
    while pending:
        start = pending.pop()
        queue = deque([start])
        min_x = max_x = start[1]
        min_y = max_y = start[0]
        area = 0
        while queue:
            y, x = queue.popleft()
            area += 1
            if x < min_x:
                min_x = x
            elif x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            elif y > max_y:
                max_y = y
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if (ny, nx) in pending:
                    pending.discard((ny, nx))
                    queue.append((ny, nx))
        box_w = max_x - min_x + 1
        box_h = max_y - min_y + 1
        if box_w < 3 or box_h < 3 or area < 9:
            continue
        mean_score = float(prob[min_y:max_y + 1, min_x:max_x + 1].mean())
        if mean_score < DET_BOX_THRESH:
            continue
        dist = (box_w * box_h * DET_UNCLIP) / (2.0 * (box_w + box_h))
        x0 = max(0.0, (min_x - dist * 1.05) * sx)
        y0 = max(0.0, (min_y - dist * 0.70) * sy)
        x1 = min(float(orig_w), (max_x + 1 + dist * 1.05) * sx)
        y1 = min(float(orig_h), (max_y + 1 + dist * 0.70) * sy)
        # rec 用更宽松的裁剪，保住上伸/下伸部分
        cx0 = max(0.0, (min_x - dist * 1.1) * sx)
        cy0 = max(0.0, (min_y - dist * 0.95) * sy)
        cx1 = min(float(orig_w), (max_x + 1 + dist * 1.1) * sx)
        cy1 = min(float(orig_h), (max_y + 1 + dist * 0.95) * sy)
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        boxes.append((x0, y0, x1, y1, cx0, cy0, cx1, cy1, mean_score))
    return boxes


def _rec(np, image, box: Sequence[float]) -> Tuple[str, float]:
    """单行识别：裁剪 → 48 高 → 归一化 → CTC 解码。"""
    cx0, cy0, cx1, cy1 = (int(round(box[4])), int(round(box[5])),
                          int(round(box[6])), int(round(box[7])))
    cx0 = max(0, cx0)
    cy0 = max(0, cy0)
    cx1 = max(cx0 + 1, cx1)
    cy1 = max(cy0 + 1, cy1)
    crop = image.crop((cx0, cy0, cx1, cy1))
    width, height = crop.size
    dst_w = max(REC_HEIGHT, min(REC_MAX_WIDTH,
                                int((width * REC_HEIGHT) / max(1, height) + 0.5)))
    resized = _resize(crop, dst_w, REC_HEIGHT)
    arr = np.asarray(resized, dtype=np.float32)[:, :, ::-1]
    tensor = (arr / 255.0 - 0.5) / 0.5
    tensor = np.transpose(tensor, (2, 0, 1))[None, ...]
    tensor = np.ascontiguousarray(tensor, dtype=np.float32)

    session = _state["rec"]
    outputs = session.run(None, {session.get_inputs()[0].name: tensor})
    logits = np.asarray(outputs[0])
    if logits.ndim == 4:  # 少数版本输出 [1, 1, T, C]
        logits = logits.reshape(logits.shape[0], -1, logits.shape[-1])
    logits = logits[0]
    # 输出是 [T, C]（C = 字典数 + blank + 空格）；个别导出版本是 [C, T]，
    # 用最后一维是否等于字符表大小来判断，别按「谁大谁小」猜——T 常比 C 小得多。
    expected = len(_state["dict"]) + 2
    if logits.shape[-1] != expected and logits.shape[0] == expected:
        logits = logits.T

    chars = _state["dict"]
    text_parts: List[str] = []
    scores: List[float] = []
    last = 0
    for step in range(logits.shape[0]):
        row = logits[step]
        best = int(row.argmax())
        best_value = float(row[best])
        if best != 0 and best != last and best_value >= REC_MIN_SCORE:
            if best <= len(chars):
                text_parts.append(chars[best - 1])
                scores.append(best_value)
            elif best == len(chars) + 1:
                text_parts.append(" ")
                scores.append(best_value)
        last = best
    text = "".join(text_parts).strip()
    confidence = (sum(scores) / len(scores)) if scores else 0.0
    return text, confidence


def recognize(png: bytes, max_side: int = DET_MAX_SIDE) -> List[TextLine]:
    """整图识别，返回按行聚好的结果（空列表表示没识别到文字）。"""
    if not _ensure_loaded():
        return []
    np = _numpy()
    try:
        image = _to_array(png)
    except Exception as exc:  # noqa: BLE001 - 坏图不该炸掉命令
        LOGGER.info("紫卡截图解码失败：%s", exc)
        return []
    orig_w, orig_h = image.size
    if orig_w < 32 or orig_h < 32:
        return []
    tensor, dst_w, dst_h = _det_tensor(np, image, max_side)
    session = _state["det"]
    try:
        outputs = session.run(None, {session.get_inputs()[0].name: tensor})
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("PP-OCRv5 det 推理失败：%s", exc)
        return []
    prob = np.asarray(outputs[0]).squeeze()
    if prob.ndim != 2:
        prob = prob.reshape(dst_h, dst_w)
    boxes = _det_boxes(np, prob, dst_w, dst_h, orig_w, orig_h)
    if not boxes:
        return []

    # 同一行先按 y 聚类，再按 x 拼接：det 会把「+184.7%」和「暴击几率」拆成两段
    boxes.sort(key=lambda item: ((item[1] + item[3]) / 2.0, item[0]))
    rows: List[List[Tuple[float, ...]]] = []
    for box in boxes:
        center = (box[1] + box[3]) / 2.0
        height = max(1.0, box[3] - box[1])
        for row in rows:
            row_center = sum((b[1] + b[3]) / 2.0 for b in row) / len(row)
            row_height = max(max(1.0, b[3] - b[1]) for b in row)
            if abs(row_center - center) < min(row_height, height) * 0.55:
                row.append(box)
                break
        else:
            rows.append([box])

    lines: List[TextLine] = []
    for row in rows:
        row.sort(key=lambda item: item[0])
        parts: List[str] = []
        for box in row:
            text, _ = _rec(np, image, box)
            if text:
                parts.append(text)
        if not parts:
            continue
        x0 = int(min(b[0] for b in row))
        y0 = int(min(b[1] for b in row))
        x1 = int(max(b[2] for b in row))
        y1 = int(max(b[3] for b in row))
        lines.append(TextLine(" ".join(parts), 0.0, (x0, y0, x1, y1)))
    return lines


def warm() -> bool:
    """启动时预热（模型加载 ~1.5s），免得第一张截图多等这么久。"""
    return _ensure_loaded()
