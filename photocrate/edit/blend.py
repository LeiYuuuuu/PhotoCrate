"""图层混合模式。公式取自 W3C Compositing and Blending Level 1（与 Photoshop 的同名模式一致或非常接近）。

颜色都是 float32 0–1、不预乘 alpha。composite() 把一层（cs, a）按模式叠到底下已合成的结果（cb, ab）上。
"""
from __future__ import annotations

import numpy as np
from ..i18n import t as _t

BLEND_MODES = [
    ("normal", _t("正常")), ("multiply", _t("正片叠底")), ("screen", _t("滤色")), ("overlay", _t("叠加")),
    ("soft_light", _t("柔光")), ("hard_light", _t("强光")), ("darken", _t("变暗")), ("lighten", _t("变亮")),
    ("color_dodge", _t("颜色减淡")), ("color_burn", _t("颜色加深")), ("linear_dodge", _t("线性减淡（添加）")),
    ("linear_burn", _t("线性加深")), ("difference", _t("差值")), ("exclusion", _t("排除")),
    ("hue", _t("色相")), ("saturation", _t("饱和度")), ("color", _t("颜色")), ("luminosity", _t("明度")),
]
BLEND_NAMES = dict(BLEND_MODES)
EPS = 1e-6


def _lum(c):
    return c[..., 0] * 0.3 + c[..., 1] * 0.59 + c[..., 2] * 0.11


def _clip_color(c):
    l = _lum(c)[..., None]
    n = c.min(axis=-1, keepdims=True)
    x = c.max(axis=-1, keepdims=True)
    c = np.where(n < 0, l + (c - l) * l / np.maximum(l - n, EPS), c)
    c = np.where(x > 1, l + (c - l) * (1 - l) / np.maximum(x - l, EPS), c)
    return c


def _set_lum(c, l):
    return _clip_color(c + (l - _lum(c))[..., None])


def _sat(c):
    return c.max(axis=-1) - c.min(axis=-1)


def _set_sat(c, s):
    mn = c.min(axis=-1, keepdims=True)
    mx = c.max(axis=-1, keepdims=True)
    rng = mx - mn
    return np.where(rng > EPS, (c - mn) * s[..., None] / np.maximum(rng, EPS), 0.0)


def blend(cb: np.ndarray, cs: np.ndarray, mode: str) -> np.ndarray:
    """B(cb, cs)：两层完全不透明时的结果。"""
    if mode == "multiply":
        return cb * cs
    if mode == "screen":
        return cb + cs - cb * cs
    if mode == "overlay":
        return np.where(cb <= 0.5, 2 * cb * cs, 1 - 2 * (1 - cb) * (1 - cs))
    if mode == "hard_light":
        return np.where(cs <= 0.5, 2 * cb * cs, 1 - 2 * (1 - cb) * (1 - cs))
    if mode == "soft_light":
        d = np.where(cb <= 0.25, ((16 * cb - 12) * cb + 4) * cb, np.sqrt(np.maximum(cb, 0)))
        return np.where(cs <= 0.5, cb - (1 - 2 * cs) * cb * (1 - cb), cb + (2 * cs - 1) * (d - cb))
    if mode == "darken":
        return np.minimum(cb, cs)
    if mode == "lighten":
        return np.maximum(cb, cs)
    if mode == "color_dodge":
        return np.where(cb <= 0, 0.0, np.where(cs >= 1, 1.0, np.minimum(1.0, cb / np.maximum(1 - cs, EPS))))
    if mode == "color_burn":
        return np.where(cb >= 1, 1.0, np.where(cs <= 0, 0.0, 1 - np.minimum(1.0, (1 - cb) / np.maximum(cs, EPS))))
    if mode == "linear_dodge":
        return np.minimum(1.0, cb + cs)
    if mode == "linear_burn":
        return np.maximum(0.0, cb + cs - 1)
    if mode == "difference":
        return np.abs(cb - cs)
    if mode == "exclusion":
        return cb + cs - 2 * cb * cs
    if mode == "hue":
        return _set_lum(_set_sat(cs, _sat(cb)), _lum(cb))
    if mode == "saturation":
        return _set_lum(_set_sat(cb, _sat(cs)), _lum(cb))
    if mode == "color":
        return _set_lum(cs, _lum(cb))
    if mode == "luminosity":
        return _set_lum(cb, _lum(cs))
    return cs


def composite(cb: np.ndarray, ab: np.ndarray, cs: np.ndarray, a: np.ndarray, mode: str = "normal"):
    """把源（cs 颜色, a 不透明度）按 mode 叠到底层（cb, ab）上，返回新的 (颜色, 不透明度)。"""
    if mode == "normal" or mode not in BLEND_NAMES:
        mixed = cs
    else:
        mixed = cs + (np.clip(blend(cb, cs, mode), 0, 1) - cs) * ab[..., None]
    ao = a + ab * (1 - a)
    num = a[..., None] * mixed + ((1 - a) * ab)[..., None] * cb
    co = np.where(ao[..., None] > EPS, num / np.maximum(ao, EPS)[..., None], 0.0)
    return co.astype(np.float32, copy=False), ao.astype(np.float32, copy=False)
