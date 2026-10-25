"""「相册」里的基础编辑（类似 iOS 照片的「编辑」）：自动增强、调整滑块、滤镜（可调强度）、旋转 / 翻转 / 拉直 / 裁剪。

只是一组参数（QuickState），像素计算全部交给 adjust.develop，与修图窗口的调整图层是同一套算法。
预览在缩小的图上算（RenderCtx.scale 让半径类效果看起来一致），保存时在原尺寸上再算一次，写成新文件——原片只读。
处理顺序：转 90° / 翻转 → 拉直（自动放大到没有空角）→ 裁剪 → 调整。
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import numpy as np

from . import adjust
from ..i18n import t as _t

# 编辑面板的滑块：(键, 名称)；范围用 adjust.SLIDERS 里的
LIGHT = [("exposure", "曝光"), ("highlights", "高光"), ("shadows", "阴影"), ("contrast", "对比度"),
         ("blacks", "黑色色阶"), ("whites", "白色色阶")]
COLOR = [("saturation", "饱和度"), ("vibrance", "自然饱和度"), ("temp", "色温"), ("tint", "色调")]
DETAIL = [("clarity", "清晰度"), ("sharpen", "锐化"), ("denoise", "降噪"), ("vignette", "暗角")]
SLIDER_KEYS = [k for k, _ in LIGHT + COLOR + DETAIL]

ASPECTS = [("free", "自由", None), ("orig", "原始比例", 0.0), ("1:1", "正方形", 1.0), ("4:3", "4:3", 4 / 3),
           ("3:2", "3:2", 3 / 2), ("16:9", "16:9", 16 / 9), ("3:4", "3:4", 3 / 4), ("9:16", "9:16", 9 / 16)]


@dataclass
class QuickState:
    sliders: dict = field(default_factory=dict)       # 键 → 数值（没有的键 = 0）
    auto: dict = field(default_factory=dict)          # 自动增强算出来的参数（叠加在滑块上；空 = 没开）
    preset: str | None = None
    amount: float = 100.0                             # 滤镜强度 0–100
    rot: int = 0                                      # 顺时针转几个 90°
    flip: bool = False                                # 水平翻转
    angle: float = 0.0                                # 拉直角度（度，顺时针为正）
    crop: tuple = (0.0, 0.0, 1.0, 1.0)                # 在“转正、拉直之后”的图上的裁剪框（0–1）

    def copy(self) -> "QuickState":
        return copy.deepcopy(self)

    def is_identity(self) -> bool:
        return (not any(abs(float(v)) > 1e-6 for v in self.sliders.values()) and not self.auto
                and (self.preset is None or self.amount <= 0) and self.rot % 4 == 0 and not self.flip
                and abs(self.angle) < 1e-3 and tuple(self.crop) == (0.0, 0.0, 1.0, 1.0))

    def geometry_changed(self) -> bool:
        return self.rot % 4 != 0 or self.flip or abs(self.angle) > 1e-3 or tuple(self.crop) != (0.0, 0.0, 1.0, 1.0)

    def params(self) -> dict:
        """合并成一份 adjust 参数：滤镜按强度缩放，再加上自动增强与滑块。"""
        p = adjust.default_params()
        if self.preset and self.amount > 0:
            p = blend_preset(self.preset, self.amount / 100.0)
        for src in (self.auto, self.sliders):
            for k, v in src.items():
                lo, hi = adjust.SLIDERS[k][1:3]
                p[k] = float(np.clip(float(p.get(k, 0.0)) + float(v), lo, hi))
        return p


def blend_preset(name: str, amount: float) -> dict:
    """滤镜按强度 amount（0–1）与“不调整”插值：数值、曲线（偏离对角线的部分）、HSL 都按比例缩放。"""
    d = adjust.default_params()
    pr = adjust.preset(name)
    out = copy.deepcopy(d)
    for k in adjust.SLIDERS:
        out[k] = float(d[k]) + (float(pr.get(k, d[k])) - float(d[k])) * amount
    for ch, pts in (pr.get("curve") or {}).items():
        out["curve"][ch] = [[float(x), float(x) + (float(y) - float(x)) * amount] for x, y in pts]
    for band, v in (pr.get("hsl") or {}).items():
        out["hsl"][band] = [float(x) * amount for x in v]
    out["invert"] = bool(pr.get("invert")) and amount >= 0.5
    return out


# ---------------------------------------------------------------------------
# 自动增强
# ---------------------------------------------------------------------------

def auto_params(rgb: np.ndarray) -> dict:
    """看直方图给一组温和的参数：中间调偏暗就提曝光，高光溢出就压高光，暗部堆积就提阴影，再加一点自然饱和度。"""
    small = rgb[:: max(1, rgb.shape[0] // 400), :: max(1, rgb.shape[1] // 400)]
    luma = small[..., 0] * 0.2126 + small[..., 1] * 0.7152 + small[..., 2] * 0.0722
    med = float(np.median(luma))
    p02, p98 = (float(v) for v in np.percentile(luma, (2, 98)))
    hi = float((luma > 0.97).mean())
    lo = float((luma < 0.04).mean())
    out = {}
    ev = math.log2(0.45 / max(0.02, med)) * 0.55
    if abs(ev) > 0.08:
        out["exposure"] = round(float(np.clip(ev, -1.2, 1.2)), 2)
    if hi > 0.004 or p98 > 0.95:
        out["highlights"] = -round(min(55.0, 15 + hi * 900), 0)
    if lo > 0.01 or p02 < 0.03:
        out["shadows"] = round(min(45.0, 12 + lo * 300), 0)
    spread = p98 - p02
    if spread < 0.6 and 0.2 < med < 0.75:                  # 偏暗 / 偏亮的照片先靠曝光，加对比度会把它压回去
        out["contrast"] = round(min(25.0, (0.6 - spread) * 60), 0)
    sat = small.max(axis=2) - small.min(axis=2)
    if float(sat.mean()) < 0.25:
        out["vibrance"] = 18.0
    return out or {"vibrance": 10.0}


# ---------------------------------------------------------------------------
# 几何
# ---------------------------------------------------------------------------

def straighten_scale(w: int, h: int, angle: float) -> float:
    """转 angle 度后放大多少倍，才能让原尺寸的框里没有空角。"""
    a = math.radians(abs(angle))
    c, s = math.cos(a), math.sin(a)
    return max((w * c + h * s) / w, (w * s + h * c) / h)


def oriented(rgb: np.ndarray, st: QuickState) -> np.ndarray:
    """转 90° / 翻转 / 拉直之后、裁剪之前的图（与原图同类型）。"""
    import cv2
    out = np.rot90(rgb, -(st.rot % 4)) if st.rot % 4 else rgb
    if st.flip:
        out = out[:, ::-1]
    if abs(st.angle) > 1e-3:
        h, w = out.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), -st.angle, straighten_scale(w, h, st.angle))
        out = cv2.warpAffine(np.ascontiguousarray(out), M, (w, h), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REFLECT)
    return np.ascontiguousarray(out)


def crop_box(w: int, h: int, crop) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = crop
    l, t = int(round(x0 * w)), int(round(y0 * h))
    r, b = max(l + 1, int(round(x1 * w))), max(t + 1, int(round(y1 * h)))
    return l, t, min(w, r), min(h, b)


def apply_geometry(rgb: np.ndarray, st: QuickState) -> np.ndarray:
    out = oriented(rgb, st)
    h, w = out.shape[:2]
    l, t, r, b = crop_box(w, h, st.crop)
    return np.ascontiguousarray(out[t:b, l:r])


def apply(rgb: np.ndarray, st: QuickState, scale: float = 1.0, geometry: bool = True) -> np.ndarray:
    """float32 RGB（0–1）→ 编辑后的 float32 RGB。scale = 这张图相对原尺寸的比例（预览时 < 1）。"""
    img = apply_geometry(rgb, st) if geometry else rgb
    p = st.params()
    if adjust.is_identity(p):
        return img
    h, w = img.shape[:2]
    rc = adjust.RenderCtx(scale=scale, cw=int(round(w / max(scale, 1e-6))), ch=int(round(h / max(scale, 1e-6))))
    return adjust.develop(img, p, rc)


def to_float(arr: np.ndarray) -> np.ndarray:
    """uint8 / uint16 的 RGB(A) → float32 RGB 0–1。"""
    a = arr[..., :3]
    if a.dtype == np.uint8:
        return a.astype(np.float32) / 255.0
    if a.dtype == np.uint16:
        return a.astype(np.float32) / 65535.0
    return a.astype(np.float32)


def to_rgba8(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    out = np.empty((h, w, 4), np.uint8)
    out[..., :3] = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return out


def describe(st: QuickState) -> str:
    """一句话说明改了什么（状态栏、保存提示用）。"""
    parts = []
    if st.auto:
        parts.append(_t("自动增强"))
    if st.preset and st.amount > 0:
        parts.append(_t("滤镜 {0} {1:.0f}%", _t(st.preset), st.amount))
    n = sum(1 for v in st.sliders.values() if abs(float(v)) > 1e-6)
    if n:
        parts.append(_t("{0} 项调整", n))
    if st.geometry_changed():
        parts.append(_t("裁剪 / 旋转"))
    return _t("、").join(parts) or _t("没有修改")
