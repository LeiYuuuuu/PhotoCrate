"""天空替换：自动找出天空，换成另一片天空（内置几种，或用自己的照片），并让前景的色调和新天空协调。

找天空（离线、不用模型）：缩小后按“亮、平滑（没有纹理）、偏蓝或偏白”找候选像素，只保留和画面上边相连的大块，
清理后放大，再用「选择并遮住」的导向滤波沿树梢、楼顶的真实边缘精修。结果放进一个图层组：新天空（带蒙版）+
一个只作用在前景上的照片滤镜（颜色取新天空靠近地平线的颜色），都可以再改。
"""
from __future__ import annotations

import numpy as np

from ..i18n import t as _t

PRESETS = [("clear", _t("晴空")), ("sunset", _t("黄昏")), ("overcast", _t("多云")), ("night", _t("星空"))]
_GRAD = {"clear": [(0, "#2E6CC4"), (0.65, "#7FB4E8"), (1, "#CFE6F7")],
         "sunset": [(0, "#24174A"), (0.45, "#9B3F6B"), (0.75, "#F0803A"), (1, "#FCD27C")],
         "overcast": [(0, "#6F7A88"), (1, "#C8CED6")],
         "night": [(0, "#030A1C"), (1, "#1C3863")]}


def _hex(c):
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255


def _noise(h, w, seed, octaves=5):
    import cv2
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        s = 2 ** (o + 2)
        n = rng.random((max(2, h * s // max(h, w) + 2), max(2, w * s // max(h, w) + 2))).astype(np.float32)
        out += cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC) * amp
        tot += amp
        amp *= 0.55
    return out / tot


def make_sky(kind: str, w: int, h: int, seed: int = 7) -> np.ndarray:
    """生成一片天空（RGB uint8）。"""
    stops = _GRAD.get(kind, _GRAD["clear"])
    t = np.linspace(0, 1, h)[:, None]
    pos = np.array([p for p, _ in stops])
    cols = np.stack([_hex(c) for _, c in stops])
    grad = np.stack([np.interp(t[:, 0], pos, cols[:, i]) for i in range(3)], -1)[:, None, :]
    img = np.repeat(grad, w, axis=1).astype(np.float32)
    if kind in ("clear", "overcast", "sunset"):
        n = _noise(h, w, seed)
        cover = {"clear": 0.58, "overcast": 0.35, "sunset": 0.6}[kind]
        c = np.clip((n - cover) * 4, 0, 1) * np.linspace(0.6, 1, h)[:, None]
        cloud = np.array([1, 1, 1], np.float32) if kind != "sunset" else _hex("#FFC9A0")
        shade = 0.85 + 0.15 * n
        img = img * (1 - c[..., None] * 0.85) + (cloud * shade[..., None]) * (c[..., None] * 0.85)
    if kind == "night":
        rng = np.random.default_rng(seed)
        k = max(50, w * h // 1500)
        ys, xs = rng.integers(0, h, k), rng.integers(0, w, k)
        img[ys, xs] = np.clip(img[ys, xs] + rng.random((k, 1)) * 0.9, 0, 1)
    return np.clip(img * 255 + 0.5, 0, 255).astype(np.uint8)


def detect(rgb8: np.ndarray) -> np.ndarray:
    """找天空：返回 uint8 选区（255 = 天空）。"""
    import cv2
    from .select import refine
    H, W = rgb8.shape[:2]
    s = min(1.0, 800 / max(H, W))
    small = cv2.resize(np.ascontiguousarray(rgb8[..., :3]), (max(1, int(W * s)), max(1, int(H * s))),
                       interpolation=cv2.INTER_AREA)
    f = small.astype(np.float32) / 255
    hsv = cv2.cvtColor(f, cv2.COLOR_RGB2HSV)
    V, S, Hh = hsv[..., 2], hsv[..., 1], hsv[..., 0]
    gray = cv2.GaussianBlur(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY), (0, 0), 1.2)
    gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    grad = cv2.GaussianBlur(np.sqrt(gx * gx + gy * gy), (0, 0), 2)
    blue = (Hh > 180) & (Hh < 250) & (S > 0.12)
    bright_gray = (S < 0.18) & (V > 0.6)
    warm = ((Hh < 50) | (Hh > 300)) & (V > 0.55) & (np.arange(small.shape[0])[:, None] < small.shape[0] * 0.55)
    cand = (V > 0.35) & (grad < 0.06) & (blue | bright_gray | warm)
    cand = cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, 4)
    keep = np.zeros_like(cand)
    top = set(np.unique(lab[:max(1, small.shape[0] // 50)])) - {0}
    for i in top:
        if stats[i, cv2.CC_STAT_AREA] > cand.size * 0.01:
            keep[lab == i] = 1
    keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    # 边缘附近梯度大、没被选进来：往外扩两个像素盖住真正的边，交给导向滤波按照片的边缘决定
    keep = cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    m = cv2.resize(keep * 255, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.uint8)
    if not m.any():
        return m
    return refine(rgb8, m, radius=max(6, max(H, W) / 150), smooth=10)


def replace(doc, mask: np.ndarray, sky_rgb: np.ndarray, harmonize: float = 20.0, brightness: float = 0.0):
    """在文档最上面放一个“天空替换”组：新天空（按天空范围铺满、带蒙版）+ 前景协调（照片滤镜，只作用于前景）。"""
    import cv2
    from . import adjust
    from .document import Layer, convert
    H, W = doc.height, doc.width
    ys = np.nonzero(mask.max(axis=1) > 127)[0]
    sky_h = int(ys.max()) + 1 if len(ys) else H // 2
    tgt_h = max(sky_h, int(H * 0.25))
    sh, sw = sky_rgb.shape[:2]
    k = max(W / sw, tgt_h / sh)
    big = cv2.resize(sky_rgb, (int(np.ceil(sw * k)), int(np.ceil(sh * k))), interpolation=cv2.INTER_CUBIC)
    x0 = (big.shape[1] - W) // 2
    canvas = np.zeros((H, W, 3), np.uint8)
    hh = min(H, big.shape[0])
    canvas[:hh] = big[:hh, x0:x0 + W]
    if brightness:
        canvas = np.clip(canvas.astype(np.float32) * (1 + brightness / 100), 0, 255).astype(np.uint8)
    rgba = np.dstack([canvas, np.full((H, W), 255, np.uint8)])
    band = canvas[max(0, sky_h - max(4, H // 20)):max(1, sky_h)].reshape(-1, 3)
    tone = band.mean(axis=0) if band.size else np.array([200, 200, 200])
    col = "#{:02X}{:02X}{:02X}".format(*[int(v) for v in tone])
    g = doc._register(Layer("group", _t("天空替换")))
    g.blend = "pass_through"
    sky = doc._register(Layer("pixel", _t("新天空"), np.ascontiguousarray(convert(rgba, doc.dtype))))
    sky.mask = mask.copy()
    sky.parent = g.id
    fg = doc._register(Layer("adjust", _t("前景协调"),
                             params=adjust.normalize({"photo_filter": {"on": True, "color": col,
                                                                        "density": float(harmonize)}})))
    fg.mask = 255 - mask
    fg.parent = g.id
    with doc.edit(_t("天空替换")):
        doc.layers.extend([fg, sky, g])
        doc.active_id = sky.id
        doc.mask_edit = False
    return g
