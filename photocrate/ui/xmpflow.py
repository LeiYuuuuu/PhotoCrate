"""写回 XMP 的完整流程（「时间与地点」页和「挑片」页共用）：生成计划 → 预览确认 → 后台写入 → 结果。"""
from __future__ import annotations

from ..ops.journal import OPERATION_LABELS
from .dialogs import info, run_with_progress


def run_xmp_flow(ctx, parent, groups: list, scope_text: str, *, gps: bool, place: bool, time: bool,
                 rating: bool, update_existing: bool) -> object | None:
    from ..ops.xmpwrite import build_xmp_plan, write_xmp
    from .timeplace_dialogs import XmpDialog
    lib = ctx.library
    if not lib.exiftool_path:
        info(parent, "需要 ExifTool", "写入 XMP 需要 ExifTool。请在「系统设置」中检测或指定 exiftool.exe。")
        return None
    if not (gps or place or time or rating):
        info(parent, "没有选择写入内容", "请至少勾选一项要写入的内容。")
        return None
    plan = build_xmp_plan(groups, gps, place, time, rating, update_existing)
    if not plan.tasks:
        hint = ("只有补全了地点（轨迹匹配 / 手动指定）、校正了时间，或在本软件里打过星级 / 色标 / 弃用的 RAW 才会写入。")
        ex = (f"已有 .xmp（未勾选“更新已有 .xmp”）{plan.skipped_exists} 张 · " if plan.skipped_exists else "")
        info(parent, "没有需要写入的照片",
             f"范围：{scope_text}<br>没有可写内容 {plan.skipped_nothing} 张 · {ex}非 RAW {plan.skipped_no_raw} 张。"
             f"<br><br>{hint}")
        return None
    if XmpDialog(parent, plan, scope_text).exec() != XmpDialog.DialogCode.Accepted:
        return None
    ctx.busy = True

    def job(prog, cancel):
        return write_xmp(plan, lib.exiftool_path, ctx.journal, lambda d, t, c: prog((d, t, c)), cancel)

    try:
        res, tb = run_with_progress(ctx, parent, "正在写入 XMP", "WRITING", job)
    finally:
        ctx.busy = False
    if tb or res is None:
        info(parent, "写入出错", f"<pre style='font-size:8pt'>{(tb or '')[-1500:]}</pre>", "ERROR")
        return None
    n = lib.attach_files(res.written)
    ctx.files_changed.emit()
    parts = []
    if n:
        parts.append(f"新建 <b>{n}</b> 个 .xmp")
    if res.edited:
        parts.append(f"更新 <b>{res.edited}</b> 个已有 .xmp（原文件已备份）")
    msg = ("、".join(parts) or "没有写入任何文件") + f"（批次 #{res.batch_id}，可在「操作记录」页撤销）。"
    if res.failed:
        msg += f"<br><br>{res.failed} 个失败：<br>" + "<br>".join(res.errors[:8])
    if res.cancelled:
        msg += "<br><br>已取消，剩余照片未写入。"
    ctx.status.emit(f"{OPERATION_LABELS['xmp']}：新建 {n} · 更新 {res.edited}")
    info(parent, "写入完成", msg, "COMPLETE")
    return res
