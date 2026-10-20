"""05 操作记录：每次移动/复制的完整清单，可撤销、导出。"""
from __future__ import annotations

import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QSplitter,
                               QTableView, QTableWidget, QTableWidgetItem)

from ...ops.journal import STATUS_LABELS
from ...ops.pathsafe import human_size
from .. import theme
from ..dialogs import ProgressDialog, ask, info
from ..theme import font
from ..widgets.deco import Panel, TacButton, caption
from ..widgets.thumbgrid import open_folder
from .base import Page


class OpsModel(QAbstractTableModel):
    """文件清单（可能有上千行）：用数据模型按需绘制，避免逐格创建控件导致界面卡顿。"""
    HEAD = ["状态", "源路径", "目标路径", "备注"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self._mono = font("mono", 8)

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEAD[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        o = self.rows[index.row()]
        c = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if c == 0:
                return STATUS_LABELS.get(o["status"], o["status"])
            return (o["src"], o["dst"], o["error"] or "")[c - 1]
        if role == Qt.ItemDataRole.FontRole and c in (1, 2):
            return self._mono
        if role == Qt.ItemDataRole.ForegroundRole and c == 0:
            p = theme.current()
            col = {"done": p.ok, "failed": p.danger, "undo_failed": p.danger, "undone": p.ink3}.get(o["status"])
            return QColor(col) if col else None
        return None


class HistoryPage(Page):
    page_id = "history"
    index = "05"
    title = "操作记录"
    en = "Operation log // undo"
    sub = "每次执行前都会先写日志。选中一个批次即可查看清单、撤销（把文件移回原处）或导出。"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        split = QSplitter(Qt.Orientation.Vertical)
        bp = Panel("批次", "Batches", "")
        self.bpanel = bp
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["#", "时间", "操作", "分区规则", "照片 / 文件", "大小", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for c in (0, 1, 2, 4, 5, 6):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._load_ops)
        bp.body.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(caption("撤销会按日志逆序把文件移回原位置；原位置已有同名文件时会改名恢复，不会覆盖。", "faint", 8.5), 1)
        self.b_export = TacButton("导出清单 CSV", "secondary", height=32)
        self.b_folder = TacButton("打开目标文件夹", "secondary", height=32)
        self.b_undo = TacButton("撤销此批次", "danger", en="UNDO", height=34)
        self.b_export.clicked.connect(self._export)
        self.b_folder.clicked.connect(self._open)
        self.b_undo.clicked.connect(self._undo)
        for b in (self.b_export, self.b_folder, self.b_undo):
            row.addWidget(b)
        bp.body.addLayout(row)
        split.addWidget(bp)

        op = Panel("文件清单", "Manifest", "")
        self.opanel = op
        self.ops_model = OpsModel(self)
        self.ops = QTableView()
        self.ops.setModel(self.ops_model)
        self.ops.verticalHeader().setVisible(False)
        self.ops.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ops.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ops.setAlternatingRowColors(True)
        self.ops.setWordWrap(False)
        oh = self.ops.horizontalHeader()
        # 不用 ResizeToContents：它会在每次数据变化时扫描全部行，上千行时非常慢
        oh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        oh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        oh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        oh.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        oh.resizeSection(0, 90)
        oh.resizeSection(3, 180)
        self.ops.verticalHeader().setDefaultSectionSize(22)
        op.body.addWidget(self.ops, 1)
        split.addWidget(op)
        split.setSizes([300, 400])
        self.root.addWidget(split, 1)
        ctx.files_changed.connect(self.refresh)
        self._batches = []
        self.refresh()

    def on_show(self):
        self.refresh()

    def refresh(self):
        self._batches = self.ctx.journal.batches()
        p = theme.current()
        self.table.setRowCount(len(self._batches))
        for r, b in enumerate(self._batches):
            vals = [f"{b['id']}", (b["created"] or "").replace("T", " "), "复制" if b["operation"] == "copy" else "移动",
                    b["spec"] or "", f"{b['groups']} / {b['total']}", human_size(b["bytes"] or 0),
                    STATUS_LABELS.get(b["status"], b["status"])]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (0, 1, 4, 5):
                    it.setFont(font("mono", 8.5))
                if c == 6:
                    col = {"done": p.ok, "undone": p.ink3, "failed": p.danger, "running": p.warn,
                           "undoing": p.warn, "partially_undone": p.warn, "cancelled": p.ink2}.get(b["status"])
                    if col:
                        it.setForeground(QColor(col))
                    it.setFont(font("cn", 9, 700))
                self.table.setItem(r, c, it)
        self.bpanel.set_meta(f"{len(self._batches)} BATCHES")
        if self._batches and not self.table.selectedItems():
            self.table.selectRow(0)
        # 撤销等操作后选中行没变，itemSelectionChanged 不会触发 → 手动刷新下方文件清单
        self._load_ops()

    def _current(self):
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return None
        r = min(rows)
        return self._batches[r] if r < len(self._batches) else None

    def _update_buttons(self):
        b = self._current()
        ok = b is not None
        self.b_export.setEnabled(ok)
        self.b_folder.setEnabled(ok)
        self.b_undo.setEnabled(ok and b["status"] in ("done", "failed", "cancelled", "partially_undone"))

    def _load_ops(self):
        self._update_buttons()
        b = self._current()
        if not b:
            self.ops_model.set_rows([])
            return
        ops = self.ctx.journal.ops(b["id"])
        self.ops_model.set_rows(ops)
        self.opanel.set_meta(f"{len(ops)} FILES · BATCH #{b['id']}")

    def _export(self):
        b = self._current()
        if not b:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出清单", f"批次{b['id']}.csv", "CSV (*.csv)")
        if path:
            self.ctx.journal.export_csv(b["id"], path)
            self.ctx.status.emit(f"已导出：{path}")

    def _open(self):
        b = self._current()
        if b and os.path.isdir(b["target_root"]):
            open_folder(b["target_root"])

    def _undo(self):
        b = self._current()
        if not b:
            return
        op = "删除复制出来的文件（放入回收站）" if b["operation"] == "copy" else "把文件移回原来的位置"
        if not ask(self, f"撤销批次 #{b['id']}", f"将{op}，共 {b['done'] or b['total']} 个文件。<br>"
                   "如果之后手动改动过这些文件，相关文件会被跳过并在清单里标注。", ok="撤销", danger=True, en="UNDO"):
            return
        pd = ProgressDialog(self, "正在撤销", "ROLLBACK")
        mover = self.ctx.mover

        def job(prog, cancel):
            return mover.undo(b["id"], lambda d, t, c: prog((d, t, c)), cancel)

        w = self.ctx.run(job, lambda v: pd.update_progress(*v), self._undone)
        pd.attach(w)
        pd.exec()

    def _undone(self, res):
        self.ctx.library.apply_moves(res.mapping)
        self.ctx.files_changed.emit()
        msg = f"已恢复 {res.done_files} 个文件。"
        if res.errors:
            msg += "<br><br>" + "<br>".join(res.errors[:10])
        info(self, "撤销完成", msg, "ROLLBACK COMPLETE")
