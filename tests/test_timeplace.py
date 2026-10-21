"""第一梯队功能测试：手动地点 / GPS 补全（轨迹、参考照片）/ 时间校正 / 跨文件夹配对 / 写回 XMP。

python -m pytest tests -q
需要 ExifTool 的用例：设置环境变量 PHOTOCRATE_TEST_EXIFTOOL 指向 exiftool，否则自动跳过。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

from photocrate.bootstrap import bootstrap  # noqa: E402
from photocrate.config import Settings  # noqa: E402

bootstrap(Settings(), load_user_plugins=False)

import piexif  # noqa: E402
from PIL import Image  # noqa: E402

from photocrate.analyzers.builtin import apply_time_shift  # noqa: E402
from photocrate.core.daybook import build_days, parse_itinerary  # noqa: E402
from photocrate.core.geotime import (TrackIndex, TrackPoint, fill_locations, format_offset, load_track_file,  # noqa: E402
                                     parse_offset, photo_utc)
from photocrate.core.models import MemberFile, PhotoGroup  # noqa: E402
from photocrate.core.pairing import pair_across_folders, renumber  # noqa: E402

ET = os.environ.get("PHOTOCRATE_TEST_EXIFTOOL") or shutil.which("exiftool")
need_et = pytest.mark.skipif(not ET, reason="需要 ExifTool")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _deg(v):
    v = abs(v)
    d = int(v)
    m = int((v - d) * 60)
    s = round(((v - d) * 60 - m) * 60 * 100)
    return ((d, 1), (m, 1), (s, 100))


def make_jpeg(path: Path, dt: datetime, offset: str | None = None, gps=None, model="ILCE-7M4",
              make="SONY", color=(120, 90, 60)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    zeroth = {piexif.ImageIFD.Make: make.encode(), piexif.ImageIFD.Model: model.encode()}
    exif = {piexif.ExifIFD.DateTimeOriginal: dt.strftime("%Y:%m:%d %H:%M:%S").encode()}
    if offset:
        exif[36881] = offset.encode()        # OffsetTimeOriginal
    gpsd = {}
    if gps:
        lat, lon = gps
        gpsd = {piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S", piexif.GPSIFD.GPSLatitude: _deg(lat),
                piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W", piexif.GPSIFD.GPSLongitude: _deg(lon)}
    im = Image.new("RGB", (320, 240), color)
    im.save(path, "JPEG", exif=piexif.dump({"0th": zeroth, "Exif": exif, "GPS": gpsd}))
    return path


def write_gpx(path: Path, pts: list[tuple[datetime, float, float]]) -> Path:
    body = "".join(f'<trkpt lat="{la}" lon="{lo}"><time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time></trkpt>'
                   for t, la, lo in pts)
    path.write_text('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
                    f"<trk><trkseg>{body}</trkseg></trk></gpx>", "utf-8")
    return path


def _g(gid, dt: str | None, tz: str | None = "+09:00", path="C:/p/A.JPG", geo=None):
    g = PhotoGroup(gid, [MemberFile(path, "jpeg", 100, gid)], path)
    if dt:
        g.feats["date"] = {"dt": dt, "source": "exif", "tz": tz}
    g.feats["geo"] = geo or {"has_gps": False, "located": False, "source": None}
    return g


class _Geo:
    def lookup(self, lat, lon):
        return {"country": "日本", "province": "京都府", "city": "京都市", "cc": "JP"} if lat > 34.8 else \
            {"country": "日本", "province": "大阪府", "city": "大阪市", "cc": "JP"}


def _library(tmp_path, **settings):
    from photocrate.core.library import Library
    s = Settings(tmp_path / "settings.json")
    s.set("parallel_mode", "thread")
    s.set("exiftool_path", ET or "")
    for k, v in settings.items():
        s.set(k, v)
    lib = Library(s)
    if not ET:
        lib.exiftool_path = None
    return lib


# ---------------------------------------------------------------------------
# 时区与轨迹
# ---------------------------------------------------------------------------

def test_offsets():
    assert parse_offset("+09:00") == 9 * 3600
    assert parse_offset("-0530") == -(5 * 3600 + 30 * 60)
    assert parse_offset("UTC+8") == 8 * 3600
    assert parse_offset("Z") == 0
    assert parse_offset("") is None and parse_offset("abc") is None
    assert format_offset(-18000) == "-05:00"


def test_photo_utc_uses_offset_and_default():
    g = _g(0, "2026-03-10T12:00:00", "+09:00")
    t, aware = photo_utc(g, None)
    assert aware and datetime.fromtimestamp(t, timezone.utc) == datetime(2026, 3, 10, 3, tzinfo=timezone.utc)
    g2 = _g(1, "2026-03-10T12:00:00", None)
    assert photo_utc(g2, None)[1] is False
    t2, aware2 = photo_utc(g2, 8 * 3600)
    assert aware2 and datetime.fromtimestamp(t2, timezone.utc).hour == 4


def test_gpx_parse_and_interpolate(tmp_path):
    t0 = datetime(2026, 3, 10, 3, 0, tzinfo=timezone.utc)
    p = write_gpx(tmp_path / "a.gpx", [(t0, 35.00, 135.70), (t0 + timedelta(minutes=10), 35.02, 135.72)])
    pts = load_track_file(str(p))
    assert len(pts) == 2 and pts[0].aware
    idx = TrackIndex(pts)
    lat, lon, near, gap = idx.locate((t0 + timedelta(minutes=5)).timestamp(), "utc", 1800)
    assert abs(lat - 35.01) < 1e-6 and abs(lon - 135.71) < 1e-6 and gap == 300
    assert idx.locate((t0 + timedelta(hours=2)).timestamp(), "utc", 1800) is None
    assert idx.locate((t0 + timedelta(minutes=5)).timestamp(), "local", 1800) is None   # GPX 没有当地时间


def test_kml_gx_track(tmp_path):
    p = tmp_path / "t.kml"
    p.write_text('<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">'
                 "<Document><Placemark><gx:Track><when>2026-03-10T03:00:00Z</when><when>2026-03-10T03:05:00Z</when>"
                 "<gx:coord>135.7 35.0 10</gx:coord><gx:coord>135.8 35.1 10</gx:coord></gx:Track></Placemark>"
                 "</Document></kml>", "utf-8")
    pts = load_track_file(str(p))
    assert [(round(x.lat, 2), round(x.lon, 2)) for x in pts] == [(35.0, 135.7), (35.1, 135.8)]


def test_fill_locations_timezone_aware():
    t_utc = datetime(2026, 3, 10, 3, 0, tzinfo=timezone.utc).timestamp()
    idx = TrackIndex([TrackPoint(t_utc, 35.0, 135.7, None, "track", "a.gpx")])
    ok = _g(0, "2026-03-10T12:05:00", "+09:00")             # 日本时间 12:05 = UTC 03:05 → 匹配
    wrong_tz = _g(1, "2026-03-10T12:05:00", "+08:00")       # 相机时区设成北京时间 → 差 1 小时 → 不匹配
    has_gps = _g(2, "2026-03-10T12:00:00", geo={"has_gps": True, "source": "gps", "country": "中国",
                                                 "lat": 30.0, "lon": 120.0})
    st = fill_locations([ok, wrong_tz, has_gps], idx, _Geo(), 30, None)
    assert st["matched"] == 1
    assert ok.feats["geo"]["source"] == "track" and ok.feats["geo"]["city"] == "京都市"
    assert ok.feats["geo"]["match_gap_s"] == 300
    assert wrong_tz.feats["geo"]["source"] is None
    assert has_gps.feats["geo"]["country"] == "中国"      # 自带 GPS 的不动


def test_fill_locations_naive_fallback():
    # 参考点没有时区：按“当地时间”比较
    naive = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    idx = TrackIndex([TrackPoint(None, 34.6, 135.5, naive, "reference", "IMG_1.HEIC")])
    g = _g(0, "2026-03-10T12:10:00", "+09:00")
    fill_locations([g], idx, _Geo(), 30, None)
    assert g.feats["geo"]["source"] == "track" and g.feats["geo"]["city"] == "大阪市"
    assert g.feats["geo"]["match_kind"] == "reference"


def test_camera_without_timezone_matches_phone_local_time():
    # 老相机不写时区；手机照片有时区 → 用双方的“当地时间”比对
    from photocrate.core.geotime import reference_point_from_meta
    p = reference_point_from_meta({"lat": 35.0, "lon": 135.76, "dt_original": "2026:03:10 12:00:00",
                                   "offset": "+09:00"}, "IMG_1.JPG", None)
    assert p.utc is not None and p.local is not None
    g = _g(0, "2026-03-10T12:20:00", None)
    st = fill_locations([g], TrackIndex([p]), _Geo(), 30, None)
    assert st["matched"] == 1 and g.feats["geo"]["match_gap_s"] == 1200
    # 设置了默认时区（相机是北京时间 +08:00）后按 UTC 比对：实际相差 80 分钟 → 不匹配，也不再按当地时间误配
    g2 = _g(1, "2026-03-10T12:20:00", None)
    fill_locations([g2], TrackIndex([p]), _Geo(), 30, 8 * 3600)
    assert g2.feats["geo"]["source"] is None
    g3 = _g(2, "2026-03-10T11:10:00", None)      # 北京时间 11:10 = 日本 12:10 → 匹配
    fill_locations([g3], TrackIndex([p]), _Geo(), 30, 8 * 3600)
    assert g3.feats["geo"]["source"] == "track" and g3.feats["geo"]["match_gap_s"] == 600


def test_reference_point_uses_gps_time_when_no_offset():
    from photocrate.core.geotime import reference_point_from_meta
    p = reference_point_from_meta({"lat": 35.0, "lon": 135.76, "dt_original": "2026:03:10 12:00:00",
                                   "gps_utc": "2026:03:10 03:00:05Z"}, "IMG_2.JPG", None)
    assert datetime.fromtimestamp(p.utc, timezone.utc) == datetime(2026, 3, 10, 3, 0, 5, tzinfo=timezone.utc)
    assert reference_point_from_meta({"dt_original": "2026:03:10 12:00:00"}, "x", None) is None


# ---------------------------------------------------------------------------
# 时间校正
# ---------------------------------------------------------------------------

def test_apply_time_shift():
    d = {"dt": "2026-03-10T23:30:00", "source": "exif", "tz": "+08:00"}
    r = apply_time_shift(d, 3600, "+09:00")
    assert r["dt"] == "2026-03-11T00:30:00" and r["orig_dt"] == d["dt"] and r["tz"] == "+09:00"
    assert r["corrected"] and r["shift"] == 3600 and r["orig_tz"] == "+08:00"
    assert apply_time_shift(d, 0, None) is d


# ---------------------------------------------------------------------------
# 行程文本
# ---------------------------------------------------------------------------

def test_parse_itinerary():
    lines = parse_itinerary("# 注释\n2026-03-08 东京\n2026-03-09 ~ 2026-03-11  日本/京都\n3/12 大阪\n"
                            "3月13日-3月14日 奈良\n2026-03-15\nxx 乱写\n2026-03-20~2026-03-18 反了", 2026)
    ok = [x for x in lines if not x.error]
    assert [(str(x.start), str(x.end), x.place) for x in ok] == [
        ("2026-03-08", "2026-03-08", "东京"), ("2026-03-09", "2026-03-11", "日本/京都"),
        ("2026-03-12", "2026-03-12", "大阪"), ("2026-03-13", "2026-03-14", "奈良")]
    bad = [x for x in lines if x.error]
    assert len(bad) == 2 and "看不懂" in bad[0].error and "早于" in bad[1].error
    assert len(ok[1].days()) == 3


# ---------------------------------------------------------------------------
# 跨文件夹配对
# ---------------------------------------------------------------------------

def _pg(gid, path, role, dt, model="ILCE-7M4"):
    g = PhotoGroup(gid, [MemberFile(path, role, 10, 1)], path)
    g.meta = {"dt_original": dt, "model": model}
    return g


def test_pair_across_folders():
    a_raw = _pg(0, "/x/ARW/DSC1.ARW", "raw", "2026:03:10 12:00:00")
    a_jpg = _pg(1, "/x/JPG/DSC1.JPG", "jpeg", "2026:03:10 12:00:01")
    # 同名但不同时间（相机编号循环）→ 不配对
    b_raw = _pg(2, "/x/ARW/DSC2.ARW", "raw", "2026:03:10 12:00:00")
    b_jpg = _pg(3, "/x/2025/DSC2.JPG", "jpeg", "2025:01:01 08:00:00")
    # 同名同时间但相机不同 → 不配对
    c_raw = _pg(4, "/x/ARW/DSC3.ARW", "raw", "2026:03:10 13:00:00")
    c_jpg = _pg(5, "/x/JPG/DSC3.JPG", "jpeg", "2026:03:10 13:00:00", model="X100V")
    # 一个 RAW 对两个都符合的 JPG → 有歧义，不配对
    d_raw = _pg(6, "/x/ARW/DSC4.ARW", "raw", "2026:03:10 14:00:00")
    d_j1 = _pg(7, "/x/JPG/DSC4.JPG", "jpeg", "2026:03:10 14:00:00")
    d_j2 = _pg(8, "/x/JPG2/DSC4.JPG", "jpeg", "2026:03:10 14:00:00")
    groups = [a_raw, a_jpg, b_raw, b_jpg, c_raw, c_jpg, d_raw, d_j1, d_j2]
    assert pair_across_folders(groups) == 1
    groups = renumber(groups)
    assert len(groups) == 8
    merged = next(g for g in groups if g.name == "DSC1.JPG")
    assert merged.kind == "raw+jpeg" and merged.cross_folder and merged.primary.endswith("DSC1.JPG")
    assert [g.gid for g in groups] == list(range(8))


def test_annotation_found_by_any_member():
    raw = MemberFile("/x/ARW/DSC1.ARW", "raw", 10, 1)
    jpg = MemberFile("/x/JPG/DSC1.JPG", "jpeg", 20, 2)
    pair = PhotoGroup(0, [jpg, raw], jpg.path)
    table = {s: {"country": "日本"} for s in pair.member_sigs}
    alone = PhotoGroup(1, [MemberFile("/y/DSC1.ARW", "raw", 10, 1)], "/y/DSC1.ARW")   # 移动后单独扫描到的 RAW
    assert alone.annotation(table) == {"country": "日本"}


# ---------------------------------------------------------------------------
# Library 端到端：扫描 → 参考照片补 GPS → 手动地点 → 时间校正 → 按天汇总 → 计划
# ---------------------------------------------------------------------------

def test_library_end_to_end(tmp_path):
    from photocrate.ops.planner import build_plan
    from photocrate.partition.base import PartitionContext
    from photocrate.partition.engine import PartitionSpec, Rule

    cam = tmp_path / "cam"
    ref = tmp_path / "phone"
    # 相机：3/10 两张（京都时段），3/11 一张（无参考），3/12 一张（23:30，时钟慢 1 小时 → 校正后跨天）
    make_jpeg(cam / "DSC0001.JPG", datetime(2026, 3, 10, 10, 0), "+09:00")
    make_jpeg(cam / "DSC0002.JPG", datetime(2026, 3, 10, 15, 0), "+09:00")
    make_jpeg(cam / "DSC0003.JPG", datetime(2026, 3, 11, 9, 0), "+09:00")
    make_jpeg(cam / "DSC0004.JPG", datetime(2026, 3, 12, 23, 30), "+09:00")
    # 手机参考照片（京都 / 大阪），时区写的是 +09:00
    make_jpeg(ref / "IMG_1.JPG", datetime(2026, 3, 10, 10, 10), "+09:00", gps=(35.0116, 135.7681), make="Apple",
              model="iPhone 15")
    make_jpeg(ref / "IMG_2.JPG", datetime(2026, 3, 10, 15, 20), "+09:00", gps=(34.6937, 135.5023), make="Apple",
              model="iPhone 15")
    lib = _library(tmp_path, gps_ref_dirs=[str(ref)], gps_fill_minutes=30)
    rep = lib.scan([str(cam)], True)
    assert rep.groups == 4
    by = {g.name: g for g in lib.groups}
    g1, g2, g3, g4 = (by[f"DSC000{i}.JPG"] for i in range(1, 5))
    assert lib.ref_sources[0]["points"] == 2
    assert g1.feat("geo", "source") == "track" and g1.feat("geo", "province") == "京都府"
    assert g2.feat("geo", "province") == "大阪府"
    assert g3.feat("geo", "located") is False

    # 手动指定 3/11 → 京都；默认不覆盖轨迹匹配到的
    from photocrate.core.places import PlaceIndex
    place = PlaceIndex.get().resolve("日本/京都")
    assert lib.set_manual_place([g1.gid, g3.gid], place, override_track=False) == 1
    # 时间校正：DSC0004 +1 小时 → 3/13 00:30
    prev = lib.preview_time_shift([g4.gid], 3600, None)
    assert prev[0][2] == datetime(2026, 3, 13, 0, 30)
    lib.set_time_shift([g4.gid], 3600, None)
    lib.run_meta_analyzers()
    assert g3.feat("geo", "source") == "manual" and g3.feat("geo", "province") == "京都府"
    assert g1.feat("geo", "source") == "track"
    assert g4.date == datetime(2026, 3, 13, 0, 30) and g4.feat("date", "corrected")
    # 再次校正是“替换”而不是叠加
    assert lib.preview_time_shift([g4.gid], 7200, None)[0][2] == datetime(2026, 3, 13, 1, 30)

    days = {r.key: r for r in build_days(lib.groups)}
    assert set(days) == {"2026-03-10", "2026-03-11", "2026-03-13"}
    assert days["2026-03-10"].sources["track"] == 2 and days["2026-03-11"].sources["manual"] == 1
    assert days["2026-03-13"].shifts[3600] == 1

    plan = build_plan(lib.groups, PartitionSpec("rules", [Rule("date", {"granularity": "ymd"})]),
                      PartitionContext(lib.settings.snapshot()), str(tmp_path / "out"))
    assert any("时间匹配" in w for w in plan.warnings)
    assert any("校正后的拍摄时间" in w for w in plan.warnings)
    dst = {os.path.basename(op.dst): os.path.basename(os.path.dirname(op.dst)) for it in plan.items for op in it.ops}
    assert dst["DSC0004.JPG"] == "2026-03-13"

    # 标注在重新扫描后仍然有效；清除后恢复
    lib.scan([str(cam)], True)
    by = {g.name: g for g in lib.groups}
    assert by["DSC0003.JPG"].feat("geo", "source") == "manual"
    assert by["DSC0004.JPG"].date == datetime(2026, 3, 13, 0, 30)
    lib.set_time_shift([by["DSC0004.JPG"].gid], None)
    lib.set_manual_place([by["DSC0003.JPG"].gid], None)
    lib.run_meta_analyzers()
    assert by["DSC0004.JPG"].date == datetime(2026, 3, 12, 23, 30)
    assert by["DSC0003.JPG"].feat("geo", "located") is False
    lib.close()


def test_library_gpx_and_library_points(tmp_path):
    cam = tmp_path / "trip"
    make_jpeg(cam / "DSC0010.JPG", datetime(2026, 3, 10, 12, 0), "+09:00")
    make_jpeg(cam / "DSC0011.JPG", datetime(2026, 3, 10, 18, 0), "+09:00")
    # 同批里的手机照片自带 GPS（大阪，18:05）
    make_jpeg(cam / "IMG_9.JPG", datetime(2026, 3, 10, 18, 5), "+09:00", gps=(34.6937, 135.5023), make="Apple",
              model="iPhone 15")
    gpx = write_gpx(tmp_path / "day.gpx", [(datetime(2026, 3, 10, 2, 50, tzinfo=timezone.utc), 35.0, 135.76),
                                           (datetime(2026, 3, 10, 3, 10, tzinfo=timezone.utc), 35.02, 135.78)])
    lib = _library(tmp_path, gps_track_files=[str(gpx)])
    lib.scan([str(cam)], True)
    by = {g.name: g for g in lib.groups}
    assert by["DSC0010.JPG"].feat("geo", "match_kind") == "track"
    assert by["DSC0011.JPG"].feat("geo", "match_kind") == "library"
    assert by["DSC0011.JPG"].feat("geo", "province") == "大阪府"
    # 关闭“同批照片”参考后，18:00 那张就没有地点了
    lib.settings.set("gps_fill_library", False)
    lib.run_meta_analyzers()
    assert by["DSC0011.JPG"].feat("geo", "located") is False
    lib.close()


# ---------------------------------------------------------------------------
# 写回 XMP（需要 ExifTool）
# ---------------------------------------------------------------------------

@need_et
def test_xmp_write_read_and_undo(tmp_path, monkeypatch):
    from photocrate.ops import mover as mover_mod
    from photocrate.ops.journal import Journal
    from photocrate.ops.mover import Mover
    from photocrate.ops.xmpwrite import build_xmp_plan, write_xmp

    d = tmp_path / "raw"
    d.mkdir()
    raw = d / "DSC0100.ARW"
    raw.write_bytes(b"\0" * 2048)
    old = d / "DSC0101.ARW"
    old.write_bytes(b"\0" * 2048)
    (d / "DSC0101.xmp").write_text("<x:xmpmeta xmlns:x='adobe:ns:meta/'/>", "utf-8")   # 已有修图记录
    jpg = make_jpeg(d / "IMG_5.JPG", datetime(2026, 3, 10, 12, 0), "+09:00")

    def grp(gid, p, role):
        st = os.stat(p)
        g = PhotoGroup(gid, [MemberFile(str(p), role, st.st_size, st.st_mtime_ns)], str(p))
        g.feats["geo"] = {"has_gps": False, "source": "track", "lat": 35.0116, "lon": 135.7681, "country": "日本",
                          "province": "京都府", "city": "京都市", "cc": "JP", "located": True}
        g.feats["date"] = apply_time_shift({"dt": "2026-03-10T11:00:00", "source": "exif", "tz": "+08:00"},
                                           3600, "+09:00")
        return g

    groups = [grp(0, raw, "raw"), grp(1, old, "raw"), grp(2, jpg, "jpeg")]
    plan = build_xmp_plan(groups)
    assert len(plan.tasks) == 1 and plan.skipped_exists == 1 and plan.skipped_no_raw == 1
    j = Journal(tmp_path / "j.db")
    res = write_xmp(plan, ET, j)
    xmp = d / "DSC0100.xmp"
    assert res.failed == 0 and xmp.exists() and res.written == {0: [str(xmp)]}
    text = xmp.read_text("utf-8")
    assert "京都市" in text and "2026-03-10T12:00:00+09:00" in text
    assert j.batch(res.batch_id)["operation"] == "xmp"

    # 再次扫描：.xmp 跟着 RAW 成组，GPS 从附属文件读出来
    lib = _library(tmp_path)
    lib.scan([str(d)], True)
    g = next(x for x in lib.groups if x.name == "DSC0100.ARW")
    assert any(f.path.endswith("DSC0100.xmp") for f in g.files)
    assert abs(g.meta["lat"] - 35.0116) < 1e-4 and g.feat("geo", "source") == "gps"

    # 撤销 = 新建的 .xmp 进回收站；原有的 .xmp 不受影响
    trashed = []
    monkeypatch.setattr(mover_mod, "trash", lambda p: (trashed.append(p), os.remove(p)))
    r = Mover(j).undo(res.batch_id)
    assert r.done_files == 1 and not xmp.exists() and (d / "DSC0101.xmp").exists()
    assert j.batch(res.batch_id)["status"] == "undone"
    assert lib.prune_missing() == 1
    assert "lat" not in g.meta or g.meta.get("lat") is None
    lib.close()


@need_et
def test_xmp_recovery_never_moves_photos(tmp_path):
    from photocrate.ops.journal import Journal
    from photocrate.ops.mover import Mover
    from photocrate.ops.xmpwrite import XmpPlan, XmpTask

    raw = tmp_path / "DSC1.ARW"
    raw.write_bytes(b"\0" * 100)
    plan = XmpPlan(tasks=[XmpTask(0, str(raw), str(tmp_path / "DSC1.xmp"), {"XMP-photoshop:City": "x"}, ["地点"])])
    j = Journal(tmp_path / "j.db")
    bid = j.create_batch(plan)            # 模拟写到一半崩溃：批次停在 running
    res = Mover(j).resume(bid)
    assert raw.exists() and not (tmp_path / "DSC1.xmp").exists()
    assert j.batch(bid)["status"] == "cancelled" and res.done_files == 0


@need_et
def test_xmp_undo_follows_later_moves(tmp_path, monkeypatch):
    """写入 XMP 后照片（连同 .xmp）又被分区移动过：撤销 XMP 批次时要在新位置找到 .xmp 并放入回收站。"""
    from photocrate.ops import mover as mover_mod
    from photocrate.ops.journal import Journal
    from photocrate.ops.mover import Mover
    from photocrate.ops.planner import build_plan
    from photocrate.ops.xmpwrite import build_xmp_plan, write_xmp
    from photocrate.partition.base import PartitionContext
    from photocrate.partition.engine import PartitionSpec, Rule

    src = tmp_path / "src"
    src.mkdir()
    raw = src / "DSC0200.ARW"
    raw.write_bytes(b"\0" * 4096)
    st = os.stat(raw)
    g = PhotoGroup(0, [MemberFile(str(raw), "raw", st.st_size, st.st_mtime_ns)], str(raw))
    g.feats["geo"] = {"has_gps": False, "source": "track", "lat": 35.0, "lon": 135.7, "country": "日本",
                      "province": "京都府", "city": "京都", "cc": "JP", "located": True}
    g.feats["date"] = {"dt": "2026-03-10T12:00:00", "source": "exif", "tz": "+09:00"}
    j = Journal(tmp_path / "j.db")
    res = write_xmp(build_xmp_plan([g]), ET, j)
    xmp = src / "DSC0200.xmp"
    assert xmp.exists()
    st2 = os.stat(xmp)
    g.files.append(MemberFile(str(xmp), "sidecar", st2.st_size, st2.st_mtime_ns))
    plan = build_plan([g], PartitionSpec("rules", [Rule("fixed", {"name": "整理后"})]),
                      PartitionContext(Settings().snapshot()), str(tmp_path / "out"))
    mres = Mover(j).execute(plan)
    moved_xmp = tmp_path / "out" / "整理后" / "DSC0200.xmp"
    assert moved_xmp.exists() and not xmp.exists() and mres.done_files == 2
    trashed = []
    monkeypatch.setattr(mover_mod, "trash", lambda p: (trashed.append(p), os.remove(p)))
    r = Mover(j).undo(res.batch_id)
    assert r.done_files == 1 and not moved_xmp.exists()
    assert (tmp_path / "out" / "整理后" / "DSC0200.ARW").exists()     # 照片本身不动
    assert os.path.normcase(trashed[0]) == os.path.normcase(str(moved_xmp))
    # 再撤销移动批次：照片回到原处；已进回收站的 .xmp 不算失败
    r2 = Mover(j).undo(mres.batch_id)
    assert r2.failed_groups == 0 and raw.exists() and not xmp.exists()
    assert j.batch(mres.batch_id)["status"] == "undone"
