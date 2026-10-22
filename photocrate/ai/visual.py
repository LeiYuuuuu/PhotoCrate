"""画面相似度（不用 AI）：颜色布局 + 颜色分布，给“找相似”补上“看起来像不像”这一半。

CLIP 向量只懂“内容”：同样是“树下的小路”，白天和夜里、顺光和逆光，它都认为很像。
人眼说的“相似”还包括颜色、明暗和构图，所以每张照片再存一个很小的画面指纹：
- 布局：缩到 8×8，每格的亮度 + 两个色差（YCbCr），192 维；
- 颜色：RGB 各分 4 档的 64 格直方图。
从缩略图计算，每张约 1 毫秒；和 CLIP 向量一样按主图指纹缓存在 cache.db 的 emb 表。
"""
from __future__ import annotations

VIS_KEY = "vis-v1"
GRID = 8
DIM = GRID * GRID * 3 + 64


def descriptor(img) -> "np.ndarray":
    """PIL 图像 → 256 维 float32 画面指纹（前 192 维布局，0–1；后 64 维颜色直方图，和为 1）。"""
    import numpy as np
    small = img.convert("RGB").resize((32, 32))
    ycc = np.asarray(small.convert("YCbCr").resize((GRID, GRID)), dtype=np.float32) / 255.0
    rgb = np.asarray(small, dtype=np.int32) // 64                      # 每个通道 4 档
    idx = (rgb[..., 0] * 16 + rgb[..., 1] * 4 + rgb[..., 2]).ravel()
    hist = np.bincount(idx, minlength=64).astype(np.float32)
    hist /= max(1.0, float(hist.sum()))
    return np.concatenate([ycc.ravel(), hist]).astype(np.float32)


def similarity(q: "np.ndarray", M: "np.ndarray") -> "np.ndarray":
    """一张照片与 N 张照片的画面相似度（0–1，越大越像）。"""
    import numpy as np
    n = GRID * GRID * 3
    lq, lm = q[:n].reshape(GRID * GRID, 3), M[:, :n].reshape(len(M), GRID * GRID, 3)
    w = np.array([2.0, 1.0, 1.0], dtype=np.float32)                     # 亮度比色差更重要
    d = (np.abs(lm - lq) * w).sum(axis=2).mean(axis=1) / w.sum()        # 每格平均差，0–1
    layout = np.clip(1.0 - d * 4.0, 0.0, 1.0)                           # 平均差 ≥ 0.25 视为完全不像
    hist = np.minimum(M[:, n:], q[n:]).sum(axis=1)                      # 直方图交集，0–1
    return 0.6 * layout + 0.4 * hist


def hash_similarity(a: str | None, b: str | None) -> float | None:
    """两个 64 位 pHash 的相似度（0–1）；随机两张约 0.5。"""
    if not a or not b:
        return None
    try:
        return 1.0 - bin(int(a, 16) ^ int(b, 16)).count("1") / 64.0
    except ValueError:
        return None
