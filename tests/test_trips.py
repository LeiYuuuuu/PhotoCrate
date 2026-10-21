"""第三梯队：旅行自动分段与命名、常住地解析、旅行分区维度。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

from photocrate.bootstrap import bootstrap  # noqa: E402
from photocrate.config import Settings  # noqa: E402

bootstrap(Settings(), load_user_plugins=False)

from photocrate.core.models import MemberFile, PhotoGroup  # noqa: E402
from photocrate.core.trips import Home, photo_state, resolve_home, segment_trips, suggest_home  # noqa: E402
from photocrate.partition.base import DIMENSIONS, PartitionContext  # noqa: E402

BOCA = {"country": "美国", "province": "佛罗里达州", "city": "Boca Raton", "cc": "US", "lat": 26.36, "lon": -80.10}
TOKYO = {"country": "日本", "province": "东京都", "city": "东京", "cc": "JP", "lat": 35.68, "lon": 139.76}
KYOTO = {"country": "日本", "province": "京都府", "city": "京都", "cc": "JP", "lat": 35.01, "lon": 135.77}
OSAKA = {"country": "日本", "province": "大阪府", "city": "大阪", "cc": "JP", "lat": 34.69, "lon": 135.50}
SHAOYANG = {"country": "中国", "province": "湖南省", "city": "邵阳市", "cc": "CN"}         # 手动地点：没有坐标
CHANGSHA = {"country": "中国", "province": "湖南省", "city": "长沙市", "cc": "CN", "lat": 28.23, "lon": 112.94}
ORLANDO = {"country": "美国", "province": "佛罗里达州", "city": "Orlando", "cc": "US", "lat": 28.54, "lon": -81.38}


def _g(gid: int, dt: datetime, place: dict | None):
    p = f"C:/p/IMG_{gid:04d}.JPG"
    g = PhotoGroup(gid, [MemberFile(p, "jpeg", 100, gid)], p)
    g.feats["date"] = {"dt": dt.isoformat(), "source": "exif"}
    if place:
        g.feats["geo"] = {"has_gps": "lat" in place, "located": True, "source": "gps" if "lat" in place else "manual",
                          **place}
    else:
        g.feats["geo"] = {"has_gps": False, "located": False, "source": None}
    return g


def _library_of_life():
    """在家（博卡拉顿）→ 日本 7 天（东京 → 京都 → 大阪，中间有没定位的照片）→ 回家 → 奥兰多一日游 → 回国邵阳过年 → 长沙两天。"""
    gs, t = [], datetime(2025, 12, 20, 10)
    n = 0

    def add(dt, place):
        nonlocal n
        gs.append(_g(n, dt, place))
        n += 1
    for d in range(5):
        add(t + timedelta(days=d), BOCA)
    jp = datetime(2025, 12, 31, 18)
    for d, pl in enumerate([TOKYO, TOKYO, TOKYO, KYOTO, KYOTO, OSAKA, OSAKA]):
        add(jp + timedelta(days=d), pl)
        add(jp + timedelta(days=d, hours=2), None)              # 同一天没定位的照片
    add(jp + timedelta(days=6, hours=8), None)                  # 离开前最后一张（6 小时后）
    add(datetime(2026, 1, 9, 12), BOCA)
    for h in range(4):
        add(datetime(2026, 1, 17, 9 + h), ORLANDO)
    add(datetime(2026, 1, 18, 10), BOCA)
    for d in range(10):
        add(datetime(2026, 2, 10, 12) + timedelta(days=d), SHAOYANG)
    for h in range(0, 30, 6):
        add(datetime(2026, 2, 21, 9) + timedelta(hours=h), CHANGSHA)
    add(datetime(2026, 2, 23, 10), SHAOYANG)
    return gs


HOMES = [Home("美国/佛罗里达州/Boca Raton", 26.3587, -80.0831, 50, ["Boca Raton"], "美国"),
         Home("中国/湖南省/邵阳市", None, None, 50, ["邵阳市"], "中国")]


def test_photo_state():
    assert photo_state(_g(0, datetime(2026, 1, 1), BOCA), HOMES) == "home"
    assert photo_state(_g(0, datetime(2026, 1, 1), SHAOYANG), HOMES) == "home"       # 没有坐标 → 按地名
    assert photo_state(_g(0, datetime(2026, 1, 1), TOKYO), HOMES) == "away"
    assert photo_state(_g(0, datetime(2026, 1, 1), None), HOMES) == "unknown"
    assert photo_state(_g(0, datetime(2026, 1, 1), BOCA), []) == "away"


def test_segment_trips_with_homes():
    gs = _library_of_life()
    trips = segment_trips(gs, HOMES, gap_hours=36, min_photos=3, tail_hours=12)
    names = [t.name for t in trips]
    assert names == ["2025-12-31 日本 东京·京都·大阪", "2026-01-17 佛罗里达州 Orlando", "2026-02-21 湖南省 长沙市"], names
    jp = trips[0]
    assert jp.days == 8 and jp.photos == 15 and jp.away_photos == 7 and not jp.domestic
    assert jp.start == datetime(2025, 12, 31, 18) and jp.end == datetime(2026, 1, 7, 2)     # 含没定位的最后一张
    assert trips[2].domestic and trips[1].domestic
    first = next(g for g in gs if g.gid == jp.gids[0])
    assert first.feats["trip"]["day"] == 1 and first.feats["trip"]["days"] == 8
    last_jp = next(g for g in gs if g.gid == jp.gids[-1])
    assert last_jp.feats["trip"]["day"] == 8
    assert not any("trip" in g.feats for g in gs if g.feats["geo"].get("city") in ("Boca Raton", "邵阳市"))


def test_rename_and_gap():
    gs = _library_of_life()
    trips = segment_trips(gs, HOMES, names={"2025-12-31": "日本跨年"})
    assert trips[0].name == "日本跨年" and trips[0].auto_name.startswith("2025-12-31 日本")
    assert all(g.feats["trip"]["name"] == "日本跨年" for g in gs if g.gid in trips[0].gids)
    # 间隔改成 6 小时（在国外放宽到 12 小时）：日本每天只有一张有定位的照片（间隔 24 小时）→ 不足 3 张的段被丢弃
    assert not [t for t in segment_trips(gs, HOMES, gap_hours=6) if t.countries == ["日本"]]


def test_gap_abroad_is_relaxed():
    """在国外中间有一天没拍照（间隔 48 小时）仍是同一次旅行；在国内（常住国）同样的间隔就断开。"""
    gs, n = [], 0
    for day, pl in ((1, TOKYO), (1, TOKYO), (3, OSAKA), (3, OSAKA), (4, KYOTO)):
        gs.append(_g(n, datetime(2026, 1, day, 12, n), pl))
        n += 1
    trips = segment_trips(gs, HOMES, gap_hours=36, min_photos=1)
    assert len(trips) == 1 and trips[0].days == 4, [t.name for t in trips]
    cs = [_g(i, datetime(2026, 2, d, 12, i), CHANGSHA) for i, d in enumerate((1, 1, 3, 3))]
    assert len(segment_trips(cs, HOMES, gap_hours=36, min_photos=1)) == 2


def test_without_homes_segments_by_time():
    gs = _library_of_life()
    trips = segment_trips(gs, [], gap_hours=36)
    assert trips[0].start == datetime(2025, 12, 20, 10)          # 没有常住地：在家的照片也算“出行”
    assert len(trips) >= 3


def test_suggest_home():
    gs = _library_of_life()
    extra = [_g(1000 + d, datetime(2025, 9, 1, 12) + timedelta(days=d), BOCA) for d in range(40)]
    s = suggest_home(gs + extra)
    assert s and "Boca Raton" in s["label"] and abs(s["lat"] - 26.36) < 0.2
    assert suggest_home(gs[5:20]) is None                         # 只有一次旅行：不给建议


def test_resolve_home():
    h = resolve_home("Boca Raton, US")
    assert h and abs(h.lat - 26.3587) < 0.01 and h.country == "美国"
    h = resolve_home("26.36, -80.10")
    assert h.lat == 26.36 and h.country == "美国"
    from photocrate.core.places import PlaceIndex
    h = resolve_home("湖南/邵阳", PlaceIndex())
    assert h.country == "中国" and "邵阳" in h.label and h.names


def test_trip_dimension():
    gs = _library_of_life()
    segment_trips(gs, HOMES)
    d = DIMENSIONS["trip"]
    ctx = PartitionContext({"trip_other_name": "日常"})
    jp = next(g for g in gs if g.feats.get("trip") and g.feats["trip"]["day"] == 3)
    assert d.segments(jp, d.resolve_opts({"mode": "trip/daydate"}), ctx) == ["2025-12-31 日本 东京·京都·大阪",
                                                                            "第3天 01-02"]
    home = gs[0]
    assert d.segments(home, d.resolve_opts({}), ctx) == ["日常"]
    from photocrate.partition.engine import render_template
    assert render_template("{旅行}/{第几天}", jp, ctx) == ["2025-12-31 日本 东京·京都·大阪", "第3天"]
