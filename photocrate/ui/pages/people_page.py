"""08 人物：自动把同一个人的脸归成一组；起名后以后的照片自动认出，并可按人物分区。"""
from __future__ import annotations

import base64

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QSplitter, QVBoxLayout, QWidget)

from ...ai import models
from .. import theme
from ..dialogs import ask, info
from ..widgets.deco import Panel, TacButton, caption
from ..widgets.thumbgrid import ThumbGrid
from .base import Page

LONERS = "__loners__"


def face_pixmap(b64: str, size: int = 96) -> QPixmap:
    pm = QPixmap()
    if b64:
        pm.loadFromData(QByteArray(base64.b64decode(b64)), "JPG")
    if pm.isNull():
        pm = QPixmap(size, size)
        pm.fill(theme.current().c("surface2"))
    return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)


class PeoplePage(Page):
    page_id = "people"
    index = "08"
    title = "人物"
    en = "People // face clustering"
    sub = ("同一个人的脸自动归成一组。给一组起名后，以后扫描到的照片会自动认出；起相同的名字 = 合并成一个人。"
           "分区编排里可用「人物」条件按人归档。人脸特征只保存在本机。")

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        lp = Panel("人物", "People", "")
        self.list_panel = lp
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(QSize(88, 88))
        self.list.setGridSize(QSize(112, 122))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        self.list.currentItemChanged.connect(lambda *_: self._selected())
        lp.body.addWidget(self.list, 1)
        nr = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("给选中的人起名 / 改名（与已有名字相同会合并）")
        self.name.returnPressed.connect(self.apply_name)
        nr.addWidget(self.name, 1)
        self.b_name = TacButton("命名", "primary", en="NAME", height=30)
        self.b_name.clicked.connect(self.apply_name)
        nr.addWidget(self.b_name)
        lp.body.addLayout(nr)
        br = QHBoxLayout()
        self.b_forget = TacButton("忘记这个名字", "secondary", height=28)
        self.b_forget.clicked.connect(self.forget)
        br.addWidget(self.b_forget)
        br.addStretch(1)
        br.addWidget(QLabel("相似度"))
        self.thr = QDoubleSpinBox()
        self.thr.setRange(0.25, 0.7)
        self.thr.setSingleStep(0.02)
        self.thr.setDecimals(2)
        self.thr.setValue(float(ctx.settings.get("face_threshold", 0.42)))
        self.thr.setToolTip("两张脸的特征相似度达到多少算同一个人。调高 = 更严格（同一个人可能被拆成几组），"
                            "调低 = 更宽松（不同的人可能混在一起）。")
        br.addWidget(self.thr)
        lp.body.addLayout(br)
        self.hint = caption("", "faint", 8)
        lp.body.addWidget(self.hint)
        lp.setMinimumWidth(430)
        split.addWidget(lp)

        rp = Panel("照片", "Photos of this person", "")
        self.photo_panel = rp
        self.grid = ThumbGrid(selectable=True)
        self.grid.set_cell_width(150)
        rp.body.addWidget(self.grid, 1)
        rr = QHBoxLayout()
        self.b_remove = TacButton("选中的照片不是 TA", "secondary", height=30)
        self.b_remove.clicked.connect(self.remove_selected)
        rr.addWidget(self.b_remove)
        rr.addStretch(1)
        rr.addWidget(caption("误认时选中照片点左边按钮，以后不会再把这张脸归给 TA。", "faint", 8))
        rp.body.addLayout(rr)
        split.addWidget(rp)
        split.setSizes([480, 900])
        self.root.addWidget(split, 1)

        self._regroup = QTimer(self)
        self._regroup.setSingleShot(True)
        self._regroup.setInterval(350)
        self._regroup.timeout.connect(self._apply_thr)
        self.thr.valueChanged.connect(lambda _: self._regroup.start())
        ctx.library_changed.connect(self.refresh)
        ctx.files_changed.connect(self.refresh)
        self._cur = None

    def on_show(self):
        self.refresh()

    # ---- 列表 ------------------------------------------------------------
    def refresh(self):
        lib = self.ctx.library
        ai = lib.ai
        keep = self._cur
        self.list.blockSignals(True)
        self.list.clear()
        for c in ai.people:
            cov = c.cover
            it = QListWidgetItem(QIcon(face_pixmap(cov.crop if cov else "")), f"{c.name} · {len(c.gids)}")
            it.setData(Qt.ItemDataRole.UserRole, c.cid)
            it.setToolTip(f"{c.name}：{len(c.faces)} 张脸，{len(c.gids)} 张照片" + ("" if c.named else "（未命名）"))
            if c.named:
                it.setForeground(theme.current().c("accent2"))
            self.list.addItem(it)
        if ai.loners:
            cov = max(ai.loners, key=lambda f: f.size)
            it = QListWidgetItem(QIcon(face_pixmap(cov.crop)), f"单次出现 · {len({f.gid for f in ai.loners})}")
            it.setData(Qt.ItemDataRole.UserRole, LONERS)
            it.setToolTip("只在一张照片里出现过的脸（不单独成组；分区时算“其他人”）")
            self.list.addItem(it)
        self.list.blockSignals(False)
        s = ai.stats(lib.groups)
        self.list_panel.set_meta(f"{len(ai.people)} PEOPLE · {s['named']} NAMED · {s['faces']} FACES")
        if not models.ready("face"):
            self.hint.setText("人脸模型还没有下载：到「AI 搜图」页下载“人脸检测 + 识别”，然后分析照片。")
        elif s["faces_done"] < s["photos"]:
            self.hint.setText(f"还有 {s['photos'] - s['faces_done']} 张照片没做人脸检测：到「AI 搜图」页点“分析全部照片”。")
        else:
            self.hint.setText(f"共检测到 {s['faces']} 张脸。只在一张照片里出现的脸不单独成组。")
        row = 0
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == keep:
                row = i
        if self.list.count():
            self.list.setCurrentRow(row)
        self._selected()

    def _cid(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _selected(self):
        lib = self.ctx.library
        cid = self._cid()
        self._cur = cid
        if cid == LONERS:
            faces = lib.ai.loners
            title = "只出现一次的脸"
            named = False
        else:
            c = lib.ai.cluster(cid) if cid else None
            faces = c.faces if c else []
            title = c.name if c else ""
            named = bool(c and c.named)
        gids = []
        for f in faces:
            if f.gid not in gids:
                gids.append(f.gid)
        groups = [g for gid in gids if (g := lib.by_gid(gid)) is not None and g.files]
        groups.sort(key=lambda g: (g.date is None, g.date or 0))
        self.grid.settings_snapshot = self.ctx.settings.snapshot()
        self.grid.set_groups(groups)
        self.photo_panel.set_meta(f"{len(groups)} ITEMS · {title}")
        self.name.setText(title if named else "")
        ok = cid not in (None, LONERS)
        self.b_name.setEnabled(ok)
        self.name.setEnabled(ok)
        self.b_forget.setEnabled(named)
        self.b_remove.setEnabled(ok)

    # ---- 操作 ------------------------------------------------------------
    def _recluster(self, msg: str):
        lib = self.ctx.library
        lib.ai.recluster(lib.groups)
        self.ctx.status.emit(msg)
        self.refresh()

    def _apply_thr(self):
        self.ctx.set_setting("face_threshold", round(float(self.thr.value()), 2))
        self._recluster(f"人物重新分组：相似度 {self.thr.value():.2f}")

    def apply_name(self):
        cid = self._cid()
        name = self.name.text().strip()
        if cid in (None, LONERS) or not name:
            return
        lib = self.ctx.library
        exists = any(c.named and c.name == name and c.cid != cid for c in lib.ai.people)
        if exists and not ask(self, "合并人物", f"已经有一个叫“{name}”的人。把这一组合并进去吗？", ok="合并"):
            return
        pid = lib.ai.name_cluster(cid, name)
        self._cur = pid
        self._recluster(f"已命名：{name}")

    def forget(self):
        cid = self._cid()
        c = self.ctx.library.ai.cluster(cid)
        if c is None or not c.named:
            return
        if ask(self, "忘记名字", f"忘记“{c.name}”？这些脸会回到未命名的分组（照片文件不受影响）。", ok="忘记", danger=True):
            self.ctx.library.ai.forget_person(cid)
            self._cur = None
            self._recluster(f"已忘记：{c.name}")

    def remove_selected(self):
        cid = self._cid()
        c = self.ctx.library.ai.cluster(cid)
        sel = set(self.grid.selected_gids())
        if c is None or not sel:
            info(self, "先选照片", "在右边选中认错的照片（可多选），再点这个按钮。")
            return
        keys = [f.key for f in c.faces if f.gid in sel]
        if not c.named:
            # 未命名的组没有固定 id：先起个临时名字不合适，直接提示
            info(self, "先命名", "未命名的分组会随参数变化重新计算。请先给这个人起名，再更正认错的照片；"
                 "或者调高“相似度”让分组更严格。")
            return
        self.ctx.library.ai.remove_faces(cid, keys)
        self._recluster(f"{len(sel)} 张照片已从“{c.name}”中移除")
