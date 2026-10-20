"""内置分析器：日期、地区、相机/来源、画幅形状、色调、清晰度。"""
from __future__ import annotations

import re
from typing import Any

from ..core.dates import resolve_date
from .base import Analyzer, AnalysisContext, PixelInput, register_analyzer


# ---------------------------------------------------------------------------
# 日期
# ---------------------------------------------------------------------------

class DateAnalyzer(Analyzer):
    id = "date"
    label = "拍摄日期"
    order = 10

    def analyze(self, group, ctx: AnalysisContext):
        s = ctx.settings
        # 用组内最早的文件修改时间作为兜底（RAW 通常保留相机写入时间）
        mtimes = [f.mtime_ns for f in group.image_files] or [group.primary_file.mtime_ns]
        names = [group.name] + [f.name for f in group.image_files if f.path != group.primary]
        best = None
        for n in names:
            r = resolve_date(group.meta, n, min(mtimes), ctx.date_patterns,
                             use_filename=s.get("date_use_filename", True),
                             use_mtime=s.get("date_use_mtime", True))
            if best is None or _date_rank(r["source"]) < _date_rank(best["source"]):
                best = r
            if best["source"] in ("exif", "exif_create", "xmp", "filename"):
                break
        return best


def _date_rank(src):
    return {"exif": 0, "exif_create": 1, "xmp": 2, "filename": 3, "mtime": 4}.get(src, 9)


# ---------------------------------------------------------------------------
# 地区
# ---------------------------------------------------------------------------

class GeoAnalyzer(Analyzer):
    id = "geo"
    label = "地区"
    order = 20

    """结果字段：
    has_gps  文件里是否有真实 GPS
    located  是否知道地点（GPS / 手动指定 / 按时间推测 任一来源）
    source   "gps" / "manual" / "inferred" / None
    country province city district cc in_china   地点
    """

    def analyze(self, group, ctx: AnalysisContext):
        lat, lon = group.meta.get("lat"), group.meta.get("lon")
        real = lat is not None and lon is not None
        manual = (ctx.extras.get("manual_places") or {}).get(group.primary_file.sig)
        if manual and manual.get("country") and (not real or manual.get("override")):
            place = {k: manual.get(k) for k in ("country", "province", "city", "district", "cc")}
            return {
                "has_gps": real, "lat": lat, "lon": lon, "located": True, "source": "manual",
                "manual": True, "inferred": False,
                "in_china": place.get("cc") == "CN" or place.get("country") == "中国", **place,
            }
        if not real or ctx.geo is None:
            return {"has_gps": False, "located": False, "source": None, "manual": False, "inferred": False}
        r = ctx.geo.lookup(lat, lon) or {}
        return {"has_gps": True, "lat": lat, "lon": lon, "located": bool(r.get("country")), "source": "gps",
                "manual": False, "inferred": False, **r}


PLACE_KEYS = ("country", "province", "city", "district", "cc", "in_china")


def place_label(geo: dict | None, sep: str = " / ") -> str | None:
    if not geo or not geo.get("country"):
        return None
    parts = []
    for k in ("country", "province", "city", "district"):
        v = geo.get(k)
        if v and (not parts or parts[-1] != v):
            parts.append(v)
    return sep.join(parts)


def infer_missing_locations(groups, minutes: int) -> int:
    """没有地点的照片：若同一时间段（±minutes）内有已知地点的照片（GPS 或手动指定），推测为同一地点。"""
    from bisect import bisect_left

    window = minutes * 60
    known = []
    for g in groups:
        geo = g.feats.get("geo") or {}
        if geo.get("country") and geo.get("source") in ("gps", "manual") and g.date \
                and g.date_source in ("exif", "exif_create", "xmp"):
            known.append((g.date.timestamp(), g))
    known.sort(key=lambda t: t[0])
    times = [t for t, _ in known]
    n = 0
    for g in groups:
        geo = g.feats.get("geo") or {}
        if geo.get("country") or not g.date or g.date_source not in ("exif", "exif_create", "xmp"):
            continue
        t = g.date.timestamp()
        i = bisect_left(times, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(times):
                d = abs(times[j] - t)
                if d <= window and (best is None or d < best[0]):
                    best = (d, known[j][1])
        if best:
            src = best[1].feats["geo"]
            g.feats["geo"] = {**{k: src.get(k) for k in PLACE_KEYS}, "has_gps": False, "located": True,
                              "source": "inferred", "manual": False, "inferred": True,
                              "inferred_from": best[1].name}
            n += 1
    return n


# ---------------------------------------------------------------------------
# 相机 / 镜头 / 来源（相机、手机、截图、其他）
# ---------------------------------------------------------------------------

MAKE_ALIASES = {
    "nikon corporation": "Nikon", "nikon": "Nikon", "canon": "Canon", "sony": "Sony",
    "fujifilm": "Fujifilm", "fuji photo film co., ltd.": "Fujifilm",
    "olympus imaging corp.": "Olympus", "olympus corporation": "Olympus", "olympus optical co.,ltd": "Olympus",
    "om digital solutions": "OM System", "panasonic": "Panasonic", "leica camera ag": "Leica", "leica": "Leica",
    "pentax": "Pentax", "pentax corporation": "Pentax", "ricoh imaging company, ltd.": "Ricoh", "ricoh": "Ricoh",
    "apple": "Apple", "huawei": "Huawei", "xiaomi": "Xiaomi", "redmi": "Xiaomi", "samsung": "Samsung",
    "google": "Google", "oppo": "OPPO", "vivo": "vivo", "honor": "Honor", "oneplus": "OnePlus",
    "realme": "realme", "meizu": "Meizu", "nubia": "nubia", "zte": "ZTE", "motorola": "Motorola",
    "dji": "DJI", "hasselblad": "Hasselblad", "sigma": "Sigma", "gopro": "GoPro", "insta360": "Insta360",
    "eastman kodak company": "Kodak", "kodak": "Kodak", "casio computer co.,ltd.": "Casio", "casio": "Casio",
    "minolta co., ltd.": "Minolta", "konica minolta": "Konica Minolta", "phase one": "Phase One",
}
PHONE_MAKES = {"Apple", "Huawei", "Xiaomi", "Samsung", "Google", "OPPO", "vivo", "Honor", "OnePlus",
               "realme", "Meizu", "nubia", "ZTE", "Motorola", "Nothing", "Lenovo", "Sony Mobile"}
SCREENSHOT_WORDS = re.compile(r"screenshot|screen ?shot|screencapture|snipaste|截屏|截图|屏幕截图|屏幕快照", re.I)


def normalize_make(make: str | None) -> str | None:
    if not make:
        return None
    m = " ".join(make.split())
    hit = MAKE_ALIASES.get(m.lower())
    if hit:
        return hit
    for suf in (" CORPORATION", " Corporation", " CO.,LTD", " CO., LTD.", " Co., Ltd.", " IMAGING CORP.", " Inc."):
        if m.endswith(suf):
            m = m[: -len(suf)]
    first = m.split()[0].lower() if m.split() else ""
    return MAKE_ALIASES.get(first, m.title() if m.isupper() and len(m) > 4 else m)


def normalize_model(make: str | None, model: str | None) -> str | None:
    if not model:
        return None
    mo = " ".join(model.split())
    if make:
        for prefix in {make.lower(), make.split()[0].lower(), (make + " ").lower()}:
            if mo.lower().startswith(prefix):
                mo = mo[len(prefix):].strip(" -_")
                break
    if not mo:
        return make
    return f"{make} {mo}" if make else mo


class CameraAnalyzer(Analyzer):
    id = "camera"
    label = "相机与来源"
    order = 30

    def analyze(self, group, ctx: AnalysisContext):
        m = group.meta
        make = normalize_make(m.get("make"))
        model = normalize_model(make, m.get("model"))
        lens = m.get("lens")
        names = " ".join(f.name for f in group.image_files)
        uc = (m.get("user_comment") or "").lower()
        has_camera = bool(make or model)
        screenshot = bool(SCREENSHOT_WORDS.search(names) or uc == "screenshot")
        if not screenshot and not has_camera and group.kind == "png" and not m.get("dt_original"):
            screenshot = True
        if screenshot:
            source = "screenshot"
        elif make in PHONE_MAKES or (make == "Sony" and model and "XQ-" in model):
            source = "phone"
        elif has_camera:
            source = "camera"
        else:
            source = "other"
        if make == "DJI" and source == "camera":
            source = "drone"
        return {"make": make, "model": model, "lens": lens, "source": source}


SOURCE_LABELS = {"camera": "相机", "phone": "手机", "drone": "无人机", "screenshot": "截图", "other": "其他（无相机信息）"}


# ---------------------------------------------------------------------------
# 画幅形状与分辨率
# ---------------------------------------------------------------------------

class ShapeAnalyzer(Analyzer):
    id = "shape"
    label = "画幅"
    order = 40

    def analyze(self, group, ctx: AnalysisContext):
        m = group.meta
        w, h = m.get("width"), m.get("height")
        src = "meta"
        if w and h and w > 64 and h > 64:
            if int(m.get("orientation") or 1) in (5, 6, 7, 8):
                w, h = h, w
        else:
            pw, ph = group.feat("preview", "w"), group.feat("preview", "h")
            if pw and ph:
                w, h, src = pw, ph, "preview"
            else:
                return {"orientation": None, "mp": None}
        ratio = w / h
        if 0.95 <= ratio <= 1.05:
            ori = "square"
        elif ratio > 1:
            ori = "landscape"
        else:
            ori = "portrait"
        mp = round(w * h / 1e6, 1) if src == "meta" else None
        if ratio >= 2.2 or ratio <= 1 / 2.2:
            ori = "pano" if max(w, h) > 3000 else ori
        return {"orientation": ori, "w": w, "h": h, "mp": mp, "ratio": round(ratio, 3), "from": src}


ORIENTATION_LABELS = {"landscape": "横幅", "portrait": "竖幅", "square": "方形", "pano": "全景"}


# ---------------------------------------------------------------------------
# 色调（像素）
# ---------------------------------------------------------------------------

class ColorAnalyzer(Analyzer):
    id = "color"
    label = "色调"
    stage = "pixel"
    version = 3     # v3：黑白判定只看画面中足够亮的部分（很暗的彩色夜景不再被当成黑白）

    def analyze_pixels(self, px: PixelInput, settings):
        import cv2
        import numpy as np

        img = px.small(96).astype(np.float32) / 255.0
        lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB).reshape(-1, 3)
        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV).reshape(-1, 3)
        L = lab[:, 0]
        a, b = lab[:, 1], lab[:, 2]
        chroma = np.hypot(a, b)
        H, S, V = hsv[:, 0], hsv[:, 1], hsv[:, 2]

        # 冷暖：按色相统计，权重为色度（越鲜艳的颜色影响越大）
        w = chroma * ((S > 0.15) & (V > 0.12))
        tot = float(w.sum()) + 1e-6
        warm = ((H < 75) | (H >= 330))
        cool_full = (H >= 160) & (H < 290)
        green = (H >= 90) & (H < 160)
        warm_ratio = float(w[warm].sum()) / tot
        cool_ratio = (float(w[cool_full].sum()) + 0.5 * float(w[green].sum())) / tot

        # 主色：Lab 空间 k-means
        data = lab.astype(np.float32)
        k = 5
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 12, 0.5)
        cv2.setRNGSeed(7)
        _, labels, centers = cv2.kmeans(data, k, None, criteria, 2, cv2.KMEANS_PP_CENTERS)
        counts = np.bincount(labels.ravel(), minlength=k).astype(np.float64)
        shares = counts / counts.sum()
        palette = []
        for i in np.argsort(-shares):
            c = centers[i].reshape(1, 1, 3).astype(np.float32)
            rgb = cv2.cvtColor(c, cv2.COLOR_LAB2RGB).reshape(3)
            rgb = [int(round(float(x) * 255)) for x in np.clip(rgb, 0, 1)]
            cl, ca, cb = (float(x) for x in centers[i])
            palette.append({"rgb": rgb, "share": round(float(shares[i]), 3),
                            "L": round(cl, 1), "chroma": round(float(np.hypot(ca, cb)), 1),
                            "hue": round(float(_rgb_hue(rgb)), 1)})
        return {
            "mean_l": round(float(L.mean()), 1),
            "median_l": round(float(np.median(L)), 1),
            "std_l": round(float(L.std()), 1),
            "chroma_mean": round(float(chroma.mean()), 2),
            "chroma_p90": round(float(np.percentile(chroma, 90)), 2),
            # 只统计足够亮的像素（L≥18）：暗部天然几乎没有色度，会把暗的彩色照片误判成黑白
            "lit_frac": round(float((L >= 18).mean()), 3),
            "chroma_hi_lit": (round(float(np.percentile(chroma[L >= 18], 99.5)), 2)
                               if int((L >= 18).sum()) >= 50 else None),
            "sat_mean": round(float(S.mean()), 3),
            "warm_ratio": round(warm_ratio, 3),
            "cool_ratio": round(cool_ratio, 3),
            "mean_a": round(float(a.mean()), 2),
            "mean_b": round(float(b.mean()), 2),
            "palette": palette,
        }


def _rgb_hue(rgb) -> float:
    import colorsys
    r, g, b = (x / 255 for x in rgb)
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360


# 色相分类：(名称, 起始角度)，按 HSV 色相
HUE_BUCKETS = [("红", 345), ("橙", 15), ("黄", 42), ("黄绿", 65), ("绿", 90), ("青", 155),
               ("蓝", 195), ("紫", 255), ("粉紫", 290)]


def hue_name(h: float) -> str:
    h = h % 360
    names = sorted(HUE_BUCKETS, key=lambda x: x[1])
    cur = names[-1][0]   # 红（跨 0°）
    for n, start in names:
        if h >= start:
            cur = n
    return cur


def classify_color(feat: dict[str, Any] | None, s: dict[str, Any]) -> dict[str, str] | None:
    """把色调原始特征按当前阈值归类（阈值可在设置里调，不需要重新分析）。"""
    if not feat:
        return None
    mono_thr = float(s.get("color_mono_chroma", 7.0))
    if "lit_frac" in feat:
        # 真正的黑白照片：亮部里 99.5% 的像素都没有颜色；占画面 0.5% 以上的人脸、灯光就足以排除
        hi = feat.get("chroma_hi_lit")
        mono = hi is not None and feat["lit_frac"] >= 0.03 and hi < mono_thr
    else:
        mono = feat["chroma_p90"] < mono_thr
    low_sat = feat["chroma_mean"] < float(s.get("color_low_sat_chroma", 9.0))
    margin = float(s.get("color_warm_margin", 0.12))
    if mono:
        warmth = "黑白"
    elif low_sat:
        warmth = "中性"
    elif feat["warm_ratio"] - feat["cool_ratio"] > margin:
        warmth = "暖色调"
    elif feat["cool_ratio"] - feat["warm_ratio"] > margin:
        warmth = "冷色调"
    else:
        warmth = "中性"
    L = feat.get("median_l", feat["mean_l"])
    if L < float(s.get("color_dark_l", 35)):
        tone = "暗调"
    elif L > float(s.get("color_bright_l", 65)):
        tone = "亮调"
    else:
        tone = "中间调"
    # 主色：占比够大的最鲜艳簇
    main = None
    if not mono:
        best = None
        for p in feat.get("palette", []):
            if p["chroma"] < 12 or p["share"] < 0.08:
                continue
            score = p["share"] * (p["chroma"] ** 0.6)
            if best is None or score > best[0]:
                best = (score, p)
        if best:
            p = best[1]
            main = hue_name(p["hue"])
            if main in ("橙", "红") and p["L"] < 45 and p["chroma"] < 40:
                main = "棕"
    if main is None:
        main = "黑白灰" if mono else "低饱和"
    return {"warmth": warmth, "tone": tone, "main": main}


# ---------------------------------------------------------------------------
# 清晰度（像素）
# ---------------------------------------------------------------------------

class BlurAnalyzer(Analyzer):
    """拉普拉斯方差。分 4×4 小块计算、取最清晰的两块平均，
    这样"主体清晰、背景虚化"的照片不会被误判为模糊。"""
    id = "blur"
    label = "清晰度"
    stage = "pixel"
    version = 2

    def analyze_pixels(self, px: PixelInput, settings):
        import cv2

        gray = px.gray(1024)
        h, w = gray.shape
        lap = cv2.Laplacian(gray, cv2.CV_64F)
        tiles = []
        n = 4
        for i in range(n):
            for j in range(n):
                t = lap[i * h // n:(i + 1) * h // n, j * w // n:(j + 1) * w // n]
                if t.size:
                    tiles.append(float(t.var()))
        tiles.sort()
        score = sum(tiles[-2:]) / 2 if len(tiles) >= 2 else float(lap.var())
        return {
            "score": round(score, 1),
            "global": round(float(lap.var()), 1),
            "res": int(max(h, w)),
            "reliable": bool(max(h, w) >= 640),
        }


def blur_state(group, threshold: float) -> str:
    """返回 sharp / blurry / unknown。"""
    sc = group.feat("blur", "score")
    if sc is None or not group.feat("blur", "reliable", False):
        return "unknown"
    return "blurry" if sc < threshold else "sharp"


BLUR_LABELS = {"sharp": "清晰", "blurry": "模糊", "unknown": "无法判断"}


def register_builtin() -> None:
    for a in (DateAnalyzer(), GeoAnalyzer(), CameraAnalyzer(), ShapeAnalyzer(), ColorAnalyzer(), BlurAnalyzer()):
        register_analyzer(a)
