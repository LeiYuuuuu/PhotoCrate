"""合并曝光（包围曝光融合）：同一场景、不同曝光的几张照片 → 一张亮部暗部都有细节、曝光正常的照片。

全部用 numpy / OpenCV 完成，不需要额外的库。流程：

1. 选参考帧：曝光居中的一张。参考帧不做任何重采样，清晰度原样保留；
   移动的物体最后也以它为准。
2. 对齐：其余每张先把亮度匹配到参考帧（直方图匹配），用 SIFT 特征 + RANSAC 求单应矩阵（手持时的平移、旋转、
   轻微透视都能修正），再在两张都做过局部对比度归一化的图上用 LK 光流跟踪几千个角点，精修到 0.1 像素左右。
   位移小于 0.05 像素（三脚架）就完全不动；需要变换时用 Lanczos 插值，尽量不损失锐度。
3. 去重影：每张和离参考帧更近的几张做“亮度映射后比较”——两张都没过曝 / 欠曝、内容却对不上的地方就是
   移动的物体（行人、树叶、水面）。这些地方这一张不参与融合，最终退回到参考帧那一张的画面。
4. 融合：Mertens 曝光融合（对比度 × 饱和度 × 曝光适中 作为权重，拉普拉斯金字塔逐级混合），直接得到
   正常亮度的照片，不需要 HDR 色调映射。权重里的对比度项会偏向更清晰的那张。

自动去不干净的重影，可以在修图窗口里框出来，挑一张源图补上（ghost_patch）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..i18n import t as _t

MAX_FRAMES = 9
DEGHOST = {"off": None, "low": 0.14, "normal": 0.09, "high": 0.06}   # 判为移动物体的差异阈值（0–1 亮度）
_ALIGN_SIDE = 2400          # 特征匹配用的分辨率（长边）
_REFINE_SIDE = 4000         # 亚像素精修用的分辨率（长边）
_GHOST_SIDE = 1600          # 判断移动物体用的分辨率（长边）
_STILL = 0.05               # 位移小于这么多像素就当作没动（不重采样）


class Cancelled(Exception):
    pass


class MergeError(ValueError):
    pass


@dataclass
class Frame:
    rgb: np.ndarray                  # uint8 H×W×3，已转正
    name: str = ""
    ev: float | None = None          # 曝光量 log2(快门 × ISO / 光圈²)；没有 EXIF 时为 None，按画面亮度估


@dataclass
class MergeResult:
    image: np.ndarray                # uint8 H×W×3
    aligned: list[np.ndarray]        # 对齐后的每张（与输入同序）；没参与融合的是原图
    ref: int                         # 参考帧下标
    evs: list[float]                 # 相对参考帧的曝光（EV），参考帧为 0
    shifts: list[float]              # 对齐时画面最大移动了多少像素
    skipped: list[int] = field(default_factory=list)   # 对不齐、没参与融合的
    ghost_fraction: float = 0.0      # 判为移动物体的面积占比


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _small(img: np.ndarray, side: int) -> tuple[np.ndarray, float]:
    import cv2
    h, w = img.shape[:2]
    s = min(1.0, side / max(h, w))
    if s >= 1.0:
        return img, 1.0
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA), s


def _gray(rgb: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def _match_lut(src: np.ndarray, dst: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    """直方图匹配：返回 256 项的查找表，把 src 的亮度分布映射成 dst 的（同一场景、不同曝光 → 单调映射）。"""
    a = src[mask] if mask is not None else src.ravel()
    b = dst[mask] if mask is not None else dst.ravel()
    if a.size < 64:
        return np.arange(256, dtype=np.float32)
    ha = np.bincount(a, minlength=256).astype(np.float64)
    hb = np.bincount(b, minlength=256).astype(np.float64)
    ca = (np.cumsum(ha) - ha / 2) / a.size
    cb = (np.cumsum(hb) - hb / 2) / b.size
    keep = hb > 0
    return np.interp(ca, cb[keep], np.arange(256)[keep]).astype(np.float32)


def _apply_lut(img: np.ndarray, luts) -> np.ndarray:
    """每个通道一张表；返回 float32（0–255）。"""
    if img.ndim == 2:
        return luts[img]
    return np.stack([luts[c][img[..., c]] for c in range(img.shape[2])], axis=-1)


def _channel_luts(src: np.ndarray, dst: np.ndarray, mask: np.ndarray | None) -> list[np.ndarray]:
    return [_match_lut(src[..., c], dst[..., c], mask) for c in range(src.shape[2])]


def _scale_mat(s: float) -> np.ndarray:
    """原图坐标 → 缩小 s 倍后的坐标（按像素中心对齐，INTER_AREA 的约定）。"""
    c = 0.5 * s - 0.5
    return np.array([[s, 0, c], [0, s, c], [0, 0, 1]], np.float64)


def _to_level(H: np.ndarray, s: float) -> np.ndarray:
    S = _scale_mat(s)
    return S @ H @ np.linalg.inv(S)


def _to_full(H: np.ndarray, s: float) -> np.ndarray:
    S = _scale_mat(s)
    return np.linalg.inv(S) @ H @ S


def _max_shift(H: np.ndarray, w: int, h: int) -> float:
    """单应矩阵让画面（四角和中心）最多移动多少像素。"""
    pts = np.array([[0, 0, 1], [w - 1, 0, 1], [0, h - 1, 1], [w - 1, h - 1, 1], [(w - 1) / 2, (h - 1) / 2, 1]],
                   np.float64).T
    q = H @ pts
    q = q[:2] / q[2:]
    return float(np.max(np.hypot(*(q - pts[:2]))))


def _sane(H: np.ndarray | None, w: int, h: int) -> bool:
    if H is None or not np.all(np.isfinite(H)) or abs(H[2, 2]) < 1e-9:
        return False
    H = H / H[2, 2]
    det = np.linalg.det(H[:2, :2])
    return 0.8 < det < 1.25 and _max_shift(H, w, h) < 0.12 * math.hypot(w, h)


# ---------------------------------------------------------------------------
# 1. 曝光与参考帧
# ---------------------------------------------------------------------------

def exif_ev(exposure, fnumber, iso) -> float | None:
    """曝光量（越大越亮）：log2(快门秒数 × ISO / 光圈²)。"""
    try:
        t, n, i = float(exposure), float(fnumber), float(iso or 100)
        if t <= 0 or n <= 0 or i <= 0:
            return None
        return math.log2(t * i / (n * n))
    except (TypeError, ValueError):
        return None


def _well_exposed(gray_small: np.ndarray) -> float:
    g = gray_small.astype(np.float32) / 255.0
    return float(np.mean(np.exp(-((g - 0.5) ** 2) / 0.08)))


def _relative_evs(smalls: list[np.ndarray], frames: list[Frame], ref: int) -> list[float]:
    if all(f.ev is not None for f in frames):
        return [float(f.ev - frames[ref].ev) for f in frames]
    out = []
    g0 = _gray(smalls[ref]).astype(np.float32) / 255.0
    for i, s in enumerate(smalls):
        if i == ref:
            out.append(0.0)
            continue
        g = _gray(s).astype(np.float32) / 255.0
        ok = (g0 > 0.04) & (g0 < 0.96) & (g > 0.04) & (g < 0.96)
        if ok.sum() < 100:
            ok = np.ones_like(ok)
        a = np.median(np.power(g[ok], 2.2)) + 1e-6
        b = np.median(np.power(g0[ok], 2.2)) + 1e-6
        out.append(float(np.log2(a / b)))
    return out


def _clipped(small: np.ndarray) -> float:
    """过曝或死黑的像素占比。"""
    g = _gray(small)
    return float(((g > 245) | (g < 10)).mean())


def pick_reference(smalls: list[np.ndarray], frames: list[Frame] | None = None) -> int:
    """曝光居中的一张（有 EXIF 按曝光量，没有按画面亮度）：它和更亮、更暗的几张共同的细节都最多，
    对齐、判断移动物体都最可靠。张数是偶数时，中间两张里挑过曝 / 欠曝少的那张。"""
    if frames is not None and all(f.ev is not None for f in frames):
        key = [f.ev for f in frames]
    else:
        key = [float(np.mean((_gray(s).astype(np.float32) / 255) ** 2.2)) for s in smalls]
    order = sorted(range(len(smalls)), key=lambda i: key[i])
    n = len(order)
    mids = [order[n // 2]] if n % 2 else [order[n // 2 - 1], order[n // 2]]
    return max(mids, key=lambda i: _well_exposed(_gray(smalls[i])))


# ---------------------------------------------------------------------------
# 2. 对齐
# ---------------------------------------------------------------------------

def _features(ref_g: np.ndarray, img_g: np.ndarray) -> np.ndarray | None:
    """SIFT + RANSAC，再用全部内点做一次最小二乘：返回把 img 坐标映射到 ref 坐标的单应矩阵（这一级分辨率上），
    失败返回 None。img_g 应该先亮度匹配到 ref_g。"""
    import cv2
    try:
        sift = cv2.SIFT_create()
    except Exception:
        return None
    m1 = ((ref_g > 4) & (ref_g < 251)).astype(np.uint8) * 255
    m2 = ((img_g > 4) & (img_g < 251)).astype(np.uint8) * 255
    k1, d1 = sift.detectAndCompute(ref_g, m1)
    k2, d2 = sift.detectAndCompute(img_g, m2)
    if d1 is None or d2 is None or len(k1) < 12 or len(k2) < 12:
        return None
    matches = cv2.BFMatcher(cv2.NORM_L2).knnMatch(d2, d1, k=2)
    good = [m for m, n in (p for p in matches if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 12:
        return None
    src = np.float32([k2[m.queryIdx].pt for m in good])
    dst = np.float32([k1[m.trainIdx].pt for m in good])
    H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 1.5, maxIters=5000, confidence=0.999)
    if H is None or inl is None or int(inl.sum()) < 12:
        return None
    inl = inl.ravel().astype(bool)
    H2, _ = cv2.findHomography(src[inl], dst[inl], 0)
    return H2 if H2 is not None else H


def _phase(ref_g: np.ndarray, img_g: np.ndarray) -> np.ndarray | None:
    """相位相关：只求平移（精度只有一两个像素），特征太少（天空、雾、夜景）时给精修当起点。"""
    import cv2
    a, b = ref_g.astype(np.float32), img_g.astype(np.float32)
    win = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(a, b, win)
    if resp < 0.05:
        return None
    return np.array([[1, 0, -dx], [0, 1, -dy], [0, 0, 1]], np.float64)


def _local_norm(x: np.ndarray, sigma: float = 5.0) -> np.ndarray:
    """局部对比度归一化：减去周围的平均亮度、除以周围的起伏。两张曝光不同的照片经过它以后，
    比的是“纹理的形状”而不是亮度，跟踪时不会被色调曲线、噪点带偏。"""
    import cv2
    x = x.astype(np.float32)
    mu = cv2.GaussianBlur(x, (0, 0), sigma)
    sd = np.sqrt(np.maximum(cv2.GaussianBlur(x * x, (0, 0), sigma) - mu * mu, 0))
    return np.clip((x - mu) / (sd + 2.0) * 40 + 128, 0, 255).astype(np.uint8)


class _Tracker:
    """在参考帧上找角点，到按 H 变换过的另一张里用金字塔 LK 光流跟踪（两张都先做局部归一化），
    来回各跟一次、对不上的点扔掉。"""

    def __init__(self, ref_g: np.ndarray, img_g: np.ndarray):
        import cv2
        self.A, self.s = _small(ref_g, _REFINE_SIDE)
        self.B, _ = _small(img_g, _REFINE_SIDE)
        self.lut = _match_lut(self.B, self.A)
        self.An = _local_norm(self.A)
        self.ones = np.full(self.B.shape, 255, np.uint8)
        self.crit = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, 0.003)

    def track(self, Hl: np.ndarray):
        """返回 (变换后坐标, 参考帧坐标)，点太少返回 None。"""
        import cv2
        A = self.A
        hh, ww = A.shape
        Wf = cv2.warpPerspective(self.lut[self.B], Hl, (ww, hh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        Wv = cv2.warpPerspective(self.ones, Hl, (ww, hh), flags=cv2.INTER_NEAREST) > 0
        ok = ((A > 8) & (A < 247) & (Wf > 8) & (Wf < 247) & Wv).astype(np.uint8) * 255
        ok = cv2.erode(ok, np.ones((15, 15), np.uint8))                     # 两张都没过曝 / 欠曝、有画面
        pts = cv2.goodFeaturesToTrack(A, maxCorners=4000, qualityLevel=0.01, minDistance=max(6, min(hh, ww) // 80),
                                      mask=ok, blockSize=7)
        if pts is None or len(pts) < 20:
            return None
        pts = pts.reshape(-1, 2)
        Wn = _local_norm(Wf)
        p1, st, _ = cv2.calcOpticalFlowPyrLK(self.An, Wn, pts, None, winSize=(25, 25), maxLevel=3, criteria=self.crit)
        back, st2, _ = cv2.calcOpticalFlowPyrLK(Wn, self.An, p1, None, winSize=(25, 25), maxLevel=3, criteria=self.crit)
        good = (st.ravel() == 1) & (st2.ravel() == 1) & (np.hypot(*(back - pts).T) < 0.08)
        if good.sum() < 20:
            return None
        return p1[good].astype(np.float32), pts[good].astype(np.float32)

    def residual(self, H: np.ndarray) -> float:
        """按 H 对齐后还剩多少错位（跟踪点位移的中位数，参考帧原图像素）。对不上 / 点太少 = 无穷大。"""
        r = self.track(_to_level(H, self.s))
        if r is None:
            return math.inf
        return float(np.median(np.hypot(*(r[0] - r[1]).T))) / self.s

    def refine(self, H: np.ndarray, iters: int = 3) -> np.ndarray | None:
        """亚像素精修：跟踪 → RANSAC + 最小二乘拟合剩余的修正量 → 重复几次。
        合成测试里（含 4 EV 曝光差、移动物体、起点偏 12 像素）四角误差都在 0.12 像素以内。"""
        import cv2
        hh, ww = self.A.shape
        Hl = _to_level(H, self.s)
        moved = False
        for _ in range(iters):
            r = self.track(Hl)
            if r is None:
                break
            P, Q = r                                                          # 变换后的坐标 → 参考帧坐标
            R, inl = cv2.findHomography(P, Q, cv2.RANSAC, 0.4, maxIters=3000)
            if R is None:
                break
            inl = inl.ravel().astype(bool)
            if inl.sum() < 20:
                break
            R2, _ = cv2.findHomography(P[inl], Q[inl], 0)
            R = R2 if R2 is not None else R
            Hl = R @ Hl
            Hl /= Hl[2, 2]
            moved = True
            if _max_shift(R, ww, hh) < 0.01:
                break
        return _to_full(Hl, self.s) if moved else None


def _coarse(ref_g: np.ndarray, img_g: np.ndarray) -> tuple[np.ndarray, str]:
    """粗对齐：SIFT 特征；不行就相位相关只求平移；再不行就单位矩阵。原图坐标，img → ref。"""
    rg, s1 = _small(ref_g, _ALIGN_SIDE)
    ig, _ = _small(img_g, _ALIGN_SIDE)
    ig = np.clip(_match_lut(ig, rg)[ig] + 0.5, 0, 255).astype(np.uint8)
    H, how = _features(rg, ig), "features"
    if not _sane(H, rg.shape[1], rg.shape[0]):
        H, how = _phase(rg, ig), "phase"
        if not _sane(H, rg.shape[1], rg.shape[0]):
            H, how = np.eye(3), "none"
    return _to_full(H / H[2, 2], s1), how


def estimate_alignment(ref: np.ndarray, img: np.ndarray, guesses: tuple = ()) -> tuple[np.ndarray | None, str, float]:
    """求把 img 对齐到 ref 的单应矩阵（原图坐标，img → ref）。返回 (矩阵或 None, 用的方法, 剩余错位像素)。
    剩余错位是对齐后再跟踪一遍量出来的（正常在 0.3 像素以内）；无穷大 = 没法验证（能跟踪的点太少）。
    先粗：SIFT 特征（不行就相位相关只求平移）；再细：局部归一化的 LK 光流跟踪，到亚像素。
    guesses：别的起点（例如经过曝光相邻那张串起来的结果）；每个起点都精修一遍，留剩余错位最小的。"""
    rg, ig = _gray(ref), _gray(img)
    h, w = ref.shape[:2]
    H0, how = _coarse(rg, ig)
    tr = _Tracker(rg, ig)
    best, best_how, best_r = None, "none", math.inf
    for H, name in [(H0, how)] + [(g, "chain") for g in guesses if g is not None]:
        R = tr.refine(H)
        cand = (R, name + "+lk") if R is not None and _sane(R, w, h) else (H, name)
        if cand[1] == "none":
            continue
        r = tr.residual(cand[0])
        if r < best_r or best is None:
            best, best_how, best_r = cand[0], cand[1], r
    if best is None or best_how == "none":
        return None, "none", math.inf
    return best, best_how, best_r


def warp(img: np.ndarray, H: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """按 H（img → 参考帧）变换；返回 (对齐后的图, 有画面的区域 bool 或 None = 整张)。几乎没动就原样返回。"""
    import cv2
    h, w = img.shape[:2]
    if _max_shift(H, w, h) < _STILL:
        return img, None
    out = cv2.warpPerspective(img, H, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
    valid = cv2.warpPerspective(np.full((h, w), 255, np.uint8), H, (w, h), flags=cv2.INTER_NEAREST,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    valid = cv2.erode(valid, np.ones((7, 7), np.uint8)) > 0       # 边上几像素插值不可靠
    return out, valid


# ---------------------------------------------------------------------------
# 3. 去重影
# ---------------------------------------------------------------------------

def _compare(a: np.ndarray, b: np.ndarray, pair: np.ndarray, thr: float) -> tuple[np.ndarray, np.ndarray]:
    """两张都没削平的通道上双向比较。返回 (比过的像素, 对不上的像素)。
    每个通道单独学亮度映射（只用这个通道两边都可靠的像素），差取两个方向里较小的：暗图提亮会放大噪点，
    只有两边都对不上才算。"""
    tested = np.zeros(pair.shape, bool)
    diff = np.zeros(pair.shape, np.float32)
    for c in range(a.shape[2]):
        ac, bc = a[..., c], b[..., c]
        jm = (ac > 10) & (ac < 245) & (bc > 10) & (bc < 245) & pair
        if jm.sum() < 200:
            continue
        pa = _match_lut(bc, ac, jm)[bc]                 # 由 b 预测 a
        pb = _match_lut(ac, bc, jm)[ac]
        d = np.minimum(np.abs(pa - ac), np.abs(pb - bc))
        diff = np.where(jm, np.maximum(diff, d), diff)
        tested |= jm
    return tested, tested & (diff > thr * 255)


def _clip_conflict(a: np.ndarray, b: np.ndarray, pair: np.ndarray, thr: float) -> np.ndarray:
    """一张在某个通道上过曝（或死黑）、另一张没有：只能单向比——由没削平的那张预测，结果明显不该被削平，
    就是移动物体。只在亮度映射学到过的范围内判断（范围外是外推，不可靠），余量也比双向比较宽。"""
    m = thr * 255 * 1.5
    out = np.zeros(pair.shape, bool)
    for c in range(a.shape[2]):
        ac, bc = a[..., c], b[..., c]
        jm = (ac > 10) & (ac < 245) & (bc > 10) & (bc < 245) & pair
        if jm.sum() < 200:
            continue
        for x, y in ((ac, bc), (bc, ac)):                # x 被削平，用 y 预测 x
            lo, hi = np.percentile(y[jm], (0.5, 99.5))
            sup = (y >= lo) & (y <= hi) & (y > 10) & (y < 245)
            px = _match_lut(y, x, jm)[y]
            out |= sup & (((x >= 248) & (px < 248 - m)) | ((x <= 6) & (px > 6 + m)))
    return out


def ghost_masks(smalls: list[np.ndarray], valids: list[np.ndarray | None], ref: int, evs: list[float],
                thr: float) -> list[np.ndarray | None]:
    """每张一张“移动物体”蒙版（float32 0–1，小分辨率；参考帧为 None）。

    从离参考帧最近的一张开始，逐张和已经确认的几张（参考帧优先）比，先用直方图匹配把对方映射到自己的亮度：
    - 两张都没过曝 / 欠曝：两个方向的差取较小的（暗图提亮会放大噪点，只有两边都对不上才算），超过阈值就是移动物体；
    - 其中一张过曝（或死黑）：它那里已经被削平了，只能单向比——另一张映射过来的值明显不该被削平，也是移动物体。
      没有这一条，参考帧过曝的天空、雪地里走过的人就查不出来。
    """
    import cv2
    n = len(smalls)
    blur = [cv2.GaussianBlur(s, (0, 0), 1.2) for s in smalls]
    valid = [v if v is not None else np.ones(smalls[0].shape[:2], bool) for v in valids]
    order = sorted((i for i in range(n) if i != ref), key=lambda i: (abs(evs[i]), i))
    truth = [ref]
    ghost: list[np.ndarray | None] = [None] * n
    hard: dict[int, np.ndarray] = {ref: np.zeros(smalls[ref].shape[:2], bool)}
    h, w = smalls[ref].shape[:2]
    min_area = max(16, int(h * w * 0.00008))
    grow = max(3, round(max(h, w) * 0.006))
    for i in order:
        tested = np.zeros((h, w), bool)
        bad = np.zeros((h, w), bool)
        for k in truth:
            pair = valid[i] & valid[k] & ~hard[k]
            t2, b2 = _compare(blur[i], blur[k], pair, thr)
            bad |= (b2 & ~tested) | (pair & _clip_conflict(blur[i], blur[k], pair, thr))
            tested |= t2
        m = cv2.morphologyEx(bad.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        cnt, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        keep = np.zeros(cnt, bool)
        keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
        m = keep[lab].astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1)))
        hard[i] = m > 0
        ghost[i] = cv2.GaussianBlur(m.astype(np.float32), (0, 0), grow / 2.5) if m.any() else None
        if ghost[i] is not None:
            ghost[i] = np.maximum(ghost[i], hard[i] * 1.0).astype(np.float32)   # 蒙版里面一定是 1，只在外沿过渡
        truth.append(i)
    return ghost


# ---------------------------------------------------------------------------
# 4. 融合
# ---------------------------------------------------------------------------

def _weights(img: np.ndarray, strip: int = 256) -> np.ndarray:
    """Mertens 权重：对比度（拉普拉斯）× 饱和度 × 曝光适中。分条算，临时内存只有一条的大小。"""
    import cv2
    h = img.shape[0]
    out = np.empty(img.shape[:2], np.float32)
    for y0 in range(0, h, strip):
        a, b = max(0, y0 - 1), min(h, y0 + strip + 1)             # 上下各多一行，拉普拉斯要用到
        f = img[a:b].astype(np.float32) * (1 / 255.0)
        c = np.abs(cv2.Laplacian(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY), cv2.CV_32F))
        mu = f.mean(axis=2, keepdims=True)
        s = np.sqrt(((f - mu) ** 2).mean(axis=2))
        e = np.exp(-((f - 0.5) ** 2) * (1 / 0.08)).prod(axis=2)
        w = c * s * e + 1e-12
        out[y0:min(h, y0 + strip)] = w[y0 - a:y0 - a + min(strip, h - y0)]
    return out


def _levels(h: int, w: int) -> int:
    return max(1, int(math.log2(min(h, w))) - 3)


def fuse(images: list[np.ndarray], masks: list[np.ndarray | None], progress=None, cancel=None) -> np.ndarray:
    """Mertens 曝光融合。masks：每张的参与度（float32 0–1，原尺寸；None = 全部参与）。"""
    import cv2
    h, w = images[0].shape[:2]
    n = len(images)
    W = []
    for i, img in enumerate(images):
        _check(cancel)
        wi = _weights(img)
        if masks[i] is not None:
            wi *= masks[i]
        W.append(wi)
        if progress:
            progress(i + 1, 2 * n)
    total = W[0].copy()
    for wi in W[1:]:
        total += wi
    total[total < 1e-20] = 1.0
    L = _levels(h, w)
    acc: list[np.ndarray] = []
    for i, img in enumerate(images):
        _check(cancel)
        g = img.astype(np.float32) * (1 / 255.0)
        wg = W[i] / total
        W[i] = None
        for k in range(L):                       # 逐级：拉普拉斯层 × 权重的高斯层，直接累加，算完就丢，省内存
            gd = cv2.pyrDown(g)
            wd = cv2.pyrDown(wg)
            lap = cv2.pyrUp(gd, dstsize=(g.shape[1], g.shape[0]))
            np.subtract(g, lap, out=lap)
            lap *= wg[..., None]
            if i == 0:
                acc.append(lap)
            else:
                acc[k] += lap
            del lap
            g, wg = gd, wd
        g *= wg[..., None]
        if i == 0:
            acc.append(g)
        else:
            acc[L] += g
        if progress:
            progress(n + i + 1, 2 * n)
    out = acc[-1]
    for k in range(len(acc) - 2, -1, -1):
        up = cv2.pyrUp(out, dstsize=(acc[k].shape[1], acc[k].shape[0]))
        acc[k + 1] = None
        acc[k] += up
        out = acc[k]
        del up
    out *= 255.0
    out += 0.5
    return np.clip(out, 0, 255).astype(np.uint8)


def _check(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


# ---------------------------------------------------------------------------
# 总流程
# ---------------------------------------------------------------------------

def merge(frames: list[Frame], ref: int | None = None, align: bool = True, deghost: str = "normal",
          progress: Callable[[int, int, str], None] | None = None, cancel=None) -> MergeResult:
    import cv2
    n = len(frames)
    if n < 2:
        raise MergeError(_t("至少要两张照片才能合并曝光。"))
    if n > MAX_FRAMES:
        raise MergeError(_t("一次最多合并 {0} 张。", MAX_FRAMES))
    h, w = frames[0].rgb.shape[:2]
    for f in frames:
        if f.rgb.shape[:2] != (h, w):
            raise MergeError(_t("照片尺寸不一致（{0} 是 {1}×{2}，{3} 是 {4}×{5}）：只能合并同一台相机、同样尺寸拍的一组照片。",
                                frames[0].name, w, h, f.name, f.rgb.shape[1], f.rgb.shape[0]))
    steps = (n - 1 if align else 0) + 2 + 2 * n
    done = [0]

    def step(text: str, k: int = 1) -> None:
        _check(cancel)
        done[0] += k
        if progress:
            progress(min(done[0], steps), steps, text)

    smalls = [_small(f.rgb, _GHOST_SIDE)[0] for f in frames]
    if ref is None or not 0 <= ref < n:
        ref = pick_reference(smalls, frames)
    evs = _relative_evs(smalls, frames, ref)
    aligned: list[np.ndarray] = [f.rgb for f in frames]
    valids: list[np.ndarray | None] = [None] * n
    shifts = [0.0] * n
    skipped: list[int] = []
    if align:
        # 从曝光离参考帧最近的开始；每张先对齐到曝光相邻、已经对齐好的那张（两张的共同细节最多），
        # 再拿串起来的结果直接对参考帧精修。跨度很大的包围（±3 EV 以上）也对得上。
        by_ev = sorted(range(n), key=lambda i: evs[i])
        pos = {i: k for k, i in enumerate(by_ev)}
        Hs: dict[int, np.ndarray] = {ref: np.eye(3)}
        for i in sorted((i for i in range(n) if i != ref), key=lambda i: (abs(pos[i] - pos[ref]), i)):
            _check(cancel)
            j = by_ev[pos[i] + (1 if pos[i] < pos[ref] else -1)]
            chain = None
            if j != ref and j in Hs:
                Hij, _how, _r = estimate_alignment(frames[j].rgb, frames[i].rgb)
                chain = None if Hij is None else Hs[j] @ Hij
            H, _how, resid = estimate_alignment(frames[ref].rgb, frames[i].rgb, (chain,))
            if H is not None and not resid < 1.0 and _clipped(smalls[i]) > 0.7:
                H = None              # 几乎全白 / 全黑、对齐又验证不了：它能贡献的很少，对不准反而添重影
            if H is None:
                skipped.append(i)
            else:
                Hs[i] = H
                shifts[i] = _max_shift(H, w, h)
                aligned[i], valids[i] = warp(frames[i].rgb, H)
            step(_t("对齐第 {0} 张…", i + 1))
    use = [i for i in range(n) if i not in skipped]
    if len(use) < 2:
        raise MergeError(_t("其他照片都没能和参考帧对齐：它们可能不是同一个场景，或者画面变化太大。"))
    thr = DEGHOST.get(deghost)
    ghosts: list[np.ndarray | None] = [None] * n
    ghost_area = 0.0
    if thr is not None:
        sm = [_small(aligned[i], _GHOST_SIDE)[0] for i in range(n)]
        sv = [None if v is None else cv2.resize(v.astype(np.uint8), sm[i].shape[1::-1],
                                                interpolation=cv2.INTER_NEAREST) > 0 for i, v in enumerate(valids)]
        sub = ghost_masks([sm[i] for i in use], [sv[i] for i in use], use.index(ref), [evs[i] for i in use], thr)
        union = None
        for j, i in enumerate(use):
            if sub[j] is not None:
                ghosts[i] = sub[j]
                union = sub[j] > 0.5 if union is None else union | (sub[j] > 0.5)
        if union is not None:
            ghost_area = float(union.mean())
    step(_t("检测移动的物体…"), 2)
    masks = []
    for i in use:
        m = None
        if valids[i] is not None:
            m = valids[i].astype(np.float32)
        if ghosts[i] is not None:
            g = 1.0 - cv2.resize(ghosts[i], (w, h), interpolation=cv2.INTER_LINEAR)
            m = g if m is None else m * g
        masks.append(m)
    base = done[0]

    def fprog(k: int, total: int) -> None:
        done[0] = base + round(k * 2 * n / total)
        _check(cancel)
        if progress:
            progress(min(done[0], steps), steps, _t("融合…"))

    out = fuse([aligned[i] for i in use], masks, fprog, cancel)
    return MergeResult(out, aligned, ref, evs, shifts, skipped, ghost_area)


def ev_label(ev: float) -> str:
    if abs(ev) < 0.05:
        return "0 EV"
    return f"{'+' if ev > 0 else '−'}{abs(ev):.1f} EV"


# ---------------------------------------------------------------------------
# 结果 → 修图文档
# ---------------------------------------------------------------------------

def to_document(res: MergeResult, frames: list[Frame], keep_sources: bool = True):
    """图层（自下而上）：参考帧（可见，按住 \\ 看原图时显示它）、其余源图（隐藏）、合并结果。"""
    from .document import Document, Layer
    h, w = res.image.shape[:2]
    doc = Document(w, h)
    order = [res.ref] + sorted((i for i in range(len(frames)) if i != res.ref and i not in res.skipped),
                               key=lambda i: res.evs[i])
    if not keep_sources:
        order = [res.ref]
    for i in order:
        px = np.dstack([res.aligned[i], np.full((h, w), 255, np.uint8)])
        name = f"{ev_label(res.evs[i])} · {frames[i].name}"
        if i == res.ref:
            name += _t("（参考）")
        l = Layer("pixel", name, px, params={"merge_source": i, "ev": round(res.evs[i], 2), "ref": i == res.ref})
        l.visible = i == res.ref
        doc.layers.append(doc._register(l))
    top = Layer("pixel", _t("合并结果"), np.dstack([res.image, np.full((h, w), 255, np.uint8)]),
                params={"merge_result": True})
    doc.layers.append(doc._register(top))
    doc.active_id = top.id
    return doc


def merge_sources(doc) -> list:
    return [l for l in doc.layers if l.kind == "pixel" and "merge_source" in l.params and l.pixels is not None]


def merge_result_layer(doc):
    return next((l for l in doc.layers if l.params.get("merge_result")), None)


# ---------------------------------------------------------------------------
# 手动修补重影：框出来，挑一张源图补上
# ---------------------------------------------------------------------------

def ghost_patch(source: np.ndarray, target: np.ndarray, sel: np.ndarray, feather: float = 12.0,
                match: bool = True) -> tuple[np.ndarray, np.ndarray, int, int] | None:
    """source / target：同尺寸 uint8 RGBA（对齐后的源图 / 合并结果）；sel：选区 uint8。
    返回 (补丁像素 RGBA, 补丁蒙版, x, y)。补丁只是源图原样的像素（不重采样），match 时亮度按选区周围一圈
    匹配到合并结果，这样补上去不会突然变暗 / 变亮。"""
    import cv2
    from .document import mask_bbox
    hard = sel > 127
    bb = mask_bbox(hard)
    if bb is None:
        return None
    H, W = sel.shape[:2]
    x, y, w, h = bb
    pad = int(math.ceil(feather)) + 2
    ring = max(16, round(0.25 * math.hypot(w, h)))
    x0, y0 = max(0, x - pad - ring), max(0, y - pad - ring)
    x1, y1 = min(W, x + w + pad + ring), min(H, y + h + pad + ring)
    src = source[y0:y1, x0:x1, :3]
    patch = src.copy()
    if match:
        hs = hard[y0:y1, x0:x1].astype(np.uint8)
        near = cv2.dilate(hs, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * ring + 1, 2 * ring + 1))) > 0
        around = near & ~(cv2.dilate(hs, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8)) > 0)
        tgt = target[y0:y1, x0:x1, :3]
        if around.sum() < 400:                            # 选区几乎占满整张：用整张来匹配
            around, src, tgt = None, source[..., :3], target[..., :3]
        luts = _channel_luts(src, tgt, around)
        patch = np.clip(_apply_lut(patch, luts) + 0.5, 0, 255).astype(np.uint8)
    # 补丁只保留选区（含羽化）外接的那块
    bx0, by0 = max(0, x - pad), max(0, y - pad)
    bx1, by1 = min(W, x + w + pad), min(H, y + h + pad)
    m = sel[y0:y1, x0:x1].astype(np.uint8)
    if feather > 0:
        m = cv2.GaussianBlur(m, (0, 0), feather / 2)
        m = np.maximum(m, (hard[y0:y1, x0:x1] * 255).astype(np.uint8))   # 选区里面完全用补丁，只在外沿过渡
    m = m[by0 - y0:by1 - y0, bx0 - x0:bx1 - x0]
    px = patch[by0 - y0:by1 - y0, bx0 - x0:bx1 - x0]
    rgba = np.dstack([px, np.full(px.shape[:2], 255, np.uint8)])
    return np.ascontiguousarray(rgba), np.ascontiguousarray(m), bx0, by0
