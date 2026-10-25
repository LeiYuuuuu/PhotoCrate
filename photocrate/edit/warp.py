"""自由变换里的透视、扭曲和变形（Photoshop 的“变形”网格）。

- 透视 / 扭曲 / 斜切：一个 3×3 单应矩阵 H，把图层像素坐标映射到画布坐标（OpenCV warpPerspective）。
- 变形：4×4 个控制点（画布坐标）的双三次贝塞尔曲面。图层像素 (u, v) 对应曲面参数 (u / w, v / h)。
  渲染时把曲面切成 n×n 个四边形、每个拆成两个三角形，画出“这个像素属于哪个三角形”的编号图，
  再用每个三角形的仿射逆变换算出每个像素对应的图层坐标，最后 remap 一次——全程向量化，不逐像素循环。

坐标约定和 warpAffine 一样：像素 (x, y) 的位置就是整数下标。
"""
from __future__ import annotations

import numpy as np


def is_affine(H: np.ndarray, eps: float = 1e-9) -> bool:
    H = np.asarray(H, np.float64)
    return H.shape == (2, 3) or (abs(H[2, 0]) < eps and abs(H[2, 1]) < eps and abs(H[2, 2] - 1) < 1e-9)


def to3(M: np.ndarray) -> np.ndarray:
    M = np.asarray(M, np.float64)
    if M.shape == (3, 3):
        return M / M[2, 2]
    return np.vstack([M, [0, 0, 1]])


def apply_h(H: np.ndarray, pts) -> np.ndarray:
    """把点 (N, 2) 用 H（2×3 或 3×3）映射过去。"""
    H = to3(H)
    p = np.asarray(pts, np.float64).reshape(-1, 2)
    q = np.hstack([p, np.ones((len(p), 1))]) @ H.T
    return q[:, :2] / q[:, 2:3]


def quad_h(w: float, h: float, quad) -> np.ndarray:
    """图层矩形 (0,0)-(w,h) 的四个角（左上、右上、右下、左下）映射到 quad 的单应矩阵。"""
    import cv2
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    return cv2.getPerspectiveTransform(src, np.float32(quad)).astype(np.float64)


def level_h(H: np.ndarray, f: int, ex: int, ey: int) -> np.ndarray:
    """缩放级别 f（代理像素 = 原像素 / f）上的矩阵，并平移到输出块 (ex, ey)。"""
    S = np.diag([float(f), float(f), 1.0])
    Si = np.diag([1.0 / f, 1.0 / f, 1.0])
    T = np.array([[1, 0, -ex], [0, 1, -ey], [0, 0, 1]], np.float64)
    return T @ Si @ to3(H) @ S


def perspective(src: np.ndarray, H: np.ndarray, w: int, h: int, interp=None, border=0) -> np.ndarray:
    import cv2
    interp = cv2.INTER_LINEAR if interp is None else interp
    bv = (border,) * (src.shape[2] if src.ndim == 3 else 1)
    return cv2.warpPerspective(src, to3(H), (int(w), int(h)), flags=interp, borderMode=cv2.BORDER_CONSTANT,
                               borderValue=bv)


# ---------------------------------------------------------------------------
# 变形网格
# ---------------------------------------------------------------------------

def _bern(t: np.ndarray) -> np.ndarray:
    t = np.asarray(t, np.float64)[:, None]
    return np.hstack([(1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3])


def grid_from_h(H: np.ndarray, w: float, h: float) -> np.ndarray:
    """初始变形网格：图层上均匀的 4×4 点经 H 映射（H 是仿射时得到的曲面就是这个仿射变换本身）。"""
    us, vs = np.linspace(0, w, 4), np.linspace(0, h, 4)
    pts = np.array([[u, v] for v in vs for u in us])
    return apply_h(H, pts).reshape(4, 4, 2)


def surface(ctrl: np.ndarray, nu: int, nv: int) -> np.ndarray:
    """曲面上 (nv+1)×(nu+1) 个点的位置。ctrl[j, i] = 第 j 行（v）、第 i 列（u）的控制点。"""
    Bu, Bv = _bern(np.linspace(0, 1, nu + 1)), _bern(np.linspace(0, 1, nv + 1))
    return np.einsum("bj,ai,jik->bak", Bv, Bu, np.asarray(ctrl, np.float64))


def mesh_bounds(ctrl: np.ndarray, n: int = 24) -> tuple[float, float, float, float]:
    P = surface(ctrl, n, n).reshape(-1, 2)
    return float(P[:, 0].min()), float(P[:, 1].min()), float(P[:, 0].max()), float(P[:, 1].max())


def mesh_maps(ctrl: np.ndarray, w: float, h: float, ox: int, oy: int, ow: int, oh: int, f: int = 1,
              n: int = 32) -> tuple[np.ndarray, np.ndarray]:
    """输出块（级别 f 上的 ox, oy, ow, oh）里每个像素对应的源坐标（级别 f 上的图层坐标），给 cv2.remap 用。
    不在曲面上的像素是 -1（remap 填透明）。"""
    import cv2
    P = surface(ctrl, n, n) / f - (ox, oy)                         # 输出块里的位置
    U, V = np.meshgrid(np.linspace(0, w / f, n + 1), np.linspace(0, h / f, n + 1))
    S = np.dstack([U, V])
    tris_d, tris_s = [], []
    for j in range(n):
        for i in range(n):
            a, b, c, d = (j, i), (j, i + 1), (j + 1, i + 1), (j + 1, i)
            for t in ((a, b, c), (a, c, d)):
                tris_d.append([P[k] for k in t])
                tris_s.append([S[k] for k in t])
    D = np.asarray(tris_d)                                          # (N, 3, 2)
    Sr = np.asarray(tris_s)
    N = len(D)
    ids = np.full((oh, ow), -1, np.int32)
    lo, hi = D.min(axis=1), D.max(axis=1)
    vis = (hi[:, 0] >= -1) & (hi[:, 1] >= -1) & (lo[:, 0] <= ow) & (lo[:, 1] <= oh)
    for k in np.flatnonzero(vis):
        cv2.fillConvexPoly(ids, np.round(D[k] * 16).astype(np.int32), int(k), cv2.LINE_8, 4)
    # 每个三角形：输出 (x, y, 1) → 源 (u, v) 的仿射矩阵
    Dm = np.concatenate([D.transpose(0, 2, 1), np.ones((N, 1, 3))], axis=1)    # (N, 3, 3)
    det = np.linalg.det(Dm)
    good = np.abs(det) > 1e-9
    A = np.zeros((N, 2, 3))
    A[good] = Sr[good].transpose(0, 2, 1) @ np.linalg.inv(Dm[good])
    mapx = np.full((oh, ow), -1, np.float32)
    mapy = np.full((oh, ow), -1, np.float32)
    m = ids >= 0
    if m.any():
        yy, xx = np.nonzero(m)
        Ak = A[ids[m]]
        mapx[m] = Ak[:, 0, 0] * xx + Ak[:, 0, 1] * yy + Ak[:, 0, 2]
        mapy[m] = Ak[:, 1, 0] * xx + Ak[:, 1, 1] * yy + Ak[:, 1, 2]
        bad = ~good[ids[m]]
        if bad.any():
            mapx[yy[bad], xx[bad]] = -1
            mapy[yy[bad], xx[bad]] = -1
    return mapx, mapy


def mesh_warp(src: np.ndarray, ctrl: np.ndarray, ox: int, oy: int, ow: int, oh: int, f: int = 1, n: int = 32,
              src_size: tuple[float, float] | None = None, interp=None) -> np.ndarray:
    """把 src（级别 f 上的图层像素）按变形网格画到输出块里。src_size = 原尺寸 (w, h)（代理图宽高补齐过）。"""
    import cv2
    w, h = src_size if src_size is not None else (src.shape[1] * f, src.shape[0] * f)
    mx, my = mesh_maps(ctrl, w, h, ox, oy, ow, oh, f, n)
    interp = cv2.INTER_LINEAR if interp is None else interp
    bv = (0,) * (src.shape[2] if src.ndim == 3 else 1)
    return cv2.remap(src, mx, my, interp, borderMode=cv2.BORDER_CONSTANT, borderValue=bv)


def warp_any(src: np.ndarray, xf, f: int, ex: int, ey: int, ew: int, eh: int, src_size=None,
             interp=None, n: int = 20) -> np.ndarray:
    """按图层的 xform（2×3 / 3×3 矩阵或 {"mesh": 控制点}）把级别 f 的代理像素画到输出块 (ex, ey, ew, eh)。"""
    import cv2
    if isinstance(xf, dict):
        return mesh_warp(src, xf["mesh"], ex, ey, ew, eh, f, n, src_size, interp)
    Hl = level_h(xf, f, ex, ey)
    interp = cv2.INTER_LINEAR if interp is None else interp
    bv = (0,) * (src.shape[2] if src.ndim == 3 else 1)
    if is_affine(Hl):
        return cv2.warpAffine(src, Hl[:2], (int(ew), int(eh)), flags=interp, borderMode=cv2.BORDER_CONSTANT,
                              borderValue=bv)
    return cv2.warpPerspective(src, Hl, (int(ew), int(eh)), flags=interp, borderMode=cv2.BORDER_CONSTANT,
                               borderValue=bv)


def xform_bounds(xf, w: float, h: float) -> tuple[float, float, float, float]:
    """变换后的外接框（画布坐标）x0, y0, x1, y1。"""
    if isinstance(xf, dict):
        return mesh_bounds(xf["mesh"])
    c = apply_h(xf, [[0, 0], [w, 0], [w, h], [0, h]])
    if not is_affine(xf):                      # 透视：边是直线，四个角就够了；但要防止跨过地平线
        H = to3(xf)
        den = np.array([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]], np.float64) @ H[2]
        if (den <= 1e-6).any():
            raise ValueError("perspective crosses the horizon")
    return float(c[:, 0].min()), float(c[:, 1].min()), float(c[:, 0].max()), float(c[:, 1].max())
