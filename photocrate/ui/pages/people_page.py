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
from ...i18n import t as _t

LONERS = "__loners__"
NONFACE = "__nonface__"


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
    title = _t("人物")
    en = "People // face clustering"
    sub = (_t("同一个人的脸自动归成一组。给一组起名后，以后扫描到的照片会自动认出；起相同的名字 = 合并成一个人。"
           "分区编排里可用「人物」条件按人归档。人脸特征只保存在本机。"))

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        lp = Panel(_t("人物"), "People", "")
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
        self.name.setPlaceholderText(_t("给选中的人起名 / 改名（与已有名字相同会合并）"))
        self.name.returnPressed.connect(self.apply_name)
        nr.addWidget(self.name, 1)
        self.b_name = TacButton(_t("命名"), "primary", en="NAME", height=30)
        self.b_name.clicked.connect(self.apply_name)
        nr.addWidget(self.b_name)
        lp.body.addLayout(nr)
        br = QHBoxLayout()
        self.b_forget = TacButton(_t("忘记这个名字"), "secondary", height=28)
        self.b_forget.clicked.connect(self.forget)
        br.addWidget(self.b_forget)
        br.addStretch(1)
        br.addWidget(QLabel(_t("相似度")))
        self.thr = QDoubleSpinBox()
        self.thr.setRange(0.25, 0.7)
        self.thr.setSingleStep(0.02)
        self.thr.setDecimals(2)
        self.thr.setValue(float(ctx.settings.get("face_threshold", 0.42)))
        self.thr.setToolTip(_t("两张脸的特征相似度达到多少算同一个人。调高 = 更严格（同一个人可能被拆成几组），"
                            "调低 = 更宽松（不同的人可能混在一起）。"))
        br.addWidget(self.thr)
        lp.body.addLayout(br)
        self.hint = caption("", "faint", 8)
        lp.body.addWidget(self.hint)
        lp.setMinimumWidth(430)
        split.addWidget(lp)

        rp = Panel(_t("照片"), "Photos of this person", "")
        self.photo_panel = rp
        self.grid = ThumbGrid(selectable=True)
        self.grid.set_cell_width(150)
        rp.body.addWidget(self.grid, 1)
        rr = QHBoxLayout()
        self.b_remove = TacButton(_t("选中的照片不是 TA"), "secondary", height=30)
        self.b_remove.clicked.connect(self.remove_selected)
        rr.addWidget(self.b_remove)
        self.b_notface = TacButton(_t("不是人脸"), "secondary", height=30)
        self.b_notface.setToolTip(_t("选中的照片里这张“脸”不是真人（动物、玩偶、海报……）；不选照片 = 整组都不是"))
        self.b_notface.clicked.connect(self.toggle_face)
        rr.addWidget(self.b_notface)
        rr.addStretch(1)
        self.fix_hint = caption(_t("误认时选中照片点左边按钮，以后不会再把这张脸归给 TA。"), "faint", 8)
        rr.addWidget(self.fix_hint)
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
            it.setToolTip(_t('{0}：{1} 张脸，{2} 张照片', c.name, len(c.faces), len(c.gids)) + ("" if c.named else _t("（未命名）")))
            if c.named:
                it.setForeground(theme.current().c("accent2"))
            self.list.addItem(it)
        if ai.loners:
            cov = max(ai.loners, key=lambda f: f.size)
            it = QListWidgetItem(QIcon(face_pixmap(cov.crop)), _t('单次出现 · {0}', len({f.gid for f in ai.loners})))
            it.setData(Qt.ItemDataRole.UserRole, LONERS)
            it.setToolTip(_t("只在一张照片里出现过的脸（不单独成组；分区时算“其他人”）"))
            self.list.addItem(it)
        if ai.nonfaces:
            cov = max(ai.nonfaces, key=lambda f: f.size)
            it = QListWidgetItem(QIcon(face_pixmap(cov.crop)), _t('不是人脸 · {0}', len({f.gid for f in ai.nonfaces})))
            it.setData(Qt.ItemDataRole.UserRole, NONFACE)
            it.setForeground(theme.current().c("ink3"))
            it.setToolTip(_t("被认出“不是真人”的脸：动物、玩偶、手办、动漫人物、雕像等。不参与人物分组；认错了可以恢复。"))
            self.list.addItem(it)
        self.list.blockSignals(False)
        s = ai.stats(lib.groups)
        self.list_panel.set_meta(_t("{0} 人 · 已命名 {1} · {2} 张人脸", len(ai.people), s['named'], s['faces']))
        if not models.ready("face"):
            self.hint.setText(_t("人脸模型还没有下载：到「AI 搜图」页下载“人脸检测 + 识别”，然后分析照片。"))
        elif s["faces_done"] < s["photos"]:
            self.hint.setText(_t('还有 {0} 张照片没做人脸检测：到「AI 搜图」页点“分析全部照片”。', s['photos'] - s['faces_done']))
        else:
            extra = _t('其中 {0} 张不是真人的脸（动物、玩偶等），已排除。', s['nonfaces']) if s["nonfaces"] else ""
            self.hint.setText(_t('共检测到 {0} 张脸。{1}只在一张照片里出现的脸不单独成组。', s['faces'], extra))
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
            title = _t("只出现一次的脸")
            named = False
        elif cid == NONFACE:
            faces = lib.ai.nonfaces
            title = _t("不是人脸（不参与分组）")
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
        self.photo_panel.set_meta(_t("{0} 项 · {1}", len(groups), title))
        self.name.setText(title if named else "")
        ok = cid not in (None, LONERS, NONFACE)
        self.b_name.setEnabled(ok)
        self.name.setEnabled(ok)
        self.b_forget.setEnabled(named)
        self.b_remove.setEnabled(ok)
        self.b_remove.setVisible(cid != NONFACE)
        self.b_notface.setEnabled(cid is not None)
        self.b_notface.setText(_t("是人脸（恢复）") if cid == NONFACE else _t("不是人脸"))
        self.fix_hint.setText(_t("选中认错的照片点“是人脸”，这张脸会重新参与人物分组。") if cid == NONFACE else
                              _t("认错人：选中照片点“不是 TA”。不是真人（动物、玩偶…）：点“不是人脸”。"))

    # ---- 操作 ------------------------------------------------------------
    def _recluster(self, msg: str):
        lib = self.ctx.library
        lib.ai.recluster(lib.groups)
        self.ctx.status.emit(msg)
        self.refresh()

    def _apply_thr(self):
        self.ctx.set_setting("face_threshold", round(float(self.thr.value()), 2))
        self._recluster(_t('人物重新分组：相似度 {0:.2f}', self.thr.value()))

    def apply_name(self):
        cid = self._cid()
        name = self.name.text().strip()
        if cid in (None, LONERS) or not name:
            return
        lib = self.ctx.library
        exists = any(c.named and c.name == name and c.cid != cid for c in lib.ai.people)
        if exists and not ask(self, _t("合并人物"), _t('已经有一个叫“{0}”的人。把这一组合并进去吗？', name), ok=_t("合并")):
            return
        pid = lib.ai.name_cluster(cid, name)
        self._cur = pid
        self._recluster(_t('已命名：{0}', name))

    def forget(self):
        cid = self._cid()
        c = self.ctx.library.ai.cluster(cid)
        if c is None or not c.named:
            return
        if ask(self, _t("忘记名字"), _t('忘记“{0}”？这些脸会回到未命名的分组（照片文件不受影响）。', c.name), ok=_t("忘记"), danger=True):
            self.ctx.library.ai.forget_person(cid)
            self._cur = None
            self._recluster(_t('已忘记：{0}', c.name))

    def _faces_of(self, cid):
        ai = self.ctx.library.ai
        if cid == LONERS:
            return ai.loners
        if cid == NONFACE:
            return ai.nonfaces
        c = ai.cluster(cid)
        return c.faces if c else []

    def toggle_face(self):
        """“不是人脸”：把选中照片里属于这一组的脸排除；在“不是人脸”组里则是恢复。"""
        cid = self._cid()
        if cid is None:
            return
        faces = self._faces_of(cid)
        sel = set(self.grid.selected_gids())
        restore = cid == NONFACE
        if sel:
            keys = [f.key for f in faces if f.gid in sel]
        else:
            if restore:
                info(self, _t("先选照片"), _t("在右边选中认错的照片（可多选），再点“是人脸（恢复）”。"))
                return
            it = self.list.currentItem()
            label = it.text().split(" · ")[0] if it else ""
            if not ask(self, _t("整组都不是人脸"), _t('没有选中照片。把“{0}”这一组的 {1} 张脸都标成“不是人脸”吗？以后不再参与人物分组（随时可以在“不是人脸”里恢复）。', label, len(faces)), ok=_t("都不是人脸")):
                return
            keys = [f.key for f in faces]
        if not keys:
            return
        self.ctx.library.ai.mark_faces(keys, human=restore)
        marked = set(keys)
        n = len({f.gid for f in faces if f.key in marked})
        self._recluster(_t('{0} 张照片里的脸已恢复为人脸', n) if restore else _t('{0} 张照片里的脸已标为“不是人脸”', n))

    def remove_selected(self):
        cid = self._cid()
        c = self.ctx.library.ai.cluster(cid)
        sel = set(self.grid.selected_gids())
        if c is None or not sel:
            info(self, _t("先选照片"), _t("在右边选中认错的照片（可多选），再点这个按钮。"))
            return
        keys = [f.key for f in c.faces if f.gid in sel]
        if not c.named:
            # 未命名的组没有固定 id：先起个临时名字不合适，直接提示
            info(self, _t("先命名"), _t("未命名的分组会随参数变化重新计算。请先给这个人起名，再更正认错的照片；"
                 "或者调高“相似度”让分组更严格。"))
            return
        self.ctx.library.ai.remove_faces(cid, keys)
        self._recluster(_t('{0} 张照片已从“{1}”中移除', len(sel), c.name))
