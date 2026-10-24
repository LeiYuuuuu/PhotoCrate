"""对话框：确认、进度、执行计划预览。"""
from __future__ import annotations

import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QTableView, QVBoxLayout, QWidget)

from ..ops.pathsafe import human_size
from . import theme
from .theme import font
from .widgets.deco import HazardStripe, Panel, SegProgress, StatCard, TacButton, caption
from ..i18n import t as _t


class _Base(QDialog):
    def __init__(self, parent, title: str, en: str, hazard: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        if hazard:
            root.addWidget(HazardStripe(10))
        head = QWidget()
        head.setStyleSheet(f"background: {theme.current().slab};")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(18, 12, 18, 12)
        t = QLabel(title)
        t.setFont(font("cn", 13, 700))
        t.setStyleSheet(f"color: {theme.current().slab_ink};")
        e = QLabel(en.upper())
        e.setFont(font("mono", 8, 400, 2))
        e.setStyleSheet(f"color: {theme.current().accent};")
        hl.addWidget(t)
        hl.addSpacing(10)
        hl.addWidget(e)
        hl.addStretch(1)
        root.addWidget(head)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(12)
        root.addLayout(self.body, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setContentsMargins(18, 0, 18, 16)
        self.buttons.addStretch(1)
        root.addLayout(self.buttons)


def ask(parent, title: str, text: str, ok: str = _t("确定"), cancel: str = _t("取消"), danger: bool = False,
        en: str = "CONFIRM") -> bool:
    d = _Base(parent, title, en, hazard=danger)
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.TextFormat.RichText)
    lab.setMinimumWidth(420)
    d.body.addWidget(lab)
    bc = TacButton(cancel, "secondary")
    bo = TacButton(ok, "danger" if danger else "primary")
    bc.clicked.connect(d.reject)
    bo.clicked.connect(d.accept)
    d.buttons.addWidget(bc)
    d.buttons.addWidget(bo)
    bo.setFocus()
    return d.exec() == QDialog.DialogCode.Accepted


def info(parent, title: str, text: str, en: str = "NOTICE") -> None:
    d = _Base(parent, title, en)
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.TextFormat.RichText)
    lab.setMinimumWidth(420)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    d.body.addWidget(lab)
    b = TacButton(_t("知道了"), "primary")
    b.clicked.connect(d.accept)
    d.buttons.addWidget(b)
    d.exec()


def choose(parent, title: str, text: str, options: list[tuple[str, str, str]], en: str = "SELECT") -> str | None:
    """options: [(返回值, 按钮文字, 按钮风格)]"""
    d = _Base(parent, title, en, hazard=True)
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.TextFormat.RichText)
    lab.setMinimumWidth(460)
    d.body.addWidget(lab)
    result = {"v": None}
    for val, txt, variant in options:
        b = TacButton(txt, variant)

        def make(v):
            return lambda: (result.__setitem__("v", v), d.accept())
        b.clicked.connect(make(val))
        d.buttons.addWidget(b)
    d.exec()
    return result["v"]


class ProgressDialog(_Base):
    """执行移动 / 撤销时的进度窗口。"""

    def __init__(self, parent, title: str, en: str = "PROCESSING"):
        super().__init__(parent, title, en, hazard=True)
        self.setMinimumWidth(520)
        self.bar = SegProgress()
        self.bar.busy = True
        self.cur = QLabel(_t("准备中…"))
        self.cur.setFont(font("mono", 9))
        self.cur.setProperty("role", "dim")
        self.count = QLabel("")
        self.count.setFont(font("display", 11, 600))
        self.body.addWidget(self.bar)
        row = QHBoxLayout()
        row.addWidget(self.cur, 1)
        row.addWidget(self.count)
        self.body.addLayout(row)
        self.body.addWidget(caption(_t("取消会在当前这组照片处理完后停止，不会留下复制了一半的文件。"), "faint", 8.5))
        self.cancel_btn = TacButton(_t("取消"), "secondary")
        self.buttons.addWidget(self.cancel_btn)
        self.worker = None
        self.cancel_btn.clicked.connect(self._cancel)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.bar.tick)
        self._timer.start(60)

    def attach(self, worker) -> None:
        self.worker = worker
        worker.finished.connect(self._finish)
        if worker.isFinished():          # 任务极快、在连接信号前就已结束
            QTimer.singleShot(0, self._finish)

    def update_progress(self, done: int, total: int, current: str) -> None:
        self.bar.set_value(done / total if total else 1)
        self.cur.setText(current)
        self.count.setText(f"{done} / {total}")

    def _cancel(self):
        if self.worker:
            self.worker.cancel()
        self.cancel_btn.setEnabled(False)
        self.cur.setText(_t("正在取消（完成当前一组后停止）…"))

    def _finish(self):
        self._timer.stop()
        self.accept()

    def reject(self):   # 禁止 Esc 直接关掉
        self._cancel()


def run_with_progress(ctx, parent, title: str, en: str, job):
    """在后台运行 job(prog, cancel)，期间显示进度窗口；返回 (结果, 错误信息)。

    结果对话框一定要等进度窗口关闭之后再弹出：任务很快完成时，进度窗口在结果对话框弹出的同时关闭，
    Windows 会把焦点交给别的程序，结果对话框可能落到其他窗口后面、看不见却挡住整个界面。
    """
    pd = ProgressDialog(parent, title, en)
    box: dict = {}
    w = ctx.run(job, lambda v: pd.update_progress(*v), lambda r: box.__setitem__("result", r),
                lambda tb: box.__setitem__("error", tb))
    pd.attach(w)
    pd.exec()
    w.wait(10_000)
    QApplication.processEvents()        # 确保 done / error 信号已经处理
    win = parent.window() if parent is not None else None
    if win is not None:
        win.raise_()
        win.activateWindow()
    return box.get("result"), box.get("error")


class _OpsModel(QAbstractTableModel):
    HEAD = [_t("源文件"), _t("→ 目标位置（相对目标文件夹）")]

    def __init__(self, plan, groups_by_id):
        super().__init__()
        self.rows = []
        for it in plan.items:
            for op in it.ops:
                rel = os.path.relpath(op.dst, plan.target_root)
                self.rows.append((op.src, rel, it.renamed))

    def rowCount(self, parent=QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 2

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and o == Qt.Orientation.Horizontal:
            return self.HEAD[s]
        return None

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        src, rel, renamed = self.rows[idx.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return src if idx.column() == 0 else rel + (_t("   [自动编号]") if renamed and idx.column() == 1 else "")
        if role == Qt.ItemDataRole.ToolTipRole:
            return src if idx.column() == 0 else rel
        if role == Qt.ItemDataRole.FontRole:
            return font("mono", 8.5)
        return None


class PlanDialog(_Base):
    """模拟运行结果：确认后才真正执行。"""

    def __init__(self, parent, plan, journal_hint: bool = True):
        op_name = _t("复制") if plan.operation == "copy" else _t("移动")
        super().__init__(parent, _t('确认{0}计划', op_name), "DRY RUN // REVIEW BEFORE EXECUTE", hazard=True)
        self.plan = plan
        self.resize(980, 640)
        cards = QGridLayout()
        cards.setSpacing(8)
        c1 = StatCard(_t('将{0}照片', op_name), "GROUPS")
        c1.set(f"{len(plan.items)}", unit=_t("组"))
        c2 = StatCard(_t("涉及文件"), "FILES")
        c2.set(f"{plan.file_count}", unit=_t("个"))
        c3 = StatCard(_t("数据量"), "VOLUME")
        c3.set(human_size(plan.total_bytes).split()[0], unit=human_size(plan.total_bytes).split()[1])
        c4 = StatCard(_t("新建文件夹"), "NEW DIRS")
        c4.set(f"{plan.new_dirs}", unit=_t("个"))
        for i, c in enumerate((c1, c2, c3, c4)):
            cards.addWidget(c, 0, i)
        self.body.addLayout(cards)
        lines = [_t('<b>目标文件夹：</b>{0}', plan.target_root), _t('<b>分区规则：</b>{0}', plan.spec_text)]
        if plan.cross_bytes and plan.operation == "move":
            lines.append(_t('<b>跨磁盘：</b>{0} 需要先复制、校验后再删除原文件', human_size(plan.cross_bytes))
                         + (_t('（目标盘剩余 {0}）', human_size(plan.free_bytes)) if plan.free_bytes else ""))
        if plan.skipped_inplace:
            lines.append(_t('<b>已在目标位置：</b>{0} 组（跳过）', plan.skipped_inplace))
        info_lab = QLabel("<br>".join(lines))
        info_lab.setWordWrap(True)
        info_lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(info_lab)
        p = theme.current()
        for w in plan.warnings:
            l = QLabel("▲  " + w)
            l.setStyleSheet(f"color: {p.warn};")
            self.body.addWidget(l)
        for e in plan.errors:
            l = QLabel("■  " + e)
            l.setStyleSheet(f"color: {p.danger}; font-weight: 700;")
            self.body.addWidget(l)
        panel = Panel(_t("操作清单"), "OPERATION MANIFEST", f"{plan.file_count} ENTRIES", pad=6)
        tv = QTableView()
        tv.setModel(_OpsModel(plan, None))
        tv.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tv.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tv.verticalHeader().setVisible(False)
        tv.verticalHeader().setDefaultSectionSize(22)
        tv.setAlternatingRowColors(True)
        tv.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        panel.body.addWidget(tv)
        self.body.addWidget(panel, 1)
        note = caption(_t("执行前会先写入操作日志；完成后可在「记录」页一键撤销。重名文件只会自动加编号，绝不覆盖。"), "faint", 8.5)
        self.body.addWidget(note)
        exp = TacButton(_t("导出清单 CSV"), "secondary")
        exp.clicked.connect(self._export)
        self.buttons.insertWidget(0, exp)
        bc = TacButton(_t("返回修改"), "secondary")
        bc.clicked.connect(self.reject)
        bo = TacButton(_t('确认{0}', op_name), "primary", en="EXECUTE")
        bo.clicked.connect(self.accept)
        bo.setEnabled(plan.ok)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, _t("导出清单"), _t("移动计划.csv"), "CSV (*.csv)")
        if not path:
            return
        import csv
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([_t("源路径"), _t("目标路径"), _t("大小(字节)")])
            for it in self.plan.items:
                for op in it.ops:
                    w.writerow([op.src, op.dst, op.size])
