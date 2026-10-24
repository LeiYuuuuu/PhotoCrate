"""滤镜：作用在一个图层的像素上（有选区时只作用在选区里）。

每个滤镜 fn(rgba, scale, **参数) → rgba，rgba 为 float32 0–1、不预乘。scale = 当前像素 / 原尺寸像素：
在缩小的代理图上预览时半径按比例缩小，看起来和应用到原图上一样。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from ..i18n import t as _t


@dataclass
class FilterSpec:
    name: str
    fn: Callable
    params: list[tuple] = field(default_factory=list)      # (键, 名称, 最小, 最大, 默认, 小数位, 单位)
    checks: list[tuple] = field(default_factory=list)      # (键, 名称, 默认)


def _premul(rgba):
    out = rgba.copy()
    out[..., :3] *= rgba[..., 3:4]
    return out


def _unpremul(rgba):
    out = rgba.copy()
    a = rgba[..., 3:4]
    out[..., :3] = np.where(a > 1e-5, rgba[..., :3] / np.maximum(a, 1e-5), 0)
    return np.clip(out, 0, 1)


def _blur(img, sigma):
    import cv2
    if sigma < 0.25:
        return img
    return cv2.GaussianBlur(img, (0, 0), sigmaX=sigma, sigmaY=sigma, borderType=cv2.BORDER_REFLECT)


def gaussian(rgba, scale, radius=4.0):
    return _unpremul(_blur(_premul(rgba), float(radius) * scale / 2))


def motion(rgba, scale, angle=0.0, distance=20.0):
    import cv2
    n = max(1, int(round(float(distance) * scale)))
    if n < 2:
        return rgba
    k = np.zeros((n * 2 + 1, n * 2 + 1), np.float32)
    a = math.radians(float(angle))
    for i in range(-n, n + 1):
        x = int(round(n + i * math.cos(a) / 2))
        y = int(round(n - i * math.sin(a) / 2))
        k[y, x] = 1
    k /= k.sum()
    return _unpremul(cv2.filter2D(_premul(rgba), -1, k, borderType=cv2.BORDER_REFLECT))


def unsharp(rgba, scale, amount=100.0, radius=1.5, threshold=0.0):
    rgb = rgba[..., :3]
    blur = _blur(rgb, max(0.3, float(radius) * scale))
    diff = rgb - blur
    if threshold:
        diff = np.where(np.abs(diff).max(axis=-1, keepdims=True) * 255 >= float(threshold), diff, 0)
    out = rgba.copy()
    out[..., :3] = np.clip(rgb + diff * float(amount) / 100, 0, 1)
    return out


def denoise(rgba, scale, strength=40.0):
    import cv2
    s = float(strength) / 100
    rgb = np.ascontiguousarray(rgba[..., :3], dtype=np.float32)
    d = int(max(3, round(7 * max(scale, 0.3)))) | 1
    flt = cv2.bilateralFilter(rgb, d, 0.04 + 0.16 * s, max(1.0, 4.0 * scale))
    out = rgba.copy()
    out[..., :3] = rgb + (flt - rgb) * min(1.0, 0.4 + s)
    return out


def highpass(rgba, scale, radius=6.0):
    rgb = rgba[..., :3]
    out = rgba.copy()
    out[..., :3] = np.clip(0.5 + rgb - _blur(rgb, max(0.3, float(radius) * scale)), 0, 1)
    return out


def mosaic(rgba, scale, cell=16.0):
    import cv2
    c = max(1, int(round(float(cell) * scale)))
    h, w = rgba.shape[:2]
    small = cv2.resize(_premul(rgba), (max(1, math.ceil(w / c)), max(1, math.ceil(h / c))),
                       interpolation=cv2.INTER_AREA)
    big = cv2.resize(small, (small.shape[1] * c, small.shape[0] * c), interpolation=cv2.INTER_NEAREST)
    return _unpremul(big[:h, :w])


def add_noise(rgba, scale, amount=8.0, mono=True):
    h, w = rgba.shape[:2]
    rng = np.random.default_rng(4321)
    a = float(amount) / 100
    n = rng.standard_normal((h, w, 1) if mono else (h, w, 3), dtype=np.float32) * a * 0.5
    out = rgba.copy()
    out[..., :3] = np.clip(rgba[..., :3] + n, 0, 1)
    return out


def desaturate(rgba, scale):
    out = rgba.copy()
    l = rgba[..., 0] * 0.299 + rgba[..., 1] * 0.587 + rgba[..., 2] * 0.114
    out[..., :3] = l[..., None]
    return out


def invert(rgba, scale):
    out = rgba.copy()
    out[..., :3] = 1 - rgba[..., :3]
    return out


def auto_tone(rgba, scale):
    """每个通道按 0.3% / 99.7% 分位数拉伸（类似 Photoshop 的“自动色调”）。"""
    out = rgba.copy()
    vis = rgba[..., 3] > 0.5
    for i in range(3):
        ch = rgba[..., i]
        v = ch[vis] if vis.any() else ch.ravel()
        if v.size > 400_000:
            v = v[:: v.size // 400_000]
        lo, hi = np.percentile(v, (0.3, 99.7)) if v.size else (0.0, 1.0)
        if hi - lo > 1e-3:
            out[..., i] = np.clip((ch - lo) / (hi - lo), 0, 1)
    return out


def posterize(rgba, scale, levels=6.0):
    n = max(2, int(levels)) - 1
    out = rgba.copy()
    out[..., :3] = np.round(rgba[..., :3] * n) / n
    return out


def threshold(rgba, scale, level=128.0):
    out = rgba.copy()
    l = rgba[..., 0] * 0.299 + rgba[..., 1] * 0.587 + rgba[..., 2] * 0.114
    out[..., :3] = (l >= float(level) / 255).astype(np.float32)[..., None]
    return out


FILTERS: dict[str, FilterSpec] = {
    "gaussian": FilterSpec(_t("高斯模糊"), gaussian, [("radius", _t("半径"), 0.2, 250, 4.0, 1, " px")]),
    "motion": FilterSpec(_t("动感模糊"), motion, [("angle", _t("角度"), -180, 180, 0, 0, "°"),
                                              ("distance", _t("距离"), 1, 500, 20, 0, " px")]),
    "unsharp": FilterSpec(_t("USM 锐化"), unsharp, [("amount", _t("数量"), 1, 500, 100, 0, "%"),
                                                ("radius", _t("半径"), 0.1, 50, 1.5, 1, " px"),
                                                ("threshold", _t("阈值"), 0, 255, 0, 0, "")]),
    "denoise": FilterSpec(_t("减少杂色"), denoise, [("strength", _t("强度"), 1, 100, 40, 0, "")]),
    "highpass": FilterSpec(_t("高反差保留"), highpass, [("radius", _t("半径"), 0.2, 250, 6, 1, " px")]),
    "mosaic": FilterSpec(_t("马赛克"), mosaic, [("cell", _t("单元格大小"), 2, 300, 16, 0, " px")]),
    "noise": FilterSpec(_t("添加杂色"), add_noise, [("amount", _t("数量"), 0.1, 100, 8, 1, "%")], [("mono", _t("单色"), True)]),
    "posterize": FilterSpec(_t("色调分离"), posterize, [("levels", _t("色阶"), 2, 32, 6, 0, "")]),
    "threshold": FilterSpec(_t("阈值"), threshold, [("level", _t("阈值色阶"), 1, 255, 128, 0, "")]),
    "desaturate": FilterSpec(_t("去色"), desaturate),
    "invert": FilterSpec(_t("反相"), invert),
    "auto_tone": FilterSpec(_t("自动色调"), auto_tone),
}


def defaults(fid: str) -> dict:
    spec = FILTERS[fid]
    d = {k: v for k, _, _, _, v, _, _ in spec.params}
    d.update({k: v for k, _, v in spec.checks})
    return d


def run(fid: str, rgba: np.ndarray, scale: float = 1.0, params: dict | None = None) -> np.ndarray:
    spec = FILTERS[fid]
    kw = defaults(fid)
    kw.update(params or {})
    return np.clip(spec.fn(rgba.astype(np.float32, copy=False), scale, **kw), 0, 1).astype(np.float32, copy=False)
