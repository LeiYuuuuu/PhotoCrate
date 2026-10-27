"""看一眼当前照片：亮度分布、溢出、对比、饱和度、偏色。给调色建议收放幅度、做“这张怎么修”的诊断用。

只用 numpy，缩到长边 ≤ 512 像素再算，几毫秒。偏色按“近中性灰的像素应该是灰的”估计，
换算成调整图层的色温 / 色调值（和 adjust.develop 的白平衡增益同一套公式），只当参考。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PhotoStats:
    mean: float          # 平均亮度（sRGB，0–1）
    p1: float            # 1% 分位亮度：最暗处到哪
    p99: float           # 99% 分位亮度：最亮处到哪
    hi_clip: float       # 接近纯白的像素比例
    lo_clip: float       # 接近纯黑的像素比例
    sat: float           # 平均饱和度（HSV S）
    temp_fix: float      # 把中性灰校正回灰需要的「色温」值（负 = 照片偏暖）
    tint_fix: float      # 需要的「色调」值（正 = 照片偏绿）
    neutral: float       # 用来估计偏色的近中性像素占比（太少就不判断偏色）

    @property
    def spread(self) -> float:
        return self.p99 - self.p1

    @property
    def mono(self) -> bool:
        return self.sat < 0.03


def analyze(img: np.ndarray) -> PhotoStats:
    """img：H×W×3 / H×W×4，uint8 / uint16 / float（0–1）。"""
    a = np.asarray(img)
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, axis=2)
    alpha = None
    if a.shape[2] == 4:
        alpha = a[..., 3]
    a = a[..., :3]
    step = max(1, int(max(a.shape[:2]) / 512))
    a = a[::step, ::step]
    if alpha is not None:
        alpha = alpha[::step, ::step]
    if a.dtype == np.uint8:
        x = a.astype(np.float32) / 255
    elif a.dtype == np.uint16:
        x = a.astype(np.float32) / 65535
    else:
        x = np.clip(a.astype(np.float32), 0, 1)
    x = x.reshape(-1, 3)
    if alpha is not None:
        keep = alpha.reshape(-1) > (127 if alpha.dtype == np.uint8 else 0.5 if alpha.dtype.kind == "f" else 32767)
        if keep.sum() > 64:
            x = x[keep]
    L = x @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    mx, mn = x.max(1), x.min(1)
    s = np.where(mx > 1e-4, (mx - mn) / np.maximum(mx, 1e-4), 0)
    p1, p99 = (float(v) for v in np.percentile(L, [1, 99]))

    temp_fix = tint_fix = 0.0
    nm = (s < 0.25) & (L > 0.2) & (L < 0.85)
    neutral = float(nm.mean()) if len(nm) else 0.0
    if nm.sum() > 200:
        lin = x[nm].astype(np.float64) ** 2.2
        r, g, b = (float(v) for v in lin.mean(0))
        if r > 1e-4 and b > 1e-4 and g > 1e-4:
            k = b / r                                   # 想要 (1+0.28t)/(1-0.28t) = b/r
            temp_fix = (k - 1) / (0.28 * (k + 1)) * 100
            rb = (r * (1 + 0.28 * temp_fix / 100) + b * (1 - 0.28 * temp_fix / 100)) / 2
            tint_fix = (1 - rb / g) / 0.22 * 100       # 想要 g·(1−0.22t) = 校正后的 (r+b)/2
    return PhotoStats(float(L.mean()), p1, p99, float((L > 0.985).mean()), float((L < 0.015).mean()),
                      float(s.mean()), float(np.clip(temp_fix, -100, 100)), float(np.clip(tint_fix, -100, 100)),
                      neutral)
