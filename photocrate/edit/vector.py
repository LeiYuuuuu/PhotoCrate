"""矢量：钢笔路径与形状图层。

路径 = 若干子路径，每个子路径 {"pts": [[x, y, ix, iy, ox, oy], …], "closed": bool}：
锚点 (x, y)，进入方向的控制柄 (ix, iy)、离开方向的控制柄 (ox, oy)，都是画布坐标；控制柄和锚点重合就是尖角。
相邻两个锚点之间是三次贝塞尔：P0 = 锚点 a、P1 = a 的离开柄、P2 = b 的进入柄、P3 = 锚点 b。

形状图层（kind = "shape"）的参数：
  {"type": rect / rrect / ellipse / polygon / line / path, "x0", "y0", "x1", "y1"（外接框，画布坐标）,
   "radius"（圆角）, "sides"（多边形边数）, "path"（type = path 时）,
   "fill": "#RRGGBB" 或 None, "stroke": "#RRGGBB" 或 None, "stroke_w": 描边宽度}
和文字图层一样：改参数就按参数重新画出像素，所以一直可以再编辑；画笔 / 滤镜作用在它上面时先栅格化。
"""
from __future__ import annotations

import math

import numpy as np

SHAPES = ["rect", "rrect", "ellipse", "polygon", "line"]


def anchor(x: float, y: float) -> list[float]:
    return [float(x), float(y), float(x), float(y), float(x), float(y)]


def _bez(p0, p1, p2, p3, n: int) -> np.ndarray:
    t = np.linspace(0, 1, n + 1)[:, None]
    return ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * p1 + 3 * (1 - t) * t * t * p2 + (t ** 3) * p3


def flatten(sub: dict, tol: float = 0.5) -> np.ndarray:
    """子路径 → 折线点 (N, 2)。每段按控制多边形长度决定分几步。"""
    pts = sub.get("pts") or []
    if not pts:
        return np.zeros((0, 2))
    segs = list(zip(pts, pts[1:]))
    if sub.get("closed") and len(pts) > 1:
        segs.append((pts[-1], pts[0]))
    out = [np.array(pts[0][:2], np.float64)[None]]
    for a, b in segs:
        p0, p1 = np.array(a[:2]), np.array(a[4:6])
        p2, p3 = np.array(b[2:4]), np.array(b[:2])
        L = np.linalg.norm(p1 - p0) + np.linalg.norm(p2 - p1) + np.linalg.norm(p3 - p2)
        n = int(np.clip(L / max(0.5, tol * 6), 1, 400))
        if np.allclose(p0, p1) and np.allclose(p2, p3):
            n = 1
        out.append(_bez(p0, p1, p2, p3, n)[1:])
    return np.vstack(out)


def path_polys(path: list[dict]) -> list[tuple[np.ndarray, bool]]:
    return [(flatten(s), bool(s.get("closed"))) for s in path if s.get("pts")]


def path_bounds(path: list[dict]):
    pts = [p for poly, _ in path_polys(path) for p in poly]
    if not pts:
        return None
    a = np.array(pts)
    return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())


def _ss(w: int, h: int) -> int:
    """超采样倍数：形状不大时按 4×4 子像素算覆盖率（边缘的抗锯齿准确），很大时少一点省内存。"""
    n = w * h
    return 4 if n * 16 <= 64_000_000 else 2 if n * 4 <= 64_000_000 else 1


def _down(m: np.ndarray, w: int, h: int) -> np.ndarray:
    import cv2
    return m if m.shape[:2] == (h, w) else cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA)


def path_mask(path: list[dict], w: int, h: int, ox: float = 0, oy: float = 0) -> np.ndarray:
    """路径围成的区域（非零环绕规则，开放的子路径也按闭合算，和 Photoshop 一样），uint8，按覆盖率抗锯齿。
    画布坐标里像素 i 占 [i, i+1)；在 k 倍超采样的网格里，子像素 j 的中心是 (j + 0.5) / k。"""
    import cv2
    k = _ss(w, h)
    m = np.zeros((h * k, w * k), np.uint8)
    polys = [np.round(((p - (ox, oy)) * k - 0.5) * 16).astype(np.int32) for p, _ in path_polys(path) if len(p) >= 3]
    if polys:
        cv2.fillPoly(m, polys, 255, cv2.LINE_8, 4)
    return _down(m, w, h)


def stroke_mask(path: list[dict], w: int, h: int, width: float, ox: float = 0, oy: float = 0) -> np.ndarray:
    import cv2
    k = _ss(w, h)
    m = np.zeros((h * k, w * k), np.uint8)
    for p, closed in path_polys(path):
        if len(p) < 2:
            continue
        pts = np.round(((p - (ox, oy)) * k - 0.5) * 16).astype(np.int32)
        cv2.polylines(m, [pts], closed, 255, max(1, int(round(width * k))), cv2.LINE_8, 4)
    return _down(m, w, h)


# ---------------------------------------------------------------------------
# 形状
# ---------------------------------------------------------------------------

def shape_path(p: dict) -> list[dict]:
    """形状参数 → 路径。"""
    t = p.get("type", "rect")
    if t == "path":
        return p.get("path") or []
    x0, y0, x1, y1 = (float(p.get(k, 0)) for k in ("x0", "y0", "x1", "y1"))
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if t == "line":
        return [{"pts": [anchor(p["x0"], p["y0"]), anchor(p["x1"], p["y1"])], "closed": False}]
    if t == "ellipse":
        cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
        k = 0.5522847498
        pts = [[cx, cy - ry, cx - rx * k, cy - ry, cx + rx * k, cy - ry],
               [cx + rx, cy, cx + rx, cy - ry * k, cx + rx, cy + ry * k],
               [cx, cy + ry, cx + rx * k, cy + ry, cx - rx * k, cy + ry],
               [cx - rx, cy, cx - rx, cy + ry * k, cx - rx, cy - ry * k]]
        return [{"pts": pts, "closed": True}]
    if t == "polygon":
        n = max(3, int(p.get("sides", 6)))
        cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
        pts = [anchor(cx + rx * math.sin(2 * math.pi * i / n), cy - ry * math.cos(2 * math.pi * i / n)) for i in range(n)]
        return [{"pts": pts, "closed": True}]
    if t == "rrect":
        r = max(0.0, min(float(p.get("radius", 20)), (x1 - x0) / 2, (y1 - y0) / 2))
        k = 0.5522847498 * r
        pts = [[x0 + r, y0, x0 + r - k, y0, x0 + r, y0], [x1 - r, y0, x1 - r, y0, x1 - r + k, y0],
               [x1, y0 + r, x1, y0 + r - k, x1, y0 + r], [x1, y1 - r, x1, y1 - r, x1, y1 - r + k],
               [x1 - r, y1, x1 - r + k, y1, x1 - r, y1], [x0 + r, y1, x0 + r, y1, x0 + r - k, y1],
               [x0, y1 - r, x0, y1 - r + k, x0, y1 - r], [x0, y0 + r, x0, y0 + r, x0, y0 + r - k]]
        return [{"pts": pts, "closed": True}]
    return [{"pts": [anchor(x0, y0), anchor(x1, y0), anchor(x1, y1), anchor(x0, y1)], "closed": True}]


def _rgb(c: str) -> np.ndarray:
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255


def render_shape(p: dict) -> tuple[np.ndarray, int, int]:
    """按参数画出形状：返回 (uint8 RGBA, 左上角 x, y)。"""
    path = shape_path(p)
    b = path_bounds(path)
    sw = float(p.get("stroke_w", 0) or 0) if p.get("stroke") else 0.0
    if b is None:
        return np.zeros((1, 1, 4), np.uint8), 0, 0
    pad = int(math.ceil(sw / 2)) + 2
    x0, y0 = int(math.floor(b[0])) - pad, int(math.floor(b[1])) - pad
    w = max(1, int(math.ceil(b[2])) + pad - x0)
    h = max(1, int(math.ceil(b[3])) + pad - y0)
    out = np.zeros((h, w, 4), np.float32)
    fill = p.get("fill")
    if fill and p.get("type") != "line":
        a = path_mask(path, w, h, x0, y0).astype(np.float32) / 255
        out[..., :3] = _rgb(fill)
        out[..., 3] = a
    if sw > 0:
        s = stroke_mask(path, w, h, sw, x0, y0).astype(np.float32) / 255
        c = _rgb(p["stroke"])
        oa = s + out[..., 3] * (1 - s)
        out[..., :3] = np.where(oa[..., None] > 1e-6, (c * s[..., None] + out[..., :3] * (out[..., 3] * (1 - s))[..., None])
                                / np.maximum(oa, 1e-6)[..., None], 0)
        out[..., 3] = oa
    return np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8), x0, y0


def translate_path(path: list[dict], dx: float, dy: float) -> list[dict]:
    return [{"pts": [[v + (dx if i % 2 == 0 else dy) for i, v in enumerate(pt)] for pt in s["pts"]],
             "closed": s.get("closed", False)} for s in path]


def translate_shape(p: dict, dx: float, dy: float) -> dict:
    q = dict(p)
    for k in ("x0", "x1"):
        if k in q:
            q[k] = float(q[k]) + dx
    for k in ("y0", "y1"):
        if k in q:
            q[k] = float(q[k]) + dy
    if q.get("path"):
        q["path"] = translate_path(q["path"], dx, dy)
    return q


def transform_path(path: list[dict], H) -> list[dict]:
    """路径的每个锚点和控制柄都按矩阵 H（2×3 / 3×3）映射。"""
    from .warp import apply_h
    out = []
    for sub in path:
        pts = []
        for pt in sub["pts"]:
            q = apply_h(H, [pt[0:2], pt[2:4], pt[4:6]])
            pts.append([float(v) for v in q.ravel()])
        out.append({"pts": pts, "closed": sub.get("closed", False)})
    return out


def transform_shape(p: dict, H) -> dict:
    """形状参数整体变换：先转成路径（type = path），描边宽度按面积缩放。"""
    from .warp import to3
    q = dict(p)
    path = shape_path(p)
    q["path"] = transform_path(path, H)
    q["type"] = "path"
    A = to3(H)[:2, :2]
    q["stroke_w"] = float(p.get("stroke_w", 0) or 0) * math.sqrt(abs(np.linalg.det(A)))
    q.pop("rx", None)
    q.pop("ry", None)
    return q


def scale_shape(p: dict, sx: float, sy: float) -> dict:
    q = transform_shape(p, np.array([[sx, 0, 0], [0, sy, 0]], np.float64))
    return q
