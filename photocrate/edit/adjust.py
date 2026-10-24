"""调整：一组滑块参数 → 像素。调整图层、滤镜预设都用这一套。

输入输出都是 float32 RGB（0–1，与原片同一色彩空间，通常是 sRGB）。处理顺序与 Lightroom 的「基本」面板接近：
白平衡 → 曝光（线性空间）→ 色阶 → 高光 / 阴影（按模糊后的亮度分区，所以是“局部”的）→ 白色 / 黑色色阶、对比度、褪色
→ 清晰度 → 曲线 → HSL → 饱和度 / 自然饱和度 → 分离色调 → 反相 → 暗角 → 锐化 → 降噪 → 颗粒。

有半径的操作（高光阴影的分区、清晰度、锐化、降噪、颗粒）按 RenderCtx.scale 换算：在缩小的代理图上预览时，
效果和原尺寸导出时看起来一致。只渲染画布的一部分时，RenderCtx 带上这一块在画布里的位置（暗角要用），
调用方按 margin() 在四周多算一圈再裁掉，避免模糊在边上露馅。
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass

import numpy as np

HSL_BANDS = [("red", "红", 0.0), ("orange", "橙", 30.0), ("yellow", "黄", 60.0), ("green", "绿", 120.0),
             ("aqua", "青", 180.0), ("blue", "蓝", 225.0), ("purple", "紫", 270.0), ("magenta", "洋红", 315.0)]

# (键, 名称, 最小, 最大, 小数位)
SLIDERS: dict[str, tuple[str, float, float, int]] = {
    "exposure": ("曝光", -4.0, 4.0, 2),
    "contrast": ("对比度", -100, 100, 0),
    "highlights": ("高光", -100, 100, 0),
    "shadows": ("阴影", -100, 100, 0),
    "whites": ("白色色阶", -100, 100, 0),
    "blacks": ("黑色色阶", -100, 100, 0),
    "temp": ("色温", -100, 100, 0),
    "tint": ("色调", -100, 100, 0),
    "vibrance": ("自然饱和度", -100, 100, 0),
    "saturation": ("饱和度", -100, 100, 0),
    "clarity": ("清晰度", -100, 100, 0),
    "sharpen": ("锐化", 0, 100, 0),
    "denoise": ("降噪", 0, 100, 0),
    "vignette": ("暗角", -100, 100, 0),
    "grain": ("颗粒", 0, 100, 0),
    "fade": ("褪色", 0, 100, 0),
    "lv_lo": ("输入黑场", 0, 254, 0),
    "lv_hi": ("输入白场", 1, 255, 0),
    "lv_gamma": ("中间调", 0.10, 9.99, 2),
    "lv_out_lo": ("输出黑场", 0, 255, 0),
    "lv_out_hi": ("输出白场", 0, 255, 0),
    "sp_hi_hue": ("高光色相", 0, 360, 0),
    "sp_hi_sat": ("高光饱和度", 0, 100, 0),
    "sp_sh_hue": ("阴影色相", 0, 360, 0),
    "sp_sh_sat": ("阴影饱和度", 0, 100, 0),
    "sp_balance": ("平衡", -100, 100, 0),
}

GROUPS = [
    ("基本", ["exposure", "contrast", "highlights", "shadows", "whites", "blacks"]),
    ("颜色", ["temp", "tint", "vibrance", "saturation"]),
    ("细节", ["clarity", "sharpen", "denoise"]),
    ("效果", ["vignette", "grain", "fade"]),
    ("色阶", ["lv_lo", "lv_gamma", "lv_hi", "lv_out_lo", "lv_out_hi"]),
    ("分离色调", ["sp_hi_hue", "sp_hi_sat", "sp_sh_hue", "sp_sh_sat", "sp_balance"]),
]

CURVE_CHANNELS = [("rgb", "RGB"), ("r", "红"), ("g", "绿"), ("b", "蓝")]
IDENTITY_CURVE = [[0, 0], [255, 255]]


def default_params() -> dict:
    p = {k: 0.0 for k in SLIDERS}
    p.update(lv_hi=255.0, lv_gamma=1.0, lv_out_hi=255.0, sp_hi_hue=40.0, sp_sh_hue=210.0)
    p["curve"] = {c: [list(pt) for pt in IDENTITY_CURVE] for c, _ in CURVE_CHANNELS}
    p["hsl"] = {k: [0.0, 0.0, 0.0] for k, _, _ in HSL_BANDS}
    p["invert"] = False
    return p


def normalize(params: dict | None) -> dict:
    """补齐缺的键（旧工程文件、预设只写了几个键）。"""
    p = default_params()
    for k, v in (params or {}).items():
        if k == "curve" and isinstance(v, dict):
            p["curve"].update({c: [list(pt) for pt in pts] for c, pts in v.items() if c in p["curve"]})
        elif k == "hsl" and isinstance(v, dict):
            p["hsl"].update({b: list(x) for b, x in v.items() if b in p["hsl"]})
        else:
            p[k] = v
    return p


def _curve_is_identity(pts) -> bool:
    return all(abs(float(x) - float(y)) < 0.5 for x, y in pts)


def is_identity(p: dict) -> bool:
    d = default_params()
    for k in SLIDERS:
        if abs(float(p.get(k, d[k])) - float(d[k])) > 1e-6:
            return False
    if any(not _curve_is_identity(v) for v in (p.get("curve") or {}).values()):
        return False
    if any(any(abs(float(x)) > 1e-6 for x in v) for v in (p.get("hsl") or {}).values()):
        return False
    return not p.get("invert")


@dataclass
class RenderCtx:
    """scale = 渲染像素 / 画布像素；x0, y0 = 这块数组左上角在（缩放后）画布里的位置；cw, ch = 画布原尺寸。"""
    scale: float = 1.0
    cw: int = 0
    ch: int = 0
    x0: int = 0
    y0: int = 0

    @property
    def long(self) -> float:
        return max(self.cw, self.ch) * self.scale


def _blur(img, sigma: float):
    """高斯模糊。半径很大时先缩小、模糊、再放大：结果几乎一样（这里的大半径只用来分区，本来就很平滑），快几十倍。"""
    import cv2
    if sigma < 0.3:
        return img
    if sigma <= 8:
        return cv2.GaussianBlur(img, (0, 0), sigmaX=sigma, sigmaY=sigma, borderType=cv2.BORDER_REFLECT)
    h, w = img.shape[:2]
    k = max(2, int(sigma // 4))
    small = cv2.resize(img, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigmaX=sigma / k, sigmaY=sigma / k, borderType=cv2.BORDER_REFLECT)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


_LUMA = np.array([[0.2126, 0.7152, 0.0722]], np.float32)


def _luma(rgb) -> np.ndarray:
    import cv2
    return cv2.transform(np.ascontiguousarray(rgb, dtype=np.float32), _LUMA)


def _sigmas(p: dict, rc: RenderCtx) -> list[float]:
    s = []
    if p.get("highlights") or p.get("shadows"):
        s.append(rc.long * 0.012)
    if p.get("clarity"):
        s.append(rc.long * 0.006)
    if p.get("sharpen"):
        s.append(max(0.6, 1.0 * rc.scale))
    if p.get("denoise"):
        s.append(max(1.0, 3.0 * rc.scale))
    return s


def margin(p: dict, rc: RenderCtx) -> int:
    """只渲染一部分画布时，四周要多算多少像素（渲染像素）。"""
    s = _sigmas(p, rc)
    return int(math.ceil(3 * max(s))) + 2 if s else 0


def spatial(p: dict) -> bool:
    return any(p.get(k) for k in ("highlights", "shadows", "clarity", "sharpen", "denoise", "vignette", "grain"))


# ---------------------------------------------------------------------------
# 曲线
# ---------------------------------------------------------------------------

def curve_lut(points, n: int = 4096) -> np.ndarray:
    """单调三次插值（Fritsch–Carlson），不会在控制点之间过冲。points 是 0–255 的 [[x, y], …]。"""
    pts = sorted((float(x) / 255.0, float(y) / 255.0) for x, y in points)
    xs, ys = [], []
    for x, y in pts:
        if xs and x - xs[-1] < 1e-4:
            ys[-1] = y
            continue
        xs.append(x)
        ys.append(y)
    t = np.linspace(0.0, 1.0, n, dtype=np.float64)
    if len(xs) < 2:
        return np.clip(t if not ys else np.full(n, ys[0]), 0, 1).astype(np.float32)
    X, Y = np.array(xs), np.array(ys)
    d = np.diff(Y) / np.diff(X)
    m = np.empty(len(X))
    m[0], m[-1] = d[0], d[-1]
    m[1:-1] = (d[:-1] + d[1:]) / 2
    for i in range(len(d)):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
        elif i > 0 and d[i - 1] * d[i] < 0:
            m[i] = 0.0
    for i in range(len(d)):
        if d[i] == 0:
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        s = a * a + b * b
        if s > 9:
            k = 3 / math.sqrt(s)
            m[i], m[i + 1] = k * a * d[i], k * b * d[i]
    idx = np.clip(np.searchsorted(X, t) - 1, 0, len(X) - 2)
    h = X[idx + 1] - X[idx]
    u = (t - X[idx]) / h
    h00 = (2 * u ** 3 - 3 * u ** 2 + 1)
    h10 = (u ** 3 - 2 * u ** 2 + u)
    h01 = (-2 * u ** 3 + 3 * u ** 2)
    h11 = (u ** 3 - u ** 2)
    out = h00 * Y[idx] + h10 * h * m[idx] + h01 * Y[idx + 1] + h11 * h * m[idx + 1]
    out = np.where(t < X[0], Y[0], np.where(t > X[-1], Y[-1], out))
    return np.clip(out, 0, 1).astype(np.float32)


def _apply_lut(x, lut: np.ndarray):
    """查表（就近取，表有 4096 项，误差 < 0.1/255）。x 可以是任意形状的 0–1 数组。"""
    n = len(lut) - 1
    idx = x * np.float32(n)
    idx += np.float32(0.5)
    np.clip(idx, 0, n, out=idx)
    return lut[idx.astype(np.int32)]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _tone_lut(p: dict, n: int = 4096) -> np.ndarray | None:
    c, w, b, fade = (float(p.get(k) or 0) / 100 for k in ("contrast", "whites", "blacks", "fade"))
    if not (c or w or b or fade):
        return None
    x = np.linspace(0, 1, n, dtype=np.float32)
    y = x + 0.12 * b * (1 - x) ** 4 + 0.12 * w * x ** 4
    y = np.clip(y, 0, 1)
    if c > 0:
        s = y * y * (3 - 2 * y)
        y = y + c * (s - y)
    elif c < 0:
        y = 0.5 + (y - 0.5) * (1 + 0.5 * c)
    if fade:
        y = fade * 0.14 + y * (1 - fade * 0.2)
    return np.clip(y, 0, 1).astype(np.float32)


def _hue_rgb(hue: float) -> np.ndarray:
    import colorsys
    return np.array(colorsys.hsv_to_rgb((hue % 360) / 360.0, 1.0, 1.0), dtype=np.float32)


def _hsl(rgb, p: dict):
    import cv2
    bands = p.get("hsl") or {}
    vals = [bands.get(k, [0, 0, 0]) for k, _, _ in HSL_BANDS]
    if not any(any(abs(float(v)) > 1e-6 for v in b) for b in vals):
        return rgb
    hsv = cv2.cvtColor(np.clip(rgb, 0, 1).astype(np.float32), cv2.COLOR_RGB2HSV)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    centers = [c for _, _, c in HSL_BANDS]
    dh = np.zeros_like(H)
    ds = np.zeros_like(H)
    dl = np.zeros_like(H)
    n = len(centers)
    for i, (hv, sv, lv) in enumerate(vals):
        if not (hv or sv or lv):
            continue
        c, prev, nxt = centers[i], centers[i - 1], centers[(i + 1) % n]
        left = (c - prev) % 360 or 360
        right = (nxt - c) % 360 or 360
        d = (H - c + 180) % 360 - 180               # -180..180，正 = 在中心右边
        w = np.where(d >= 0, 1 - d / right, 1 + d / left)
        w = np.clip(w, 0, 1)
        if hv:
            dh += w * float(hv) * 0.3
        if sv:
            ds += w * float(sv) / 100
        if lv:
            dl += w * float(lv) / 100
    hsv[..., 0] = (H + dh) % 360
    sat0 = S.copy()
    hsv[..., 1] = np.clip(S * (1 + ds), 0, 1)
    hsv[..., 2] = np.clip(V * (1 + 0.6 * dl * np.sqrt(sat0)), 0, 1)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)


def develop(rgb: np.ndarray, p: dict, rc: RenderCtx | None = None) -> np.ndarray:
    """按参数处理一块 float32 RGB（不修改输入）。"""
    import cv2

    rc = rc or RenderCtx(1.0, rgb.shape[1], rgb.shape[0])
    if not rc.cw:
        rc = RenderCtx(rc.scale, int(rgb.shape[1] / rc.scale), int(rgb.shape[0] / rc.scale), rc.x0, rc.y0)
    g = lambda k: float(p.get(k) or 0)          # noqa: E731
    out = rgb.astype(np.float32, copy=True)

    # 白平衡 + 曝光：在线性空间里乘（每个通道一张查找表）
    ev, temp, tint = g("exposure"), g("temp") / 100, g("tint") / 100
    if ev or temp or tint:
        gains = np.array([1 + 0.28 * temp, 1 - 0.22 * tint, 1 - 0.28 * temp], dtype=np.float64)
        gains /= gains[0] * 0.2126 + gains[1] * 0.7152 + gains[2] * 0.0722
        gains *= 2.0 ** ev
        lin = np.linspace(0, 1, 4096) ** 2.2
        src, out = out, np.empty_like(out)
        for i in range(3):
            lut = (np.clip(lin * gains[i], 0, 1) ** (1 / 2.2)).astype(np.float32)
            out[..., i] = _apply_lut(src[..., i], lut)

    # 色阶（三个通道同一张表）
    lo, hi, gam = g("lv_lo") / 255, (p.get("lv_hi", 255) or 255) / 255, float(p.get("lv_gamma", 1) or 1)
    olo, ohi = g("lv_out_lo") / 255, float(p.get("lv_out_hi", 255)) / 255
    if lo > 0 or hi < 1 or abs(gam - 1) > 1e-3 or olo > 0 or ohi < 1:
        x = np.clip((np.linspace(0, 1, 4096) - lo) / max(1e-3, hi - lo), 0, 1)
        if abs(gam - 1) > 1e-3:
            x = np.power(x, 1 / max(0.01, gam))
        out = _apply_lut(out, (olo + x * (ohi - olo)).astype(np.float32))

    # 影调（按亮度改，乘回 RGB 保持色相；很暗的像素改用加法，免得“褪色”抬不起纯黑）
    sh, hl = g("shadows") / 100, g("highlights") / 100
    lut = _tone_lut(p)
    clar = g("clarity") / 100
    if sh or hl or lut is not None or clar:
        L = _luma(out)
        np.clip(L, 0, 1, out=L)
        L2 = L
        if sh or hl:
            base = _blur(L, rc.long * 0.012)
            base *= 0.6
            base += 0.4 * L
            e = np.ones_like(L)
            if sh:
                ws = np.clip(1 - base / 0.55, 0, 1)
                e -= (0.75 * sh) * ws * ws
            if hl:
                wh = np.clip((base - 0.45) / 0.55, 0, 1)
                e -= (0.75 * hl) * wh * wh
            np.clip(e, 0.25, 3.0, out=e)
            L2 = np.power(np.maximum(L, 1e-5), e)
        if lut is not None:
            L2 = _apply_lut(L2, lut)
        if clar:
            detail = L2 - _blur(L2, rc.long * 0.006)
            L2 = L2 + (clar * 1.4) * detail * (0.25 + 3 * L2 * (1 - L2))
        np.clip(L2, 0, 1, out=L2)
        # out·ratio·w + (out + ΔL)·(1−w) = out·k + b，只做两次三通道运算
        w = np.clip(L * (1 / 0.06), 0, 1)
        k = (L2 + 1e-3) / (L + 1e-3) * w + (1 - w)
        b = (L2 - L) * (1 - w)
        out = out * k[..., None] + b[..., None]

    # 曲线
    curves = p.get("curve") or {}
    master = curves.get("rgb")
    if master and not _curve_is_identity(master):
        out = _apply_lut(out, curve_lut(master))
    for i, ch in enumerate("rgb"):
        pts = curves.get(ch)
        if pts and not _curve_is_identity(pts):
            out[..., i] = _apply_lut(out[..., i], curve_lut(pts))

    out = _hsl(out, p)

    # 饱和度 / 自然饱和度
    sat, vib = g("saturation") / 100, g("vibrance") / 100
    if sat or vib:
        L = _luma(out)[..., None]
        k = np.full(out.shape[:2], 1 + sat, dtype=np.float32)
        if vib:
            r, gg, b = out[..., 0], out[..., 1], out[..., 2]
            pxs = np.clip(np.maximum(np.maximum(r, gg), b) - np.minimum(np.minimum(r, gg), b), 0, 1)
            k *= 1 + vib * (1 - pxs) ** 2 * (1.0 if vib > 0 else 0.8)
        out = L + (out - L) * k[..., None]

    # 分离色调
    hs, ss = g("sp_hi_sat") / 100, g("sp_sh_sat") / 100
    if hs or ss:
        L = np.clip(_luma(out), 0, 1)
        bal = g("sp_balance") / 100
        if hs:
            t = _hue_rgb(g("sp_hi_hue"))
            t = t - (t @ np.array([0.2126, 0.7152, 0.0722], np.float32))
            out += (hs * 0.3 * np.power(L, 1.5 - bal))[..., None] * t
        if ss:
            t = _hue_rgb(g("sp_sh_hue"))
            t = t - (t @ np.array([0.2126, 0.7152, 0.0722], np.float32))
            out += (ss * 0.3 * np.power(1 - L, 1.5 + bal))[..., None] * t

    if p.get("invert"):
        out = 1 - np.clip(out, 0, 1)

    # 暗角（按画布位置，不随裁出来的这一块变化）
    vig = g("vignette") / 100
    if vig:
        h, w = out.shape[:2]
        sx, sy = rc.cw * rc.scale / 2, rc.ch * rc.scale / 2
        xs = ((np.arange(w, dtype=np.float32) + rc.x0 + 0.5) - sx) / max(1.0, sx)
        ys = ((np.arange(h, dtype=np.float32) + rc.y0 + 0.5) - sy) / max(1.0, sy)
        r = np.sqrt(xs[None, :] ** 2 + ys[:, None] ** 2) / math.sqrt(2)
        m = np.clip((r - 0.25) / 0.75, 0, 1)
        m = (m * m * (3 - 2 * m))[..., None]
        if vig < 0:
            out = out * (1 + vig * 0.95 * m)
        else:
            out = out + (1 - out) * vig * 0.9 * m

    # 锐化（只锐化亮度，避免彩色噪点）
    shp = g("sharpen") / 100
    if shp:
        L = _luma(out)
        detail = L - _blur(L, max(0.6, 1.0 * rc.scale))
        out = out + (shp * 1.6 * detail)[..., None]

    dn = g("denoise") / 100
    if dn:
        src = np.clip(out, 0, 1).astype(np.float32)
        d = int(max(3, round(5 * max(rc.scale, 0.3)))) | 1
        flt = cv2.bilateralFilter(src, d, 0.03 + 0.12 * dn, max(1.0, 3.0 * rc.scale))
        out = src + (flt - src) * min(1.0, dn * 1.3)

    gr = g("grain") / 100
    if gr:
        h, w = out.shape[:2]
        rng = np.random.default_rng(1234 + rc.x0 * 7 + rc.y0 * 13)
        noise = rng.standard_normal((h, w), dtype=np.float32)
        noise = _blur(noise, 0.6 * max(0.6, rc.scale))
        noise /= max(1e-3, float(noise.std()))
        L = np.clip(_luma(out), 0, 1)
        out = out + (noise * gr * 0.07 * (0.3 + 2.8 * L * (1 - L)))[..., None]

    return np.clip(out, 0, 1).astype(np.float32, copy=False)


# ---------------------------------------------------------------------------
# 滤镜预设（新建调整图层时一键套用；都可以再微调）
# ---------------------------------------------------------------------------

def _p(**kw) -> dict:
    p = default_params()
    for k, v in kw.items():
        if k == "curve_rgb":
            p["curve"]["rgb"] = v
        elif k == "hsl":
            p["hsl"].update(v)
        else:
            p[k] = float(v) if not isinstance(v, bool) else v
    return p


PRESETS: dict[str, dict] = {
    "鲜明": _p(contrast=20, vibrance=35, saturation=8, clarity=12),
    "清新": _p(exposure=0.2, contrast=-12, highlights=-25, shadows=25, temp=-8, vibrance=20, saturation=-5),
    "日系": _p(exposure=0.35, contrast=-28, highlights=-35, shadows=30, temp=-6, tint=4, saturation=-15, vibrance=12,
              fade=25, curve_rgb=[[0, 22], [128, 136], [255, 248]]),
    "暖阳": _p(temp=25, tint=4, vibrance=10, sp_hi_hue=40, sp_hi_sat=18, sp_sh_hue=25, sp_sh_sat=6),
    "冷调": _p(temp=-24, tint=-3, saturation=-6, sp_sh_hue=210, sp_sh_sat=12),
    "胶片": _p(contrast=-6, saturation=-12, fade=35, grain=25, sp_hi_hue=45, sp_hi_sat=12, sp_sh_hue=200,
              sp_sh_sat=10, curve_rgb=[[0, 18], [64, 60], [192, 200], [255, 245]]),
    "复古": _p(temp=16, saturation=-28, fade=40, vignette=-28, grain=22, sp_hi_hue=45, sp_hi_sat=16),
    "电影感": _p(contrast=16, saturation=-10, highlights=-15, sp_hi_hue=35, sp_hi_sat=28, sp_sh_hue=195, sp_sh_sat=30,
               sp_balance=10, vignette=-15),
    "黑白": _p(saturation=-100, contrast=18, clarity=15),
    "高对比黑白": _p(saturation=-100, contrast=55, blacks=-25, vignette=-30, grain=15, clarity=20),
    "夜景": _p(highlights=-45, shadows=22, temp=-10, clarity=20, denoise=35, vibrance=10),
    "美食": _p(temp=12, vibrance=28, contrast=10, sharpen=30, shadows=10),
    "人像柔肤": _p(clarity=-25, exposure=0.15, highlights=-15, vibrance=-5,
                  hsl={"orange": [0, -10, 12], "red": [0, -5, 5]}),
    "风光": _p(clarity=25, vibrance=30, highlights=-40, shadows=30, contrast=10,
              hsl={"blue": [0, 15, -12], "aqua": [0, 10, -5], "green": [-5, 10, 0]}),
}


def preset(name: str) -> dict:
    return copy.deepcopy(PRESETS[name])
