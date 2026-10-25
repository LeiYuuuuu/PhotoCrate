"""更多滤镜：镜头校正、表面模糊、镜头模糊，以及扭曲类（旋转扭曲、波浪、波纹、球面化、挤压、极坐标）。

和 filters.py 一样：fn(rgba, scale, **参数) → rgba（float32 0–1、不预乘）。扭曲类和镜头校正要按整个图层的
几何中心算，所以标成 whole（原尺寸应用时不分条）；坐标一律按“相对图层宽高”的比例算，在缩小的代理图上预览和
原尺寸应用的结果一样。
"""
from __future__ import annotations

import math

import numpy as np

from ..i18n import t as _t
from .filters import FilterSpec, _premul, _unpremul


def _remap(rgba, mx, my):
    import cv2
    out = cv2.remap(_premul(rgba), mx.astype(np.float32), my.astype(np.float32), cv2.INTER_CUBIC,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    return _unpremul(np.clip(out, 0, None))


def _grid(rgba):
    h, w = rgba.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = (w - 1) / 2, (h - 1) / 2
    return xx, yy, cx, cy, w, h


# ---------------------------------------------------------------------------
# 镜头校正
# ---------------------------------------------------------------------------

def lens_correct(rgba, scale, distortion=0.0, ca_red=0.0, ca_blue=0.0, vignette=0.0, midpoint=50.0, fill=True):
    """几何扭曲（+ 校正桶形、− 校正枕形）、横向色差（红 / 蓝通道按半径缩放）、暗角（+ 提亮四角）。"""
    xx, yy, cx, cy, w, h = _grid(rgba)
    R = math.hypot(cx, cy) or 1.0
    nx, ny = (xx - cx) / R, (yy - cy) / R
    r2 = nx * nx + ny * ny
    k = -float(distortion) / 100 * 0.35
    out = rgba
    if abs(k) > 1e-6:
        f = 1 + k * r2                                     # 输出半径 r 取源图 r·(1 + k·r²)
        if fill and k > 0:                                 # 校正枕形会往外取样：整体缩一点，四角不露出透明
            f = f / (1 + k)
        out = _remap(out, cx + nx * f * R, cy + ny * f * R)
    if ca_red or ca_blue:
        res = out.copy()
        for ch, amt in ((0, ca_red), (2, ca_blue)):
            if not amt:
                continue
            s = 1 + float(amt) / 100 * 0.01
            import cv2
            mx = (cx + (xx - cx) * s).astype(np.float32)
            my = (cy + (yy - cy) * s).astype(np.float32)
            res[..., ch] = cv2.remap(out[..., ch], mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        out = np.clip(res, 0, 1)
    if vignette:
        mp = max(0.05, float(midpoint) / 100)
        rr = np.sqrt(r2)
        m = np.clip((rr - (1 - mp)) / max(1e-3, mp), 0, 1)
        m = m * m * (3 - 2 * m)
        v = float(vignette) / 100
        out = out.copy()
        if v > 0:
            out[..., :3] = out[..., :3] * (1 + v * 1.2 * m[..., None])
        else:
            out[..., :3] = out[..., :3] * (1 + v * 0.95 * m[..., None])
    return out


# ---------------------------------------------------------------------------
# 模糊
# ---------------------------------------------------------------------------

def surface_blur(rgba, scale, radius=5.0, threshold=15.0):
    """表面模糊：只在颜色相近的地方模糊，保留边缘（磨皮、去掉平滑区域的噪点）。"""
    import cv2
    d = int(max(3, round(float(radius) * scale * 2))) | 1
    rgb = np.ascontiguousarray(rgba[..., :3], dtype=np.float32)
    flt = rgb
    for _ in range(2):
        flt = cv2.bilateralFilter(flt, d, float(threshold) / 255 * 2.2, max(1.0, float(radius) * scale))
    out = rgba.copy()
    out[..., :3] = flt
    return out


def _disk(r: float) -> np.ndarray:
    n = int(math.ceil(r))
    yy, xx = np.mgrid[-n:n + 1, -n:n + 1].astype(np.float32)
    d = np.sqrt(xx * xx + yy * yy)
    k = np.clip(r + 0.5 - d, 0, 1)
    return k / k.sum()


def lens_blur(rgba, scale, radius=15.0, bright=30.0, threshold=230.0, noise=0.0):
    """镜头模糊：圆形光圈的散景（亮点会变成光斑），亮部先按“镜面高光”放大亮度再模糊。"""
    import cv2
    r = float(radius) * scale
    if r < 0.6:
        return rgba
    pm = _premul(rgba)
    lin = pm.copy()
    lin[..., :3] = np.power(np.clip(pm[..., :3], 0, 1), 2.2)
    if bright:
        L = rgba[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32)
        t = float(threshold) / 255
        boost = 1 + float(bright) / 100 * 8 * np.clip((L - t) / max(1e-3, 1 - t), 0, 1) ** 2
        lin[..., :3] *= boost[..., None]
    k = _disk(r)
    bl = cv2.filter2D(lin, -1, k, borderType=cv2.BORDER_REFLECT)
    bl[..., :3] = np.power(np.clip(bl[..., :3], 0, None), 1 / 2.2)
    out = _unpremul(np.clip(bl, 0, None))
    if noise:
        rng = np.random.default_rng(77)
        out[..., :3] = out[..., :3] + rng.standard_normal(out.shape[:2] + (1,)).astype(np.float32) * float(noise) / 400
    return out


# ---------------------------------------------------------------------------
# 扭曲
# ---------------------------------------------------------------------------

def twirl(rgba, scale, angle=90.0):
    xx, yy, cx, cy, w, h = _grid(rgba)
    R = min(cx, cy) or 1.0
    dx, dy = xx - cx, yy - cy
    r = np.sqrt(dx * dx + dy * dy) / R
    a = np.radians(float(angle)) * np.clip(1 - r, 0, 1) ** 2
    c, s = np.cos(a), np.sin(a)
    return _remap(rgba, cx + dx * c - dy * s, cy + dx * s + dy * c)


def wave(rgba, scale, amplitude=10.0, wavelength=120.0, kind="sine"):
    xx, yy, cx, cy, w, h = _grid(rgba)
    A = float(amplitude) * scale
    L = max(2.0, float(wavelength) * scale)
    ph_x, ph_y = 2 * np.pi * yy / L, 2 * np.pi * xx / L
    if kind == "triangle":
        f = lambda t: 2 / np.pi * np.arcsin(np.sin(t))          # noqa: E731
    elif kind == "square":
        f = lambda t: np.sign(np.sin(t))                          # noqa: E731
    else:
        f = np.sin
    return _remap(rgba, xx + A * f(ph_x), yy + A * f(ph_y))


def ripple(rgba, scale, amplitude=8.0, wavelength=40.0):
    """水波纹（从中心向外的同心波）。"""
    xx, yy, cx, cy, w, h = _grid(rgba)
    dx, dy = xx - cx, yy - cy
    r = np.sqrt(dx * dx + dy * dy) + 1e-6
    L = max(2.0, float(wavelength) * scale)
    d = float(amplitude) * scale * np.sin(2 * np.pi * r / L)
    return _remap(rgba, xx + dx / r * d, yy + dy / r * d)


def spherize(rgba, scale, amount=60.0):
    """球面化（+ 凸起、− 凹陷），作用在图层内切的椭圆里。"""
    xx, yy, cx, cy, w, h = _grid(rgba)
    nx, ny = (xx - cx) / max(1.0, cx), (yy - cy) / max(1.0, cy)
    r = np.sqrt(nx * nx + ny * ny)
    a = float(amount) / 100
    inside = r < 1
    rr = np.clip(r, 1e-6, 1)
    if a >= 0:
        src = np.arcsin(rr) / (np.pi / 2)                       # 凸：中心放大
    else:
        src = np.sin(rr * np.pi / 2)                            # 凹
    f = np.where(inside, (rr + (src - rr) * abs(a)) / rr, 1.0)
    return _remap(rgba, cx + nx * f * cx, cy + ny * f * cy)


def pinch(rgba, scale, amount=50.0):
    """挤压（+ 向中心收、− 向外鼓）。"""
    xx, yy, cx, cy, w, h = _grid(rgba)
    nx, ny = (xx - cx) / max(1.0, cx), (yy - cy) / max(1.0, cy)
    r = np.sqrt(nx * nx + ny * ny)
    a = float(amount) / 100
    rr = np.clip(r, 1e-6, 1)
    f = np.where(r < 1, np.power(rr, 1 - a * 0.5) / rr, 1.0)        # 正：从更远处取样 = 画面往中心收
    f = np.where(r < 1, 1 + (f - 1) * np.sin(np.clip(1 - r, 0, 1) * np.pi / 2), 1.0)
    return _remap(rgba, cx + nx * f * cx, cy + ny * f * cy)


def polar(rgba, scale, mode="to_polar"):
    """极坐标：平面 → 极坐标（把一张全景卷成“小行星”）或反过来。"""
    xx, yy, cx, cy, w, h = _grid(rgba)
    if mode == "to_polar":
        dx, dy = (xx - cx) / max(1.0, cx), (yy - cy) / max(1.0, cy)
        r = np.sqrt(dx * dx + dy * dy)
        th = (np.arctan2(dx, -dy) + np.pi) / (2 * np.pi)       # 0..1，从正上方顺时针
        mx = th * (w - 1)
        my = r * (h - 1)
        out = _remap(rgba, mx, my)
        out[r > 1.0001] = 0
        return out
    th = xx / max(1.0, w - 1) * 2 * np.pi - np.pi
    r = yy / max(1.0, h - 1)
    mx = cx + np.sin(th) * r * cx
    my = cy - np.cos(th) * r * cy
    return _remap(rgba, mx, my)


def skin_smooth(rgba, scale, amount=50.0, detail=40.0):
    """皮肤平滑（人像）：只在肤色区域里做保边磨皮，再按“保留细节”加回一部分细纹理，不会像塑料。"""
    import cv2
    from .select import color_range
    rgb8 = np.clip(rgba[..., :3] * 255 + 0.5, 0, 255).astype(np.uint8)
    m = color_range(rgb8, "skin").astype(np.float32) / 255
    m = cv2.GaussianBlur(m, (0, 0), max(1.0, 3 * scale))
    rgb = np.ascontiguousarray(rgba[..., :3], dtype=np.float32)
    r = max(1.0, 7 * scale)
    sm = cv2.bilateralFilter(rgb, int(r * 2) | 1, 0.08, r)
    sm = cv2.bilateralFilter(sm, int(r * 2) | 1, 0.06, r)
    fine = rgb - cv2.GaussianBlur(rgb, (0, 0), max(0.6, 0.8 * scale))
    target = sm + fine * (float(detail) / 100)
    out = rgba.copy()
    out[..., :3] = rgb + (target - rgb) * (m * float(amount) / 100)[..., None]
    return out


def _r(key):
    return lambda p: float(p.get(key, 0) or 0) * 2 + 12


SPECS = {
    "lens": FilterSpec(_t("镜头校正"), lens_correct,
                       [("distortion", _t("移去扭曲"), -100, 100, 0, 0, ""),
                        ("ca_red", _t("修复红 / 青边"), -100, 100, 0, 0, ""),
                        ("ca_blue", _t("修复蓝 / 黄边"), -100, 100, 0, 0, ""),
                        ("vignette", _t("暗角数量"), -100, 100, 0, 0, ""),
                        ("midpoint", _t("暗角中点"), 5, 100, 50, 0, "")],
                       [("fill", _t("自动缩放（不露出透明的边）"), True)], whole=True),
    "surface": FilterSpec(_t("表面模糊"), surface_blur, [("radius", _t("半径"), 1, 100, 5, 0, " px"),
                                                     ("threshold", _t("阈值"), 2, 255, 15, 0, "")],
                          margin=_r("radius")),
    "lensblur": FilterSpec(_t("镜头模糊"), lens_blur, [("radius", _t("半径"), 1, 100, 15, 0, " px"),
                                                   ("bright", _t("镜面高光亮度"), 0, 100, 30, 0, ""),
                                                   ("threshold", _t("镜面高光阈值"), 128, 255, 230, 0, ""),
                                                   ("noise", _t("杂色"), 0, 100, 0, 0, "")],
                           margin=_r("radius")),
    "twirl": FilterSpec(_t("旋转扭曲"), twirl, [("angle", _t("角度"), -999, 999, 90, 0, "°")], whole=True),
    "wave": FilterSpec(_t("波浪"), wave, [("amplitude", _t("波幅"), 1, 200, 10, 0, " px"),
                                        ("wavelength", _t("波长"), 4, 999, 120, 0, " px")], whole=True,
                       choices=[("kind", _t("类型"), [("sine", _t("正弦")), ("triangle", _t("三角形")),
                                                     ("square", _t("方形"))], "sine")]),
    "ripple": FilterSpec(_t("水波"), ripple, [("amplitude", _t("波幅"), 1, 100, 8, 0, " px"),
                                            ("wavelength", _t("波长"), 4, 400, 40, 0, " px")], whole=True),
    "spherize": FilterSpec(_t("球面化"), spherize, [("amount", _t("数量"), -100, 100, 60, 0, "%")], whole=True),
    "pinch": FilterSpec(_t("挤压"), pinch, [("amount", _t("数量"), -100, 100, 50, 0, "%")], whole=True),
    "skin": FilterSpec(_t("皮肤平滑"), skin_smooth, [("amount", _t("平滑度"), 0, 100, 50, 0, "%"),
                                                  ("detail", _t("保留细节"), 0, 100, 40, 0, "%")],
                       margin=lambda p: 40),
    "polar": FilterSpec(_t("极坐标"), polar, whole=True,
                        choices=[("mode", _t("方式"), [("to_polar", _t("平面坐标到极坐标")),
                                                      ("to_rect", _t("极坐标到平面坐标"))], "to_polar")]),
}
