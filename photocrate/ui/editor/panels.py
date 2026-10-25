"""修图窗口右侧的面板：直方图、属性（调整参数 / 文字 / 蒙版）、图层、历史记录；左侧工具栏底部的前景 / 背景色。"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QGridLayout,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ...edit import adjust
from ...edit.blend import BLEND_MODES, BLEND_NAMES
from ...edit.document import to8
from .. import theme
from ..theme import font
from .canvas import checker_brush
from .icons import icon_pixmap, paint_icon
from ...i18n import t as _t


# ---------------------------------------------------------------------------
# 小控件
# ---------------------------------------------------------------------------

class ParamSlider(QWidget):
    """名称 + 滑块 + 数值框。双击名称恢复默认值。"""
    changed = Signal(float)

    def __init__(self, label: str, lo: float, hi: float, default: float, decimals: int = 0, suffix: str = "",
                 color: str | None = None, parent=None):
        super().__init__(parent)
        self.default = float(default)
        self.k = 10 ** decimals
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.lab = QLabel(label)
        self.lab.setFixedWidth(min(130, max(70, QFontMetrics(self.lab.font()).horizontalAdvance(label) + 6)))
        self.lab.setToolTip(_t("双击恢复默认值"))
        if color:
            self.lab.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(int(round(lo * self.k)), int(round(hi * self.k)))
        self.slider.setMinimumWidth(80)
        if decimals:
            self.spin = QDoubleSpinBox()
            self.spin.setDecimals(decimals)
            self.spin.setSingleStep(1 / self.k)
        else:
            self.spin = QSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(64 if not suffix else 74)
        # 不用 NoButtons：配合全局样式表时输入框会被算成 1 像素宽、数字看不见；改成把上下按钮的宽度设成 0
        self.spin.setStyleSheet(
            "QAbstractSpinBox { padding: 2px 4px; }"
            "QAbstractSpinBox::up-button { subcontrol-origin: border; width: 0px; border: none; }"
            "QAbstractSpinBox::down-button { subcontrol-origin: border; width: 0px; border: none; }"
            "QAbstractSpinBox::up-arrow, QAbstractSpinBox::down-arrow { image: none; width: 0px; height: 0px; }")
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.lab)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.set_value(default)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.lab.mouseDoubleClickEvent = lambda e: self._reset()

    def value(self) -> float:
        return float(self.spin.value())

    def set_value(self, v: float) -> None:
        for w in (self.slider, self.spin):
            w.blockSignals(True)
        self.slider.setValue(int(round(float(v) * self.k)))
        self.spin.setValue(float(v) if self.k > 1 else int(round(float(v))))
        for w in (self.slider, self.spin):
            w.blockSignals(False)

    def _from_slider(self, iv: int) -> None:
        v = iv / self.k
        self.spin.blockSignals(True)
        self.spin.setValue(v if self.k > 1 else int(v))
        self.spin.blockSignals(False)
        self.changed.emit(v)

    def _from_spin(self, v) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(float(v) * self.k)))
        self.slider.blockSignals(False)
        self.changed.emit(float(v))

    def _reset(self) -> None:
        self.set_value(self.default)
        self.changed.emit(self.default)


class ColorButton(QPushButton):
    changed = Signal(QColor)

    def __init__(self, color: QColor | str = "#FFFFFF", parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(46, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)

    def set_color(self, c) -> None:
        self.color = QColor(c)
        self.update()

    def _pick(self) -> None:
        c = QColorDialog.getColor(self.color, self, _t("选择颜色"))
        if c.isValid():
            self.set_color(c)
            self.changed.emit(c)

    def paintEvent(self, e):
        qp = QPainter(self)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        qp.fillRect(r, self.color)
        qp.setPen(QPen(QColor(theme.current().ink2), 1))
        qp.drawRect(r)
        qp.end()


class Section(QWidget):
    """可折叠的一组控件。"""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.btn = QToolButton()
        self.btn.setText(title)
        self.btn.setCheckable(True)
        self.btn.setChecked(expanded)
        self.btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.btn.setFont(font("cn", 9.5, 700))
        self.btn.setStyleSheet("QToolButton { border: none; padding: 4px 0; }")
        self.btn.toggled.connect(self._toggle)
        self.body_w = QWidget(self)          # 先有父控件再 setVisible，否则会变成一个单独的顶层窗口
        self.body = QVBoxLayout(self.body_w)
        self.body.setContentsMargins(4, 0, 0, 6)
        self.body.setSpacing(4)
        lay.addWidget(self.btn)
        lay.addWidget(self.body_w)
        self.body_w.setVisible(expanded)

    def _toggle(self, on: bool) -> None:
        self.body_w.setVisible(on)
        self.btn.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)

    def expand(self) -> None:
        self.btn.setChecked(True)


# ---------------------------------------------------------------------------
# 直方图
# ---------------------------------------------------------------------------

class Histogram(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(78)
        self.h: np.ndarray | None = None
        self.stats = ""

    def set_rgba(self, buf: np.ndarray | None) -> None:
        if buf is None or buf.size == 0:
            self.h = None
            self.update()
            return
        flat = buf.reshape(-1, 4)
        step = max(1, flat.shape[0] // 250_000)
        s = flat[::step]
        s = s[s[:, 3] > 0]
        if s.size == 0:
            self.h = None
            self.update()
            return
        hs = [np.bincount(s[:, i], minlength=256).astype(np.float32) for i in range(3)]
        lum = (s[:, 0] * 0.299 + s[:, 1] * 0.587 + s[:, 2] * 0.114).astype(np.uint8)
        hs.append(np.bincount(lum, minlength=256).astype(np.float32))
        self.h = np.stack(hs)
        lo = float((lum <= 2).mean()) * 100
        hi = float((lum >= 253).mean()) * 100
        self.stats = (_t('暗部溢出 {0:.1f}%', lo) if lo >= 0.5 else "") + ("   " if lo >= 0.5 and hi >= 0.5 else "") + \
                     (_t('高光溢出 {0:.1f}%', hi) if hi >= 0.5 else "")
        self.update()

    def paintEvent(self, e):
        qp = QPainter(self)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        qp.fillRect(r, QColor("#16181A"))
        if self.h is not None:
            qp.setRenderHint(QPainter.RenderHint.Antialiasing)
            top = float(np.percentile(self.h[:3, 2:254], 99.5)) or 1.0
            w, hgt = r.width(), r.height() - 4
            qp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            for i, col in enumerate(("#E0473A", "#3FBF5F", "#3A7BE0")):
                path = QPainterPath(QPointF(r.left(), r.bottom()))
                for x in range(256):
                    v = min(1.0, self.h[i, x] / top)
                    path.lineTo(r.left() + x / 255 * w, r.bottom() - v * hgt)
                path.lineTo(r.right(), r.bottom())
                path.closeSubpath()
                c = QColor(col)
                c.setAlpha(150)
                qp.fillPath(path, c)
            qp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            if self.stats:
                qp.setFont(font("cn", 8))
                qp.setPen(QColor("#E6A23C"))
                qp.drawText(r.adjusted(6, 3, -6, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, self.stats)
        qp.setPen(QPen(QColor("#2C3034"), 1))
        qp.drawRect(r)
        qp.end()


# ---------------------------------------------------------------------------
# 曲线
# ---------------------------------------------------------------------------

class CurveEditor(QWidget):
    """单击添加点，拖动点调整，把点拖出框外或双击删除（两端的点不能删）。"""
    changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(220)
        self.setMinimumWidth(200)
        self.pts: list[list[float]] = [[0, 0], [255, 255]]
        self.color = QColor("#E8E8E8")
        self.hist: np.ndarray | None = None
        self.drag: int | None = None
        self.setMouseTracking(True)

    def set_points(self, pts, color: str = "#E8E8E8") -> None:
        self.pts = sorted([list(map(float, p)) for p in pts]) or [[0, 0], [255, 255]]
        self.color = QColor(color)
        self.update()

    def _area(self) -> QRectF:
        return QRectF(self.rect()).adjusted(8, 8, -8, -8)

    def _to_w(self, x, y) -> QPointF:
        a = self._area()
        return QPointF(a.left() + x / 255 * a.width(), a.bottom() - y / 255 * a.height())

    def _from_w(self, p: QPointF) -> tuple[float, float]:
        a = self._area()
        return ((p.x() - a.left()) / a.width() * 255, (a.bottom() - p.y()) / a.height() * 255)

    def _near(self, p: QPointF) -> int | None:
        for i, (x, y) in enumerate(self.pts):
            q = self._to_w(x, y)
            if abs(q.x() - p.x()) <= 7 and abs(q.y() - p.y()) <= 7:
                return i
        return None

    def mousePressEvent(self, e):
        i = self._near(e.position())
        if i is None:
            x, y = self._from_w(e.position())
            x = min(255.0, max(0.0, x))
            lut = adjust.curve_lut(self.pts, 256)
            y = float(lut[int(round(x))] * 255)
            self.pts.append([x, y])
            self.pts.sort()
            i = self.pts.index([x, y])
            self.changed.emit(self.pts)
        self.drag = i
        self.update()

    def mouseMoveEvent(self, e):
        if self.drag is None:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._near(e.position()) is not None
                           else Qt.CursorShape.CrossCursor)
            return
        x, y = self._from_w(e.position())
        i = self.drag
        end = i in (0, len(self.pts) - 1)
        out = x < -25 or x > 280 or y < -25 or y > 280
        if out and not end and len(self.pts) > 2:
            self.pts.pop(i)
            self.drag = None
        else:
            lo = self.pts[i - 1][0] + 1 if i > 0 else 0
            hi = self.pts[i + 1][0] - 1 if i < len(self.pts) - 1 else 255
            self.pts[i] = [min(hi, max(lo, x)), min(255.0, max(0.0, y))]
        self.changed.emit(self.pts)
        self.update()

    def mouseReleaseEvent(self, e):
        self.drag = None

    def mouseDoubleClickEvent(self, e):
        i = self._near(e.position())
        if i is not None and 0 < i < len(self.pts) - 1:
            self.pts.pop(i)
            self.changed.emit(self.pts)
            self.update()

    def paintEvent(self, e):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = self._area()
        qp.fillRect(QRectF(self.rect()), QColor("#16181A"))
        if self.hist is not None:
            top = float(np.percentile(self.hist, 99)) or 1.0
            path = QPainterPath(QPointF(a.left(), a.bottom()))
            for x in range(256):
                path.lineTo(a.left() + x / 255 * a.width(), a.bottom() - min(1.0, self.hist[x] / top) * a.height())
            path.lineTo(a.right(), a.bottom())
            path.closeSubpath()
            qp.fillPath(path, QColor(255, 255, 255, 28))
        qp.setPen(QPen(QColor("#33373C"), 1))
        for k in range(1, 4):
            qp.drawLine(QPointF(a.left() + a.width() * k / 4, a.top()), QPointF(a.left() + a.width() * k / 4, a.bottom()))
            qp.drawLine(QPointF(a.left(), a.top() + a.height() * k / 4), QPointF(a.right(), a.top() + a.height() * k / 4))
        qp.drawRect(a)
        qp.setPen(QPen(QColor("#4A4F55"), 1, Qt.PenStyle.DashLine))
        qp.drawLine(a.bottomLeft(), a.topRight())
        lut = adjust.curve_lut(self.pts, 256)
        path = QPainterPath(self._to_w(0, lut[0] * 255))
        for x in range(1, 256):
            path.lineTo(self._to_w(x, lut[x] * 255))
        qp.setPen(QPen(self.color, 2))
        qp.drawPath(path)
        qp.setPen(QPen(QColor("#000000"), 1))
        qp.setBrush(self.color)
        for x, y in self.pts:
            q = self._to_w(x, y)
            qp.drawRect(QRectF(q.x() - 4, q.y() - 4, 8, 8))
        qp.end()


# ---------------------------------------------------------------------------
# 属性面板：调整图层
# ---------------------------------------------------------------------------

HSL_COLORS = {"red": "#E5484D", "orange": "#F08A24", "yellow": "#D9B310", "green": "#3FA34D", "aqua": "#1FB5B5",
              "blue": "#3A6FE0", "purple": "#8E4EC6", "magenta": "#D6409F"}
CURVE_COLORS = {"rgb": "#E8E8E8", "r": "#F0605A", "g": "#5BD07A", "b": "#5A8DF0"}


class AdjustEditor(QWidget):
    edited = Signal(str, str)           # (键, 中文名)：参数已改到 self.params 里
    preset = Signal(str)

    SECTION_ORDER = ["基本", "颜色", "曲线", "HSL", "细节", "效果", "色阶", "分离色调",
                     "色彩平衡", "可选颜色", "通道混合器", "照片滤镜", "黑白", "渐变映射"]
    EXTRA = {"色彩平衡": "color_balance", "可选颜色": "selective", "通道混合器": "mixer", "照片滤镜": "photo_filter",
             "黑白": "bw", "渐变映射": "gradient_map"}

    def __init__(self, params: dict, focus: str | None = None, parent=None):
        super().__init__(parent)
        self.params = adjust.normalize(params)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        r = QHBoxLayout()
        self.presets = QComboBox()
        self.presets.addItem(_t("套用预设…"), None)
        for name in adjust.PRESETS:
            self.presets.addItem(_t(name), name)
        self.presets.activated.connect(self._preset)
        r.addWidget(self.presets, 1)
        b = QPushButton(_t("全部复位"))
        b.clicked.connect(lambda: self.preset.emit(""))
        r.addWidget(b)
        lay.addLayout(r)
        self.sliders: dict[str, ParamSlider] = {}
        self.sections: dict[str, Section] = {}
        self._syncers: list = []                      # 撤销 / 重做后把控件同步回参数
        groups = dict(adjust.GROUPS)
        for name in self.SECTION_ORDER:
            on = name in self.EXTRA and self.params[self.EXTRA[name]].get("on")
            sec = Section(_t(name), expanded=(name == focus) if focus else (name in ("基本", "颜色") or bool(on)))
            self.sections[name] = sec
            if name == "曲线":
                self._build_curve(sec)
            elif name == "HSL":
                self._build_hsl(sec)
            elif name in self.EXTRA:
                self._build_extra(sec, self.EXTRA[name])
            else:
                for key in groups[name]:
                    label, lo, hi, dec = adjust.SLIDERS[key]
                    s = ParamSlider(label, lo, hi, adjust.default_params()[key], dec)
                    s.set_value(self.params[key])
                    s.changed.connect(lambda v, k=key, lb=label: self._set(k, v, lb))
                    self.sliders[key] = s
                    sec.body.addWidget(s)
                if name == "效果":
                    self.cb_invert = QCheckBox(_t("反相"))
                    self.cb_invert.setChecked(bool(self.params.get("invert")))
                    self.cb_invert.toggled.connect(lambda on: self._set("invert", on, _t("反相")))
                    sec.body.addWidget(self.cb_invert)
            lay.addWidget(sec)
        lay.addStretch(1)

    def _build_curve(self, sec: Section) -> None:
        r = QHBoxLayout()
        self.curve_ch = QComboBox()
        for k, t in adjust.CURVE_CHANNELS:
            self.curve_ch.addItem(t, k)
        self.curve_ch.currentIndexChanged.connect(lambda _: self._show_curve())
        r.addWidget(self.curve_ch, 1)
        b = QPushButton(_t("复位曲线"))
        b.clicked.connect(self._reset_curve)
        r.addWidget(b)
        sec.body.addLayout(r)
        self.curve = CurveEditor()
        self.curve.changed.connect(self._curve_changed)
        sec.body.addWidget(self.curve)
        self._show_curve()

    def _show_curve(self) -> None:
        ch = self.curve_ch.currentData()
        self.curve.set_points(self.params["curve"][ch], CURVE_COLORS[ch])

    def _curve_changed(self, pts) -> None:
        ch = self.curve_ch.currentData()
        self.params["curve"][ch] = [[round(x, 1), round(y, 1)] for x, y in pts]
        self.edited.emit(f"curve_{ch}", _t("曲线"))

    def _reset_curve(self) -> None:
        ch = self.curve_ch.currentData()
        self.params["curve"][ch] = [list(p) for p in adjust.IDENTITY_CURVE]
        self._show_curve()
        self.edited.emit(f"curve_{ch}", _t("曲线"))

    def _build_hsl(self, sec: Section) -> None:
        self.hsl_mode = QComboBox()
        for i, t in enumerate((_t("色相"), _t("饱和度"), _t("明度"))):
            self.hsl_mode.addItem(t, i)
        self.hsl_mode.currentIndexChanged.connect(lambda _: self._show_hsl())
        sec.body.addWidget(self.hsl_mode)
        self.hsl_sliders: dict[str, ParamSlider] = {}
        for band, label, _ in adjust.HSL_BANDS:
            s = ParamSlider(label, -100, 100, 0, color=HSL_COLORS[band])
            s.changed.connect(lambda v, b=band: self._set_hsl(b, v))
            self.hsl_sliders[band] = s
            sec.body.addWidget(s)
        self._show_hsl()

    def _show_hsl(self) -> None:
        i = self.hsl_mode.currentData() or 0
        for band, s in self.hsl_sliders.items():
            s.set_value(self.params["hsl"][band][i])

    def _set_hsl(self, band: str, v: float) -> None:
        i = self.hsl_mode.currentData() or 0
        self.params["hsl"][band][i] = float(v)
        self.edited.emit(f"hsl_{band}_{i}", "HSL")

    def _set(self, key: str, v, label: str) -> None:
        self.params[key] = v if isinstance(v, bool) else float(v)
        self.edited.emit(key, label)

    def _preset(self, i: int) -> None:
        name = self.presets.itemData(i)
        self.presets.setCurrentIndex(0)
        if name:
            self.preset.emit(name)

    def sync(self, params: dict) -> None:
        self.params = adjust.normalize(params)
        for k, s in self.sliders.items():
            s.set_value(self.params[k])
        self.cb_invert.blockSignals(True)
        self.cb_invert.setChecked(bool(self.params.get("invert")))
        self.cb_invert.blockSignals(False)
        self._show_curve()
        self._show_hsl()
        for fn in self._syncers:
            fn()

    # ---- 黑白 / 通道混合器 / 可选颜色 / 照片滤镜 / 渐变映射 / 色彩平衡 ----
    def _build_extra(self, sec: Section, adj: str) -> None:
        from ...edit import coloradj as C
        q = lambda: self.params[adj]                                       # noqa: E731
        label = C.NAMES[adj]
        on = QCheckBox(_t("启用"))
        sec.body.addWidget(on)

        def sync_on():
            on.blockSignals(True)
            on.setChecked(bool(q().get("on")))
            on.blockSignals(False)
        on.toggled.connect(lambda v: self._set_sub(adj, "on", bool(v), label, sync_on))
        self._syncers.append(sync_on)
        sync_on()

        def slider(text, lo, hi, get, put, color=None):
            s = ParamSlider(text, lo, hi, 0, color=color)
            s.changed.connect(lambda v: (put(float(v)), self._touch(adj, label, sync_on)))
            self._syncers.append(lambda: s.set_value(get()))
            s.set_value(get())
            sec.body.addWidget(s)
            return s

        def combo(items, cur_get, on_change):
            cb = QComboBox()
            for k, t in items:
                cb.addItem(t, k)
            cb.currentIndexChanged.connect(lambda _: on_change(cb.currentData()))
            sec.body.addWidget(cb)
            return cb

        def check(text, key):
            c = QCheckBox(text)
            c.toggled.connect(lambda v: self._set_sub(adj, key, bool(v), label, sync_on))

            def sync():
                c.blockSignals(True)
                c.setChecked(bool(q().get(key)))
                c.blockSignals(False)
            self._syncers.append(sync)
            sync()
            sec.body.addWidget(c)

        if adj == "bw":
            for k, t, _ in C.BW_KEYS:
                slider(t, -200, 300, lambda k=k: q()[k], lambda v, k=k: q().__setitem__(k, v))
            check(_t("色调"), "tint")
            slider(_t("色相"), 0, 360, lambda: q()["tint_hue"], lambda v: q().__setitem__("tint_hue", v))
            slider(_t("饱和度"), 0, 100, lambda: q()["tint_sat"], lambda v: q().__setitem__("tint_sat", v))
        elif adj == "mixer":
            st = {"ch": "r"}
            sl = []
            names = [_t("红色"), _t("绿色"), _t("蓝色"), _t("常数")]
            cb = combo([("r", _t("输出：红")), ("g", _t("输出：绿")), ("b", _t("输出：蓝")), ("gray", _t("输出：灰（单色）"))],
                       None, lambda v: (st.__setitem__("ch", v), [f() for f in self._syncers]))
            for i, n in enumerate(names):
                sl.append(slider(n, -200, 200, lambda i=i: q()[st["ch"]][i],
                                 lambda v, i=i: q()[st["ch"]].__setitem__(i, v)))
            check(_t("单色（输出灰度）"), "mono")
            cb.setCurrentIndex(0)
        elif adj == "selective":
            st = {"r": "reds"}
            combo(C.SEL_RANGES, None, lambda v: (st.__setitem__("r", v), [f() for f in self._syncers]))
            for i, n in enumerate((_t("青色"), _t("洋红"), _t("黄色"), _t("黑色"))):
                slider(n, -100, 100, lambda i=i: q()[st["r"]][i], lambda v, i=i: q()[st["r"]].__setitem__(i, v))
            rel = combo([(True, _t("相对")), (False, _t("绝对"))], None,
                        lambda v: self._set_sub(adj, "relative", bool(v), label, sync_on))
            self._syncers.append(lambda: (rel.blockSignals(True), rel.setCurrentIndex(0 if q().get("relative", True)
                                                                                        else 1), rel.blockSignals(False)))
        elif adj == "photo_filter":
            r = QHBoxLayout()
            cb = QComboBox()
            for c, t in C.PHOTO_FILTERS:
                cb.addItem(t, c)
            btn = ColorButton(q().get("color", "#EC8A00"))
            cb.currentIndexChanged.connect(lambda _: (btn.set_color(cb.currentData()),
                                                      self._set_sub(adj, "color", cb.currentData(), label, sync_on)))
            btn.changed.connect(lambda c: self._set_sub(adj, "color", c.name().upper(), label, sync_on))
            r.addWidget(cb, 1)
            r.addWidget(btn)
            sec.body.addLayout(r)
            self._syncers.append(lambda: btn.set_color(q().get("color", "#EC8A00")))
            slider(_t("浓度"), 1, 100, lambda: q()["density"], lambda v: q().__setitem__("density", v))
            check(_t("保留明度"), "preserve")
        elif adj == "gradient_map":
            cb = QComboBox()
            cb.addItem(_t("选择渐变…"), None)
            for k, t, _ in C.GRADIENTS:
                cb.addItem(t, k)
            grads = {k: s for k, _, s in C.GRADIENTS}
            cb.activated.connect(lambda i: cb.itemData(i) and self._set_sub(
                adj, "stops", [list(s) for s in grads[cb.itemData(i)]], label, sync_on))
            sec.body.addWidget(cb)
            r = QHBoxLayout()
            r.addWidget(QLabel(_t("暗部")))
            b0 = ColorButton(q()["stops"][0][1])
            r.addWidget(b0)
            r.addWidget(QLabel(_t("亮部")))
            b1 = ColorButton(q()["stops"][-1][1])
            r.addWidget(b1)
            r.addStretch(1)
            sec.body.addLayout(r)

            def ends(i, c):
                stops = [list(s) for s in q()["stops"]]
                stops[0 if i == 0 else -1][1] = c.name().upper()
                self._set_sub(adj, "stops", stops, label, sync_on)
            b0.changed.connect(lambda c: ends(0, c))
            b1.changed.connect(lambda c: ends(1, c))
            self._syncers.append(lambda: (b0.set_color(q()["stops"][0][1]), b1.set_color(q()["stops"][-1][1])))
            check(_t("反向"), "reverse")
        elif adj == "color_balance":
            st = {"t": "midtones"}
            cbt = combo(C.TONES, None, lambda v: (st.__setitem__("t", v), [f() for f in self._syncers]))
            for i, (a, b, col) in enumerate(((_t("青"), _t("红"), "#E5484D"), (_t("洋红"), _t("绿"), "#3FA34D"),
                                              (_t("黄"), _t("蓝"), "#3A6FE0"))):
                slider(f"{a} ↔ {b}", -100, 100, lambda i=i: q()[st["t"]][i],
                       lambda v, i=i: q()[st["t"]].__setitem__(i, v), col)
            check(_t("保持明度"), "preserve")
            cbt.setCurrentIndex(1)

    def _touch(self, adj: str, label: str, sync_on) -> None:
        """改了任何一个控件：自动打开这一项。"""
        if not self.params[adj].get("on"):
            self.params[adj]["on"] = True
            sync_on()
        self.edited.emit(adj, label)

    def _set_sub(self, adj: str, key: str, v, label: str, sync_on) -> None:
        self.params[adj][key] = v
        if key != "on":
            self.params[adj]["on"] = True
            sync_on()
        self.edited.emit(adj, label)

    def set_hist(self, h: np.ndarray | None) -> None:
        self.curve.hist = h
        self.curve.update()


# ---------------------------------------------------------------------------
# 属性面板：形状图层
# ---------------------------------------------------------------------------

class ShapeEditor(QWidget):
    edited = Signal(str)

    def __init__(self, params: dict, parent=None):
        super().__init__(parent)
        self.params = dict(params)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for key, label, default in (("fill", _t("填充"), "#E5484D"), ("stroke", _t("描边"), "#000000")):
            r = QHBoxLayout()
            cb = QCheckBox(label)
            cb.setChecked(bool(self.params.get(key)))
            btn = ColorButton(self.params.get(key) or default)
            cb.toggled.connect(lambda on, k=key, b=btn: self._set(k, b.color.name().upper() if on else None))
            btn.changed.connect(lambda c, k=key, c2=cb: (c2.blockSignals(True), c2.setChecked(True),
                                                         c2.blockSignals(False), self._set(k, c.name().upper())))
            r.addWidget(cb)
            r.addWidget(btn)
            r.addStretch(1)
            lay.addLayout(r)
        s = ParamSlider(_t("描边粗细"), 0, 200, 0, 0, " px")
        s.set_value(float(self.params.get("stroke_w", 0) or 0))
        s.changed.connect(lambda v: self._set("stroke_w", float(v)))
        lay.addWidget(s)
        t = self.params.get("type")
        if t == "rrect":
            s2 = ParamSlider(_t("圆角"), 0, 1000, 24, 0, " px")
            s2.set_value(float(self.params.get("radius", 24)))
            s2.changed.connect(lambda v: self._set("radius", float(v)))
            lay.addWidget(s2)
        if t == "polygon":
            s3 = ParamSlider(_t("边数"), 3, 64, 6)
            s3.set_value(float(self.params.get("sides", 6)))
            s3.changed.connect(lambda v: self._set("sides", int(v)))
            lay.addWidget(s3)
        tip = QLabel(_t("形状是矢量的：改颜色、描边随时可以；Ctrl+T 缩放、旋转、透视后仍然清晰。用画笔或滤镜时会先栅格化。"))
        tip.setWordWrap(True)
        tip.setProperty("role", "faint")
        tip.setFont(font("cn", 8.5))
        lay.addWidget(tip)

    def _set(self, key, v):
        self.params[key] = v
        self.edited.emit(key)

    def sync(self, params: dict) -> None:
        self.params = dict(params)


# ---------------------------------------------------------------------------
# 属性面板：文字图层
# ---------------------------------------------------------------------------

class TextEditor(QWidget):
    edited = Signal(str)

    def __init__(self, params: dict, parent=None):
        super().__init__(parent)
        from ...edit.text import available_fonts
        self.params = dict(params)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.text = QPlainTextEdit(self.params.get("text", ""))
        self.text.setFixedHeight(80)
        self.text.textChanged.connect(lambda: self._set("text", self.text.toPlainText()))
        lay.addWidget(self.text)
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        self.font_cb = QComboBox()
        for f in available_fonts():
            self.font_cb.addItem(_t(f), f)
        self.font_cb.setCurrentIndex(max(0, self.font_cb.findData(self.params.get("font", ""))))
        self.font_cb.currentIndexChanged.connect(lambda _i: self._set("font", self.font_cb.currentData()))
        g.addWidget(QLabel(_t("字体")), 0, 0)
        g.addWidget(self.font_cb, 0, 1, 1, 3)
        self.size = QSpinBox()
        self.size.setRange(4, 4000)
        self.size.setSuffix(" px")
        self.size.setValue(int(self.params.get("size", 96)))
        self.size.valueChanged.connect(lambda v: self._set("size", v))
        g.addWidget(QLabel(_t("字号")), 1, 0)
        g.addWidget(self.size, 1, 1)
        self.color = ColorButton(self.params.get("color", "#FFFFFF"))
        self.color.changed.connect(lambda c: self._set("color", c.name()))
        g.addWidget(QLabel(_t("颜色")), 1, 2)
        g.addWidget(self.color, 1, 3)
        self.align = QComboBox()
        for k, t in (("left", _t("左对齐")), ("center", _t("居中")), ("right", _t("右对齐"))):
            self.align.addItem(t, k)
        self.align.setCurrentIndex(max(0, self.align.findData(self.params.get("align", "left"))))
        self.align.currentIndexChanged.connect(lambda _: self._set("align", self.align.currentData()))
        g.addWidget(QLabel(_t("对齐")), 2, 0)
        g.addWidget(self.align, 2, 1)
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(0.8, 3.0)
        self.spacing.setSingleStep(0.1)
        self.spacing.setValue(float(self.params.get("spacing", 1.2)))
        self.spacing.valueChanged.connect(lambda v: self._set("spacing", v))
        g.addWidget(QLabel(_t("行距")), 2, 2)
        g.addWidget(self.spacing, 2, 3)
        self.stroke = QSpinBox()
        self.stroke.setRange(0, 200)
        self.stroke.setSuffix(" px")
        self.stroke.setValue(int(self.params.get("stroke", 0)))
        self.stroke.valueChanged.connect(lambda v: self._set("stroke", v))
        g.addWidget(QLabel(_t("描边")), 3, 0)
        g.addWidget(self.stroke, 3, 1)
        self.stroke_color = ColorButton(self.params.get("stroke_color", "#000000"))
        self.stroke_color.changed.connect(lambda c: self._set("stroke_color", c.name()))
        g.addWidget(QLabel(_t("描边色")), 3, 2)
        g.addWidget(self.stroke_color, 3, 3)
        lay.addLayout(g)
        self.shadow = QCheckBox(_t("投影"))
        self.shadow.setChecked(bool(self.params.get("shadow")))
        self.shadow.toggled.connect(lambda on: self._set("shadow", on))
        lay.addWidget(self.shadow)

    def _set(self, k: str, v) -> None:
        self.params[k] = v
        self.edited.emit(k)

    def sync(self, params: dict) -> None:
        self.params = dict(params)
        widgets = (self.text, self.font_cb, self.size, self.color, self.align, self.spacing, self.stroke,
                   self.stroke_color, self.shadow)
        for w in widgets:
            w.blockSignals(True)
        if self.text.toPlainText() != params.get("text", ""):
            self.text.setPlainText(params.get("text", ""))
        self.font_cb.setCurrentIndex(max(0, self.font_cb.findData(params.get("font", ""))))
        self.size.setValue(int(params.get("size", 96)))
        self.color.set_color(params.get("color", "#FFFFFF"))
        self.align.setCurrentIndex(max(0, self.align.findData(params.get("align", "left"))))
        self.spacing.setValue(float(params.get("spacing", 1.2)))
        self.stroke.setValue(int(params.get("stroke", 0)))
        self.stroke_color.set_color(params.get("stroke_color", "#000000"))
        self.shadow.setChecked(bool(params.get("shadow")))
        for w in widgets:
            w.blockSignals(False)


# ---------------------------------------------------------------------------
# 属性面板（按当前图层类型切换内容）
# ---------------------------------------------------------------------------

class PropertiesPanel(QScrollArea):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lid = None
        self.kind = None
        self.editor = None
        self._build_empty()

    def _container(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(8)
        return w, lay

    def _build_empty(self) -> None:
        w, lay = self._container()
        lay.addWidget(QLabel(_t("没有选中图层")))
        lay.addStretch(1)
        self.setWidget(w)

    def rebuild(self, focus: str | None = None) -> None:
        doc = self.win.doc
        l = doc.active if doc is not None else None
        self.lid, self.kind = (l.id, l.kind) if l else (None, None)
        if l is None:
            self._build_empty()
            return
        w, lay = self._container()
        title = QLabel({"adjust": _t("调整图层"), "text": _t("文字图层"), "pixel": _t("像素图层"),
                        "group": _t("图层组"), "shape": _t("形状图层"), "smart": _t("智能对象")}[l.kind] + f"  ·  {l.name}")
        title.setFont(font("cn", 10, 700))
        title.setWordWrap(True)
        lay.addWidget(title)
        self.editor = None
        if l.kind == "adjust":
            ed = AdjustEditor(l.params, focus)
            ed.edited.connect(lambda key, label: self.win.adjust_edited(key, label))
            ed.preset.connect(self.win.apply_preset)
            ed.set_hist(self.win.hist_lum())
            self.editor = ed
            lay.addWidget(ed)
        elif l.kind == "text":
            ed = TextEditor(l.params)
            ed.edited.connect(self.win.text_edited)
            self.editor = ed
            lay.addWidget(ed)
            b = QPushButton(_t("栅格化文字（变成普通像素图层）"))
            b.clicked.connect(lambda: doc.rasterize(l.id))
            lay.addWidget(b)
        elif l.kind == "shape":
            ed = ShapeEditor(l.params)
            ed.edited.connect(lambda key, lid=l.id: self.win.shape_edited(lid, key))
            self.editor = ed
            lay.addWidget(ed)
            b = QPushButton(_t("栅格化形状（变成普通像素图层）"))
            b.clicked.connect(lambda: doc.rasterize(l.id))
            lay.addWidget(b)
        elif l.kind == "smart":
            lay.addWidget(self._smart_box(l))
        elif l.kind == "group":
            n = len(doc.descendants(l.id))
            info = QLabel(_t('组里有 {0} 个图层。混合模式「穿透」= 组里的图层（包括调整图层）照常作用到下面；'
                             '换成别的模式时组先单独合成，组里的调整图层只调组里的图层。', n))
            info.setWordWrap(True)
            info.setProperty("role", "dim")
            lay.addWidget(info)
        else:
            info = QLabel(_t('{0} × {1} 像素，位置 ({2}, {3})\n可以用画笔、仿制图章、修复、滤镜直接改它；想要可反复修改的调色，用调整图层。', l.w, l.h, l.x, l.y))
            info.setWordWrap(True)
            info.setProperty("role", "dim")
            lay.addWidget(info)
        if l.kind != "text":
            lay.addWidget(self._mask_box(l))
        lay.addStretch(1)
        self.setWidget(w)

    def _smart_box(self, l) -> QWidget:
        """智能对象：说明 + 智能滤镜列表（勾选开关、双击重新设置、删除）+ 替换内容 / 栅格化。"""
        from ...edit.filters import FILTERS
        doc = self.win.doc
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        src = l.source
        info = QLabel(_t('原始内容 {0} × {1}。缩放、旋转、透视和滤镜都是参数，随时可改，放大缩小不会越来越糊。',
                         src.shape[1] if src is not None else 0, src.shape[0] if src is not None else 0))
        info.setWordWrap(True)
        info.setProperty("role", "dim")
        lay.addWidget(info)
        sec = Section(_t("智能滤镜"), True)
        flist = list(l.params.get("filters") or [])
        if not flist:
            t = QLabel(_t("还没有滤镜：选中这个图层后用「滤镜」菜单，滤镜会加在这里，以后还能改。"))
            t.setWordWrap(True)
            t.setProperty("role", "faint")
            sec.body.addWidget(t)
        for i, (fid, p, *rest) in enumerate(flist):
            r = QHBoxLayout()
            cb = QCheckBox(FILTERS[fid].name if fid in FILTERS else fid)
            cb.setChecked(bool(rest[0]) if rest else True)
            cb.toggled.connect(lambda on, k=i: self.win.smart_filter(l.id, k, "toggle", on))
            r.addWidget(cb, 1)
            be = QPushButton(_t("设置…"))
            be.clicked.connect(lambda _=False, k=i: self.win.smart_filter(l.id, k, "edit"))
            r.addWidget(be)
            bd = QPushButton(_t("删除"))
            bd.clicked.connect(lambda _=False, k=i: self.win.smart_filter(l.id, k, "delete"))
            r.addWidget(bd)
            sec.body.addLayout(r)
        lay.addWidget(sec)
        r = QHBoxLayout()
        b = QPushButton(_t("替换内容…"))
        b.clicked.connect(self.win.replace_smart)
        r.addWidget(b)
        b2 = QPushButton(_t("栅格化"))
        b2.clicked.connect(lambda: doc.rasterize(l.id))
        r.addWidget(b2)
        lay.addLayout(r)
        return w

    def _mask_box(self, l) -> QWidget:
        doc = self.win.doc
        sec = Section(_t("图层蒙版"), True)
        if l.mask is None:
            r = QHBoxLayout()
            b = QPushButton(_t("添加蒙版（全部显示）"))
            b.clicked.connect(lambda: doc.add_mask(l.id, "reveal"))
            r.addWidget(b)
            b2 = QPushButton(_t("全部隐藏"))
            b2.clicked.connect(lambda: doc.add_mask(l.id, "hide"))
            r.addWidget(b2)
            sec.body.addLayout(r)
            if doc.selection is not None:
                b3 = QPushButton(_t("用当前选区做蒙版"))
                b3.clicked.connect(lambda: doc.add_mask(l.id, "selection"))
                sec.body.addWidget(b3)
            tip = QLabel(_t("蒙版上白色显示、黑色隐藏。加上蒙版后用画笔 / 渐变在蒙版上画，就能只让一部分生效。"))
        else:
            cb = QCheckBox(_t("在蒙版上画（画笔、橡皮擦、渐变作用于蒙版）"))
            cb.setChecked(doc.mask_edit)
            cb.toggled.connect(self.win.set_mask_edit)
            sec.body.addWidget(cb)
            cb2 = QCheckBox(_t("停用蒙版"))
            cb2.setChecked(not l.mask_enabled)
            cb2.toggled.connect(lambda on: doc.set_props(l.id, name=_t("停用蒙版") if on else _t("启用蒙版"),
                                                          mask_enabled=not on))
            sec.body.addWidget(cb2)
            r = QHBoxLayout()
            for text, fn in ((_t("反相"), lambda: doc.invert_mask(l.id)), (_t("删除"), lambda: doc.delete_mask(l.id))):
                b = QPushButton(text)
                b.clicked.connect(fn)
                r.addWidget(b)
            if l.has_pixels:
                b = QPushButton(_t("应用到像素"))
                b.clicked.connect(lambda: doc.apply_mask(l.id))
                r.addWidget(b)
            sec.body.addLayout(r)
            tip = QLabel(_t("提示：前景色黑色 = 隐藏、白色 = 显示，按 X 交换，按 D 恢复黑白。"))
        tip.setWordWrap(True)
        tip.setProperty("role", "faint")
        tip.setFont(font("cn", 8.5))
        sec.body.addWidget(tip)
        return sec

    def sync(self) -> None:
        """撤销 / 重做后让控件跟上参数（不重建，免得打断正在拖的滑块）。"""
        doc = self.win.doc
        l = doc.active if doc is not None else None
        if l is None or l.id != self.lid or l.kind != self.kind:
            self.rebuild()
            return
        if self.editor is not None and hasattr(self.editor, "sync"):
            self.editor.sync(l.params)


# ---------------------------------------------------------------------------
# 图层面板
# ---------------------------------------------------------------------------

def layer_thumb(layer, w: int = 44, h: int = 34, mask: bool = False) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    qp = QPainter(pm)
    qp.fillRect(QRectF(0, 0, w, h), checker_brush(4))
    if mask:
        arr = layer.mask
    else:
        arr = layer.pixels if not layer.canvas_sized else None
    if arr is not None:
        lvl = max(0, int(math.ceil(math.log2(max(1, max(arr.shape[0] / h, arr.shape[1] / w))))))
        if mask:
            _, small = layer.proxy(lvl) if layer.mask_enabled else (None, None)
            if small is None:
                from ...edit.document import _downscale
                small = _downscale(arr, lvl)
            small = np.ascontiguousarray(small)
            img = QImage(small.data, small.shape[1], small.shape[0], small.shape[1], QImage.Format.Format_Grayscale8)
        else:
            small, _ = layer.proxy(lvl)
            small = np.ascontiguousarray(to8(small))
            img = QImage(small.data, small.shape[1], small.shape[0], small.shape[1] * 4,
                         QImage.Format.Format_RGBA8888)
        s = min(w / img.width(), h / img.height())
        tw, th = img.width() * s, img.height() * s
        qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        qp.drawImage(QRectF((w - tw) / 2, (h - th) / 2, tw, th), img)
    else:
        qp.fillRect(QRectF(0, 0, w, h), QColor("#2A2E33"))
        paint_icon(qp, QRectF(0, 0, w, h).adjusted(8, 4, -8, -4), "folder" if layer.kind == "group" else "adjust",
                   QColor("#E6E6E6"))
    if layer.kind in ("text", "shape", "smart") and not mask:
        qp.fillRect(QRectF(0, h - 13, 13, 13), QColor(0, 0, 0, 170))
        if layer.kind == "text":
            qp.setPen(QColor("#FFFFFF"))
            qp.setFont(font("display", 8, 700))
            qp.drawText(QRectF(0, h - 13, 13, 13), Qt.AlignmentFlag.AlignCenter, "T")
        else:
            paint_icon(qp, QRectF(0.5, h - 12.5, 12, 12), "smart" if layer.kind == "smart" else "rect",
                       QColor("#FFFFFF"))
    qp.setPen(QPen(QColor(0, 0, 0, 90), 1))
    qp.drawRect(QRectF(0.5, 0.5, w - 1, h - 1))
    qp.end()
    return pm


class _Eye(QPushButton):
    def __init__(self, on: bool, parent=None):
        super().__init__(parent)
        self.on_accent = False
        self.setCheckable(True)
        self.setChecked(on)
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(_t("显示 / 隐藏"))

    def paintEvent(self, e):
        qp = QPainter(self)
        p = theme.current()
        if self.isChecked():
            paint_icon(qp, QRectF(self.rect()).adjusted(3, 3, -3, -3), "eye",
                       QColor(p.accent_ink if self.on_accent else p.ink2))
        else:
            qp.setPen(QPen(QColor(p.accent_ink if self.on_accent else p.line), 1))
            qp.drawRect(QRectF(self.rect()).adjusted(7.5, 7.5, -7.5, -7.5))
        qp.end()


class _Thumb(QLabel):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.active = False

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.active:
            qp = QPainter(self)
            qp.setPen(QPen(QColor(theme.current().accent2), 2))
            qp.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))
            qp.end()


class LayerRow(QWidget):
    def __init__(self, panel: "LayersPanel", layer):
        super().__init__()
        self.panel, self.lid = panel, layer.id
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 3, 6, 3)
        lay.setSpacing(6)
        self.eye = _Eye(layer.visible)
        self.eye.toggled.connect(lambda on: panel.win.doc.set_props(
            self.lid, name=_t("显示图层") if on else _t("隐藏图层"), visible=on))
        lay.addWidget(self.eye)
        depth = len(panel.win.doc.ancestors(layer))
        self.indent = QWidget()
        self.indent.setFixedWidth(14 * depth + (12 if layer.clip else 0))
        lay.addWidget(self.indent)
        self.arrow = QToolButton()                     # 组：折叠 / 展开；剪贴图层：↳ 标记
        self.arrow.setAutoRaise(True)
        self.arrow.setFixedSize(16, 24)
        if layer.kind == "group":
            self.arrow.setToolTip(_t("折叠 / 展开组"))
            self.arrow.clicked.connect(lambda: panel.toggle_collapse(self.lid))
        else:
            self.arrow.setEnabled(False)
        self.arrow.setVisible(layer.kind == "group" or layer.clip)
        lay.addWidget(self.arrow)
        self.thumb = _Thumb()
        self.thumb.clicked.connect(lambda: panel.pick(self.lid, False))
        lay.addWidget(self.thumb)
        self.mthumb = _Thumb()
        self.mthumb.clicked.connect(lambda: panel.pick(self.lid, True))
        self.mthumb.setToolTip(_t("图层蒙版：点一下在蒙版上画"))
        lay.addWidget(self.mthumb)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.name = QLabel()
        self.name.setFont(font("cn", 9.5, 600))
        self.sub = QLabel()
        self.sub.setFont(font("cn", 8))
        self.sub.setProperty("role", "faint")
        col.addWidget(self.name)
        col.addWidget(self.sub)
        lay.addLayout(col, 1)
        self.update_from(layer)

    def update_from(self, layer, thumbs: bool = True) -> None:
        doc = self.panel.win.doc
        self.eye.blockSignals(True)
        self.eye.setChecked(layer.visible)
        self.eye.blockSignals(False)
        fm = QFontMetrics(self.name.font())
        self.name.setText(fm.elidedText(layer.name, Qt.TextElideMode.ElideRight, 150))
        self.name.setToolTip(layer.name)
        if layer.kind == "group":
            self.arrow.setText("▸" if layer.collapsed else "▾")
        elif layer.clip:
            self.arrow.setIcon(QIcon(icon_pixmap("clip", QColor(theme.current().ink2), 14)))
            self.arrow.setToolTip(_t("剪贴蒙版：只在下面那一层有像素的地方显示"))
        parts = [{"adjust": _t("调整"), "text": _t("文字"), "pixel": _t("像素"), "group": _t("组"), "shape": _t("形状"),
                  "smart": _t("智能对象")}[layer.kind]]
        if layer.pixels is not None and layer.pixels.dtype == np.uint16 and layer.kind == "pixel":
            parts.append(_t("16 位"))
        if layer.clip:
            parts.append(_t("剪贴"))
        if layer.style:
            parts.append("fx")
        if layer.lock_all or layer.lock_pixels or layer.lock_pos or layer.lock_alpha:
            parts.append(_t("已锁定"))
        if layer.blend not in ("normal", "pass_through"):
            parts.append(BLEND_NAMES.get(layer.blend, layer.blend))
        if layer.opacity < 0.999:
            parts.append(f"{layer.opacity * 100:.0f}%")
        if layer.mask is not None and not layer.mask_enabled:
            parts.append(_t("蒙版已停用"))
        self.sub.setText(" · ".join(parts))
        if thumbs:
            self.thumb.setPixmap(layer_thumb(layer))
            self.mthumb.setVisible(layer.mask is not None)
            if layer.mask is not None:
                self.mthumb.setPixmap(layer_thumb(layer, mask=True))
        active = doc.active_id == layer.id
        p = theme.current()
        self.name.setStyleSheet(f"color: {p.accent_ink};" if active else "")
        self.sub.setStyleSheet(f"color: {p.accent_ink};" if active else "")
        self.eye.on_accent = active
        self.eye.update()
        editing_mask = active and doc.mask_edit and layer.mask is not None
        self.thumb.active = active and not editing_mask
        self.mthumb.active = editing_mask
        self.thumb.update()
        self.mthumb.update()

    def mouseDoubleClickEvent(self, e):
        self.panel.rename(self.lid)


class LayersPanel(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(6)
        r = QHBoxLayout()
        self.blend = QComboBox()
        self.blend.addItem(_t("穿透"), "pass_through")          # 只给图层组用
        for k, t in BLEND_MODES:
            self.blend.addItem(t, k)
        self.blend.setToolTip(_t("混合模式"))
        self.blend.currentIndexChanged.connect(self._blend)
        r.addWidget(self.blend, 1)
        r.addWidget(QLabel(_t("不透明度")))
        self.opacity = QSpinBox()
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix("%")
        self.opacity.setFixedWidth(68)
        self.opacity.valueChanged.connect(self._opacity)
        r.addWidget(self.opacity)
        lay.addLayout(r)
        self.op_slider = QSlider(Qt.Orientation.Horizontal)
        self.op_slider.setRange(0, 100)
        self.op_slider.valueChanged.connect(self.opacity.setValue)
        lay.addWidget(self.op_slider)
        r = QHBoxLayout()                                  # 锁定 + 填充 + 图层样式
        r.setSpacing(3)
        lk = QLabel()
        lk.setPixmap(icon_pixmap("lock", QColor(theme.current().ink2), 14))
        lk.setToolTip(_t("锁定"))
        r.addWidget(lk)
        self.locks: dict[str, QToolButton] = {}
        for key, text, tip in (("lock_alpha", "▦", _t("锁定透明像素：只能改有像素的地方的颜色")),
                               ("lock_pixels", "✎", _t("锁定图像像素：不能画、不能用滤镜")),
                               ("lock_pos", "✥", _t("锁定位置：不能移动、变换")),
                               ("lock_all", _t("全"), _t("全部锁定"))):
            b = QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setFixedSize(26, 24)
            b.toggled.connect(lambda on, k=key: self._lock(k, on))
            self.locks[key] = b
            r.addWidget(b)
        r.addStretch(1)
        r.addWidget(QLabel(_t("填充")))
        self.fill = QSpinBox()
        self.fill.setRange(0, 100)
        self.fill.setSuffix("%")
        self.fill.setFixedWidth(64)
        self.fill.setToolTip(_t("填充不透明度：只影响图层自己的像素，不影响投影、描边等图层样式"))
        self.fill.valueChanged.connect(self._fill)
        r.addWidget(self.fill)
        self.fx = QToolButton()
        self.fx.setIcon(QIcon(icon_pixmap("fx", QColor(theme.current().ink2), 16)))
        self.fx.setToolTip(_t("图层样式（投影、描边、发光、叠加、斜面和浮雕）"))
        self.fx.setFixedSize(28, 24)
        self.fx.clicked.connect(win.layer_style)
        r.addWidget(self.fx)
        lay.addLayout(r)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.currentRowChanged.connect(self._row_changed)
        lay.addWidget(self.list, 1)
        g = QGridLayout()
        g.setSpacing(4)
        btns = [(_t("＋ 图层"), _t("新建空白图层（Ctrl+Shift+N）"), lambda: win.doc.new_pixel_layer()),
                (_t("◐ 调整"), _t("新建调整图层（曝光、曲线、HSL…）"), lambda: win.new_adjust()),
                (_t("▣ 蒙版"), _t("给当前图层加蒙版（有选区时按选区）"), win.add_mask),
                (_t("▤ 组"), _t("把当前图层放进新的图层组（Ctrl+G）"), lambda: win.doc.group_layers()),
                (_t("复制"), _t("复制图层（Ctrl+J）"), lambda: win.doc.duplicate_layer(win.doc.active_id)),
                ("↑", _t("上移一层（Ctrl+]）"), lambda: win.doc.move_layer(win.doc.active_id, 1)),
                ("↓", _t("下移一层（Ctrl+[）"), lambda: win.doc.move_layer(win.doc.active_id, -1)),
                (_t("剪贴"), _t("创建 / 释放剪贴蒙版（Ctrl+Alt+G）：只作用于下面那一层"),
                 lambda: win.doc.toggle_clip()),
                (_t("合并"), _t("向下合并 / 合并组（Ctrl+E）"), lambda: win.doc.merge_down(win.doc.active_id)),
                (_t("删除"), _t("删除图层（组连同里面的图层）"), lambda: win.doc.delete_layer(win.doc.active_id))]
        for i, (t, tip, fn) in enumerate(btns):
            b = QPushButton(t)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            b.setStyleSheet("padding: 4px 4px;")
            if t == _t("剪贴"):
                b.setIcon(QIcon(icon_pixmap("clip", QColor(theme.current().ink2), 14)))
            g.addWidget(b, i // 5, i % 5)
        lay.addLayout(g)
        self._ids: list[int] = []
        self._rows: dict[int, LayerRow] = {}
        self._updating = False

    def refresh(self, thumbs: bool = True) -> None:
        doc = self.win.doc
        if doc is None:
            return
        shown = [l for l in reversed(doc.layers) if not any(a.collapsed for a in doc.ancestors(l))]
        ids = [l.id for l in shown]
        sig = [(l.id, l.parent, l.clip, l.kind) for l in shown]
        self._updating = True
        if sig != getattr(self, "_sig", None):
            self.list.clear()
            self._rows.clear()
            for l in shown:
                it = QListWidgetItem()
                it.setSizeHint(QSize(10, 42))
                self.list.addItem(it)
                row = LayerRow(self, l)
                self._rows[l.id] = row
                self.list.setItemWidget(it, row)
            self._ids = ids
            self._sig = sig
        else:
            for l in shown:
                self._rows[l.id].update_from(l, thumbs)
        act = doc.active
        while act is not None and act.id not in self._ids and doc.layer(act.parent) is not None:
            act = doc.layer(act.parent)                    # 当前图层在折叠的组里：高亮那个组
        if act is not None and act.id in self._ids:
            self.list.setCurrentRow(self._ids.index(act.id))
            self.update_marks()
        l = doc.active
        if l is not None:
            self.blend.blockSignals(True)
            self.blend.setCurrentIndex(max(0, self.blend.findData(l.blend)))
            self.blend.setEnabled(l.kind != "adjust")
            item = self.blend.model().item(0)
            if item is not None:
                item.setEnabled(l.kind == "group")
            self.blend.blockSignals(False)
            for w in (self.opacity, self.op_slider):
                w.blockSignals(True)
                w.setValue(int(round(l.opacity * 100)))
                w.blockSignals(False)
            self.fill.blockSignals(True)
            self.fill.setValue(int(round(l.fill * 100)))
            self.fill.setEnabled(l.has_pixels)
            self.fill.blockSignals(False)
            for k, b in self.locks.items():
                b.blockSignals(True)
                b.setChecked(bool(getattr(l, k)))
                b.setEnabled(not l.canvas_sized)
                b.blockSignals(False)
            self.fx.setEnabled(l.has_pixels)
        self._updating = False

    def _lock(self, key: str, on: bool) -> None:
        if self._updating:
            return
        doc = self.win.doc
        if doc.active is not None:
            doc.set_props(doc.active_id, name=_t("锁定图层") if on else _t("解锁图层"), **{key: on})

    def _fill(self, v: int) -> None:
        if self._updating:
            return
        doc = self.win.doc
        if doc.active is not None:
            self.win.mark_interactive()
            doc.set_props(doc.active_id, key=f"fill{doc.active_id}", name=_t("填充不透明度"), fill=v / 100)

    def update_marks(self) -> None:
        doc = self.win.doc
        for l in doc.layers:
            if l.id in self._rows:
                self._rows[l.id].update_from(l, thumbs=False)

    def _row_changed(self, row: int) -> None:
        if self._updating or not 0 <= row < len(self._ids):
            return
        self.win.doc.set_active(self._ids[row])

    def toggle_collapse(self, lid: int) -> None:
        """折叠 / 展开组：只是面板上的显示，不进历史记录（但会随工程保存）。"""
        doc = self.win.doc
        g = doc.layer(lid)
        if g is None or g.kind != "group":
            return
        g.collapsed = not g.collapsed
        doc.modified = True
        self.refresh(thumbs=False)

    def pick(self, lid: int, mask: bool) -> None:
        doc = self.win.doc
        if doc.active_id != lid:
            doc.set_active(lid)
        self.win.set_mask_edit(mask and doc.layer(lid) is not None and doc.layer(lid).mask is not None)

    def rename(self, lid: int) -> None:
        doc = self.win.doc
        l = doc.layer(lid)
        if l is None:
            return
        name, ok = QInputDialog.getText(self, _t("重命名图层"), _t("图层名称："), text=l.name)
        if ok and name.strip():
            doc.set_props(lid, name=_t("重命名图层"), **{"name": name.strip()})

    def _blend(self, i: int) -> None:
        if self._updating:
            return
        doc = self.win.doc
        if doc.active is not None:
            doc.set_props(doc.active_id, key=f"blend{doc.active_id}", name=_t("混合模式"), blend=self.blend.itemData(i))

    def _opacity(self, v: int) -> None:
        self.op_slider.blockSignals(True)
        self.op_slider.setValue(v)
        self.op_slider.blockSignals(False)
        if self._updating:
            return
        doc = self.win.doc
        if doc.active is not None:
            self.win.mark_interactive()
            doc.set_props(doc.active_id, key=f"opacity{doc.active_id}", name=_t("不透明度"), opacity=v / 100)


# ---------------------------------------------------------------------------
# 历史记录
# ---------------------------------------------------------------------------

class HistoryPanel(QListWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self._updating = False
        self.currentRowChanged.connect(self._goto)

    def refresh(self) -> None:
        doc = self.win.doc
        if doc is None:
            return
        self._updating = True
        self.clear()
        p = theme.current()
        self.addItem(_t("打开"))
        for i, name in enumerate(doc.history.names(), 1):
            it = QListWidgetItem(name)
            if i > doc.history.index:
                it.setForeground(QColor(p.ink3))
            self.addItem(it)
        self.setCurrentRow(doc.history.index)
        self.scrollToItem(self.currentItem())
        self._updating = False

    def _goto(self, row: int) -> None:
        if self._updating or row < 0:
            return
        self.win.history_goto(row)


# ---------------------------------------------------------------------------
# 前景色 / 背景色
# ---------------------------------------------------------------------------

class Swatches(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setFixedSize(44, 48)
        self.setToolTip(_t("前景色 / 背景色：单击修改。X 交换，D 恢复黑白"))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _rects(self):
        return QRectF(4, 4, 24, 24), QRectF(16, 16, 24, 24), QRectF(31, 2, 11, 11), QRectF(2, 34, 11, 11)

    def mousePressEvent(self, e):
        fg, bg, swap, reset = self._rects()
        p = e.position()
        if swap.contains(p):
            self.win.swap_colors()
        elif reset.contains(p):
            self.win.reset_colors()
        elif fg.contains(p):
            c = QColorDialog.getColor(self.win.fg, self, _t("前景色"))
            if c.isValid():
                self.win.set_fg(c)
        elif bg.contains(p):
            c = QColorDialog.getColor(self.win.bg, self, _t("背景色"))
            if c.isValid():
                self.win.set_bg(c)

    def paintEvent(self, e):
        qp = QPainter(self)
        fg, bg, swap, reset = self._rects()
        for r, c in ((bg, self.win.bg), (fg, self.win.fg)):
            qp.fillRect(r, c)
            qp.setPen(QPen(QColor("#000000"), 1))
            qp.drawRect(r)
            qp.setPen(QPen(QColor("#FFFFFF"), 1))
            qp.drawRect(r.adjusted(1, 1, -1, -1))
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setPen(QPen(QColor(theme.current().slab_ink2), 1.3))
        qp.drawArc(swap.adjusted(1, 1, -1, -1), 0, 90 * 16)
        qp.drawLine(QPointF(swap.right() - 1, swap.center().y()), QPointF(swap.right() - 3, swap.center().y() - 2.5))
        qp.drawLine(QPointF(swap.center().x(), swap.top() + 1), QPointF(swap.center().x() + 2.5, swap.top() + 3))
        qp.fillRect(QRectF(reset.left() + 4, reset.top() + 4, 7, 7), QColor("#FFFFFF"))
        qp.fillRect(QRectF(reset.left(), reset.top(), 7, 7), QColor("#000000"))
        qp.setPen(QPen(QColor("#FFFFFF"), 1))
        qp.drawRect(QRectF(reset.left(), reset.top(), 7, 7))
        qp.end()
