"""液化对话框：左边是当前图层的预览，右边是工具和笔刷。确定后按原尺寸应用到当前图层（可撤销）。"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QButtonGroup, QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ...edit.liquify import Liquify
from ...i18n import t as _t
from ..dialogs import _Base
from ..widgets.deco import TacButton, caption
from .canvas import checker_brush
from .panels import ParamSlider

TOOL_NAMES = [("push", _t("向前变形")), ("reconstruct", _t("重建")), ("twirl_cw", _t("顺时针旋转扭曲")),
              ("twirl_ccw", _t("逆时针旋转扭曲")), ("pucker", _t("褶皱（收缩）")), ("bloat", _t("膨胀"))]


class _View(QWidget):
    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        self.setMinimumSize(640, 480)
        self.setMouseTracking(True)
        self.img: QImage | None = None
        self.buf = None
        self.last = None
        self.hover = None
        self.checker = checker_brush(8)

    def set_rgba(self, f: np.ndarray) -> None:
        self.buf = np.ascontiguousarray(np.clip(f * 255 + 0.5, 0, 255).astype(np.uint8))
        h, w = self.buf.shape[:2]
        self.img = QImage(self.buf.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
        self.update()

    def _rect(self) -> QRectF:
        lq = self.dlg.lq
        s = min((self.width() - 8) / lq.w, (self.height() - 8) / lq.h)
        w, h = lq.w * s, lq.h * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def to_img(self, p: QPointF):
        r = self._rect()
        k = self.dlg.lq.w / r.width()
        return (p.x() - r.left()) * k, (p.y() - r.top()) * k

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.dlg.lq.begin()
        self.last = self.to_img(e.position())
        self.dlg.press(*self.last)

    def mouseMoveEvent(self, e):
        self.hover = e.position()
        if self.last is not None:
            p = self.to_img(e.position())
            self.dlg.drag(self.last, p)
            self.last = p
        self.update()

    def mouseReleaseEvent(self, e):
        self.last = None
        self.dlg.release()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def paintEvent(self, e):
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor("#1E2023"))
        r = self._rect()
        qp.setBrushOrigin(r.topLeft())
        qp.fillRect(r, self.checker)
        if self.img is not None:
            qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            qp.drawImage(r, self.img)
        if self.hover is not None:
            rad = self.dlg.size() / 2 * r.width() / self.dlg.lq.w
            qp.setRenderHint(QPainter.RenderHint.Antialiasing)
            qp.setPen(QPen(QColor(0, 0, 0, 200), 1))
            qp.drawEllipse(self.hover, rad + 0.5, rad + 0.5)
            qp.setPen(QPen(QColor(255, 255, 255, 220), 1))
            qp.drawEllipse(self.hover, rad - 0.5, rad - 0.5)
        qp.end()


class LiquifyDialog(_Base):
    def __init__(self, win, rgba: np.ndarray):
        super().__init__(win, _t("液化"), "LIQUIFY")
        self.win = win
        self.setModal(True)
        self.lq = Liquify(rgba)
        self.result = None
        row = QHBoxLayout()
        self.view = _View(self)
        row.addWidget(self.view, 1)
        side = QVBoxLayout()
        grid = QGridLayout()
        self.group = QButtonGroup(self)
        self.tool = "push"
        for i, (k, t) in enumerate(TOOL_NAMES):
            b = QPushButton(t)
            b.setCheckable(True)
            b.setChecked(k == "push")
            b.clicked.connect(lambda _=False, kk=k: setattr(self, "tool", kk))
            self.group.addButton(b)
            grid.addWidget(b, i // 2, i % 2)
        side.addLayout(grid)
        self.size_s = ParamSlider(_t("大小"), 5, 600, 120, 0, " px")
        self.press_s = ParamSlider(_t("压力"), 1, 100, 50, 0, "%")
        side.addWidget(self.size_s)
        side.addWidget(self.press_s)
        r = QHBoxLayout()
        b_undo = QPushButton(_t("撤销一笔"))
        b_undo.clicked.connect(lambda: self.lq.undo() and self._refresh())
        b_reset = QPushButton(_t("全部恢复"))
        b_reset.clicked.connect(lambda: (self.lq.reset(), self._refresh()))
        r.addWidget(b_undo)
        r.addWidget(b_reset)
        side.addLayout(r)
        side.addWidget(caption(_t("向前变形：按住拖动，像推开湿颜料一样把画面推过去（瘦脸、收腰、理顺头发）。"
                                  "膨胀 / 褶皱 / 旋转扭曲：按住不动也会持续作用。重建：把涂过的地方慢慢恢复原样。"
                                  "笔刷大小是原图像素。"), "faint", 8.5))
        side.addStretch(1)
        row.addLayout(side)
        self.body.addLayout(row)
        bc = TacButton(_t("取消"), "secondary")
        bo = TacButton(_t("确定"), "primary")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self._ok)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)
        self._hold = QTimer(self)
        self._hold.setInterval(40)
        self._hold.timeout.connect(self._tick)
        self._pos = None
        self.resize(1180, 760)
        self._refresh()

    def size(self) -> float:                                  # 预览像素
        return float(self.size_s.value()) * self.lq.s

    def pressure(self) -> float:
        return float(self.press_s.value()) / 100

    def _refresh(self) -> None:
        self.view.set_rgba(self.lq.preview())

    def press(self, x, y) -> None:
        self._pos = (x, y)
        if self.tool != "push":
            self.lq.dab(self.tool, x, y, self.size(), self.pressure())
            self._hold.start()
            self._refresh()

    def drag(self, a, b) -> None:
        self._pos = b
        if self.tool == "push":
            self.lq.stroke_to("push", a[0], a[1], b[0], b[1], self.size(), self.pressure())
        else:
            self.lq.dab(self.tool, b[0], b[1], self.size(), self.pressure())
        self._refresh()

    def release(self) -> None:
        self._hold.stop()
        self._pos = None

    def _tick(self) -> None:
        if self._pos is not None:
            self.lq.dab(self.tool, *self._pos, self.size(), self.pressure())
            self._refresh()

    def _ok(self) -> None:
        if self.lq.changed():
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.result = self.lq.apply()
            finally:
                QApplication.restoreOverrideCursor()
        self.accept()
