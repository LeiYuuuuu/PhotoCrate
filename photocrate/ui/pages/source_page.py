"""01 资源接入：选择文件夹 → 扫描建库 → 浏览与挑选照片。"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QSlider, QScrollArea, QWidget, QLayout)

from ...core.library import Progress
from .. import theme
from ..dialogs import info
from ..theme import font
from ..widgets.deco import Panel, SegProgress, StatCard, TacButton, caption, micro_label
from ..widgets.thumbgrid import ThumbGrid
from .base import Page

FILTERS = [
    ("all", "全部照片"), ("raw", "含 RAW"), ("jpeg", "JPG"), ("heif", "HEIC"), ("png", "PNG / 截图"),
    ("guess", "日期为推测"), ("nodate", "无日期"), ("nogps", "无 GPS"), ("gps", "有 GPS"), ("noplace", "无地点"),
    ("cross", "跨文件夹配对"), ("screenshot", "截图"),
]
SORTS = [("date_asc", "拍摄时间 ↑"), ("date_desc", "拍摄时间 ↓"), ("name", "文件名"), ("folder", "所在文件夹")]


class SourcePage(Page):
    page_id = "source"
    index = "01"
    title = "资源接入"
    en = "Source input // scan & index"
    sub = "添加要整理的文件夹并扫描。扫描结果会缓存，第二次扫描同一批照片几乎是瞬间完成。"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.worker = None
        self._t0 = 0.0

        top = QHBoxLayout()
        top.setSpacing(12)
        # ---- 数据源 ----
        src = Panel("数据源", "Source folders")
        src.setFixedWidth(400)
        self.dirs = QListWidget()
        self.dirs.setFixedHeight(96)
        self.dirs.setFont(font("mono", 8.5))
        for d in ctx.settings.get("source_dirs") or []:
            self.dirs.addItem(d)
        src.body.addWidget(self.dirs)
        row = QHBoxLayout()
        b_add = TacButton("添加文件夹", "secondary", height=30)
        b_rm = TacButton("移除", "secondary", height=30)
        b_add.clicked.connect(self._add_dir)
        b_rm.clicked.connect(self._rm_dir)
        row.addWidget(b_add)
        row.addWidget(b_rm)
        row.addStretch(1)
        self.recursive = QCheckBox("包含子文件夹")
        self.recursive.setChecked(bool(ctx.settings.get("recursive", True)))
        row.addWidget(self.recursive)
        src.body.addLayout(row)
        row2 = QHBoxLayout()
        self.b_scan = TacButton("开始扫描", "primary", en="SCAN", height=38)
        self.b_cancel = TacButton("取消", "secondary", height=38)
        self.b_cancel.setEnabled(False)
        self.b_scan.clicked.connect(self.start_scan)
        self.b_cancel.clicked.connect(self.cancel_scan)
        row2.addWidget(self.b_scan, 1)
        self.b_import = TacButton("导入存储卡…", "secondary", en="IMPORT", height=38)
        self.b_import.clicked.connect(self.import_media)
        row2.addWidget(self.b_import)
        row2.addWidget(self.b_cancel)
        src.body.addLayout(row2)
        self.tool_note = caption("", "warn", 8.5)
        src.body.addWidget(self.tool_note)
        src.body.addStretch(1)
        top.addWidget(src)

        # ---- 扫描状态 ----
        st = Panel("扫描状态", "Index status", "IDLE")
        self.status_panel = st
        self.bar = SegProgress()
        st.body.addWidget(self.bar)
        self.stage = QLabel("等待扫描")
        self.stage.setFont(font("cn", 10, 600))
        self.counts = QLabel("META  —      PIXEL  —")
        self.counts.setFont(font("mono", 8.5))
        self.counts.setProperty("role", "dim")
        self.current = QLabel("")
        self.current.setFont(font("mono", 8))
        self.current.setProperty("role", "faint")
        r = QHBoxLayout()
        r.addWidget(self.stage)
        r.addStretch(1)
        r.addWidget(self.counts)
        st.body.addLayout(r)
        st.body.addWidget(self.current)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.c_total = StatCard("照片", "Groups")
        self.c_raw = StatCard("含 RAW", "Raw")
        self.c_gps = StatCard("有 GPS", "Geotag", accent=theme.current().accent2)
        self.c_date = StatCard("EXIF 拍摄时间", "Exif date")
        self.c_guess = StatCard("日期推测 / 未知", "Inferred", accent=theme.current().warn)
        self.c_shot = StatCard("截图", "Screenshot")
        for i, c in enumerate((self.c_total, self.c_raw, self.c_gps, self.c_date, self.c_guess, self.c_shot)):
            grid.addWidget(c, i // 3, i % 3)
        st.body.addLayout(grid)
        top.addWidget(st, 1)
        # 小窗口让接入面板滚动，保持两排统计卡片的真实高度。
        top_widget = QWidget()
        top_widget.setLayout(top)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.source_overview = QScrollArea()
        self.source_overview.setWidgetResizable(True)
        self.source_overview.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.source_overview.setWidget(top_widget)
        self.source_overview.setMinimumHeight(240)
        self.source_overview.setMaximumHeight(top_widget.minimumSizeHint().height() + 2)
        self.root.addWidget(self.source_overview)


        # ---- 浏览 ----
        br = Panel("照片浏览与挑选", "Browser // select", "")
        self.browser = br
        tb = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("按文件名 / 地点 / 相机搜索…")
        self.search.setClearButtonEnabled(True)
        self.filter = QComboBox()
        for k, v in FILTERS:
            self.filter.addItem(v, k)
        self.sort = QComboBox()
        for k, v in SORTS:
            self.sort.addItem(v, k)
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size.setRange(120, 280)
        self.size.setValue(168)
        self.size.setFixedWidth(110)
        b_all = TacButton("全选", "secondary", height=30)
        b_none = TacButton("清除选择", "secondary", height=30)
        b_all.clicked.connect(lambda: self.grid.selectAll())
        b_none.clicked.connect(self._clear_selection)
        tb.addWidget(self.search, 2)
        tb.addWidget(self.filter)
        tb.addWidget(self.sort)
        tb.addWidget(micro_label("size"))
        tb.addWidget(self.size)
        tb.addStretch(1)
        tb.addWidget(b_all)
        tb.addWidget(b_none)
        br.body.addLayout(tb)
        self.sel_label = caption("扫描后可在这里挑选照片：不选 = 处理全部；选中部分 = 可在「分区」页只处理选中的。", "dim", 9)
        br.body.addWidget(self.sel_label)
        self.grid = ThumbGrid()
        br.body.addWidget(self.grid, 1)
        self.root.addWidget(br, 1)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(180)
        self._filter_timer.timeout.connect(self.refresh_grid)
        self.search.textChanged.connect(lambda: self._filter_timer.start())
        self.filter.currentIndexChanged.connect(self.refresh_grid)
        self.sort.currentIndexChanged.connect(self.refresh_grid)
        self.size.valueChanged.connect(self.grid.set_cell_width)
        self.grid.selection_changed.connect(self._on_select)
        ctx.library_changed.connect(self._on_library)
        ctx.files_changed.connect(self.refresh_grid)
        ctx.settings_changed.connect(lambda k: self.grid.viewport().update())
        self._update_tool_note()

    # ---- 文件夹 ----------------------------------------------------------
    def set_dirs(self, dirs: list[str]) -> None:
        """由其他页面更新数据源（例如移动到新的目标文件夹之后）。"""
        self.dirs.clear()
        for d in dirs:
            self.dirs.addItem(d)
        self.ctx.settings.set("source_dirs", self._dirs())

    def _dirs(self) -> list[str]:
        return [self.dirs.item(i).text() for i in range(self.dirs.count())]

    def _add_dir(self):
        start = self._dirs()[-1] if self._dirs() else os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, "选择照片文件夹", start)
        if d:
            d = os.path.normpath(d)
            if d not in self._dirs():
                self.dirs.addItem(d)
            self.ctx.settings.set("source_dirs", self._dirs())

    def import_media(self):
        """导入向导：存储卡 / 手机 / 文件夹 → 复制到电脑（跳过已导入的）→ 可选加入数据源并扫描。"""
        from ...ops.importer import ImportHistory, history_path, record_import
        from ..dialogs import info, run_with_progress
        from ..workflow_dialogs import ImportDialog
        if self.ctx.busy:
            return
        dlg = ImportDialog(self, self.ctx)
        if dlg.exec() != ImportDialog.DialogCode.Accepted or not dlg.plan or not dlg.plan.items:
            return
        plan, groups = dlg.plan, dlg.check.new
        self.ctx.busy = True

        def job(prog, cancel):
            return self.ctx.mover.execute(plan, lambda d, t, c: prog((d, t, c)), cancel,
                                          {"spec": plan.spec_text, "kind": "import"})
        try:
            res, tb = run_with_progress(self.ctx, self, "正在导入", "IMPORTING", job)
        finally:
            self.ctx.busy = False
        if tb or res is None:
            info(self, "导入出错", f"<pre style='font-size:8pt'>{(tb or '')[-1500:]}</pre>", "ERROR")
            return
        hist = ImportHistory(history_path())
        try:
            record_import(hist, plan, res, groups)
        finally:
            hist.close()
        self.ctx.files_changed.emit()
        dst = dlg.dst.text().strip()
        msg = f"已导入 {res.done_groups} 张照片 / 视频（{res.done_files} 个文件）到<br>{dst}"
        if res.failed_groups:
            msg += f"<br><br>{res.failed_groups} 张失败：<br>" + "<br>".join(res.errors[:6])
        if res.cancelled:
            msg += "<br><br>已取消，剩余文件未导入（下次导入会接着来）。"
        msg += f"<br><br>批次 #{res.batch_id}，可在「操作记录」页撤销。"
        self.ctx.status.emit(f"导入完成：{res.done_files} 个文件")
        info(self, "导入完成", msg, "COMPLETE")
        if dlg.cb_add.isChecked() and res.done_files:
            norm = os.path.normcase(os.path.normpath(dst))
            covered = any(norm == os.path.normcase(d) or norm.startswith(os.path.normcase(d) + os.sep)
                          for d in self._dirs())
            if not covered:
                self.dirs.addItem(os.path.normpath(dst))
                self.ctx.settings.set("source_dirs", self._dirs())
            QTimer.singleShot(0, self.start_scan)

    def _rm_dir(self):
        for it in self.dirs.selectedItems():
            self.dirs.takeItem(self.dirs.row(it))
        self.ctx.settings.set("source_dirs", self._dirs())

    def _update_tool_note(self):
        lib = self.ctx.library
        if lib.exiftool_path:
            self.tool_note.setText("")
            self.tool_note.hide()
        else:
            self.tool_note.setText("▲ 未检测到 ExifTool：将使用内置读取，部分 RAW（如 CR3、RAF）的拍摄时间可能读不到。"
                                   "可运行 tools\\get_exiftool.ps1 自动下载，或在「设置」中指定。")
            self.tool_note.show()

    # ---- 扫描 ------------------------------------------------------------
    def start_scan(self):
        dirs = [d for d in self._dirs() if os.path.isdir(d)]
        if not dirs:
            info(self, "还没有文件夹", "请先点击「添加文件夹」选择要整理的照片所在位置。")
            return
        self.ctx.settings.set("recursive", self.recursive.isChecked())
        self.ctx.settings.set("source_dirs", self._dirs())
        if not self.ctx.settings.get("target_dir"):
            self.ctx.settings.set("target_dir", dirs[0])
        self.b_scan.setEnabled(False)
        self.b_cancel.setEnabled(True)
        self.bar.busy = True
        self.bar.set_value(0)
        self.status_panel.set_meta("SCANNING")
        self._t0 = time.time()
        lib = self.ctx.library
        rec = self.recursive.isChecked()
        self.ctx.busy = True
        self.worker = self.ctx.run(lambda prog, cancel: lib.scan(dirs, rec, prog, cancel),
                                   self._on_progress, self._on_done, self._on_error)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self.bar.tick)
        self._tick.start(60)

    def cancel_scan(self):
        if self.worker:
            self.worker.cancel()
            self.stage.setText("正在取消…")

    def _on_progress(self, pg: Progress):
        names = {"enumerate": "枚举文件", "meta": "读取元数据", "pixel": "分析画面", "analyze": "整理日期与地点",
                 "done": "完成"}
        stage = pg.message if pg.stage in ("analyze", "ai") and pg.message else names.get(pg.stage, pg.stage)
        if pg.stage == "ai" and pg.ai_total:
            stage += f" {pg.ai_done}/{pg.ai_total}"
        self.stage.setText(f"{stage} · 已发现 {pg.groups} 组照片")
        self.counts.setText(f"META  {pg.meta_done}/{pg.meta_total}      PIXEL  {pg.pix_done}/{pg.pix_total}")
        self.current.setText(pg.current[-90:] if pg.current else "")
        self.bar.set_value(pg.fraction)

    def _finish_ui(self):
        self.b_scan.setEnabled(True)
        self.b_cancel.setEnabled(False)
        self.bar.busy = False
        if hasattr(self, "_tick"):
            self._tick.stop()
        self.ctx.busy = False

    def _on_done(self, rep):
        self._finish_ui()
        if rep.cancelled:
            self.stage.setText("已取消")
            self.status_panel.set_meta("CANCELLED")
            return
        self.bar.set_value(1)
        self.status_panel.set_meta(f"DONE · {rep.seconds:.1f}s")
        paired = f"（{rep.paired_across} 组 RAW+JPG 跨文件夹配对）" if rep.paired_across else ""
        self.stage.setText(f"扫描完成：{rep.groups} 组照片 / {rep.files} 个文件{paired}")
        self.counts.setText(f"缓存命中 {rep.meta_cached + rep.pix_cached}   新分析 {rep.meta_read + rep.pix_computed}"
                            + (f"   失败 {len(rep.errors)}" if rep.errors else ""))
        self.current.setText("")
        self.ctx.library_changed.emit()
        self.ctx.status.emit(f"扫描完成：{rep.groups} 组照片，用时 {rep.seconds:.1f} 秒")
        if rep.errors:
            more = "<br>".join(rep.errors[:12]) + ("<br>…" if len(rep.errors) > 12 else "")
            info(self, "部分文件无法预览", f"{len(rep.errors)} 个文件无法生成预览（不影响按日期/地区分区）：<br>"
                                         f"<span style='font-size:8pt'>{more}</span>")

    def _on_error(self, tb: str):
        self._finish_ui()
        self.stage.setText("扫描出错")
        info(self, "扫描出错", f"<pre style='font-size:8pt'>{tb[-1500:]}</pre>", "ERROR")

    # ---- 浏览 ------------------------------------------------------------
    def _on_library(self):
        lib = self.ctx.library
        gs = [g for g in lib.groups if g.files]
        n = len(gs) or 1
        self.c_total.set(f"{len(gs)}", None, "组")
        self.c_raw.set(f"{sum(1 for g in gs if g.has_raw)}", sum(1 for g in gs if g.has_raw) / n)
        gps = sum(1 for g in gs if g.feat("geo", "has_gps") and not g.feat("geo", "inferred"))
        self.c_gps.set(f"{gps}", gps / n)
        ex = sum(1 for g in gs if g.date_source in ("exif", "exif_create", "xmp"))
        self.c_date.set(f"{ex}", ex / n)
        gu = sum(1 for g in gs if g.date_is_guess or g.date is None)
        self.c_guess.set(f"{gu}", gu / n)
        sh = sum(1 for g in gs if g.feat("camera", "source") == "screenshot")
        self.c_shot.set(f"{sh}", sh / n)
        self._update_tool_note()
        self.refresh_grid()

    def _filtered(self):
        lib = self.ctx.library
        q = self.search.text().strip().lower()
        f = self.filter.currentData()
        out = []
        for g in lib.groups:
            if not g.files:
                continue
            if f == "raw" and not g.has_raw:
                continue
            if f in ("jpeg", "heif") and g.primary_file.role != f:
                continue
            if f == "png" and g.primary_file.role != "png":
                continue
            if f == "guess" and not g.date_is_guess:
                continue
            if f == "nodate" and g.date is not None:
                continue
            if f == "nogps" and g.feat("geo", "has_gps"):
                continue
            if f == "gps" and not g.feat("geo", "has_gps"):
                continue
            if f == "noplace" and g.feat("geo", "country"):
                continue
            if f == "cross" and not g.cross_folder:
                continue
            if f == "screenshot" and g.feat("camera", "source") != "screenshot":
                continue
            if q:
                geo = g.feats.get("geo") or {}
                hay = " ".join(str(x) for x in (g.name, g.folder, geo.get("country"), geo.get("province"), geo.get("city"),
                                                geo.get("district"), g.feat("camera", "model"), g.feat("camera", "lens")) if x)
                if q not in hay.lower():
                    continue
            out.append(g)
        s = self.sort.currentData()
        if s in ("date_asc", "date_desc"):
            from datetime import datetime
            out.sort(key=lambda g: (g.date or datetime.max, g.name), reverse=(s == "date_desc"))
        elif s == "name":
            out.sort(key=lambda g: g.name.lower())
        else:
            out.sort(key=lambda g: (g.folder.lower(), g.name.lower()))
        return out

    def refresh_grid(self):
        gs = self._filtered()
        sel = set(self.ctx.library.selected)
        self.grid.settings_snapshot = self.ctx.settings.snapshot()
        self._suppress = True
        self.grid.set_groups(gs)
        self._suppress = False
        if sel:
            self.grid.select_gids(sel)
        total = sum(1 for g in self.ctx.library.groups if g.files)
        self.browser.set_meta(f"SHOWING {len(gs)} / {total}")
        self._update_sel_label()

    def _clear_selection(self):
        self.ctx.library.selected = set()
        self.grid.clearSelection()
        self._update_sel_label()
        self.ctx.selection_changed.emit()

    def _on_select(self, gids):
        if getattr(self, "_suppress", False):
            return
        visible = {g.gid for g in self.grid.model_.groups}
        self.ctx.library.selected = (self.ctx.library.selected - visible) | set(gids)
        self._update_sel_label()
        self.ctx.selection_changed.emit()

    def _update_sel_label(self):
        n = len(self.ctx.library.selected)
        total = sum(1 for g in self.ctx.library.groups if g.files)
        if not total:
            return
        if n:
            self.sel_label.setText(f"已选 {n} / 共 {total} 组 —— 在「分区」页可选择“仅处理已选”。双击打开图片，右键可在文件夹中显示。")
        else:
            self.sel_label.setText(f"共 {total} 组照片。未选择 = 分区时处理全部；按住 Ctrl / Shift 可多选。双击打开图片。")
