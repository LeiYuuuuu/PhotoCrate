"""「相册」的单张照片页：大图 + 信息栏（时间、地点、相机、人物、场景、照片里的文字），以及基础编辑。

基础编辑（类似 iOS）：自动增强、光效 / 颜色 / 细节滑块、滤镜（可调强度）、旋转 / 翻转 / 拉直 / 裁剪。
预览在缩小的图上实时计算；「存储为新照片」在原尺寸上重新计算并另存（原片不变），然后重新扫描让新照片进入相册。
"""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QScrollArea, QSlider, QStackedWidget, QVBoxLayout, QWidget)

from .. import theme
from ..dialogs import info, run_with_progress
from ..theme import font
from ..widgets.deco import TacButton, caption, hline, micro_label
from .canvas import PhotoCanvas, centered_aspect, qimage_from_rgb
from ...core import smart
from ...edit import adjust, quick
from ...i18n import t as _t

PREVIEW_SIDE = 1600
PROXY_SIDE = 1100


def _fmt_exposure(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return f"1/{round(1 / v)}s" if 0 < v < 0.5 else f"{v:g}s"


def shooting_line(g) -> str:
    m = g.meta or {}
    parts = []
    if m.get("fnumber"):
        parts.append(f"ƒ/{float(m['fnumber']):g}")
    if m.get("exposure"):
        parts.append(_fmt_exposure(m["exposure"]))
    if m.get("iso"):
        parts.append(f"ISO {int(float(m['iso']))}")
    if m.get("focal"):
        parts.append(f"{float(m['focal']):g}mm")
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# 信息栏
# ---------------------------------------------------------------------------

class InfoPanel(QScrollArea):
    """每换一张照片整块重建（内容不多，比逐项更新简单可靠）。"""

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_box: QPlainTextEdit | None = None

    def _row(self, lay: QVBoxLayout, label: str, value: str, sel: bool = False) -> None:
        if not value:
            return
        lay.addWidget(micro_label(label))
        v = QLabel(value)
        v.setWordWrap(True)
        v.setFont(font("cn", 10))
        if sel:
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(v)

    def show_group(self, g, hits: list[int]) -> None:
        from ...analyzers.builtin import place_label
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(6)
        d = g.date
        self._row(lay, _t("拍摄时间"), d.strftime("%Y-%m-%d  %H:%M:%S") if d else _t("（没有拍摄日期）"))
        self._row(lay, _t("地点"), place_label(g.feats.get("geo")) or "")
        cam = g.feats.get("camera") or {}
        make, model = cam.get("make") or "", cam.get("model") or ""
        self._row(lay, _t("相机"), model if make and model.lower().startswith(make.lower()) else f"{make} {model}".strip())
        self._row(lay, _t("镜头"), cam.get("lens") or "")
        self._row(lay, _t("拍摄参数"), shooting_line(g))
        m = g.meta or {}
        size = f"{m['width']} × {m['height']}" if m.get("width") and m.get("height") else ""
        self._row(lay, _t("文件"), f"{g.name}\n{g.ext_label} · {g.total_size / 1e6:.1f} MB" + (f" · {size}" if size else ""),
                  sel=True)
        # 路径里没有空格时 QLabel 不会换行：在分隔符后面加零宽空格
        self._row(lay, _t("位置"), g.folder.replace("\\", "\\​").replace("/", "/​"), sel=True)
        people = [c.name for c in (getattr(self.page.ctx.library.ai, "people", []) or [])
                  if c.named and g.gid in c.gids]
        self._row(lay, _t("人物"), _t("、").join(people))
        sc = g.feats.get("scene") or {}
        if sc.get("top3"):
            self._row(lay, _t("画面内容（AI）"), "   ".join(f"{_t(n)} {p:.0%}" for n, p in sc["top3"] if p >= 0.08))
        tr = g.feats.get("trip") or {}
        self._row(lay, _t("旅行"), tr.get("name") or "")
        marks = self.page.marks
        self._row(lay, _t("相簿"), _t("、").join(marks.albums[a].name for a in marks.albums_of(g)))
        lay.addWidget(hline())
        lay.addWidget(micro_label(_t("照片里的文字")))
        lib = self.page.ctx.library
        self.text_box = None
        if g.is_video:
            lay.addWidget(caption(_t("视频不识别文字"), "faint", 9))
        elif "ocr" not in g.feats:
            msg = _t("还没有识别（在「相簿」页上方开始识别）") if lib.ocr.ready() else \
                _t("下载文字识别模型后可以识别、搜索照片里的文字")
            lay.addWidget(caption(msg, "faint", 9))
        elif not g.feats["ocr"]:
            lay.addWidget(caption(_t("没有识别到文字"), "faint", 9))
        else:
            lines = g.feats["ocr"]
            tb = self.text_box = QPlainTextEdit("\n".join(l["t"] for l in lines))
            tb.setReadOnly(True)
            tb.setMinimumHeight(90)
            tb.setMaximumHeight(240)
            lay.addWidget(tb)
            b = TacButton(_t("复制全部文字"), "secondary", height=28)
            b.clicked.connect(lambda: (QGuiApplication.clipboard().setText(tb.toPlainText()),
                                       self.page.status(_t("已复制照片里的文字"))))
            r = QHBoxLayout()
            r.addWidget(b)
            r.addStretch(1)
            lay.addLayout(r)
            tip = _t("共 {0} 行。大图上单击文字框可以复制那一行。", len(lines))
            if hits:
                tip = _t("搜索词出现在 {0} 行（黄色框）。", len(hits)) + tip
            lay.addWidget(caption(tip, "faint", 8))
        lay.addStretch(1)
        old = self.takeWidget()
        self.setWidget(body)
        if old is not None:
            old.deleteLater()


# ---------------------------------------------------------------------------
# 基础编辑面板
# ---------------------------------------------------------------------------

class QuickEditPanel(QWidget):
    changed = Signal()
    tab_changed = Signal(str)
    save = Signal()
    cancel = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = quick.QuickState()
        self.sliders: dict[str, tuple[QSlider, QLabel, float]] = {}
        self.preset_btns: dict[str | None, QPushButton] = {}
        self._mute = False
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.pages = QStackedWidget()
        for i, (key, name) in enumerate((("adjust", _t("调整")), ("filter", _t("滤镜")), ("crop", _t("裁剪")))):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setProperty("seg", True)
            b.setMinimumHeight(30)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.tab_group.addButton(b, i)
            tabs.addWidget(b)
            b.clicked.connect(lambda _=False, k=key, i=i: self._tab(k, i))
        self.tab_group.button(0).setChecked(True)
        v.addLayout(tabs)
        self.pages.addWidget(self._adjust_page())
        self.pages.addWidget(self._filter_page())
        self.pages.addWidget(self._crop_page())
        v.addWidget(self.pages, 1)
        self.summary = caption("", "faint", 8)
        v.addWidget(self.summary)
        r = QHBoxLayout()
        self.b_reset = TacButton(_t("还原"), "secondary", height=32)
        self.b_reset.clicked.connect(self.reset)
        self.b_cancel = TacButton(_t("取消"), "secondary", height=32)
        self.b_cancel.clicked.connect(self.cancel.emit)
        self.b_save = TacButton(_t("存储为新照片"), "primary", en="SAVE", height=32)
        self.b_save.setToolTip(_t("在原片旁边另存一张（原名_编辑.jpg，保留 EXIF），原片不会被修改"))
        self.b_save.clicked.connect(self.save.emit)
        r.addWidget(self.b_reset)
        r.addWidget(self.b_cancel)
        r.addStretch(1)
        r.addWidget(self.b_save)
        v.addLayout(r)
        p = theme.current()
        self.setStyleSheet(f"""
QPushButton[seg="true"] {{ background: {p.surface2}; color: {p.ink2}; border: 1px solid {p.line}; padding: 4px 10px; }}
QPushButton[seg="true"]:checked {{ background: {p.slab}; color: {p.accent}; border-color: {p.slab}; font-weight: 700; }}
QPushButton[preset="true"] {{ background: transparent; border: 2px solid transparent; padding: 2px; color: {p.ink2}; }}
QPushButton[preset="true"]:checked {{ border-color: {p.accent}; color: {p.ink}; font-weight: 700; }}
""")

    @property
    def tab(self) -> str:
        return ("adjust", "filter", "crop")[self.pages.currentIndex()]

    def _tab(self, key: str, i: int) -> None:
        self.pages.setCurrentIndex(i)
        self.tab_changed.emit(key)

    def _scroll(self, inner: QWidget) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setWidget(inner)
        return sc

    def _adjust_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 4, 4, 4)
        v.setSpacing(4)
        self.b_auto = TacButton(_t("自动增强"), "secondary", en="AUTO", height=32)
        self.b_auto.setCheckable(True)
        self.b_auto.setToolTip(_t("按这张照片的明暗分布自动调整曝光、高光、阴影、对比度和鲜艳度；再点一次取消"))
        self.b_auto.toggled.connect(self._auto)
        v.addWidget(self.b_auto)
        for title, keys in ((_t("光效"), quick.LIGHT), (_t("颜色"), quick.COLOR), (_t("细节与效果"), quick.DETAIL)):
            v.addSpacing(6)
            v.addWidget(micro_label(title))
            for key, name in keys:
                v.addLayout(self._slider(key, _t(name)))
        v.addStretch(1)
        return self._scroll(w)

    def _slider(self, key: str, name: str) -> QHBoxLayout:
        _, lo, hi, dec = adjust.SLIDERS[key]
        mul = 10 ** dec
        r = QHBoxLayout()
        lab = QLabel(name)
        lab.setMinimumWidth(70)
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(int(lo * mul), int(hi * mul))
        s.setValue(0)
        s.setToolTip(_t("双击数值归零"))
        val = QLabel("0")
        val.setMinimumWidth(40)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val.setFont(font("mono", 8.5))
        s.valueChanged.connect(lambda x, k=key: self._slid(k, x))
        val.mouseDoubleClickEvent = lambda e, s=s: s.setValue(0)
        lab.mouseDoubleClickEvent = lambda e, s=s: s.setValue(0)
        r.addWidget(lab)
        r.addWidget(s, 1)
        r.addWidget(val)
        self.sliders[key] = (s, val, mul)
        return r

    def _slid(self, key: str, x: int) -> None:
        s, val, mul = self.sliders[key]
        v = x / mul
        val.setText(f"{v:+.2f}" if mul > 1 else (f"{v:+.0f}" if v else "0"))
        if self._mute:
            return
        if abs(v) < 1e-9:
            self.state.sliders.pop(key, None)
        else:
            self.state.sliders[key] = v
        self._emit()

    def _auto(self, on: bool) -> None:
        if self._mute:
            return
        self.state.auto = quick.auto_params(self.base) if on and getattr(self, "base", None) is not None else {}
        self._emit()

    def _filter_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 4, 4, 4)
        self.preset_grid = QGridLayout()
        self.preset_grid.setSpacing(6)
        self.preset_group = QButtonGroup(self)
        self.preset_group.setExclusive(True)
        names: list[str | None] = [None] + list(adjust.PRESETS)
        for i, name in enumerate(names):
            b = QPushButton(_t("原图") if name is None else _t(name))
            b.setProperty("preset", True)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumSize(96, 92)
            b.clicked.connect(lambda _=False, n=name: self._preset(n))
            self.preset_group.addButton(b)
            self.preset_btns[name] = b
            self.preset_grid.addWidget(b, i // 3, i % 3)
        self.preset_btns[None].setChecked(True)
        v.addLayout(self.preset_grid)
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("强度")))
        self.amount = QSlider(Qt.Orientation.Horizontal)
        self.amount.setRange(0, 100)
        self.amount.setValue(100)
        self.amount.valueChanged.connect(self._amount)
        self.amount_lab = QLabel("100%")
        self.amount_lab.setFont(font("mono", 8.5))
        r.addWidget(self.amount, 1)
        r.addWidget(self.amount_lab)
        v.addLayout(r)
        v.addStretch(1)
        return self._scroll(w)

    def set_preset_icons(self, base_small: np.ndarray | None) -> None:
        """用当前照片的小图渲染每个滤镜的样子作为按钮图标。"""
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon
        if base_small is None:
            return
        for name, b in self.preset_btns.items():
            img = base_small if name is None else adjust.develop(base_small, adjust.preset(name),
                                                                 adjust.RenderCtx(scale=0.1, cw=base_small.shape[1] * 10,
                                                                                  ch=base_small.shape[0] * 10))
            b.setIcon(QIcon(QPixmap.fromImage(qimage_from_rgb(img))))
            b.setIconSize(QSize(84, 60))
            b.setStyleSheet("text-align: center;")

    def _preset(self, name: str | None) -> None:
        self.state.preset = name
        self._emit()

    def _amount(self, x: int) -> None:
        self.amount_lab.setText(f"{x}%")
        if not self._mute:
            self.state.amount = float(x)
            if self.state.preset:
                self._emit()

    def _crop_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 4, 4, 4)
        v.setSpacing(8)
        r = QHBoxLayout()
        b1 = TacButton(_t("向左转 90°"), "secondary", height=30)
        b1.clicked.connect(lambda: self._rotate(-1))
        b2 = TacButton(_t("向右转 90°"), "secondary", height=30)
        b2.clicked.connect(lambda: self._rotate(1))
        b3 = TacButton(_t("⇋ 翻转"), "secondary", height=30)
        b3.clicked.connect(self._flip)
        for b in (b1, b2, b3):
            r.addWidget(b)
        v.addLayout(r)
        v.addWidget(micro_label(_t("拉直")))
        r = QHBoxLayout()
        self.angle = QSlider(Qt.Orientation.Horizontal)
        self.angle.setRange(-450, 450)
        self.angle.setValue(0)
        self.angle.valueChanged.connect(self._angle)
        self.angle_lab = QLabel("0.0°")
        self.angle_lab.setFont(font("mono", 8.5))
        self.angle_lab.setMinimumWidth(48)
        self.angle_lab.mouseDoubleClickEvent = lambda e: self.angle.setValue(0)
        r.addWidget(self.angle, 1)
        r.addWidget(self.angle_lab)
        v.addLayout(r)
        v.addWidget(micro_label(_t("比例")))
        self.aspect = QComboBox()
        for key, name, _ in quick.ASPECTS:
            self.aspect.addItem(_t(name), key)
        self.aspect.currentIndexChanged.connect(lambda _: self._emit_aspect())
        v.addWidget(self.aspect)
        b = TacButton(_t("重置裁剪"), "secondary", height=30)
        b.clicked.connect(self._reset_crop)
        v.addWidget(b)
        v.addWidget(caption(_t("拖动框的边和角调整范围，拖框内移动。拉直时会自动放大，四角不会出现空白。"), "faint", 8))
        v.addStretch(1)
        return self._scroll(w)

    aspect_changed = Signal()

    def _emit_aspect(self) -> None:
        if not self._mute:
            self.aspect_changed.emit()

    def aspect_ratio(self, w: int, h: int) -> float | None:
        key = self.aspect.currentData()
        for k, _, r in quick.ASPECTS:
            if k == key:
                if r is None:
                    return None
                return (w / h) if r == 0.0 else r
        return None

    def _rotate(self, d: int) -> None:
        self.state.rot = (self.state.rot + d) % 4
        self.state.crop = (0.0, 0.0, 1.0, 1.0)
        self._emit()

    def _flip(self) -> None:
        self.state.flip = not self.state.flip
        x0, y0, x1, y1 = self.state.crop
        self.state.crop = (1 - x1, y0, 1 - x0, y1)
        self._emit()

    def _angle(self, x: int) -> None:
        self.angle_lab.setText(f"{x / 10:+.1f}°")
        if not self._mute:
            self.state.angle = x / 10
            self._emit()

    def _reset_crop(self) -> None:
        self.state.rot, self.state.flip, self.state.angle = 0, False, 0.0
        self.state.crop = (0.0, 0.0, 1.0, 1.0)
        self._mute = True
        self.angle.setValue(0)
        self.aspect.setCurrentIndex(0)
        self._mute = False
        self._emit()

    def _emit(self) -> None:
        self.summary.setText(quick.describe(self.state))
        self.changed.emit()

    def reset(self) -> None:
        self.load(quick.QuickState())
        self._emit()

    def load(self, st: quick.QuickState) -> None:
        self.state = st
        self._mute = True
        for key, (s, val, mul) in self.sliders.items():
            s.setValue(int(round(float(st.sliders.get(key, 0.0)) * mul)))
            self._slid_label(key)
        self.b_auto.setChecked(bool(st.auto))
        self.preset_btns.get(st.preset, self.preset_btns[None]).setChecked(True)
        self.amount.setValue(int(st.amount))
        self.angle.setValue(int(round(st.angle * 10)))
        self.aspect.setCurrentIndex(0)
        self._mute = False
        self.summary.setText(quick.describe(st))

    def _slid_label(self, key: str) -> None:
        s, val, mul = self.sliders[key]
        v = s.value() / mul
        val.setText(f"{v:+.2f}" if mul > 1 else (f"{v:+.0f}" if v else "0"))


# ---------------------------------------------------------------------------
# 单张照片页
# ---------------------------------------------------------------------------

class PhotoDetail(QWidget):
    back = Signal()
    changed = Signal()          # 收藏 / 相簿等变了，网格要重绘

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self.ctx = page.ctx
        self.groups: list = []
        self.i = 0
        self.hits: dict[int, list[str]] = {}
        self.base: np.ndarray | None = None       # 当前照片的预览（float32 RGB，长边 ≤ PREVIEW_SIDE）
        self.proxy: np.ndarray | None = None
        self.editing = False
        self._load_token = 0
        self._full_size: tuple[int, int] | None = None
        p = theme.current()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("detailBar")
        bar.setStyleSheet(f"#detailBar {{ background: {p.surface}; border-bottom: 1px solid {p.line}; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.setSpacing(8)
        self.b_back = TacButton(_t("‹ 返回"), "secondary", height=32)
        self.b_back.clicked.connect(self._back)
        bl.addWidget(self.b_back)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        self.t_title = QLabel("")
        self.t_title.setFont(font("cn", 12, 800))
        self.t_sub = QLabel("")
        self.t_sub.setProperty("role", "dim")
        tv.addWidget(self.t_title)
        tv.addWidget(self.t_sub)
        bl.addLayout(tv, 1)
        self.b_fav = TacButton("♡", "secondary", height=32)
        self.b_fav.setToolTip(_t("收藏（F）"))
        self.b_fav.clicked.connect(self.toggle_fav)
        self.b_text = TacButton(_t("文字"), "secondary", height=32)
        self.b_text.setCheckable(True)
        self.b_text.setToolTip(_t("显示照片里识别出的文字框（单击复制）"))
        self.b_text.toggled.connect(self._toggle_text)
        self.b_info = TacButton(_t("信息"), "secondary", height=32)
        self.b_info.setCheckable(True)
        self.b_info.setChecked(True)
        self.b_info.toggled.connect(lambda on: self.side.setVisible(on or self.editing))
        self.b_edit = TacButton(_t("编辑"), "primary", en="EDIT", height=32)
        self.b_edit.clicked.connect(self.start_edit)
        self.b_studio = TacButton(_t("在修图中打开"), "dark", height=32)
        self.b_studio.setToolTip(_t("切换到「修图」模式，用图层、蒙版、修复画笔等专业工具处理这张照片"))
        self.b_studio.clicked.connect(self.open_studio)
        self.b_more = TacButton("⋯", "secondary", height=32)
        self.b_more.clicked.connect(lambda: self.page.photo_menu(self.b_more.mapToGlobal(self.b_more.rect().bottomLeft()),
                                                                [self.group] if self.group else []))
        for b in (self.b_fav, self.b_text, self.b_info, self.b_edit, self.b_studio, self.b_more):
            bl.addWidget(b)
        v.addWidget(bar)
        mid = QHBoxLayout()
        mid.setSpacing(0)
        self.canvas = PhotoCanvas()
        self.canvas.copied.connect(lambda t: self.page.status(_t("已复制：{0}", t[:60])))
        self.canvas.nav.connect(self.step)
        self.canvas.crop_changed.connect(self._crop_from_canvas)
        mid.addWidget(self.canvas, 1)
        self.side = QStackedWidget()
        self.side.setFixedWidth(340)
        self.side.setObjectName("detailSide")
        self.side.setStyleSheet(f"#detailSide {{ background: {p.surface}; border-left: 1px solid {p.line}; }}")
        self.info = InfoPanel(page)
        self.edit = QuickEditPanel()
        self.edit.changed.connect(self._schedule)
        self.edit.tab_changed.connect(lambda _: self._render(final=True))
        self.edit.aspect_changed.connect(self._apply_aspect)
        self.edit.save.connect(self.save_edit)
        self.edit.cancel.connect(self.cancel_edit)
        self.side.addWidget(self.info)
        self.side.addWidget(self.edit)
        mid.addWidget(self.side)
        v.addLayout(mid, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(40)
        self._timer.timeout.connect(lambda: self._render(final=False))
        self._final = QTimer(self)
        self._final.setSingleShot(True)
        self._final.setInterval(350)
        self._final.timeout.connect(lambda: self._render(final=True))

    @property
    def group(self):
        return self.groups[self.i] if 0 <= self.i < len(self.groups) else None

    # ---- 显示 ----
    def show_photos(self, groups: list, index: int, hits: dict[int, list[str]] | None = None) -> None:
        self.groups = groups
        self.hits = hits or {}
        self.i = max(0, min(index, len(groups) - 1))
        self._show_current()

    def step(self, d: int) -> None:
        if self.editing or not self.groups:
            return
        j = self.i + d
        if 0 <= j < len(self.groups):
            self.i = j
            self._show_current()

    def _show_current(self) -> None:
        g = self.group
        if g is None:
            return
        d = g.date
        self.t_title.setText(smart.day_title(d.date()) + d.strftime("  %H:%M") if d else g.name)
        from ...analyzers.builtin import place_label
        self.t_sub.setText(f"{place_label(g.feats.get('geo')) or ''}    {self.i + 1} / {len(self.groups)}")
        self._update_fav()
        hits = smart.matched_lines(g, self.hits.get(g.gid, []))
        self.canvas.set_text(g.feats.get("ocr") or [], hits)
        self.b_text.setEnabled(bool(g.feats.get("ocr")))
        self.info.show_group(g, hits)
        self.b_edit.setEnabled(not g.is_video)
        self.b_studio.setEnabled(not g.is_video)
        # 先用缩略图，后台读大预览
        pm = QPixmap(g.thumb) if g.thumb else None
        self.canvas.set_pixmap(pm)
        self.base = self.proxy = None
        self._load_token += 1
        tok = self._load_token
        lib = self.ctx.library

        def job(prog, cancel, g=g):
            from ...core.imaging import load_preview
            im = load_preview(g.primary, g.primary_file.role, PREVIEW_SIDE, lib.exiftool_path)[0]
            return np.asarray(im.convert("RGB"))

        self.ctx.run(job, on_done=lambda arr, tok=tok: self._loaded(tok, arr),
                     on_error=lambda tb: self.page.status(_t("读取大图失败：") + tb.strip().splitlines()[-1]))

    def _loaded(self, tok: int, arr) -> None:
        if tok != self._load_token or arr is None:
            return
        self.base = quick.to_float(arr)
        h, w = self.base.shape[:2]
        r = min(1.0, PROXY_SIDE / max(h, w))
        if r < 1.0:
            import cv2
            self.proxy = cv2.resize(self.base, (int(w * r), int(h * r)), interpolation=cv2.INTER_AREA)
        else:
            self.proxy = self.base
        self.edit.base = self.proxy
        if self.editing:
            self._render(final=True)
        else:
            self.canvas.set_pixmap(QPixmap.fromImage(qimage_from_rgb(arr)), reset=False)

    def _update_fav(self) -> None:
        g = self.group
        fav = g is not None and self.page.marks.is_fav(g)
        self.b_fav.setText("♥" if fav else "♡")

    def toggle_fav(self) -> None:
        g = self.group
        if g is None:
            return
        on = self.page.marks.toggle_fav([g])
        self._update_fav()
        self.page.status(_t("已收藏") if on else _t("已取消收藏"))
        self.changed.emit()

    def _toggle_text(self, on: bool) -> None:
        self.canvas.show_text = on
        self.canvas.update()

    def _back(self) -> None:
        if self.editing:
            self.cancel_edit()
            if self.editing:
                return
        self.back.emit()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self._back()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not self.editing:
            self.step(-1 if k == Qt.Key.Key_Left else 1)
        elif k == Qt.Key.Key_F and not self.editing:
            self.toggle_fav()
        elif k == Qt.Key.Key_E and not self.editing and self.b_edit.isEnabled():
            self.start_edit()
        else:
            super().keyPressEvent(e)

    def open_studio(self) -> None:
        g = self.group
        if g is not None:
            self.ctx.open_editor(g, self.window())

    # ---- 编辑 ----
    def start_edit(self) -> None:
        g = self.group
        if g is None or g.is_video:
            return
        self.editing = True
        self.edit.load(quick.QuickState())
        self.edit.base = self.proxy
        self.side.setCurrentWidget(self.edit)
        self.side.setVisible(True)
        self.canvas.nav_enabled = False
        self.canvas.set_text([], [])
        for b in (self.b_fav, self.b_text, self.b_info, self.b_edit, self.b_studio, self.b_more):
            b.setEnabled(False)
        if self.proxy is not None:
            small = self._small(self.proxy, 110)
            self.edit.set_preset_icons(small)
        self.edit._tab("adjust", 0)
        self.edit.tab_group.button(0).setChecked(True)
        self._render(final=True)

    @staticmethod
    def _small(img: np.ndarray, side: int) -> np.ndarray:
        import cv2
        h, w = img.shape[:2]
        r = side / max(h, w)
        return cv2.resize(img, (max(1, int(w * r)), max(1, int(h * r))), interpolation=cv2.INTER_AREA)

    def _end_edit(self) -> None:
        self.editing = False
        self.canvas.crop = None
        self.canvas.nav_enabled = True
        self.side.setCurrentWidget(self.info)
        self.side.setVisible(self.b_info.isChecked())
        for b in (self.b_fav, self.b_info, self.b_edit, self.b_studio, self.b_more):
            b.setEnabled(True)
        self._show_current()

    def cancel_edit(self) -> None:
        if not self.edit.state.is_identity():
            from ..dialogs import ask
            if not ask(self, _t("放弃修改？"), _t("这张照片的编辑还没有存储，确定要放弃吗？"), ok=_t("放弃"), danger=True):
                return
        self._end_edit()

    def _schedule(self) -> None:
        self._timer.start()
        self._final.start()

    def _scale(self, img: np.ndarray) -> float:
        g = self.group
        m = (g.meta or {}) if g else {}
        full = max(m.get("width") or 0, m.get("height") or 0)
        return min(1.0, max(img.shape[:2]) / full) if full else 1.0

    def _render(self, final: bool) -> None:
        if not self.editing or self.proxy is None:
            return
        img = self.base if final else self.proxy
        st = self.edit.state
        scale = self._scale(img)
        if self.edit.tab == "crop":
            o = quick.oriented(img, st)
            out = quick.apply(o, st, scale, geometry=False)
            self.canvas.crop = tuple(st.crop)
            h, w = o.shape[:2]
            self.canvas.aspect = self.edit.aspect_ratio(w, h)
        else:
            self.canvas.crop = None
            out = quick.apply(img, st, scale)
        self.canvas.set_pixmap(QPixmap.fromImage(qimage_from_rgb(out)), reset=True)

    def _crop_from_canvas(self, box: tuple) -> None:
        self.edit.state.crop = tuple(float(v) for v in box)
        self.edit.summary.setText(quick.describe(self.edit.state))

    def _apply_aspect(self) -> None:
        if self.proxy is None:
            return
        o = quick.oriented(self.proxy, self.edit.state)
        h, w = o.shape[:2]
        r = self.edit.aspect_ratio(w, h)
        self.edit.state.crop = (0.0, 0.0, 1.0, 1.0) if r is None else centered_aspect(r, w, h)
        self._render(final=True)

    def save_edit(self) -> None:
        g = self.group
        st = self.edit.state.copy()
        if g is None:
            return
        if st.is_identity():
            info(self, _t("没有修改"), _t("还没有做任何调整。"), "NOTICE")
            return
        lib = self.ctx.library

        def job(prog, cancel):
            from ...edit import io as eio
            prog((0, 3, _t("读取原片…")))
            doc = eio.open_group(g, lib.exiftool_path)
            src = quick.to_float(doc.layers[0].pixels)
            prog((1, 3, _t("按原尺寸计算…")))
            out = quick.apply(src, st, 1.0)
            path = unique_path(doc.source.path or g.primary, _t("_编辑"))
            prog((2, 3, _t("存储…")))
            eio.export_image(doc, path, quality=95, rgba=quick.to_rgba8(out))
            return path

        path, tb = run_with_progress(self.ctx, self, _t("正在存储"), "SAVE", job)
        if tb or not path:
            info(self, _t("存储失败"), f"<pre style='font-size:8pt'>{(tb or '')[-1200:]}</pre>", "ERROR")
            return
        self.page.status(_t("已存储为新照片：{0}（原片未修改）", os.path.basename(path)))
        self._end_edit()
        self.page.pending_open = os.path.normcase(os.path.abspath(path))
        self.ctx.rescan_requested.emit()


def unique_path(src: str, suffix: str, ext: str = ".jpg") -> str:
    folder = os.path.dirname(src) or os.path.expanduser("~")
    stem = os.path.splitext(os.path.basename(src))[0]
    n = 0
    while True:
        p = os.path.join(folder, f"{stem}{suffix}{'' if n == 0 else f' ({n})'}{ext}")
        if not os.path.exists(p):
            return p
        n += 1
