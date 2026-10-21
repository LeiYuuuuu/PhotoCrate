"""缩略图网格：虚拟化列表（只绘制可见项），几千张也流畅。"""
from __future__ import annotations

import os
import subprocess
import sys
from collections import OrderedDict

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, QUrl,
                            Signal)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFontMetrics, QGuiApplication, QPainter,
                           QPen, QPixmap, QPolygonF)
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QAbstractItemView, QListView, QMenu, QStyle, QStyledItemDelegate

from ...analyzers.builtin import blur_state, classify_color, place_label
from ...core.dates import DATE_SOURCE_LABELS
from .. import theme
from ..theme import font

GroupRole = Qt.ItemDataRole.UserRole + 1
LabelRole = Qt.ItemDataRole.UserRole + 2     # 可选的显示覆盖：{"name": 文件名, "tag": 格式标签}


class PixmapCache:
    def __init__(self, cap: int = 900):
        self.cap = cap
        self._d: OrderedDict[str, QPixmap] = OrderedDict()

    def get(self, path: str | None) -> QPixmap | None:
        if not path:
            return None
        pm = self._d.get(path)
        if pm is not None:
            self._d.move_to_end(path)
            return pm
        pm = QPixmap(path)
        if pm.isNull():
            return None
        self._d[path] = pm
        if len(self._d) > self.cap:
            self._d.popitem(last=False)
        return pm


PIXMAPS = PixmapCache()


class ThumbModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups: list = []

    def set_groups(self, groups: list, labels: dict | None = None) -> None:
        self.beginResetModel()
        self.groups = list(groups)
        self.labels = labels or {}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.groups)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        g = self.groups[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return g.name
        if role == GroupRole:
            return g
        if role == LabelRole:
            return getattr(self, "labels", {}).get(g.gid)
        if role == Qt.ItemDataRole.ToolTipRole:
            return tooltip_for(g)
        return None


def tooltip_for(g, settings: dict | None = None) -> str:
    m = g.meta
    lines = [f"<b>{g.name}</b>", f"<span style='color:gray'>{g.folder}</span>"]
    if len(g.files) > 1:
        lines.append("同组文件：" + "、".join(f.name for f in g.files if f.path != g.primary))
    if g.cross_folder:
        lines.append("<span style='color:gray'>RAW 与 JPG 在不同文件夹（按文件名 + 拍摄时间配对）</span>")
    d = g.date
    dd = g.feats.get("date") or {}
    tz = f" {dd['tz']}" if dd.get("tz") else ""
    lines.append(f"日期：{d.strftime('%Y-%m-%d %H:%M') if d else '未知'}{tz}（{DATE_SOURCE_LABELS.get(g.date_source, '')}）")
    if dd.get("corrected"):
        from ...core.geotime import shift_label
        orig = dd.get("orig_dt", "").replace("T", " ")[:16]
        lines.append(f"时间已校正 {shift_label(int(dd.get('shift') or 0)) or '（仅时区）'}，文件里记录的是 {orig}"
                     + (f" {dd['orig_tz']}" if dd.get("orig_tz") else ""))
    geo = g.feats.get("geo") or {}
    if geo.get("country") or geo.get("has_gps"):
        place = place_label(geo) or "未识别"
        if geo.get("source") == "track":
            from ...core.geotime import MATCH_KIND_LABELS
            gap = int(geo.get("match_gap_s") or 0)
            how = (f"（{MATCH_KIND_LABELS.get(geo.get('match_kind'), '轨迹')}匹配：{geo.get('match_from', '')}，"
                   f"相差 {gap // 60} 分 {gap % 60} 秒）")
        else:
            how = {"manual": "（手动指定）", "inferred": "（按时间推测）"}.get(geo.get("source"), "")
        lines.append(f"地点：{place}{how}")
    if geo.get("lat") is not None and geo.get("source") in ("gps", "track"):
        lines.append(f"GPS：{geo['lat']:.5f}, {geo['lon']:.5f}" + ("（匹配）" if geo.get("source") == "track" else ""))
    cam = g.feat("camera", "model")
    if cam:
        lines.append(f"相机：{cam}")
    if g.feat("camera", "lens"):
        lines.append(f"镜头：{g.feat('camera', 'lens')}")
    exp = []
    if m.get("focal"):
        exp.append(f"{m['focal']:.0f}mm")
    if m.get("fnumber"):
        exp.append(f"f/{m['fnumber']:.1f}")
    if m.get("iso"):
        exp.append(f"ISO {m['iso']:.0f}")
    if exp:
        lines.append("参数：" + "  ".join(exp))
    if settings is not None:
        c = classify_color(g.feats.get("color"), settings)
        if c:
            lines.append(f"色调：{c['warmth']} · {c['tone']} · 主色{c['main']}")
    sc = g.feat("blur", "score")
    if sc is not None:
        lines.append(f"清晰度分数：{sc:.0f}")
    return "<br>".join(lines)


class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cell = QSize(168, 206)
        self.blur_threshold = 60.0
        self.show_score = False
        self.highlight_blurry = False

    def sizeHint(self, option, index):
        return self.cell

    def paint(self, qp: QPainter, option, index):
        p = theme.current()
        g = index.data(GroupRole)
        if g is None:
            return
        qp.save()
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRect(option.rect).adjusted(5, 5, -5, -5)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        qp.fillRect(r, QColor(p.surface))
        img = QRect(r.left() + 6, r.top() + 6, r.width() - 12, r.width() - 12)
        qp.fillRect(img, QColor(p.surface2))
        pm = PIXMAPS.get(g.thumb)
        if pm is not None:
            sc = pm.scaled(img.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            x = img.left() + (img.width() - sc.width()) // 2
            y = img.top() + (img.height() - sc.height()) // 2
            qp.drawPixmap(x, y, sc)
        else:
            qp.setPen(QColor(p.ink3))
            qp.setFont(font("mono", 8))
            qp.drawText(img, Qt.AlignmentFlag.AlignCenter, "NO PREVIEW")
        # 标签
        tags = []
        over = index.data(LabelRole) or {}
        if over.get("tag"):
            tags.append((over["tag"], p.slab, p.slab_ink))
        elif g.kind not in ("jpeg",):
            tags.append((g.kind_label, p.slab, p.slab_ink))
        if g.date_is_guess:
            tags.append(("日期推测", p.warn, "#FFFFFF"))
        elif g.date is None:
            tags.append(("无日期", p.warn, "#FFFFFF"))
        if self.highlight_blurry and blur_state(g, self.blur_threshold) == "blurry":
            tags.append(("模糊", p.danger, "#FFFFFF"))
        src = g.feat("geo", "source")
        if src == "inferred":
            tags.append(("地点推测", p.accent2, "#FFFFFF"))
        elif src == "track":
            tags.append(("轨迹匹配", p.ok, "#FFFFFF"))
        elif src == "manual":
            tags.append(("手动地点", p.ink2, p.surface))
        if g.feat("date", "corrected"):
            tags.append(("时间校正", p.accent, p.accent_ink))
        ty = img.top() + 4
        qp.setFont(font("mono", 7.5, 600))
        fm = QFontMetrics(qp.font())
        for text, bg, fg in tags:
            tw = fm.horizontalAdvance(text) + 10
            tr = QRect(img.left() + 4, ty, tw, 16)
            qp.fillRect(tr, QColor(bg))
            qp.setPen(QColor(fg))
            qp.drawText(tr, Qt.AlignmentFlag.AlignCenter, text)
            ty += 19
        if self.show_score:
            sc = g.feat("blur", "score")
            st = blur_state(g, self.blur_threshold)
            txt = "—" if sc is None else f"{sc:.0f}"
            qp.setFont(font("display", 10, 700))
            tw = QFontMetrics(qp.font()).horizontalAdvance(txt) + 14
            tr = QRect(img.right() - tw - 3, img.bottom() - 21, tw, 18)
            qp.fillRect(tr, QColor(p.danger if st == "blurry" else (p.slab if st == "sharp" else p.ink3)))
            qp.setPen(QColor("#FFFFFF"))
            qp.drawText(tr, Qt.AlignmentFlag.AlignCenter, txt)
        # 文件名与日期
        ty = img.bottom() + 6
        qp.setFont(font("cn", 8.5, 500))
        qp.setPen(QColor(p.ink))
        name = QFontMetrics(qp.font()).elidedText(over.get("name") or g.name, Qt.TextElideMode.ElideMiddle,
                                                  r.width() - 14)
        qp.drawText(QRect(r.left() + 7, ty, r.width() - 14, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        qp.setFont(font("mono", 7.5))
        qp.setPen(QColor(p.ink3))
        d = g.date
        ds = d.strftime("%Y-%m-%d %H:%M") if d else "DATE UNKNOWN"
        qp.drawText(QRect(r.left() + 7, ty + 16, r.width() - 14, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, ds)
        # 边框
        if selected:
            qp.setPen(QPen(QColor(p.accent), 3))
            qp.drawRect(QRectF(r).adjusted(1.5, 1.5, -1.5, -1.5))
            qp.setPen(QPen(QColor(p.ink), 1))
            qp.drawRect(QRectF(r).adjusted(0, 0, -1, -1))
            tri = QPolygonF([QPointF(r.right() - 22, r.top()), QPointF(r.right() + 1, r.top()),
                             QPointF(r.right() + 1, r.top() + 23)])
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(QColor(p.accent))
            qp.drawPolygon(tri)
            qp.setPen(QPen(QColor(p.accent_ink), 1.6))
            qp.drawLine(QPointF(r.right() - 11, r.top() + 6), QPointF(r.right() - 8, r.top() + 9))
            qp.drawLine(QPointF(r.right() - 8, r.top() + 9), QPointF(r.right() - 3, r.top() + 3))
        elif hover:
            qp.setPen(QPen(QColor(p.ink), 1))
            qp.drawRect(QRectF(r).adjusted(0, 0, -1, -1))
        else:
            qp.setPen(QPen(QColor(p.line), 1))
            qp.drawRect(QRectF(r).adjusted(0, 0, -1, -1))
        qp.restore()


class ThumbGrid(QListView):
    selection_changed = Signal(list)    # 选中的 gid 列表

    def __init__(self, parent=None, selectable: bool = True):
        super().__init__(parent)
        self.model_ = ThumbModel(self)
        self.delegate = ThumbDelegate(self)
        self.setModel(self.model_)
        self.setItemDelegate(self.delegate)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWrapping(True)
        self.setSpacing(0)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection if selectable
                              else QAbstractItemView.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.doubleClicked.connect(self._open)
        self.selectionModel().selectionChanged.connect(self._sel_changed)
        self.settings_snapshot: dict | None = None

    def set_groups(self, groups: list, labels: dict | None = None) -> None:
        self.model_.set_groups(groups, labels)

    def set_cell_width(self, w: int) -> None:
        self.delegate.cell = QSize(w, w + 38)
        self.model_.layoutChanged.emit()

    def selected_gids(self) -> list[int]:
        return [self.model_.groups[i.row()].gid for i in self.selectionModel().selectedIndexes()]

    def select_gids(self, gids: set[int]) -> None:
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        sel = QItemSelection()
        for row, g in enumerate(self.model_.groups):
            if g.gid in gids:
                idx = self.model_.index(row)
                sel.select(idx, idx)
        self.selectionModel().select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _sel_changed(self, *_):
        self.selection_changed.emit(self.selected_gids())

    def _group_at(self, pos: QPoint):
        idx = self.indexAt(pos)
        return idx.data(GroupRole) if idx.isValid() else None

    def _open(self, index):
        g = index.data(GroupRole)
        if g:
            QDesktopServices.openUrl(QUrl.fromLocalFile(g.primary))

    def _menu(self, pos: QPoint):
        g = self._group_at(pos)
        if g is None:
            return
        m = QMenu(self)
        a1 = QAction("打开图片", m)
        a1.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(g.primary)))
        a2 = QAction("在文件夹中显示", m)
        a2.triggered.connect(lambda: reveal_in_explorer(g.primary))
        a3 = QAction("复制文件路径", m)
        a3.triggered.connect(lambda: QGuiApplication.clipboard().setText(g.primary))
        for a in (a1, a2, a3):
            m.addAction(a)
        m.exec(self.viewport().mapToGlobal(pos))

    def viewportEvent(self, e):
        if e.type() == e.Type.ToolTip:
            g = self._group_at(e.pos())
            if g is not None:
                from PySide6.QtWidgets import QToolTip
                QToolTip.showText(e.globalPos(), tooltip_for(g, self.settings_snapshot), self)
                return True
        return super().viewportEvent(e)


def reveal_in_explorer(path: str) -> None:
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
    except Exception:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


def open_folder(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
