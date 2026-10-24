"""05 挑片：大图查看与对比、打星 / 色标 / 留弃、连拍与相似照片分组，以及清理 RAW、批量重命名。"""
from __future__ import annotations

import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QRadioButton, QSpinBox,
                               QVBoxLayout)

from ...ops.planner import build_plan
from ...partition.base import PartitionContext
from ...partition.engine import PartitionSpec, Rule
from .. import theme
from ..dialogs import PlanDialog, info, run_with_progress
from ..widgets.deco import Panel, StatCard, TacButton, caption
from ..widgets.thumbgrid import ThumbGrid
from .base import Page
from ...i18n import t as _t

VIEWS = [("all", _t("全部")), ("similar", _t("相似组")), ("unrated", _t("未标记")), ("rated", _t("已打星")), ("pick", _t("留用")),
         ("reject", _t("弃用"))]


def _rt(g) -> dict:
    return g.feats.get("rating") or {}


def is_marked(g) -> bool:
    r = _rt(g)
    return int(r.get("rating") or 0) > 0 or bool(r.get("flag")) or bool(r.get("label"))


def similar_extras(groups) -> list:
    """每个相似组里“非最佳、也没有被打星 / 标记留用”的照片——建议移到「待确认」。"""
    out = []
    for g in groups:
        sim = g.feats.get("similar")
        if not sim or sim.get("best"):
            continue
        r = _rt(g)
        if int(r.get("rating") or 0) > 0 or r.get("flag") == "pick":
            continue
        out.append(g)
    return out


class CullingPage(Page):
    page_id = "culling"
    index = "05"
    title = _t("挑片")
    en = "Culling // rate · compare · bursts"
    sub = (_t("双击或按空格看大图（Z 放大到 100%，C 两张并排对比）。1–5 打星、0 清除，P 留用、X 弃用、U 取消，6–9 色标；"
           "可多选后一起打。连拍 / 相似照片自动分组，并标出最清晰的一张。"))

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        p = theme.current()
        self.shown: list = []
        self.sel: list[int] = []
        top = QHBoxLayout()
        top.setSpacing(12)

        # ---- 相似照片 ----
        sp = Panel(_t("连拍 / 相似照片"), "Bursts // perceptual hash")
        sp.setFixedWidth(500)
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("相邻间隔 ≤")))
        self.gap = QSpinBox()
        self.gap.setRange(1, 600)
        self.gap.setSuffix(_t(" 秒"))
        self.gap.setValue(int(ctx.settings.get("similar_gap_seconds", 10)))
        r.addWidget(self.gap)
        r.addSpacing(8)
        r.addWidget(QLabel(_t("相似度")))
        self.thr = QSpinBox()
        self.thr.setRange(0, 32)
        self.thr.setValue(int(ctx.settings.get("similar_threshold", 12)))
        self.thr.setToolTip(_t("两张照片的“指纹”最多相差几位（0–32）。越小越严格：6 只认几乎一样的连拍，16 会把同一场景换了角度的也归到一组。"))
        r.addWidget(self.thr)
        r.addStretch(1)
        sp.body.addLayout(r)
        cr = QHBoxLayout()
        self.c_sets = StatCard(_t("相似组"), "SETS")
        self.c_in = StatCard(_t("组内照片"), "PHOTOS", accent=p.ink2)
        self.c_extra = StatCard(_t("建议待确认"), "EXTRAS", accent=p.warn)
        for c in (self.c_sets, self.c_in, self.c_extra):
            cr.addWidget(c)
        sp.body.addLayout(cr)
        ar = QHBoxLayout()
        ar.addWidget(QLabel(_t("文件夹名：")))
        self.folder = QLineEdit(_t(ctx.settings.get("similar_folder") or "待确认_相似"))
        self.folder.setFixedWidth(130)
        ar.addWidget(self.folder)
        ar.addStretch(1)
        self.b_isolate = TacButton(_t("非最佳移入文件夹…"), "primary", en="ISOLATE", height=34)
        self.b_isolate.clicked.connect(self.isolate)
        ar.addWidget(self.b_isolate)
        sp.body.addLayout(ar)
        sp.body.addWidget(caption(_t("拍摄时间相近、画面指纹相似的照片归为一组，清晰度最高的标“最佳”。"
                                  "移出时跳过每组最佳、已打星和标记“留用”的照片。"), "faint", 8))
        top.addWidget(sp)

        # ---- 标记与批量操作 ----
        mp = Panel(_t("标记与批量操作"), "Marks // batch")
        mr = QHBoxLayout()
        self.c_rated = StatCard(_t("已打星"), "RATED", accent=p.accent)
        self.c_pick = StatCard(_t("留用"), "PICK", accent=p.ok)
        self.c_reject = StatCard(_t("弃用"), "REJECT", accent=p.danger)
        self.c_none = StatCard(_t("未标记"), "UNMARKED", accent=p.ink3)
        for c in (self.c_rated, self.c_pick, self.c_reject, self.c_none):
            mr.addWidget(c)
        mp.body.addLayout(mr)
        self.scope_lab = caption("", "dim", 8.5)
        mp.body.addWidget(self.scope_lab)
        br = QHBoxLayout()
        self.b_view = TacButton(_t("大图查看"), "secondary", en="VIEW", height=32)
        self.b_edit = TacButton(_t("修图…"), "secondary", en="EDIT", height=32)
        self.b_edit.setToolTip(_t("用图层、调整图层、修复画笔、文字等修这张照片（类似 Photoshop）。原片不动，保存为新文件。"))
        self.b_export = TacButton(_t("导出分享…"), "primary", en="EXPORT", height=32)
        self.b_xmp = TacButton(_t("写入 XMP…"), "secondary", en="XMP", height=32)
        self.b_rename = TacButton(_t("批量重命名…"), "secondary", en="RENAME", height=32)
        for b in (self.b_view, self.b_edit, self.b_export, self.b_xmp, self.b_rename):
            br.addWidget(b)
        br.addStretch(1)
        mp.body.addLayout(br)
        br = QHBoxLayout()
        self.b_clean = TacButton(_t("清理 RAW…"), "danger", en="CLEANUP", height=32)
        self.b_dupe = TacButton(_t("查找重复照片…"), "danger", en="DUPES", height=32)
        self.b_clear = TacButton(_t("清除标记"), "secondary", height=32)
        for b in (self.b_clean, self.b_dupe, self.b_clear):
            br.addWidget(b)
        br.addStretch(1)
        mp.body.addLayout(br)
        mp.body.addWidget(caption(_t("操作范围：有选中时只处理选中的照片，否则处理下方当前列表。星级只保存在本软件里，"
                                  "写入 XMP 后 Lightroom / Bridge 才能看到（JPG 不写）。"), "faint", 8))
        top.addWidget(mp, 1)
        self.root.addLayout(top)

        # ---- 照片 ----
        gp = Panel(_t("照片"), "Gallery", "")
        self.gpanel = gp
        fr = QHBoxLayout()
        self.view_btns: dict[str, QRadioButton] = {}
        bg = QButtonGroup(self)
        for k, text in VIEWS:
            b = QRadioButton(text)
            bg.addButton(b)
            fr.addWidget(b)
            self.view_btns[k] = b
        self.view_btns["all"].setChecked(True)
        for b in self.view_btns.values():
            b.toggled.connect(lambda on: on and self.refresh())
        fr.addStretch(1)
        gp.body.addLayout(fr)
        self.grid = ThumbGrid(selectable=True)
        self.grid.delegate.show_score = True
        gp.body.addWidget(self.grid, 1)
        self.root.addWidget(gp, 1)

        self._regroup = QTimer(self)
        self._regroup.setSingleShot(True)
        self._regroup.setInterval(300)
        self._regroup.timeout.connect(self._apply_similar)
        self.gap.valueChanged.connect(lambda _: self._regroup.start())
        self.thr.valueChanged.connect(lambda _: self._regroup.start())
        self.grid.selection_changed.connect(self._sel)
        self.b_view.clicked.connect(self.view)
        self.b_edit.clicked.connect(self.edit)
        self.b_xmp.clicked.connect(self.write_xmp)
        self.b_clean.clicked.connect(self.cleanup)
        self.b_rename.clicked.connect(self.rename)
        self.b_clear.clicked.connect(self.clear_marks)
        self.b_export.clicked.connect(self.export)
        self.b_dupe.clicked.connect(self.dedupe)
        ctx.library_changed.connect(self.refresh)
        ctx.files_changed.connect(self.refresh)
        ctx.ratings_changed.connect(self._ratings_changed)

    def on_show(self):
        self.refresh()

    # ---- 数据 ----
    def view_mode(self) -> str:
        return next((k for k, b in self.view_btns.items() if b.isChecked()), "all")

    def _all(self) -> list:
        return [g for g in self.ctx.library.groups if g.files]

    def refresh(self) -> None:
        gs = self._all()
        self._stats(gs)
        mode = self.view_mode()
        key = (lambda g: (g.date is None, g.date or 0, g.name.lower()))
        if mode == "similar":
            show = [g for g in gs if g.feats.get("similar")]
            show.sort(key=lambda g: (g.feats["similar"]["cid"], g.feats["similar"]["rank"]))
        else:
            flt = {"all": lambda g: True, "unrated": lambda g: not is_marked(g),
                   "rated": lambda g: int(_rt(g).get("rating") or 0) > 0,
                   "pick": lambda g: _rt(g).get("flag") == "pick",
                   "reject": lambda g: _rt(g).get("flag") == "reject"}[mode]
            show = sorted((g for g in gs if flt(g)), key=key)
        keep = set(self.sel)
        self.shown = show
        self.grid.delegate.blur_threshold = float(self.ctx.settings.get("blur_threshold", 60))
        self.grid.settings_snapshot = self.ctx.settings.snapshot()
        self.grid.set_groups(show)
        if keep:
            self.grid.select_gids(keep)
        self.gpanel.set_meta(f"{len(show)} ITEMS · {dict(VIEWS)[mode]}")
        self._update_scope()

    def _stats(self, gs=None) -> None:
        gs = self._all() if gs is None else gs
        n = len(gs) or 1
        sims = [g for g in gs if g.feats.get("similar")]
        sets = len({g.feats["similar"]["cid"] for g in sims})
        extra = len(similar_extras(gs))
        self.c_sets.set(str(sets), unit=_t("组"))
        self.c_in.set(str(len(sims)), len(sims) / n, _t("张"))
        self.c_extra.set(str(extra), extra / n, _t("张"))
        rated = sum(1 for g in gs if int(_rt(g).get("rating") or 0) > 0)
        pick = sum(1 for g in gs if _rt(g).get("flag") == "pick")
        rej = sum(1 for g in gs if _rt(g).get("flag") == "reject")
        none = sum(1 for g in gs if not is_marked(g))
        self.c_rated.set(str(rated), rated / n, _t("张"))
        self.c_pick.set(str(pick), pick / n, _t("张"))
        self.c_reject.set(str(rej), rej / n, _t("张"))
        self.c_none.set(str(none), none / n, _t("张"))
        self.b_isolate.setEnabled(extra > 0)

    def _ratings_changed(self) -> None:
        self._stats()
        if self.view_mode() in ("unrated", "rated", "pick", "reject"):
            # 不立刻从列表里拿走刚打完分的照片（否则连续打分时位置会乱跳），切换视图或回到本页时再筛选
            self.gpanel.set_meta(_t('{0} ITEMS · {1} · 标记已变化，切换视图刷新', len(self.shown), dict(VIEWS)[self.view_mode()]))

    def _sel(self, gids: list[int]) -> None:
        self.sel = gids
        self._update_scope()

    def _update_scope(self) -> None:
        if self.sel:
            self.scope_lab.setText(_t('范围：选中的 {0} 张照片', len(self.sel)))
        else:
            self.scope_lab.setText(_t('范围：当前列表「{0}」{1} 张照片', dict(VIEWS)[self.view_mode()], len(self.shown)))

    def scope(self) -> tuple[list, str]:
        lib = self.ctx.library
        if self.sel:
            gs = [g for gid in self.sel if (g := lib.by_gid(gid)) is not None and g.files]
            return gs, _t('选中的 {0} 张照片', len(gs))
        return list(self.shown), _t('当前列表「{0}」{1} 张照片', dict(VIEWS)[self.view_mode()], len(self.shown))

    def _apply_similar(self) -> None:
        self.ctx.set_setting("similar_gap_seconds", int(self.gap.value()))
        self.ctx.set_setting("similar_threshold", int(self.thr.value()))
        n = self.ctx.library.regroup_similar()
        self.ctx.status.emit(_t('相似照片重新分组：{0} 组', n))
        self.refresh()

    # ---- 操作 ----
    def view(self) -> None:
        if not self.shown:
            return
        idx = 0
        if self.sel:
            first = self.sel[0]
            idx = next((i for i, g in enumerate(self.shown) if g.gid == first), 0)
        self.ctx.open_viewer(self.shown, idx, self.window())

    def edit(self) -> None:
        """修图：选中的第一张，没有选中时用列表里的第一张。"""
        lib = self.ctx.library
        g = lib.by_gid(self.sel[0]) if self.sel else next((x for x in self.shown if not x.is_video), None)
        if g is None:
            info(self, _t("没有可以修的照片"), _t("先在下方选中一张照片。"))
            return
        self.ctx.open_editor(g, self.window())

    def clear_marks(self) -> None:
        gs, text = self.scope()
        marked = [g.gid for g in gs if _rt(g).get("manual")]
        if not marked:
            info(self, _t("没有可清除的标记"), _t('{0}里没有在本软件里打过的星级 / 色标 / 留弃标记。', text))
            return
        from ..dialogs import ask
        if ask(self, _t("清除标记"), _t('清除{0}中 {1} 张照片在本软件里打的星级、色标和留弃标记？<br>（照片 / .xmp 里原有的评级不受影响；已写入 .xmp 的需要在「操作记录」页撤销那次写入。）', text, len(marked)),
               ok=_t("清除"), danger=True):
            self.ctx.rate(marked, None)
            self.refresh()

    def write_xmp(self) -> None:
        from ..xmpflow import run_xmp_flow
        gs, text = self.scope()
        run_xmp_flow(self.ctx, self, gs, text, gps=False, place=False, time=False, rating=True,
                     update_existing=bool(self.ctx.settings.get("xmp_update_existing", True)),
                     keywords=bool(self.ctx.settings.get("xmp_write_keywords", True)))

    def isolate(self) -> None:
        groups = similar_extras(self._all())
        if not groups:
            return
        start = self.ctx.settings.get("target_dir") or groups[0].folder
        d = QFileDialog.getExistingDirectory(self, _t("「待确认」文件夹放在哪里？"), start)
        if not d:
            return
        name = self.folder.text().strip() or _t("待确认_相似")
        self.ctx.set_setting("similar_folder", name)
        spec = PartitionSpec("rules", [Rule("fixed", {"name": name})])
        plan = build_plan(groups, spec, PartitionContext(self.ctx.settings.snapshot()), os.path.normpath(d),
                          "move", "rename", "none")
        if PlanDialog(self, plan).exec() != PlanDialog.DialogCode.Accepted:
            return
        page = self.window().page("partition") if hasattr(self.window(), "page") else None
        if page is not None:
            page.execute(plan)

    def cleanup(self) -> None:
        from ..culling_dialogs import CleanupDialog
        gs, text = self.scope()
        if not gs:
            return
        mode = "rejected" if self.view_mode() == "reject" else "keep_jpg"
        dlg = CleanupDialog(self, gs, text, mode)
        if dlg.exec() != CleanupDialog.DialogCode.Accepted or not dlg.plan or not dlg.plan.items:
            return
        plan = dlg.plan
        self.ctx.busy = True

        def job(prog, cancel):
            return self.ctx.mover.delete(plan, lambda d, t, c: prog((d, t, c)), cancel)

        try:
            res, tb = run_with_progress(self.ctx, self, _t("正在放入回收站"), "TRASHING", job)
        finally:
            self.ctx.busy = False
        if tb or res is None:
            self.ctx.files_changed.emit()
            info(self, _t("清理出错"), f"<pre style='font-size:8pt'>{(tb or '')[-1500:]}</pre>", "ERROR")
            return
        from ...ops.pathsafe import human_size
        self.ctx.library.remove_files(res.deleted)
        self.sel = []
        self.ctx.files_changed.emit()
        msg = [_t('已把 <b>{0}</b> 个文件（{1}）放入回收站，涉及 {2} 张照片。', len(res.deleted), human_size(res.freed), res.done_groups)]
        if res.cancelled:
            msg.append(_t("已取消，剩余照片未处理。"))
        if res.failed_groups:
            msg.append(_t('{0} 张失败（已放回原处）：<br>', res.failed_groups) + "<br>".join(res.errors[:8]))
        msg.append(_t('批次 #{0}，可在「操作记录」页撤销（从回收站放回）。', res.batch_id))
        self.ctx.status.emit(_t('清理完成：{0} 个文件放入回收站，释放 {1}', len(res.deleted), human_size(res.freed)))
        info(self, _t("清理完成"), "<br><br>".join(msg), "COMPLETE")

    def export(self) -> None:
        from ..workflow_dialogs import ExportDialog, run_export
        gs, text = self.scope()
        if not gs:
            return
        dlg = ExportDialog(self, self.ctx, gs, text)
        if dlg.exec() == ExportDialog.DialogCode.Accepted:
            run_export(self.ctx, self, dlg)

    def dedupe(self) -> None:
        from ..workflow_dialogs import DedupeDialog
        gs = self._all()
        if not gs:
            return
        dlg = DedupeDialog(self, self.ctx, gs)
        if dlg.exec() != DedupeDialog.DialogCode.Accepted or not dlg.plan or not dlg.plan.items:
            return
        plan = dlg.plan
        self.ctx.busy = True

        def job(prog, cancel):
            return self.ctx.mover.delete(plan, lambda d, t, c: prog((d, t, c)), cancel)

        try:
            res, tb = run_with_progress(self.ctx, self, _t("正在删除副本"), "TRASHING", job)
        finally:
            self.ctx.busy = False
        if tb or res is None:
            self.ctx.files_changed.emit()
            info(self, _t("删除出错"), f"<pre style='font-size:8pt'>{(tb or '')[-1500:]}</pre>", "ERROR")
            return
        from ...ops.pathsafe import human_size
        self.ctx.library.remove_files(res.deleted)
        self.sel = []
        self.ctx.files_changed.emit()
        msg = (_t('已把 <b>{0}</b> 张重复照片的副本（{1} 个文件，{2}）放入回收站。', res.done_groups, len(res.deleted), human_size(res.freed)))
        if res.failed_groups:
            msg += _t('<br><br>{0} 张失败（已放回）：<br>', res.failed_groups) + "<br>".join(res.errors[:6])
        msg += _t('<br><br>批次 #{0}，可在「操作记录」页撤销（从回收站放回）。', res.batch_id)
        self.ctx.status.emit(_t('删除重复：{0} 个文件，释放 {1}', len(res.deleted), human_size(res.freed)))
        info(self, _t("完成"), msg, "COMPLETE")

    def rename(self) -> None:
        from ..culling_dialogs import RenameDialog
        gs, text = self.scope()
        if not gs:
            return
        dlg = RenameDialog(self, self.ctx, gs, text)
        if dlg.exec() != RenameDialog.DialogCode.Accepted or not dlg.plan:
            return
        plan = dlg.plan
        self.ctx.busy = True

        def job(prog, cancel):
            return self.ctx.mover.execute(plan, lambda d, t, c: prog((d, t, c)), cancel, {"spec": plan.spec_text})

        try:
            res, tb = run_with_progress(self.ctx, self, _t("正在重命名"), "RENAMING", job)
        finally:
            self.ctx.busy = False
        if tb or res is None:
            self.ctx.files_changed.emit()
            info(self, _t("改名出错"), f"<pre style='font-size:8pt'>{(tb or '')[-1500:]}</pre>", "ERROR")
            return
        self.ctx.library.apply_moves(res.mapping)
        self.ctx.files_changed.emit()
        msg = [_t('已重命名 <b>{0}</b> 张照片（{1} 个文件）。', res.done_groups, res.done_files)]
        if res.cancelled:
            msg.append(_t("已取消，剩余照片未处理。"))
        if res.failed_groups:
            msg.append(_t('{0} 张失败（已改回原名）：<br>', res.failed_groups) + "<br>".join(res.errors[:8]))
        msg.append(_t('批次 #{0}，可在「操作记录」页撤销（改回原名）。', res.batch_id))
        self.ctx.status.emit(_t('重命名完成：{0} 个文件（批次 #{1}）', res.done_files, res.batch_id))
        info(self, _t("改名完成"), "<br><br>".join(msg), "COMPLETE")
