"""「相册」的两种网格（都只画可见部分，几万张也流畅）：

- PhotoGrid：按段（每天 / 每月 / 一个相簿）排列的正方形缩略图，段头是日期 + 地点，类似 iOS 照片的「图库」。
  单击选择（Ctrl / Shift 多选），双击或回车打开；1–5 打星、F 收藏、Ctrl+滚轮 调整每行几张。
- CardGrid：卡片（封面 + 标题 + 张数），用于「年 / 月」视图和「相簿」页，同样可以分段（人物、地点、旅行…）。
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractScrollArea

from .. import theme
from ..theme import font
from ..widgets.thumbgrid import HOOKS, PIXMAPS
from ...i18n import t as _t

MARGIN = 18
GAP = 3


@dataclass
class GridSection:
    key: str
    title: str
    sub: str
    items: list                      # PhotoGrid：PhotoGroup；CardGrid：Card
    header: bool = True


@dataclass
class Card:
    key: str
    title: str
    sub: str
    count: int
    cover: object | None = None      # PhotoGroup
    icon: str = ""
    payload: object = None


def draw_cover(qp: QPainter, rect: QRectF, g, radius: float = 0.0) -> bool:
    """把照片的缩略图居中裁成 rect 的比例画上去。没有缩略图返回 False。"""
    pm = PIXMAPS.get(getattr(g, "thumb", None)) if g is not None else None
    if pm is None:
        return False
    pw, ph = pm.width(), pm.height()
    if pw <= 0 or ph <= 0:
        return False
    r = rect.width() / max(1.0, rect.height())
    if pw / ph > r:
        sw, sh = ph * r, ph
    else:
        sw, sh = pw, pw / r
    src = QRectF((pw - sw) / 2, (ph - sh) / 2, sw, sh)
    if radius:
        qp.save()
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        qp.setClipPath(path)
        qp.drawPixmap(rect, pm, src)
        qp.restore()
    else:
        qp.drawPixmap(rect, pm, src)
    return True


class _SectionGrid(QAbstractScrollArea):
    """分段网格的公共部分：布局、滚动、命中测试。子类定义 cell 大小和怎么画。"""
    header_h = 54

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sections: list[GridSection] = []
        self._tops: list[int] = []
        self._cols = 4
        self._cw = 100.0
        self._ch = 100.0
        self._total = 0
        self._hover: tuple[int, int] | None = None
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalScrollBar().setSingleStep(40)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    # 子类实现
    def _cell_size(self, avail: float) -> tuple[int, float, float]:
        raise NotImplementedError

    def _paint_cell(self, qp: QPainter, rect: QRectF, si: int, ii: int) -> None:
        raise NotImplementedError

    # ---- 布局 ----
    def set_sections(self, sections: list[GridSection], keep_scroll: bool = False) -> None:
        y = self.verticalScrollBar().value()
        self.sections = sections
        self._hover = None
        self._relayout()
        self.verticalScrollBar().setValue(y if keep_scroll else 0)
        self.viewport().update()

    def _relayout(self) -> None:
        avail = max(100.0, self.viewport().width() - 2 * MARGIN)
        self._cols, self._cw, self._ch = self._cell_size(avail)
        y = 8
        self._tops = []
        for s in self.sections:
            self._tops.append(y)
            rows = -(-len(s.items) // self._cols)
            y += (self.header_h if s.header else 0) + rows * (self._ch + GAP) + (14 if s.header else 6)
        self._total = int(y + 20)
        sb = self.verticalScrollBar()
        sb.setRange(0, max(0, self._total - self.viewport().height()))
        sb.setPageStep(self.viewport().height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def _cell_rect(self, si: int, ii: int) -> QRectF:
        s = self.sections[si]
        row, col = divmod(ii, self._cols)
        y = self._tops[si] + (self.header_h if s.header else 0) + row * (self._ch + GAP)
        return QRectF(MARGIN + col * (self._cw + GAP), y, self._cw, self._ch)

    def _at(self, pos: QPoint) -> tuple[int, int] | None:
        y = pos.y() + self.verticalScrollBar().value()
        si = bisect_right(self._tops, y) - 1
        if si < 0 or si >= len(self.sections):
            return None
        s = self.sections[si]
        top = self._tops[si] + (self.header_h if s.header else 0)
        if y < top:
            return None
        row = int((y - top) // (self._ch + GAP))
        col = int((pos.x() - MARGIN) // (self._cw + GAP))
        if not 0 <= col < self._cols or pos.x() < MARGIN:
            return None
        ii = row * self._cols + col
        if ii >= len(s.items):
            return None
        if not self._cell_rect(si, ii).contains(QPointF(pos.x(), y)):
            return None
        return si, ii

    def scroll_to(self, si: int, ii: int = 0) -> None:
        if not 0 <= si < len(self.sections):
            return
        r = self._cell_rect(si, ii) if self.sections[si].items else QRectF(0, self._tops[si], 1, 1)
        sb = self.verticalScrollBar()
        top = self._tops[si] if ii < self._cols else r.top() - 8
        if r.top() < sb.value() or r.bottom() > sb.value() + self.viewport().height():
            sb.setValue(int(top if r.top() < sb.value() or ii < self._cols else r.bottom() - self.viewport().height() + 8))
        self.viewport().update()

    def section_index(self, key: str) -> int:
        return next((i for i, s in enumerate(self.sections) if s.key == key), -1)

    # ---- 绘制 ----
    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self.viewport())
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        qp.fillRect(self.viewport().rect(), QColor(p.bg))
        y0 = self.verticalScrollBar().value()
        h = self.viewport().height()
        qp.translate(0, -y0)
        if not self.sections or not any(s.items for s in self.sections):
            self._paint_empty(qp, y0)
        first = max(0, bisect_right(self._tops, y0) - 1)
        for si in range(first, len(self.sections)):
            top = self._tops[si]
            if top > y0 + h:
                break
            s = self.sections[si]
            if s.header:
                self._paint_header(qp, QRectF(MARGIN, top, self.viewport().width() - 2 * MARGIN, self.header_h), s)
            base = top + (self.header_h if s.header else 0)
            r0 = max(0, int((y0 - base) // (self._ch + GAP)))
            r1 = int((y0 + h - base) // (self._ch + GAP)) + 1
            for ii in range(r0 * self._cols, min(len(s.items), (r1 + 1) * self._cols)):
                self._paint_cell(qp, self._cell_rect(si, ii), si, ii)
        qp.end()

    empty_text = ""

    def _paint_empty(self, qp: QPainter, y0: int) -> None:
        if not self.empty_text:
            return
        p = theme.current()
        qp.setPen(QColor(p.ink3))
        qp.setFont(font("cn", 11))
        qp.drawText(QRectF(0, y0 + 60, self.viewport().width(), 200),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, self.empty_text)

    def _paint_header(self, qp: QPainter, r: QRectF, s: GridSection) -> None:
        p = theme.current()
        f1 = font("cn", 14, 800)
        qp.setFont(f1)
        qp.setPen(QColor(p.ink))
        tw = QFontMetrics(f1).horizontalAdvance(s.title)
        qp.drawText(QRectF(r.left(), r.top() + 10, tw + 4, 26), Qt.AlignmentFlag.AlignVCenter, s.title)
        qp.fillRect(QRectF(r.left(), r.top() + 40, 22, 2), QColor(p.accent))
        x = r.left() + tw + 14
        if s.sub:
            qp.setFont(font("cn", 9.5))
            qp.setPen(QColor(p.ink2))
            qp.drawText(QRectF(x, r.top() + 10, r.width() - tw - 120, 26), Qt.AlignmentFlag.AlignVCenter, s.sub)
        qp.setFont(font("mono", 7.5, 400, 1.0))
        qp.setPen(QColor(p.ink3))
        qp.drawText(QRectF(r.right() - 100, r.top() + 10, 100, 26),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{len(s.items):,}")

    def leaveEvent(self, e):
        self._hover = None
        self.viewport().update()


# ---------------------------------------------------------------------------
# 照片网格
# ---------------------------------------------------------------------------

class PhotoGrid(_SectionGrid):
    activated = Signal(int)              # 打开某张（gid）
    selection_changed = Signal()
    menu_requested = Signal(QPoint, list)   # 全局坐标, 选中的照片
    zoom_changed = Signal(int)
    fav_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cols_pref = 5
        self.selected: set[int] = set()
        self.current: tuple[int, int] | None = None
        self._anchor: tuple[int, int] | None = None
        self.is_fav = lambda g: False
        self.badges: dict[int, str] = {}     # gid → 左上角的小标签（例如搜索时“文字”）
        self.empty_text = _t("这里还没有照片")

    def _cell_size(self, avail):
        n = max(2, min(14, self.cols_pref))
        c = (avail - (n - 1) * GAP) / n
        return n, c, c

    def set_columns(self, n: int) -> None:
        n = max(2, min(14, n))
        if n == self.cols_pref:
            return
        # 缩放后保持当前看着的那一段
        anchor = self._at(QPoint(MARGIN + 2, 2)) or (self.current if self.current else None)
        self.cols_pref = n
        self._relayout()
        if anchor:
            r = self._cell_rect(*anchor)
            self.verticalScrollBar().setValue(int(r.top()))
        self.viewport().update()
        self.zoom_changed.emit(n)

    def groups(self) -> list:
        return [g for s in self.sections for g in s.items]

    def group_at_index(self, si: int, ii: int):
        return self.sections[si].items[ii]

    def selected_groups(self) -> list:
        return [g for s in self.sections for g in s.items if g.gid in self.selected]

    def clear_selection(self) -> None:
        if self.selected:
            self.selected.clear()
            self.viewport().update()
            self.selection_changed.emit()

    def select_all(self) -> None:
        self.selected = {g.gid for g in self.groups()}
        self.viewport().update()
        self.selection_changed.emit()

    def set_sections(self, sections, keep_scroll: bool = False) -> None:
        live = {g.gid for s in sections for g in s.items}
        self.selected &= live
        self.current = None
        super().set_sections(sections, keep_scroll)
        self.selection_changed.emit()

    def _flat(self, pos: tuple[int, int]) -> int:
        si, ii = pos
        return sum(len(s.items) for s in self.sections[:si]) + ii

    def _unflat(self, k: int) -> tuple[int, int] | None:
        for si, s in enumerate(self.sections):
            if k < len(s.items):
                return si, k
            k -= len(s.items)
        return None

    # ---- 绘制 ----
    def _paint_cell(self, qp, rect, si, ii):
        p = theme.current()
        g = self.sections[si].items[ii]
        if not draw_cover(qp, rect, g):
            qp.fillRect(rect, QColor(p.surface2))
            qp.setPen(QColor(p.ink3))
            qp.setFont(font("mono", 7))
            qp.drawText(rect, Qt.AlignmentFlag.AlignCenter, g.ext_label)
        small = rect.width() < 90
        # 视频时长 / 实况 / 收藏
        if g.is_video:
            from ..viewer import fmt_duration
            self._chip(qp, rect, "▶ " + (fmt_duration((g.meta or {}).get("duration")) or ""), "br", small)
        elif any(f.role == "live" for f in g.files) and not small:
            self._chip(qp, rect, "LIVE", "tl", small)
        tag = self.badges.get(g.gid)
        if tag and not small:
            self._chip(qp, rect, tag, "tl", small, accent=True)
        if self.is_fav(g):
            qp.setFont(font("cn", 11 if not small else 8, 700))
            qp.setPen(QColor(0, 0, 0, 120))
            qp.drawText(QRectF(rect.left() + 6, rect.bottom() - 22, 20, 20), Qt.AlignmentFlag.AlignCenter, "♥")
            qp.setPen(QColor("#FFFFFF"))
            qp.drawText(QRectF(rect.left() + 5, rect.bottom() - 23, 20, 20), Qt.AlignmentFlag.AlignCenter, "♥")
        sel = g.gid in self.selected
        if sel:
            qp.fillRect(rect, QColor(255, 255, 255, 60))
            qp.setPen(QPen(QColor(p.accent), 3))
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.drawRect(rect.adjusted(1.5, 1.5, -1.5, -1.5))
            c = QRectF(rect.right() - 24, rect.bottom() - 24, 18, 18)
            qp.setPen(QPen(QColor("#FFFFFF"), 1.5))
            qp.setBrush(QColor(p.accent2))
            qp.drawEllipse(c)
            qp.setPen(QPen(QColor("#FFFFFF"), 2))
            qp.drawPolyline([QPointF(c.left() + 4.5, c.center().y()), QPointF(c.left() + 8, c.bottom() - 5),
                             QPointF(c.right() - 4, c.top() + 5)])
        elif self._hover == (si, ii):
            qp.fillRect(rect, QColor(255, 255, 255, 36))
        if self.current == (si, ii) and self.hasFocus():
            qp.setPen(QPen(QColor(p.accent2), 2, Qt.PenStyle.DashLine))
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.drawRect(rect.adjusted(1, 1, -1, -1))

    def _chip(self, qp, rect, text, corner, small, accent=False):
        p = theme.current()
        f = font("mono" if text.isascii() else "cn", 7 if small else 8, 700)
        qp.setFont(f)
        w = QFontMetrics(f).horizontalAdvance(text) + 10
        h = 16
        if corner == "br":
            r = QRectF(rect.right() - w - 4, rect.bottom() - h - 4, w, h)
        else:
            r = QRectF(rect.left() + 4, rect.top() + 4, w, h)
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(p.accent) if accent else QColor(0, 0, 0, 150))
        qp.drawRoundedRect(r, 3, 3)
        qp.setPen(QColor(p.accent_ink) if accent else QColor("#FFFFFF"))
        qp.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    # ---- 交互 ----
    def mouseMoveEvent(self, e):
        h = self._at(e.position().toPoint())
        if h != self._hover:
            self._hover = h
            self.viewport().update()
            if h is not None:
                from ..widgets.thumbgrid import tooltip_for
                g = self.group_at_index(*h)
                self.viewport().setToolTip(tooltip_for(g))
            else:
                self.viewport().setToolTip("")

    def mousePressEvent(self, e):
        self.setFocus()
        h = self._at(e.position().toPoint())
        mods = e.modifiers()
        if h is None:
            if e.button() == Qt.MouseButton.LeftButton and not mods:
                self.clear_selection()
            return
        g = self.group_at_index(*h)
        if e.button() == Qt.MouseButton.RightButton:
            if g.gid not in self.selected:
                self.selected = {g.gid}
                self.selection_changed.emit()
            self.current = h
            self.viewport().update()
            self.menu_requested.emit(e.globalPosition().toPoint(), self.selected_groups())
            return
        if mods & Qt.KeyboardModifier.ShiftModifier and self._anchor is not None:
            a, b = sorted((self._flat(self._anchor), self._flat(h)))
            flat = self.groups()
            self.selected |= {flat[k].gid for k in range(a, b + 1)}
        elif mods & Qt.KeyboardModifier.ControlModifier:
            self.selected ^= {g.gid}
            self._anchor = h
        else:
            self.selected = {g.gid}
            self._anchor = h
        self.current = h
        self.viewport().update()
        self.selection_changed.emit()

    def mouseDoubleClickEvent(self, e):
        h = self._at(e.position().toPoint())
        if h is not None and e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.group_at_index(*h).gid)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_columns(self.cols_pref + (-1 if e.angleDelta().y() > 0 else 1))
            e.accept()
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        flat_n = sum(len(s.items) for s in self.sections)
        if not flat_n:
            return super().keyPressEvent(e)
        cur = self._flat(self.current) if self.current else -1
        step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -self._cols, Qt.Key.Key_Down: self._cols}
        if k in step:
            nk = max(0, min(flat_n - 1, cur + step[k] if cur >= 0 else 0))
            pos = self._unflat(nk)
            g = self.group_at_index(*pos)
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.selected.add(g.gid)
            else:
                self.selected = {g.gid}
                self._anchor = pos
            self.current = pos
            self.scroll_to(*pos)
            self.selection_changed.emit()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.current:
            self.activated.emit(self.group_at_index(*self.current).gid)
            return
        if k == Qt.Key.Key_A and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.select_all()
            return
        if k == Qt.Key.Key_Escape and self.selected:
            self.clear_selection()
            return
        if k == Qt.Key.Key_F and self.selected and not e.modifiers():
            self.fav_requested.emit()
            return
        if Qt.Key.Key_0 <= k <= Qt.Key.Key_5 and self.selected and HOOKS.rate and not e.modifiers():
            HOOKS.rate(list(self.selected), {"rating": k - Qt.Key.Key_0})
            self.viewport().update()
            return
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.set_columns(self.cols_pref - 1)
            return
        if k == Qt.Key.Key_Minus:
            self.set_columns(self.cols_pref + 1)
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------------------
# 卡片网格
# ---------------------------------------------------------------------------

class CardGrid(_SectionGrid):
    activated = Signal(object)           # Card
    menu_requested = Signal(QPoint, object)
    header_h = 50

    def __init__(self, parent=None, min_w: int = 190, ratio: float = 1.0):
        super().__init__(parent)
        self.min_w = min_w
        self.ratio = ratio                 # 封面 高 / 宽
        self.text_h = 46
        self.empty_text = _t("还没有内容")

    def _cell_size(self, avail):
        n = max(1, int((avail + GAP * 6) // (self.min_w + GAP * 6)))
        w = (avail - (n - 1) * GAP * 6) / n
        return n, w, w * self.ratio + self.text_h

    def _cell_rect(self, si, ii):
        s = self.sections[si]
        row, col = divmod(ii, self._cols)
        y = self._tops[si] + (self.header_h if s.header else 0) + row * (self._ch + GAP)
        return QRectF(MARGIN + col * (self._cw + GAP * 6), y, self._cw, self._ch)

    def _at(self, pos):
        y = pos.y() + self.verticalScrollBar().value()
        si = bisect_right(self._tops, y) - 1
        if not 0 <= si < len(self.sections):
            return None
        s = self.sections[si]
        for ii in range(len(s.items)):
            r = self._cell_rect(si, ii)
            if r.top() > y:
                break
            if r.contains(QPointF(pos.x(), y)):
                return si, ii
        return None

    def _paint_cell(self, qp, rect, si, ii):
        p = theme.current()
        c: Card = self.sections[si].items[ii]
        img = QRectF(rect.left(), rect.top(), rect.width(), rect.width() * self.ratio)
        if not draw_cover(qp, img, c.cover, 6):
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(QColor(p.surface2))
            qp.drawRoundedRect(img, 6, 6)
            if c.icon:
                qp.setPen(QColor(p.ink3))
                qp.setFont(font("cn", 28, 700))
                qp.drawText(img, Qt.AlignmentFlag.AlignCenter, c.icon)
        elif c.icon:
            r = QRectF(img.left() + 8, img.bottom() - 32, 24, 24)
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(QColor(0, 0, 0, 140))
            qp.drawRoundedRect(r, 4, 4)
            qp.setPen(QColor("#FFFFFF"))
            qp.setFont(font("cn", 10, 700))
            qp.drawText(r, Qt.AlignmentFlag.AlignCenter, c.icon)
        if self._hover == (si, ii):
            qp.setPen(QPen(QColor(p.accent), 2))
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.drawRoundedRect(img.adjusted(1, 1, -1, -1), 6, 6)
        f = font("cn", 10.5, 700)
        qp.setFont(f)
        qp.setPen(QColor(p.ink))
        title = QFontMetrics(f).elidedText(c.title, Qt.TextElideMode.ElideRight, int(rect.width()))
        qp.drawText(QRectF(rect.left(), img.bottom() + 5, rect.width(), 20), Qt.AlignmentFlag.AlignVCenter, title)
        qp.setFont(font("cn", 8.5))
        qp.setPen(QColor(p.ink3))
        sub = " · ".join(x for x in ((f"{c.count:,}" if c.cover is not None or c.count else ""), c.sub) if x)
        sub = QFontMetrics(font("cn", 8.5)).elidedText(sub, Qt.TextElideMode.ElideRight, int(rect.width()))
        qp.drawText(QRectF(rect.left(), img.bottom() + 24, rect.width(), 18), Qt.AlignmentFlag.AlignVCenter, sub)

    def mouseMoveEvent(self, e):
        h = self._at(e.position().toPoint())
        if h != self._hover:
            self._hover = h
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if h else Qt.CursorShape.ArrowCursor)
            self.viewport().update()

    def mouseReleaseEvent(self, e):
        h = self._at(e.position().toPoint())
        if h is None:
            return
        c = self.sections[h[0]].items[h[1]]
        if e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(c)
        elif e.button() == Qt.MouseButton.RightButton:
            self.menu_requested.emit(e.globalPosition().toPoint(), c)

    def sizeHint(self):
        return QSize(600, 400)
