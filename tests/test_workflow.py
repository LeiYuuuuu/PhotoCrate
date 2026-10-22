"""补全工作流：视频、重复照片、导入向导、导出分享 / 网页相册、AI 关键词写入 XMP。

python -m pytest tests -q        （视频与 XMP 用例需要 ExifTool：设置 PHOTOCRATE_TEST_EXIFTOOL）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_culling import ET, _library, _scene, need_et, trash_home  # noqa: E402,F401
from test_timeplace import make_jpeg  # noqa: E402

TOKYO = (35.6586, 139.7454)


def make_video(path: Path, utc: str | None = None, creation: str | None = None, frames: int = 50) -> Path:
    """生成一段小 MP4；utc = QuickTime CreateDate（UTC），creation = 带时区的 CreationDate（苹果 / 索尼风格）。"""
    import cv2
    import numpy as np
    path.parent.mkdir(parents=True, exist_ok=True)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (320, 240))
    for i in range(frames):
        f = np.zeros((240, 320, 3), np.uint8)
        f[:] = (40, 100 + i, 200)
        w.write(f)
    w.release()
    args = []
    if utc:
        args += [f"-QuickTime:CreateDate={utc}", f"-QuickTime:MediaCreateDate={utc}"]
    if creation:
        args += [f"-Keys:CreationDate={creation}"]
    if args and ET:
        subprocess.run([ET, "-q", "-overwrite_original", *args, str(path)], check=True)
    return path


# ---------------------------------------------------------------------------
# 视频
# ---------------------------------------------------------------------------

@need_et
def test_videos_scan_group_time_and_sidecars(tmp_path):
    d = tmp_path / "日本"
    make_jpeg(d / "DSC00001.JPG", datetime(2026, 1, 5, 15, 30), "+09:00", gps=TOKYO)
    make_video(d / "C0001.MP4", utc="2026:01:05 06:36:47")                       # 东京 15:36:47
    (d / "C0001M01.XML").write_text("<NonRealTimeMeta/>", "utf-8")
    make_video(d / "C0002.MP4", creation="2026:01:05 15:40:00+09:00", utc="2026:01:05 06:40:00")
    make_jpeg(d / "IMG_0003.JPG", datetime(2026, 1, 5, 16, 0), "+09:00")
    make_video(d / "IMG_0003.MOV", utc="2026:01:05 07:00:00")                    # 实况照片的视频部分
    lib = _library(tmp_path)
    try:
        lib.scan([str(d)], True)
        by = {g.stem: g for g in lib.groups if g.files}
        assert set(by) == {"DSC00001", "C0001", "C0002", "IMG_0003"}, set(by)
        v1, v2 = by["C0001"], by["C0002"]
        assert v1.is_video and v1.kind_label == "视频" and v1.thumb and os.path.exists(v1.thumb)
        assert [f.name for f in v1.files] == ["C0001.MP4", "C0001M01.XML"]
        assert v1.date == datetime(2026, 1, 5, 15, 36, 47) and v1.feat("date", "tz") == "+09:00", v1.feats["date"]
        assert v1.feat("date", "tz_from") == "DSC00001.JPG"
        assert v2.date == datetime(2026, 1, 5, 15, 40) and not v2.feat("date", "tz_from")
        assert 1.5 < float(v1.meta.get("duration") or 0) < 2.5
        assert v1.feat("blur", "score") is None
        live = by["IMG_0003"]
        assert not live.is_video and any(f.role == "live" for f in live.files)
        # 视频也按地点 / 旅行归档：同一时段有 GPS 照片 → 地点补全
        assert v1.feat("geo", "province") == "东京都", v1.feats.get("geo")
        # 只扫照片
        lib.settings.set("include_videos", False)
        lib.scan([str(d)], True)
        assert not any(g.is_video for g in lib.groups)
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# 重复照片
# ---------------------------------------------------------------------------

def test_duplicates_find_delete_and_undo(tmp_path, trash_home):
    from photocrate.ops.dedupe import build_dedupe_plan, find_duplicates
    from photocrate.ops.journal import Journal
    from photocrate.ops.mover import Mover
    a = tmp_path / "整理好"
    b = tmp_path / "旧备份" / "再一层"
    _scene(a / "DSC0001.JPG", datetime(2026, 1, 5, 10), seed=1)
    (a / "DSC0001.ARW").write_bytes(os.urandom(5000))
    _scene(a / "DSC0002.JPG", datetime(2026, 1, 5, 11), seed=2)
    _scene(a / "DSC0003.JPG", datetime(2026, 1, 5, 12), seed=3)
    b.mkdir(parents=True)
    shutil.copy2(a / "DSC0001.JPG", b / "DSC0001.JPG")          # 只复制了 JPG → 是 RAW+JPG 的子集
    shutil.copy2(a / "DSC0002.JPG", b / "copy_of_2.jpg")         # 改了名的副本
    shutil.copy2(a / "DSC0003.JPG", b / "DSC0003.JPG")
    (b / "DSC0003.xmp").write_text("<x/>", "utf-8")              # 副本有自己的修图记录 → 不删
    _scene(b / "DSC0001_other.JPG", datetime(2026, 1, 5, 13), seed=9)   # 不重复
    lib = _library(tmp_path)
    try:
        lib.scan([str(tmp_path)], True)
        res = find_duplicates(lib.groups, "shortest")
        removed = sorted(os.path.relpath(lib.by_gid(gid).primary, tmp_path) for s in res.sets for gid in s.remove)
        kept = sorted(os.path.relpath(lib.by_gid(s.keep).primary, tmp_path) for s in res.sets)
        assert removed == [os.path.join("旧备份", "再一层", "DSC0001.JPG"),
                           os.path.join("旧备份", "再一层", "copy_of_2.jpg")], removed
        assert kept == [os.path.join("整理好", "DSC0001.JPG"), os.path.join("整理好", "DSC0002.JPG")]
        assert len(res.skipped_sidecar) == 1
        plan = build_dedupe_plan(lib.groups, res)
        assert plan.file_count == 2 and any("修图记录" in w for w in plan.warnings)
        j = Journal(tmp_path / "j.db")
        dr = Mover(j).delete(plan)
        assert dr.done_groups == 2 and not (b / "copy_of_2.jpg").exists() and (a / "DSC0002.JPG").exists()
        u = Mover(j).undo(dr.batch_id)
        assert u.failed_groups == 0 and (b / "copy_of_2.jpg").exists()
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# 导入向导
# ---------------------------------------------------------------------------

def test_import_from_card_skip_existing_rename_and_undo(tmp_path, trash_home):
    from photocrate.core.library import Library
    from photocrate.ops.importer import ImportHistory, build_import_plan, card_roots, classify, record_import
    from photocrate.ops.journal import Journal
    from photocrate.ops.mover import Mover
    from photocrate.partition.base import PartitionContext
    card = tmp_path / "SDCARD"
    dcim = card / "DCIM" / "100MSDCF"
    make_jpeg(dcim / "DSC00001.JPG", datetime(2026, 1, 5, 10), "+09:00")
    (dcim / "DSC00001.ARW").write_bytes(os.urandom(4000))
    make_jpeg(dcim / "DSC00002.JPG", datetime(2026, 1, 6, 9), "+09:00")
    make_video(card / "PRIVATE" / "M4ROOT" / "CLIP" / "C0001.MP4", utc="2026:01:06 01:00:00")
    (card / "PRIVATE" / "M4ROOT" / "STATUS.BIN").write_bytes(b"x")
    roots = card_roots(str(card))
    assert len(roots) == 2 and roots[0].endswith("DCIM")
    reader = _library(tmp_path)
    try:
        reader.scan(roots, True, pixels=False)
        groups = [g for g in reader.groups if g.files]
        assert len(groups) == 3 and all(g.thumb is None for g in groups)
        hist = ImportHistory(tmp_path / "imports.db")
        chk = classify(groups, hist)
        assert len(chk.new) == 3 and chk.videos == 1 and chk.photos == 2
        dest = tmp_path / "照片库"
        ctx = PartitionContext(reader.settings.snapshot())
        plan = build_import_plan(chk.new, str(dest), "{年}/{年月日}", ctx, rename_template="{日期}_{序号}")
        names = sorted(os.path.relpath(op.dst, dest) for it in plan.items for op in it.ops)
        assert os.path.join("2026", "2026-01-05", "20260105_001.ARW") in names
        assert os.path.join("2026", "2026-01-05", "20260105_001.JPG") in names
        assert any(n.endswith("_003.MP4") for n in names), names
        j = Journal(tmp_path / "j.db")
        res = Mover(j).execute(plan)
        assert res.done_files == 4 and len(res.copied) == 4
        assert (dcim / "DSC00001.ARW").exists()                     # 存储卡上的原文件不动
        assert record_import(hist, plan, res, chk.new) == 4
        assert len(classify(groups, hist).already) == 3             # 再插一次卡：全部已导入
        # 目标位置已有同名同大小的文件（以前按同样的规则导入过 / 手动复制过）→ 也算已导入
        plan2 = build_import_plan(chk.new, str(dest), "{年}/{年月日}", ctx, rename_template="{日期}_{序号}")
        assert not plan2.items and any("已导入" in w for w in plan2.warnings), plan2.warnings
        plan3 = build_import_plan(chk.new, str(dest), "{年}/{年月日}", ctx)       # 不改名：原名不存在 → 要导入
        assert len(plan3.items) == 3
        # 撤销导入：复制出来的文件进回收站，再插卡时又是“新的”
        Mover(j).undo(res.batch_id)
        assert not any(dest.rglob("*.JPG"))
        assert len(classify(groups, hist).new) == 3
        # 照片库里已有同一个文件 → 已导入
        lib_sigs = {f.sig for g in groups for f in g.files}
        assert len(classify(groups, hist, lib_sigs).already) == 3
        hist.close()
    finally:
        reader.close()
    assert isinstance(Library, type)


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def test_export_jpegs_and_album(tmp_path):
    from PIL import Image
    from photocrate.ops.exporter import ExportOptions, export_album, export_jpegs
    d = tmp_path / "src"
    p = _scene(d / "DSC0001.JPG", datetime(2026, 1, 5, 10), seed=4)
    big = Image.open(p).resize((3000, 2000))
    import piexif
    exif = piexif.dump({"0th": {piexif.ImageIFD.Make: b"SONY", piexif.ImageIFD.Model: b"ILCE-7CM2",
                                piexif.ImageIFD.Orientation: 6},
                        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:01:05 10:00:00"},
                        "GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: ((35, 1), (39, 1), (0, 1)),
                                piexif.GPSIFD.GPSLongitudeRef: b"E", piexif.GPSIFD.GPSLongitude: ((139, 1), (44, 1), (0, 1))}})
    big.save(p, "JPEG", exif=exif, quality=90)
    _scene(d / "DSC0002.JPG", datetime(2026, 1, 6, 10), seed=5)
    make_video(d / "C0001.MP4")
    lib = _library(tmp_path)
    try:
        lib.scan([str(d)], True)
        gs = [g for g in lib.groups if g.files]
        out = tmp_path / "导出"
        res = export_jpegs(gs, str(out), ExportOptions(long_edge=1080, watermark="© 影匣测试"))
        assert len(res.written) == 2 and res.skipped_video == 1 and not res.failed, res.errors
        im = Image.open(out / "DSC0001.jpg")
        assert im.size == (720, 1080)                               # 竖幅：按 EXIF 方向转正后长边 1080
        ex = im.getexif()
        assert ex.get(0x0110) == "ILCE-7CM2" and ex.get(0x0112) == 1 and 0x8825 not in ex
        assert ex.get_ifd(0x8769).get(0x9003) == "2026:01:05 10:00:00"
        res2 = export_jpegs(gs, str(out), ExportOptions(long_edge=0, strip_all=True))
        assert all(os.path.basename(x).startswith("DSC000") and "(1)" in x for x in res2.written)
        assert not Image.open(res2.written[0]).getexif()
        # 网页相册
        lib.settings.set("home_places", [])
        index, r3 = export_album(gs, str(tmp_path / "相册"), "2026 日本", ExportOptions(long_edge=1280),
                                 subtitle="测试")
        html = open(index, encoding="utf-8").read()
        assert "2026 日本" in html and '"days"' in html and len(r3.written) == 2
        assert os.path.exists(os.path.join(os.path.dirname(index), "thumbs", os.path.basename(r3.written[0])))
        assert not Image.open(r3.written[0]).getexif().get(0x8825)
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# AI 关键词写入 XMP
# ---------------------------------------------------------------------------

@need_et
def test_xmp_keywords_create_and_update_without_duplicates(tmp_path):
    from photocrate.ops.journal import Journal
    from photocrate.ops.xmpwrite import ai_keywords, build_xmp_plan, exiftool_args, write_xmp
    d = tmp_path / "trip"
    make_jpeg(d / "DSC0001.JPG", datetime(2026, 1, 5, 10), "+09:00")
    (d / "DSC0001.ARW").write_bytes(os.urandom(3000))
    lib = _library(tmp_path)
    try:
        lib.scan([str(d)], True)
        g = next(g for g in lib.groups if g.files)
        g.feats["scene"] = {"scene": "夜景", "group": "城市建筑", "prob": 0.8}
        g.feats["people"] = {"names": ["小明"]}
        g.feats["trip"] = {"name": "2026-01-05 日本 东京"}
        flat, hier = ai_keywords(g)
        assert flat == ["夜景", "小明", "2026-01-05 日本 东京"]
        assert hier == ["场景|城市建筑|夜景", "人物|小明", "旅行|2026-01-05 日本 东京"]
        assert "-XMP-dc:Subject-=夜景" in exiftool_args({"XMP-dc:Subject": ["夜景"]})
        j = Journal(tmp_path / "j.db")
        plan = build_xmp_plan([g], False, False, False, False, True, True)
        assert plan.tasks and plan.tasks[0].what == ["关键词"]
        res = write_xmp(plan, ET, j)
        assert not res.failed, res.errors
        lib.attach_files(res.written)
        xmp = str(d / "DSC0001.xmp")

        def subjects():
            out = subprocess.run([ET, "-j", "-XMP-dc:Subject", "-XMP-lr:HierarchicalSubject", xmp],
                                 capture_output=True, text=True, encoding="utf-8").stdout
            import json
            r = json.loads(out)[0]
            s = r.get("Subject", [])
            return s if isinstance(s, list) else [s], r.get("HierarchicalSubject")
        s, h = subjects()
        assert s == ["夜景", "小明", "2026-01-05 日本 东京"] and "人物|小明" in h
        # 用户在 Lightroom 里加了一个自己的关键词；再写一次（更新已有 .xmp）：不重复、不删用户的词
        subprocess.run([ET, "-q", "-overwrite_original", "-XMP-dc:Subject+=我的关键词", xmp], check=True)
        g.feats["scene"]["scene"] = "夜景"
        plan2 = build_xmp_plan([g], False, False, False, False, True, True)
        assert plan2.tasks[0].mode == "edit"
        assert not write_xmp(plan2, ET, j).failed
        s, _ = subjects()
        assert sorted(s) == sorted(["夜景", "小明", "2026-01-05 日本 东京", "我的关键词"]), s
    finally:
        lib.close()
