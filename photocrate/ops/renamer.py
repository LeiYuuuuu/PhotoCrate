"""批量重命名：按模板生成新文件名 → 预览 → 确认 → 执行（与移动共用日志与撤销）。

模板写法与分区模板相同（占位符见「分区编排」→ 高级模板），另外支持：
    {序号}  {序号:4}      按拍摄时间排序后的序号（默认 3 位，补零）
    {当日序号}           每天从 1 重新开始
    {原名}               原来的主文件名（不含扩展名）
例：{日期}_{市|省|国家|未知地点}_{序号}  →  20260105_东京_001.JPG / 20260105_东京_001.ARW
    （{日期} = 20260105，{时间} = 153012；也可以用 {年月日} = 2026-01-05）

规则：
- 同一组（RAW + JPG + .xmp …）改成同一个主文件名，扩展名与“.ARW.xmp”这类双扩展名保留；
- 新名字与已有文件冲突时自动加编号“ (1)”，绝不覆盖；只是大小写不同或与原名相同的跳过；
- 照片不换文件夹；改名后手动地点、时间校正、星级等标注与缓存会自动迁移到新文件名。
"""
from __future__ import annotations

import os
import re

from ..partition.base import PartitionContext, all_tokens
from ..partition.engine import render_template
from .pathsafe import sanitize_component
from .planner import Plan, PlanItem, PlanOp, _NameBook

_SEQ_RE = re.compile(r"\{序号(?::(\d))?\}")


def _suffix(member_name: str, old_stem: str) -> str:
    if old_stem and member_name.lower().startswith(old_stem.lower()):
        return member_name[len(old_stem):]
    return os.path.splitext(member_name)[1]


def render_name(template: str, g, ctx: PartitionContext, seq: int, day_seq: int, tokens=None) -> str:
    t = _SEQ_RE.sub(lambda m: str(seq).zfill(int(m.group(1) or 3)), template)
    t = t.replace("{当日序号}", str(day_seq).zfill(2)).replace("{原名}", g.stem)
    segs = render_template(t.replace("/", "_").replace("\\", "_"), g, ctx, tokens)
    return sanitize_component("_".join(segs)).strip(" .")


def build_rename_plan(groups, template: str, ctx: PartitionContext, start: int = 1) -> Plan:
    plan = Plan(spec_text=f"批量重命名：{template}", target_root="", operation="rename")
    tokens = all_tokens()
    book = _NameBook()
    ordered = sorted((g for g in groups if g.files), key=lambda g: (g.date is None, g.date or 0, g.name.lower()))
    day_counter: dict[str, int] = {}
    unchanged = 0
    dirs = set()
    for i, g in enumerate(ordered):
        day = g.date.strftime("%Y-%m-%d") if g.date else ""
        day_counter[day] = day_counter.get(day, 0) + 1
        stem = render_name(template, g, ctx, start + i, day_counter[day], tokens)
        if not stem:
            plan.warnings.append(f"{g.name}：模板生成的名字为空，已跳过")
            continue
        if stem.lower() == g.stem.lower():
            unchanged += 1
            continue
        members = [(m, _suffix(m.name, g.stem)) for m in g.files]
        n = 0
        while True:
            cand = stem if n == 0 else f"{stem} ({n})"
            if all(not book.taken(os.path.dirname(m.path), cand + suf) for m, suf in members):
                break
            n += 1
            if n > 9999:
                cand = None
                break
        if cand is None:
            plan.skipped_conflict.append(g.gid)
            continue
        ops = []
        for m, suf in members:
            d = os.path.dirname(m.path)
            book.reserve(d, cand + suf)
            ops.append(PlanOp(m.path, os.path.join(d, cand + suf), m.size, m.mtime_ns, m.role))
            plan.total_bytes += m.size
            dirs.add(d)
        if n > 0:
            plan.renamed += 1
        plan.items.append(PlanItem(g.gid, ops, os.path.dirname(g.primary), n > 0, False))
    if dirs:
        try:
            plan.target_root = os.path.commonpath(sorted(dirs))
        except ValueError:
            plan.target_root = sorted(dirs)[0]
    if unchanged:
        plan.warnings.append(f"{unchanged} 张照片的新名字与原名相同，跳过。")
    if plan.renamed:
        plan.warnings.append(f"{plan.renamed} 张照片的新名字与已有文件重名，已自动加编号（不会覆盖）。")
    if not plan.items:
        plan.warnings.append("没有需要改名的照片。")
    return plan
