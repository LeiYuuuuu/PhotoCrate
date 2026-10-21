"""02 时间与地点：按天检查拍摄时间与地点 —— 手动 / 按行程指定地点、用轨迹或手机照片补 GPS、校正时间与时区，
可选把结果写回 XMP 附属文件。所有修改默认只保存在软件里（annotations.db），不改照片文件。"""
from __future__ import annotations

import os
from collections import Counter

from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QModelIndex, QStringListModel, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFileDialog, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QScrollArea, QSizePolicy,
                               QSpinBox, QSplitter, QTableView, QVBoxLayout, QWidget)

from ...core.daybook import DayRow, build_days
from ...core.geotime import MATCH_KIND_LABELS, format_offset, parse_offset, shift_label, timezone_choices
from ...ops.journal import OPERATION_LABELS
from .. import theme
from ..dialogs import info, run_with_progress
from ..theme import font
from ..timeplace_dialogs import PLACE_SCOPES, ItineraryDialog, TimeShiftDialog, XmpDialog, scope_flags, would_apply
from ..widgets.deco import Panel, StatCard, TacButton, caption
from ..widgets.thumbgrid import ThumbGrid
from .base import Page

DayRole = Qt.ItemDataRole.UserRole + 11


class DayModel(QAbstractTableModel):
    HEAD = ["日期", "照片", "时间段", "相机", "地点", "地点来源", "时区 / 校正"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[DayRow] = []
        self._mono = font("mono", 8.5)
        self._num = font("display", 10, 600)

    def set_rows(self, rows: list[DayRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEAD)

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and o == Qt.Orientation.Horizontal:
            return self.HEAD[s]
        return None

    @staticmethod
    def _cams(r: DayRow) -> str:
        if not r.cameras:
            return "—"
        top = r.cameras.most_common()
        return top[0][0] + (f" 等 {len(top)} 台" if len(top) > 1 else "")

    @staticmethod
    def _zone(r: DayRow) -> str:
        parts = []
        if r.tzs:
            tz = r.tzs.most_common()
            parts.append(tz[0][0] + (f" 等 {len(tz)} 种" if len(tz) > 1 else ""))
        if r.shifts:
            sh = r.shifts.most_common()
            parts.append("校正 " + "/".join(shift_label(s) or "时区" for s, _ in sh[:2]))
        return "  ".join(parts) or "—"

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        r = self.rows[idx.row()]
        c = idx.column()
        if role == DayRole:
            return r
        if role == Qt.ItemDataRole.DisplayRole:
            if c == 0:
                return r.label
            if c == 1:
                return str(r.count)
            if c == 2:
                return f"{r.first:%H:%M} – {r.last:%H:%M}" if r.first else "—"
            if c == 3:
                return self._cams(r)
            if c == 4:
                return r.main_place or "（未知）"
            if c == 5:
                return r.source_text
            return self._zone(r)
        if role == Qt.ItemDataRole.ToolTipRole:
            if c == 4 and r.places:
                return "<br>".join(f"{k}：{v} 张" for k, v in r.places.most_common(12))
            if c == 3 and r.cameras:
                return "<br>".join(f"{k}：{v} 张" for k, v in r.cameras.most_common())
            return None
        if role == Qt.ItemDataRole.FontRole:
            if c in (0, 2, 6):
                return self._mono
            if c == 1:
                return self._num
        if role == Qt.ItemDataRole.TextAlignmentRole and c == 1:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            p = theme.current()
            if c == 4 and not r.places:
                return QColor(p.warn)
            if c == 5 and r.unknown:
                return QColor(p.warn)
            if c == 6 and r.shifts:
                return QColor(p.accent2)
        return None


class TimePlacePage(Page):
    page_id = "timeplace"
    index = "02"
    title = "时间与地点"
    en = "Time & place // dates, zones, locations"
    sub = "按天检查拍摄时间与地点：补全 GPS、手动指定地点、校正时间。修改只保存在软件里，照片文件不变（可选写入 XMP）。"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        p = theme.current()
        self._days: list[DayRow] = []
        self._is_busy = False
        self._place_index = None
        self._loading_index = False

        # ---- 概览卡片 ----
        cards = QHBoxLayout()
        cards.setSpacing(8)
        self.c_total = StatCard("照片", "Photos")
        self.c_loc = StatCard("已知地点", "Located", accent=p.ok)
        self.c_track = StatCard("轨迹 / 参考匹配", "Track match", accent=p.accent2)
        self.c_manual = StatCard("手动指定", "Manual", accent=p.ink2)
        self.c_none = StatCard("没有地点", "Unknown", accent=p.warn)
        self.c_fix = StatCard("时间已校正", "Time shifted", accent=p.accent)
        for c in (self.c_total, self.c_loc, self.c_track, self.c_manual, self.c_none, self.c_fix):
            cards.addWidget(c)
        self.root.addLayout(cards)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ======== 左：按天列表 + 当天照片 ========
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setChildrenCollapsible(False)
        dp = Panel("按天查看", "Day ledger", "")
        self.day_panel = dp
        self.day_model = DayModel(self)
        self.days = QTableView()
        self.days.setModel(self.day_model)
        self.days.verticalHeader().setVisible(False)
        self.days.verticalHeader().setDefaultSectionSize(24)
        self.days.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.days.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.days.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.days.setAlternatingRowColors(True)
        self.days.setWordWrap(False)
        h = self.days.horizontalHeader()
        for col, w in ((0, 118), (1, 44), (2, 96), (3, 112), (5, 112), (6, 128)):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            h.resizeSection(col, w)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.days.selectionModel().selectionChanged.connect(self._days_selected)
        dp.body.addWidget(self.days, 1)
        dr = QHBoxLayout()
        b_all = TacButton("全选日期", "secondary", height=28)
        b_unk = TacButton("选中有未知地点的天", "secondary", height=28)
        b_all.clicked.connect(self.days.selectAll)
        b_unk.clicked.connect(self._select_unknown_days)
        dr.addWidget(b_all)
        dr.addWidget(b_unk)
        dr.addStretch(1)
        dr.addWidget(caption("按住 Ctrl / Shift 多选；下方照片也可单独挑选。", "faint", 8.5))
        dp.body.addLayout(dr)
        vsplit.addWidget(dp)

        gp = Panel("所选日期的照片", "Photos of selected days", "")
        self.grid_panel = gp
        self.grid = ThumbGrid()
        self.grid.set_cell_width(132)
        self.grid.selection_changed.connect(lambda _: self._update_target())
        gp.body.addWidget(self.grid, 1)
        vsplit.addWidget(gp)
        vsplit.setSizes([330, 300])
        split.addWidget(vsplit)

        # ======== 右：操作 ========
        rw = QWidget()
        right = QVBoxLayout(rw)
        right.setContentsMargins(0, 0, 6, 0)
        right.setSpacing(10)

        tgt = Panel("操作对象", "Target")
        self.target_lab = QLabel("")
        self.target_lab.setFont(font("cn", 10, 700))
        self.target_lab.setWordWrap(True)
        tgt.body.addWidget(self.target_lab)
        right.addWidget(tgt)

        # ---- 指定地点 ----
        pp = Panel("指定地点", "Manual place")
        self.place_edit = QLineEdit()
        self.place_edit.setPlaceholderText("例如：日本/京都、湖南/邵阳、日本/箱根（输入时有候选）")
        self.place_edit.setClearButtonEnabled(True)
        pp.body.addWidget(self.place_edit)
        self.place_res = caption("", "dim", 9)
        pp.body.addWidget(self.place_res)
        self.place_scope = QComboBox()
        for k, v in PLACE_SCOPES:
            self.place_scope.addItem(v, k)
        self.place_scope.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.place_scope.setMinimumContentsLength(10)
        pp.body.addWidget(self.place_scope)
        self.place_count = caption("", "faint", 8.5)
        pp.body.addWidget(self.place_count)
        r = QHBoxLayout()
        self.b_place = TacButton("应用到操作对象", "primary", en="ASSIGN", height=32)
        self.b_place_clear = TacButton("清除手动地点", "secondary", height=32)
        r.addWidget(self.b_place, 1)
        r.addWidget(self.b_place_clear)
        pp.body.addLayout(r)
        self.b_itin = TacButton("按行程批量指定…", "secondary", height=30)
        pp.body.addWidget(self.b_itin)
        right.addWidget(pp)

        # ---- GPS 补全 ----
        fp = Panel("GPS 补全", "Fill from track / phone")
        self.cb_fill = QCheckBox("按拍摄时间匹配，给没有 GPS 的照片补上位置")
        self.cb_fill.setChecked(bool(ctx.settings.get("gps_fill", True)))
        fp.body.addWidget(self.cb_fill)
        self.cb_lib = QCheckBox("用本批照片中自带 GPS 的照片（如手机拍的）作参考")
        self.cb_lib.setChecked(bool(ctx.settings.get("gps_fill_library", True)))
        fp.body.addWidget(self.cb_lib)
        fp.body.addWidget(caption("参考照片文件夹（如手机照片；只读取，不会移动）：", "dim", 8.5))
        self.ref_dirs = QListWidget()
        self.ref_dirs.setFixedHeight(54)
        self.ref_dirs.setFont(font("mono", 8))
        for d in ctx.settings.get("gps_ref_dirs") or []:
            self.ref_dirs.addItem(d)
        fp.body.addWidget(self.ref_dirs)
        r = QHBoxLayout()
        b1 = TacButton("添加文件夹", "secondary", height=26)
        b2 = TacButton("移除", "secondary", height=26)
        b1.clicked.connect(self._add_ref_dir)
        b2.clicked.connect(lambda: self._remove_selected(self.ref_dirs, "gps_ref_dirs"))
        r.addWidget(b1)
        r.addWidget(b2)
        r.addStretch(1)
        fp.body.addLayout(r)
        fp.body.addWidget(caption("GPS 轨迹文件（GPX / KML）：", "dim", 8.5))
        self.tracks = QListWidget()
        self.tracks.setFixedHeight(54)
        self.tracks.setFont(font("mono", 8))
        for t in ctx.settings.get("gps_track_files") or []:
            self.tracks.addItem(t)
        fp.body.addWidget(self.tracks)
        r = QHBoxLayout()
        b3 = TacButton("添加轨迹", "secondary", height=26)
        b4 = TacButton("移除", "secondary", height=26)
        b3.clicked.connect(self._add_tracks)
        b4.clicked.connect(lambda: self._remove_selected(self.tracks, "gps_track_files"))
        r.addWidget(b3)
        r.addWidget(b4)
        r.addStretch(1)
        fp.body.addLayout(r)
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.addWidget(QLabel("时间相差不超过"), 0, 0)
        self.sp_window = QSpinBox()
        self.sp_window.setRange(1, 720)
        self.sp_window.setSuffix(" 分钟")
        self.sp_window.setValue(int(ctx.settings.get("gps_fill_minutes", 30)))
        g.addWidget(self.sp_window, 0, 1)
        g.addWidget(QLabel("照片没有时区时按"), 1, 0)
        self.cb_deftz = self._tz_combo("未知（只与同样没有时区的参考比较）")
        self._set_tz_combo(self.cb_deftz, ctx.settings.get("camera_tz_default") or None)
        g.addWidget(self.cb_deftz, 1, 1)
        g.setColumnStretch(1, 1)
        fp.body.addLayout(g)
        self.fill_status = caption("", "dim", 8.5)
        self.fill_status.setTextFormat(Qt.TextFormat.RichText)
        fp.body.addWidget(self.fill_status)
        self.b_fill = TacButton("读取参考并重新匹配", "primary", en="MATCH", height=32)
        fp.body.addWidget(self.b_fill)
        fp.body.addWidget(caption("带时区的照片按 UTC 比对，所以相机设的是北京时间、手机是当地时间也能对上；"
                                  "相机时钟本身不准时，先在下面“时间校正”里修正。", "faint", 8))
        right.addWidget(fp)

        # ---- 时间校正 ----
        tp = Panel("时间校正", "Time shift")
        r = QHBoxLayout()
        r.setSpacing(4)
        self.sh_dir = QComboBox()
        self.sh_dir.addItem("推后 ＋", 1)
        self.sh_dir.addItem("提前 －", -1)
        r.addWidget(self.sh_dir)
        self.sh = []
        for suffix, mx in ((" 天", 3650), (" 时", 23), (" 分", 59), (" 秒", 59)):
            sp = QSpinBox()
            sp.setRange(0, mx)
            sp.setSuffix(suffix)
            sp.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            sp.setMinimumWidth(56)
            self.sh.append(sp)
            r.addWidget(sp)
        tp.body.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel("实际时区"))
        self.sh_tz = self._tz_combo("保持照片原有时区")
        r.addWidget(self.sh_tz, 1)
        tp.body.addLayout(r)
        r = QHBoxLayout()
        r.setSpacing(4)
        r.addWidget(caption("相机时区设错了：", "dim", 8.5))
        self.cv_from = self._tz_combo(None)
        self.cv_to = self._tz_combo(None)
        self._set_tz_combo(self.cv_from, "+08:00")
        self._set_tz_combo(self.cv_to, "+09:00")
        r.addWidget(self.cv_from, 1)
        r.addWidget(QLabel("→"))
        r.addWidget(self.cv_to, 1)
        b = TacButton("换算", "secondary", height=28)
        b.clicked.connect(self._convert_tz)
        r.addWidget(b)
        tp.body.addLayout(r)
        r = QHBoxLayout()
        self.b_shift = TacButton("预览并应用…", "primary", en="SHIFT", height=32)
        self.b_shift_clear = TacButton("清除时间校正", "secondary", height=32)
        r.addWidget(self.b_shift, 1)
        r.addWidget(self.b_shift_clear)
        tp.body.addLayout(r)
        tp.body.addWidget(caption("例：相机设成北京时间（+08:00）去了日本（+09:00），选“+08:00 → +09:00”点换算，"
                                  "即推后 1 小时并把时区改为 +09:00。只改软件里使用的时间，不改照片文件。", "faint", 8))
        right.addWidget(tp)

        # ---- 写回 XMP ----
        xp = Panel("写回 XMP 附属文件", "Write-back (optional)")
        self.x_gps = QCheckBox("GPS 坐标（轨迹 / 参考照片匹配到的）")
        self.x_place = QCheckBox("地点名称（手动指定 / 匹配）")
        self.x_time = QCheckBox("校正后的拍摄时间")
        for cb, key in ((self.x_gps, "xmp_write_gps"), (self.x_place, "xmp_write_place"),
                        (self.x_time, "xmp_write_time")):
            cb.setChecked(bool(ctx.settings.get(key, True)))
            cb.toggled.connect(lambda v, k=key: ctx.settings.set(k, bool(v)))
            xp.body.addWidget(cb)
        self.b_xmp = TacButton("生成 XMP…", "secondary", en="SIDECAR", height=32)
        xp.body.addWidget(self.b_xmp)
        xp.body.addWidget(caption("只为 RAW 新建“文件名.xmp”供 Lightroom 等软件读取；已有 .xmp 的跳过，不改原片。"
                                  "没有选择对象时处理全部照片。可在「操作记录」页撤销。", "faint", 8))
        right.addWidget(xp)
        right.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(rw)
        scroll.setMinimumWidth(400)
        scroll.setMaximumWidth(470)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        self.root.addWidget(split, 1)

        # ---- 信号 ----
        self._resolve_timer = QTimer(self)
        self._resolve_timer.setSingleShot(True)
        self._resolve_timer.setInterval(250)
        self._resolve_timer.timeout.connect(self._resolve_place)
        self._fill_timer = QTimer(self)
        self._fill_timer.setSingleShot(True)
        self._fill_timer.setInterval(500)
        self._fill_timer.timeout.connect(lambda: self._rematch(reload=False))
        self.place_edit.textChanged.connect(lambda: self._resolve_timer.start())
        self.place_scope.currentIndexChanged.connect(self._update_place_count)
        self.b_place.clicked.connect(self._apply_place)
        self.b_place_clear.clicked.connect(self._clear_place)
        self.b_itin.clicked.connect(self._itinerary)
        self.cb_fill.toggled.connect(lambda v: self._fill_setting("gps_fill", bool(v)))
        self.cb_lib.toggled.connect(lambda v: self._fill_setting("gps_fill_library", bool(v)))
        self.sp_window.valueChanged.connect(lambda v: self._fill_setting("gps_fill_minutes", int(v)))
        self.cb_deftz.currentIndexChanged.connect(
            lambda: self._fill_setting("camera_tz_default", format_offset(self.cb_deftz.currentData())
                                       if self.cb_deftz.currentData() is not None else ""))
        self.b_fill.clicked.connect(lambda: self._rematch(reload=True))
        self.b_shift.clicked.connect(self._preview_shift)
        self.b_shift_clear.clicked.connect(self._clear_shift)
        self.b_xmp.clicked.connect(self._write_xmp)
        ctx.library_changed.connect(self.refresh)
        ctx.files_changed.connect(self.refresh)
        self.refresh()

    # ---- 小工具 ----------------------------------------------------------
    @staticmethod
    def _tz_combo(none_label: str | None) -> QComboBox:
        cb = QComboBox()
        if none_label:
            cb.addItem(none_label, None)
        for sec, label in timezone_choices():
            cb.addItem(label, sec)
        cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(8)
        cb.setMaxVisibleItems(16)
        return cb

    @staticmethod
    def _set_tz_combo(cb: QComboBox, tz: str | None) -> None:
        sec = parse_offset(tz) if tz else None
        i = cb.findData(sec) if sec is not None else 0
        cb.setCurrentIndex(max(0, i))

    def _busy(self, on: bool) -> None:
        self._is_busy = on
        for b in (self.b_place, self.b_place_clear, self.b_itin, self.b_fill, self.b_shift, self.b_shift_clear,
                  self.b_xmp):
            b.setEnabled(not on)
        if not on:
            self._update_target()

    def on_show(self):
        self._ensure_place_index()
        self.refresh()

    # ---- 地名库（第一次打开页面时在后台载入） ------------------------------
    def _ensure_place_index(self):
        if self._place_index is not None or self._loading_index:
            return
        self._loading_index = True
        self.place_edit.setPlaceholderText("正在载入地名库…")

        def job(prog, cancel):
            from ...core.places import PlaceIndex
            idx = PlaceIndex.get()
            return idx, idx.suggestions()

        def done(res):
            idx, items = res
            self._place_index = idx
            self._loading_index = False
            comp = QCompleter(self)
            comp.setModel(QStringListModel(items, comp))
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            comp.setMaxVisibleItems(14)
            self.place_edit.setCompleter(comp)
            self.place_edit.setPlaceholderText("例如：日本/京都、湖南/邵阳、日本/箱根（输入时有候选）")
            self._resolve_place()

        self.ctx.run(job, None, done)

    def _resolve_place(self):
        text = self.place_edit.text().strip()
        if not text:
            self.place_res.setText("输入地名后，这里显示识别结果。")
            self._update_place_count()
            return
        if self._place_index is None:
            self.place_res.setText("地名库载入中…")
            return
        r = self._place_index.resolve(text)
        if r and r.get("needs_country"):
            self.place_res.setText(f"→ 不认识“{text}”，请加上国家，例如“日本/{text}”")
        elif r:
            known = "（标准地名）" if r.get("cc") else "（不在地名库中，按输入的层级保存）"
            self.place_res.setText("→ " + r["label"].replace("/", " / ") + "  " + known)
        else:
            self.place_res.setText("→ 无法识别")
        self._update_place_count()

    # ---- 刷新 ------------------------------------------------------------
    def refresh(self):
        lib = self.ctx.library
        gs = [g for g in lib.groups if g.files]
        keep = {r.key for r in self.selected_days()}
        self._days = build_days(gs)
        self.day_model.set_rows(self._days)
        if keep:
            sm = self.days.selectionModel()
            for i, r in enumerate(self._days):
                if r.key in keep:
                    sm.select(self.day_model.index(i, 0), QItemSelectionModel.SelectionFlag.Select
                              | QItemSelectionModel.SelectionFlag.Rows)
        n = len(gs) or 1
        src = Counter()
        for r in self._days:
            src.update(r.sources)
        located = len(gs) - src.get("none", 0)
        fixed = sum(1 for g in gs if g.feat("date", "corrected"))
        self.c_total.set(str(len(gs)), None, f"张 · {len([r for r in self._days if r.day])} 天")
        self.c_loc.set(str(located), located / n, "张")
        self.c_track.set(str(src.get("track", 0)), src.get("track", 0) / n, "张")
        self.c_manual.set(str(src.get("manual", 0)), src.get("manual", 0) / n, "张")
        self.c_none.set(str(src.get("none", 0)), src.get("none", 0) / n, "张")
        self.c_fix.set(str(fixed), fixed / n, "张")
        self.day_panel.set_meta(f"{len(self._days)} DAYS")
        self._update_fill_status()
        self._days_selected()

    def _update_fill_status(self):
        lib = self.ctx.library
        p = theme.current()
        lines = []
        for s in lib.ref_sources:
            name = os.path.basename(s["path"].rstrip("\\/")) or s["path"]
            if s.get("error"):
                lines.append(f"<span style='color:{p.danger}'>■ {name}：{s['error']}</span>")
            elif s["kind"] == "track":
                lines.append(f"● {name}：{s.get('span', '')}")
            else:
                extra = f"，其中 {s['naive']} 张没有时区" if s.get("naive") else ""
                lines.append(f"● {name}：{s.get('photos', 0)} 张照片，{s['points']} 张有 GPS{extra}")
        st = lib.fill_stats or {}
        if st.get("matched"):
            kinds = "、".join(f"{MATCH_KIND_LABELS.get(k, k)} {st[k]}" for k in ("track", "reference", "library")
                             if st.get(k))
            lines.append(f"<b>已匹配 {st['matched']} 张</b>（{kinds}）")
        elif lib.groups and (lib.ref_sources or any(g.feat("geo", "source") == "gps" for g in lib.groups)):
            lines.append("暂无匹配：参考数据的时间段与照片不重合，或时间差超过窗口。")
        self.fill_status.setText("<br>".join(lines) or "还没有参考数据：添加手机照片文件夹或 GPX / KML 轨迹后点“读取参考并重新匹配”。")

    def selected_days(self) -> list[DayRow]:
        rows = sorted({i.row() for i in self.days.selectionModel().selectedRows()})
        return [self._days[r] for r in rows if r < len(self._days)]

    def _select_unknown_days(self):
        sm = self.days.selectionModel()
        sm.clearSelection()
        for i, r in enumerate(self._days):
            if r.unknown:
                sm.select(self.day_model.index(i, 0), QItemSelectionModel.SelectionFlag.Select
                          | QItemSelectionModel.SelectionFlag.Rows)

    def _days_selected(self, *_):
        lib = self.ctx.library
        gids = {gid for r in self.selected_days() for gid in r.gids}
        gs = [g for g in lib.groups if g.gid in gids and g.files]
        gs.sort(key=lambda g: (g.date is None, g.date or 0, g.name))
        self.grid.settings_snapshot = self.ctx.settings.snapshot()
        self.grid.set_groups(gs)
        self.grid_panel.set_meta(f"{len(gs)} ITEMS")
        self._update_target()

    # ---- 操作对象 --------------------------------------------------------
    def target(self) -> tuple[list[int], str]:
        sel = self.grid.selected_gids()
        if sel:
            return sel, f"下方已选的 {len(sel)} 张照片"
        days = self.selected_days()
        if days:
            gids = [gid for r in days for gid in r.gids]
            if len(days) == 1:
                return gids, f"{days[0].label.strip()} 的全部 {len(gids)} 张照片"
            return gids, f"所选 {len(days)} 天的全部 {len(gids)} 张照片"
        return [], ""

    def _update_target(self):
        gids, text = self.target()
        if gids:
            self.target_lab.setText("▶ " + text)
            self.target_lab.setStyleSheet("")
        else:
            self.target_lab.setText("先在左侧选择日期（Ctrl / Shift 多选），或在下方挑选照片。")
            self.target_lab.setStyleSheet(f"color: {theme.current().ink3};")
        for b in (self.b_place, self.b_place_clear, self.b_shift, self.b_shift_clear):
            b.setEnabled(bool(gids) and not self._is_busy)
        self._update_place_count()

    def _update_place_count(self):
        gids, _ = self.target()
        if not gids:
            self.place_count.setText("")
            return
        lib = self.ctx.library
        scope = self.place_scope.currentData()
        n = sum(1 for gid in gids if (g := lib.by_gid(gid)) is not None and would_apply(g, scope))
        self.place_count.setText(f"将指定 {n} 张；跳过 {len(gids) - n} 张（自带 GPS 或已有轨迹地点，取决于上面的范围）。")

    # ---- 后台重新整理 ------------------------------------------------------
    def _reanalyze(self, message: str, reload: bool = False, after=None):
        lib = self.ctx.library
        self._busy(True)
        self.ctx.status.emit("正在重新整理日期与地点…")

        def job(prog, cancel):
            if reload:
                lib.load_references(cancel=cancel)
            lib.run_meta_analyzers()
            return True

        def done(_):
            self._busy(False)
            self.ctx.library_changed.emit()
            self.ctx.status.emit(message)
            if after:
                after()

        def err(tb):
            self._busy(False)
            info(self, "出错了", f"<pre style='font-size:8pt'>{tb[-1500:]}</pre>", "ERROR")

        self.ctx.run(job, None, done, err)

    # ---- 地点 ------------------------------------------------------------
    def _apply_place(self):
        gids, text = self.target()
        if not gids:
            return
        if self._place_index is None:
            info(self, "请稍候", "地名库还在载入，几秒后再试。")
            return
        raw = self.place_edit.text().strip()
        place = self._place_index.resolve(raw) if raw else None
        if not place:
            info(self, "缺少地点", "请先输入地点，例如“日本/京都”。")
            return
        if place.get("needs_country"):
            info(self, "请加上国家", f"地名库里没有“{raw}”，无法确定是哪个国家。<br>请写成“国家/地名”，例如“日本/{raw}”。")
            return
        over_gps, over_track = scope_flags(self.place_scope.currentData())
        n = self.ctx.library.set_manual_place(gids, place, over_gps, over_track)
        self._reanalyze(f"已把 {n} 张照片的地点指定为：{place['label']}")

    def _clear_place(self):
        gids, _ = self.target()
        if gids:
            n = self.ctx.library.set_manual_place(gids, None)
            self._reanalyze(f"已清除 {n} 张照片的手动地点")

    def _itinerary(self):
        if self._place_index is None:
            info(self, "请稍候", "地名库还在载入，几秒后再试。")
            return
        years = Counter(r.day.year for r in self._days if r.day for _ in range(r.count))
        year = years.most_common(1)[0][0] if years else None
        dlg = ItineraryDialog(self, self.ctx.library, self._days, year, self._place_index)
        if dlg.exec() != ItineraryDialog.DialogCode.Accepted or not dlg.result:
            return
        over_gps, over_track = scope_flags(dlg.scope.currentData())
        total = 0
        for gids, place, _ in dlg.result:
            total += self.ctx.library.set_manual_place(gids, place, over_gps, over_track)
        self._reanalyze(f"已按行程给 {total} 张照片指定地点（{len(dlg.result)} 段）")

    # ---- GPS 补全 --------------------------------------------------------
    def _fill_setting(self, key, value):
        self.ctx.settings.set(key, value)
        if self.ctx.library.groups:
            self._fill_timer.start()

    def _list_items(self, lw: QListWidget) -> list[str]:
        return [lw.item(i).text() for i in range(lw.count())]

    def _add_ref_dir(self):
        start = (self._list_items(self.ref_dirs) or [os.path.expanduser("~")])[-1]
        d = QFileDialog.getExistingDirectory(self, "选择参考照片文件夹（例如手机照片）", start)
        if d:
            d = os.path.normpath(d)
            if d not in self._list_items(self.ref_dirs):
                self.ref_dirs.addItem(d)
            self.ctx.settings.set("gps_ref_dirs", self._list_items(self.ref_dirs))
            self.fill_status.setText("已添加，点“读取参考并重新匹配”生效。")

    def _add_tracks(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择 GPS 轨迹文件", os.path.expanduser("~"),
                                                "GPS 轨迹 (*.gpx *.kml);;所有文件 (*)")
        if files:
            have = self._list_items(self.tracks)
            for f in files:
                f = os.path.normpath(f)
                if f not in have:
                    self.tracks.addItem(f)
            self.ctx.settings.set("gps_track_files", self._list_items(self.tracks))
            self.fill_status.setText("已添加，点“读取参考并重新匹配”生效。")

    def _remove_selected(self, lw: QListWidget, key: str):
        for it in lw.selectedItems():
            lw.takeItem(lw.row(it))
        self.ctx.settings.set(key, self._list_items(lw))

    def _rematch(self, reload: bool):
        if not self.ctx.library.groups:
            if reload:
                info(self, "还没有照片", "请先在「资源接入」页扫描照片。")
            return
        lib = self.ctx.library

        def msg():
            st = lib.fill_stats or {}
            self.ctx.status.emit(f"GPS 补全完成：匹配到 {st.get('matched', 0)} 张")

        self._reanalyze("GPS 补全完成", reload=reload, after=msg)

    # ---- 时间校正 --------------------------------------------------------
    def _shift_seconds(self) -> int:
        d, h, m, s = (sp.value() for sp in self.sh)
        return int(self.sh_dir.currentData()) * (d * 86400 + h * 3600 + m * 60 + s)

    def _convert_tz(self):
        a, b = self.cv_from.currentData(), self.cv_to.currentData()
        diff = int(b) - int(a)
        self.sh_dir.setCurrentIndex(0 if diff >= 0 else 1)
        diff = abs(diff)
        for sp, v in zip(self.sh, (diff // 86400, diff % 86400 // 3600, diff % 3600 // 60, diff % 60)):
            sp.setValue(v)
        self._set_tz_combo(self.sh_tz, format_offset(b))

    def _preview_shift(self):
        gids, text = self.target()
        if not gids:
            return
        seconds = self._shift_seconds()
        tzv = self.sh_tz.currentData()
        tz = format_offset(tzv) if tzv is not None else None
        if not seconds and not tz:
            info(self, "没有要校正的内容", "请设置要推后 / 提前的时间，或选择实际时区。")
            return
        rows = self.ctx.library.preview_time_shift(gids, seconds, tz)
        if not rows:
            info(self, "无法校正", "所选照片都没有可用的拍摄时间。")
            return
        dlg = TimeShiftDialog(self, rows, seconds, tz)
        if dlg.exec() != TimeShiftDialog.DialogCode.Accepted:
            return
        n = self.ctx.library.set_time_shift([g.gid for g, _, _ in rows], seconds, tz)
        self._reanalyze(f"已校正 {n} 张照片的时间（{shift_label(seconds) or '仅时区'}）", reload=False)

    def _clear_shift(self):
        gids, _ = self.target()
        if gids:
            n = self.ctx.library.set_time_shift(gids, None)
            self._reanalyze(f"已清除 {n} 张照片的时间校正")

    # ---- 写回 XMP --------------------------------------------------------
    def _write_xmp(self):
        from ...ops.xmpwrite import build_xmp_plan, write_xmp
        lib = self.ctx.library
        if not lib.exiftool_path:
            info(self, "需要 ExifTool", "写入 XMP 需要 ExifTool。请在「系统设置」中检测或指定 exiftool.exe。")
            return
        gids, text = self.target()
        if gids:
            groups = [g for gid in gids if (g := lib.by_gid(gid)) is not None]
            scope_text = text
        else:
            groups = [g for g in lib.groups if g.files]
            scope_text = f"全部 {len(groups)} 张照片"
        plan = build_xmp_plan(groups, self.x_gps.isChecked(), self.x_place.isChecked(), self.x_time.isChecked())
        if not plan.tasks:
            info(self, "没有需要写入的照片",
                 f"范围：{scope_text}<br>没有可写内容 {plan.skipped_nothing} 张 · 已有 .xmp {plan.skipped_exists} 张 · "
                 f"非 RAW {plan.skipped_no_raw} 张。<br><br>只有补全了地点（轨迹匹配 / 手动指定）或校正了时间的 RAW 才会生成 .xmp。")
            return
        if XmpDialog(self, plan, scope_text).exec() != XmpDialog.DialogCode.Accepted:
            return
        self.ctx.busy = True

        def job(prog, cancel):
            return write_xmp(plan, lib.exiftool_path, self.ctx.journal, lambda d, t, c: prog((d, t, c)), cancel)

        res, tb = run_with_progress(self.ctx, self, "正在写入 XMP", "WRITING", job)
        self.ctx.busy = False
        if tb or res is None:
            info(self, "写入出错", f"<pre style='font-size:8pt'>{(tb or '')[-1500:]}</pre>", "ERROR")
            return
        n = lib.attach_files(res.written)
        self.ctx.files_changed.emit()
        msg = f"已新建 <b>{n}</b> 个 .xmp 附属文件（批次 #{res.batch_id}，可在「操作记录」页撤销）。"
        if res.failed:
            msg += f"<br><br>{res.failed} 个失败：<br>" + "<br>".join(res.errors[:8])
        if res.cancelled:
            msg += "<br><br>已取消，剩余照片未写入。"
        self.ctx.status.emit(f"{OPERATION_LABELS['xmp']}：{n} 个文件")
        info(self, "写入完成", msg, "COMPLETE")
