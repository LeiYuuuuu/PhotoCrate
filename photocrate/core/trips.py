"""旅行自动分段：把照片按“离家 + 时间连续”切成一次次旅行，并自动命名。

思路
----
1. 每张照片判断状态：
   - home：在“常住地”（设置里可填多个，如 博卡拉顿、邵阳；按坐标 + 半径判断，没有坐标时按地名判断）；
   - away：有地点、但不在常住地；
   - unknown：没有地点（没有 GPS、也没有匹配 / 手动 / 推测到地点）。
   没有设置常住地时，所有有地点的照片都算 away（这时按时间间隔切出的是一次次“出行 / 活动”）。
2. 只用 away 照片按时间排序切段：相邻两张间隔超过 gap_hours（默认 36 小时）、或中间出现 home 照片，就开始新的一段；
   但前后两张在同一个“非常住地国家”时（人还在国外），间隔放宽到 2 倍。
   away 照片少于 min_photos 张的段丢弃（偶尔一张定位偏差不算旅行）。
3. unknown 照片如果落在某段的时间范围内（前后各放宽 tail_hours，默认 12 小时）且这段时间里没有 home 照片，归入该段。
4. 命名：起始日期 + 国家（国内旅行用省份）+ 出现最多的 3 个城市，例如
   “2025-12-31 日本 东京·京都·大阪”。用户可以改名（按起始日期记住，重新分段后依然有效）。
5. 结果写进 group.feats["trip"] = {key, name, day, days, index}；分区维度「旅行」与模板占位符 {旅行} {第几天} 使用它。
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .places import _core
from ..i18n import t as _t

# ---------------------------------------------------------------------------
# 常住地
# ---------------------------------------------------------------------------


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


@dataclass
class Home:
    label: str
    lat: float | None = None
    lon: float | None = None
    radius_km: float = 50.0
    names: list[str] = field(default_factory=list)     # 没有坐标的照片按地名比较（去掉“省/市/都”等后缀）
    country: str | None = None                          # 用来区分国内游 / 出国（国内游按省份命名）

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Home":
        label = str(d.get("label") or "").strip()
        names = [n for n in (d.get("names") or []) if n] or [p for p in label.replace("／", "/").split("/") if p][-1:]
        return cls(label, d.get("lat"), d.get("lon"), float(d.get("radius_km") or 50), names, d.get("country"))

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "lat": self.lat, "lon": self.lon, "radius_km": self.radius_km,
                "names": self.names, "country": self.country}


def photo_coords(g) -> tuple[float, float] | None:
    geo = g.feats.get("geo") or {}
    lat, lon = geo.get("lat"), geo.get("lon")
    if lat is None or lon is None:
        lat, lon = geo.get("pin_lat"), geo.get("pin_lon")
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def photo_state(g, homes: list[Home]) -> str:
    geo = g.feats.get("geo") or {}
    if not geo.get("country"):
        return "unknown"
    if not homes:
        return "away"
    c = photo_coords(g)
    if c is not None:
        for h in homes:
            if h.lat is not None and h.lon is not None and haversine_km(c[0], c[1], h.lat, h.lon) <= h.radius_km:
                return "home"
    names = {_core(geo.get(k) or "") for k in ("province", "city", "district")} - {""}
    for h in homes:
        if (c is None or h.lat is None) and any(_core(n) in names for n in h.names):
            return "home"
    return "away"


def suggest_home(groups, min_share: float = 0.35, cell_km: float = 30.0) -> dict[str, Any] | None:
    """从照片里猜常住地：按“拍摄天数”统计每个约 30 km 的格子，占比最高且超过 min_share 的格子。
    只有一次旅行的照片库（所有照片都在旅途中）不会给出建议。"""
    days: dict[tuple[int, int], set] = {}
    labels: dict[tuple[int, int], Counter] = {}
    all_days = set()
    pts: dict[tuple[int, int], list] = {}
    for g in groups:
        if not g.files or not g.date:
            continue
        c = photo_coords(g)
        if c is None:
            continue
        step = cell_km / 111.0
        key = (int(c[0] // step), int(c[1] // (step / max(0.2, math.cos(math.radians(c[0]))))))
        d = g.date.date()
        days.setdefault(key, set()).add(d)
        all_days.add(d)
        pts.setdefault(key, []).append(c)
        geo = g.feats.get("geo") or {}
        labels.setdefault(key, Counter())["/".join(x for x in (geo.get("country"), geo.get("province"),
                                                                 geo.get("city")) if x)] += 1
    if not days or len(all_days) < 20:
        return None
    key, ds = max(days.items(), key=lambda kv: len(kv[1]))
    if len(ds) / len(all_days) < min_share:
        return None
    lat = sum(p[0] for p in pts[key]) / len(pts[key])
    lon = sum(p[1] for p in pts[key]) / len(pts[key])
    label = labels[key].most_common(1)[0][0]
    return {"label": label, "lat": round(lat, 4), "lon": round(lon, 4), "radius_km": 50,
            "names": [label.split("/")[-1]], "country": label.split("/")[0] or None,
            "share": round(len(ds) / len(all_days), 2)}


# ---------------------------------------------------------------------------
# 分段
# ---------------------------------------------------------------------------

@dataclass
class Trip:
    key: str
    start: datetime
    end: datetime
    gids: list[int]
    auto_name: str
    name: str
    countries: list[str]
    places: list[str]               # 主要城市（按首次出现排序）
    days: int
    away_photos: int
    domestic: bool = False

    @property
    def photos(self) -> int:
        return len(self.gids)

    @property
    def span_text(self) -> str:
        if self.start.date() == self.end.date():
            return self.start.strftime("%Y-%m-%d")
        fmt = "%m-%d" if self.start.year == self.end.year else "%Y-%m-%d"
        return f"{self.start:%Y-%m-%d}~{self.end.strftime(fmt)}"


def _place_name(geo: dict, domestic: bool) -> str | None:
    if domestic:
        return geo.get("city") or geo.get("province")
    return geo.get("city") or geo.get("province")


def _home_country(homes: list[Home], groups) -> set[str]:
    out = set()
    for h in homes:
        if h.country:
            out.add(h.country)
            continue
        parts = [p for p in h.label.replace("／", "/").split("/") if p]
        if parts:
            out.add(parts[0])
    return out


def _country_zh(cc: str) -> str:
    import json

    from ..config import find_resource
    p = find_resource("data", "place_names_zh.json")
    try:
        return json.loads(p.read_text("utf-8")).get("countries", {}).get(cc.upper(), cc) if p else cc
    except Exception:
        return cc


def segment_trips(groups, homes: list[Home] | None = None, gap_hours: float = 36, min_photos: int = 3,
                  tail_hours: float = 12, names: dict[str, str] | None = None) -> list[Trip]:
    """切分旅行并写入 g.feats["trip"]。返回按时间排序的旅行列表。"""
    homes = homes or []
    names = names or {}
    items = sorted(((g.date, g) for g in groups if g.files and g.date), key=lambda t: (t[0], t[1].gid))
    for g in groups:
        g.feats.pop("trip", None)
    states = [(t, g, photo_state(g, homes)) for t, g in items]
    gap = timedelta(hours=gap_hours)
    # 人还在国外（同一个非常住国家）时，中间一两天没拍照不算回家：间隔放宽到 2 倍（默认 72 小时）
    abroad_gap = gap * 2
    home_cc = _home_country(homes, groups)
    segs: list[list[tuple[datetime, Any]]] = []
    cur: list[tuple[datetime, Any]] = []
    for t, g, st in states:
        if st == "home":
            if cur:
                segs.append(cur)
            cur = []
        elif st == "away":
            if cur and t - cur[-1][0] > gap:
                c_prev = (cur[-1][1].feats.get("geo") or {}).get("country")
                c_now = (g.feats.get("geo") or {}).get("country")
                abroad = bool(c_now) and c_now == c_prev and c_now not in home_cc
                if not (abroad and t - cur[-1][0] <= abroad_gap):
                    segs.append(cur)
                    cur = []
            cur.append((t, g))
    if cur:
        segs.append(cur)
    segs = [s for s in segs if len(s) >= max(1, min_photos)]

    # unknown 照片：落在某段时间范围内（前后放宽 tail_hours）且期间没有 home 照片
    home_times = [t for t, _, st in states if st == "home"]
    tail = timedelta(hours=tail_hours)
    members: list[list[Any]] = [[g for _, g in s] for s in segs]
    bounds = [(s[0][0] - tail, s[-1][0] + tail) for s in segs]
    starts = [b[0] for b in bounds]
    for t, g, st in states:
        if st != "unknown":
            continue
        i = bisect_right(starts, t) - 1
        for j in (i, i + 1):
            if 0 <= j < len(segs) and bounds[j][0] <= t <= bounds[j][1]:
                a, b = sorted((t, segs[j][0][0] if t < segs[j][0][0] else segs[j][-1][0]))
                if t < segs[j][0][0] or t > segs[j][-1][0]:
                    lo, hi = bisect_left(home_times, a), bisect_right(home_times, b)
                    if hi > lo:
                        continue
                members[j].append(g)
                break

    home_countries = _home_country(homes, groups)
    trips: list[Trip] = []
    used_keys: Counter = Counter()
    for seg, mem in zip(segs, members):
        mem.sort(key=lambda g: (g.date, g.gid))
        start, end = mem[0].date, mem[-1].date
        geos = [g.feats.get("geo") or {} for _, g in seg]
        cc = Counter(geo.get("country") for geo in geos if geo.get("country"))
        countries = [c for c, _ in cc.most_common()]
        domestic = bool(countries) and countries[0] in home_countries
        pc: Counter = Counter()
        first: dict[str, int] = {}
        for i, geo in enumerate(geos):
            p = _place_name(geo, domestic)
            if p:
                pc[p] += 1
                first.setdefault(p, i)
        top = sorted((p for p, _ in pc.most_common(3)), key=lambda p: first[p])
        if domestic:
            pcount = Counter(geo.get("province") for geo in geos if geo.get("province"))
            provs = [p for p, _ in pcount.most_common(2)]
            main_raw = "·".join(provs) or (countries[0] if countries else "")
            top = [p for p in top if p not in main_raw]
            main = "·".join(_t(p) for p in provs) or (_t(countries[0]) if countries else "")
        else:
            main = "·".join(_t(c) for c in countries[:2])
        title = " ".join(x for x in (main, "·".join(_t(p) for p in top)) if x)     # 地名按界面语言
        auto = f"{start:%Y-%m-%d} {title}".strip()
        base_key = f"{start:%Y-%m-%d}"
        used_keys[base_key] += 1
        key = base_key if used_keys[base_key] == 1 else f"{base_key}#{used_keys[base_key]}"
        name = (names.get(key) or "").strip() or auto
        days = (end.date() - start.date()).days + 1
        trip = Trip(key, start, end, [g.gid for g in mem], auto, name, countries, top, days, len(seg), domestic)
        idx = len(trips)
        for g in mem:
            g.feats["trip"] = {"key": key, "name": name, "index": idx, "days": days,
                               "day": (g.date.date() - start.date()).days + 1}
        trips.append(trip)
    return trips


def day_label(day: int, days: int | None = None, pad: bool = True) -> str:
    n = f"{day:02d}" if pad and (days or 0) >= 10 else str(day)
    return _t('第{0}天', n)


# ---------------------------------------------------------------------------
# 常住地输入解析
# ---------------------------------------------------------------------------

def search_world_city(name: str, cc: str | None = None) -> dict[str, Any] | None:
    """在 GeoNames 城市表里按名字（英文 / 拼音，不区分大小写）找坐标，用于“Boca Raton”这类没有中文名的城市。"""
    import csv
    import gzip

    from ..config import find_resource
    p = find_resource("data", "geonames_cities1000.csv.gz")
    want = (name or "").strip().lower()
    if not p or not want:
        return None
    best = None
    with gzip.open(p, "rt", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            try:
                if row[2].lower() != want:
                    continue
                if cc and row[5] != cc.upper():
                    continue
                best = {"lat": float(row[0]), "lon": float(row[1]), "name": row[2], "admin1": row[3], "cc": row[5]}
                break
            except (IndexError, ValueError):
                continue
    return best


def _world_home(w: dict[str, Any]) -> Home:
    """GeoNames 城市 → Home，标签尽量用中文（“美国/佛罗里达州/博卡拉顿”）。"""
    label, names = f"{_country_zh(w['cc'])}/{w['admin1']}/{w['name']}", [w["name"]]
    try:
        from .geo import GeoService
        r = GeoService().lookup(w["lat"], w["lon"]) or {}
        if r.get("country") and r.get("city"):
            label = "/".join(x for x in (r.get("country"), r.get("province"), r.get("city")) if x)
            names.append(r["city"])
    except Exception:
        pass
    return Home(label, w["lat"], w["lon"], 50.0, names, _country_zh(w["cc"]))


def resolve_home(text: str, place_index=None) -> Home | None:
    """把用户输入的常住地解析成 Home：支持“纬度,经度”、中文地名（“湖南/邵阳”“日本/东京”）、英文城市名（“Boca Raton”，
    可写成“Boca Raton, US”）。"""
    import re

    t = (text or "").strip()
    if not t:
        return None
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*$", t)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        country, label = None, f"{lat:.4f},{lon:.4f}"
        try:
            from .geo import GeoService
            r = GeoService().lookup(lat, lon) or {}
            country = r.get("country")
            if country:
                label = "/".join(x for x in (country, r.get("province"), r.get("city")) if x)
        except Exception:
            pass
        return Home(label, lat, lon, 50.0, [], country)
    m = re.match(r"^(.*?)[,，]\s*([A-Za-z]{2})$", t)
    if m and re.search(r"[A-Za-z]", m.group(1)):
        w = search_world_city(m.group(1), m.group(2))
        if w:
            return _world_home(w)
    if place_index is not None and not re.fullmatch(r"[A-Za-z .'\-]+", t):
        p = place_index.resolve(t)
        if p and p.get("country") and not p.get("needs_country"):
            last = next((p.get(k) for k in ("district", "city", "province", "country") if p.get(k)), t)
            return Home(p.get("label") or t, p.get("lat"), p.get("lon"), 50.0, [last], p.get("country"))
    if re.search(r"[A-Za-z]", t):
        w = search_world_city(t)
        if w:
            return _world_home(w)
    return Home(t, None, None, 50.0, [t.replace("／", "/").split("/")[-1]])
