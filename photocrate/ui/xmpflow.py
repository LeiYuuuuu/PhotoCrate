"""写回 XMP 的完整流程（「时间与地点」页和「挑片」页共用）：生成计划 → 预览确认 → 后台写入 → 结果。"""
from __future__ import annotations

from ..ops.journal import OPERATION_LABELS
from .dialogs import info, run_with_progress
from ..i18n import t as _t


def run_xmp_flow(ctx, parent, groups: list, scope_text: str, *, gps: bool, place: bool, time: bool,
                 rating: bool, update_existing: bool, keywords: bool = False) -> object | None:
    from ..ops.xmpwrite import build_xmp_plan, write_xmp
    from .timeplace_dialogs import XmpDialog
    lib = ctx.library
    if not lib.exiftool_path:
        info(parent, _t("需要 ExifTool"), _t("写入 XMP 需要 ExifTool。请在「系统设置」中检测或指定 exiftool.exe。"))
        return None
    if not (gps or place or time or rating or keywords):
        info(parent, _t("没有选择写入内容"), _t("请至少勾选一项要写入的内容。"))
        return None
    plan = build_xmp_plan(groups, gps, place, time, rating, update_existing, keywords)
    if not plan.tasks:
        hint = (_t("只有补全了地点（轨迹匹配 / 手动指定）、校正了时间、在本软件里打过星级 / 色标 / 弃用，"
                "或有 AI 关键词（场景 / 已命名的人物 / 旅行）的 RAW 才会写入。"))
        ex = (_t('已有 .xmp（未勾选“更新已有 .xmp”）{0} 张 · ', plan.skipped_exists) if plan.skipped_exists else "")
        info(parent, _t("没有需要写入的照片"),
             _t('范围：{0}<br>没有可写内容 {1} 张 · {2}非 RAW {3} 张。<br><br>{4}', scope_text, plan.skipped_nothing, ex, plan.skipped_no_raw, hint))
        return None
    if XmpDialog(parent, plan, scope_text).exec() != XmpDialog.DialogCode.Accepted:
        return None
    ctx.busy = True

    def job(prog, cancel):
        return write_xmp(plan, lib.exiftool_path, ctx.journal, lambda d, t, c: prog((d, t, c)), cancel)

    try:
        res, tb = run_with_progress(ctx, parent, _t("正在写入 XMP"), "WRITING", job)
    finally:
        ctx.busy = False
    if tb or res is None:
        info(parent, _t("写入出错"), f"<pre style='font-size:8pt'>{(tb or '')[-1500:]}</pre>", "ERROR")
        return None
    n = lib.attach_files(res.written)
    ctx.files_changed.emit()
    parts = []
    if n:
        parts.append(_t('新建 <b>{0}</b> 个 .xmp', n))
    if res.edited:
        parts.append(_t('更新 <b>{0}</b> 个已有 .xmp（原文件已备份）', res.edited))
    msg = (_t("、").join(parts) or _t("没有写入任何文件")) + _t('（批次 #{0}，可在「操作记录」页撤销）。', res.batch_id)
    if res.failed:
        msg += _t('<br><br>{0} 个失败：<br>', res.failed) + "<br>".join(res.errors[:8])
    if res.cancelled:
        msg += _t("<br><br>已取消，剩余照片未写入。")
    ctx.status.emit(_t('{0}：新建 {1} · 更新 {2}', OPERATION_LABELS['xmp'], n, res.edited))
    info(parent, _t("写入完成"), msg, "COMPLETE")
    return res
