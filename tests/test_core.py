"""核心逻辑测试：python -m pytest tests -q"""
from __future__ import annotations

import errno
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

from photocrate.bootstrap import bootstrap  # noqa: E402
from photocrate.config import Settings  # noqa: E402

bootstrap(Settings(), load_user_plugins=False)

from photocrate.core.dates import DEFAULT_FILENAME_PATTERNS, compile_patterns, parse_exif_datetime, parse_filename_date  # noqa: E402
from photocrate.core.models import MemberFile, PhotoGroup  # noqa: E402
from photocrate.core.scanner import scan  # noqa: E402
from photocrate.ops import mover as mover_mod  # noqa: E402
from photocrate.ops.journal import Journal  # noqa: E402
from photocrate.ops.mover import Mover  # noqa: E402
from photocrate.ops.pathsafe import sanitize_component  # noqa: E402
from photocrate.ops.planner import build_plan  # noqa: E402
from photocrate.partition.base import PartitionContext  # noqa: E402
from photocrate.partition.engine import PartitionSpec, Rule, render_template  # noqa: E402

PAT = compile_patterns(DEFAULT_FILENAME_PATTERNS)


# ---------------------------------------------------------------------------
# 日期
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expect", [
    ("微信图片_20231001123045.jpg", datetime(2023, 10, 1, 12, 30, 45)),
    ("IMG_20231001_123045.jpg", datetime(2023, 10, 1, 12, 30, 45)),
    ("Screenshot_2023-10-01-12-30-45.png", datetime(2023, 10, 1, 12, 30, 45)),
    ("屏幕截图 2023-10-01 123045.png", datetime(2023, 10, 1, 12, 30, 45)),
    ("PXL_20231001_123045123.jpg", datetime(2023, 10, 1, 12, 30, 45)),
    ("2023年10月1日.jpg", datetime(2023, 10, 1)),
    ("DSC01234.JPG", None),
    ("IMG_9999.JPG", None),
])
def test_filename_dates(name, expect):
    dt, _ = parse_filename_date(name, PAT)
    assert dt == expect


def test_mmexport_timestamp():
    dt, rule = parse_filename_date("mmexport1696123456789.jpg", PAT)
    assert dt == datetime.fromtimestamp(1696123456.789)
    assert "微信" in rule


def test_exif_datetime():
    assert parse_exif_datetime("2023:10:01 12:30:45")[0] == datetime(2023, 10, 1, 12, 30, 45)
    dt, tz = parse_exif_datetime("2018:02:21 07:08:56-05:00")
    assert dt == datetime(2018, 2, 21, 7, 8, 56) and tz == "-05:00"
    assert parse_exif_datetime("0000:00:00 00:00:00")[0] is None
    assert parse_exif_datetime("garbage")[0] is None


def test_sanitize():
    assert sanitize_component("EF-M15-45mm f/3.5-6.3") == "EF-M15-45mm f-3.5-6.3"
    assert sanitize_component("CON") == "_CON"
    assert sanitize_component("a:b*c?") == "a-b_c_"
    assert sanitize_component("  trailing. ") == "trailing"


# ---------------------------------------------------------------------------
# 扫描分组
# ---------------------------------------------------------------------------

def _touch(p: Path, data: bytes = b"x" * 100) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_grouping(tmp_path):
    d = tmp_path / "src"
    for n in ("DSC01.ARW", "DSC01.JPG", "DSC01.ARW.xmp", "IMG_1234.HEIC", "IMG_O1234.AAE", "IMG_1234.MOV",
              "IMG_5.JPG", "IMG_5.JPG.json", "lonely.xmp", "note.txt", "single.png"):
        _touch(d / n)
    groups = scan([str(d)])
    names = {g.name: sorted(f.name for f in g.files) for g in groups}
    assert names["DSC01.JPG"] == ["DSC01.ARW", "DSC01.ARW.xmp", "DSC01.JPG"]
    assert names["IMG_1234.HEIC"] == ["IMG_1234.HEIC", "IMG_1234.MOV", "IMG_O1234.AAE"]
    assert names["IMG_5.JPG"] == ["IMG_5.JPG", "IMG_5.JPG.json"]
    assert names["single.png"] == ["single.png"]
    assert len(groups) == 4
    g = next(g for g in groups if g.name == "DSC01.JPG")
    assert g.kind == "raw+jpeg" and g.kind_label == "RAW+JPG"


# ---------------------------------------------------------------------------
# 计划与移动
# ---------------------------------------------------------------------------

def _group(gid, paths, date=None):
    files = []
    for p in paths:
        st = os.stat(p)
        role = {".arw": "raw", ".jpg": "jpeg", ".xmp": "sidecar"}.get(os.path.splitext(p)[1].lower(), "jpeg")
        files.append(MemberFile(str(p), role, st.st_size, st.st_mtime_ns))
    prim = next((f.path for f in files if f.role == "jpeg"), files[0].path)
    g = PhotoGroup(gid, files, prim)
    if date:
        g.feats["date"] = {"dt": date.isoformat(), "source": "exif"}
    return g


def _ctx():
    return PartitionContext(Settings().snapshot())


def test_plan_conflict_keeps_pairs_together(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    a = [_touch(src / "DSC01.ARW"), _touch(src / "DSC01.JPG"), _touch(src / "DSC01.ARW.xmp")]
    # 目标里已有同名 JPG → 整组加同一个编号
    _touch(dst / "2024" / "2024-05" / "DSC01.JPG", b"other")
    g = _group(0, a, datetime(2024, 5, 3))
    spec = PartitionSpec("rules", [Rule("date", {"granularity": "y/ym"})])
    plan = build_plan([g], spec, _ctx(), str(dst))
    names = sorted(os.path.basename(op.dst) for op in plan.items[0].ops)
    assert names == ["DSC01 (1).ARW", "DSC01 (1).ARW.xmp", "DSC01 (1).JPG"]
    assert plan.renamed == 1
    # 跳过策略
    plan2 = build_plan([g], spec, _ctx(), str(dst), conflict="skip")
    assert not plan2.items and plan2.skipped_conflict == [0]


def test_execute_undo_roundtrip(tmp_path):
    src = tmp_path / "src"
    files = [_touch(src / f"IMG_{i}.JPG", bytes([i]) * (100 + i)) for i in range(5)]
    groups = [_group(i, [f], datetime(2020 + i, 1, 1)) for i, f in enumerate(files)]
    spec = PartitionSpec("rules", [Rule("date", {"granularity": "y"})])
    plan = build_plan(groups, spec, _ctx(), str(tmp_path / "out"))
    j = Journal(tmp_path / "j.db")
    m = Mover(j)
    res = m.execute(plan)
    assert res.done_files == 5 and not res.errors
    assert all(not f.exists() for f in files)
    assert (tmp_path / "out" / "2022" / "IMG_2.JPG").exists()
    u = m.undo(res.batch_id)
    assert u.done_files == 5
    assert all(f.exists() for f in files)
    assert not (tmp_path / "out").exists()       # 新建的空文件夹被清理
    assert j.batch(res.batch_id)["status"] == "undone"


def test_cross_device_copy_verify_delete(tmp_path, monkeypatch):
    src = tmp_path / "src"
    f = _touch(src / "A.JPG", b"hello" * 1000)
    g = _group(0, [f], datetime(2021, 1, 1))
    plan = build_plan([g], PartitionSpec("rules", [Rule("date", {"granularity": "y"})]), _ctx(), str(tmp_path / "o"))
    real_rename = os.rename
    calls = {"n": 0}

    def fake_rename(a, b):
        # 第一次（直接改名）模拟跨盘失败，之后（.pctmp → 最终名）正常
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "cross-device")
        return real_rename(a, b)

    monkeypatch.setattr(mover_mod.os, "rename", fake_rename)
    res = Mover(Journal(tmp_path / "j.db"), verify_hash=True).execute(plan)
    assert res.done_files == 1
    out = tmp_path / "o" / "2021" / "A.JPG"
    assert out.read_bytes() == b"hello" * 1000
    assert not f.exists()
    assert not list((tmp_path / "o" / "2021").glob("*.pctmp"))


def test_group_rollback_on_failure(tmp_path, monkeypatch):
    src = tmp_path / "src"
    a = [_touch(src / "DSC02.ARW"), _touch(src / "DSC02.JPG")]
    g = _group(0, a, datetime(2022, 2, 2))
    plan = build_plan([g], PartitionSpec("rules", [Rule("date", {"granularity": "y"})]), _ctx(), str(tmp_path / "o"))
    real = mover_mod.transfer
    n = {"i": 0}

    def flaky(s, d, copy_only=False, verify_hash=False):
        n["i"] += 1
        if n["i"] == 2:
            raise IOError("磁盘出错")
        return real(s, d, copy_only, verify_hash)

    monkeypatch.setattr(mover_mod, "transfer", flaky)
    res = Mover(Journal(tmp_path / "j.db")).execute(plan)
    assert res.failed_groups == 1
    assert all(p.exists() for p in a)        # 整组退回原处


def test_recovery_reconcile(tmp_path):
    src = tmp_path / "src"
    f1 = _touch(src / "A.JPG", b"a" * 50)
    f2 = _touch(src / "B.JPG", b"b" * 60)
    groups = [_group(0, [f1], datetime(2021, 1, 1)), _group(1, [f2], datetime(2021, 1, 1))]
    plan = build_plan(groups, PartitionSpec("rules", [Rule("date", {"granularity": "y"})]), _ctx(), str(tmp_path / "o"))
    j = Journal(tmp_path / "j.db")
    bid = j.create_batch(plan)
    # 模拟崩溃：A 已经移动但日志没来得及标记；B 留下了 .pctmp 半成品
    os.makedirs(tmp_path / "o" / "2021")
    os.rename(f1, tmp_path / "o" / "2021" / "A.JPG")
    _touch(tmp_path / "o" / "2021" / "B.JPG.pctmp", b"b" * 10)
    m = Mover(j)
    assert j.incomplete()[0]["id"] == bid
    res = m.resume(bid)
    assert res.done_files == 1                         # 只剩 B 需要处理
    assert (tmp_path / "o" / "2021" / "B.JPG").read_bytes() == b"b" * 60
    assert not (tmp_path / "o" / "2021" / "B.JPG.pctmp").exists()
    assert j.batch(bid)["status"] == "done"
    m.undo(bid)
    assert f1.exists() and f2.exists()


def test_undo_when_original_spot_taken(tmp_path):
    src = tmp_path / "src"
    f = _touch(src / "C.JPG", b"c" * 30)
    g = _group(0, [f], datetime(2021, 1, 1))
    plan = build_plan([g], PartitionSpec("rules", [Rule("date", {"granularity": "y"})]), _ctx(), str(tmp_path / "o"))
    j = Journal(tmp_path / "j.db")
    m = Mover(j)
    res = m.execute(plan)
    _touch(src / "C.JPG", b"new")                      # 原位置被新文件占用
    u = m.undo(res.batch_id)
    assert (src / "C.JPG").read_bytes() == b"new"      # 不覆盖
    assert (src / "C (恢复1).JPG").read_bytes() == b"c" * 30
    assert u.errors


def test_template_fallback():
    g = PhotoGroup(0, [MemberFile("/x/IMG_1.JPG", "jpeg", 1, 1)], "/x/IMG_1.JPG")
    g.feats["date"] = {"dt": "2026-10-19T10:00:00", "source": "exif"}
    g.feats["geo"] = {"has_gps": True, "country": "中国", "province": "湖南省", "city": None}
    segs = render_template("{年}/{年}-{月} {市|省|未知地点}", g, _ctx())
    assert segs == ["2026", "2026-10 湖南省"]
    g.feats["geo"] = {"has_gps": False}
    assert render_template("{年}/{市|省|未知地点}", g, _ctx()) == ["2026", "未知地点"]


def test_region_dimension_collapses_municipality():
    from photocrate.partition.base import DIMENSIONS
    g = PhotoGroup(0, [MemberFile("/x/a.jpg", "jpeg", 1, 1)], "/x/a.jpg")
    g.feats["geo"] = {"has_gps": True, "country": "中国", "province": "北京市", "city": "北京市",
                      "district": "东城区", "in_china": True}
    d = DIMENSIONS["region"]
    assert d.segments(g, d.resolve_opts({"level": "country/province/city"}), _ctx()) == ["中国", "北京市"]
    assert d.segments(g, d.resolve_opts({"level": "province/city"}), _ctx()) == ["北京市"]


def test_geo_lookup():
    from photocrate.core.geo import GeoService
    geo = GeoService()
    r = geo.lookup(27.2418, 111.4674)
    assert r["province"] == "湖南省" and r["city"] == "邵阳市"
    r = geo.lookup(35.6895, 139.6917)
    assert r["country"] == "日本" and r["province"] == "东京都"
    r = geo.lookup(26.36, -80.10)
    assert r["country"] == "美国" and r["province"] == "佛罗里达州"


def test_cross_device_locked_source_leaves_no_copy(tmp_path, monkeypatch):
    src = tmp_path / "src"
    f = _touch(src / "L.JPG", b"locked" * 100)
    g = _group(0, [f], datetime(2021, 1, 1))
    plan = build_plan([g], PartitionSpec("rules", [Rule("date", {"granularity": "y"})]), _ctx(), str(tmp_path / "o"))
    real_rename, real_remove = os.rename, mover_mod._remove
    state = {"first": True}

    def fake_rename(a, b):
        if state["first"]:
            state["first"] = False
            raise OSError(errno.EXDEV, "cross-device")
        return real_rename(a, b)

    def fake_remove(p):
        if os.path.abspath(p) == os.path.abspath(str(f)):
            raise PermissionError("file in use")
        return real_remove(p)

    monkeypatch.setattr(mover_mod.os, "rename", fake_rename)
    monkeypatch.setattr(mover_mod, "_remove", fake_remove)
    res = Mover(Journal(tmp_path / "j.db")).execute(plan)
    assert res.failed_groups == 1
    assert f.exists()
    assert not (tmp_path / "o" / "2021" / "L.JPG").exists()


# ---------------------------------------------------------------------------
# 按单个文件格式拆分（RAW 与 JPG 各放各的）
# ---------------------------------------------------------------------------

def _group_roles(gid, paths_roles, date=None):
    files = []
    for p, role in paths_roles:
        st = os.stat(p)
        files.append(MemberFile(str(p), role, st.st_size, st.st_mtime_ns))
    prim = next((f.path for f in files if f.role == "jpeg"), files[0].path)
    g = PhotoGroup(gid, files, prim)
    if date:
        g.feats["date"] = {"dt": date.isoformat(), "source": "exif"}
    return g


def test_format_split_plan_and_tree(tmp_path):
    from photocrate.partition.engine import build_tree
    src = tmp_path / "src"
    g = _group_roles(0, [(_touch(src / "DSC01.ARW", b"r" * 300), "raw"), (_touch(src / "DSC01.JPG", b"j" * 100), "jpeg"),
                         (_touch(src / "DSC01.ARW.xmp", b"x" * 10), "sidecar")], datetime(2026, 1, 5))
    h = _group_roles(1, [(_touch(src / "IMG_2.HEIC", b"h" * 50), "heif"), (_touch(src / "IMG_O2.AAE", b"a"), "sidecar")])
    spec = PartitionSpec("rules", [Rule("filetype", {"mode": "file"})])
    plan = build_plan([g, h], spec, _ctx(), str(tmp_path / "out"))
    dst = {os.path.basename(op.src): os.path.relpath(op.dst, tmp_path / "out") for it in plan.items for op in it.ops}
    assert dst["DSC01.ARW"] == os.path.join("ARW", "DSC01.ARW")
    assert dst["DSC01.ARW.xmp"] == os.path.join("ARW", "DSC01.ARW.xmp")
    assert dst["DSC01.JPG"] == os.path.join("JPG", "DSC01.JPG")
    assert dst["IMG_O2.AAE"] == os.path.join("HEIC", "IMG_O2.AAE")
    root, _ = build_tree([g, h], spec, _ctx())
    kids = {c.name: (c.count, c.size) for c in root.children.values()}
    assert kids == {"ARW": (1, 310), "JPG": (1, 100), "HEIC": (1, 51)}
    # 与日期组合：格式在第二级
    spec2 = PartitionSpec("rules", [Rule("date", {"granularity": "ymd"}), Rule("filetype", {"mode": "file"})])
    plan2 = build_plan([g], spec2, _ctx(), str(tmp_path / "out"))
    d2 = sorted(os.path.relpath(op.dst, tmp_path / "out") for op in plan2.items[0].ops)
    assert d2 == sorted([os.path.join("2026-01-05", "ARW", "DSC01.ARW"), os.path.join("2026-01-05", "ARW", "DSC01.ARW.xmp"),
                         os.path.join("2026-01-05", "JPG", "DSC01.JPG")])
    # 模板占位符 {格式}
    spec3 = PartitionSpec("template", [], "{年}/{格式}")
    plan3 = build_plan([g], spec3, _ctx(), str(tmp_path / "out"))
    assert os.path.join("2026", "JPG", "DSC01.JPG") in [os.path.relpath(op.dst, tmp_path / "out") for op in plan3.items[0].ops]


def test_format_split_execute_and_undo(tmp_path):
    src = tmp_path / "src"
    a = [(_touch(src / "DSC09.ARW", b"r" * 30), "raw"), (_touch(src / "DSC09.JPG", b"j" * 20), "jpeg")]
    g = _group_roles(0, a, datetime(2026, 1, 1))
    plan = build_plan([g], PartitionSpec("rules", [Rule("filetype", {})]), _ctx(), str(src))
    m = Mover(Journal(tmp_path / "j.db"))
    res = m.execute(plan)
    assert (src / "ARW" / "DSC09.ARW").exists() and (src / "JPG" / "DSC09.JPG").exists()
    m.undo(res.batch_id)
    assert (src / "DSC09.ARW").exists() and (src / "DSC09.JPG").exists()
    assert not (src / "ARW").exists() and not (src / "JPG").exists()


# ---------------------------------------------------------------------------
# 手动指定地点
# ---------------------------------------------------------------------------

def test_place_resolve():
    from photocrate.core.places import PlaceIndex
    idx = PlaceIndex.get()
    r = idx.resolve("日本/东京")
    assert r["country"] == "日本" and r["province"] == "东京都"
    r = idx.resolve("湖南/邵阳")
    assert r["country"] == "中国" and r["province"] == "湖南省" and r["city"] == "邵阳市"
    r = idx.resolve("日本/箱根")
    assert r["country"] == "日本" and r["city"] == "箱根"


def test_manual_place_used_by_region(tmp_path):
    from photocrate.analyzers.base import AnalysisContext
    from photocrate.analyzers.builtin import GeoAnalyzer
    from photocrate.partition.base import DIMENSIONS
    f = _touch(tmp_path / "A.JPG")
    g = _group(0, [f], datetime(2026, 1, 3))
    place = {"country": "日本", "province": "京都府", "city": "京都", "district": None, "cc": "JP"}
    ctx = AnalysisContext(settings={}, geo=None, extras={"manual_places": {g.primary_file.sig: place}})
    g.feats["geo"] = GeoAnalyzer().analyze(g, ctx)
    d = DIMENSIONS["region"]
    assert d.segments(g, d.resolve_opts({"level": "country/province/city"}), _ctx()) == ["日本", "京都府", "京都"]


def test_plan_counts_all_new_folder_levels(tmp_path):
    f = _touch(tmp_path / "src" / "A.JPG")
    g = _group(0, [f], datetime(2026, 1, 5))
    spec = PartitionSpec("rules", [Rule("date", {"granularity": "y/ym/ymd"})])
    plan = build_plan([g], spec, _ctx(), str(tmp_path / "out"))
    # out/、2026/、2026-01/、2026-01-05/ 四级都不存在
    assert plan.new_dirs == 4


def test_plan_warns_about_unknown_folders(tmp_path):
    f = _touch(tmp_path / "src" / "A.JPG")
    g = _group(0, [f], datetime(2026, 1, 5))
    g.feats["geo"] = {"has_gps": False}
    plan = build_plan([g], PartitionSpec("rules", [Rule("region", {})]), _ctx(), str(tmp_path / "out"))
    assert any("未知地点" in w for w in plan.warnings)
