"""照片里的文字识别（OCR）：PaddleOCR PP-OCRv4 的检测 + 识别两个 ONNX 模型（RapidOCR 转换），onnxruntime 本机运行。

流程与 PaddleOCR 一致，只是不依赖 paddle / pyclipper：
1. 检测（DBNet）：图片长边缩到 960（32 的整数倍）→ 每个像素“是文字”的概率 → 二值化 → 轮廓 → 最小外接矩形，
   按“面积 × 1.6 / 周长”向外扩（矩形的 unclip 正好是各边外推这么多）→ 换算回原图坐标；
2. 识别（SVTR-CTC）：按四个角点把每行文字透视拉正（竖排的转 90°），高缩到 48，按宽高比分批送进模型，
   CTC 贪心解码（去掉重复与空白），字表在模型的元数据里（6623 个字 + 空格）。
没有方向分类器：照片里的文字绝大多数是正的，倒着的字会识别成低分结果被丢掉。

输入一律是 BGR uint8（cv2 的习惯，和 PaddleOCR 训练时一致）。
"""
from __future__ import annotations

import math
import threading
from pathlib import Path

import numpy as np

DET_LIMIT = 960
DET_THRESH = 0.3          # 概率图二值化阈值
BOX_THRESH = 0.5          # 框内平均概率低于这个不要
UNCLIP_RATIO = 1.6
MIN_SIZE = 3
REC_H = 48
REC_BATCH = 6
DROP_SCORE = 0.5          # 识别分数低于这个的行不要
MAX_BOXES = 300


def _session(path: Path):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])


class OcrEngine:
    def __init__(self, det_path: Path, rec_path: Path):
        self.det = _session(det_path)
        self.rec = _session(rec_path)
        meta = self.rec.get_modelmeta().custom_metadata_map
        chars = (meta.get("character") or "").splitlines()
        if not chars:
            raise RuntimeError("OCR 识别模型里没有字表")
        n_cls = self.rec.get_outputs()[0].shape[-1]
        self.chars = ["\0"] + chars + [" "]               # 0 = CTC 空白
        if isinstance(n_cls, int) and n_cls != len(self.chars):
            self.chars = (self.chars + [""] * n_cls)[:n_cls]
        self._lock = threading.Lock()                     # 同一个会话串行用（onnxruntime 内部已经多线程）

    # ---- 检测 ------------------------------------------------------------
    def _det_input(self, bgr: np.ndarray) -> tuple[np.ndarray, float, float]:
        import cv2
        h, w = bgr.shape[:2]
        r = min(1.0, DET_LIMIT / max(h, w))
        nh, nw = max(32, int(round(h * r / 32)) * 32), max(32, int(round(w * r / 32)) * 32)
        im = cv2.resize(bgr, (nw, nh)).astype(np.float32) / 255.0
        im = (im - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
        return im.transpose(2, 0, 1)[None], w / nw, h / nh

    def detect(self, bgr: np.ndarray) -> list[np.ndarray]:
        """返回每行文字的四个角点（原图坐标，float32 (4, 2)，左上 → 右上 → 右下 → 左下），从上到下、从左到右排好。"""
        import cv2
        x, sx, sy = self._det_input(bgr)
        with self._lock:
            prob = self.det.run(None, {self.det.get_inputs()[0].name: x})[0][0, 0]
        bitmap = (prob > DET_THRESH).astype(np.uint8)
        contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        H, W = bgr.shape[:2]
        boxes = []
        for c in contours[:1000]:
            rect = cv2.minAreaRect(c)
            (cx, cy), (rw, rh), ang = rect
            if min(rw, rh) < MIN_SIZE:
                continue
            if _box_score(prob, cv2.boxPoints(rect)) < BOX_THRESH:
                continue
            d = rw * rh * UNCLIP_RATIO / max(1e-6, 2 * (rw + rh))
            rw2, rh2 = rw + 2 * d, rh + 2 * d
            if min(rw2, rh2) < MIN_SIZE + 2:
                continue
            pts = cv2.boxPoints(((cx, cy), (rw2, rh2), ang))
            pts[:, 0] = np.clip(pts[:, 0] * sx, 0, W - 1)
            pts[:, 1] = np.clip(pts[:, 1] * sy, 0, H - 1)
            pts = _order(pts)
            if np.linalg.norm(pts[0] - pts[1]) < 4 or np.linalg.norm(pts[0] - pts[3]) < 4:
                continue
            boxes.append(pts)
        boxes.sort(key=lambda b: (b[0, 1], b[0, 0]))
        # 同一行（纵向差不到 10 像素）的按横坐标排
        for i in range(len(boxes) - 1):
            for j in range(i, -1, -1):
                if abs(boxes[j + 1][0, 1] - boxes[j][0, 1]) < 10 and boxes[j + 1][0, 0] < boxes[j][0, 0]:
                    boxes[j], boxes[j + 1] = boxes[j + 1], boxes[j]
                else:
                    break
        return boxes[:MAX_BOXES]

    # ---- 识别 ------------------------------------------------------------
    def recognize(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        import cv2
        out: list[tuple[str, float]] = [("", 0.0)] * len(crops)
        ratios = [c.shape[1] / max(1, c.shape[0]) for c in crops]
        order = np.argsort(ratios)
        for b in range(0, len(crops), REC_BATCH):
            idx = order[b:b + REC_BATCH]
            max_r = max(320 / REC_H, max(ratios[i] for i in idx))
            W = int(math.ceil(REC_H * max_r))
            batch = np.zeros((len(idx), 3, REC_H, W), np.float32)
            for k, i in enumerate(idx):
                c = crops[i]
                w = min(W, max(1, int(math.ceil(REC_H * ratios[i]))))
                im = cv2.resize(c, (w, REC_H)).astype(np.float32) / 255.0
                batch[k, :, :, :w] = ((im - 0.5) / 0.5).transpose(2, 0, 1)
            with self._lock:
                pred = self.rec.run(None, {self.rec.get_inputs()[0].name: batch})[0]
            for k, i in enumerate(idx):
                out[int(i)] = self._decode(pred[k])
        return out

    def _decode(self, p: np.ndarray) -> tuple[str, float]:
        ids = p.argmax(axis=1)
        probs = p.max(axis=1)
        keep = ids != 0
        keep[1:] &= ids[1:] != ids[:-1]
        sel = ids[keep]
        if not len(sel):
            return "", 0.0
        text = "".join(self.chars[int(i)] if int(i) < len(self.chars) else "" for i in sel)
        return text, float(probs[keep].mean())

    # ---- 整张图 ----------------------------------------------------------
    def read(self, bgr: np.ndarray) -> list[dict]:
        """一张 BGR 图 → [{"t": 文字, "s": 分数, "b": [x0,y0,…,x3,y3]（0–1，相对图片宽高）}]，按阅读顺序。"""
        boxes = self.detect(bgr)
        if not boxes:
            return []
        crops = [_crop(bgr, b) for b in boxes]
        res = self.recognize(crops)
        H, W = bgr.shape[:2]
        out = []
        for b, (text, score) in zip(boxes, res):
            text = text.strip()
            if not text or score < DROP_SCORE:
                continue
            nb = (b / np.array([W, H], np.float32)).ravel()
            out.append({"t": text, "s": round(score, 3), "b": [round(float(v), 4) for v in nb]})
        return out


def _order(pts: np.ndarray) -> np.ndarray:
    """四个点排成 左上 → 右上 → 右下 → 左下。"""
    pts = pts[np.argsort(pts[:, 0])]
    left, right = pts[:2], pts[2:]
    tl, bl = left[np.argsort(left[:, 1])]
    tr, br = right[np.argsort(right[:, 1])]
    return np.array([tl, tr, br, bl], np.float32)


def _box_score(prob: np.ndarray, pts: np.ndarray) -> float:
    import cv2
    h, w = prob.shape
    xmin, xmax = int(np.clip(np.floor(pts[:, 0].min()), 0, w - 1)), int(np.clip(np.ceil(pts[:, 0].max()), 0, w - 1))
    ymin, ymax = int(np.clip(np.floor(pts[:, 1].min()), 0, h - 1)), int(np.clip(np.ceil(pts[:, 1].max()), 0, h - 1))
    mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), np.uint8)
    q = pts.copy()
    q[:, 0] -= xmin
    q[:, 1] -= ymin
    cv2.fillPoly(mask, [q.astype(np.int32)], 1)
    return float(cv2.mean(prob[ymin:ymax + 1, xmin:xmax + 1], mask)[0])


def _crop(bgr: np.ndarray, pts: np.ndarray) -> np.ndarray:
    import cv2
    w = int(max(np.linalg.norm(pts[0] - pts[1]), np.linalg.norm(pts[3] - pts[2])))
    h = int(max(np.linalg.norm(pts[0] - pts[3]), np.linalg.norm(pts[1] - pts[2])))
    w, h = max(1, w), max(1, h)
    M = cv2.getPerspectiveTransform(pts, np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32))
    im = cv2.warpPerspective(bgr, M, (w, h), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC)
    if h / w >= 1.5:
        im = np.rot90(im)
    return np.ascontiguousarray(im)


# ---------------------------------------------------------------------------
# 搜索用的文字归一化：全角转半角、大小写、空白。照片里同一句话常被拆成几行，搜索时把整张的文字连起来比。
# ---------------------------------------------------------------------------

def norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKC", s or "").lower()
    return "".join(ch for ch in s if not ch.isspace())


def full_text(lines: list[dict]) -> str:
    return "\n".join(l.get("t", "") for l in lines or [])
