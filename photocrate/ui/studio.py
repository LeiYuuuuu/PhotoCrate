"""「修图」模式：专业修图的工作台。修图窗口（图层、蒙版、选区、调整图层、修复画笔…）作为标签页嵌在主窗口里，
可以同时开几张；第一个标签「开始」放打开图片 / 工程、合并曝光和最近的照片。

修图窗口本身不变（photocrate/ui/editor）：open_editor 建好窗口后交给这里 adopt()，变成一个标签页。
嵌入后把窗口里所有快捷键限定在这个标签页内（Qt.WidgetWithChildrenShortcut），所以 B / V / Ctrl+Z 这类单键
不会在「相册」模式或别的标签页里误触发。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QTabBar, QVBoxLayout,
                               QWidget)

from . import theme
from .theme import font
from .widgets.deco import TacButton, caption
from ..i18n import t as _t

RECENT_KEY = "studio_recent"
RECENT_MAX = 12


class StudioHome(QWidget):
    def __init__(self, studio, parent=None):
        super().__init__(parent)
        self.studio = studio
        self.ctx = studio.ctx
        p = theme.current()
        self.setObjectName("studioHome")
        self.setStyleSheet(f"#studioHome {{ background: {p.bg}; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(60, 46, 60, 40)
        v.setSpacing(14)
        t = QLabel(_t("修图工作台"))
        t.setFont(font("cn", 24, 800))
        t.setStyleSheet("font-size: 24pt; font-weight: 800;")     # 全局样式表会盖掉 setFont 的字号
        v.addWidget(t)
        v.addWidget(caption(_t("类似 Photoshop 的专业修图：图层与混合模式、蒙版、选区与抠图、调整图层、修复画笔 / 仿制图章 / 内容识别填充、"
                               "文字、滤镜、合并曝光（HDR）。原片只读：导出写新文件，或存成 .pcedit 工程以后接着改。"), "dim", 10))
        r = QHBoxLayout()
        r.setSpacing(10)
        b1 = TacButton(_t("打开图片…"), "primary", height=40)
        b1.clicked.connect(self.open_image)
        b2 = TacButton(_t("打开工程…"), "secondary", height=40)
        b2.clicked.connect(lambda: self.open_image(project=True))
        b3 = TacButton(_t("合并曝光（HDR）…"), "secondary", height=40)
        b3.clicked.connect(self.merge)
        b4 = TacButton(_t("从相册选一张"), "secondary", height=40)
        b4.setToolTip(_t("回到「相册」，选中照片后点「在修图中打开」"))
        b4.clicked.connect(lambda: self.studio.want_library.emit())
        for b in (b1, b2, b3, b4):
            r.addWidget(b)
        r.addStretch(1)
        v.addLayout(r)
        v.addSpacing(16)
        self.recent_title = QLabel(_t("最近修过的"))
        self.recent_title.setFont(font("cn", 12, 700))
        v.addWidget(self.recent_title)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(4)
        v.addLayout(self.recent_box)
        v.addStretch(1)
        tips = caption(_t("提示：在「相册」里选中照片按右键 →「在修图中打开」，或在单张照片页点「在修图中打开」；"
                          "每张图一个标签页，Ctrl+W 关闭当前标签。"), "faint", 9)
        v.addWidget(tips)
        self.refresh()

    def refresh(self) -> None:
        while self.recent_box.count():
            it = self.recent_box.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        items = [p for p in (self.ctx.settings.get(RECENT_KEY) or []) if os.path.exists(p)]
        self.recent_title.setVisible(bool(items))
        for p in items[:RECENT_MAX]:
            b = TacButton(os.path.basename(p) + "    " + os.path.dirname(p), "secondary", height=30)
            b.setMinimumWidth(0)
            b.setStyleSheet("text-align: left; padding-left: 12px;")
            b.setToolTip(p)
            b.clicked.connect(lambda _=False, p=p: self.studio.open_path(p))
            self.recent_box.addWidget(b)

    def open_image(self, project: bool = False) -> None:
        from ..edit import io as eio
        items = self.ctx.settings.get(RECENT_KEY) or []
        start = os.path.dirname(items[0]) if items else os.path.expanduser("~")
        flt = _t("修图工程 (*.pcedit)") if project else eio.OPEN_FILTER
        p, _ = QFileDialog.getOpenFileName(self, _t("打开"), start, flt)
        if p:
            self.studio.open_path(p)

    def merge(self) -> None:
        from ..edit import io as eio
        from .editor.merge import merge_paths
        ps, _ = QFileDialog.getOpenFileNames(self, _t("选择同一场景、不同曝光的几张照片"), os.path.expanduser("~"),
                                             eio.OPEN_FILTER)
        ps = [p for p in ps if not p.lower().endswith(eio.PROJECT_EXT)]
        if ps:
            merge_paths(self.ctx, self.window(), ps)


class StudioMode(QWidget):
    activated = Signal()          # 有修图窗口加入，主窗口应切到「修图」模式
    want_library = Signal()       # 请切回「相册」
    count_changed = Signal(int)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        p = theme.current()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.tabs = QTabBar()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setDrawBase(False)
        self.tabs.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.tabs.setStyleSheet(f"""
QTabBar {{ background: {p.slab}; }}
QTabBar::tab {{ background: {p.slab}; color: {p.slab_ink2}; padding: 5px 14px; margin: 0 2px; border: none;
    border-radius: 4px; min-width: 70px; max-width: 260px; }}
QTabBar::tab:selected {{ background: {p.slab2}; color: {p.slab_ink}; }}
QTabBar::tab:hover {{ color: {p.slab_ink}; }}
QTabBar::close-button {{ image: url("{theme.asset('close')}"); subcontrol-position: right; margin-left: 4px;
    border-radius: 3px; }}
QTabBar::close-button:hover {{ background: {p.line}; }}
""")
        self.tab_bar = bar = QWidget()
        bar.setStyleSheet(f"background: {p.slab};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.tabs, 1)
        v.addWidget(bar)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        self.home = StudioHome(self)
        self.tabs.addTab(_t("开始"))
        self.stack.addWidget(self.home)
        self.tabs.setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
        self.tabs.setTabButton(0, QTabBar.ButtonPosition.LeftSide, None)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.tabMoved.connect(self._tab_moved)
        self.editors: list[QMainWindow] = []          # 与标签 1.. 一一对应
        a = QAction(self)
        a.setShortcut("Ctrl+Tab")
        a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        a.triggered.connect(lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % self.tabs.count()))
        self.addAction(a)

    def take_tab_bar(self) -> QWidget:
        """把文档标签条交给主窗口放进顶栏（和模式切换同一行）；不调用时标签条留在修图区上方。"""
        self.layout().removeWidget(self.tab_bar)
        return self.tab_bar

    # ---- 标签页 ----
    def adopt(self, ew: QMainWindow) -> None:
        """把 open_editor 建好的修图窗口收进来，成为一个标签页并切过去。"""
        ew.setWindowFlags(Qt.WindowType.Widget)
        ew.setParent(self.stack)
        for act in ew.findChildren(QAction):
            if act.shortcutContext() == Qt.ShortcutContext.WindowShortcut:
                act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.stack.addWidget(ew)
        self.editors.append(ew)
        i = self.tabs.addTab(self._title(ew))
        self.tabs.setTabToolTip(i, ew.windowTitle())
        ew.windowTitleChanged.connect(lambda _t_, ew=ew: self._retitle(ew))
        ew.destroyed.connect(lambda *_: self._gone(ew))
        self._remember(ew)
        self.tabs.setCurrentIndex(i)
        ew.show()
        self.activated.emit()
        self.count_changed.emit(len(self.editors))
        QTimer.singleShot(0, ew.canvas.setFocus if hasattr(ew, "canvas") else ew.setFocus)

    def _title(self, ew) -> str:
        t = ew.windowTitle()
        prefix = _t("影匣 · 修图 — {0}{1}", "\x00", "").split("\x00")[0]       # 修图窗口标题的固定前缀（随界面语言）
        if prefix and t.startswith(prefix):
            t = t[len(prefix):]
        return t or _t("未命名")

    def _retitle(self, ew) -> None:
        i = self._index_of(ew) + 1
        if i > 0:
            self.tabs.setTabText(i, self._title(ew))
            self.tabs.setTabToolTip(i, ew.windowTitle())

    def _remember(self, ew) -> None:
        d = getattr(ew, "doc", None)
        src = getattr(d, "source", None)
        p = (getattr(src, "project", None) or getattr(src, "path", None)) if src is not None else None
        if not p:
            return
        items = [x for x in (self.ctx.settings.get(RECENT_KEY) or []) if os.path.normcase(x) != os.path.normcase(p)]
        self.ctx.settings.set(RECENT_KEY, [p] + items[:RECENT_MAX - 1])
        self.home.refresh()

    def _index_of(self, ew) -> int:
        return next((i for i, x in enumerate(self.editors) if x is ew), -1)

    def _gone(self, ew) -> None:
        """修图窗口关闭后被销毁（WA_DeleteOnClose）：去掉它的标签。QStackedWidget 会自己移除已销毁的子控件。"""
        i = self._index_of(ew)
        if i < 0:
            return
        self.editors.pop(i)
        self.tabs.blockSignals(True)
        self.tabs.removeTab(i + 1)
        self.tabs.blockSignals(False)
        self._tab_changed(self.tabs.currentIndex())
        self.count_changed.emit(len(self.editors))
        self.home.refresh()

    def _tab_changed(self, i: int) -> None:
        if i <= 0:
            self.stack.setCurrentWidget(self.home)
            self.home.refresh()
        elif i - 1 < len(self.editors):
            ew = self.editors[i - 1]
            self.stack.setCurrentWidget(ew)
            if hasattr(ew, "canvas"):
                ew.canvas.setFocus()

    def _tab_moved(self, frm: int, to: int) -> None:
        if frm == 0 or to == 0:                     # 「开始」固定在最前
            self.tabs.blockSignals(True)
            self.tabs.moveTab(to, frm)
            self.tabs.blockSignals(False)
            return
        ew = self.editors.pop(frm - 1)
        self.editors.insert(to - 1, ew)

    def _close_tab(self, i: int) -> None:
        if i >= 1 and i - 1 < len(self.editors):
            self.editors[i - 1].close()

    def current_editor(self):
        i = self.tabs.currentIndex()
        return self.editors[i - 1] if 1 <= i <= len(self.editors) else None

    def open_path(self, path: str) -> None:
        from .editor.window import open_editor
        open_editor(self.ctx, self, path=path)

    def close_all(self) -> bool:
        """退出程序前逐个关闭（各自会问要不要保存）。有一个取消就返回 False。"""
        for ew in list(self.editors):
            self.tabs.setCurrentIndex(self._index_of(ew) + 1)
            if not ew.close():
                return False
        return True
