"""「相册」大图画布：适合窗口显示、滚轮 / 双击缩放、拖动平移；叠加照片里的文字框（命中搜索词的高亮，
悬停显示文字、单击复制），编辑时叠加裁剪框（拖边角 / 拖框内移动，可锁定比例）。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QToolTip, QWidget

from .. import theme
from ..theme import font
from ...i18n import t as _t


def qimage_from_rgb(rgb) -> QImage:
    """float32 / uint8 RGB numpy → QImage（拷贝一份，不依赖 numpy 数组的生命周期）。"""
    import numpy as np
    a = rgb
    if a.dtype != np.uint8:
        a = np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)
    a = np.ascontiguousarray(a[..., :3])
    h, w = a.shape[:2]
    return QImage(a.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class PhotoCanvas(QWidget):
    copied = Signal(str)
    crop_changed = Signal(tuple)
    nav = Signal(int)                       # 点了左右两侧的翻页区：-1 / +1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pm: QPixmap | None = None
        self.zoom_fit = True
        self.zoom = 1.0                     # 显示像素 / 图片像素（不适合窗口时）
        self.center = QPointF(0.5, 0.5)     # 视图中心对应图片上的位置（0–1）
        self.lines: list[dict] = []         # 照片里的文字（框坐标 0–1）
        self.hits: set[int] = set()
        self.show_text = False
        self.crop: tuple | None = None      # 编辑裁剪时：(x0, y0, x1, y1)，0–1
        self.aspect: float | None = None    # 裁剪锁定的宽高比（图片像素）
        self.nav_enabled = True
        self._drag = None
        self._hover_line = -1
        self.setMouseTracking(True)
        self.setMinimumSize(200, 200)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    # ---- 内容 ----
    def set_pixmap(self, pm: QPixmap | None, reset: bool = True) -> None:
        self.pm = pm if pm is not None and not pm.isNull() else None
        if reset:
            self.zoom_fit = True
            self.center = QPointF(0.5, 0.5)
        self.update()

    def set_text(self, lines: list[dict], hits: list[int] | None = None) -> None:
        self.lines = lines or []
        self.hits = set(hits or [])
        self._hover_line = -1
        self.update()

    # ---- 坐标 ----
    def _scale(self) -> float:
        if self.pm is None:
            return 1.0
        pad = 40 if self.crop is not None else 0
        fit = min((self.width() - pad) / self.pm.width(), (self.height() - pad) / self.pm.height())
        return fit if self.zoom_fit else self.zoom

    def image_rect(self) -> QRectF:
        if self.pm is None:
            return QRectF()
        s = self._scale()
        w, h = self.pm.width() * s, self.pm.height() * s
        if self.zoom_fit:
            return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        x = self.width() / 2 - self.center.x() * w
        y = self.height() / 2 - self.center.y() * h
        if w <= self.width():
            x = (self.width() - w) / 2
        if h <= self.height():
            y = (self.height() - h) / 2
        return QRectF(x, y, w, h)

    def _to_img(self, pos: QPointF) -> QPointF:
        r = self.image_rect()
        return QPointF((pos.x() - r.left()) / max(1, r.width()), (pos.y() - r.top()) / max(1, r.height()))

    def _poly(self, line: dict) -> QPolygonF:
        r = self.image_rect()
        b = line.get("b") or []
        return QPolygonF([QPointF(r.left() + b[i] * r.width(), r.top() + b[i + 1] * r.height()) for i in range(0, 8, 2)])

    def _crop_rect(self) -> QRectF:
        r = self.image_rect()
        x0, y0, x1, y1 = self.crop
        return QRectF(r.left() + x0 * r.width(), r.top() + y0 * r.height(), (x1 - x0) * r.width(), (y1 - y0) * r.height())

    # ---- 绘制 ----
    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        qp.fillRect(self.rect(), QColor("#0B0D0E"))
        if self.pm is None:
            qp.setPen(QColor("#8A9590"))
            qp.setFont(font("cn", 10))
            qp.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, _t("正在载入…"))
            qp.end()
            return
        r = self.image_rect()
        qp.drawPixmap(r, self.pm, QRectF(self.pm.rect()))
        if self.show_text or self.hits:
            for i, line in enumerate(self.lines):
                if not self.show_text and i not in self.hits:
                    continue
                poly = self._poly(line)
                hit = i in self.hits
                col = QColor(p.accent) if hit else QColor(255, 255, 255, 200)
                fill = QColor(p.accent) if hit else QColor(255, 255, 255)
                fill.setAlpha(70 if hit else (50 if i == self._hover_line else 18))
                qp.setPen(QPen(col, 2 if hit or i == self._hover_line else 1))
                qp.setBrush(fill)
                qp.drawPolygon(poly)
        if self.crop is not None:
            cr = self._crop_rect()
            shade = QColor(0, 0, 0, 150)
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(shade)
            for rr in (QRectF(r.left(), r.top(), r.width(), cr.top() - r.top()),
                       QRectF(r.left(), cr.bottom(), r.width(), r.bottom() - cr.bottom()),
                       QRectF(r.left(), cr.top(), cr.left() - r.left(), cr.height()),
                       QRectF(cr.right(), cr.top(), r.right() - cr.right(), cr.height())):
                if rr.width() > 0 and rr.height() > 0:
                    qp.drawRect(rr)
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.setPen(QPen(QColor(255, 255, 255, 90), 1))
            for k in (1, 2):
                x = cr.left() + cr.width() * k / 3
                y = cr.top() + cr.height() * k / 3
                qp.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
                qp.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
            qp.setPen(QPen(QColor("#FFFFFF"), 1.5))
            qp.drawRect(cr)
            qp.setPen(QPen(QColor(p.accent), 4))
            L = 18
            for cx, cy, dx, dy in ((cr.left(), cr.top(), 1, 1), (cr.right(), cr.top(), -1, 1),
                                   (cr.left(), cr.bottom(), 1, -1), (cr.right(), cr.bottom(), -1, -1)):
                qp.drawLine(QPointF(cx, cy), QPointF(cx + dx * L, cy))
                qp.drawLine(QPointF(cx, cy), QPointF(cx, cy + dy * L))
        elif self.nav_enabled and self.underMouse() and self.zoom_fit:
            qp.setFont(font("cn", 22, 700))
            qp.setPen(QColor(255, 255, 255, 110))
            qp.drawText(QRectF(10, 0, 50, self.height()), Qt.AlignmentFlag.AlignCenter, "‹")
            qp.drawText(QRectF(self.width() - 60, 0, 50, self.height()), Qt.AlignmentFlag.AlignCenter, "›")
        if not self.zoom_fit:
            qp.setFont(font("mono", 8))
            qp.setPen(QColor(255, 255, 255, 170))
            qp.drawText(QRectF(10, self.height() - 26, 120, 20), Qt.AlignmentFlag.AlignVCenter, f"{self._scale() * 100:.0f}%")
        qp.end()

    # ---- 交互 ----
    def _line_at(self, pos: QPointF) -> int:
        if not (self.show_text or self.hits):
            return -1
        for i, line in enumerate(self.lines):
            if (self.show_text or i in self.hits) and self._poly(line).containsPoint(pos, Qt.FillRule.OddEvenFill):
                return i
        return -1

    def _crop_handle(self, pos: QPointF) -> str | None:
        cr = self._crop_rect()
        tol = 14
        near_l, near_r = abs(pos.x() - cr.left()) < tol, abs(pos.x() - cr.right()) < tol
        near_t, near_b = abs(pos.y() - cr.top()) < tol, abs(pos.y() - cr.bottom()) < tol
        inside_x = cr.left() - tol < pos.x() < cr.right() + tol
        inside_y = cr.top() - tol < pos.y() < cr.bottom() + tol
        h = ""
        if near_t and inside_x:
            h += "t"
        elif near_b and inside_x:
            h += "b"
        if near_l and inside_y:
            h += "l"
        elif near_r and inside_y:
            h += "r"
        if h:
            return h
        return "move" if cr.contains(pos) else None

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.crop is not None:
            h = self._crop_handle(pos)
            if h:
                self._drag = ("crop", h, self._to_img(pos), tuple(self.crop))
            return
        i = self._line_at(pos)
        if i >= 0:
            text = self.lines[i].get("t", "")
            QGuiApplication.clipboard().setText(text)
            self.copied.emit(text)
            return
        if not self.zoom_fit:
            self._drag = ("pan", pos, QPointF(self.center))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self.nav_enabled and (pos.x() < 70 or pos.x() > self.width() - 70):
            self.nav.emit(-1 if pos.x() < 70 else 1)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag and self._drag[0] == "pan":
            r = self.image_rect()
            d = pos - self._drag[1]
            self.center = QPointF(self._drag[2].x() - d.x() / max(1, r.width()), self._drag[2].y() - d.y() / max(1, r.height()))
            self.update()
            return
        if self._drag and self._drag[0] == "crop":
            self._drag_crop(pos)
            return
        if self.crop is not None:
            h = self._crop_handle(pos)
            cur = {"t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                   "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
                   "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                   "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                   "move": Qt.CursorShape.SizeAllCursor}.get(h or "", Qt.CursorShape.ArrowCursor)
            self.setCursor(cur)
            return
        i = self._line_at(pos)
        if i != self._hover_line:
            self._hover_line = i
            self.update()
        if i >= 0:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            QToolTip.showText(e.globalPosition().toPoint(), self.lines[i].get("t", "") + _t("\n（单击复制）"), self)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor if not self.zoom_fit else Qt.CursorShape.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag and self._drag[0] == "crop":
            self.crop_changed.emit(tuple(self.crop))
        self._drag = None
        self.unsetCursor()

    def leaveEvent(self, e):
        self._hover_line = -1
        self.update()

    def _drag_crop(self, pos: QPointF) -> None:
        _, h, start, (x0, y0, x1, y1) = self._drag
        q = self._to_img(pos)
        dx, dy = q.x() - start.x(), q.y() - start.y()
        mn = 0.03
        if h == "move":
            w, hh = x1 - x0, y1 - y0
            nx = min(max(0.0, x0 + dx), 1.0 - w)
            ny = min(max(0.0, y0 + dy), 1.0 - hh)
            self.crop = (nx, ny, nx + w, ny + hh)
        else:
            if "l" in h:
                x0 = min(max(0.0, x0 + dx), x1 - mn)
            if "r" in h:
                x1 = max(min(1.0, x1 + dx), x0 + mn)
            if "t" in h:
                y0 = min(max(0.0, y0 + dy), y1 - mn)
            if "b" in h:
                y1 = max(min(1.0, y1 + dy), y0 + mn)
            if self.aspect and self.pm is not None:
                x0, y0, x1, y1 = fit_aspect((x0, y0, x1, y1), self.aspect, self.pm.width(), self.pm.height(), h)
            self.crop = (x0, y0, x1, y1)
        self.update()

    def mouseDoubleClickEvent(self, e):
        if self.crop is not None or self.pm is None or self._line_at(e.position()) >= 0:
            return
        if self.zoom_fit:
            self.center = self._to_img(e.position())
            self.zoom = 1.0 if self._scale() < 1.0 else min(4.0, self._scale() * 2)
            self.zoom_fit = False
        else:
            self.zoom_fit = True
        self.update()

    def wheelEvent(self, e):
        if self.pm is None or self.crop is not None:
            return
        f = 1.2 if e.angleDelta().y() > 0 else 1 / 1.2
        cur = self._scale()
        new = max(0.05, min(8.0, cur * f))
        fit = min(self.width() / self.pm.width(), self.height() / self.pm.height())
        if new <= fit * 1.001:
            self.zoom_fit = True
        else:
            if self.zoom_fit:
                self.center = self._to_img(e.position())
            self.zoom_fit = False
            self.zoom = new
        self.update()


def fit_aspect(box, aspect: float, w: int, h: int, handle: str = "br") -> tuple:
    """把裁剪框调成 aspect（图片像素的宽 / 高），尽量保持被拖动的那个角以外的对角不动，并留在图片里。"""
    x0, y0, x1, y1 = box
    bw, bh = (x1 - x0) * w, (y1 - y0) * h
    if handle in ("t", "b"):
        bw = bh * aspect
    elif handle in ("l", "r"):
        bh = bw / aspect
    elif bw / max(1e-6, bh) > aspect:
        bw = bh * aspect
    else:
        bh = bw / aspect
    nw, nh = bw / w, bh / h
    if nw > 1:
        nh /= nw
        nw = 1.0
    if nh > 1:
        nw /= nh
        nh = 1.0
    ax = x1 if "l" in handle else x0
    ay = y1 if "t" in handle else y0
    if handle in ("t", "b"):
        ax = (x0 + x1) / 2
    if handle in ("l", "r"):
        ay = (y0 + y1) / 2
    nx0 = ax - nw if "l" in handle else (ax - nw / 2 if handle in ("t", "b") else ax)
    ny0 = ay - nh if "t" in handle else (ay - nh / 2 if handle in ("l", "r") else ay)
    nx0 = min(max(0.0, nx0), 1.0 - nw)
    ny0 = min(max(0.0, ny0), 1.0 - nh)
    return nx0, ny0, nx0 + nw, ny0 + nh


def centered_aspect(aspect: float, w: int, h: int) -> tuple:
    """图片里最大的、居中的 aspect 比例框。"""
    if w / h > aspect:
        nw = aspect * h / w
        return ((1 - nw) / 2, 0.0, (1 + nw) / 2, 1.0)
    nh = w / aspect / h
    return (0.0, (1 - nh) / 2, 1.0, (1 + nh) / 2)
