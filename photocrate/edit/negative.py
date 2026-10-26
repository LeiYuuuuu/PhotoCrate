"""负片反转：用相机翻拍 / 扫描仪扫的彩色或黑白底片 → 正片。纯 numpy，原图不动，返回新数组。

做法（思路同 darktable 的 negadoctor，简化成全自动）：
1. sRGB 解码成线性光，每个通道算密度 D = −log10(透过率)；
2. 每个通道各自找“最透明”（片基 / 画面里最暗的地方，密度最低）和“最密”（画面里最亮的地方）：
   取画面中间 90% 的区域（避开片夹、齿孔）的 0.5% / 99.5% 分位。彩色负片的橙色片基就这样逐通道减掉，
   偏色也一起校正，不用手动吸片基；
3. 密度线性映射到 0–1 当正片（密度本身近似对数曝光，看着自然），再加一点 S 曲线提对比。
黑白负片先转灰度再算，避免各通道分别拉伸带来偏色。统计量在缩小的图上算，预览和原尺寸结果一致。
"""
from __future__ import annotations

import numpy as np

from ..i18n import t as _t

MODES = [("color", _t("彩色负片")), ("bw", _t("黑白负片"))]


def to_linear(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def density(rgb: np.ndarray) -> np.ndarray:
    return (-np.log10(np.maximum(to_linear(rgb), 1e-4))).astype(np.float32)


def _sample(rgb: np.ndarray, side: int = 512) -> np.ndarray:
    h, w = rgb.shape[:2]
    step = max(1, int(max(h, w) / side))
    y0, y1, x0, x1 = int(h * 0.05), max(int(h * 0.95), int(h * 0.05) + 1), int(w * 0.05), max(int(w * 0.95), int(w * 0.05) + 1)
    return rgb[y0:y1:step, x0:x1:step]


def stats(rgb: np.ndarray, mode: str = "color") -> dict:
    """{"lo": [r,g,b], "hi": [r,g,b]}：每个通道的最低 / 最高密度（黑白负片三个通道相同）。"""
    d = density(_sample(rgb[..., :3]))
    if mode == "bw":
        d = np.repeat(d.mean(axis=2, keepdims=True), 3, axis=2)
    flat = d.reshape(-1, 3)
    lo = np.percentile(flat, 0.5, axis=0)
    hi = np.percentile(flat, 99.5, axis=0)
    hi = np.maximum(hi, lo + 0.05)
    return {"lo": [float(v) for v in lo], "hi": [float(v) for v in hi]}


def invert(rgb: np.ndarray, mode: str = "color", st: dict | None = None, contrast: float = 0.25) -> np.ndarray:
    """float32 RGB（0–1，sRGB）→ 反转后的正片（float32 RGB 0–1）。st 不给时按这张图自己算。"""
    src = rgb[..., :3]
    st = st or stats(src, mode)
    d = density(src)
    if mode == "bw":
        d = np.repeat(d.mean(axis=2, keepdims=True), 3, axis=2)
    lo = np.asarray(st["lo"], np.float32)
    hi = np.asarray(st["hi"], np.float32)
    p = np.clip((d - lo) / (hi - lo), 0.0, 1.0)
    if contrast:
        p = p + float(contrast) * ((p * p * (3.0 - 2.0 * p)) - p)
    return p.astype(np.float32)


def filter_fn(rgba: np.ndarray, scale: float, mode: str = "color", contrast: float = 25.0) -> np.ndarray:
    """修图窗口的滤镜（图像 → 滤镜 → 其他 → 负片反转）：RGBA 进出，透明度不变。"""
    out = rgba.copy()
    out[..., :3] = invert(rgba[..., :3], mode, None, float(contrast) / 100.0)
    return out


def _specs() -> dict:
    from .filters import FilterSpec
    return {"negative": FilterSpec(_t("负片反转"), filter_fn, [("contrast", _t("对比度"), 0, 100, 25, 0, "")],
                                   whole=True, choices=[("mode", _t("底片"), MODES, "color")])}
