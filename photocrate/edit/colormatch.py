"""统一色调：把一组照片的颜色往一张参考照片靠（Reinhard 的 Lab 均值 / 标准差迁移）。

参考照片只需要它的 Lab 统计量（6 个数），所以可以存进「复制编辑」里粘贴到别的照片。
强度 0–100：0 不变，100 完全对齐参考；亮度（L）的对比只迁移一部分并限幅，免得把暗夜照片硬拉成白天。
"""
from __future__ import annotations

import numpy as np

L_STD_RANGE = (0.75, 1.35)      # 亮度对比最多改这么多
AB_STD_RANGE = (0.5, 2.0)


def _sample(rgb: np.ndarray, side: int = 384) -> np.ndarray:
    h, w = rgb.shape[:2]
    step = max(1, int(max(h, w) / side))
    return np.ascontiguousarray(rgb[::step, ::step, :3])


def to_lab(rgb: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(np.clip(rgb[..., :3], 0, 1).astype(np.float32), cv2.COLOR_RGB2Lab)


def lab_stats(rgb: np.ndarray) -> dict:
    """{"mean": [L, a, b], "std": [L, a, b]}（在缩小的图上算）。"""
    lab = to_lab(_sample(rgb)).reshape(-1, 3)
    return {"mean": [float(v) for v in lab.mean(axis=0)], "std": [float(max(v, 1e-3)) for v in lab.std(axis=0)]}


def transfer(rgb: np.ndarray, ref: dict, amount: float = 0.7, src: dict | None = None) -> np.ndarray:
    """float32 RGB 0–1 → 颜色往 ref（lab_stats 的结果）靠之后的 float32 RGB。src 不给时按这张图自己算。"""
    import cv2
    amount = float(np.clip(amount, 0.0, 1.0))
    if amount <= 0:
        return rgb[..., :3].astype(np.float32, copy=True)
    src = src or lab_stats(rgb)
    lab = to_lab(rgb)
    m_s, s_s = np.asarray(src["mean"], np.float32), np.asarray(src["std"], np.float32)
    m_r, s_r = np.asarray(ref["mean"], np.float32), np.asarray(ref["std"], np.float32)
    ratio = s_r / np.maximum(s_s, 1e-3)
    ratio[0] = np.clip(1.0 + (ratio[0] - 1.0) * 0.6, *L_STD_RANGE)
    ratio[1:] = np.clip(ratio[1:], *AB_STD_RANGE)
    target = (lab - m_s) * ratio + m_r
    out = lab + (target - lab) * amount
    out[..., 0] = np.clip(out[..., 0], 0, 100)
    return np.clip(cv2.cvtColor(out.astype(np.float32), cv2.COLOR_Lab2RGB), 0, 1).astype(np.float32)
