"""导入向导：从存储卡 / 手机 / 任意文件夹复制照片到电脑，跳过已经导入过的，按规则建文件夹、可选改名。

- 只复制，不动存储卡上的原文件（复制完成后可以在相机里格式化）；
- 先写日志再复制，可在「操作记录」撤销（复制出来的文件进回收站）；
- “已导入”的判断：导入记录里有同名、同大小、同修改时间的文件且目标文件还在，或者照片库里已有同一个文件
  （文件名 + 大小 + 修改时间相同），或者目标文件夹里已经有同名且大小相同的文件；
- 复制保留修改时间，所以导入后扫描目标文件夹时，读卡时做过的分析直接命中缓存。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import string
import threading
import time
from dataclasses import dataclass, field

from ..partition.base import PartitionContext
from ..partition.engine import PartitionSpec, Rule
from .pathsafe import existing_ancestor
from .planner import Plan, PlanOp, _NameBook, build_plan
from ..i18n import t as _t

TEMPLATES = [
    (_t("{年}/{年月日}"), _t("年 / 年-月-日（2026/2026-01-05）")),
    (_t("{年}/{年月}"), _t("年 / 年-月（2026/2026-01）")),
    (_t("{年月日}"), _t("年-月-日（2026-01-05）")),
    (_t("{年}/{年月日} {市|省|国家}"), _t("年 / 日期 + 地点（有 GPS 时）")),
    (_t("{相机}/{年}/{年月日}"), _t("相机 / 年 / 日期")),
]


# ---------------------------------------------------------------------------
# 找存储卡
# ---------------------------------------------------------------------------

@dataclass
class Source:
    path: str
    label: str
    removable: bool = False


def detect_sources() -> list[Source]:
    """可移动磁盘（存储卡、U 盘、相机）以及任何带 DCIM 文件夹的磁盘。"""
    out: list[Source] = []
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        mask = k32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if not mask & (1 << i) or letter in "AB":
                continue
            root = f"{letter}:\\"
            dtype = k32.GetDriveTypeW(root)       # 2 = 可移动，3 = 本地硬盘，4 = 网络，5 = 光驱
            if dtype not in (2, 3):
                continue
            dcim = os.path.join(root, "DCIM")
            has_dcim = os.path.isdir(dcim)
            if dtype == 2 or has_dcim:
                name = ""
                try:
                    buf = ctypes.create_unicode_buffer(261)
                    if k32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0):
                        name = buf.value
                except Exception:
                    pass
                label = f"{letter}:  {name or (_t('可移动磁盘') if dtype == 2 else _t('本地磁盘'))}" + (_t("（相机存储卡）") if has_dcim else "")
                out.append(Source(root, label, dtype == 2))
    else:
        for base in ("/media", "/run/media", "/Volumes"):
            if not os.path.isdir(base):
                continue
            for dirpath, dirs, _ in os.walk(base):
                if "DCIM" in dirs:
                    out.append(Source(dirpath, _t('{0}（相机存储卡）', os.path.basename(dirpath)), True))
                if dirpath.count(os.sep) - base.count(os.sep) >= 2:
                    dirs[:] = []
    return out


def card_roots(src: str) -> list[str]:
    """存储卡上真正放照片 / 视频的文件夹：DCIM（照片）与 PRIVATE/M4ROOT/CLIP（索尼视频）、PRIVATE/AVCHD 等；
    不是存储卡就是文件夹本身。"""
    roots = []
    for rel in ("DCIM", os.path.join("PRIVATE", "M4ROOT", "CLIP"), os.path.join("PRIVATE", "AVCHD"),
                os.path.join("MP_ROOT"), "AVF_INFO"):
        p = os.path.join(src, rel)
        if os.path.isdir(p):
            roots.append(p)
    return [r for r in roots if os.path.basename(r) != "AVF_INFO"] or [src]


# ---------------------------------------------------------------------------
# 导入记录
# ---------------------------------------------------------------------------

class ImportHistory:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS imported(sig TEXT PRIMARY KEY, src TEXT, dst TEXT, "
                         "size INTEGER, batch INTEGER, time REAL)")
        self._db.commit()

    def lookup(self, sigs: list[str]) -> dict[str, tuple[str, int]]:
        out = {}
        with self._lock:
            for i in range(0, len(sigs), 500):
                ch = sigs[i:i + 500]
                for sig, dst, size in self._db.execute(
                        f"SELECT sig, dst, size FROM imported WHERE sig IN ({','.join('?' * len(ch))})", ch):
                    out[sig] = (dst, size)
        return out

    def record(self, rows: list[tuple[str, str, str, int, int]]) -> None:
        with self._lock, self._db:
            self._db.executemany("INSERT OR REPLACE INTO imported(sig, src, dst, size, batch, time) "
                                 "VALUES(?,?,?,?,?,?)", [(*r, time.time()) for r in rows])

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM imported").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass


def history_path():
    from ..config import user_data_dir
    return user_data_dir() / "imports.db"


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------

@dataclass
class ImportCheck:
    new: list = field(default_factory=list)              # 要导入的组
    already: list = field(default_factory=list)          # 已经导入过 / 照片库里已有的组
    photos: int = 0
    videos: int = 0
    bytes: int = 0


def classify(groups, history: ImportHistory | None, library_sigs: set[str] | None = None) -> ImportCheck:
    """把存储卡上的照片分成“新的”和“已导入过的”（组内所有主体文件都已导入才算已导入）。"""
    chk = ImportCheck()
    from ..core.formats import MEDIA_ROLES
    sigs = [f.sig for g in groups for f in g.files if f.role in MEDIA_ROLES]
    hist = history.lookup(sigs) if history is not None else {}
    lib = library_sigs or set()
    for g in groups:
        if not g.files:
            continue
        media = [f for f in g.files if f.role in MEDIA_ROLES]

        def done(f):
            if f.sig in lib:
                return True
            h = hist.get(f.sig)
            return bool(h and os.path.isfile(h[0]) and os.path.getsize(h[0]) == f.size)
        if media and all(done(f) for f in media):
            chk.already.append(g)
            continue
        chk.new.append(g)
        if g.is_video:
            chk.videos += 1
        else:
            chk.photos += 1
        chk.bytes += g.total_size
    return chk


def build_import_plan(groups, target_root: str, template: str, ctx: PartitionContext,
                      rename_template: str = "", start: int = 1) -> Plan:
    """复制计划：文件夹按 template（与分区“高级模板”写法相同），文件名可选按 rename_template 改。
    目标位置已有同名且大小相同的文件 → 视为已导入，跳过；同名不同内容 → 自动编号。"""
    spec = PartitionSpec("template", template=template) if template.strip() else PartitionSpec("rules", [])
    # 先剔除目标位置已有同名同大小文件的组（空间检查放到最后，只算真正要复制的）
    pre = build_plan(groups, spec, ctx, target_root, "copy", "rename", "none", verify_free_space=False)
    plan = Plan(spec_text=_t('导入：{0}', template or _t('（不建子文件夹）')) + (_t('；改名 {0}', rename_template) if rename_template else ""),
                target_root=pre.target_root, operation="copy", errors=list(pre.errors))
    by_gid = {g.gid: g for g in groups}
    same_existing = 0
    keep_items = []
    for it in pre.items:
        g = by_gid[it.gid]
        exists_same = True
        for op in it.ops:
            d = os.path.join(os.path.dirname(op.dst), os.path.basename(op.src))
            if not (os.path.isfile(d) and os.path.getsize(d) == op.size):
                exists_same = False
                break
        if exists_same:
            same_existing += 1
            continue
        keep_items.append((it, g))
    book = _NameBook()
    skip: set[int] = set()
    if rename_template:
        from ..partition.base import all_tokens
        from .renamer import _suffix, render_name
        tokens = all_tokens()
        ordered = sorted(keep_items, key=lambda t: (t[1].date is None, t[1].date or 0, t[1].name.lower()))
        day_n: dict[str, int] = {}
        for i, (it, g) in enumerate(ordered):
            day = g.date.strftime("%Y-%m-%d") if g.date else ""
            day_n[day] = day_n.get(day, 0) + 1
            stem = render_name(rename_template, g, ctx, start + i, day_n[day], tokens) or g.stem
            members = [(op, _suffix(os.path.basename(op.src), g.stem)) for op in it.ops]
            if all(os.path.isfile(p := os.path.join(os.path.dirname(op.dst), stem + suf))
                   and os.path.getsize(p) == op.size for op, suf in members):
                skip.add(id(it))                      # 以前按同一模板导入过（同名、同大小）
                same_existing += 1
                continue
            n = 0
            while True:
                cand = stem if n == 0 else f"{stem} ({n})"
                if all(not book.taken(os.path.dirname(op.dst), cand + suf) for op, suf in members):
                    break
                n += 1
            for op, suf in members:
                book.reserve(os.path.dirname(op.dst), cand + suf)
                op.dst = os.path.join(os.path.dirname(op.dst), cand + suf)
            it.renamed = n > 0
    for it, g in keep_items:
        if id(it) in skip:
            continue
        plan.items.append(it)
        plan.total_bytes += sum(op.size for op in it.ops)
    plan.cross_bytes = plan.total_bytes
    plan.new_dirs = pre.new_dirs
    if plan.total_bytes and target_root:
        try:
            plan.free_bytes = shutil.disk_usage(existing_ancestor(os.path.abspath(target_root))).free
            if plan.free_bytes < plan.total_bytes * 1.02 + 50 * 1024 * 1024:
                plan.errors.append(_t("目标磁盘剩余空间不足。"))
        except OSError:
            pass
    if same_existing:
        plan.warnings.append(_t('{0} 张照片在目标文件夹里已有同名、同大小的文件，视为已导入，跳过。', same_existing))
    plan.warnings += [w for w in pre.warnings if w != _t("没有需要移动的照片（可能都已经在目标位置）。")]
    if not plan.items and not plan.errors:
        plan.warnings.append(_t("没有需要导入的照片。"))
    return plan


def record_import(history: ImportHistory, plan: Plan, res, groups) -> int:
    """把成功复制的文件写进导入记录。"""
    by_src = {}
    for g in groups:
        for f in g.files:
            by_src[os.path.normcase(os.path.abspath(f.path))] = f
    rows = []
    for src, dst in res.copied:
        f = by_src.get(os.path.normcase(os.path.abspath(src)))
        if f is not None:
            rows.append((f.sig, f.path, dst, f.size, res.batch_id))
    if rows:
        history.record(rows)
    return len(rows)
