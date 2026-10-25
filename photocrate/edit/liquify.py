"""液化：向前变形 / 重建 / 顺时针、逆时针旋转扭曲 / 褶皱 / 膨胀。

在缩小的预览图（长边约 1400）上维护一张位移场 D（每个像素：从源图的哪里取，相对位移，单位 = 预览像素）。
每一笔把新的局部变换和已有的位移场复合：D'(x) = D(x + δ(x)) + δ(x)（δ 是这一笔在 x 处的取样偏移，
用 remap 对 D 本身取样，所以来回推不会越推越糊）。应用时把位移场放大到原尺寸，一次 remap 原图——
只重新取样一次，清晰度只损失一次插值。
"""
from __future__ import annotations

import math

import numpy as np

TOOLS = ["push", "reconstruct", "twirl_cw", "twirl_ccw", "pucker", "bloat"]


class Liquify:
    def __init__(self, rgba: np.ndarray, max_side: int = 1400):
        import cv2
        from .document import to_float
        self.full = rgba
        H, W = rgba.shape[:2]
        self.s = min(1.0, max_side / max(H, W))
        self.w, self.h = max(1, int(round(W * self.s))), max(1, int(round(H * self.s)))
        f = to_float(rgba)
        if self.s < 1:
            f = cv2.resize(f, (self.w, self.h), interpolation=cv2.INTER_AREA)
        self.small = np.ascontiguousarray(f)
        yy, xx = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        self.gx, self.gy = xx, yy
        self.D = np.zeros((self.h, self.w, 2), np.float32)
        self.history: list[np.ndarray] = []

    # ---- 笔触 ----
    def begin(self) -> None:
        self.history.append(self.D.copy())
        del self.history[:-30]

    def undo(self) -> bool:
        if not self.history:
            return False
        self.D = self.history.pop()
        return True

    def reset(self) -> None:
        self.begin()
        self.D[...] = 0

    def _brush(self, cx, cy, r):
        x0, y0 = max(0, int(cx - r) - 1), max(0, int(cy - r) - 1)
        x1, y1 = min(self.w, int(cx + r) + 2), min(self.h, int(cy + r) + 2)
        if x0 >= x1 or y0 >= y1:
            return None
        xs, ys = self.gx[y0:y1, x0:x1], self.gy[y0:y1, x0:x1]
        d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max(0.5, r)
        w = np.clip(1 - d, 0, 1)
        w = w * w * (3 - 2 * w)                           # 平滑衰减
        return (x0, y0, x1, y1), xs, ys, w

    def dab(self, tool: str, cx: float, cy: float, size: float, pressure: float = 0.5, dx: float = 0.0,
            dy: float = 0.0) -> tuple[int, int, int, int] | None:
        """在预览坐标 (cx, cy) 处按一下（size = 笔刷直径，预览像素）。push 用 (dx, dy) 表示这一下拖了多远。"""
        import cv2
        r = size / 2
        b = self._brush(cx, cy, r)
        if b is None:
            return None
        (x0, y0, x1, y1), xs, ys, w = b
        p = float(np.clip(pressure, 0.01, 1))
        if tool == "reconstruct":
            self.D[y0:y1, x0:x1] *= (1 - p * 0.35 * w)[..., None]
            return x0, y0, x1, y1
        if tool == "push":
            ox, oy = -dx * w * p * 1.2, -dy * w * p * 1.2                  # 取样点往回挪 = 画面往前推
        elif tool in ("bloat", "pucker"):
            k = (0.06 if tool == "bloat" else -0.06) * p * w
            ox, oy = -(xs - cx) * k, -(ys - cy) * k
        else:
            a = (0.08 if tool == "twirl_cw" else -0.08) * p * w
            c, s = np.cos(-a), np.sin(-a)
            rx, ry = xs - cx, ys - cy
            ox, oy = rx * c - ry * s - rx, rx * s + ry * c - ry
        if not (np.abs(ox).max() > 1e-4 or np.abs(oy).max() > 1e-4):
            return None
        mx = (xs + ox).astype(np.float32)
        my = (ys + oy).astype(np.float32)
        old = cv2.remap(self.D, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        self.D[y0:y1, x0:x1, 0] = old[..., 0] + ox
        self.D[y0:y1, x0:x1, 1] = old[..., 1] + oy
        return x0, y0, x1, y1

    def stroke_to(self, tool, x0, y0, x1, y1, size, pressure):
        """从 (x0, y0) 拖到 (x1, y1)：按笔刷大小的 1/4 一步插值。"""
        L = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(L / max(1.0, size * 0.12)))
        for i in range(1, n + 1):
            t0, t1 = (i - 1) / n, i / n
            ax, ay = x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0
            bx, by = x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1
            self.dab(tool, bx, by, size, pressure, bx - ax, by - ay)

    # ---- 出图 ----
    def preview(self) -> np.ndarray:
        import cv2
        mx = self.gx + self.D[..., 0]
        my = self.gy + self.D[..., 1]
        return cv2.remap(self.small, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    def changed(self) -> bool:
        return bool(np.abs(self.D).max() > 1e-3)

    def apply(self) -> np.ndarray:
        """按原尺寸应用：位移场放大到原图大小（数值乘以倍数），分条 remap 原图。"""
        import cv2
        H, W = self.full.shape[:2]
        k = 1 / self.s
        out = np.empty_like(self.full)
        step = 1024
        for y0 in range(0, H, step):
            y1 = min(H, y0 + step)
            ys = (np.arange(y0, y1, dtype=np.float32) + 0.5) * self.s - 0.5
            xs = (np.arange(W, dtype=np.float32) + 0.5) * self.s - 0.5
            mxs, mys = np.meshgrid(xs, ys)
            d = cv2.remap(self.D, mxs, mys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(y0, y1, dtype=np.float32))
            out[y0:y1] = cv2.remap(self.full, gx + d[..., 0] * k, gy + d[..., 1] * k, cv2.INTER_CUBIC,
                                   borderMode=cv2.BORDER_REPLICATE)
        return out
