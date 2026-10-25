"""Photoshop「图像 → 调整」里的另外 6 种：黑白、通道混合器、可选颜色、照片滤镜、渐变映射、色彩平衡。

它们都是调整图层参数里的一个子字典（{"on": True, ...}），在 adjust.develop() 的分离色调之后、反相之前按顺序应用。
输入输出 float32 RGB 0–1（sRGB）。公式按 Photoshop 的表现来做：
- 黑白：最小通道 = 白色成分（原样）；次大 − 最小 = 两个最大通道合成的二次色（黄 / 青 / 洋红）；
  最大 − 次大 = 主色（红 / 绿 / 蓝）。灰度 = 最小 + (次大 − 最小)·二次色权重 + (最大 − 次大)·主色权重；
  默认权重 红 40、黄 60、绿 40、青 60、蓝 20、洋红 80（和 Photoshop 的默认值一样）。
- 可选颜色：九个范围（红黄绿青蓝洋红 + 白 / 中性 / 黑）各自的 青 / 洋红 / 黄 / 黑；颜色范围的权重 = 上面那种分解里
  这个颜色的成分，白 / 黑 / 中性按最大、最小通道算。“相对”= 按已有的油墨量成比例加减，“绝对”= 直接加减。
"""
from __future__ import annotations

import copy

import numpy as np

from ..i18n import t as _t

LUMA = np.array([0.299, 0.587, 0.114], np.float32)

BW_KEYS = [("reds", _t("红色"), 40), ("yellows", _t("黄色"), 60), ("greens", _t("绿色"), 40), ("cyans", _t("青色"), 60),
           ("blues", _t("蓝色"), 20), ("magentas", _t("洋红"), 80)]
SEL_RANGES = [("reds", _t("红色")), ("yellows", _t("黄色")), ("greens", _t("绿色")), ("cyans", _t("青色")),
              ("blues", _t("蓝色")), ("magentas", _t("洋红")), ("whites", _t("白色")), ("neutrals", _t("中性色")),
              ("blacks", _t("黑色"))]
TONES = [("shadows", _t("阴影")), ("midtones", _t("中间调")), ("highlights", _t("高光"))]
PHOTO_FILTERS = [("#EC8A00", _t("加温滤镜（85）")), ("#FA9600", _t("加温滤镜（LBA）")), ("#EBB113", _t("加温滤镜（81）")),
                 ("#006DFF", _t("冷却滤镜（80）")), ("#005DFF", _t("冷却滤镜（LBB）")), ("#00B5FF", _t("冷却滤镜（82）")),
                 ("#EA1A1A", _t("红")), ("#F3840B", _t("橙")), ("#F9E31C", _t("黄")), ("#19C919", _t("绿")),
                 ("#1DCBEA", _t("青")), ("#1D35EA", _t("蓝")), ("#9B1DEA", _t("紫")), ("#E31DEA", _t("洋红")),
                 ("#AC7A33", _t("深褐")), ("#FF8C8C", _t("深红")), ("#2A9FBF", _t("水下"))]
GRADIENTS = [("bw", _t("黑 → 白"), [[0, "#000000"], [1, "#FFFFFF"]]),
             ("teal_orange", _t("青 → 橙（电影感）"), [[0, "#0B2A3A"], [0.5, "#6B8A8F"], [1, "#FFC48A"]]),
             ("sepia", _t("深褐（老照片）"), [[0, "#1E120A"], [0.55, "#8E6B45"], [1, "#F6E7C8"]]),
             ("violet_gold", _t("紫 → 金"), [[0, "#1B0B3A"], [0.5, "#B03A6E"], [1, "#FFD36B"]]),
             ("blue_white", _t("蓝 → 白（冷调）"), [[0, "#04122E"], [0.6, "#4A7FC1"], [1, "#F2F7FF"]]),
             ("infrared", _t("红外"), [[0, "#000000"], [0.4, "#7A1B1B"], [0.75, "#F2C14E"], [1, "#FFFFFF"]])]

DEFAULTS = {
    "bw": {"on": False, **{k: float(v) for k, _, v in BW_KEYS}, "tint": False, "tint_hue": 35.0, "tint_sat": 25.0},
    "mixer": {"on": False, "mono": False, "r": [100.0, 0.0, 0.0, 0.0], "g": [0.0, 100.0, 0.0, 0.0],
              "b": [0.0, 0.0, 100.0, 0.0], "gray": [40.0, 40.0, 20.0, 0.0]},
    "selective": {"on": False, "relative": True, **{k: [0.0, 0.0, 0.0, 0.0] for k, _ in SEL_RANGES}},
    "photo_filter": {"on": False, "color": "#EC8A00", "density": 25.0, "preserve": True},
    "gradient_map": {"on": False, "stops": [[0, "#000000"], [1, "#FFFFFF"]], "reverse": False},
    "color_balance": {"on": False, "preserve": True, **{k: [0.0, 0.0, 0.0] for k, _ in TONES}},
}
NAMES = {"bw": _t("黑白"), "mixer": _t("通道混合器"), "selective": _t("可选颜色"), "photo_filter": _t("照片滤镜"),
         "gradient_map": _t("渐变映射"), "color_balance": _t("色彩平衡")}
ORDER = ["color_balance", "selective", "mixer", "photo_filter", "bw", "gradient_map"]


def defaults() -> dict:
    return copy.deepcopy(DEFAULTS)


def normalize_into(p: dict, src: dict) -> None:
    """把旧参数里这 6 种的子字典合并进默认值（缺的键补上）。"""
    for k, d in DEFAULTS.items():
        v = src.get(k)
        cur = copy.deepcopy(d)
        if isinstance(v, dict):
            cur.update(copy.deepcopy(v))
        p[k] = cur


def active(p: dict) -> list[str]:
    return [k for k in ORDER if isinstance(p.get(k), dict) and p[k].get("on")]


def _hex(c: str) -> np.ndarray:
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)], np.float32)


def _decompose(rgb):
    """每个像素：最小、次大、最大通道和它们的下标。"""
    order = np.argsort(rgb, axis=-1)
    srt = np.take_along_axis(rgb, order, axis=-1)
    return srt[..., 0], srt[..., 1], srt[..., 2], order


# 主色：最大通道是 R / G / B；二次色：最小通道是 B（黄）/ R（青）/ G（洋红）
_PRIMARY = {0: "reds", 1: "greens", 2: "blues"}
_SECONDARY = {2: "yellows", 0: "cyans", 1: "magentas"}


def color_weights(rgb) -> dict[str, np.ndarray]:
    mn, md, mx, order = _decompose(np.clip(rgb, 0, 1))
    w = {}
    pri = mx - md
    sec = md - mn
    top, low = order[..., 2], order[..., 0]
    for i, name in _PRIMARY.items():
        w[name] = np.where(top == i, pri, 0).astype(np.float32)
    for i, name in _SECONDARY.items():
        w[name] = np.where(low == i, sec, 0).astype(np.float32)
    w["whites"] = np.clip((mn - 0.5) * 2, 0, 1)
    w["blacks"] = np.clip((0.5 - mx) * 2, 0, 1)
    w["neutrals"] = np.clip(1 - (np.abs(mx - 0.5) + np.abs(mn - 0.5)), 0, 1)
    return w


def black_white(rgb, q: dict):
    mn, md, mx, order = _decompose(np.clip(rgb, 0, 1))
    top, low = order[..., 2], order[..., 0]
    wp = np.choose(top, [q["reds"] / 100, q["greens"] / 100, q["blues"] / 100]).astype(np.float32)
    ws = np.choose(low, [q["cyans"] / 100, q["magentas"] / 100, q["yellows"] / 100]).astype(np.float32)
    gray = np.clip(mn + (md - mn) * ws + (mx - md) * wp, 0, 1)
    out = np.repeat(gray[..., None], 3, axis=-1)
    if q.get("tint"):
        import colorsys
        c = np.array(colorsys.hls_to_rgb((float(q["tint_hue"]) % 360) / 360, 0.5, 1.0), np.float32)
        c = c - float(c @ LUMA)
        k = float(q["tint_sat"]) / 100 * 0.6
        out = out + (k * 4 * gray * (1 - gray))[..., None] * c
    return out


def channel_mixer(rgb, q: dict):
    def mix(row):
        r, g, b, c = (float(v) / 100 for v in row)
        return rgb[..., 0] * r + rgb[..., 1] * g + rgb[..., 2] * b + c
    if q.get("mono"):
        m = mix(q["gray"])
        return np.repeat(m[..., None], 3, axis=-1)
    return np.dstack([mix(q["r"]), mix(q["g"]), mix(q["b"])])


def selective_color(rgb, q: dict):
    w = color_weights(rgb)
    out = rgb.copy()
    rel = bool(q.get("relative", True))
    for name, _ in SEL_RANGES:
        c, m, y, k = (float(v) / 100 for v in q[name])
        if not (c or m or y or k):
            continue
        ww = w[name]
        for ch, ink in ((0, c), (1, m), (2, y)):
            amt = ink + k                                  # 黑 = 三种油墨一起加减
            if not amt:
                continue
            v = out[..., ch]
            base = (1 - v) if rel else 1.0                # 相对：按已有的油墨量（1 − 通道值）成比例
            out[..., ch] = v - amt * base * ww
    return out


def photo_filter(rgb, q: dict):
    c = _hex(q.get("color", "#EC8A00"))
    d = float(q.get("density", 25)) / 100
    out = rgb + (rgb * c - rgb) * d                      # 正片叠底一层滤镜色，按浓度混合
    if q.get("preserve", True):
        L0 = rgb @ LUMA
        L1 = out @ LUMA
        out = out + (L0 - L1)[..., None]
    return out


def gradient_lut(stops, n: int = 1024, reverse: bool = False) -> np.ndarray:
    st = sorted(((float(p), _hex(c)) for p, c in stops), key=lambda s: s[0]) or [(0.0, _hex("#000000"))]
    xs = np.linspace(0, 1, n)
    if reverse:
        xs = 1 - xs
    pos = np.array([s[0] for s in st])
    cols = np.stack([s[1] for s in st])
    return np.stack([np.interp(xs, pos, cols[:, i]) for i in range(3)], axis=-1).astype(np.float32)


def gradient_map(rgb, q: dict):
    lut = gradient_lut(q.get("stops") or DEFAULTS["gradient_map"]["stops"], reverse=bool(q.get("reverse")))
    L = np.clip(rgb @ LUMA, 0, 1)
    idx = L * (len(lut) - 1)
    i0 = np.minimum(idx.astype(np.int32), len(lut) - 2)
    f = (idx - i0)[..., None]
    return lut[i0] + (lut[i0 + 1] - lut[i0]) * f


def tone_weights(L):
    """GIMP / Photoshop 色彩平衡的三段权重（平滑过渡，中间调在两头为 0）。"""
    a, b, s = 0.25, 0.333, 0.7
    sh = np.clip((L - b) / -a + 0.5, 0, 1) * s
    mid = np.clip((L - b) / a + 0.5, 0, 1) * np.clip((L + b - 1) / -a + 0.5, 0, 1) * s
    hi = np.clip((L + b - 1) / a + 0.5, 0, 1) * s
    return {"shadows": sh, "midtones": mid, "highlights": hi}


def color_balance(rgb, q: dict):
    L0 = np.clip(rgb @ LUMA, 0, 1)
    w = tone_weights(L0)
    out = rgb.copy()
    for tone, _ in TONES:
        d = np.array([float(v) / 100 for v in q[tone]], np.float32)
        if not d.any():
            continue
        out = out + w[tone][..., None] * d
    if q.get("preserve", True):
        out = out + (L0 - np.clip(out, 0, 1) @ LUMA)[..., None]
    return out


FUNCS = {"bw": black_white, "mixer": channel_mixer, "selective": selective_color, "photo_filter": photo_filter,
         "gradient_map": gradient_map, "color_balance": color_balance}


def apply(rgb, p: dict):
    for k in active(p):
        rgb = np.clip(FUNCS[k](np.clip(rgb, 0, 1), p[k]), 0, 1).astype(np.float32)
    return rgb
