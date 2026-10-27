"""修图用的抠图模型，全部在本机用 onnxruntime 跑（模型文件见 models.py，第一次用到时才下载）。

- SAM 2.1 tiny：对象选择。点（加 / 减）或框 → 选区。图像编码一次要零点几秒到一秒，之后每次点选只跑解码器
  （几十毫秒），所以编码结果按图缓存（同一张图连续点选不重复编码）。
- BiRefNet lite：选择主体 / 去除背景（CPU 上一张三到六秒）。
- MODNet：人像抠图，头发丝是半透明的。
- 天空分割（U²-Net）：天空替换里找天空。

都输入 8 位 RGB(A)，返回同尺寸的 float32 0–1 图。没有 Qt，可以单元测试。
"""
from __future__ import annotations

import threading

import numpy as np

from . import models

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
SAM_SIZE = 1024

_lock = threading.Lock()
_sessions: dict[str, object] = {}
_sam_cache: dict = {"key": None, "emb": None}


def ready(key: str) -> bool:
    return models.ready(key) and models.onnxruntime_ok()


def _session(name: str):
    with _lock:
        s = _sessions.get(name)
        if s is None:
            import onnxruntime as ort
            p = models.file_path(name)
            if p is None:
                raise FileNotFoundError(name)
            so = ort.SessionOptions()
            so.log_severity_level = 3                       # 不打印常量折叠之类的警告
            s = ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"])
            _sessions[name] = s
        return s


def unload() -> None:
    with _lock:
        _sessions.clear()
        _sam_cache.update(key=None, emb=None)


def _resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    import cv2
    shrink = w * h < img.shape[0] * img.shape[1]
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA if shrink else cv2.INTER_LINEAR)


def _tensor(rgb8: np.ndarray, w: int, h: int, mean=MEAN, std=STD) -> np.ndarray:
    x = _resize(np.ascontiguousarray(rgb8[..., :3]), w, h).astype(np.float32) / 255
    x = (x - mean) / std
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None])


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(x, -40, 40)))


def _back(prob: np.ndarray, w: int, h: int) -> np.ndarray:
    import cv2
    return np.clip(cv2.resize(prob.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR), 0, 1)


# ---------------------------------------------------------------------------
# SAM 2.1：点 / 框 → 选区
# ---------------------------------------------------------------------------

def sam_encode(rgb8: np.ndarray, key=None) -> list[np.ndarray]:
    """图像编码（SAM 把整张图直接缩放成 1024 × 1024，不保持比例）。key 相同就用缓存。"""
    if key is not None and _sam_cache["key"] == key:
        return _sam_cache["emb"]
    s = _session("sam21_tiny/vision_encoder_quantized.onnx")
    emb = s.run(None, {"pixel_values": _tensor(rgb8, SAM_SIZE, SAM_SIZE)})
    _sam_cache.update(key=key, emb=emb)
    return emb


def sam_predict(emb: list[np.ndarray], w: int, h: int, points=(), labels=(), box=None) -> np.ndarray:
    """points：[(x, y)…]（图像像素坐标），labels：1 = 要、0 = 不要；box：(x0, y0, x1, y1)。
    返回 h × w 的 0 / 1 图（float32）；模型认为那里没有物体时全 0。

    SAM 的输出是 256 × 256 的 logit，按 0 二值化才是它的本意——直接当透明度用会是半透明的一片；
    边缘要细，交给调用方按照片本身的边缘精修。单击时三个候选（局部 / 部件 / 整体）取模型自评最高的那个。"""
    s = _session("sam21_tiny/prompt_encoder_mask_decoder.onnx")
    sx, sy = SAM_SIZE / w, SAM_SIZE / h
    pts = np.array([[x * sx, y * sy] for x, y in points], np.float32).reshape(1, 1, -1, 2)
    lab = np.array(list(labels), np.int64).reshape(1, 1, -1)
    if box is not None:
        x0, y0, x1, y1 = box
        bx = np.array([[[min(x0, x1) * sx, min(y0, y1) * sy, max(x0, x1) * sx, max(y0, y1) * sy]]], np.float32)
    else:
        bx = np.zeros((1, 0, 4), np.float32)
    feeds = {"input_points": pts, "input_labels": lab, "input_boxes": bx,
             "image_embeddings.0": emb[0], "image_embeddings.1": emb[1], "image_embeddings.2": emb[2]}
    iou, masks, obj = s.run(None, feeds)
    if float(obj.reshape(-1)[0]) < 0:
        return np.zeros((h, w), np.float32)
    i = int(np.argmax(iou.reshape(-1)[:masks.shape[2]]))
    import cv2
    logit = cv2.resize(masks[0, 0, i].astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    return (logit > 0).astype(np.float32)


# ---------------------------------------------------------------------------
# BiRefNet lite：主体
# ---------------------------------------------------------------------------

def subject(rgb8: np.ndarray) -> np.ndarray:
    h, w = rgb8.shape[:2]
    s = _session("birefnet_lite_fp16.onnx")
    out = s.run(None, {"input_image": _tensor(rgb8, 1024, 1024)})[0][0, 0]
    return _back(_sigmoid(out), w, h)


# ---------------------------------------------------------------------------
# MODNet：人像
# ---------------------------------------------------------------------------

def portrait(rgb8: np.ndarray, ref: int = 512) -> np.ndarray:
    h, w = rgb8.shape[:2]
    if w >= h:                                          # 短边缩到 ref，两边都取 32 的倍数（MODNet 官方做法）
        rh, rw = ref, int(w / h * ref)
    else:
        rw, rh = ref, int(h / w * ref)
    rw, rh = max(32, rw - rw % 32), max(32, rh - rh % 32)
    s = _session("modnet.onnx")
    x = _tensor(rgb8, rw, rh, np.float32(0.5), np.float32(0.5))
    out = s.run(None, {"input": x})[0][0, 0]
    return _back(out, w, h)


# ---------------------------------------------------------------------------
# 天空
# ---------------------------------------------------------------------------

def sky(rgb8: np.ndarray) -> np.ndarray:
    h, w = rgb8.shape[:2]
    s = _session("skyseg.onnx")
    out = s.run(None, {s.get_inputs()[0].name: _tensor(rgb8, 320, 320)})[0][0, 0]
    return _back(out, w, h)
