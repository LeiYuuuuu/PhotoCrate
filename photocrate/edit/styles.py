"""图层样式：投影、外发光、内阴影、内发光、描边、颜色叠加、渐变叠加、斜面和浮雕。

都由图层的不透明度（alpha，含蒙版）算出来：在图层四周留出效果需要的边距（pad），得到一串“平面”
（颜色 + alpha + 混合模式），合成时按顺序叠到下面的结果上：
    投影 → 外发光 → 图层本身（受“填充”影响）→ 渐变叠加 → 颜色叠加 → 内发光 → 内阴影 → 斜面和浮雕 → 描边
大小 / 距离都是画布像素，按缩放级别换算，所以缩小预览和原尺寸看起来一致。
"""
from __future__ import annotations

import copy
import math

import numpy as np

from ..i18n import t as _t

EFFECTS = [("drop_shadow", _t("投影")), ("outer_glow", _t("外发光")), ("inner_shadow", _t("内阴影")),
           ("inner_glow", _t("内发光")), ("bevel", _t("斜面和浮雕")), ("color_overlay", _t("颜色叠加")),
           ("gradient_overlay", _t("渐变叠加")), ("stroke", _t("描边"))]

DEFAULTS = {
    "drop_shadow": {"on": False, "color": "#000000", "opacity": 60.0, "angle": 120.0, "distance": 12.0,
                    "spread": 0.0, "size": 16.0, "blend": "multiply"},
    "outer_glow": {"on": False, "color": "#FFF2B0", "opacity": 70.0, "spread": 0.0, "size": 24.0, "blend": "screen"},
    "inner_shadow": {"on": False, "color": "#000000", "opacity": 60.0, "angle": 120.0, "distance": 6.0,
                     "size": 10.0, "blend": "multiply"},
    "inner_glow": {"on": False, "color": "#FFF2B0", "opacity": 70.0, "size": 14.0, "blend": "screen"},
    "bevel": {"on": False, "depth": 100.0, "size": 10.0, "soften": 0.0, "angle": 120.0, "altitude": 30.0,
              "highlight": 75.0, "shadow": 75.0},
    "color_overlay": {"on": False, "color": "#E5484D", "opacity": 100.0, "blend": "normal"},
    "gradient_overlay": {"on": False, "c0": "#000000", "c1": "#FFFFFF", "angle": 90.0, "opacity": 100.0,
                         "blend": "normal"},
    "stroke": {"on": False, "color": "#000000", "size": 6.0, "position": "outside", "opacity": 100.0},
}
PARAMS = {   # 界面：(键, 名称, 最小, 最大, 单位)
    "drop_shadow": [("opacity", _t("不透明度"), 0, 100, "%"), ("angle", _t("角度"), -180, 180, "°"),
                    ("distance", _t("距离"), 0, 500, " px"), ("spread", _t("扩展"), 0, 100, "%"),
                    ("size", _t("大小"), 0, 250, " px")],
    "outer_glow": [("opacity", _t("不透明度"), 0, 100, "%"), ("spread", _t("扩展"), 0, 100, "%"),
                   ("size", _t("大小"), 0, 250, " px")],
    "inner_shadow": [("opacity", _t("不透明度"), 0, 100, "%"), ("angle", _t("角度"), -180, 180, "°"),
                     ("distance", _t("距离"), 0, 500, " px"), ("size", _t("大小"), 0, 250, " px")],
    "inner_glow": [("opacity", _t("不透明度"), 0, 100, "%"), ("size", _t("大小"), 0, 250, " px")],
    "bevel": [("depth", _t("深度"), 1, 1000, "%"), ("size", _t("大小"), 0, 250, " px"),
              ("soften", _t("软化"), 0, 16, " px"), ("angle", _t("角度"), -180, 180, "°"),
              ("altitude", _t("高度"), 0, 90, "°"), ("highlight", _t("高光不透明度"), 0, 100, "%"),
              ("shadow", _t("阴影不透明度"), 0, 100, "%")],
    "color_overlay": [("opacity", _t("不透明度"), 0, 100, "%")],
    "gradient_overlay": [("opacity", _t("不透明度"), 0, 100, "%"), ("angle", _t("角度"), -180, 180, "°")],
    "stroke": [("size", _t("大小"), 1, 250, " px"), ("opacity", _t("不透明度"), 0, 100, "%")],
}
COLORS = {"drop_shadow": ["color"], "outer_glow": ["color"], "inner_shadow": ["color"], "inner_glow": ["color"],
          "color_overlay": ["color"], "gradient_overlay": ["c0", "c1"], "stroke": ["color"]}
STROKE_POS = [("outside", _t("外部")), ("inside", _t("内部")), ("center", _t("居中"))]


def normalize(style: dict | None) -> dict:
    out = {}
    for k, d in DEFAULTS.items():
        v = copy.deepcopy(d)
        if style and isinstance(style.get(k), dict):
            v.update(style[k])
        out[k] = v
    return out


def active(style: dict | None) -> list[str]:
    if not style:
        return []
    return [k for k, _ in EFFECTS if isinstance(style.get(k), dict) and style[k].get("on")]


def key(style: dict | None) -> tuple:
    return tuple((k, tuple(sorted((a, str(b)) for a, b in style[k].items()))) for k in active(style))


def pad(style: dict | None) -> int:
    """效果最多伸出图层边界多少画布像素。"""
    p = 0
    for k in active(style):
        q = style[k]
        if k == "drop_shadow":
            p = max(p, float(q["distance"]) + float(q["size"]) * 1.5 + 2)
        elif k == "outer_glow":
            p = max(p, float(q["size"]) * 1.5 + 2)
        elif k == "stroke" and q.get("position", "outside") != "inside":
            p = max(p, float(q["size"]) + 2)
    return int(math.ceil(p))


def _hex(c: str) -> np.ndarray:
    c = (c or "#000000").lstrip("#")
    return np.array([int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)], np.float32)


def _blur(a, sigma):
    import cv2
    if sigma < 0.3:
        return a
    return cv2.GaussianBlur(a, (0, 0), sigma, borderType=cv2.BORDER_CONSTANT)


def _shift(a, dx, dy):
    import cv2
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(a, M, (a.shape[1], a.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=0)


def _spread(a, spread, size):
    """扩展：先把边缘往外推 size·spread，再按剩下的大小模糊。"""
    import cv2
    s = float(spread) / 100
    grow = size * s
    if grow >= 0.5:
        hard = (a > 0.02).astype(np.uint8)
        dist = cv2.distanceTransform(1 - hard, cv2.DIST_L2, 5)
        a = np.maximum(a, np.clip(grow + 0.5 - dist, 0, 1))
    return _blur(a, max(0.0, size * (1 - s)) / 2)


def planes(alpha: np.ndarray, style: dict, scale: float):
    """alpha：已经加了边距的图层不透明度（float 0–1）。返回 [(位置, 颜色, alpha, 混合模式)]，
    位置 = behind（图层下面）/ above（图层上面）。颜色可以是 3 个数或 H×W×3。"""
    import cv2
    out = []
    st = normalize(style)
    A = alpha.astype(np.float32)
    for k in active(style):
        q = st[k]
        op = float(q.get("opacity", 100)) / 100
        if k == "drop_shadow":
            a = math.radians(float(q["angle"]))
            d = float(q["distance"]) * scale
            sh = _shift(A, -math.cos(a) * d, math.sin(a) * d)
            sh = _spread(sh, q["spread"], float(q["size"]) * scale)
            out.append(("behind", _hex(q["color"]), sh * op, q.get("blend", "multiply")))
        elif k == "outer_glow":
            g = _spread(A, q["spread"], float(q["size"]) * scale)
            out.append(("behind", _hex(q["color"]), g * op, q.get("blend", "screen")))
        elif k == "gradient_overlay":
            h, w = A.shape
            a = math.radians(float(q["angle"]))
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            t = (xx - w / 2) * math.cos(a) - (yy - h / 2) * math.sin(a)
            L = abs(w * math.cos(a)) + abs(h * math.sin(a))
            t = np.clip(t / max(1.0, L) + 0.5, 0, 1)[..., None]
            col = _hex(q["c0"]) + (_hex(q["c1"]) - _hex(q["c0"])) * t
            out.append(("above", col, A * op, q.get("blend", "normal")))
        elif k == "color_overlay":
            out.append(("above", _hex(q["color"]), A * op, q.get("blend", "normal")))
        elif k == "inner_glow":
            g = _blur(1 - A, float(q["size"]) * scale / 2) * A
            out.append(("above", _hex(q["color"]), np.clip(g * 1.6, 0, 1) * op, q.get("blend", "screen")))
        elif k == "inner_shadow":
            a = math.radians(float(q["angle"]))
            d = float(q["distance"]) * scale
            inv = _shift(1 - A, -math.cos(a) * d, math.sin(a) * d)
            inv = np.where(np.abs(_shift(np.ones_like(A), -math.cos(a) * d, math.sin(a) * d) - 1) > 0.5, 1.0, inv)
            s = _blur(inv.astype(np.float32), float(q["size"]) * scale / 2) * A
            out.append(("above", _hex(q["color"]), s * op, q.get("blend", "multiply")))
        elif k == "bevel":
            size = max(0.5, float(q["size"]) * scale)
            hmap = _blur(A, size / 2)
            gy, gx = np.gradient(hmap)
            depth = float(q["depth"]) / 100 * size * 1.2
            nx, ny, nz = -gx * depth, -gy * depth, np.ones_like(hmap)
            n = np.sqrt(nx * nx + ny * ny + nz * nz)
            az, alt = math.radians(float(q["angle"])), math.radians(float(q["altitude"]))
            lx, ly, lz = math.cos(alt) * math.cos(az), -math.cos(alt) * math.sin(az), math.sin(alt)
            shade = (nx * lx + ny * ly + nz * lz) / n - lz
            if float(q.get("soften", 0)) > 0:
                shade = _blur(shade.astype(np.float32), float(q["soften"]) * scale / 2)
            hi = np.clip(shade * 2.2, 0, 1) * A * float(q["highlight"]) / 100
            lo = np.clip(-shade * 2.2, 0, 1) * A * float(q["shadow"]) / 100
            out.append(("above", np.array([1, 1, 1], np.float32), hi.astype(np.float32), "screen"))
            out.append(("above", np.array([0, 0, 0], np.float32), lo.astype(np.float32), "multiply"))
        elif k == "stroke":
            size = float(q["size"]) * scale
            hard = (A > 0.5).astype(np.uint8)
            d_out = cv2.distanceTransform(1 - hard, cv2.DIST_L2, 5)
            d_in = cv2.distanceTransform(hard, cv2.DIST_L2, 5)
            pos = q.get("position", "outside")
            if pos == "outside":
                ring = np.clip(size + 0.5 - d_out, 0, 1) * (1 - A)
            elif pos == "inside":
                ring = np.clip(size + 0.5 - d_in, 0, 1) * A
            else:
                ring = np.maximum(np.clip(size / 2 + 0.5 - d_out, 0, 1) * (1 - A), np.clip(size / 2 + 0.5 - d_in, 0, 1) * A)
            out.append(("stroke", _hex(q["color"]), ring.astype(np.float32) * op, "normal"))
    order = {"behind": 0, "above": 2, "stroke": 3}
    behind = [p for p in out if p[0] == "behind"]
    behind.sort(key=lambda p: 0 if p[3] == "multiply" else 1)     # 投影在外发光下面
    return behind + [p for p in out if order[p[0]] == 2] + [p for p in out if p[0] == "stroke"]
