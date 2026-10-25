"""修图窗口：菜单、左侧工具栏、顶部选项栏、中间画布、右侧面板（直方图 / 属性 / 图层 / 历史记录）。

快捷键尽量和 Photoshop 一致：V 移动、M 选框、L 套索、W 魔棒、C 裁剪、B 画笔、E 橡皮擦、S 仿制图章、J 修复、
T 文字、Ctrl+J 复制图层、Ctrl+T 自由变换、Ctrl+Z / Ctrl+Shift+Z 撤销重做、[ ] 调画笔大小，按住空格拖动画面，
按住 \\ 看原图。原片只读：「导出图片」写一个新文件，「保存工程」把图层存成 .pcedit 以后接着改。
"""
from __future__ import annotations

import os
import time

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QSlider, QSpinBox,
                               QSplitter, QVBoxLayout, QWidget)

from ...edit import adjust
from ...edit import io as eio
from ...edit.document import Layer, to8
from ...edit.filters import FILTERS
from ...edit.paint import BLUR as P_BLUR
from .. import theme
from ..dialogs import _Base, choose, info
from ..theme import font
from ..widgets.deco import Panel, TacButton, caption
from .canvas import CanvasView
from .icons import paint_icon
from .panels import (HistoryPanel, Histogram, LayersPanel, ParamSlider, PropertiesPanel, Swatches, TextEditor)
from .tools import TOOLS, TransformTool, _Busy
from ...i18n import t as _t

# 「图像 → 调整」里的条目：(名称, 展开哪一组, 初始参数)
ADJUSTMENTS = [
    ("亮度 / 对比度", "基本", {}), ("曝光", "基本", {}), ("色阶", "色阶", {}), ("曲线", "曲线", {}),
    ("自然饱和度", "颜色", {}), ("色相 / 饱和度（HSL）", "HSL", {}), ("色温 / 色调", "颜色", {}),
    ("色彩平衡", "色彩平衡", {"color_balance": {"on": True}}), ("黑白", "黑白", {"bw": {"on": True}}),
    ("照片滤镜", "照片滤镜", {"photo_filter": {"on": True}}), ("通道混合器", "通道混合器", {"mixer": {"on": True}}),
    ("可选颜色", "可选颜色", {"selective": {"on": True}}), ("渐变映射", "渐变映射", {"gradient_map": {"on": True}}),
    ("反相", "效果", {"invert": True}), ("分离色调", "分离色调", {}),
    ("清晰度 / 锐化 / 降噪", "细节", {}), ("暗角 / 颗粒", "效果", {}),
]


# ---------------------------------------------------------------------------
# 选项栏
# ---------------------------------------------------------------------------

class OptionsBar(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setFixedHeight(44)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(12, 5, 12, 5)
        self.lay.setSpacing(8)
        self.widgets: dict[str, QWidget] = {}

    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor(p.surface))
        qp.setPen(QPen(QColor(p.line), 1))
        qp.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        qp.end()

    def set_tool(self, tool) -> None:
        while self.lay.count():
            it = self.lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)                       # 立刻从选项栏上拿掉（deleteLater 要等回到事件循环）
                w.deleteLater()
        self.widgets.clear()
        t = QLabel(tool.name)
        t.setFont(font("cn", 10.5, 700))
        self.lay.addWidget(t)
        sep = QWidget()
        sep.setFixedSize(1, 22)
        sep.setStyleSheet(f"background: {theme.current().line};")
        self.lay.addWidget(sep)
        tool.options(self)
        self.lay.addStretch(1)

    def _opt(self, key, default):
        if key not in self.win.opts:
            self.win.opts[key] = default
        return self.win.opts[key]

    def spin(self, label, key, lo, hi, default, suffix="", decimals=0, on_change=None, slider=True):
        v = self._opt(key, default)
        if label:
            self.lay.addWidget(QLabel(label))
        sp = QDoubleSpinBox() if decimals else QSpinBox()
        if decimals:
            sp.setDecimals(decimals)
            sp.setRange(float(lo), float(hi))
        else:
            sp.setRange(int(lo), int(hi))
        sp.setSuffix(suffix)
        sp.setValue(float(v) if decimals else int(v))
        sp.setFixedWidth(84 if suffix else 64)
        sl = None
        if slider:
            sl = QSlider(Qt.Orientation.Horizontal)
            k = 10 ** decimals
            sl.setRange(int(lo * k), int(hi * k))
            sl.setValue(int(round(float(v) * k)))
            sl.setFixedWidth(110)
            sl.valueChanged.connect(lambda iv: sp.setValue(iv / k if decimals else iv))

        def changed(val):
            self.win.opts[key] = val
            if sl is not None:
                sl.blockSignals(True)
                sl.setValue(int(round(float(val) * (10 ** decimals))))
                sl.blockSignals(False)
            if on_change:
                on_change(val)
            self.win.canvas.update()
        sp.valueChanged.connect(changed)
        if sl is not None:
            self.lay.addWidget(sl)
        self.lay.addWidget(sp)
        self.widgets[key] = sp
        return sp

    def check(self, label, key, default, on_change=None):
        cb = QCheckBox(label)
        cb.setChecked(bool(self._opt(key, default)))

        def changed(on):
            self.win.opts[key] = on
            if on_change:
                on_change(on)
        cb.toggled.connect(changed)
        self.lay.addWidget(cb)
        self.widgets[key] = cb
        return cb

    def combo(self, label, key, items, default, on_change=None):
        if label:
            self.lay.addWidget(QLabel(label))
        cb = QComboBox()
        for val, text in items:
            cb.addItem(text, val)
        cur = self._opt(key, default)
        cb.setCurrentIndex(max(0, cb.findData(cur)))
        self.win.opts[key] = cb.currentData()

        def changed(i):
            self.win.opts[key] = cb.itemData(i)
            if on_change:
                on_change(cb.itemData(i))
            self.win.canvas.update()
        cb.currentIndexChanged.connect(changed)
        self.lay.addWidget(cb)
        self.widgets[key] = cb
        return cb

    def seg(self, key, items, default):
        cur = self._opt(key, default)
        grp = QButtonGroup(self)
        box = QWidget()
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        for val, text in items:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(val == cur)
            b.setStyleSheet("padding: 3px 10px;")
            b.setToolTip({"new": _t("新选区"), "add": _t("添加到选区（Shift）"), "sub": _t("从选区减去（Alt）"),
                          "and": _t("与选区交叉（Shift+Alt）")}.get(val, text))
            grp.addButton(b)
            b.toggled.connect(lambda on, v=val: on and self.win.opts.__setitem__(key, v))
            bl.addWidget(b)
        self.lay.addWidget(box)
        self.widgets[key] = box
        return box

    def button(self, text, fn, variant="secondary"):
        b = TacButton(text, variant, height=28)
        b.clicked.connect(fn)
        self.lay.addWidget(b)
        return b

    def label(self, text):
        lab = QLabel(text)
        lab.setProperty("role", "faint")
        lab.setFont(font("cn", 8.5))
        self.lay.addWidget(lab)
        return lab

    def set_value(self, key, v) -> None:
        w = self.widgets.get(key)
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
            sl = None
            i = self.lay.indexOf(w)
            if i > 0:
                sl = self.lay.itemAt(i - 1).widget()
            if isinstance(sl, QSlider):
                k = 10 ** (w.decimals() if isinstance(w, QDoubleSpinBox) else 0)
                sl.blockSignals(True)
                sl.setValue(int(round(float(v) * k)))
                sl.blockSignals(False)


# ---------------------------------------------------------------------------
# 工具栏
# ---------------------------------------------------------------------------

class ToolButton(QPushButton):
    """工具栏的一格。一格里有几个工具时右下角画个小三角：右键或按住不放弹出菜单选别的。"""

    def __init__(self, tools, win, parent=None):
        super().__init__(parent)
        self.tools = list(tools)
        self.tool = self.tools[0]
        self.win = win
        self.setCheckable(True)
        self.setFixedSize(40, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self.tool.tip)
        self._hover = False
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(380)
        self._hold.timeout.connect(self.show_menu)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda _: self.show_menu())

    def set_tool(self, tool) -> None:
        self.tool = tool
        self.setToolTip(tool.tip)
        self.update()

    def show_menu(self) -> None:
        if len(self.tools) < 2:
            return
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        for t in self.tools:
            a = m.addAction(f"{t.name}\t{t.shortcut}" if t.shortcut else t.name)
            a.setCheckable(True)
            a.setChecked(t is self.tool)
            a.triggered.connect(lambda _=False, tid=t.id: self.win.set_tool(tid))
        self.setDown(False)
        m.exec(self.mapToGlobal(self.rect().topRight()))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and len(self.tools) > 1:
            self._hold.start()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._hold.stop()
        super().mouseReleaseEvent(e)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self)
        r = QRectF(self.rect())
        if self.isChecked():
            qp.fillRect(r, QColor(p.accent))
            col = QColor(p.accent_ink)
        else:
            if self._hover:
                qp.fillRect(r, QColor(p.slab2))
            col = QColor(p.slab_ink)
        paint_icon(qp, r.adjusted(9, 6, -9, -6), self.tool.icon, col)
        if len(self.tools) > 1:
            from PySide6.QtGui import QPolygonF
            from PySide6.QtCore import QPointF
            qp.setRenderHint(QPainter.RenderHint.Antialiasing)
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(col)
            qp.drawPolygon(QPolygonF([QPointF(r.right() - 3, r.bottom() - 8), QPointF(r.right() - 3, r.bottom() - 3),
                                      QPointF(r.right() - 8, r.bottom() - 3)]))
        qp.end()


class ToolBar(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        from .tools import TOOL_SLOTS
        self.win = win
        self.setFixedWidth(52)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 8)
        lay.setSpacing(2)
        self.buttons: dict[str, ToolButton] = {}         # 工具 id → 它所在的那一格
        self.slots: list[ToolButton] = []
        breaks = {"marquee", "crop", "heal", "text", "hand"}
        for slot in TOOL_SLOTS:
            if slot[0] in breaks:
                sep = QWidget()
                sep.setFixedHeight(7)
                lay.addWidget(sep)
            b = ToolButton([win.tools[t] for t in slot], win)
            b.clicked.connect(lambda _=False, btn=b: win.set_tool(btn.tool.id))
            for tid in slot:
                self.buttons[tid] = b
            self.slots.append(b)
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)
        self.swatches = Swatches(win)
        lay.addWidget(self.swatches, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_current(self, tid: str) -> None:
        cur = self.buttons.get(tid)
        if cur is not None and cur.tool.id != tid:
            cur.set_tool(self.win.tools[tid])
        for b in self.slots:
            b.setChecked(b is cur)

    def slot_of(self, tid: str) -> ToolButton | None:
        return self.buttons.get(tid)

    def paintEvent(self, e):
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor(theme.current().slab))
        qp.end()


# ---------------------------------------------------------------------------
# 对话框
# ---------------------------------------------------------------------------

class FilterDialog(_Base):
    """滤镜参数 + 实时预览（在画布上的代理图上算，应用时按原尺寸重算）。"""

    def __init__(self, win, fid: str, params: dict | None = None):
        spec = FILTERS[fid]
        super().__init__(win, spec.name, "FILTER")
        self.win, self.fid = win, fid
        self.params = dict(params or {})
        self.setModal(True)
        self.setMinimumWidth(460)
        from ...edit.filters import defaults
        d = defaults(fid)
        d.update(self.params)
        self.params = d
        for key, label, lo, hi, dv, dec, unit in spec.params:
            s = ParamSlider(label, lo, hi, dv, dec, unit)
            s.set_value(self.params[key])
            s.changed.connect(lambda v, k=key: self._set(k, v))
            self.body.addWidget(s)
        for key, label, items, dv in spec.choices:
            r = QHBoxLayout()
            r.addWidget(QLabel(label))
            cb = QComboBox()
            for v, t in items:
                cb.addItem(t, v)
            cb.setCurrentIndex(max(0, cb.findData(self.params[key])))
            cb.currentIndexChanged.connect(lambda _i, k=key, c=cb: self._set(k, c.currentData()))
            r.addWidget(cb, 1)
            self.body.addLayout(r)
        for key, label, dv in spec.checks:
            cb = QCheckBox(label)
            cb.setChecked(bool(self.params[key]))
            cb.toggled.connect(lambda on, k=key: self._set(k, on))
            self.body.addWidget(cb)
        self.preview = QCheckBox(_t("预览"))
        self.preview.setChecked(True)
        self.preview.toggled.connect(lambda _: self._update())
        self.body.addWidget(self.preview)
        doc = win.doc
        note = _t("作用于当前图层") + (_t("的选区内") if doc.selection is not None else "")
        if doc.active is not None and doc.active.kind == "text":
            note += _t("；文字图层会被栅格化")
        self.body.addWidget(caption(note + _t("。"), "faint", 8.5))
        bc = TacButton(_t("取消"), "secondary")
        bo = TacButton(_t("确定"), "primary")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self.accept)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(90)
        self._t.timeout.connect(self._update)
        self._update()

    def _set(self, k, v):
        self.params[k] = v
        self._t.start()

    def _update(self):
        doc = self.win.doc
        self.win.mark_interactive()
        doc.set_filter_preview(doc.active_id if self.preview.isChecked() else None, self.fid, self.params)

    def done(self, r):
        self.win.doc.set_filter_preview(None)
        super().done(r)


class NumberDialog(_Base):
    def __init__(self, parent, title: str, label: str, value: float, lo: float, hi: float, suffix: str = " px",
                 decimals: int = 0, note: str = ""):
        super().__init__(parent, title, "INPUT")
        r = QHBoxLayout()
        r.addWidget(QLabel(label))
        self.spin = QDoubleSpinBox() if decimals else QSpinBox()
        if decimals:
            self.spin.setDecimals(decimals)
        self.spin.setRange(lo, hi)
        self.spin.setValue(value)
        self.spin.setSuffix(suffix)
        r.addWidget(self.spin)
        r.addStretch(1)
        self.body.addLayout(r)
        if note:
            self.body.addWidget(caption(note, "faint", 8.5))
        bc = TacButton(_t("取消"), "secondary")
        bo = TacButton(_t("确定"), "primary")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self.accept)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)

    @classmethod
    def ask(cls, *a, **kw):
        d = cls(*a, **kw)
        return d.spin.value() if d.exec() == _Base.DialogCode.Accepted else None


class ImageSizeDialog(_Base):
    def __init__(self, parent, w: int, h: int):
        super().__init__(parent, _t("图像大小"), "IMAGE SIZE")
        self.ratio = w / h
        g = QHBoxLayout()
        self.w = QSpinBox()
        self.h = QSpinBox()
        for s, v in ((self.w, w), (self.h, h)):
            s.setRange(1, 30000)
            s.setValue(v)
            s.setSuffix(" px")
        g.addWidget(QLabel(_t("宽")))
        g.addWidget(self.w)
        g.addWidget(QLabel(_t("高")))
        g.addWidget(self.h)
        self.keep = QCheckBox(_t("保持比例"))
        self.keep.setChecked(True)
        g.addWidget(self.keep)
        self.body.addLayout(g)
        r = QHBoxLayout()
        for pct in (25, 50, 75, 150, 200):
            b = QPushButton(f"{pct}%")
            b.clicked.connect(lambda _=False, p=pct: (self.w.setValue(round(w * p / 100)),
                                                       self.h.setValue(round(h * p / 100))))
            r.addWidget(b)
        r.addStretch(1)
        self.body.addLayout(r)
        self.body.addWidget(caption(_t('原尺寸 {0} × {1}。缩小用区域平均，放大用双三次插值；文字图层按比例改字号，仍可编辑。', w, h),
                                    "faint", 8.5))
        self.w.valueChanged.connect(lambda v: self.keep.isChecked() and self._sync(self.h, round(v / self.ratio)))
        self.h.valueChanged.connect(lambda v: self.keep.isChecked() and self._sync(self.w, round(v * self.ratio)))
        bc = TacButton(_t("取消"), "secondary")
        bo = TacButton(_t("确定"), "primary")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self.accept)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)

    @staticmethod
    def _sync(spin, v):
        spin.blockSignals(True)
        spin.setValue(max(1, int(v)))
        spin.blockSignals(False)


class FillDialog(_Base):
    def __init__(self, parent, has_sel: bool):
        super().__init__(parent, _t("填充"), "FILL")
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("内容：")))
        self.kind = QComboBox()
        for k, t in (("fg", _t("前景色")), ("bg", _t("背景色")), ("black", _t("黑色")), ("white", _t("白色")), ("gray", _t("50% 灰色")),
                     ("content", _t("内容识别（用周围画面补上）"))):
            self.kind.addItem(t, k)
        r.addWidget(self.kind, 1)
        self.body.addLayout(r)
        self.opacity = ParamSlider(_t("不透明度"), 1, 100, 100, 0, "%")
        self.body.addWidget(self.opacity)
        self.body.addWidget(caption(_t("填充当前图层") + (_t("的选区。") if has_sel else _t("（没有选区 = 整个图层）。内容识别需要先做选区。")),
                                    "faint", 8.5))
        bc = TacButton(_t("取消"), "secondary")
        bo = TacButton(_t("确定"), "primary")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self.accept)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)


class TextDialog(_Base):
    def __init__(self, win, layer, new: bool):
        super().__init__(win, _t("新建文字") if new else _t("编辑文字"), "TEXT")
        self.win, self.layer = win, layer
        self.setModal(True)
        self.setMinimumWidth(460)
        self.ed = TextEditor(layer.params)
        self.ed.edited.connect(self._edited)
        self.body.addWidget(self.ed)
        self.body.addWidget(caption(_t("文字图层以后随时可以改：选中它在右侧「属性」里改，或用文字工具点一下。"), "faint", 8.5))
        bc = TacButton(_t("取消"), "secondary")
        bo = TacButton(_t("确定"), "primary")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self.accept)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)
        self.ed.text.setFocus()
        self.ed.text.selectAll()

    def _edited(self, key):
        l = self.layer
        l.params = dict(self.ed.params)
        l.render_text()
        first = (l.params.get("text") or _t("文字")).strip().splitlines()
        l.name = (first[0] if first else _t("文字"))[:16]
        self.win.doc.version += 1
        self.win.mark_interactive()
        self.win.canvas.request_render(True)


class ExportDialog(_Base):
    FORMATS = [(".jpg", _t("JPEG（.jpg，体积小，发朋友圈 / 打印）")), (".png", _t("PNG（无损，保留透明）")),
               (".tif", _t("TIFF（无损，给其他修图软件）")), (".webp", "WebP")]

    def __init__(self, win):
        super().__init__(win, _t("导出图片"), "EXPORT // NEW FILE")
        self.win = win
        doc = win.doc
        s = win.ctx.settings if win.ctx is not None else None
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("格式：")))
        self.fmt = QComboBox()
        for ext, t in self.FORMATS:
            self.fmt.addItem(t, ext)
        last = (s.get("editor_export_fmt") if s else None) or ".jpg"
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(last)))
        r.addWidget(self.fmt, 1)
        self.body.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("保存到：")))
        self.path = QLineEdit(eio.suggest_export_path(doc, self.fmt.currentData()))
        r.addWidget(self.path, 1)
        b = TacButton(_t("浏览…"), "secondary", height=30)
        b.clicked.connect(self._browse)
        r.addWidget(b)
        self.body.addLayout(r)
        self.quality = ParamSlider(_t("质量"), 50, 100, int((s.get("editor_export_q") if s else 0) or 92))
        self.body.addWidget(self.quality)
        self.keep_exif = QCheckBox(_t("保留原片的拍摄信息（EXIF：时间、相机、镜头…）"))
        self.keep_exif.setChecked(True)
        self.strip_gps = QCheckBox(_t("去掉 GPS 位置（发到网上时建议勾选）"))
        self.strip_gps.setChecked(bool(s.get("editor_strip_gps")) if s else False)
        self.rescan = QCheckBox(_t("保存后重新扫描，让新文件出现在照片库里"))
        self.rescan.setChecked(True)
        self.deep = QCheckBox(_t("保存为 16 位（PNG / TIFF，给其他修图软件接着调；文件大一倍）"))
        self.deep.setChecked(doc.depth == 16)
        for w in (self.deep, self.keep_exif, self.strip_gps, self.rescan):
            self.body.addWidget(w)
        self.body.addWidget(caption(_t('画布 {0} × {1}，{2} 个图层合并成一张。原片不会被修改；想保留图层以后再改，用「文件 → 保存工程」。', doc.width, doc.height, len(doc.layers)), "faint", 8.5))
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        self.path.textChanged.connect(lambda _: self._update_rescan())
        self._fmt_changed()
        bc = TacButton(_t("取消"), "secondary")
        bo = TacButton(_t("导出"), "primary", en="EXPORT")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self._ok)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)
        self.resize(640, 10)

    def _fmt_changed(self):
        ext = self.fmt.currentData()
        p = self.path.text().strip()
        if p:
            self.path.setText(os.path.splitext(p)[0] + ext)
        self.quality.setEnabled(ext in (".jpg", ".webp"))
        self.deep.setEnabled(ext in (".png", ".tif"))
        self._update_rescan()

    def deep_on(self) -> bool:
        return self.deep.isChecked() and self.fmt.currentData() in (".png", ".tif")

    def in_library(self) -> bool:
        """导出位置在「资源接入」的某个文件夹里时，重新扫描才能把新文件加进照片库。"""
        ctx = self.win.ctx
        p = os.path.normcase(os.path.abspath(self.path.text().strip() or "."))
        dirs = (ctx.settings.get("source_dirs") or []) if ctx is not None else []
        return any(p.startswith(os.path.normcase(os.path.abspath(d)) + os.sep) for d in dirs if d)

    def _update_rescan(self):
        self.rescan.setVisible(self.in_library())

    def _browse(self):
        ext = self.fmt.currentData()
        p, _ = QFileDialog.getSaveFileName(self, _t("导出到"), self.path.text(), _t('图片 (*{0})', ext))
        if p:
            if not os.path.splitext(p)[1]:
                p += ext
            self.path.setText(os.path.normpath(p))

    def _ok(self):
        p = self.path.text().strip()
        if not p:
            return
        if os.path.exists(p) and not choose(self, _t("文件已存在"), _t('{0}<br>已经存在，要覆盖它吗？', p),
                                            [("yes", _t("覆盖"), "danger"), (None, _t("取消"), "secondary")]):
            return
        self.accept()


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class EditorWindow(QMainWindow):
    def __init__(self, ctx, doc, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.doc = doc
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.opts: dict = dict((ctx.settings.get("editor_opts") or {}) if ctx is not None else {})
        self.fg = QColor(self.opts.pop("_fg", "#000000"))
        self.bg = QColor(self.opts.pop("_bg", "#FFFFFF"))
        self.tools = {T.id: T(self) for T in TOOLS}
        self.tools["transform"] = TransformTool(self)
        self.tool = None
        self._prev_tool = "move"
        self._interactive_until = 0.0
        self._last_filter: tuple | None = None
        self.canvas = CanvasView(self)
        self._build()
        self._menus()
        self._style()
        doc.listeners.append(self._doc_event)
        self.canvas.set_doc(doc)
        self.canvas.view_changed.connect(self._update_zoom)
        self.canvas.cursor_info.connect(self._cursor_info)
        self.canvas.rendered.connect(lambda: self._hist_timer.start())
        self.set_tool("move")
        self.layers.refresh()
        self.props.rebuild()
        self.history.refresh()
        self._update_title()
        self.resize(1500, 940)
        self._tour = None
        if ctx is not None and not ctx.settings.get("editor_tour_done"):
            QTimer.singleShot(500, self.start_tour)        # 第一次打开修图：自动带着走一遍

    # ---- 界面 ----
    def _build(self) -> None:
        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.toolbar = ToolBar(self)
        h.addWidget(self.toolbar)
        mid = QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        self.options = OptionsBar(self)
        mid.addWidget(self.options)
        mid.addWidget(self.canvas, 1)
        h.addLayout(mid, 1)
        right = QWidget()
        right.setFixedWidth(356)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 6, 6, 6)
        rl.setSpacing(6)
        hp = Panel(_t("直方图"), "HISTOGRAM", pad=6)
        self.histogram = Histogram()
        hp.body.addWidget(self.histogram)
        rl.addWidget(hp)
        split = QSplitter(Qt.Orientation.Vertical)
        pp = self.panel_props = Panel(_t("属性"), "PROPERTIES", pad=0)
        self.props = PropertiesPanel(self)
        pp.body.addWidget(self.props)
        lp = self.panel_layers = Panel(_t("图层"), "LAYERS", pad=0)
        self.layers = LayersPanel(self)
        lp.body.addWidget(self.layers)
        hsp = self.panel_history = Panel(_t("历史记录"), "HISTORY", pad=6)
        self.history = HistoryPanel(self)
        hsp.body.addWidget(self.history)
        for w in (pp, lp, hsp):
            split.addWidget(w)
        split.setSizes([360, 380, 110])
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 5)
        split.setStretchFactor(2, 1)
        split.setChildrenCollapsible(False)
        rl.addWidget(split, 1)
        h.addWidget(right)
        self.setCentralWidget(central)
        sb = self.statusBar()
        self.msg = QLabel("")
        sb.addWidget(self.msg, 1)
        self.orig_btn = QPushButton(_t("按住看原图（\\）"))
        self.orig_btn.setToolTip(_t("按住时只显示最底下的背景层，松开恢复"))
        self.orig_btn.pressed.connect(lambda: self.show_original(True))
        self.orig_btn.released.connect(lambda: self.show_original(False))
        sb.addPermanentWidget(self.orig_btn)
        self.pos_lab = QLabel("")
        self.pos_lab.setFont(font("mono", 8.5))
        self.pos_lab.setMinimumWidth(260)
        sb.addPermanentWidget(self.pos_lab)
        self.zoom_lab = QLabel("")
        self.zoom_lab.setFont(font("mono", 8.5))
        sb.addPermanentWidget(self.zoom_lab)
        self._panel_timer = QTimer(self)
        self._panel_timer.setSingleShot(True)
        self._panel_timer.setInterval(150)
        self._panel_timer.timeout.connect(self._refresh_panels)
        self._hist_timer = QTimer(self)
        self._hist_timer.setSingleShot(True)
        self._hist_timer.setInterval(180)
        self._hist_timer.timeout.connect(self._update_hist)

    def _style(self) -> None:
        p = theme.current()
        self.setStyleSheet(f"""
QMenuBar {{ background: {p.slab}; color: {p.slab_ink}; padding: 2px 6px; }}
QMenuBar::item {{ background: transparent; padding: 6px 12px; }}
QMenuBar::item:selected {{ background: {p.accent}; color: {p.accent_ink}; }}
QStatusBar {{ background: {p.slab}; color: {p.slab_ink2}; }}
QStatusBar QLabel {{ color: {p.slab_ink2}; padding: 0 8px; }}
QStatusBar QPushButton {{ background: {p.slab2}; color: {p.slab_ink}; border: 1px solid {p.slab2}; padding: 2px 10px; }}
QStatusBar QPushButton:pressed {{ background: {p.accent}; color: {p.accent_ink}; }}
""")

    def _act(self, menu, text, fn, shortcut=None, tip=None) -> QAction:
        a = QAction(text, self)
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
        a.triggered.connect(lambda _=False: fn())
        if menu is not None:
            menu.addAction(a)
        else:
            self.addAction(a)
        return a

    def _menus(self) -> None:
        mb = self.menuBar()
        d = lambda: self.doc                                    # noqa: E731
        m = self.menu_file = mb.addMenu(_t("文件"))
        self._act(m, _t("打开图片…"), self.open_image, "Ctrl+O")
        self._act(m, _t("打开工程…"), lambda: self.open_image(project=True))
        self._act(m, _t("置入图片（作为新图层）…"), self.place_image, "Ctrl+Shift+P")
        self._act(m, _t("合并曝光（HDR）…"), self.merge_exposures)
        self.a_redevelop = self._act(m, _t("重新显影 RAW…"), self.redevelop_raw)
        m.addSeparator()
        self._act(m, _t("保存工程"), self.save_project, "Ctrl+S")
        self._act(m, _t("工程另存为…"), lambda: self.save_project(ask=True))
        self._act(m, _t("导出图片…"), self.export, ["Ctrl+Shift+S", "Ctrl+Alt+Shift+W"])
        m.addSeparator()
        self._act(m, _t("关闭"), self.close, "Ctrl+W")

        m = mb.addMenu(_t("编辑"))
        self.a_undo = self._act(m, _t("撤销"), self.undo, "Ctrl+Z")
        self.a_redo = self._act(m, _t("重做"), self.redo, ["Ctrl+Shift+Z", "Ctrl+Y"])
        m.addSeparator()
        self._act(m, _t("剪切"), lambda: self.copy(cut=True), "Ctrl+X")
        self._act(m, _t("拷贝"), self.copy, "Ctrl+C")
        self._act(m, _t("合并拷贝"), lambda: self.copy(merged=True), "Ctrl+Shift+C")
        self._act(m, _t("粘贴（作为新图层）"), self.paste, "Ctrl+V")
        m.addSeparator()
        self._act(m, _t("填充…"), self.fill, "Shift+F5")
        self._act(m, _t("内容识别填充（去掉选区里的东西）"), self.content_fill, "Shift+Backspace")
        self._act(m, _t("清除选区内容"), self.clear, "Delete")
        m.addSeparator()
        self._act(m, _t("自由变换"), lambda: self.transform_mode("free"), "Ctrl+T")
        sub = m.addMenu(_t("变换"))
        for mode, label in (("skew", _t("斜切")), ("distort", _t("扭曲")), ("perspective", _t("透视")),
                            ("warp", _t("变形"))):
            self._act(sub, label, lambda md=mode: self.transform_mode(md))

        m = self.menu_image = mb.addMenu(_t("图像"))
        sub = m.addMenu(_t("模式"))
        self.a_depth = {}
        for dep, label in ((8, _t("8 位/通道")), (16, _t("16 位/通道"))):
            a = self._act(sub, label, lambda v=dep: self.set_depth(v))
            a.setCheckable(True)
            self.a_depth[dep] = a
        sub.aboutToShow.connect(self._sync_depth_menu)
        sub = m.addMenu(_t("调整（新建调整图层）"))
        for name, focus, params in ADJUSTMENTS:
            self._act(sub, _t(name), lambda n=name, f=focus, p=params: self.new_adjust(_t(n), p, f))
        sub = m.addMenu(_t("滤镜预设（新建调整图层）"))
        for name in adjust.PRESETS:
            self._act(sub, _t(name), lambda n=name: self.new_adjust(_t(n), adjust.preset(n)))
        m.addSeparator()
        self._act(m, _t("自动色调（当前图层）"), lambda: self.run_filter("auto_tone"), "Ctrl+Shift+L")
        self._act(m, _t("去色（当前图层）"), lambda: self.run_filter("desaturate"), "Ctrl+Shift+U")
        self._act(m, _t("反相（当前图层）"), lambda: self.run_filter("invert"), "Ctrl+I")
        m.addSeparator()
        self._act(m, _t("图像大小…"), self.image_size, "Ctrl+Alt+I")
        self._act(m, _t("裁剪到选区"), self.crop_to_selection)
        m.addSeparator()
        self._act(m, _t("修补重影（从某一张源图取）…"), self.fix_ghost)
        sub = m.addMenu(_t("图像旋转"))
        self._act(sub, _t("顺时针 90°"), lambda: d().rotate(1))
        self._act(sub, _t("逆时针 90°"), lambda: d().rotate(3))
        self._act(sub, "180°", lambda: d().rotate(2))
        sub.addSeparator()
        self._act(sub, _t("水平翻转"), lambda: d().flip(True))
        self._act(sub, _t("垂直翻转"), lambda: d().flip(False))

        m = mb.addMenu(_t("图层"))
        self._act(m, _t("新建图层"), lambda: d().new_pixel_layer(), "Ctrl+Shift+N")
        self._act(m, _t("新建调整图层"), lambda: self.new_adjust())
        self._act(m, _t("复制图层 / 通过拷贝的图层"), lambda: d().layer_via_copy(), "Ctrl+J")
        self._act(m, _t("通过剪切的图层"), lambda: d().layer_via_copy(cut=True), "Ctrl+Shift+J")
        self._act(m, _t("删除图层"), lambda: d().delete_layer(d().active_id))
        m.addSeparator()
        self._act(m, _t("新建图层组"), lambda: d().new_group())
        self._act(m, _t("图层编组"), lambda: d().group_layers(), "Ctrl+G")
        self._act(m, _t("取消图层编组"), lambda: d().ungroup(), "Ctrl+Shift+G")
        self._act(m, _t("创建 / 释放剪贴蒙版"), lambda: d().toggle_clip(), "Ctrl+Alt+G")
        m.addSeparator()
        self._act(m, _t("图层样式…"), self.layer_style)
        self._act(m, _t("清除图层样式"), self.clear_style)
        m.addSeparator()
        self._act(m, _t("添加图层蒙版"), self.add_mask)
        self._act(m, _t("删除图层蒙版"), lambda: d().delete_mask(d().active_id))
        self._act(m, _t("应用图层蒙版"), lambda: d().apply_mask(d().active_id))
        self._act(m, _t("反相蒙版"), lambda: d().invert_mask(d().active_id))
        m.addSeparator()
        self._act(m, _t("上移一层"), lambda: d().move_layer(d().active_id, 1), "Ctrl+]")
        self._act(m, _t("下移一层"), lambda: d().move_layer(d().active_id, -1), "Ctrl+[")
        m.addSeparator()
        self._act(m, _t("向下合并"), lambda: self._busy_call(_t("正在合并…"), lambda: d().merge_down(d().active_id)), "Ctrl+E")
        self._act(m, _t("盖印可见图层"), lambda: self._busy_call(_t("正在合成…"), d().stamp_visible), "Ctrl+Alt+Shift+E")
        self._act(m, _t("拼合图像"), lambda: self._busy_call(_t("正在拼合…"), d().flatten))
        m.addSeparator()
        self._act(m, _t("栅格化文字"), lambda: d().rasterize(d().active_id))

        m = mb.addMenu(_t("选择"))
        self._act(m, _t("全选"), lambda: d().select_all(), "Ctrl+A")
        self._act(m, _t("取消选择"), lambda: d().deselect(), "Ctrl+D")
        self._act(m, _t("反选"), lambda: d().invert_selection(), "Ctrl+Shift+I")
        m.addSeparator()
        self._act(m, _t("选择主体（自动）"), self.select_subject)
        self._act(m, _t("色彩范围…"), self.color_range)
        self._act(m, _t("选择并遮住…"), self.select_and_mask, "Ctrl+Alt+R")
        m.addSeparator()
        self.a_qmask = self._act(m, _t("以快速蒙版模式编辑"), self.toggle_quick_mask, "Q")
        self.a_qmask.setCheckable(True)
        self._act(m, _t("存储选区…"), self.save_selection)
        self._act(m, _t("载入选区…"), self.load_selection)
        m.addSeparator()
        self._act(m, _t("羽化…"), lambda: self._sel_number(_t("羽化选区"), _t("羽化半径"), 8, d().feather_selection), "Shift+F6")
        self._act(m, _t("扩展…"), lambda: self._sel_number(_t("扩展选区"), _t("扩展量"), 4, d().grow_selection))
        self._act(m, _t("收缩…"), lambda: self._sel_number(_t("收缩选区"), _t("收缩量"), 4, lambda v: d().grow_selection(-v)))

        m = mb.addMenu(_t("滤镜"))
        self.a_last_filter = self._act(m, _t("上次滤镜"), self.repeat_filter, "Ctrl+F")
        self.a_last_filter.setEnabled(False)
        m.addSeparator()
        self._act(m, _t("镜头校正…"), lambda: self.run_filter("lens"), "Ctrl+Shift+R")
        self._act(m, _t("液化…"), self.liquify, "Ctrl+Shift+X")
        m.addSeparator()
        for title, fids in ((P_BLUR, ("gaussian", "motion", "surface", "lensblur")),
                            (_t("扭曲"), ("twirl", "wave", "ripple", "spherize", "pinch", "polar")),
                            (_t("锐化"), ("unsharp",)), (_t("杂色"), ("denoise", "noise")),
                            (_t("其他"), ("highpass", "mosaic", "posterize", "threshold"))):
            sub = m.addMenu(title)
            for fid in fids:
                self._act(sub, FILTERS[fid].name + "…", lambda f=fid: self.run_filter(f))

        m = mb.addMenu(_t("视图"))
        self._act(m, _t("放大"), lambda: self.canvas.zoom_step(True), ["Ctrl+=", "Ctrl++"])
        self._act(m, _t("缩小"), lambda: self.canvas.zoom_step(False), "Ctrl+-")
        self._act(m, _t("适合窗口"), self.canvas.fit, "Ctrl+0")
        self._act(m, _t("实际像素（100%）"), lambda: self.canvas.set_zoom(1.0), "Ctrl+1")

        m = mb.addMenu(_t("帮助"))
        self._act(m, _t("新手教程"), self.start_tour)
        self._act(m, _t("快捷键…"), self.show_help, "F1")

        # 工具与颜色的单键快捷键（不放进菜单）。几个工具共用一个键（O：减淡 / 加深 / 海绵）时，
        # 按键选这一格当前的工具，Shift + 键在它们之间轮换（和 Photoshop 一样）
        by_key: dict[str, list[str]] = {}
        for t in self.tools.values():
            if t.shortcut:
                by_key.setdefault(t.shortcut, []).append(t.id)
        for key, ids in by_key.items():
            if len(ids) == 1:
                self._act(None, self.tools[ids[0]].name, lambda tid=ids[0]: self.set_tool(tid), key)
                continue
            self._act(None, key, lambda ids=ids: self._key_tool(ids, False), key)
            self._act(None, "Shift+" + key, lambda ids=ids: self._key_tool(ids, True), "Shift+" + key)
        self._act(None, _t("交换前景 / 背景色"), self.swap_colors, "X")
        self._act(None, _t("默认颜色"), self.reset_colors, "D")
        self._act(None, _t("笔刷变小"), lambda: self._brush_size(1 / 1.25), "[")
        self._act(None, _t("笔刷变大"), lambda: self._brush_size(1.25), "]")

    # ---- 状态 ----
    def status_msg(self, text: str) -> None:
        self.msg.setText(text)

    def mark_interactive(self) -> None:
        self._interactive_until = time.monotonic() + 0.35

    def _interactive(self) -> bool:
        return time.monotonic() < self._interactive_until

    def _update_title(self) -> None:
        d = self.doc
        name = d.source.name or os.path.basename(d.source.project or "") or _t("未命名")
        self.setWindowTitle(_t('影匣 · 修图 — {0}{1}', name, '  *' if d.modified else '') + (
            _t("（16 位）") if d.depth == 16 else ""))

    def _update_zoom(self) -> None:
        self.zoom_lab.setText(f"{self.canvas.zoom * 100:.0f}%   {self.doc.width} × {self.doc.height}   "
                              + _t('{0} 位', self.doc.depth))

    def _cursor_info(self, info_) -> None:
        if info_ is None:
            self.pos_lab.setText("")
            return
        x, y, c = info_
        if c is None or not (0 <= x < self.doc.width and 0 <= y < self.doc.height):
            self.pos_lab.setText(f"X {int(x)}  Y {int(y)}")
            return
        self.pos_lab.setText(f"X {int(x)}  Y {int(y)}   R {c[0]}  G {c[1]}  B {c[2]}" + (f"  A {c[3]}" if c[3] < 255 else ""))

    def _update_hist(self) -> None:
        self.histogram.set_rgba(self.canvas.display_rgba())
        ed = self.props.editor
        if ed is not None and hasattr(ed, "set_hist"):
            ed.set_hist(self.hist_lum())

    def hist_lum(self):
        h = self.histogram.h
        return None if h is None else h[3]

    def _refresh_panels(self) -> None:
        self.layers.refresh()
        self._update_title()
        l = self.doc.active
        if l is not None and l.kind == "pixel":         # 像素图层的属性只是尺寸 / 位置说明，随时重建不影响操作
            self.props.rebuild()
            self._msig = self._mask_sig()

    def _doc_event(self, kind: str, data) -> None:
        if kind == "pixels":
            if data is None:
                self.canvas.request_render(self._interactive())
            else:
                self.canvas.update_canvas_rect(data)
            self._hist_timer.start()
        elif kind == "layers":
            self.canvas.hot = self.doc.active_id
            self.canvas.request_render(self._interactive())
            self._panel_timer.start()
            l = self.doc.active
            if l is None or l.id != self.props.lid or l.kind != self.props.kind or self._mask_sig() != self._msig:
                self.props.rebuild()
                self._msig = self._mask_sig()
        elif kind == "active":
            self.canvas.hot = self.doc.active_id
            self.layers.refresh(thumbs=False)
            self.props.rebuild()
            self._msig = self._mask_sig()
            self.canvas.update()
        elif kind == "selection":
            self.canvas.selection_changed()
        elif kind == "canvas":
            self.canvas.fit()
            self._panel_timer.start()
            if self.tool is not None and self.tool.id == "crop":
                self.tool.reset()
        elif kind == "depth":
            self._update_title()
            self._update_zoom()
            self._panel_timer.start()
        elif kind == "history":
            self.history.refresh()
            self._update_title()
            self._panel_timer.start()

    _msig = None

    def _mask_sig(self):
        l = self.doc.active
        return None if l is None else (l.id, l.mask is None, l.mask_enabled, self.doc.mask_edit)

    # ---- 工具 / 颜色 ----
    def set_tool(self, tid: str) -> None:
        if self.tool is not None and self.tool.busy():
            return
        if self.tool is not None and self.tool.id == tid and tid != "transform":
            return
        if self.tool is not None:
            if self.tool.id != "transform":
                self._prev_tool = self.tool.id
            self.tool.deactivate()
        self.tool = self.tools[tid]
        self.tool.activate()
        if tid == "transform" and self.tool.layer is None:
            self.tool = None
            self.set_tool(self._prev_tool)
            return
        self.options.set_tool(self.tool)
        self.toolbar.set_current(tid)
        self.canvas._update_cursor()
        self.status_msg(self.tool.tip)
        self.canvas.update()

    def transform_mode(self, mode: str) -> None:
        """编辑 → 自由变换 / 变换 → 斜切、扭曲、透视、变形。已经在变换中就只换方式。"""
        self.opts["xf_mode"] = mode
        if self.tool is not None and self.tool.id == "transform" and self.tool.layer is not None:
            w = self.options.widgets.get("xf_mode")
            if isinstance(w, QComboBox):
                w.setCurrentIndex(max(0, w.findData(mode)))
            else:
                self.tool._mode_changed(mode)
            return
        self.set_tool("transform")

    def set_depth(self, depth: int) -> None:
        if depth == self.doc.depth:
            return
        if depth == 8 and not choose(self, _t("转换为 8 位/通道"), _t(
                "转成 8 位后，再大幅拉曲线 / 色阶时渐变（天空、皮肤）更容易出现断层。<br>可以撤销。要转换吗？"),
                [("yes", _t("转换"), "primary"), (None, _t("取消"), "secondary")]):
            return
        self._leave_transform()
        with _Busy(self, _t("正在转换位深…")):
            self.doc.convert_depth(depth)
        self.status_msg(_t("已转换为 16 位/通道：调色不容易出断层，导出 PNG / TIFF 时可以保存 16 位")
                        if depth == 16 else _t("已转换为 8 位/通道"))

    def _sync_depth_menu(self) -> None:
        for dep, a in self.a_depth.items():
            a.setChecked(dep == self.doc.depth)

    def raw_source(self) -> str | None:
        """这张图对应的 RAW 文件：打开的就是 RAW，或照片库里同一组有 RAW。"""
        from ...edit.raw import is_raw
        s = self.doc.source
        if s.path and is_raw(s.path) and os.path.exists(s.path):
            return s.path
        if s.gid is not None and self.ctx is not None:
            g = self.ctx.library.by_gid(s.gid)
            for f in getattr(g, "files", []) or []:
                if f.role == "raw" and os.path.exists(f.path):
                    return f.path
        return None

    def redevelop_raw(self) -> None:
        """文件 → 重新显影 RAW：按新的白平衡 / 曝光重新显影，作为一个新图层放进来（原有图层不动）。"""
        from .rawdlg import RawDialog
        p = self.raw_source()
        if p is None:
            info(self, _t("重新显影 RAW"), _t("这张图没有对应的 RAW 文件（只有 JPG / PNG 等）。"))
            return
        dlg = RawDialog(self, p)
        if dlg.exec() != RawDialog.DialogCode.Accepted or dlg.result_rgb is None:
            return
        rgb = dlg.result_rgb
        rgba = np.dstack([rgb, np.full(rgb.shape[:2], 65535, np.uint16)])
        d = self.doc
        if d.depth != 16:
            d.convert_depth(16)
        if rgba.shape[:2] != (d.height, d.width):
            from ...edit.document import _resize
            rgba = _resize(rgba, d.width, d.height)
        l = Layer("pixel", _t('RAW 显影 · {0}', os.path.basename(p)), np.ascontiguousarray(rgba))
        d.add_layer(l, 1 if d.layers else 0, _t("重新显影 RAW"))
        self.status_msg(_t("已作为新图层放在背景上面：可以和原来的叠在一起，用蒙版取各自好的部分"))

    def _key_tool(self, ids: list[str], cycle: bool) -> None:
        slot = self.toolbar.slot_of(ids[0])
        cur = slot.tool.id if slot is not None and slot.tool.id in ids else ids[0]
        if cycle and self.tool is not None and self.tool.id in ids:
            cur = ids[(ids.index(self.tool.id) + 1) % len(ids)]
        self.set_tool(cur)

    def restore_tool(self) -> None:
        self.tool = None
        self.set_tool(self._prev_tool or "move")

    def set_opt(self, key, v) -> None:
        self.opts[key] = v
        self.options.set_value(key, v)

    def _brush_size(self, f: float) -> None:
        key = "heal_size" if self.tool is not None and self.tool.id == "heal" else "brush_size"
        v = int(max(1, min(2000, round(float(self.opts.get(key, 40)) * f + (1 if f > 1 else -1) * 0.5))))
        self.set_opt(key, v)
        self.canvas.update()

    def set_fg(self, c: QColor) -> None:
        self.fg = QColor(c)
        self.toolbar.swatches.update()

    def set_bg(self, c: QColor) -> None:
        self.bg = QColor(c)
        self.toolbar.swatches.update()

    def swap_colors(self) -> None:
        self.fg, self.bg = self.bg, self.fg
        self.toolbar.swatches.update()

    def reset_colors(self) -> None:
        self.fg, self.bg = QColor("#000000"), QColor("#FFFFFF")
        self.toolbar.swatches.update()

    def set_mask_edit(self, on: bool) -> None:
        l = self.doc.active
        self.doc.mask_edit = bool(on and l is not None and l.mask is not None)
        if self.doc.mask_edit:
            self.reset_colors()
            self.status_msg(_t("正在蒙版上画：黑色隐藏、白色显示（X 交换前景 / 背景色）"))
        self.layers.update_marks()
        self._msig = self._mask_sig()

    def show_original(self, on: bool) -> None:
        if self.canvas.show_original != on:
            self.canvas.show_original = on
            self.canvas.request_render()

    def after_stroke(self) -> None:
        self._panel_timer.start()
        self._hist_timer.start()

    def _tool_busy(self) -> bool:
        return self.tool is not None and self.tool.busy()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Backslash and not e.isAutoRepeat():
            self.show_original(True)
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Backslash and not e.isAutoRepeat():
            self.show_original(False)
            return
        super().keyReleaseEvent(e)

    # ---- 编辑 ----
    def undo(self) -> None:
        if self._tool_busy():
            return
        self._leave_transform()
        n = self.doc.history.undo()
        if n:
            self.status_msg(_t('撤销：{0}', n))
            self.props.sync()
            self._msig = self._mask_sig()

    def redo(self) -> None:
        if self._tool_busy():
            return
        self._leave_transform()
        n = self.doc.history.redo()
        if n:
            self.status_msg(_t('重做：{0}', n))
            self.props.sync()
            self._msig = self._mask_sig()

    def history_goto(self, i: int) -> None:
        if self._tool_busy():
            self.history.refresh()
            return
        self._leave_transform()
        self.doc.history.goto(i)
        self.props.sync()
        self._msig = self._mask_sig()

    def _leave_transform(self) -> None:
        if self.tool is not None and self.tool.id == "transform":
            self.tool.cancel()

    def _busy_call(self, msg: str, fn) -> None:
        with _Busy(self, msg):
            fn()
        self.status_msg("")

    def adjust_edited(self, key: str, label: str) -> None:
        l = self.doc.active
        ed = self.props.editor
        if l is None or l.kind != "adjust" or ed is None:
            return
        self.mark_interactive()
        self.canvas.hot = l.id
        self.doc.set_params(l.id, ed.params, key=f"adj{l.id}:{key}", name=_t('调整：{0}', label))

    def apply_preset(self, name: str) -> None:
        l = self.doc.active
        if l is None or l.kind != "adjust":
            return
        p = adjust.preset(name) if name else adjust.default_params()
        self.doc.set_params(l.id, p, name=_t('预设：{0}', _t(name)) if name else _t("复位调整"))
        if name and l.name in (_t("调整"),) + tuple(_t(n) for n in adjust.PRESETS):
            self.doc.layer(l.id).name = _t(name)
        self.props.sync()
        self.layers.refresh(thumbs=False)

    def text_edited(self, key: str) -> None:
        l = self.doc.active
        ed = self.props.editor
        if l is None or l.kind != "text" or ed is None:
            return
        self.mark_interactive()
        self.doc.set_params(l.id, ed.params, key=f"text{l.id}:{key}", name=_t("编辑文字"))

    def new_adjust(self, name: str = _t("调整"), params: dict | None = None, focus: str | None = None) -> None:
        self.doc.new_adjust_layer(params or {}, name)
        self.props.rebuild(focus)

    def add_mask(self) -> None:
        d = self.doc
        if d.active is None:
            return
        if d.active.kind == "text":
            info(self, _t("文字图层不能加蒙版"), _t("先「栅格化文字」，或者把文字放进图层组（Ctrl+G）再给组加蒙版。"))
            return
        d.add_mask(d.active_id, "selection" if d.selection is not None else "reveal")

    def edit_text(self, layer, pos=None) -> None:
        d = self.doc
        before = d.state()
        new = layer is None
        if new:
            from ...edit.text import default_params
            p = default_params()
            size = int(self.opts.get("text_size", 96))
            p.update(font=self.opts.get("text_font") or p["font"], size=size, color=self.fg.name())
            layer = Layer("text", _t("文字"), x=pos[0], y=int(pos[1] - size * 0.25), params=p)
            layer.render_text()
            i = d.index_of(d.active_id)
            d.layers.insert(i + 1 if i >= 0 else len(d.layers), d._register(layer))
            d.active_id = layer.id
            d.changed("layers")
        dlg = TextDialog(self, layer, new)
        ok = dlg.exec() == TextDialog.DialogCode.Accepted and (layer.params.get("text") or "").strip()
        if ok:
            d.history.push_state(_t("新建文字图层") if new else _t("编辑文字"), before)
            d.changed("layers")
            d.emit("history")
            self.props.rebuild()
        else:
            d.restore(before)

    def _need_pixels(self, what: str, write: bool = True):
        l = self.doc.active
        if l is None or l.canvas_sized:
            self.status_msg(_t('{0}需要一个像素图层：在图层面板里选中它', what))
            return None
        if write and self._locked(l, "pixels"):
            return None
        return l

    def run_filter(self, fid: str, params: dict | None = None, ask: bool = True) -> None:
        l = self._need_pixels(FILTERS[fid].name)
        if l is None:
            return
        spec = FILTERS[fid]
        if ask and (spec.params or spec.checks or spec.choices):
            dlg = FilterDialog(self, fid, params)
            if dlg.exec() != FilterDialog.DialogCode.Accepted:
                return
            params = dlg.params
        with _Busy(self, _t('正在应用{0}…', spec.name)):
            self.doc.apply_filter(l.id, fid, params or {})
        self._last_filter = (fid, dict(params or {}))
        self.a_last_filter.setText(_t('上次滤镜：{0}', spec.name))
        self.a_last_filter.setEnabled(True)
        self.status_msg(_t('已应用：{0}', spec.name))

    # ---- 选区进阶 ----
    def color_range(self) -> None:
        from .dialogs2 import ColorRangeDialog
        self._leave_quick_mask()
        dlg = ColorRangeDialog(self)
        if dlg.exec() != ColorRangeDialog.DialogCode.Accepted:
            return
        with _Busy(self, _t("正在计算色彩范围…")):
            m = dlg.full_mask()
        if not m.any():
            self.status_msg(_t("没有选中任何像素：把颜色容差调大一点，或换一个取样点"))
            return
        self.doc.set_selection(m, _t("色彩范围"))

    def select_and_mask(self) -> None:
        from .dialogs2 import RefineDialog
        d = self.doc
        if d.selection is None:
            self.status_msg(_t("先做一个大致的选区（选择主体、对象选择、套索…），再「选择并遮住」精修边缘"))
            return
        self._leave_quick_mask()
        dlg = RefineDialog(self)
        if dlg.exec() != RefineDialog.DialogCode.Accepted:
            return
        with _Busy(self, _t("正在精修边缘…")):
            m = dlg.full_mask()
        out = dlg.out.currentData()
        if out == "selection":
            d.set_selection(m, _t("选择并遮住"))
            return
        l = d.active
        if out == "new_layer" and l is not None and l.has_pixels:
            nl = d.duplicate_layer(l.id)
            l = nl
        if l is None or l.kind == "text":
            self.status_msg(_t("当前图层不能加蒙版：选中一个像素图层或组"))
            return
        x, y, w, h = l.bounds(d)
        from ...edit.document import crop_canvas
        with d.edit(_t("选择并遮住")):
            l.mask = crop_canvas(m, x, y, w, h, 0)
            l.mask_enabled = True
            l.touch_mask()
            d.selection = None
        d.emit("selection")

    def save_selection(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        d = self.doc
        if d.selection is None:
            self.status_msg(_t("先做一个选区，再存储"))
            return
        name, ok = QInputDialog.getText(self, _t("存储选区"), _t("通道名称："),
                                        text=_t('Alpha {0}', len(d.channels) + 1))
        if ok:
            n = d.save_selection(name)
            self.status_msg(_t("已存储为通道「{0}」：「选择 → 载入选区」随时取回；保存工程时一起保存", n))

    def load_selection(self) -> None:
        from .dialogs2 import LoadSelectionDialog
        if not self.doc.channels:
            self.status_msg(_t("还没有存储过选区（「选择 → 存储选区」）"))
            return
        dlg = LoadSelectionDialog(self)
        if dlg.exec() == LoadSelectionDialog.DialogCode.Accepted and dlg.ch.currentData():
            self.doc.load_selection(dlg.ch.currentData(), dlg.mode.currentData(), dlg.invert.isChecked())

    def toggle_quick_mask(self) -> None:
        """快速蒙版（Q）：选区变成一层半透明的红色，用画笔 / 橡皮擦 / 渐变直接涂选区（黑 = 不选、白 = 选）。"""
        d = self.doc
        if self._tool_busy():
            self.a_qmask.setChecked(d.quick_mask)
            return
        d.quick_mask = not d.quick_mask
        self.a_qmask.setChecked(d.quick_mask)
        if d.quick_mask:
            self.reset_colors()
            if self.tool is None or self.tool.id not in ("brush", "eraser", "gradient"):
                self.set_tool("brush")
            self.status_msg(_t("快速蒙版：红色 = 没选中。用画笔涂白色加选、涂黑色减选（X 交换），再按 Q 回到选区"))
        else:
            self.status_msg(_t("已退出快速蒙版"))
        self.canvas.selection_changed()

    def _leave_quick_mask(self) -> None:
        if self.doc.quick_mask:
            self.toggle_quick_mask()

    def layer_style(self) -> None:
        from .dialogs2 import LayerStyleDialog
        l = self.doc.active
        if l is None or not l.has_pixels:
            self.status_msg(_t("图层样式要用在像素或文字图层上"))
            return
        dlg = LayerStyleDialog(self, l)
        if dlg.exec() == LayerStyleDialog.DialogCode.Accepted:
            self.doc.set_props(l.id, name=_t("图层样式"), style=dlg.result_style())

    def clear_style(self) -> None:
        l = self.doc.active
        if l is not None and l.style:
            self.doc.set_props(l.id, name=_t("清除图层样式"), style={})

    def liquify(self) -> None:
        from .liquify_dlg import LiquifyDialog
        l = self._need_pixels(_t("液化"))
        if l is None or not l.has_pixels:
            return
        if self._locked(l, "pixels"):
            return
        self._leave_transform()
        dlg = LiquifyDialog(self, l.pixels)
        if dlg.exec() != LiquifyDialog.DialogCode.Accepted or dlg.result is None:
            return
        self.doc.replace_pixels(l.id, dlg.result, _t("液化"))
        self.status_msg(_t("已应用液化"))

    def _locked(self, l, what: str) -> bool:
        """图层锁定时提示并返回 True。what：pixels（锁定像素）/ position（锁定位置）。"""
        if l is None:
            return False
        if (what == "pixels" and (l.lock_pixels or l.lock_all)) or (what == "position" and (l.lock_pos or l.lock_all)):
            self.status_msg(_t("图层「{0}」已锁定：在图层面板上方点锁的按钮解锁", l.name))
            return True
        return False

    def repeat_filter(self) -> None:
        if self._last_filter:
            self.run_filter(self._last_filter[0], self._last_filter[1], ask=False)

    def image_size(self) -> None:
        d = self.doc
        dlg = ImageSizeDialog(self, d.width, d.height)
        if dlg.exec() == ImageSizeDialog.DialogCode.Accepted:
            w, h = dlg.w.value(), dlg.h.value()
            if (w, h) != (d.width, d.height):
                with _Busy(self, _t("正在调整图像大小…")):
                    d.resize_image(w, h)

    def crop_to_selection(self) -> None:
        bb = self.doc.selection_bbox()
        if bb is None:
            self.status_msg(_t("先做一个选区"))
            return
        self.doc.crop(*bb)

    def select_subject(self) -> None:
        from ...edit.paint import object_select_doc
        d = self.doc
        mx, my = d.width * 0.02, d.height * 0.02
        with _Busy(self, _t("正在识别主体…")):
            m = object_select_doc(d, (mx, my, d.width - mx, d.height - my))
        if not m.any():
            self.status_msg(_t("没有识别出主体：用「对象选择」工具框住它试试"))
            return
        d.set_selection(m, _t("选择主体"))
        self.status_msg(_t("已选出主体；边缘不准可以用「对象选择」工具 Shift / Alt 补选、减选"))

    def _sel_number(self, title: str, label: str, default: int, fn) -> None:
        if self.doc.selection is None:
            self.status_msg(_t("先做一个选区"))
            return
        v = NumberDialog.ask(self, title, label, default, 1, 500)
        if v:
            with _Busy(self):
                fn(v)

    def fill(self) -> None:
        d = self.doc
        l = self._need_pixels(_t("填充"))
        if l is None:
            return
        dlg = FillDialog(self, d.selection is not None)
        if dlg.exec() != FillDialog.DialogCode.Accepted:
            return
        k = dlg.kind.currentData()
        if k == "content":
            self.content_fill()
            return
        c = {"fg": self.fg, "bg": self.bg, "black": QColor(0, 0, 0), "white": QColor(255, 255, 255),
             "gray": QColor(128, 128, 128)}[k]
        with _Busy(self):
            d.fill(l.id, (c.red(), c.green(), c.blue(), 255), dlg.opacity.value() / 100)

    def content_fill(self) -> None:
        d = self.doc
        l = self._need_pixels(_t("内容识别填充"))
        if l is None:
            return
        if d.selection is None:
            self.status_msg(_t("先用选框 / 套索圈出要去掉的东西（圈大一点），再内容识别填充"))
            return
        with _Busy(self, _t("正在用周围的画面补上…")):
            ok = d.content_fill(l.id)
        self.status_msg(_t("完成：不满意可以撤销，换个选区再试，或用污点修复画笔补几下") if ok else _t("选区不在当前图层上"))

    def clear(self) -> None:
        d = self.doc
        if self.tool is not None and self.tool.id == "crop":
            return
        l = self._need_pixels(_t("清除"))
        if l is None:
            return
        if d.selection is None:
            self.status_msg(_t("先做一个选区，再按 Delete 清除选区里的内容"))
            return
        d.clear_selected(l.id)

    # ---- 剪贴板 ----
    def copy(self, cut: bool = False, merged: bool = False) -> None:
        from ...edit.document import crop_canvas, mask_bbox
        d = self.doc
        if merged:
            src = d.flat_rgba()
            ox = oy = 0
        else:
            l = self._need_pixels(_t("拷贝"), write=not cut)
            if l is None:
                return
            src, ox, oy = to8(l.pixels), l.x, l.y
        sel = d.selection
        if sel is not None:
            m = crop_canvas(sel, ox, oy, src.shape[1], src.shape[0], 0)
            bb = mask_bbox(m)
            if bb is None:
                return
            x, y, w, h = bb
            part = src[y:y + h, x:x + w].copy()
            part[..., 3] = (part[..., 3].astype(np.uint16) * m[y:y + h, x:x + w] // 255).astype(np.uint8)
        else:
            part = src.copy()
        part = np.ascontiguousarray(part)
        img = QImage(part.data, part.shape[1], part.shape[0], part.shape[1] * 4, QImage.Format.Format_RGBA8888).copy()
        QApplication.clipboard().setImage(img)
        if cut and not merged and sel is not None:
            d.clear_selected(d.active_id)
        self.status_msg(_t('已拷贝 {0} × {1}', part.shape[1], part.shape[0]))

    def paste(self) -> None:
        img = QApplication.clipboard().image()
        if img.isNull():
            self.status_msg(_t("剪贴板里没有图片"))
            return
        img = img.convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = img.width(), img.height()
        arr = np.frombuffer(img.constBits(), np.uint8, img.bytesPerLine() * h).reshape(h, img.bytesPerLine())
        arr = arr[:, :w * 4].reshape(h, w, 4).copy()
        self.doc.place_image(arr, _t("粘贴的图片"))

    # ---- 文件 ----
    def open_image(self, project: bool = False) -> None:
        start = os.path.dirname(self.doc.source.path or self.doc.source.project or "") or os.path.expanduser("~")
        flt = _t("修图工程 (*.pcedit)") if project else eio.OPEN_FILTER
        p, _ = QFileDialog.getOpenFileName(self, _t("打开"), start, flt)
        if p:
            open_editor(self.ctx, self.parent(), path=p)

    def merge_exposures(self) -> None:
        from .merge import merge_paths
        start = os.path.dirname(self.doc.source.path or "") or os.path.expanduser("~")
        ps, _ = QFileDialog.getOpenFileNames(self, _t("选择同一场景、不同曝光的几张照片"), start, eio.OPEN_FILTER)
        if ps:
            merge_paths(self.ctx, self.parent() or self, [p for p in ps if not p.lower().endswith(eio.PROJECT_EXT)])

    def fix_ghost(self) -> None:
        from .merge import fix_ghost
        fix_ghost(self)

    def place_image(self) -> None:
        from PIL import Image, ImageOps
        start = os.path.dirname(self.doc.source.path or "") or os.path.expanduser("~")
        p, _ = QFileDialog.getOpenFileName(self, _t("置入图片"), start, eio.OPEN_FILTER)
        if not p:
            return
        try:
            with Image.open(p) as im:
                im = ImageOps.exif_transpose(im)
                arr = np.array(im.convert("RGBA"), dtype=np.uint8)
        except Exception as e:
            info(self, _t("无法打开"), f"{p}<br>{e}", "ERROR")
            return
        self.doc.place_image(arr, os.path.splitext(os.path.basename(p))[0])
        self.set_tool("transform")

    def save_project(self, ask: bool = False) -> bool:
        d = self.doc
        path = eio.default_project_path(d)
        if ask or not path:
            path, _ = QFileDialog.getSaveFileName(self, _t("保存工程"), path or os.path.expanduser(_t("~/未命名.pcedit")),
                                                  _t("修图工程 (*.pcedit)"))
            if not path:
                return False
            if not path.lower().endswith(eio.PROJECT_EXT):
                path += eio.PROJECT_EXT
        self._leave_transform()
        try:
            with _Busy(self, _t("正在保存工程…")):
                size = eio.save_project(d, path)
        except Exception as e:
            info(self, _t("保存失败"), str(e), "ERROR")
            return False
        from ...ops.pathsafe import human_size
        self.status_msg(_t('工程已保存：{0}（{1}）。和原片同名时会跟着照片一起移动 / 改名', path, human_size(size)))
        self._update_title()
        return True

    def export(self) -> bool:
        self._leave_transform()
        dlg = ExportDialog(self)
        if dlg.exec() != ExportDialog.DialogCode.Accepted:
            return False
        path = os.path.normpath(dlg.path.text().strip())
        s = self.ctx.settings if self.ctx is not None else None
        if s is not None:
            s.set("editor_export_fmt", dlg.fmt.currentData())
            s.set("editor_export_q", int(dlg.quality.value()))
            s.set("editor_strip_gps", dlg.strip_gps.isChecked())
        try:
            with _Busy(self, _t("正在导出…")):
                size = eio.export_image(self.doc, path, int(dlg.quality.value()), dlg.keep_exif.isChecked(),
                                        dlg.strip_gps.isChecked(), deep=dlg.deep_on())
        except Exception as e:
            info(self, _t("导出失败"), str(e), "ERROR")
            return False
        from ...ops.pathsafe import human_size
        self.doc.modified = False
        self._update_title()
        self.status_msg(_t('已导出：{0}（{1}）', path, human_size(size)))
        if self.ctx is not None:
            self.ctx.status.emit(_t('修图已导出：{0}', path))
            if dlg.rescan.isChecked() and dlg.in_library() and hasattr(self.ctx, "rescan_requested"):
                self.ctx.rescan_requested.emit()
        return True

    def start_tour(self) -> None:
        """新手教程：依次照亮工具栏、选项栏、画布、属性、图层、历史记录、菜单，配文字说明。"""
        from .tutorial import TourOverlay
        if self._tour is not None or not self.isVisible():
            return
        if self.tool is not None and self.tool.busy():
            self.tool.deactivate()

        def done():
            self._tour = None
            if self.ctx is not None:
                self.ctx.settings.set("editor_tour_done", True)
            self.canvas.setFocus()
        self._tour = TourOverlay(self, done)

    def show_help(self) -> None:
        rows = [("V / M / L / W", _t("移动 / 选框 / 套索 / 魔棒、对象选择（Shift+W 轮换）")),
                ("Q", _t("快速蒙版（用画笔涂选区）")),
                ("J / Shift+J", _t("污点修复 / 修补 / 红眼（Shift+J 轮换）")),
                ("Ctrl+Alt+R", _t("选择并遮住（精修头发等边缘）")),
                ("Ctrl+Shift+X / Ctrl+Shift+R", _t("液化 / 镜头校正")),
                ("C / I", _t("裁剪（按住 Ctrl 拖线拉直）/ 吸管")),
                ("J / B / S / E", _t("污点修复 / 画笔 / 仿制图章（Alt 取样）/ 橡皮擦")),
                ("K / G / T", _t("油漆桶 / 渐变 / 文字")),
                (_t("H / Z / 空格"), _t("抓手 / 缩放 / 按住空格临时拖动画面")),
                ("[ ]", _t("笔刷变小 / 变大")),
                ("X / D", _t("交换前景背景色 / 恢复黑白")),
                ("Shift / Alt", _t("选区工具：添加 / 减去（一起按 = 交叉）")),
                ("Ctrl+Z / Ctrl+Shift+Z", _t("撤销 / 重做（历史记录面板里可以直接跳到任一步）")),
                ("Ctrl+J / Ctrl+Shift+J", _t("通过拷贝 / 剪切的图层（有选区时只取选区）")),
                ("Ctrl+T", _t("自由变换：拖角缩放、框外旋转，回车应用；按住 Ctrl 拖角 = 扭曲、拖边 = 斜切")),
                ("O / Shift+O", _t("减淡 / 加深 / 海绵（Shift+O 轮换）")),
                ("R / Shift+R", _t("模糊 / 锐化 / 涂抹（Shift+R 轮换）")),
                ("Ctrl+G / Ctrl+Shift+G", _t("图层编组 / 取消编组")),
                ("Ctrl+Alt+G", _t("创建 / 释放剪贴蒙版（只作用于下面那一层）")),
                ("Ctrl+E / Ctrl+Alt+Shift+E", _t("向下合并 / 盖印可见图层")),
                ("Ctrl+A / D / Shift+I", _t("全选 / 取消选择 / 反选")),
                ("Shift+F5 / Shift+Backspace", _t("填充 / 内容识别填充")),
                (_t("Ctrl+0 / Ctrl+1 / 滚轮"), _t("适合窗口 / 100% / 以鼠标为中心缩放")),
                (_t("按住 \\"), _t("看原图（只显示背景层）")),
                ("Ctrl+S / Ctrl+Shift+S", _t("保存工程（保留图层）/ 导出图片（新文件）"))]
        html = "<table cellspacing=6>" + "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in rows) + "</table>"
        info(self, _t("修图快捷键"), html, "SHORTCUTS")

    def closeEvent(self, e):
        if self.tool is not None and self.tool.busy():
            self.tool.deactivate()
        self._leave_transform()
        if self.doc.modified:
            r = choose(self, _t("保存修改？"), _t("这张图有还没保存的修改。<br>「保存工程」保留全部图层，以后可以接着改；"
                       "也可以先取消，用「文件 → 导出图片」存一张成品。"),
                       [("save", _t("保存工程"), "primary"), ("drop", _t("不保存"), "danger"), (None, _t("取消"), "secondary")])
            if r is None or (r == "save" and not self.save_project()):
                e.ignore()
                return
        if self.ctx is not None:
            o = {k: v for k, v in self.opts.items() if isinstance(v, (int, float, str, bool))
                 and not k.startswith("crop_angle")}
            o["_fg"], o["_bg"] = self.fg.name(), self.bg.name()
            self.ctx.settings.set("editor_opts", o)
        self.doc.listeners.clear()
        super().closeEvent(e)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

_open: set = set()


def open_editor(ctx, parent=None, group=None, path: str | None = None, doc=None) -> EditorWindow | None:
    """从照片库的一张照片（group）、一个文件（path）或已经做好的文档（doc，例如合并曝光的结果）打开修图窗口。"""
    exiftool = ctx.library.exiftool_path if ctx is not None else None
    try:
        if doc is not None:
            pass
        elif group is not None:
            if getattr(group, "is_video", False):
                info(parent, _t("不能修图"), _t("视频不能在这里修图。"), "NOTICE")
                return None
            from ...ops.exporter import source_file
            f = source_file(group)
            if f is None:
                info(parent, _t("不能修图"), _t("这组里没有可以打开的图片文件。"), "NOTICE")
                return None
            proj = eio.project_path_for(f.path)
            if os.path.exists(proj):
                r = choose(parent, _t("接着上次修？"), _t('这张照片有一个修图工程：<br><b>{0}</b><br>打开工程会恢复上次的全部图层；打开原片则从头开始。', os.path.basename(proj)),
                           [("proj", _t("打开工程"), "primary"), ("orig", _t("打开原片"), "secondary"), (None, _t("取消"), "secondary")])
                if r is None:
                    return None
                if r == "proj":
                    path = proj
            if not path and f.role == "raw":                # 只有 RAW：先弹出显影对话框
                from .rawdlg import develop_doc
                doc = develop_doc(parent, f.path, group.name, group.gid, eio._group_exif(group))
                if doc is None:
                    return None
                if doc is False:
                    doc = None
            if doc is None:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    doc = eio.load_project(path) if path else eio.open_group(group, exiftool)
                finally:
                    QApplication.restoreOverrideCursor()
        elif path:
            from ...edit.raw import is_raw
            if is_raw(path):
                from .rawdlg import develop_doc
                doc = develop_doc(parent, path)
                if doc is None:
                    return None
                if doc is False:
                    doc = None
            if doc is None:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    doc = eio.open_file(path, exiftool)
                finally:
                    QApplication.restoreOverrideCursor()
    except Exception as e:
        info(parent, _t("打开失败"), f"{e}", "ERROR")
        return None
    if doc is None:
        return None
    w = EditorWindow(ctx, doc, parent)
    _open.add(w)
    w.destroyed.connect(lambda *_: _open.discard(w))
    studio = getattr(ctx, "studio", None)          # 主窗口的「修图」模式：作为标签页嵌进去
    if studio is not None:
        studio.adopt(w)
        return w
    w.showMaximized()
    w.raise_()
    w.activateWindow()
    return w
