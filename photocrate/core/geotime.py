"""时间与地点：时区换算、GPS 轨迹（GPX / KML）解析、按拍摄时间匹配位置。

每个时间点最多有两种表示，比较时优先用 UTC：
- utc：真正的 UTC 秒数。照片 = 当地拍摄时间（已应用“时间校正”）− 时区；时区依次取：时间校正里的实际时区 →
  EXIF 的 OffsetTimeOriginal → 设置里的“照片没有时区时按…”。GPX / KML 本身就是 UTC；
  手机照片没有时区时，还可以用 EXIF 里的 GPS 时间（GPSDateTime，UTC）。
- local：“当地墙上时间”（把当地时间当作 UTC 得到的秒数）。照片时区未知时只能用它，
  和参考照片的当地时间比较（相当于假设两台设备的时钟都设成了当地时间）。
"""
from __future__ import annotations

import logging
import math
import os
import re
import xml.etree.ElementTree as ET
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
from ..i18n import t as _t

log = logging.getLogger(__name__)

_OFFSET_RE = re.compile(r"^\s*(?:UTC|GMT)?\s*([+\-−])\s*(\d{1,2})(?::?(\d{2}))?\s*$", re.I)


# ---------------------------------------------------------------------------
# 时区
# ---------------------------------------------------------------------------

def parse_offset(text: str | None) -> int | None:
    """'+09:00' / '-0530' / 'UTC+8' / 'Z' → 秒数；无法识别返回 None。"""
    if not text:
        return None
    t = str(text).strip()
    if t.upper() in ("Z", "UTC", "GMT"):
        return 0
    m = _OFFSET_RE.match(t)
    if not m:
        return None
    sign = -1 if m.group(1) in "-−" else 1
    h, mi = int(m.group(2)), int(m.group(3) or 0)
    if h > 14 or mi >= 60:
        return None
    return sign * (h * 3600 + mi * 60)


def format_offset(seconds: int | None) -> str:
    if seconds is None:
        return ""
    sign = "+" if seconds >= 0 else "-"
    s = abs(int(seconds))
    return f"{sign}{s // 3600:02d}:{(s % 3600) // 60:02d}"


_TZ_NOTES = {
    "+08:00": _t("中国、新加坡、台湾"), "+09:00": _t("日本、韩国"), "+07:00": _t("泰国、越南"), "+05:30": _t("印度"),
    "+00:00": _t("英国(冬)、冰岛"), "+01:00": _t("欧洲中部(冬)、英国(夏)"), "+02:00": _t("欧洲中部(夏)"),
    "+03:00": _t("莫斯科、土耳其"), "+04:00": _t("迪拜"), "+10:00": _t("悉尼(冬)"), "+11:00": _t("悉尼(夏)"),
    "+12:00": _t("新西兰(冬)"), "+13:00": _t("新西兰(夏)"), "-03:00": _t("巴西"),
    "-04:00": _t("美国东部(夏令时)"), "-05:00": _t("美国东部(冬令时)"), "-06:00": _t("美国中部(冬)"),
    "-07:00": _t("美国西部(夏)"), "-08:00": _t("美国西部(冬)"), "-10:00": _t("夏威夷"),
}


def timezone_choices() -> list[tuple[int, str]]:
    """[(秒数, 显示文字)]，从 UTC−12 到 UTC+14，含常见的半小时 / 45 分时区。"""
    vals = set(range(-12 * 3600, 14 * 3600 + 1, 3600))
    vals |= {s * 60 for s in (-570, -210, 210, 270, 330, 345, 390, 525, 570, 630, 765)}
    out = []
    for v in sorted(vals):
        o = format_offset(v)
        note = _TZ_NOTES.get(o)
        out.append((v, f"UTC{o}" + (_t('（{0}）', note) if note else "")))
    return out


# ---------------------------------------------------------------------------
# 照片时间 → UTC
# ---------------------------------------------------------------------------

def _naive_ts(dt: datetime) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


def photo_times(group, default_tz: int | None) -> tuple[float | None, float | None]:
    """返回 (utc, local)。没有可靠的拍摄时间（只有文件名 / 修改时间）时都为 None。"""
    d = group.feats.get("date") or {}
    if not d.get("dt") or d.get("source") not in ("exif", "exif_create", "xmp"):
        return None, None
    try:
        local = _naive_ts(datetime.fromisoformat(d["dt"]))
    except ValueError:
        return None, None
    tz = parse_offset(d.get("tz"))
    if tz is None:
        tz = default_tz
    return (local - tz if tz is not None else None), local


def photo_utc(group, default_tz: int | None) -> tuple[float | None, bool]:
    """兼容旧接口：返回 (时间戳, 是否为真正的 UTC)。"""
    utc, local = photo_times(group, default_tz)
    return (utc, True) if utc is not None else (local, False)


def gps_utc(meta: dict) -> float | None:
    """EXIF GPS 时间（GPSDateStamp + GPSTimeStamp，本身就是 UTC）。"""
    from .dates import parse_exif_datetime
    dt, _ = parse_exif_datetime(meta.get("gps_utc"))
    return _naive_ts(dt) if dt else None


# ---------------------------------------------------------------------------
# 轨迹点
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrackPoint:
    utc: float | None          # UTC 秒；未知为 None
    lat: float
    lon: float
    local: float | None = None  # 当地墙上时间（秒）；未知为 None
    source: str = "track"      # track（GPX/KML）/ reference（参考照片）/ library（同批带 GPS 的照片）
    label: str = ""

    @property
    def aware(self) -> bool:
        return self.utc is not None


def _iso_to_utc(text: str) -> float | None:
    s = (text or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?([+\-]\d{2}:?\d{2})?", s)
        if not m:
            return None
        dt = datetime.fromisoformat(m.group(1) + (m.group(3) or "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_gpx(path: str) -> list[TrackPoint]:
    out: list[TrackPoint] = []
    label = os.path.basename(path)
    for _, el in ET.iterparse(path, events=("end",)):
        name = _local(el.tag)
        if name in ("trkpt", "rtept", "wpt"):
            t = None
            for ch in el:
                if _local(ch.tag) == "time":
                    t = _iso_to_utc(ch.text or "")
            try:
                lat, lon = float(el.get("lat")), float(el.get("lon"))
            except (TypeError, ValueError):
                lat = lon = None
            if t is not None and lat is not None and lon is not None:
                out.append(TrackPoint(t, lat, lon, None, "track", label))
            el.clear()
    return out


def parse_kml(path: str) -> list[TrackPoint]:
    """支持 gx:Track（when + gx:coord）与带 TimeStamp 的 Placemark 点。"""
    out: list[TrackPoint] = []
    label = os.path.basename(path)
    root = ET.parse(path).getroot()
    for el in root.iter():
        name = _local(el.tag)
        if name == "Track":
            whens = [_iso_to_utc(c.text or "") for c in el if _local(c.tag) == "when"]
            coords = [c.text for c in el if _local(c.tag) == "coord"]
            for t, c in zip(whens, coords):
                parts = (c or "").split()
                if t is None or len(parts) < 2:
                    continue
                try:
                    out.append(TrackPoint(t, float(parts[1]), float(parts[0]), None, "track", label))
                except ValueError:
                    continue
        elif name == "Placemark":
            t = None
            coord = None
            for sub in el.iter():
                sn = _local(sub.tag)
                if sn == "when" and t is None:
                    t = _iso_to_utc(sub.text or "")
                elif sn == "coordinates" and coord is None:
                    coord = (sub.text or "").strip().split()
            if t is not None and coord and len(coord) == 1:
                xs = coord[0].split(",")
                try:
                    out.append(TrackPoint(t, float(xs[1]), float(xs[0]), None, "track", label))
                except (ValueError, IndexError):
                    pass
    return out


def load_track_file(path: str) -> list[TrackPoint]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gpx":
        return parse_gpx(path)
    if ext == ".kml":
        return parse_kml(path)
    raise ValueError(_t("只支持 .gpx 与 .kml 文件"))


# ---------------------------------------------------------------------------
# 按时间查位置
# ---------------------------------------------------------------------------

def _haversine_km(a: TrackPoint, b: TrackPoint) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(min(1.0, math.sqrt(h)))


class TrackIndex:
    """按 UTC 与当地时间各建一个有序表，二分查找最近的参考点。"""

    def __init__(self, points: Iterable[TrackPoint]):
        pts = list(points)
        self._by = {}
        for key, attr, keep in (("utc", "utc", lambda p: p.utc is not None),
                                ("local", "local", lambda p: p.local is not None),
                                ("local_only", "local", lambda p: p.local is not None and p.utc is None)):
            arr = sorted((p for p in pts if keep(p)), key=lambda p, a=attr: getattr(p, a))
            self._by[key] = (arr, [getattr(p, attr) for p in arr])
        self.count = len(pts)

    def __len__(self) -> int:
        return self.count

    def locate(self, t: float, by: str, window_s: float) -> tuple[float, float, TrackPoint, float] | None:
        """by = "utc" / "local" / "local_only"（只看没有 UTC 的参考点）。返回 (lat, lon, 最近的点, 时间差秒)。
        两侧点都在窗口内且相距不远时按时间线性插值，否则取窗口内最近的点。"""
        pts, ts = self._by[by]
        if not pts:
            return None
        i = bisect_left(ts, t)
        a, ta = (pts[i - 1], ts[i - 1]) if i > 0 else (None, None)
        b, tb = (pts[i], ts[i]) if i < len(pts) else (None, None)
        if a is not None and b is not None and tb > ta and (t - ta) <= window_s and (tb - t) <= window_s \
                and _haversine_km(a, b) <= 50:
            f = (t - ta) / (tb - ta)
            near = a if (t - ta) <= (tb - t) else b
            return a.lat + (b.lat - a.lat) * f, a.lon + (b.lon - a.lon) * f, near, min(t - ta, tb - t)
        best = None
        for p, tp in ((a, ta), (b, tb)):
            if p is None:
                continue
            d = abs(tp - t)
            if d <= window_s and (best is None or d < best[1]):
                best = (p, d)
        if best is None:
            return None
        return best[0].lat, best[0].lon, best[0], best[1]


def library_points(groups, default_tz: int | None) -> list[TrackPoint]:
    """本批照片中自带 GPS 的照片（通常是手机拍的）也可以当作参考点。"""
    out = []
    for g in groups:
        geo = g.feats.get("geo") or {}
        if geo.get("source") != "gps" or geo.get("lat") is None:
            continue
        utc, local = photo_times(g, default_tz)
        if utc is None:
            utc = gps_utc(g.meta)
        if utc is not None or local is not None:
            out.append(TrackPoint(utc, float(geo["lat"]), float(geo["lon"]), local, "library", g.name))
    return out


def reference_point_from_meta(meta: dict, name: str, default_tz: int | None) -> TrackPoint | None:
    """参考照片（不在本批里的手机照片等）：取拍摄时间 + GPS 做成参考点。"""
    from .dates import parse_exif_datetime

    lat, lon = meta.get("lat"), meta.get("lon")
    if lat is None or lon is None:
        return None
    local = utc = None
    for key in ("dt_original", "dt_create"):
        dt, tz = parse_exif_datetime(meta.get(key))
        if dt:
            local = _naive_ts(dt)
            off = parse_offset(tz or meta.get("offset"))
            if off is None:
                off = default_tz
            if off is not None:
                utc = local - off
            break
    if utc is None:
        utc = gps_utc(meta)
    if utc is None and local is None:
        return None
    return TrackPoint(utc, float(lat), float(lon), local, "reference", name)


def fill_locations(groups, index: TrackIndex, geo_service, window_minutes: int,
                   default_tz: int | None) -> dict[str, int]:
    """没有 GPS、也没有手动地点的照片：按拍摄时间在轨迹 / 参考照片里找位置。

    能算出 UTC 的照片先按 UTC 比对（相机与手机时区设置不同也能对上）；对不上、或照片时区未知时，
    再按“当地时间”与参考照片比对（GPX 轨迹只有 UTC，时区未知的照片需要在设置里指定默认时区）。
    """
    stats = {"matched": 0, "track": 0, "reference": 0, "library": 0, "candidates": 0}
    if index is None or not len(index):
        return stats
    window = max(1, int(window_minutes)) * 60
    for g in groups:
        if not g.files:
            continue
        geo = g.feats.get("geo") or {}
        if geo.get("source") in ("gps", "manual"):
            continue
        utc, local = photo_times(g, default_tz)
        if utc is None and local is None:
            continue
        stats["candidates"] += 1
        if utc is not None:
            # 照片时区已知：按 UTC 比对；对不上时，只再和“时区未知”的参考点比当地时间
            hit = index.locate(utc, "utc", window) or index.locate(local, "local_only", window)
        else:
            # 照片时区未知：只能按当地时间比对（假设相机与手机的时钟都设成了当地时间）
            hit = index.locate(local, "local", window)
        if hit is None:
            continue
        lat, lon, near, gap = hit
        r = (geo_service.lookup(lat, lon) if geo_service is not None else None) or {}
        g.feats["geo"] = {
            "has_gps": False, "lat": round(lat, 6), "lon": round(lon, 6), "located": bool(r.get("country")),
            "source": "track", "manual": False, "inferred": False,
            "match_kind": near.source, "match_from": near.label, "match_gap_s": int(round(gap)), **r,
        }
        stats["matched"] += 1
        stats[near.source] = stats.get(near.source, 0) + 1
    return stats


MATCH_KIND_LABELS = {"track": _t("GPS 轨迹"), "reference": _t("参考照片"), "library": _t("同批带 GPS 的照片")}


def describe_span(points: list[TrackPoint]) -> str:
    utc = [p.utc for p in points if p.utc is not None]
    local = [p.local for p in points if p.local is not None]
    if not utc and not local:
        return _t("无有效轨迹点")
    ts, unit = (local, _t("当地时间")) if local else (utc, "UTC")
    t0 = datetime.fromtimestamp(min(ts), timezone.utc)
    t1 = datetime.fromtimestamp(max(ts), timezone.utc)
    return _t('{0} 个点，{1:%Y-%m-%d %H:%M} ~ {2:%Y-%m-%d %H:%M}（{3}）', len(points), t0, t1, unit)


def shift_label(seconds: int) -> str:
    if not seconds:
        return ""
    sign = "+" if seconds > 0 else "-"
    s = abs(int(seconds))
    h, m = s // 3600, (s % 3600) // 60
    return f"{sign}{h}:{m:02d}"


def local_day(dt: datetime, shift_s: int) -> str:
    return (dt + timedelta(seconds=shift_s)).strftime("%Y-%m-%d")
