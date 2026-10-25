"""选区进阶：色彩范围、选择并遮住（调整边缘）、存储 / 载入选区。

- 色彩范围：在 Lab 空间里按到取样颜色的距离选（“颜色容差”越大选得越多、边缘越软），或者按预设选
  红 / 黄 / 绿 / 青 / 蓝 / 洋红、高光 / 中间调 / 阴影、肤色。
- 选择并遮住：在选区边缘一圈（“边缘检测半径”）里用导向滤波按照片本身的边缘重新算选区——头发、树枝这种
  选框选不出来的细节能跟着画面走；再加平滑、羽化、对比度、移动边缘。
"""
from __future__ import annotations

import numpy as np

from ..i18n import t as _t

RANGE_PRESETS = [("sampled", _t("取样颜色")), ("reds", _t("红色")), ("yellows", _t("黄色")), ("greens", _t("绿色")),
                 ("cyans", _t("青色")), ("blues", _t("蓝色")), ("magentas", _t("洋红")), ("highlights", _t("高光")),
                 ("midtones", _t("中间调")), ("shadows", _t("阴影")), ("skin", _t("肤色"))]
_HUES = {"reds": 0, "yellows": 60, "greens": 120, "cyans": 180, "blues": 240, "magentas": 300}


def _lab(rgb8: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(np.ascontiguousarray(rgb8[..., :3]), cv2.COLOR_RGB2LAB).astype(np.float32)


def sample_lab(rgb8: np.ndarray, x: int, y: int, r: int = 1) -> np.ndarray:
    h, w = rgb8.shape[:2]
    x0, y0, x1, y1 = max(0, x - r), max(0, y - r), min(w, x + r + 1), min(h, y + r + 1)
    return _lab(rgb8[y0:y1, x0:x1]).reshape(-1, 3).mean(axis=0)


def color_range(rgb8: np.ndarray, preset: str = "sampled", samples=(), fuzziness: float = 40,
                invert: bool = False) -> np.ndarray:
    """返回 uint8 选区（255 = 选中）。samples：Lab 颜色列表；fuzziness：0–200。"""
    import cv2
    rgb = np.ascontiguousarray(rgb8[..., :3])
    f = rgb.astype(np.float32) / 255
    L = f @ np.array([0.299, 0.587, 0.114], np.float32)
    if preset == "sampled":
        if not len(samples):
            m = np.zeros(rgb.shape[:2], np.float32)
        else:
            lab = _lab(rgb)
            d = np.full(rgb.shape[:2], 1e9, np.float32)
            for s in samples:
                d = np.minimum(d, np.sqrt(((lab - np.asarray(s, np.float32)) ** 2).sum(axis=-1)))
            fz = max(1.0, float(fuzziness)) * 0.6
            m = np.clip(1 - d / fz, 0, 1)
            m = np.where(d <= fz * 0.15, 1.0, m)
    elif preset in _HUES:
        hsv = cv2.cvtColor(f, cv2.COLOR_RGB2HSV)
        dh = np.abs((hsv[..., 0] - _HUES[preset] + 180) % 360 - 180)
        m = np.clip(1 - (dh - 15) / 30, 0, 1) * np.clip(hsv[..., 1] * 3, 0, 1)
    elif preset == "highlights":
        m = np.clip((L - 0.62) / 0.18, 0, 1)
    elif preset == "shadows":
        m = np.clip((0.38 - L) / 0.18, 0, 1)
    elif preset == "midtones":
        m = np.clip(1 - np.abs(L - 0.5) / 0.25, 0, 1)
    elif preset == "skin":
        hsv = cv2.cvtColor(f, cv2.COLOR_RGB2HSV)
        hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        hh = np.clip(1 - np.abs(((hue + 180) % 360 - 180) - 22) / 22, 0, 1)
        m = hh * np.clip(1 - np.abs(sat - 0.38) / 0.3, 0, 1) * np.clip((val - 0.2) / 0.2, 0, 1)
    else:
        m = np.zeros(rgb.shape[:2], np.float32)
    if invert:
        m = 1 - m
    return np.clip(m * 255 + 0.5, 0, 255).astype(np.uint8)


def _box(a, r):
    import cv2
    return cv2.boxFilter(a, -1, (2 * r + 1, 2 * r + 1), borderType=cv2.BORDER_REFLECT)


def guided(I: np.ndarray, p: np.ndarray, r: int, eps: float = 1e-3) -> np.ndarray:
    """导向滤波（He 等，灰度引导图），I、p 为 float32 0–1。"""
    mI, mp = _box(I, r), _box(p, r)
    cov = _box(I * p, r) - mI * mp
    var = _box(I * I, r) - mI * mI
    a = cov / (var + eps)
    b = mp - a * mI
    return _box(a, r) * I + _box(b, r)


def refine(rgb8: np.ndarray, mask: np.ndarray, radius: float = 10, smooth: float = 0, feather: float = 0,
           contrast: float = 0, shift: float = 0) -> np.ndarray:
    """选择并遮住。mask：uint8 选区；radius：边缘检测半径（像素）；smooth 0–100；feather 像素；contrast 0–100；
    shift −100…100（%，负 = 往里收）。返回新的 uint8 选区。"""
    import cv2
    m = mask.astype(np.float32) / 255
    h, w = m.shape
    r = int(round(radius))
    if r >= 1:
        hard = (m > 0.5).astype(np.uint8)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        band = cv2.dilate(hard, k) != cv2.erode(hard, k)
        if band.any():
            ys, xs = np.nonzero(band)
            x0, y0 = max(0, xs.min() - 2 * r), max(0, ys.min() - 2 * r)
            x1, y1 = min(w, xs.max() + 2 * r + 1), min(h, ys.max() + 2 * r + 1)
            gray = cv2.cvtColor(np.ascontiguousarray(rgb8[y0:y1, x0:x1, :3]), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
            sub = m[y0:y1, x0:x1]
            s = 1 if max(x1 - x0, y1 - y0) < 1500 else 2           # 很大时降采样算（快速导向滤波）
            if s > 1:
                gs = cv2.resize(gray, None, fx=1 / s, fy=1 / s, interpolation=cv2.INTER_AREA)
                ps = cv2.resize(sub, (gs.shape[1], gs.shape[0]), interpolation=cv2.INTER_AREA)
                q = guided(gs, ps, max(1, r // s), 1e-4)
                q = cv2.resize(q, (sub.shape[1], sub.shape[0]), interpolation=cv2.INTER_LINEAR)
            else:
                q = guided(gray, sub, max(1, r), 1e-4)
            q = np.clip(q, 0, 1)
            bnd = band[y0:y1, x0:x1].astype(np.float32)
            bnd = cv2.GaussianBlur(bnd, (0, 0), max(1.0, r / 3))      # 边缘带内用新结果，带外保持原样
            m = m.copy()
            m[y0:y1, x0:x1] = sub + (q - sub) * np.clip(bnd * 1.5, 0, 1)
    if smooth > 0:
        sg = 0.5 + float(smooth) / 100 * 6
        b = cv2.GaussianBlur(m, (0, 0), sg)
        m = np.clip((b - 0.5) * (1 + float(smooth) / 25) + 0.5, 0, 1)
    if feather > 0:
        m = cv2.GaussianBlur(m, (0, 0), float(feather) / 2)
    if contrast > 0:
        m = np.clip((m - 0.5) * (1 + float(contrast) / 100 * 9) + 0.5, 0, 1)
    if shift:
        m = np.clip(m + float(shift) / 200 * (1 - np.abs(2 * m - 1)) * 2, 0, 1)
    return np.clip(m * 255 + 0.5, 0, 255).astype(np.uint8)
