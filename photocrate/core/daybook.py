"""按天汇总照片（「时间与地点」页的数据），以及“行程”文本的解析。

行程文本每行一段，例如：
    2026-03-08 东京
    2026-03-09 ~ 2026-03-11  日本/京都
    3/12 大阪            （省略年份时取照片里最常见的年份）
    3月13日-3月14日 奈良
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..analyzers.builtin import place_label

WEEKDAYS = "一二三四五六日"
SOURCE_ORDER = ("gps", "track", "manual", "inferred", "none")
SOURCE_LABELS = {"gps": "GPS", "track": "轨迹", "manual": "手动", "inferred": "推测", "none": "无"}


@dataclass
class DayRow:
    key: str                       # "2026-03-10"；无日期为 ""
    day: date | None
    gids: list[int] = field(default_factory=list)
    first: datetime | None = None
    last: datetime | None = None
    cameras: Counter = field(default_factory=Counter)
    places: Counter = field(default_factory=Counter)
    sources: Counter = field(default_factory=Counter)
    tzs: Counter = field(default_factory=Counter)
    shifts: Counter = field(default_factory=Counter)
    manual: Counter = field(default_factory=Counter)

    @property
    def count(self) -> int:
        return len(self.gids)

    @property
    def label(self) -> str:
        if self.day is None:
            return "无日期"
        return f"{self.key}  周{WEEKDAYS[self.day.weekday()]}"

    @property
    def unknown(self) -> int:
        return self.sources.get("none", 0)

    @property
    def main_place(self) -> str:
        if not self.places:
            return ""
        top = self.places.most_common()
        s = top[0][0]
        return s + (f"  等 {len(top)} 处" if len(top) > 1 else "")

    @property
    def source_text(self) -> str:
        return " · ".join(f"{SOURCE_LABELS[k]} {self.sources[k]}" for k in SOURCE_ORDER if self.sources.get(k))


def geo_source(g) -> str:
    geo = g.feats.get("geo") or {}
    if not geo.get("country"):
        return "none"
    return geo.get("source") or "none"


def build_days(groups) -> list[DayRow]:
    rows: dict[str, DayRow] = {}
    for g in groups:
        if not g.files:
            continue
        d = g.date
        key = d.strftime("%Y-%m-%d") if d else ""
        r = rows.get(key)
        if r is None:
            r = rows[key] = DayRow(key, d.date() if d else None)
        r.gids.append(g.gid)
        if d:
            r.first = d if r.first is None or d < r.first else r.first
            r.last = d if r.last is None or d > r.last else r.last
        cam = g.feat("camera", "model")
        if cam:
            r.cameras[cam] += 1
        src = geo_source(g)
        r.sources[src] += 1
        geo = g.feats.get("geo") or {}
        # 按天汇总只到“市”一级（区县太细，一天里会分成很多处）
        lab = place_label({k: geo.get(k) for k in ("country", "province", "city")}, "/")
        if lab:
            r.places[lab] += 1
            if src == "manual":
                r.manual[lab] += 1
        dd = g.feats.get("date") or {}
        if dd.get("tz"):
            r.tzs[dd["tz"]] += 1
        if dd.get("corrected"):
            r.shifts[int(dd.get("shift") or 0)] += 1
    return sorted(rows.values(), key=lambda r: (r.day is None, r.day or date.max))


# ---------------------------------------------------------------------------
# 行程文本
# ---------------------------------------------------------------------------

_D = r"(?:(?P<{p}y>(?:19|20)\d{{2}})\s*[-/.年]\s*)?(?P<{p}m>\d{{1,2}})\s*[-/.月]\s*(?P<{p}d>\d{{1,2}})\s*日?"
_RANGE = r"(?:\s*(?:~|～|—|–|至|到|-)\s*" + _D.format(p="b") + r")?"
_LINE = re.compile(r"^\s*" + _D.format(p="a") + _RANGE + r"\s*[:：,，\s]\s*(?P<place>.+?)\s*$")
_DATE_ONLY = re.compile(r"^\s*" + _D.format(p="a") + _RANGE + r"\s*[:：,，]?\s*$")


@dataclass
class ItineraryLine:
    line_no: int
    text: str
    start: date | None = None
    end: date | None = None
    place: str = ""
    error: str = ""

    def days(self) -> list[date]:
        if not self.start:
            return []
        out, d = [], self.start
        while d <= (self.end or self.start) and len(out) < 400:
            out.append(d)
            d += timedelta(days=1)
        return out


def parse_itinerary(text: str, default_year: int | None = None) -> list[ItineraryLine]:
    year = default_year or datetime.now().year
    out = []
    for i, raw in enumerate((text or "").splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith(("#", "//")):
            continue
        it = ItineraryLine(i, s)
        m = _LINE.match(s)
        if not m and _DATE_ONLY.match(s):
            continue          # 只有日期、地点留空：跳过
        if not m:
            it.error = "看不懂这一行：应为“日期 地点”，例如 2026-03-08 东京"
            out.append(it)
            continue
        try:
            ya = int(m.group("ay") or year)
            it.start = date(ya, int(m.group("am")), int(m.group("ad")))
            if m.group("bm"):
                yb = int(m.group("by") or ya)
                it.end = date(yb, int(m.group("bm")), int(m.group("bd")))
                if it.end < it.start:
                    it.error = "结束日期早于开始日期"
            else:
                it.end = it.start
        except ValueError:
            it.error = "日期无效"
        it.place = m.group("place").strip()
        if not it.place:
            it.error = it.error or "缺少地点"
        out.append(it)
    return out


def itinerary_template(rows: list[DayRow]) -> str:
    """把当前的天列表变成可编辑的行程文本：已有地点的天填上地点，其余留空待填。"""
    lines = ["# 每行：日期 地点（如 2026-03-08 日本/东京）。日期可写成范围：2026-03-09 ~ 2026-03-11 京都",
             "# 以 # 开头的行会被忽略；地点留空的行不会被应用。"]
    for r in rows:
        if r.day is None:
            continue
        place = r.places.most_common(1)[0][0] if r.places else ""
        lines.append(f"{r.key} {place}".rstrip())
    return "\n".join(lines)
