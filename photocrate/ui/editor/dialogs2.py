"""修图窗口第二批对话框：色彩范围、选择并遮住、载入选区、图层样式。"""
from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget)

from ...edit import select as S
from ...edit import styles as ST
from ...edit.document import to8
from ...i18n import t as _t
from ..dialogs import _Base
from ..widgets.deco import TacButton, caption
from .panels import ColorButton, ParamSlider

PREVIEW = 640


def _small(doc, side: int = PREVIEW):
    """缩小的合成图（8 位 RGBA）和缩放倍数。"""
    import cv2
    rgba, f = doc.flat_level(side)
    h, w = rgba.shape[:2]
    s = min(1.0, side / max(h, w))
    if s < 1:
        rgba = cv2.resize(rgba, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(rgba), (rgba.shape[1] / doc.width)


class _Pic(QLabel):
    clicked = Signal(float, float, object)      # 相对坐标 0–1、鼠标事件

    def __init__(self):
        super().__init__()
        self.setMinimumSize(QSize(420, 320))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: #1E2023;")
        self._r = None

    def show_rgb(self, rgb8: np.ndarray) -> None:
        rgb8 = np.ascontiguousarray(rgb8)
        h, w = rgb8.shape[:2]
        fmt = QImage.Format.Format_Grayscale8 if rgb8.ndim == 2 else QImage.Format.Format_RGB888
        bpl = w if rgb8.ndim == 2 else w * 3
        img = QImage(rgb8.data, w, h, bpl, fmt).copy()
        pm = QPixmap.fromImage(img).scaled(max(10, self.width() - 6), max(10, self.height() - 6),
                                           Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(pm)
        self._r = ((self.width() - pm.width()) / 2, (self.height() - pm.height()) / 2, pm.width(), pm.height())

    def mousePressEvent(self, e):
        if self._r is not None:
            x0, y0, w, h = self._r
            u, v = (e.position().x() - x0) / w, (e.position().y() - y0) / h
            if 0 <= u <= 1 and 0 <= v <= 1:
                self.clicked.emit(u, v, e)


def _ok_cancel(dlg, ok_text=None):
    bc = TacButton(_t("取消"), "secondary")
    bo = TacButton(ok_text or _t("确定"), "primary")
    bc.clicked.connect(dlg.reject)
    bo.clicked.connect(dlg.accept)
    dlg.buttons.addWidget(bc)
    dlg.buttons.addWidget(bo)


# ---------------------------------------------------------------------------
# 色彩范围
# ---------------------------------------------------------------------------

class ColorRangeDialog(_Base):
    def __init__(self, win):
        super().__init__(win, _t("色彩范围"), "COLOR RANGE")
        self.setModal(True)
        self.doc = win.doc
        self.small, self.s = _small(self.doc)
        self.samples: list = []
        row = QHBoxLayout()
        self.pic = _Pic()
        self.pic.clicked.connect(self._click)
        row.addWidget(self.pic, 1)
        side = QVBoxLayout()
        self.preset = QComboBox()
        for k, t in S.RANGE_PRESETS:
            self.preset.addItem(t, k)
        side.addWidget(QLabel(_t("选择：")))
        side.addWidget(self.preset)
        self.fuzz = ParamSlider(_t("颜色容差"), 0, 200, 40)
        side.addWidget(self.fuzz)
        self.invert = QCheckBox(_t("反相"))
        side.addWidget(self.invert)
        self.view = QComboBox()
        self.view.addItem(_t("预览：选区（白 = 选中）"), "mask")
        self.view.addItem(_t("预览：图像"), "image")
        side.addWidget(self.view)
        side.addWidget(caption(_t("「取样颜色」：在左边的图上点一下要选的颜色；按住 Shift 点 = 再加一种颜色，"
                                  "按住 Alt 点 = 去掉一种。"), "faint", 8.5))
        side.addStretch(1)
        row.addLayout(side)
        self.body.addLayout(row)
        _ok_cancel(self)
        for w in (self.preset, self.view):
            w.currentIndexChanged.connect(lambda _=0: self._update())
        self.fuzz.changed.connect(lambda _=0: self._update())
        self.invert.toggled.connect(lambda _=False: self._update())
        self.resize(980, 560)
        QTimer.singleShot(0, self._update)

    def _click(self, u, v, e):
        h, w = self.small.shape[:2]
        x, y = int(u * (w - 1)), int(v * (h - 1))
        lab = S.sample_lab(self.small, x, y, 1)
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.AltModifier and self.samples:
            d = [float(((np.asarray(s) - lab) ** 2).sum()) for s in self.samples]
            self.samples.pop(int(np.argmin(d)))
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            self.samples.append(lab)
        else:
            self.samples = [lab]
        self.preset.setCurrentIndex(0)
        self._update()

    def params(self):
        return dict(preset=self.preset.currentData(), samples=list(self.samples), fuzziness=self.fuzz.value(),
                    invert=self.invert.isChecked())

    def mask_for(self, rgb8):
        return S.color_range(rgb8, **self.params())

    def _update(self):
        m = self.mask_for(self.small)
        if self.view.currentData() == "mask":
            self.pic.show_rgb(m)
        else:
            self.pic.show_rgb(self.small[..., :3])

    def full_mask(self) -> np.ndarray:
        return self.mask_for(self.doc.flat_rgba())


# ---------------------------------------------------------------------------
# 选择并遮住
# ---------------------------------------------------------------------------

REFINE_OUT = [("selection", _t("选区")), ("mask", _t("图层蒙版")), ("new_layer", _t("新建带有图层蒙版的图层"))]


class RefineDialog(_Base):
    def __init__(self, win):
        super().__init__(win, _t("选择并遮住"), "SELECT AND MASK")
        self.setModal(True)
        self.doc = win.doc
        import cv2
        self.small, self.s = _small(self.doc)
        sel = self.doc.selection
        self.sel_small = cv2.resize(sel, (self.small.shape[1], self.small.shape[0]), interpolation=cv2.INTER_AREA)
        row = QHBoxLayout()
        self.pic = _Pic()
        row.addWidget(self.pic, 1)
        side = QVBoxLayout()
        self.view = QComboBox()
        for k, t in (("overlay", _t("视图：叠加（红色 = 没选中）")), ("black", _t("视图：黑底")),
                     ("white", _t("视图：白底")), ("mask", _t("视图：黑白"))):
            self.view.addItem(t, k)
        side.addWidget(self.view)
        self.radius = ParamSlider(_t("边缘检测半径"), 0, 250, 10, 0, " px")
        self.smooth = ParamSlider(_t("平滑"), 0, 100, 0)
        self.feather = ParamSlider(_t("羽化"), 0, 250, 0, 1, " px")
        self.contrast = ParamSlider(_t("对比度"), 0, 100, 0, 0, "%")
        self.shift = ParamSlider(_t("移动边缘"), -100, 100, 0, 0, "%")
        for w in (self.radius, self.smooth, self.feather, self.contrast, self.shift):
            side.addWidget(w)
            w.changed.connect(lambda _=0: self._t.start())
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("输出到：")))
        self.out = QComboBox()
        for k, t in REFINE_OUT:
            self.out.addItem(t, k)
        r.addWidget(self.out, 1)
        side.addLayout(r)
        side.addWidget(caption(_t("边缘检测半径：在选区边缘这么宽的一圈里，按照片本身的明暗边缘重新算选区——"
                                  "头发、树枝、毛边能跟着画面走。先用「选择主体」或对象选择大致选好，再来这里精修。"),
                               "faint", 8.5))
        side.addStretch(1)
        row.addLayout(side)
        self.body.addLayout(row)
        _ok_cancel(self)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(120)
        self._t.timeout.connect(self._update)
        self.view.currentIndexChanged.connect(lambda _=0: self._update())
        self.resize(1040, 600)
        QTimer.singleShot(0, self._update)

    def params(self, s: float = 1.0) -> dict:
        return dict(radius=self.radius.value() * s, smooth=self.smooth.value(), feather=self.feather.value() * s,
                    contrast=self.contrast.value(), shift=self.shift.value())

    def _update(self):
        m = S.refine(self.small, self.sel_small, **self.params(self.s)).astype(np.float32)[..., None] / 255
        img = self.small[..., :3].astype(np.float32)
        v = self.view.currentData()
        if v == "mask":
            self.pic.show_rgb((m[..., 0] * 255).astype(np.uint8))
            return
        if v == "overlay":
            bg = img * 0.45 + np.array([230, 40, 40], np.float32) * 0.55
        elif v == "black":
            bg = np.zeros_like(img)
        else:
            bg = np.full_like(img, 255)
        self.pic.show_rgb(np.clip(bg + (img - bg) * m, 0, 255).astype(np.uint8))

    def full_mask(self) -> np.ndarray:
        return S.refine(self.doc.flat_rgba(), self.doc.selection, **self.params(1.0))


# ---------------------------------------------------------------------------
# 载入选区
# ---------------------------------------------------------------------------

class LoadSelectionDialog(_Base):
    def __init__(self, win):
        super().__init__(win, _t("载入选区"), "LOAD SELECTION")
        self.doc = win.doc
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("通道：")))
        self.ch = QComboBox()
        for n in self.doc.channels:
            self.ch.addItem(n, n)
        r.addWidget(self.ch, 1)
        b = TacButton(_t("删除这个通道"), "secondary", height=28)
        b.clicked.connect(self._delete)
        r.addWidget(b)
        self.body.addLayout(r)
        self.invert = QCheckBox(_t("反相"))
        self.body.addWidget(self.invert)
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("操作：")))
        self.mode = QComboBox()
        for k, t in (("new", _t("新建选区")), ("add", _t("添加到选区")), ("sub", _t("从选区中减去")),
                     ("and", _t("与选区交叉"))):
            self.mode.addItem(t, k)
        r.addWidget(self.mode, 1)
        self.body.addLayout(r)
        _ok_cancel(self, _t("载入"))

    def _delete(self):
        n = self.ch.currentData()
        if n:
            self.doc.delete_channel(n)
            self.ch.removeItem(self.ch.currentIndex())
            if not self.ch.count():
                self.reject()


# ---------------------------------------------------------------------------
# 图层样式
# ---------------------------------------------------------------------------

class LayerStyleDialog(_Base):
    """左边勾选效果，右边改参数；画布上实时预览，确定后记一步历史，取消恢复原样。"""

    def __init__(self, win, layer):
        super().__init__(win, _t("图层样式"), "LAYER STYLE")
        self.setModal(True)
        self.win, self.layer = win, layer
        self.orig = copy.deepcopy(layer.style or {})
        self.style = ST.normalize(layer.style)
        row = QHBoxLayout()
        self.list = QListWidget()
        self.list.setFixedWidth(170)
        self.stack = QStackedWidget()
        for k, name in ST.EFFECTS:
            it = QListWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if self.style[k]["on"] else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, k)
            self.list.addItem(it)
            self.stack.addWidget(self._page(k))
        self.list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.list.itemChanged.connect(self._checked)
        row.addWidget(self.list)
        row.addWidget(self.stack, 1)
        self.body.addLayout(row)
        self.body.addWidget(caption(_t("效果跟着图层的形状走（文字、抠出来的主体效果最好）；以后可以随时改，「向下合并 / 拼合」时才会变成像素。"),
                                    "faint", 8.5))
        _ok_cancel(self)
        first = next((i for i, (k, _) in enumerate(ST.EFFECTS) if self.style[k]["on"]), 0)
        self.list.setCurrentRow(first)
        self.resize(640, 420)

    def _page(self, k) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        q = self.style[k]
        for ck in ST.COLORS.get(k, []):
            r = QHBoxLayout()
            r.addWidget(QLabel({"color": _t("颜色"), "c0": _t("起点颜色"), "c1": _t("终点颜色")}[ck]))
            b = ColorButton(q[ck])
            b.changed.connect(lambda c, kk=ck: self._set(k, kk, c.name().upper()))
            r.addWidget(b)
            r.addStretch(1)
            lay.addLayout(r)
        if k == "stroke":
            cb = QComboBox()
            for v, t in ST.STROKE_POS:
                cb.addItem(t, v)
            cb.setCurrentIndex(max(0, cb.findData(q.get("position", "outside"))))
            cb.currentIndexChanged.connect(lambda _i: self._set(k, "position", cb.currentData()))
            r = QHBoxLayout()
            r.addWidget(QLabel(_t("位置")))
            r.addWidget(cb, 1)
            lay.addLayout(r)
        for key, label, lo, hi, unit in ST.PARAMS[k]:
            s = ParamSlider(label, lo, hi, ST.DEFAULTS[k][key], 0, unit)
            s.set_value(q[key])
            s.changed.connect(lambda v, kk=key: self._set(k, kk, float(v)))
            lay.addWidget(s)
        lay.addStretch(1)
        return w

    def _checked(self, it):
        k = it.data(Qt.ItemDataRole.UserRole)
        self.style[k]["on"] = it.checkState() == Qt.CheckState.Checked
        self._preview()

    def _set(self, k, key, v):
        self.style[k][key] = v
        if not self.style[k]["on"]:
            self.style[k]["on"] = True
            it = self.list.item([e for e, _ in ST.EFFECTS].index(k))
            self.list.blockSignals(True)
            it.setCheckState(Qt.CheckState.Checked)
            self.list.blockSignals(False)
        self._preview()

    def result_style(self) -> dict:
        return {k: v for k, v in self.style.items() if v.get("on")}

    def _preview(self):
        self.layer.style = self.result_style()
        self.layer.version += 1
        self.win.doc.version += 1
        self.win.mark_interactive()
        self.win.canvas.request_render(True)

    def done(self, r):
        self.layer.style = self.orig                       # 先恢复，确定时再按历史记录改
        self.layer.version += 1
        self.win.doc.version += 1
        self.win.canvas.request_render()
        super().done(r)
