"""生成移动计划（模拟运行）：计算每个文件的目标路径、处理重名、统计空间。

这一步不碰任何文件，结果用于预览确认；确认后交给 Mover 执行。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from ..partition.base import (FORMAT_TOKEN, PartitionContext, all_tokens, expand_format, is_raw_side,
                              is_unknown_label, member_format)
from ..partition.engine import PartitionSpec, group_segments
from .pathsafe import device_of, existing_ancestor, same_path
from ..i18n import t as _t


@dataclass
class PlanOp:
    src: str
    dst: str
    size: int
    mtime_ns: int
    role: str


@dataclass
class PlanItem:
    gid: int
    ops: list[PlanOp]
    folder: str            # 目标相对文件夹（显示用）
    renamed: bool = False
    cross_device: bool = False


@dataclass
class Plan:
    spec_text: str
    target_root: str
    operation: str                      # move / copy
    items: list[PlanItem] = field(default_factory=list)
    skipped_inplace: int = 0
    skipped_conflict: list[int] = field(default_factory=list)
    renamed: int = 0
    total_bytes: int = 0
    cross_bytes: int = 0
    free_bytes: int | None = None
    new_dirs: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    guess_dates: int = 0
    inferred_places: int = 0
    matched_places: int = 0
    manual_places: int = 0
    corrected_times: int = 0

    @property
    def file_count(self) -> int:
        return sum(len(i.ops) for i in self.items)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.items)


class _NameBook:
    """记录每个目标文件夹里已占用的文件名（磁盘上已有的 + 本计划已分配的）。"""

    def __init__(self):
        self._names: dict[str, set[str]] = {}

    def names(self, folder: str) -> set[str]:
        k = os.path.normcase(os.path.abspath(folder))
        s = self._names.get(k)
        if s is None:
            try:
                s = {n.lower() for n in os.listdir(folder)}
            except OSError:
                s = set()
            self._names[k] = s
        return s

    def taken(self, folder: str, name: str) -> bool:
        return name.lower() in self.names(folder) or (name + ".pctmp").lower() in self.names(folder)

    def reserve(self, folder: str, name: str) -> None:
        self.names(folder).add(name.lower())


def _inside(path: str, root: str) -> bool:
    """path 是否在 root 里面（或就是 root），不区分大小写 / 斜杠方向。"""
    a = os.path.normcase(os.path.abspath(path))
    b = os.path.normcase(os.path.abspath(root))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def sources_after_move(source_dirs: list[str], target_root: str) -> list[str]:
    """移动之后的数据源：照片已经搬到目标文件夹，下次扫描要从那里读。
    目标在某个数据源里面 → 不变；目标包含某些数据源 → 用目标替换它们（放在第一个被替换的位置）；否则追加。"""
    if not target_root:
        return list(source_dirs)
    tgt = os.path.normpath(target_root)
    if any(_inside(tgt, d) for d in source_dirs):
        return list(source_dirs)
    out, placed = [], False
    for d in source_dirs:
        if _inside(d, tgt):
            if not placed:
                out.append(tgt)
                placed = True
            continue
        out.append(d)
    if not placed:
        out.append(tgt)
    return out


def _subfolder_for(member, group, split: str) -> str | None:
    """RAW/JPG 拆分时，成员应放入的子文件夹名。"""
    if split == "none":
        return None
    raw_only = group.has_raw and all(f.role == "raw" for f in group.image_files)
    if is_raw_side(member, group) or (raw_only and member.role == "sidecar"):
        return "RAW"
    if split != "both_sub":
        return None
    # 其他文件按格式分：JPG / HEIC / PNG …；附属文件跟随所属图片
    return member_format(member, group)


def _renamed(name: str, stem: str, n: int) -> str:
    if n == 0:
        return name
    if name.lower().startswith(stem.lower()) and len(stem) > 0:
        return f"{stem} ({n}){name[len(stem):]}"
    s, e = os.path.splitext(name)
    return f"{s} ({n}){e}"


def build_plan(groups, spec: PartitionSpec, ctx: PartitionContext, target_root: str,
               operation: str = "move", conflict: str = "rename", split: str = "none",
               verify_free_space: bool = True) -> Plan:
    plan = Plan(spec_text=spec.describe(), target_root=target_root, operation=operation)
    if not target_root:
        plan.errors.append(_t("请先选择目标文件夹。"))
        return plan
    target_root = os.path.abspath(target_root)
    book = _NameBook()
    tokens = all_tokens()
    tgt_dev = device_of(target_root)
    dev_cache: dict[str, Any] = {}
    new_dirs: set[str] = set()
    unknown: dict[str, int] = {}

    for g in groups:
        if not g.files:
            continue
        segs = group_segments(g, spec, ctx, tokens)
        for seg in dict.fromkeys(segs):
            if is_unknown_label(seg):
                unknown[seg] = unknown.get(seg, 0) + 1
        per_file = any(FORMAT_TOKEN in s for s in segs)
        stem = g.stem
        # 每个成员的目标文件夹（“按单个文件格式”时 RAW 与 JPG 各去各的文件夹）
        targets = []
        folder = os.path.join(target_root, *[expand_format(s, g.primary_file, g) for s in segs])
        for m in g.files:
            base = os.path.join(target_root, *[expand_format(s, m, g) for s in segs]) if per_file else folder
            sub = _subfolder_for(m, g, split)
            targets.append((m, os.path.join(base, sub) if sub else base))
        # 全部已在目标位置 → 跳过
        inplace = [same_path(os.path.join(d, m.name), m.path) for m, d in targets]
        if all(inplace):
            plan.skipped_inplace += 1
            continue
        moving = [(m, d) for (m, d), ip in zip(targets, inplace) if not ip]
        # 选一个对整组都可用的编号，保证 IMG_1 (1).CR3 与 IMG_1 (1).JPG 仍然成对
        n = 0
        while True:
            if all(not book.taken(d, _renamed(m.name, stem, n)) for m, d in moving):
                break
            if conflict == "skip":
                n = -1
                break
            n += 1
            if n > 9999:
                n = -1
                break
        if n < 0:
            plan.skipped_conflict.append(g.gid)
            continue
        ops = []
        cross = False
        for m, d in moving:
            name = _renamed(m.name, stem, n)
            book.reserve(d, name)
            dst = os.path.join(d, name)
            sd = os.path.dirname(m.path)
            if sd not in dev_cache:
                dev_cache[sd] = device_of(sd)
            if dev_cache[sd] != tgt_dev:
                cross = True
            # 统计会新建的每一级文件夹（年 / 月 / 日 都算），而不只是最底层
            q = d
            while q and not os.path.isdir(q) and os.path.normcase(q) not in new_dirs:
                new_dirs.add(os.path.normcase(q))
                parent = os.path.dirname(q)
                if parent == q:
                    break
                q = parent
            ops.append(PlanOp(m.path, dst, m.size, m.mtime_ns, m.role))
            plan.total_bytes += m.size
            if cross or operation == "copy":
                plan.cross_bytes += m.size
        if n > 0:
            plan.renamed += 1
        if g.date_is_guess:
            plan.guess_dates += 1
        if g.feat("geo", "inferred"):
            plan.inferred_places += 1
        src = g.feat("geo", "source")
        if src == "track":
            plan.matched_places += 1
        elif src == "manual":
            plan.manual_places += 1
        if g.feat("date", "corrected"):
            plan.corrected_times += 1
        plan.items.append(PlanItem(g.gid, ops, os.path.relpath(folder, target_root), n > 0, cross))

    plan.new_dirs = len(new_dirs)
    # 空间检查（跨盘移动 / 复制需要目标盘有空间）
    if verify_free_space and plan.cross_bytes:
        try:
            plan.free_bytes = shutil.disk_usage(existing_ancestor(target_root)).free
            if plan.free_bytes < plan.cross_bytes * 1.02 + 50 * 1024 * 1024:
                plan.errors.append(_t("目标磁盘剩余空间不足。"))
        except OSError:
            pass
    for label, n in unknown.items():
        plan.warnings.append(_t('{0} 组照片无法判断，将放入「{1}」文件夹。', n, label))
    if plan.guess_dates:
        plan.warnings.append(_t('{0} 张照片的日期来自文件名或修改时间推测，请在预览中确认。', plan.guess_dates))
    if plan.inferred_places:
        plan.warnings.append(_t('{0} 张照片的地点由相邻时间的照片推测。', plan.inferred_places))
    if plan.matched_places:
        plan.warnings.append(_t('{0} 张照片的地点来自 GPS 轨迹 / 参考照片的时间匹配。', plan.matched_places))
    if plan.manual_places:
        plan.warnings.append(_t('{0} 张照片使用手动指定的地点。', plan.manual_places))
    if plan.corrected_times:
        plan.warnings.append(_t('{0} 张照片使用校正后的拍摄时间（照片文件本身未修改）。', plan.corrected_times))
    if plan.skipped_conflict:
        plan.warnings.append(_t('{0} 组因目标位置已有同名文件而跳过。', len(plan.skipped_conflict)))
    if plan.renamed:
        plan.warnings.append(_t('{0} 组与已有文件重名，将自动加编号（不会覆盖）。', plan.renamed))
    if not plan.items and not plan.errors:
        plan.warnings.append(_t("没有需要移动的照片（可能都已经在目标位置）。"))
    return plan
