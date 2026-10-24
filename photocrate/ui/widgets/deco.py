"""自绘控件：工业终端风格的面板、标题、按钮、数据卡片、进度条与图表。"""
from __future__ import annotations

import random
from typing import Iterable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from .. import theme, motion
from ..theme import font
from ...i18n import is_unknown, t as _t


def _p():
    return theme.current()


# ---------------------------------------------------------------------------
# 斜纹警示带
# ---------------------------------------------------------------------------

class HazardStripe(QWidget):
    def __init__(self, height: int = 8, parent=None, light: str | None = None, dark: str | None = None,
                 step: int = 10):
        super().__init__(parent)
        self.setFixedHeight(height)
        self._light, self._dark, self._step = light, dark, step
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, e):
        p = _p()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        qp.fillRect(self.rect(), QColor(self._dark or p.slab))
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(self._light or p.accent))
        s = self._step
        x = -h
        while x < w + h:
            poly = QPolygonF([QPointF(x, h), QPointF(x + h, 0), QPointF(x + h + s / 2, 0), QPointF(x + s / 2, h)])
            qp.drawPolygon(poly)
            x += s
        qp.end()


# ---------------------------------------------------------------------------
# 条码装饰
# ---------------------------------------------------------------------------

class Barcode(QWidget):
    def __init__(self, seed: int = 7, w: int = 90, h: int = 22, color: str | None = None, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        r = random.Random(seed)
        self._bars = [(r.choice([1, 1, 2, 3]), r.choice([1, 2, 2, 3])) for _ in range(60)]
        self._color = color

    def paintEvent(self, e):
        qp = QPainter(self)
        col = QColor(self._color or _p().slab_ink2)
        x = 0
        for bw, gap in self._bars:
            if x + bw > self.width():
                break
            qp.fillRect(QRectF(x, 0, bw, self.height()), col)
            x += bw + gap
        qp.end()


# ---------------------------------------------------------------------------
# 标志
# ---------------------------------------------------------------------------

class LogoMark(QWidget):
    """原创标志：带切角的方块 + 取景框角标 + 斜切光带。"""

    def __init__(self, size: int = 34, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, e):
        p = _p()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        c = s * 0.28
        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(s - c, 0)
        path.lineTo(s, c)
        path.lineTo(s, s)
        path.lineTo(0, s)
        path.closeSubpath()
        qp.fillPath(path, QColor(p.accent))
        pen = QPen(QColor(p.accent_ink), max(2, s / 14))
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        qp.setPen(pen)
        m, L = s * 0.22, s * 0.18
        for (x, y, dx, dy) in ((m, m, 1, 1), (s - m, s - m, -1, -1), (m, s - m, 1, -1)):
            qp.drawLine(QPointF(x, y), QPointF(x + dx * L, y))
            qp.drawLine(QPointF(x, y), QPointF(x, y + dy * L))
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(p.accent_ink))
        band = QPolygonF([QPointF(s * 0.40, s * 0.62), QPointF(s * 0.62, s * 0.40),
                          QPointF(s * 0.72, s * 0.40), QPointF(s * 0.50, s * 0.62)])
        qp.drawPolygon(band)
        qp.end()


# ---------------------------------------------------------------------------
# 章节标题
# ---------------------------------------------------------------------------

class SectionHeader(QWidget):
    def __init__(self, index: str, title: str, en: str, sub: str = "", parent=None):
        super().__init__(parent)
        self.index, self.title, self.en, self.sub = index, title, en, sub
        self._sub_lines = 1
        self.setFixedHeight(76 if sub else 62)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def resizeEvent(self, e):
        # 说明文字放不下一行时折行（英文界面的说明比中文长），最多三行
        if self.sub:
            fm = QFontMetrics(font("cn", 9.5))
            r = fm.boundingRect(0, 0, max(1, self.width()), 10000, Qt.TextFlag.TextWordWrap, self.sub)
            lines = max(1, min(3, round(r.height() / max(1, fm.lineSpacing()))))
            if lines != self._sub_lines:
                self._sub_lines = lines
                self.setFixedHeight(56 + 20 * lines)
        super().resizeEvent(e)

    def paintEvent(self, e):
        p = _p()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w = self.width()
        # 编号
        f_idx = font("display", 30, QFont_bold())
        qp.setFont(f_idx)
        qp.setPen(QColor(p.ink3))
        fm = QFontMetrics(f_idx)
        idx_w = fm.horizontalAdvance(self.index)
        qp.drawText(QRectF(0, 0, idx_w + 4, 48), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.index)
        x = idx_w + 14
        # 竖线
        qp.fillRect(QRectF(x, 8, 3, 34), QColor(p.accent))
        x += 14
        f_t = font("cn", 17, 700)
        qp.setFont(f_t)
        qp.setPen(QColor(p.ink))
        t_w = QFontMetrics(f_t).horizontalAdvance(self.title)
        qp.drawText(QRectF(x, 2, t_w + 6, 30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
        f_en = font("mono", 8, 400, 2.0)
        qp.setFont(f_en)
        qp.setPen(QColor(p.ink2))
        qp.drawText(QRectF(x, 30, 600, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.en.upper())
        # 右侧刻度线
        lx = x + max(t_w, QFontMetrics(f_en).horizontalAdvance(self.en.upper())) + 24
        y = 24
        if lx < w - 120:
            qp.setPen(QPen(QColor(p.line), 1))
            qp.drawLine(QPointF(lx, y), QPointF(w - 92, y))
            for i, tx in enumerate(range(int(lx), min(int(lx) + 100, int(w - 92)), 20)):
                h = 6 if i % 5 == 0 else 3
                qp.drawLine(QPointF(tx, y), QPointF(tx, y + h))
            qp.setFont(font("mono", 8, 400, 1.0))
            qp.setPen(QColor(p.ink3))
            qp.drawText(QRectF(w - 88, y - 9, 88, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        f"// SEC-{self.index}")
        if self.sub:
            qp.setFont(font("cn", 9.5))
            qp.setPen(QColor(p.ink2))
            qp.drawText(QRectF(0, 52, w, 20 * self._sub_lines),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, self.sub)
        qp.end()


def QFont_bold() -> int:
    return 700


# ---------------------------------------------------------------------------
# 面板
# ---------------------------------------------------------------------------

class Panel(QFrame):
    """带角标与标题条的面板。往 .body（QVBoxLayout）里放内容。"""

    def __init__(self, title: str = "", en: str = "", meta: str = "", parent=None, pad: int = 12):
        super().__init__(parent)
        self.title, self.en, self._meta = title, en, meta
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.header_h = 40 if title else 0
        if title:
            self.header = QWidget()
            self.header.setFixedHeight(self.header_h)
            hl = QHBoxLayout(self.header)
            hl.setContentsMargins(14, 0, 10, 0)
            hl.addStretch(1)
            self.header_right = hl
            outer.addWidget(self.header)
        self.body_w = QWidget()
        self.body = QVBoxLayout(self.body_w)
        self.body.setContentsMargins(pad, pad if not title else 8, pad, pad)
        self.body.setSpacing(8)
        outer.addWidget(self.body_w, 1)

    def set_meta(self, text: str) -> None:
        self._meta = text
        self.update()

    def add_header_widget(self, w: QWidget) -> None:
        if self.title:
            self.header_right.addWidget(w)

    def paintEvent(self, e):
        p = _p()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        r = self.rect().adjusted(0, 0, -1, -1)
        qp.fillRect(r, QColor(p.surface))
        qp.setPen(QPen(QColor(p.line), 1))
        qp.drawRect(r)
        # 单侧定位角标，避免每块面板重复四角造成视觉噪声。
        qp.setPen(QPen(QColor(p.ink3), 1))
        L = 9
        for (x, y, dx, dy) in ((r.right(), r.top() + 1, -1, 1),):
            qp.drawLine(x, y, x + dx * L, y)
            qp.drawLine(x, y, x, y + dy * L)
        if self.title:
            h = self.header_h
            qp.fillRect(QRectF(14, h / 2 - 6, 3, 12), QColor(p.accent2))
            qp.setPen(QColor(p.ink))
            f = font("cn", 10.5, 700)
            qp.setFont(f)
            tw = QFontMetrics(f).horizontalAdvance(self.title)
            qp.drawText(QRectF(28, 0, tw + 4, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.title)
            fx = 28 + tw + 10
            if self.en:
                qp.setFont(font("mono", 7.5, 400, 1.5))
                qp.setPen(QColor(p.ink3))
                qp.drawText(QRectF(fx, 0, 300, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                            self.en.upper())
            if self._meta:
                qp.setFont(font("mono", 8.5))
                qp.setPen(QColor(p.ink2))
                right_w = self.header_right.sizeHint().width() if self.header_right.count() > 1 else 0
                qp.drawText(QRectF(0, 0, r.width() - 14 - right_w, h),
                            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self._meta)
            qp.setPen(QPen(QColor(p.line), 1))
            qp.drawLine(14, h - 1, r.width() - 14, h - 1)
            qp.setPen(QPen(QColor(p.ink), 1))
            qp.drawLine(14, h - 1, 54, h - 1)
        qp.end()


# ---------------------------------------------------------------------------
# 按钮
# ---------------------------------------------------------------------------

class TacButton(QPushButton):
    """切角按钮。variant: primary（黄）/ secondary（描边）/ danger（红）/ dark（深色）。"""

    def __init__(self, text: str, variant: str = "secondary", en: str = "", parent=None, height: int = 34):
        super().__init__(text, parent)
        self.variant = variant
        self.en = en
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(height)
        self._hover = False
        self.feedback = motion.Motion(self, 0, 150)
        self._focus_visible = False
        f = font("cn", 10, 700 if variant in ("primary", "danger", "dark") else 500)
        self.setFont(f)
        extra = QFontMetrics(font("mono", 7)).horizontalAdvance(en.upper()) + 10 if en else 0
        self.setMinimumWidth(QFontMetrics(f).horizontalAdvance(text) + 40 + extra)

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(max(s.width(), self.minimumWidth()), self.height())

    def focusInEvent(self, event):
        self._focus_visible = event.reason() != Qt.FocusReason.MouseFocusReason
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        self._focus_visible = False
        super().focusOutEvent(event)
        self.update()

    def mousePressEvent(self, event):
        # 已有键盘焦点时再次鼠标点击，也应退出键盘焦点提示。
        self._focus_visible = False
        super().mousePressEvent(event)
        self.update()

    def keyPressEvent(self, event):
        self._focus_visible = True
        super().keyPressEvent(event)
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.feedback.to(1)
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.feedback.to(0)
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = _p()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        c = 9
        path = QPainterPath()
        path.moveTo(0.5, 0.5)
        path.lineTo(w - c - 0.5, 0.5)
        path.lineTo(w - 0.5, c + 0.5)
        path.lineTo(w - 0.5, h - 0.5)
        path.lineTo(0.5, h - 0.5)
        path.closeSubpath()
        enabled = self.isEnabled()
        down = self.isDown() or self.isChecked()
        v = self.variant
        if not enabled:
            fill, ink, border = QColor(p.surface2), QColor(p.ink3), QColor(p.line)
        elif v == "primary":
            fill = QColor(p.accent_hover if (self._hover or down) else p.accent)
            ink, border = QColor(p.accent_ink), QColor(p.ink)
        elif v == "danger":
            fill = QColor(p.danger).darker(110) if (self._hover or down) else QColor(p.danger)
            ink, border = QColor("#FFFFFF"), QColor(p.danger).darker(130)
        elif v == "dark":
            fill = QColor(p.slab2 if self._hover else p.slab)
            ink, border = QColor(p.slab_ink), QColor(p.slab)
        else:
            fill = QColor(p.surface2 if (self._hover or down) else p.surface)
            ink, border = QColor(p.ink), QColor(p.ink if self._hover else p.ink2)
            if down:
                fill, ink = QColor(p.accent), QColor(p.accent_ink)
        qp.fillPath(path, fill)
        if enabled and self.feedback.value > 0:
            qp.save()
            qp.setClipPath(path)
            qp.fillRect(QRectF(10, h - 3, (w - 20) * self.feedback.value, 2), ink)
            qp.restore()
        qp.setPen(QPen(border, 1))
        qp.drawPath(path)
        # 左下角小三角
        if enabled and v in ("primary", "danger", "dark"):
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(ink)
            qp.drawPolygon(QPolygonF([QPointF(3, h - 3), QPointF(9, h - 3), QPointF(3, h - 9)]))
        qp.setPen(ink)
        qp.setFont(self.font())
        text_rect = QRectF(0, 0, w, h)
        if self.en:
            fm = QFontMetrics(self.font())
            fe = font("mono", 7, 400, 1.2)
            ew = QFontMetrics(fe).horizontalAdvance(self.en.upper())
            tw = fm.horizontalAdvance(self.text())
            total = tw + 8 + ew
            x0 = (w - total) / 2
            qp.drawText(QRectF(x0, 0, tw + 2, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
            qp.setFont(fe)
            col = QColor(ink)
            col.setAlpha(150)
            qp.setPen(col)
            qp.drawText(QRectF(x0 + tw + 8, 0, ew + 2, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        self.en.upper())
        else:
            qp.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.text())
        if self.hasFocus() and self._focus_visible and enabled:
            qp.setBrush(Qt.BrushStyle.NoBrush)   # 之前画小三角时设置了画刷，不清掉会把整个按钮填黑
            # 沿用按钮的切角轮廓，避免矩形虚线压住文字或跨过切角。
            qp.setPen(QPen(QColor(p.accent2), 2))
            qp.drawPath(path)
        qp.end()


class IconButton(QPushButton):
    """小图标按钮（自绘图形，不依赖字体里的符号）。kind: up / down / close / plus"""

    def __init__(self, kind: str, tip: str = "", size: int = 26, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(size, size)
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = _p()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        danger = self.kind == "close"
        if self.isDown():
            qp.fillRect(r, QColor(p.danger if danger else p.accent))
        elif self._hover:
            qp.fillRect(r, QColor(p.surface2))
        else:
            qp.fillRect(r, QColor(p.surface))
        qp.setPen(QPen(QColor(p.ink if self._hover else p.line), 1))
        qp.drawRect(r)
        col = QColor(p.ink if self.isEnabled() else p.ink3)
        if self._hover and danger:
            col = QColor(p.danger)
        pen = QPen(col, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        qp.setPen(pen)
        c = r.center()
        d = r.width() * 0.2
        if self.kind == "up":
            qp.drawPolyline(QPolygonF([QPointF(c.x() - d, c.y() + d / 2), QPointF(c.x(), c.y() - d / 2),
                                       QPointF(c.x() + d, c.y() + d / 2)]))
        elif self.kind == "down":
            qp.drawPolyline(QPolygonF([QPointF(c.x() - d, c.y() - d / 2), QPointF(c.x(), c.y() + d / 2),
                                       QPointF(c.x() + d, c.y() - d / 2)]))
        elif self.kind == "close":
            qp.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            qp.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))
        else:
            qp.drawLine(QPointF(c.x() - d, c.y()), QPointF(c.x() + d, c.y()))
            qp.drawLine(QPointF(c.x(), c.y() - d), QPointF(c.x(), c.y() + d))
        qp.end()


# ---------------------------------------------------------------------------
# 数据卡片
# ---------------------------------------------------------------------------

class StatCard(QWidget):
    def __init__(self, label: str, en: str, value: str = "—", unit: str = "", parent=None, accent: str | None = None):
        super().__init__(parent)
        self.label, self.en, self.value, self.unit = label, en, value, unit
        self.ratio: float | None = None
        self.accent = accent
        self.setMinimumSize(150, 92)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(92)

    def set(self, value: str, ratio: float | None = None, unit: str | None = None) -> None:
        self.value = value
        self.ratio = ratio
        if unit is not None:
            self.unit = unit
        self.update()

    def paintEvent(self, e):
        p = _p()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        qp.fillRect(r, QColor(p.surface))
        qp.setPen(QPen(QColor(p.line), 1))
        qp.drawRect(r)
        acc = QColor(self.accent or p.accent)
        qp.fillRect(QRectF(r.left() + 16, r.top(), 28, 2), acc)
        qp.setFont(font("cn", 9.5, 600))
        qp.setPen(QColor(p.ink2))
        qp.drawText(QRectF(16, 8, r.width() - 24, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.label)
        label_width = QFontMetrics(qp.font()).horizontalAdvance(self.label)
        qp.setFont(font("mono", 7, 400, 1.2))
        qp.setPen(QColor(p.ink3))
        # 窄卡片优先保留中文，避免中英文标题互相覆盖。
        if label_width + QFontMetrics(qp.font()).horizontalAdvance(self.en.upper()) + 48 < r.width():
            qp.drawText(QRectF(16, 8, r.width() - 26, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        self.en.upper())
        fv = font("display", 24, 600)
        qp.setFont(fv)
        qp.setPen(QColor(p.ink))
        vw = QFontMetrics(fv).horizontalAdvance(self.value)
        qp.drawText(QRectF(16, 28, vw + 6, 40), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.value)
        if self.unit:
            qp.setFont(font("cn", 9))
            qp.setPen(QColor(p.ink2))
            qp.drawText(QRectF(16 + vw + 6, 34, 120, 32), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        self.unit)
        # 比例条
        y = r.height() - 14
        qp.fillRect(QRectF(16, y, r.width() - 32, 3), QColor(p.surface2))
        if self.ratio is not None:
            qp.fillRect(QRectF(16, y, (r.width() - 32) * max(0.0, min(1.0, self.ratio)), 3), acc)
            qp.setFont(font("mono", 7))
            qp.setPen(QColor(p.ink3))
            qp.drawText(QRectF(16, y - 14, r.width() - 32, 12), Qt.AlignmentFlag.AlignRight,
                        f"{self.ratio * 100:.0f}%")
        qp.end()


# ---------------------------------------------------------------------------
# 分段进度条
# ---------------------------------------------------------------------------

class SegProgress(QWidget):
    def __init__(self, parent=None, segments: int = 48):
        super().__init__(parent)
        self.value = 0.0
        self.segments = segments
        self.busy = False
        self._phase = 0
        self.setFixedHeight(18)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, v: float) -> None:
        self.value = max(0.0, min(1.0, v))
        self.update()

    def tick(self) -> None:
        if self.isVisible() and motion.enabled() and (self.busy or 0 < self.value < 1):
            self._phase = (self._phase + 1) % self.segments
            self.update()

    def paintEvent(self, e):
        p = _p()
        qp = QPainter(self)
        w, h = self.width() - 52, self.height()
        n = self.segments
        gap = 2
        sw = (w - gap * (n - 1)) / n
        filled = int(round(self.value * n))
        for i in range(n):
            x = i * (sw + gap)
            if i < filled:
                col = QColor(p.accent_hover if motion.enabled() and 0 < self.value < 1 and (i - self._phase) % n < 3 else p.accent)
            elif self.busy and motion.enabled() and (i - self._phase) % n < 3:
                col = QColor(p.ink3)
            else:
                col = QColor(p.surface2)
            qp.fillRect(QRectF(x, 3, sw, h - 6), col)
        qp.setPen(QColor(p.ink))
        qp.setFont(font("display", 10, 600))
        qp.drawText(QRectF(w, 0, 52, h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"{self.value * 100:5.1f}%")
        qp.end()


# ---------------------------------------------------------------------------
# 横向条形图
# ---------------------------------------------------------------------------

class BarList(QWidget):
    clicked = Signal(str)

    def __init__(self, parent=None, max_items: int = 12, row_h: int = 24, label_w: int = 120):
        super().__init__(parent)
        self.items: list[tuple[str, int, str | None]] = []
        self.max_items, self.row_h, self.label_w = max_items, row_h, label_w
        self._hover = -1
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_items(self, items: Iterable[tuple[str, int, str | None]]) -> None:
        items = list(items)
        unknown = [it for it in items if is_unknown(it[0])]
        items = [it for it in items if it not in unknown] + unknown
        if len(items) > self.max_items:
            rest = sum(c for _, c, _ in items[self.max_items - 1:])
            items = items[: self.max_items - 1] + [(_t('其余 {0} 项', len(items) - self.max_items + 1), rest, None)]
        self.items = items
        self.setFixedHeight(max(1, len(items)) * self.row_h + 4)
        self.update()

    def mouseMoveEvent(self, e):
        i = int(e.position().y() // self.row_h)
        i = i if 0 <= i < len(self.items) else -1
        if i != self._hover:
            self._hover = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = int(e.position().y() // self.row_h)
        if 0 <= i < len(self.items):
            self.clicked.emit(self.items[i][0])

    def paintEvent(self, e):
        p = _p()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.items:
            qp.setPen(QColor(p.ink3))
            qp.setFont(font("cn", 9))
            qp.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, _t("暂无数据"))
            qp.end()
            return
        total = sum(c for _, c, _ in self.items) or 1
        mx = max(c for _, c, _ in self.items) or 1
        lw = self.label_w
        bw = self.width() - lw - 74
        for i, (label, count, color) in enumerate(self.items):
            y = i * self.row_h
            if i == self._hover:
                qp.fillRect(QRectF(0, y, self.width(), self.row_h), QColor(p.surface2))
            qp.setFont(font("cn", 9))
            qp.setPen(QColor(p.ink))
            el = QFontMetrics(qp.font()).elidedText(_t(label), Qt.TextElideMode.ElideRight, lw - 22)
            if color:
                qp.fillRect(QRectF(2, y + self.row_h / 2 - 5, 10, 10), QColor(color))
                qp.setPen(QPen(QColor(p.ink2), 0.6))
                qp.drawRect(QRectF(2, y + self.row_h / 2 - 5, 10, 10))
                qp.setPen(QColor(p.ink))
                qp.drawText(QRectF(18, y, lw - 18, self.row_h), Qt.AlignmentFlag.AlignVCenter, el)
            else:
                qp.drawText(QRectF(2, y, lw - 4, self.row_h), Qt.AlignmentFlag.AlignVCenter, el)
            bx = lw
            qp.fillRect(QRectF(bx, y + 7, bw, self.row_h - 14), QColor(p.surface2))
            frac = count / mx
            bar = QRectF(bx, y + 7, max(2.0, bw * frac), self.row_h - 14)
            unknown = is_unknown(label)
            if unknown:
                qp.save()
                qp.setClipRect(bar)
                qp.fillRect(bar, QColor(p.surface2))
                qp.setPen(QPen(QColor(p.ink3), 1))
                for k in range(int(bar.left()) - 20, int(bar.right()) + 20, 5):
                    qp.drawLine(QPointF(k, bar.bottom()), QPointF(k + 10, bar.top()))
                qp.restore()
            else:
                qp.fillRect(bar, QColor(color) if color else QColor(p.ink))
                qp.fillRect(QRectF(bar.right() - 3, bar.top(), 3, bar.height()), QColor(p.accent))
            qp.setFont(font("display", 9.5, 600))
            qp.setPen(QColor(p.ink))
            qp.drawText(QRectF(bx + bw + 6, y, 64, self.row_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        f"{count}  {count / total * 100:.0f}%")
        qp.end()


# ---------------------------------------------------------------------------
# 时间分布（按月柱状图）
# ---------------------------------------------------------------------------

class TimelineChart(QWidget):
    clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.months: list[tuple[str, int]] = []
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self._hover = -1
        self._rects: list[QRectF] = []

    def set_data(self, months: dict[str, int]) -> None:
        if months:
            keys = sorted(months)
            y0, m0 = map(int, keys[0].split("-"))
            y1, m1 = map(int, keys[-1].split("-"))
            out = []
            y, m = y0, m0
            while (y, m) <= (y1, m1) and len(out) < 600:
                k = f"{y:04d}-{m:02d}"
                out.append((k, months.get(k, 0)))
                m += 1
                if m > 12:
                    y, m = y + 1, 1
            self.months = out
        else:
            self.months = []
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        i = next((k for k, r in enumerate(self._rects) if r.left() <= pos.x() <= r.right()), -1)
        if i != self._hover:
            self._hover = i
            if 0 <= i < len(self.months):
                k, c = self.months[i]
                self.setToolTip(_t('{0}：{1} 张', k, c))
            self.update()

    def mousePressEvent(self, e):
        if 0 <= self._hover < len(self.months):
            self.clicked.emit(self.months[self._hover][0])

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def paintEvent(self, e):
        p = _p()
        qp = QPainter(self)
        w, h = self.width(), self.height()
        self._rects = []
        if not self.months:
            qp.setPen(QColor(p.ink3))
            qp.setFont(font("cn", 9))
            qp.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, _t("暂无日期数据"))
            qp.end()
            return
        top, bottom, left = 18, 30, 34
        ch = h - top - bottom
        mx = max(c for _, c in self.months) or 1
        n = len(self.months)
        slot = (w - left - 6) / n
        bwid = max(1.0, slot * 0.72)
        # 网格
        qp.setFont(font("mono", 7))
        for frac in (0, 0.5, 1):
            y = top + ch * (1 - frac)
            qp.setPen(QPen(QColor(p.line), 1, Qt.PenStyle.DashLine if frac else Qt.PenStyle.SolidLine))
            qp.drawLine(QPointF(left, y), QPointF(w - 4, y))
            qp.setPen(QColor(p.ink3))
            qp.drawText(QRectF(0, y - 7, left - 6, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        str(int(round(mx * frac))))
        last_year = None
        last_label_x = -999.0
        for i, (k, c) in enumerate(self.months):
            x = left + i * slot + (slot - bwid) / 2
            bh = ch * c / mx
            r = QRectF(x, top + ch - bh, bwid, bh)
            self._rects.append(QRectF(left + i * slot, top, slot, ch))
            col = QColor(p.accent) if i == self._hover else QColor(p.ink)
            if c:
                qp.fillRect(QRectF(r.left(), r.top(), max(2.0, r.width()), r.height()), col)
            yr = k[:4]
            if yr != last_year:
                lx = left + i * slot
                qp.setPen(QPen(QColor(p.ink2), 1))
                qp.drawLine(QPointF(lx, top + ch), QPointF(lx, top + ch + 8))
                if lx - last_label_x >= 38:
                    qp.setFont(font("display", 8.5, 600))
                    qp.drawText(QRectF(lx + 3, top + ch + 4, 60, 16), Qt.AlignmentFlag.AlignLeft, yr)
                    last_label_x = lx
                last_year = yr
        if 0 <= self._hover < n:
            k, c = self.months[self._hover]
            qp.setFont(font("mono", 8))
            qp.setPen(QColor(p.ink))
            qp.drawText(QRectF(left, 0, w - left, 16), Qt.AlignmentFlag.AlignRight, f"{k}  ·  {c}")
        qp.end()


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def micro_label(text: str, role: str = "faint") -> QLabel:
    lab = QLabel(text.upper())
    lab.setFont(font("mono", 7.5, 400, 1.5))
    lab.setProperty("role", role)
    return lab


def caption(text: str, role: str = "dim", size: float = 9) -> QLabel:
    lab = QLabel(text)
    lab.setFont(font("cn", size))
    lab.setProperty("role", role)
    lab.setWordWrap(True)
    return lab


def hline() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {_p().line};")
    return f
