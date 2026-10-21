"""把软件里补全 / 校正 / 标记的信息写回 XMP 附属文件（Lightroom、Bridge、Capture One 会读取）。

原则（与移动文件一样保守）：
- 只给 RAW 写“主文件名.xmp”（如 DSC01234.ARW → DSC01234.xmp），绝不修改照片原文件；
- 没有 .xmp 时新建；已有 .xmp（通常是 Lightroom / Bridge 的修图记录）时：
    · 默认更新其中对应的字段（ExifTool 只改这几个标签，其余修图设置原样保留），
      改之前先把原文件完整备份到用户数据目录，撤销时原样换回；
    · 也可以选择跳过已有 .xmp 的照片；
- 先写日志再写文件；在「记录」页撤销 = 新建的 .xmp 放入回收站、修改过的 .xmp 恢复为备份。

可写入的内容：
- GPS 坐标：来自 GPS 轨迹 / 参考照片按时间匹配的结果（手动指定的地点只有地名，不写坐标）；
- 地点名称：国家 / 省 / 市（XMP-photoshop:Country / State / City、XMP-iptcCore:CountryCode）；
- 校正后的拍摄时间：XMP-exif:DateTimeOriginal 与 XMP-photoshop:DateCreated（带时区）；
- 星级 / 色标 / 弃用：XMP-xmp:Rating（弃用写成 -1，Bridge 显示为“拒绝”）、XMP-xmp:Label（Red / Yellow …）。
  “留用”是 Lightroom 目录里的旗标，XMP 里没有对应标准字段，只保存在本软件里。
"""
from __future__ import annotations

import logging
import os
import shutil
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
    mode: str = "create"         # create（新建）/ edit（更新已有 .xmp）
    backup: str = ""             # edit 时原文件的备份位置


@dataclass
class XmpPlan:
    tasks: list[XmpTask] = field(default_factory=list)
    skipped_exists: int = 0      # 旁边已有 .xmp 且选择了不更新
    skipped_no_raw: int = 0      # 不是 RAW（JPG/HEIC 不生成附属文件）
    skipped_nothing: int = 0     # 没有需要写入的内容
    operation: str = "xmp"
    target_root: str = ""
    spec_text: str = ""

    # ---- 与移动计划同样的接口，供 Journal.create_batch 使用 ----
    @property
    def items(self) -> list[PlanItem]:
        out = []
        for t in self.tasks:
            if t.mode == "edit":
                op = PlanOp(t.backup, t.xmp, 0, 0, "xmp_edit")       # src = 备份位置，撤销时用它换回
            else:
                op = PlanOp(t.raw, t.xmp, 0, 0, "xmp")
            out.append(PlanItem(t.gid, [op], os.path.dirname(t.xmp)))
        return out

    @property
    def file_count(self) -> int:
        return len(self.tasks)

    @property
    def total_bytes(self) -> int:
        return 0

    @property
    def created(self) -> int:
        return sum(1 for t in self.tasks if t.mode == "create")

    @property
    def edited(self) -> int:
        return sum(1 for t in self.tasks if t.mode == "edit")


def _fmt_dt(dt: datetime, tz: str | None) -> str:
    return dt.strftime("%Y:%m:%d %H:%M:%S") + (tz or "")


def tags_for(g, write_gps: bool, write_place: bool, write_time: bool,
             write_rating: bool = False) -> tuple[dict[str, str], list[str]]:
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
    r = g.feats.get("rating") or {}
    if write_rating and r.get("manual"):
        # 只写在本软件里改过的评级；照片原本就有的评级不重复写
        tags["XMP-xmp:Rating"] = "-1" if r.get("flag") == "reject" else str(int(r.get("rating") or 0))
        tags["XMP-xmp:Label"] = r.get("label") or ""
        what.append("星级/色标")
    return tags, what


def sidecar_path(raw_path: str) -> str:
    return os.path.splitext(raw_path)[0] + ".xmp"


def existing_xmp(g, raw_path: str) -> str | None:
    """已有的附属文件（Lightroom 风格 主文件名.xmp 优先，其次 darktable 风格 文件名.扩展名.xmp）。"""
    for p in (sidecar_path(raw_path), raw_path + ".xmp"):
        if os.path.lexists(p):
            return p
    for f in g.files:
        if os.path.normcase(f.path) in (os.path.normcase(sidecar_path(raw_path)), os.path.normcase(raw_path + ".xmp")) \
                and os.path.lexists(f.path):
            return f.path
    return None


def build_xmp_plan(groups, write_gps: bool = True, write_place: bool = True, write_time: bool = True,
                   write_rating: bool = False, update_existing: bool = False) -> XmpPlan:
    plan = XmpPlan()
    parts = [n for n, on in (("GPS", write_gps), ("地点", write_place), ("校正时间", write_time),
                             ("星级/色标", write_rating)) if on]
    plan.spec_text = "写入 XMP：" + "、".join(parts)
    dirs = set()
    for g in groups:
        if not g.files:
            continue
        raws = [f for f in g.image_files if f.role == "raw"]
        tags, what = tags_for(g, write_gps, write_place, write_time, write_rating)
        if not tags:
            plan.skipped_nothing += 1
            continue
        if not raws:
            plan.skipped_no_raw += 1
            continue
        raw = raws[0].path
        ex = existing_xmp(g, raw)
        if ex:
            if not update_existing:
                plan.skipped_exists += 1
                continue
            plan.tasks.append(XmpTask(g.gid, raw, ex, tags, what, "edit"))
        else:
            plan.tasks.append(XmpTask(g.gid, raw, sidecar_path(raw), tags, what, "create"))
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
    edited: int = 0
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)


def backup_dir() -> str:
    from ..config import user_data_dir
    d = os.path.join(str(user_data_dir()), "xmp_backups")
    os.makedirs(d, exist_ok=True)
    return d


def write_xmp(plan: XmpPlan, exiftool_path: str, journal, progress: Callable[[int, int, str], None] | None = None,
              cancel: threading.Event | None = None) -> XmpResult:
    if not exiftool_path:
        raise RuntimeError("写入 XMP 需要 ExifTool")
    import exiftool

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    bdir = None
    for i, t in enumerate(plan.tasks):
        if t.mode == "edit" and not t.backup:
            if bdir is None:
                bdir = os.path.join(backup_dir(), stamp)
                os.makedirs(bdir, exist_ok=True)
            t.backup = os.path.join(bdir, f"{i:05d}_{os.path.basename(t.xmp)}")
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
                if not os.path.exists(t.raw):
                    raise FileNotFoundError("照片已不在原位置，请重新扫描")
                args = [f"-{k}={v}" for k, v in t.tags.items()]
                if t.mode == "edit":
                    if not os.path.exists(t.xmp):
                        raise FileNotFoundError("原有的 .xmp 已不存在，请重新扫描")
                    shutil.copy2(t.xmp, t.backup)                     # 先完整备份
                    if os.path.getsize(t.backup) != os.path.getsize(t.xmp):
                        raise IOError("备份 .xmp 失败")
                    out = et.execute(*args, "-overwrite_original", t.xmp) or ""
                    if "1 image files updated" not in out and "1 image files unchanged" not in out:
                        if os.path.exists(t.backup):
                            shutil.copy2(t.backup, t.xmp)                # 失败时换回原样
                        raise IOError((et.last_stderr or out or "ExifTool 没有更新文件").strip()[:200])
                    res.edited += 1
                else:
                    if os.path.lexists(t.xmp):
                        raise FileExistsError("已存在同名 .xmp，未改动")
                    et.execute(*args, t.xmp)
                    if not os.path.exists(t.xmp):
                        raise IOError((et.last_stderr or "ExifTool 没有生成文件").strip()[:200])
                    res.written.setdefault(t.gid, []).append(t.xmp)
                journal.set_op(op["id"], "done")
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
