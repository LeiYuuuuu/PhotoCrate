"""03 分区编排：选择条件（可多级嵌套）→ 实时预览目标文件夹结构 → 生成计划 → 确认执行。"""
from __future__ import annotations

import os

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox,
                               QSplitter, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ...ops.pathsafe import human_size
from ...ops.planner import build_plan
from ...partition.base import DIMENSIONS, PartitionContext, all_tokens, member_format
from ...partition.engine import BUILTIN_PRESETS, PartitionSpec, Rule, build_tree, unknown_tokens
from ...plugins import PRESETS
from .. import theme
from ..dialogs import PlanDialog, ask, info, run_with_progress
from ..theme import font
from ..widgets.deco import IconButton, Panel, TacButton, caption, micro_label
from ..widgets.thumbgrid import ThumbGrid, open_folder
from .base import Page

MAX_LEVELS = 4


def shrinkable(combo: QComboBox, chars: int = 8) -> QComboBox:
    """下拉框默认按最长选项撑宽；允许它随面板缩窄，长文字省略显示（下拉列表里仍是全文）。"""
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(chars)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return combo
NodeRole = Qt.ItemDataRole.UserRole + 7


class LevelBadge(QLabel):
    def __init__(self, n: int):
        super().__init__(f"L{n}")
        self.setFixedSize(30, 30)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(font("display", 11, 700))
        p = theme.current()
        self.setStyleSheet(f"background: {p.slab}; color: {p.accent};")


class RuleRow(QFrame):
    changed = Signal()
    remove = Signal(object)
    move = Signal(object, int)

    def __init__(self, level: int, rule: Rule):
        super().__init__()
        p = theme.current()
        self.setObjectName("RuleRow")
        self.setStyleSheet(f"#RuleRow {{ background: {p.bg}; border: 1px solid {p.line}; }}")
        self.rule = rule
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.badge = LevelBadge(level)
        top.addWidget(self.badge)
        self.dim = shrinkable(QComboBox())
        for d in DIMENSIONS.values():
            if d.id == "fixed":
                continue
            self.dim.addItem(f"{d.label}", d.id)
        i = self.dim.findData(rule.dim)
        self.dim.setCurrentIndex(max(0, i))
        top.addWidget(self.dim, 1)
        for kind, tip, cb in (("up", "上移", lambda: self.move.emit(self, -1)),
                              ("down", "下移", lambda: self.move.emit(self, 1)),
                              ("close", "删除这一级", lambda: self.remove.emit(self))):
            b = IconButton(kind, tip, 28)
            b.clicked.connect(cb)
            top.addWidget(b)
        lay.addLayout(top)
        self.opt_box = QGridLayout()
        self.opt_box.setContentsMargins(36, 0, 0, 0)
        self.opt_box.setHorizontalSpacing(8)
        self.opt_box.setVerticalSpacing(4)
        lay.addLayout(self.opt_box)
        self.desc = caption("", "faint", 8)
        self.desc.setContentsMargins(36, 0, 0, 0)
        lay.addWidget(self.desc)
        self.dim.currentIndexChanged.connect(self._dim_changed)
        self._build_opts()

    def set_level(self, n: int) -> None:
        self.badge.setText(f"L{n}")

    def _dim_changed(self):
        self.rule = Rule(self.dim.currentData(), {})
        self._build_opts()
        self.changed.emit()

    def _build_opts(self):
        while self.opt_box.count():
            it = self.opt_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        d = DIMENSIONS.get(self.rule.dim)
        if not d:
            return
        self.desc.setText(d.description)
        opts = d.resolve_opts(self.rule.opts)
        self.rule.opts = opts
        for row, o in enumerate(d.options):
            lab = QLabel(o.label)
            lab.setProperty("role", "dim")
            lab.setFont(font("cn", 9))
            w = self._make_widget(o, opts.get(o.key, o.default))
            if o.tip:
                w.setToolTip(o.tip)
            if o.kind == "bool":
                self.opt_box.addWidget(w, row, 0, 1, 2)
            else:
                self.opt_box.addWidget(lab, row, 0)
                self.opt_box.addWidget(w, row, 1)
        self.opt_box.setColumnStretch(1, 1)

    def _make_widget(self, o, value):
        def setv(v):
            self.rule.opts[o.key] = v
            self.changed.emit()
        if o.kind == "choice":
            w = shrinkable(QComboBox())
            for val, label in o.choices:
                w.addItem(label, val)
            w.setCurrentIndex(max(0, w.findData(value)))
            w.currentIndexChanged.connect(lambda _=None, w=w: setv(w.currentData()))
        elif o.kind == "bool":
            w = QCheckBox(o.label)
            w.setChecked(bool(value))
            w.toggled.connect(setv)
        elif o.kind == "int":
            w = QSpinBox()
            w.setRange(int(o.minimum), int(o.maximum))
            w.setValue(int(value or 0))
            w.setSuffix(o.suffix)
            w.valueChanged.connect(setv)
        elif o.kind == "float":
            w = QDoubleSpinBox()
            w.setRange(o.minimum, o.maximum)
            w.setSingleStep(o.step)
            w.setValue(float(value or 0))
            w.setSuffix(o.suffix)
            w.valueChanged.connect(setv)
        else:
            w = QLineEdit(str(value or ""))
            w.editingFinished.connect(lambda w=w: setv(w.text()))
        return w


class CountDelegate(QStyledItemDelegate):
    def paint(self, qp: QPainter, option, index):
        node = index.data(NodeRole)
        if node is None or index.column() != 1:
            return super().paint(qp, option, index)
        p = theme.current()
        qp.save()
        r = QRect(option.rect)
        frac = index.data(NodeRole + 1) or 0
        bar = QRectF(r.left() + 4, r.top() + 5, (r.width() - 8) * frac, r.height() - 10)
        qp.fillRect(QRectF(r.left() + 4, r.top() + 5, r.width() - 8, r.height() - 10), QColor(p.surface2))
        qp.fillRect(bar, QColor(p.accent))
        f = font("display", 9.5, 600)
        qp.setFont(f)
        txt = str(node.count)
        tw = QFontMetrics(f).horizontalAdvance(txt)
        # 数字落在黄色条上时用深色字，保证两套主题下都清晰
        on_bar = bar.right() > r.right() - 6 - tw - 2
        qp.setPen(QColor(p.accent_ink if on_bar else p.ink))
        qp.drawText(r.adjusted(8, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, txt)
        qp.restore()


class PartitionPage(Page):
    page_id = "partition"
    index = "03"
    title = "分区编排"
    en = "Partition matrix // preview & execute"
    sub = "按顺序叠加分区条件，右侧实时预览文件夹结构；确认计划后才会真正移动文件。"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.rows: list[RuleRow] = []
        self.tree_root = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self.refresh)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ======== 左：条件 + 执行参数 ========
        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 6, 0)
        left.setSpacing(10)

        cond = Panel("分区条件", "Conditions")
        mode_row = QHBoxLayout()
        self.m_rules = QRadioButton("条件组合")
        self.m_tpl = QRadioButton("高级模板")
        bg = QButtonGroup(self)
        bg.addButton(self.m_rules)
        bg.addButton(self.m_tpl)
        mode_row.addWidget(self.m_rules)
        mode_row.addWidget(self.m_tpl)
        mode_row.addStretch(1)
        cond.body.addLayout(mode_row)
        pr = QHBoxLayout()
        pr.addWidget(micro_label("preset"))
        self.preset = shrinkable(QComboBox())
        self.preset.addItem("— 快速预设 —", None)
        for ps in BUILTIN_PRESETS:
            self.preset.addItem(ps["name"], ps)
        for ps in PRESETS:
            self.preset.addItem("插件：" + ps.name, {"name": ps.name, "rules": ps.rules, "template": ps.template})
        pr.addWidget(self.preset, 1)
        cond.body.addLayout(pr)

        # 条件组合
        self.rules_box = QWidget()
        rb = QVBoxLayout(self.rules_box)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(6)
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(6)
        rb.addLayout(self.rows_layout)
        self.b_add = TacButton("＋ 添加下一级条件", "secondary", height=30)
        self.b_add.clicked.connect(self._add_row)
        rb.addWidget(self.b_add)
        cond.body.addWidget(self.rules_box)

        # 模板
        self.tpl_box = QWidget()
        tb = QVBoxLayout(self.tpl_box)
        tb.setContentsMargins(0, 0, 0, 0)
        self.tpl = QLineEdit(ctx.settings.get("partition_template") or "{年}/{年}-{月}")
        self.tpl.setFont(font("mono", 10))
        tb.addWidget(self.tpl)
        tb.addWidget(caption("用 / 分隔文件夹层级；{市|省|未知地点} 表示依次尝试，都没有时用最后的文字。点击下面的占位符插入：", "faint", 8))
        chips = QGridLayout()
        chips.setSpacing(4)
        for i, name in enumerate(sorted(all_tokens(), key=_token_order)):
            b = QPushButton(name)
            b.setFont(font("cn", 8.5))
            b.setFixedHeight(24)
            b.clicked.connect(lambda _=None, n=name: self._insert_token(n))
            chips.addWidget(b, i // 5, i % 5)
        tb.addLayout(chips)
        self.tpl_warn = caption("", "warn", 8.5)
        tb.addWidget(self.tpl_warn)
        cond.body.addWidget(self.tpl_box)
        left.addWidget(cond)

        # 执行参数
        ex = Panel("执行参数", "Execution")
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(8)
        g.addWidget(self._lab("目标文件夹"), 0, 0)
        trow = QHBoxLayout()
        self.target = QLineEdit(ctx.settings.get("target_dir") or "")
        self.target.setMinimumWidth(80)
        self.target.setFont(font("mono", 8.5))
        self.target.setPlaceholderText("整理后的照片放到哪里")
        bt = QPushButton("浏览")
        bt.clicked.connect(self._pick_target)
        trow.addWidget(self.target, 1)
        trow.addWidget(bt)
        g.addLayout(trow, 0, 1)
        g.addWidget(self._lab("操作方式"), 1, 0)
        orow = QHBoxLayout()
        self.op_move = QRadioButton("移动")
        self.op_copy = QRadioButton("复制（保留原文件）")
        ob = QButtonGroup(self)
        ob.addButton(self.op_move)
        ob.addButton(self.op_copy)
        (self.op_copy if ctx.settings.get("operation") == "copy" else self.op_move).setChecked(True)
        orow.addWidget(self.op_move)
        orow.addWidget(self.op_copy)
        orow.addStretch(1)
        g.addLayout(orow, 1, 1)
        g.addWidget(self._lab("重名处理"), 2, 0)
        self.conflict = shrinkable(QComboBox())
        self.conflict.addItem("自动加编号（推荐）", "rename")
        self.conflict.addItem("跳过该照片", "skip")
        self.conflict.setCurrentIndex(max(0, self.conflict.findData(ctx.settings.get("conflict"))))
        g.addWidget(self.conflict, 2, 1)
        g.addWidget(self._lab("RAW 拆分"), 3, 0)
        self.split = shrinkable(QComboBox())
        self.split.addItem("不拆分（RAW 与 JPG 放一起）", "none")
        self.split.addItem("RAW 放进 RAW 子文件夹", "raw_sub")
        self.split.addItem("RAW → RAW/，其他按格式 JPG/ HEIC/ PNG/", "both_sub")
        self.split.setCurrentIndex(max(0, self.split.findData(ctx.settings.get("split_raw_jpg"))))
        g.addWidget(self.split, 3, 1)
        g.addWidget(self._lab("处理范围"), 4, 0)
        srow = QHBoxLayout()
        self.sc_all = QRadioButton("全部")
        self.sc_sel = QRadioButton("仅已选")
        sb = QButtonGroup(self)
        sb.addButton(self.sc_all)
        sb.addButton(self.sc_sel)
        (self.sc_sel if ctx.settings.get("scope") == "selected" else self.sc_all).setChecked(True)
        srow.addWidget(self.sc_all)
        srow.addWidget(self.sc_sel)
        srow.addStretch(1)
        g.addLayout(srow, 4, 1)
        g.setColumnStretch(1, 1)
        ex.body.addLayout(g)
        left.addWidget(ex)
        left.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(left_w)
        scroll.setMinimumWidth(380)
        scroll.setMaximumWidth(500)
        split.addWidget(scroll)

        # ======== 中：预览树 ========
        tp = Panel("目标结构预览", "Target tree", "")
        self.tree_panel = tp
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["文件夹", "照片", "大小"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().resizeSection(1, 92)
        self.tree.header().setMinimumSectionSize(40)
        self.tree.setIndentation(16)
        self.tree.setItemDelegateForColumn(1, CountDelegate(self.tree))
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.currentItemChanged.connect(self._tree_select)
        tp.body.addWidget(self.tree, 1)
        self.tree_note = caption("", "faint", 8.5)
        tp.body.addWidget(self.tree_note)
        split.addWidget(tp)

        # ======== 右：文件夹内容 ========
        gp = Panel("文件夹内容", "Folder content", "")
        self.grid_panel = gp
        self.grid = ThumbGrid(selectable=False)
        self.grid.set_cell_width(140)
        gp.body.addWidget(self.grid, 1)
        split.addWidget(gp)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 5)
        split.setStretchFactor(2, 4)
        split.setSizes([410, 560, 460])
        self.root.addWidget(split, 1)

        # ======== 底部操作条 ========
        bar = QFrame()
        bar.setObjectName("ActionBar")
        p = theme.current()
        bar.setStyleSheet(f"#ActionBar {{ background: {p.slab}; }} #ActionBar QLabel {{ color: {p.slab_ink}; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 8, 10, 8)
        self.summary = QLabel("请先在「资源接入」页扫描照片。")
        self.summary.setFont(font("cn", 9.5))
        bl.addWidget(self.summary, 1)
        self.b_open = TacButton("打开目标文件夹", "dark", height=34)
        self.b_open.clicked.connect(lambda: self.target.text() and os.path.isdir(self.target.text())
                                    and open_folder(self.target.text()))
        self.b_plan = TacButton("生成执行计划", "primary", en="DRY RUN", height=38)
        self.b_plan.clicked.connect(self.make_plan)
        bl.addWidget(self.b_open)
        bl.addWidget(self.b_plan)
        self.root.addWidget(bar)

        # 初始状态
        for r in ctx.settings.get("partition_rules") or []:
            self._add_row(Rule.from_dict(r), refresh=False)
        if not self.rows:
            self._add_row(Rule("date", {"granularity": "y/ym"}), refresh=False)
        (self.m_tpl if ctx.settings.get("partition_mode") == "template" else self.m_rules).setChecked(True)
        self._mode_changed()

        self.m_rules.toggled.connect(self._mode_changed)
        self.tpl.textChanged.connect(self._schedule)
        self.preset.activated.connect(self._apply_preset)
        for w in (self.conflict, self.split):
            w.currentIndexChanged.connect(self._save_exec)
        for w in (self.op_move, self.sc_all):
            w.toggled.connect(self._save_exec)
        self.sc_all.toggled.connect(self._schedule)
        self.target.editingFinished.connect(self._target_edited)
        ctx.library_changed.connect(self._schedule)
        ctx.files_changed.connect(self._schedule)
        ctx.selection_changed.connect(self._update_scope_labels)
        ctx.settings_changed.connect(lambda k: self._schedule())

    # ---- 辅助 ------------------------------------------------------------
    def _lab(self, t):
        l = QLabel(t)
        l.setFont(font("cn", 9.5, 600))
        return l

    def on_show(self):
        if not self.target.text():
            self.target.setText(self.ctx.settings.get("target_dir") or "")
        self._update_scope_labels()
        self._schedule()

    def _schedule(self, *_):
        self._timer.start()

    def _update_scope_labels(self):
        lib = self.ctx.library
        n_all = sum(1 for g in lib.groups if g.files)
        n_sel = len(lib.selected)
        self.sc_all.setText(f"全部（{n_all}）")
        self.sc_sel.setText(f"仅已选（{n_sel}）")
        self.sc_sel.setEnabled(n_sel > 0)
        if n_sel == 0 and self.sc_sel.isChecked():
            self.sc_all.setChecked(True)
        self._schedule()

    def _insert_token(self, name):
        self.tpl.insert("{" + name + "}")
        self.tpl.setFocus()

    def _pick_target(self):
        d = QFileDialog.getExistingDirectory(self, "选择目标文件夹", self.target.text() or os.path.expanduser("~"))
        if d:
            self.target.setText(os.path.normpath(d))
            self._target_edited()

    def _target_edited(self):
        self.ctx.settings.set("target_dir", self.target.text().strip())
        self._schedule()

    def _save_exec(self, *_):
        s = self.ctx.settings
        s.set("operation", "copy" if self.op_copy.isChecked() else "move", save=False)
        s.set("conflict", self.conflict.currentData(), save=False)
        s.set("split_raw_jpg", self.split.currentData(), save=False)
        s.set("scope", "selected" if self.sc_sel.isChecked() else "all")

    # ---- 条件行 ----------------------------------------------------------
    def _add_row(self, rule: Rule | None = None, refresh: bool = True):
        if len(self.rows) >= MAX_LEVELS:
            return
        if not isinstance(rule, Rule):
            used = {r.rule.dim for r in self.rows}
            nxt = next((d for d in ("date", "region", "color", "camera", "filetype") if d not in used), "orientation")
            rule = Rule(nxt, {})
        row = RuleRow(len(self.rows) + 1, rule)
        row.changed.connect(self._rules_changed)
        row.remove.connect(self._remove_row)
        row.move.connect(self._move_row)
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        self.b_add.setEnabled(len(self.rows) < MAX_LEVELS)
        if refresh:
            self._rules_changed()

    def _remove_row(self, row):
        if len(self.rows) <= 1:
            return
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._relevel()
        self._rules_changed()

    def _move_row(self, row, delta):
        i = self.rows.index(row)
        j = i + delta
        if not 0 <= j < len(self.rows):
            return
        self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
        for r in self.rows:
            self.rows_layout.removeWidget(r)
        for r in self.rows:
            self.rows_layout.addWidget(r)
        self._relevel()
        self._rules_changed()

    def _relevel(self):
        for i, r in enumerate(self.rows, 1):
            r.set_level(i)
        self.b_add.setEnabled(len(self.rows) < MAX_LEVELS)

    def _rules_changed(self):
        self.ctx.settings.set("partition_rules", [r.rule.to_dict() for r in self.rows])
        self._schedule()

    def _mode_changed(self, *_):
        tpl = self.m_tpl.isChecked()
        self.rules_box.setVisible(not tpl)
        self.tpl_box.setVisible(tpl)
        self.ctx.settings.set("partition_mode", "template" if tpl else "rules")
        self._schedule()

    def apply_preset_rules(self, rules: list[dict]) -> None:
        """其他页面（如「旅行与地图」）一键套用分区条件。"""
        self._apply_preset_data({"rules": rules})

    def _apply_preset(self, idx):
        ps = self.preset.itemData(idx)
        if not ps:
            return
        self._apply_preset_data(ps)

    def _apply_preset_data(self, ps):
        if ps.get("template"):
            self.tpl.setText(ps["template"])
            self.m_tpl.setChecked(True)
        else:
            for r in list(self.rows):
                r.setParent(None)
                r.deleteLater()
            self.rows = []
            for rd in ps.get("rules") or []:
                self._add_row(Rule.from_dict(rd), refresh=False)
            self.m_rules.setChecked(True)
            self._rules_changed()
        self.preset.setCurrentIndex(0)

    # ---- 预览 ------------------------------------------------------------
    def spec(self) -> PartitionSpec:
        if self.m_tpl.isChecked():
            return PartitionSpec("template", [], self.tpl.text().strip())
        return PartitionSpec("rules", [Rule(r.rule.dim, dict(r.rule.opts)) for r in self.rows], "")

    def groups(self):
        return self.ctx.library.scope_groups("selected" if self.sc_sel.isChecked() else "all")

    def refresh(self):
        if self.m_tpl.isChecked():
            self.ctx.settings.set("partition_template", self.tpl.text().strip())
            bad = unknown_tokens(self.tpl.text())
            self.tpl_warn.setText(("▲ 未识别的占位符：" + "、".join(bad) + "（会被当作文字）") if bad else "")
        groups = self.groups()
        spec = self.spec()
        ctxp = PartitionContext(self.ctx.settings.snapshot())
        root, _ = build_tree(groups, spec, ctxp)
        self.tree_root = root
        self.tree.clear()
        tgt = self.target.text().strip() or "（未选择目标文件夹）"
        top = QTreeWidgetItem([os.path.basename(tgt.rstrip("\\/")) or tgt, "", human_size(root.size)])
        top.setData(0, NodeRole, root)
        top.setData(1, NodeRole, root)
        top.setData(1, NodeRole + 1, 1.0)
        top.setFont(0, font("cn", 10, 700))
        top.setToolTip(0, tgt)
        self.tree.addTopLevelItem(top)
        mx = max([c.count for c in root.children.values()] or [1])
        self._fill(top, root, mx, 0)
        top.setExpanded(True)
        self.tree.setCurrentItem(top)
        n_folders = root.folder_count()
        self.tree_panel.set_meta(f"{n_folders} DIRS")
        notes = []
        if root.guess:
            notes.append(f"{root.guess} 张日期为推测（文件名/修改时间），缩略图上有橙色标记")
        unk = [c for c in root.children.values() if c.name.startswith("未知")]
        if unk:
            notes.append("、".join(f"{c.name} {c.count} 张" for c in unk))
        self.tree_note.setText("；".join(notes))
        op = "复制" if self.op_copy.isChecked() else "移动"
        if groups:
            self.summary.setText(f"{spec.describe()}   ·   {root.count} 组照片 → {n_folders} 个文件夹   ·   "
                                 f"{human_size(root.size)}   ·   操作：{op}")
        else:
            self.summary.setText("请先在「资源接入」页扫描照片。")
        self.b_plan.setEnabled(bool(groups))

    def _fill(self, item, node, mx, depth):
        for c in node.sorted_children():
            it = QTreeWidgetItem([c.name, "", human_size(c.size)])
            it.setData(0, NodeRole, c)
            it.setData(1, NodeRole, c)
            it.setData(1, NodeRole + 1, c.count / mx if mx else 0)
            it.setFont(2, font("mono", 8))
            if c.name.startswith(("未知", "日期待确认", "无法")):
                it.setForeground(0, QColor(theme.current().warn))
            item.addChild(it)
            cmx = max([k.count for k in c.children.values()] or [1])
            self._fill(it, c, cmx, depth + 1)
            if depth < 1:
                it.setExpanded(True)

    def _tree_select(self, cur, prev):
        if cur is None:
            return
        node = cur.data(0, NodeRole)
        if node is None:
            return
        gids = set(node.all_gids())
        lib = self.ctx.library
        gs = [g for g in lib.groups if g.gid in gids]
        gs.sort(key=lambda g: (g.date is None, g.date or 0, g.name))
        self.grid.settings_snapshot = self.ctx.settings.snapshot()
        # 按格式拆分时，ARW 文件夹里显示 .ARW 文件名、JPG 文件夹里显示 .JPG
        labels = {}
        comps = set(node.path)
        if comps:
            for g in gs:
                for m in g.image_files:
                    fmt = member_format(m, g)
                    if fmt in comps and len(g.image_files) > 1:
                        labels[g.gid] = {"name": m.name, "tag": fmt}
                        break
        self.grid.set_groups(gs, labels)
        path = "/".join(node.path) or "（根目录）"
        self.grid_panel.set_meta(f"{len(gs)} ITEMS")
        self.grid_panel.setToolTip(path)

    # ---- 计划与执行 ------------------------------------------------------
    def make_plan(self):
        target = self.target.text().strip()
        if not target:
            info(self, "缺少目标文件夹", "请先选择整理后的照片要放到哪个文件夹（可以就是原文件夹）。")
            return
        self._save_exec()
        groups = self.groups()
        spec = self.spec()
        s = self.ctx.settings.snapshot()
        op = "copy" if self.op_copy.isChecked() else "move"
        conflict = self.conflict.currentData()
        split = self.split.currentData()
        self.b_plan.setEnabled(False)
        self.ctx.status.emit("正在生成执行计划（模拟运行，不会改动文件）…")

        def job(prog, cancel):
            return build_plan(groups, spec, PartitionContext(s), target, op, conflict, split)

        self.ctx.run(job, None, self._show_plan, self._plan_error)

    def _plan_error(self, tb):
        self.b_plan.setEnabled(True)
        info(self, "生成计划失败", f"<pre style='font-size:8pt'>{tb[-1500:]}</pre>", "ERROR")

    def _show_plan(self, plan):
        self.b_plan.setEnabled(True)
        self.ctx.status.emit("计划已生成，等待确认")
        dlg = PlanDialog(self, plan)
        if dlg.exec() != PlanDialog.DialogCode.Accepted:
            return
        self.execute(plan)

    def execute(self, plan):
        mover = self.ctx.mover
        extra = {"spec": plan.spec_text}
        self.ctx.busy = True

        keep = list(self.ctx.settings.get("source_dirs") or []) + [plan.target_root]

        def job(prog, cancel):
            return mover.execute(plan, lambda d, t, c: prog((d, t, c)), cancel, extra, keep_roots=keep)

        res, tb = run_with_progress(self.ctx, self, "正在" + ("复制" if plan.operation == "copy" else "移动") + "照片",
                                    "EXECUTING", job)
        if tb or res is None:
            self.ctx.busy = False
            self.ctx.files_changed.emit()
            info(self, "执行出错", f"<pre style='font-size:8pt'>{(tb or '')[-1500:]}</pre>", "ERROR")
            return
        self._done(res, plan)

    def _done(self, res, plan):
        self.ctx.busy = False
        lib = self.ctx.library
        lib.apply_moves(res.mapping)
        self.ctx.files_changed.emit()
        op = "复制" if plan.operation == "copy" else "移动"
        msg = [f"已{op} <b>{res.done_groups}</b> 组照片（{res.done_files} 个文件）。"]
        if res.cancelled:
            msg.append("操作已取消，剩余照片未处理。")
        if res.failed_groups:
            msg.append(f"<span style='color:{theme.current().danger}'>{res.failed_groups} 组失败（已整组退回原处）：</span><br>"
                       + "<br>".join(res.errors[:8]))
        if plan.operation == "move" and res.done_files:
            from ...ops.planner import sources_after_move
            old = list(self.ctx.settings.get("source_dirs") or [])
            new = sources_after_move(old, plan.target_root)
            if new != old:
                # 照片已经搬到目标文件夹：数据源跟过去，之后“重新扫描 → 按新规则再分”才找得到它们
                src = self.window().page("source") if hasattr(self.window(), "page") else None
                if src is not None and hasattr(src, "set_dirs"):
                    src.set_dirs(new)
                else:
                    self.ctx.settings.set("source_dirs", new)
                msg.append(f"已把目标文件夹 {plan.target_root} 加入「资源接入」的数据源，以后重新扫描会从那里读取。")
        if res.pruned_dirs:
            msg.append(f"清理了 {len(res.pruned_dirs)} 个搬空的旧文件夹（撤销时会重新建出来）。")
        msg.append(f"批次编号 #{res.batch_id}，可随时在「记录」页撤销。")
        self.ctx.status.emit(f"完成：已{op} {res.done_files} 个文件（批次 #{res.batch_id}）")
        if ask(self, "执行完成", "<br><br>".join(msg), ok="打开目标文件夹", cancel="关闭", en="COMPLETE"):
            open_folder(plan.target_root)


def _token_order(name: str) -> int:
    order = ["年", "月", "日", "年月", "年月日", "日期", "时间", "季度", "国家", "省", "市", "区县", "地区", "冷暖", "主色", "明暗",
             "品牌", "相机", "镜头", "来源", "类型", "扩展名", "方向", "清晰度", "焦段", "感光度", "分辨率",
             "原文件夹", "星级", "标记", "色标", "旅行", "第几天", "旅行天数", "场景", "人物", "日期来源"]
    return order.index(name) if name in order else 100
