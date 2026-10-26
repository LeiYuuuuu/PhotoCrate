"""第二梯队功能测试：星级/色标/留弃、写入 XMP（含更新已有 .xmp 与撤销）、相似连拍分组、
清理 RAW（回收站删除与找回）、批量重命名（含标注迁移与撤销）。

python -m pytest tests -q        （需要 ExifTool 的用例：设置 PHOTOCRATE_TEST_EXIFTOOL）
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

from photocrate.bootstrap import bootstrap  # noqa: E402
from photocrate.config import Settings  # noqa: E402

bootstrap(Settings(), load_user_plugins=False)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from photocrate.analyzers.builtin import group_similar, hamming, rating_feat  # noqa: E402
from photocrate.core.models import MemberFile, PhotoGroup  # noqa: E402
from photocrate.partition.base import DIMENSIONS, PartitionContext  # noqa: E402
from test_timeplace import make_jpeg  # noqa: E402

ET = os.environ.get("PHOTOCRATE_TEST_EXIFTOOL") or shutil.which("exiftool")
need_et = pytest.mark.skipif(not ET, reason="需要 ExifTool")


def run_et(*args: str) -> str:
    """直接调用 ExifTool（造测试数据 / 检查结果）。参数经 UTF-8 的参数文件传入：
    Windows 命令行会把中文路径、中文关键词转成本地编码（变成 ???，或找不到文件）。返回标准输出。"""
    import subprocess
    out = subprocess.run([ET, "-charset", "filename=utf8", "-@", "-"], input="\n".join(args).encode("utf-8"),
                         capture_output=True, check=True)
    return out.stdout.decode("utf-8", "replace")


_XDG = tempfile.mkdtemp(prefix="pc_test_xdg_")      # send2trash 在导入时就读取 XDG_DATA_HOME，整个测试会话用同一个


@pytest.fixture()
def trash_home(monkeypatch):
    """把 freedesktop 回收站放到临时目录里（与被删文件同一磁盘），不碰真实回收站。"""
    if os.name == "nt":
        pytest.skip("Windows 上 send2trash 只能放进真实的回收站，这些用例在 Linux 上跑")
    monkeypatch.setenv("XDG_DATA_HOME", _XDG)
    import send2trash.plat_other as po
    monkeypatch.setattr(po, "XDG_DATA_HOME", _XDG, raising=False)
    monkeypatch.setattr(po, "HOMETRASH", os.path.join(_XDG, "Trash"), raising=False)
    monkeypatch.setattr(po, "HOMETRASH_B", os.path.join(_XDG, "Trash").encode(), raising=False)
    return _XDG


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


def _scene(path: Path, dt: datetime, seed: int, shift: int = 0, blur: bool = False, model="ILCE-7M4"):
    """有纹理的测试图：同一 seed = 同一场景（shift 只做轻微平移，模拟连拍）。"""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (24, 32, 3), dtype=np.uint8)
    im = Image.fromarray(base).resize((640, 480), Image.Resampling.BICUBIC)
    if shift:
        im = im.transform(im.size, Image.Transform.AFFINE, (1, 0, shift, 0, 1, 0))
    if blur:
        from PIL import ImageFilter
        im = im.filter(ImageFilter.GaussianBlur(6))
    import piexif
    exif = piexif.dump({"0th": {piexif.ImageIFD.Make: b"SONY", piexif.ImageIFD.Model: model.encode()},
                        "Exif": {piexif.ExifIFD.DateTimeOriginal: dt.strftime("%Y:%m:%d %H:%M:%S").encode(),
                                 36881: b"+09:00"}})
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "JPEG", quality=92, exif=exif)
    return path


# ---------------------------------------------------------------------------
# 星级 / 色标 / 留弃
# ---------------------------------------------------------------------------

def test_rating_feat_and_dimension():
    assert rating_feat({"rating": 3, "label": "Red"}, None) == {"rating": 3, "label": "Red", "flag": None,
                                                                "manual": False}
    assert rating_feat({"rating": -1}, None)["flag"] == "reject"
    assert rating_feat({"rating": 0}, {"rating": 5, "label": "绿", "flag": "pick"}) == \
        {"rating": 5, "label": "Green", "flag": "pick", "manual": True}
    g = PhotoGroup(0, [MemberFile("/x/A.JPG", "jpeg", 1, 1)], "/x/A.JPG")
    d = DIMENSIONS["rating"]
    ctx = PartitionContext({})
    g.feats["rating"] = {"rating": 4, "label": "Blue", "flag": None}
    assert d.segments(g, d.resolve_opts({}), ctx) == ["4星"]
    assert d.segments(g, d.resolve_opts({"mode": "label"}), ctx) == ["蓝色标"]
    assert d.segments(g, d.resolve_opts({"mode": "flag"}), ctx) == ["未标记"]
    g.feats["rating"] = {"rating": 4, "flag": "reject"}
    assert d.segments(g, d.resolve_opts({}), ctx) == ["弃用"]


def test_library_set_rating_persists_across_rescan(tmp_path):
    make_jpeg(tmp_path / "p" / "A.JPG", datetime(2026, 1, 5, 10, 0), "+09:00")
    make_jpeg(tmp_path / "p" / "B.JPG", datetime(2026, 1, 5, 11, 0), "+09:00")
    lib = _library(tmp_path)
    lib.scan([str(tmp_path / "p")], True)
    a = next(g for g in lib.groups if g.name == "A.JPG")
    assert lib.set_rating([a.gid], {"rating": 4}) == 1
    lib.set_rating([a.gid], {"label": "Red"})
    lib.set_rating([a.gid], {"flag": "pick"})
    assert a.feats["rating"] == {"rating": 4, "label": "Red", "flag": "pick", "manual": True}
    lib.scan([str(tmp_path / "p")], True)
    a = next(g for g in lib.groups if g.name == "A.JPG")
    assert a.feat("rating", "rating") == 4 and a.feat("rating", "flag") == "pick"
    lib.set_rating([a.gid], None)
    assert a.feat("rating", "rating") == 0 and not a.feat("rating", "manual")
    lib.close()


# ---------------------------------------------------------------------------
# 相似 / 连拍
# ---------------------------------------------------------------------------

def test_phash_and_grouping(tmp_path):
    t = datetime(2026, 1, 5, 10, 0, 0)
    p = tmp_path / "burst"
    _scene(p / "DSC001.JPG", t, 1)
    _scene(p / "DSC002.JPG", t + timedelta(seconds=1), 1, shift=4, blur=True)
    _scene(p / "DSC003.JPG", t + timedelta(seconds=2), 1, shift=8)
    _scene(p / "DSC004.JPG", t + timedelta(seconds=3), 2)                 # 同一时间、不同场景
    _scene(p / "DSC005.JPG", t + timedelta(minutes=5), 1)                 # 同一场景，但隔了 5 分钟
    _scene(p / "DSC006.JPG", t + timedelta(minutes=5, seconds=2), 1, shift=2)
    lib = _library(tmp_path)
    lib.scan([str(p)], True)
    by = {g.name: g for g in lib.groups}
    assert hamming(by["DSC001.JPG"].feat("phash", "phash"), by["DSC003.JPG"].feat("phash", "phash")) <= 12
    assert hamming(by["DSC001.JPG"].feat("phash", "phash"), by["DSC004.JPG"].feat("phash", "phash")) > 12
    s1 = {n: by[n].feats.get("similar") for n in by}
    assert s1["DSC004.JPG"] is None
    c1 = {n for n in ("DSC001.JPG", "DSC002.JPG", "DSC003.JPG") if s1[n] and s1[n]["cid"] == s1["DSC001.JPG"]["cid"]}
    assert c1 == {"DSC001.JPG", "DSC002.JPG", "DSC003.JPG"} and s1["DSC001.JPG"]["size"] == 3
    assert s1["DSC005.JPG"]["cid"] != s1["DSC001.JPG"]["cid"] and s1["DSC005.JPG"]["size"] == 2
    assert not s1["DSC002.JPG"]["best"]                           # 模糊的那张不会被选为最佳
    assert sum(1 for n in c1 if s1[n]["best"]) == 1
    # 收紧时间窗口后，相隔 2 秒的不再算一组
    lib.settings.set("similar_gap_seconds", 1)
    lib.regroup_similar()
    assert by["DSC006.JPG"].feats.get("similar") is None
    lib.close()


def test_group_similar_unit():
    def g(i, sec, h, score):
        x = PhotoGroup(i, [MemberFile(f"/x/{i}.JPG", "jpeg", 100 + i, i)], f"/x/{i}.JPG")
        x.feats["date"] = {"dt": (datetime(2026, 1, 1, 12) + timedelta(seconds=sec)).isoformat(), "source": "exif"}
        x.feats["phash"] = {"phash": h}
        x.feats["blur"] = {"score": score}
        return x
    gs = [g(0, 0, "ffff0000ffff0000", 50), g(1, 3, "ffff0000ffff0001", 90), g(2, 6, "0000ffff0000ffff", 70),
          g(3, 8, "0000ffff0000fffe", 10)]
    assert group_similar(gs, 10, 12) == 2
    assert gs[1].feats["similar"]["best"] and gs[2].feats["similar"]["best"]
    assert gs[0].feats["similar"]["cid"] == 1 and gs[3].feats["similar"]["cid"] == 2


# ---------------------------------------------------------------------------
# 回收站：删除与找回
# ---------------------------------------------------------------------------

def test_windows_i_file_parser():
    import struct
    from photocrate.ops.trashbin import parse_windows_i_file
    path = r"E:\照片\日本2026\DSC01564.ARW"
    ft = int((1792558000 * 1e7) + 116444736000000000)
    data = struct.pack("<qqqi", 2, 34664448, ft, len(path) + 1) + (path + "\x00").encode("utf-16-le")
    p, t, size = parse_windows_i_file(data)
    assert p == path and abs(t - 1792558000) < 1 and size == 34664448
    v1 = struct.pack("<qqq", 1, 10, ft) + path.encode("utf-16-le").ljust(520, b"\x00")
    assert parse_windows_i_file(v1)[0] == path


def test_trash_and_restore(tmp_path, trash_home):
    from photocrate.ops.trashbin import find_in_trash, restore_from_trash, send_to_trash
    f = tmp_path / "data" / "照片 1.ARW"
    f.parent.mkdir()
    f.write_bytes(b"raw1")
    t = send_to_trash(str(f))
    assert not f.exists() and find_in_trash(str(f), t) is not None
    restore_from_trash(str(f), t)
    assert f.read_bytes() == b"raw1"
    assert find_in_trash(str(f)) is None


def _pair(d: Path, stem: str, dt: datetime):
    make_jpeg(d / f"{stem}.JPG", dt, "+09:00")
    (d / f"{stem}.ARW").write_bytes(os.urandom(3000))
    return d / f"{stem}.ARW", d / f"{stem}.JPG"


def test_cleanup_keep_jpg_execute_and_undo(tmp_path, trash_home):
    from photocrate.ops.cleanup import build_cleanup_plan
    from photocrate.ops.journal import Journal
    from photocrate.ops.mover import Mover
    d = tmp_path / "trip"
    d.mkdir()
    a_raw, a_jpg = _pair(d, "DSC0001", datetime(2026, 1, 5, 10))
    (d / "DSC0001.xmp").write_text("<x/>", "utf-8")
    b_raw, b_jpg = _pair(d, "DSC0002", datetime(2026, 1, 5, 11))
    only_raw = d / "DSC0003.ARW"
    only_raw.write_bytes(os.urandom(2000))
    lib = _library(tmp_path)
    lib.scan([str(d)], True)
    b = next(g for g in lib.groups if g.stem == "DSC0002")
    lib.set_rating([b.gid], {"rating": 3})

    plan = build_cleanup_plan(lib.groups, "unrated_raw")
    names = sorted(os.path.basename(op.src) for it in plan.items for op in it.ops)
    assert names == ["DSC0001.ARW", "DSC0001.xmp"]                  # 打了星的 B 保留，只有 RAW 的 C 保留
    assert any("只有 RAW" in w for w in plan.warnings)
    plan2 = build_cleanup_plan(lib.groups, "unrated_raw", include_raw_only=True)
    assert "DSC0003.ARW" in [os.path.basename(op.src) for it in plan2.items for op in it.ops]

    plan = build_cleanup_plan(lib.groups, "keep_jpg")
    assert sorted(os.path.basename(op.src) for it in plan.items for op in it.ops) == \
        ["DSC0001.ARW", "DSC0001.xmp", "DSC0002.ARW"]
    j = Journal(tmp_path / "j.db")
    res = Mover(j).delete(plan)
    assert res.done_groups == 2 and not a_raw.exists() and not b_raw.exists() and a_jpg.exists()
    assert res.freed == plan.total_bytes
    lib.remove_files(res.deleted)
    a = next(g for g in lib.groups if g.stem == "DSC0001")
    assert [f.name for f in a.files] == ["DSC0001.JPG"] and a.kind == "jpeg"
    u = Mover(j).undo(res.batch_id)
    assert u.failed_groups == 0 and a_raw.exists() and b_raw.exists() and (d / "DSC0001.xmp").exists()
    assert j.batch(res.batch_id)["status"] == "undone"
    lib.close()


def test_cleanup_rejected_whole_group(tmp_path, trash_home):
    from photocrate.ops.cleanup import build_cleanup_plan
    d = tmp_path / "t"
    d.mkdir()
    _pair(d, "DSC0009", datetime(2026, 1, 5, 10))
    lib = _library(tmp_path)
    lib.scan([str(d)], True)
    lib.set_rating([lib.groups[0].gid], {"flag": "reject"})
    plan = build_cleanup_plan(lib.groups, "rejected")
    assert sorted(os.path.basename(op.src) for it in plan.items for op in it.ops) == ["DSC0009.ARW", "DSC0009.JPG"]
    lib.close()


# ---------------------------------------------------------------------------
# 批量重命名
# ---------------------------------------------------------------------------

def test_rename_plan_execute_undo_keeps_annotations(tmp_path):
    from photocrate.ops.journal import Journal
    from photocrate.ops.mover import Mover
    from photocrate.ops.renamer import build_rename_plan
    d = tmp_path / "trip"
    d.mkdir()
    _pair(d, "DSC0002", datetime(2026, 1, 5, 11))
    _pair(d, "DSC0001", datetime(2026, 1, 5, 10))
    (d / "DSC0001.ARW.xmp").write_text("<x/>", "utf-8")
    make_jpeg(d / "IMG_9.JPG", datetime(2026, 1, 6, 9), "+09:00")
    (d / "20260106_x_003.JPG").write_bytes(b"occupied")              # 预先占用一个目标名
    lib = _library(tmp_path)
    lib.scan([str(d)], True)
    g1 = next(g for g in lib.groups if g.stem == "DSC0001")
    lib.set_rating([g1.gid], {"rating": 5})
    groups = [g for g in lib.groups if g.name != "20260106_x_003.JPG"]
    plan = build_rename_plan(groups, "{日期}_x_{序号}", PartitionContext(lib.settings.snapshot()))
    m = {os.path.basename(op.src): os.path.basename(op.dst) for it in plan.items for op in it.ops}
    assert m["DSC0001.ARW"] == "20260105_x_001.ARW" and m["DSC0001.JPG"] == "20260105_x_001.JPG"
    assert m["DSC0001.ARW.xmp"] == "20260105_x_001.ARW.xmp"
    assert m["DSC0002.JPG"] == "20260105_x_002.JPG"
    assert m["IMG_9.JPG"] == "20260106_x_003 (1).JPG"                 # 冲突 → 加编号
    j = Journal(tmp_path / "j.db")
    res = Mover(j).execute(plan)
    assert res.failed_groups == 0 and (d / "20260105_x_001.ARW").exists() and not (d / "DSC0001.ARW").exists()
    lib.apply_moves(res.mapping)
    lib.scan([str(d)], True)                                          # 改名后重新扫描，星级仍在
    g = next(g for g in lib.groups if g.stem == "20260105_x_001")
    assert g.feat("rating", "rating") == 5
    u = Mover(j).undo(res.batch_id)
    assert u.failed_groups == 0 and (d / "DSC0001.ARW").exists() and (d / "DSC0001.ARW.xmp").exists()
    lib.apply_moves(u.mapping)
    lib.scan([str(d)], True)
    assert next(g for g in lib.groups if g.stem == "DSC0001").feat("rating", "rating") == 5
    assert j.batch(res.batch_id)["operation"] == "rename"
    lib.close()


# ---------------------------------------------------------------------------
# 写入 XMP：星级 + 更新已有 .xmp（备份 / 撤销）
# ---------------------------------------------------------------------------

@need_et
def test_xmp_rating_create_and_update_with_undo(tmp_path, monkeypatch):
    from photocrate.ops import mover as mover_mod
    from photocrate.ops.journal import Journal
    from photocrate.ops.mover import Mover
    from photocrate.ops.xmpwrite import build_xmp_plan, write_xmp
    d = tmp_path / "x"
    d.mkdir()
    r1 = d / "DSC1.ARW"
    r1.write_bytes(b"\0" * 2048)
    r2 = d / "DSC2.ARW"
    r2.write_bytes(b"\0" * 2048)
    lr = d / "DSC2.xmp"                       # 模拟 Lightroom 已有的修图记录
    run_et("-q", "-XMP-crs:Exposure2012=+0.50", "-XMP-xmp:Rating=1", str(lr))
    original = lr.read_bytes()
    gs = []
    for i, p in enumerate((r1, r2)):
        st = os.stat(p)
        g = PhotoGroup(i, [MemberFile(str(p), "raw", st.st_size, st.st_mtime_ns)], str(p))
        g.feats["rating"] = {"rating": 4 - i, "label": "Red" if i == 0 else None,
                             "flag": "reject" if i == 1 else None, "manual": True}
        gs.append(g)
    assert len(build_xmp_plan(gs, False, False, False, True, update_existing=False).tasks) == 1
    plan = build_xmp_plan(gs, False, False, False, True, update_existing=True)
    assert plan.created == 1 and plan.edited == 1
    j = Journal(tmp_path / "j.db")
    res = write_xmp(plan, ET, j)
    assert res.failed == 0 and res.edited == 1
    out = run_et("-s", "-Rating", "-Label", "-Exposure2012", str(d / "DSC1.xmp"), str(lr))
    assert "Rating                          : 4" in out and "Label                           : Red" in out
    assert "Rating                          : -1" in out and "+0.50" in out          # 修图设置保留
    monkeypatch.setattr(mover_mod, "trash", lambda p: os.remove(p))
    u = Mover(j).undo(res.batch_id)
    assert u.failed_groups == 0 and not (d / "DSC1.xmp").exists() and lr.read_bytes() == original
