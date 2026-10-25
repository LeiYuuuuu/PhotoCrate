"""智能对象：保留原始像素（source），缩放 / 旋转 / 透视和滤镜都只是参数，随时可改、可撤销，不会越改越糊。

params：
  {"H": 3×3 矩阵（source 像素 → 画布），"filters": [[滤镜 id, 参数, 是否启用], …], "rx", "ry"（上次渲染的位置）}
渲染：每次都从 source 出发——先按 H 一次重新取样（缩小用区域平均防锯齿，放大用三次插值），再依次应用启用的
智能滤镜。图层被移动工具挪动后，把挪动的距离并进 H（和形状图层一样靠 rx / ry 判断）。
"""
from __future__ import annotations

import math

import numpy as np


def _T(dx, dy):
    return np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1]], np.float64)


def make(layer, source: np.ndarray) -> None:
    """把一个图层变成智能对象（source = 现在的像素）。"""
    layer.source = np.ascontiguousarray(source)
    layer.kind = "smart"
    layer.params = {"H": _T(layer.x, layer.y).tolist(), "filters": [], "rx": layer.x, "ry": layer.y}


def matrix(layer) -> np.ndarray:
    H = np.asarray(layer.params.get("H") or np.eye(3), np.float64)
    if "rx" in layer.params:
        dx, dy = layer.x - int(layer.params["rx"]), layer.y - int(layer.params["ry"])
        if dx or dy:
            H = _T(dx, dy) @ H
    return H


def render_smart(layer) -> None:
    import cv2
    from . import warp
    from .document import _resize, from_float, to_float
    from .filters import run
    src = layer.source
    if src is None:
        return
    H = matrix(layer)
    h, w = src.shape[:2]
    x0, y0, x1, y1 = warp.xform_bounds(H, w, h)
    X0, Y0 = int(math.floor(x0)), int(math.floor(y0))
    nw = max(1, min(30000, int(math.ceil(x1)) - X0))
    nh = max(1, min(30000, int(math.ceil(y1)) - Y0))
    Hl = _T(-X0, -Y0) @ H
    A = Hl[:2, :2]
    s = math.sqrt(abs(np.linalg.det(A))) if warp.is_affine(Hl) else 1.0
    img = src
    if s < 0.7:                                          # 缩小很多：先区域平均缩小，再 warp
        pw, ph = max(1, int(round(w * s))), max(1, int(round(h * s)))
        img = _resize(src, pw, ph)
        Hl = Hl @ np.diag([w / pw, h / ph, 1.0])
    if warp.is_affine(Hl):
        if np.allclose(Hl[:2, :2], np.eye(2)) and np.allclose(Hl[:2, 2], np.round(Hl[:2, 2])):
            out = np.zeros((nh, nw, 4), src.dtype)                   # 只是平移：原样拷贝，不重新取样
            tx, ty = int(round(Hl[0, 2])), int(round(Hl[1, 2]))
            ys0, xs0 = max(0, ty), max(0, tx)
            ys1, xs1 = min(nh, ty + img.shape[0]), min(nw, tx + img.shape[1])
            out[ys0:ys1, xs0:xs1] = img[ys0 - ty:ys1 - ty, xs0 - tx:xs1 - tx]
        else:
            out = cv2.warpAffine(img, Hl[:2], (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                                 borderValue=(0, 0, 0, 0))
    else:
        out = cv2.warpPerspective(img, Hl, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(0, 0, 0, 0))
    flist = [f for f in layer.params.get("filters") or [] if len(f) < 3 or f[2]]
    if flist:
        f = to_float(out)
        for fid, p, *_ in flist:
            f = run(fid, f, 1.0, p)
        out = from_float(f, src.dtype)
    p = dict(layer.params)
    p["H"] = H.tolist()
    p["rx"], p["ry"] = X0, Y0
    layer.params = p
    layer.pixels, layer.x, layer.y = np.ascontiguousarray(out), X0, Y0
    layer.touch()


def transform(layer, M) -> None:
    """自由变换提交到智能对象：M 是“现在的像素 → 画布”的矩阵，并进 H（像素坐标 = H 的输出平移到 rx, ry）。"""
    from .warp import to3
    H = matrix(layer)
    p = dict(layer.params)
    # 现在的像素坐标 u = 画布 − (x, y)；新的 source → 画布 = M · T(−x, −y) · H
    p["H"] = (to3(M) @ _T(-layer.x, -layer.y) @ H).tolist()
    p["rx"], p["ry"] = layer.x, layer.y
    layer.params = p
    render_smart(layer)


def replace_contents(layer, rgba: np.ndarray) -> None:
    """替换内容：新图按旧内容的宽高等比缩放进去，位置和变换不变。"""
    old = layer.source
    h, w = old.shape[:2]
    nh, nw = rgba.shape[:2]
    k = min(w / nw, h / nh)
    S = np.array([[k, 0, (w - nw * k) / 2], [0, k, (h - nh * k) / 2], [0, 0, 1]], np.float64)
    from .document import convert
    layer.source = np.ascontiguousarray(convert(rgba, old.dtype))
    p = dict(layer.params)
    p["H"] = (matrix(layer) @ S).tolist()
    p["rx"], p["ry"] = layer.x, layer.y
    layer.params = p
    render_smart(layer)
