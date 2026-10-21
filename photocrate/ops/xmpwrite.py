"""把软件里补全 / 校正的信息写回 XMP 附属文件（Lightroom、Capture One 等会读取）。

原则（与移动文件一样保守）：
- 只给 RAW 生成 “主文件名.xmp”（如 DSC01234.ARW → DSC01234.xmp），绝不修改照片原文件；
- 只新建，不改写：旁边已有同名 .xmp（可能是修图记录）时跳过该照片；
- 先写日志再写文件；在「记录」页撤销 = 把这一批新建的 .xmp 放入回收站。

可写入的内容：
- GPS 坐标：来自 GPS 轨迹 / 参考照片按时间匹配的结果（手动指定的地点只有地名，不写坐标）；
- 地点名称：国家 / 省 / 市（XMP-photoshop:Country / State / City、XMP-iptcCore:CountryCode）；
- 校正后的拍摄时间：XMP-exif:DateTimeOriginal 与 XMP-photoshop:DateCreated（带时区）。
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .planner import PlanItem, PlanOp

log = logging.getLogger(__name__)


@dataclass
class XmpTask:
    gid: int
    raw: str
    xmp: str
    tags: dict[str, str]
    what: list[str]              # 写入了哪些内容（显示用）


@dataclass
class XmpPlan:
    tasks: list[XmpTask] = field(default_factory=list)
    skipped_exists: int = 0      # 旁边已有 .xmp
    skipped_no_raw: int = 0      # 不是 RAW（JPG/HEIC 不生成附属文件）
    skipped_nothing: int = 0     # 没有需要写入的内容
    operation: str = "xmp"
    target_root: str = ""
    spec_text: str = ""

    # ---- 与移动计划同样的接口，供 Journal.create_batch 使用 ----
    @property
    def items(self) -> list[PlanItem]:
        return [PlanItem(t.gid, [PlanOp(t.raw, t.xmp, 0, 0, "xmp")], os.path.dirname(t.xmp)) for t in self.tasks]

    @property
    def file_count(self) -> int:
        return len(self.tasks)

    @property
    def total_bytes(self) -> int:
        return 0


def _fmt_dt(dt: datetime, tz: str | None) -> str:
    return dt.strftime("%Y:%m:%d %H:%M:%S") + (tz or "")


def tags_for(g, write_gps: bool, write_place: bool, write_time: bool) -> tuple[dict[str, str], list[str]]:
    tags: dict[str, str] = {}
    what: list[str] = []
    geo = g.feats.get("geo") or {}
    if not geo.get("has_gps"):
        if write_gps and geo.get("source") == "track" and geo.get("lat") is not None:
            tags["XMP-exif:GPSLatitude"] = f"{float(geo['lat']):.6f}"
            tags["XMP-exif:GPSLongitude"] = f"{float(geo['lon']):.6f}"
            tags["XMP-exif:GPSMapDatum"] = "WGS-84"
            what.append("GPS")
        if write_place and geo.get("source") in ("track", "manual") and geo.get("country"):
            tags["XMP-photoshop:Country"] = geo["country"]
            if geo.get("cc"):
                tags["XMP-iptcCore:CountryCode"] = geo["cc"]
            if geo.get("province"):
                tags["XMP-photoshop:State"] = geo["province"]
            city = geo.get("city") or geo.get("district")
            if city:
                tags["XMP-photoshop:City"] = city
            what.append("地点")
    d = g.feats.get("date") or {}
    if write_time and d.get("corrected") and d.get("dt"):
        v = _fmt_dt(datetime.fromisoformat(d["dt"]), d.get("tz"))
        tags["XMP-exif:DateTimeOriginal"] = v
        tags["XMP-photoshop:DateCreated"] = v
        what.append("时间")
    return tags, what


def sidecar_path(raw_path: str) -> str:
    return os.path.splitext(raw_path)[0] + ".xmp"


def _has_xmp(g, raw_path: str) -> bool:
    cand = {os.path.normcase(sidecar_path(raw_path)), os.path.normcase(raw_path + ".xmp")}
    if any(os.path.normcase(f.path) in cand for f in g.files):
        return True
    return any(os.path.lexists(p) for p in (sidecar_path(raw_path), raw_path + ".xmp"))


def build_xmp_plan(groups, write_gps: bool = True, write_place: bool = True, write_time: bool = True) -> XmpPlan:
    plan = XmpPlan()
    parts = [n for n, on in (("GPS", write_gps), ("地点", write_place), ("校正时间", write_time)) if on]
    plan.spec_text = "写入 XMP：" + "、".join(parts)
    dirs = set()
    for g in groups:
        if not g.files:
            continue
        raws = [f for f in g.image_files if f.role == "raw"]
        tags, what = tags_for(g, write_gps, write_place, write_time)
        if not tags:
            plan.skipped_nothing += 1
            continue
        if not raws:
            plan.skipped_no_raw += 1
            continue
        raw = raws[0].path
        if _has_xmp(g, raw):
            plan.skipped_exists += 1
            continue
        plan.tasks.append(XmpTask(g.gid, raw, sidecar_path(raw), tags, what))
        dirs.add(os.path.dirname(raw))
    if dirs:
        try:
            plan.target_root = os.path.commonpath(sorted(dirs))
        except ValueError:
            plan.target_root = sorted(dirs)[0]
    return plan


@dataclass
class XmpResult:
    batch_id: int
    written: dict[int, list[str]] = field(default_factory=dict)   # gid → 新建的 .xmp
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)


def write_xmp(plan: XmpPlan, exiftool_path: str, journal, progress: Callable[[int, int, str], None] | None = None,
              cancel: threading.Event | None = None) -> XmpResult:
    if not exiftool_path:
        raise RuntimeError("写入 XMP 需要 ExifTool")
    import exiftool

    bid = journal.create_batch(plan, {"kind": "xmp"})
    res = XmpResult(bid)
    ops = {o["grp"]: o for o in journal.ops(bid)}
    total = len(plan.tasks)
    et = exiftool.ExifToolHelper(executable=exiftool_path, encoding="utf-8",
                                 common_args=["-charset", "filename=utf8", "-m"], check_execute=False)
    try:
        for i, t in enumerate(plan.tasks, 1):
            if cancel is not None and cancel.is_set():
                res.cancelled = True
                break
            op = ops.get(t.gid)
            try:
                if os.path.lexists(t.xmp):
                    raise FileExistsError("已存在同名 .xmp，未改动")
                if not os.path.exists(t.raw):
                    raise FileNotFoundError("照片已不在原位置，请重新扫描")
                args = [f"-{k}={v}" for k, v in t.tags.items()] + [t.xmp]
                et.execute(*args)
                if not os.path.exists(t.xmp):
                    raise IOError((et.last_stderr or "ExifTool 没有生成文件").strip()[:200])
                journal.set_op(op["id"], "done")
                res.written.setdefault(t.gid, []).append(t.xmp)
            except Exception as e:
                res.failed += 1
                res.errors.append(f"{os.path.basename(t.raw)}：{e}")
                if op:
                    journal.set_op(op["id"], "failed", str(e))
            if i % 20 == 0:
                journal.commit()
            if progress:
                progress(i, total, os.path.basename(t.xmp))
    finally:
        try:
            et.terminate()
        except Exception:
            pass
    for o in journal.ops(bid):
        if o["status"] == "pending":
            journal.set_op(o["id"], "skipped")
    journal.commit()
    journal.finish_batch(bid, "cancelled" if res.cancelled else ("failed" if res.failed else "done"))
    return res
