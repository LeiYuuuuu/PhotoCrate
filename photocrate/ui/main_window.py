"""主窗口：顶栏（含「相册 / 修图」模式切换）+ 左侧导航 + 页面区 + 底部状态条。

两个主界面：
- 相册（存储模式）：照片（类似 iOS 相册的图库 / 相簿 / 搜索）+ 整理工具页；
- 修图（专业模式）：ui/studio.py，修图窗口作为标签页嵌在这里。切到修图时顶栏变矮、修图的文档标签
  并进顶栏，底部状态条隐藏（修图窗口有自己的状态栏），画布比以前多出约 90 像素高。
"""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QIcon, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, APP_NAME_EN, __version__
from ..config import find_resource, screenshots_dir
from ..plugins import PAGES
from . import theme, motion
from .context import AppContext
from .dialogs import choose, info, run_with_progress
from .pages.ai_page import AiPage
from .pages.blur_page import BlurPage
from .pages.culling_page import CullingPage
from .pages.base import Page
from .pages.history_page import HistoryPage
from .pages.overview_page import OverviewPage
from .pages.partition_page import PartitionPage
from .pages.people_page import PeoplePage
from .pages.settings_page import SettingsPage
from .pages.source_page import SourcePage
from .pages.timeplace_page import TimePlacePage
from .pages.travel_page import TravelPage
from .photos import PhotosPage
from .studio import StudioMode
from .theme import font
from .widgets.deco import LogoMark
from ..i18n import is_en, t as _t


class ModeSwitch(QWidget):
    """顶栏里的两个主界面切换：相册（存储）/ 修图（专业）。"""
    changed = Signal(int)
    MODES = ["Photos" if is_en() else "相册", "Studio" if is_en() else "修图"]  # “相册”在别处是 Album

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current = 0
        self.badge = 0                      # 修图里打开着几张
        self._hover = -1
        self.marker = motion.Motion(self, 0, 200)
        self.setFixedSize(180, 30)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(_t("主界面切换"))
        self.setToolTip(_t("相册：浏览、搜索、整理照片（Alt+1）") + "\n" + _t("修图：图层、蒙版、修复等专业修图（Alt+2）"))

    def set_current(self, i: int) -> None:
        self.current = i
        self.marker.to(i)
        self.update()

    def set_badge(self, n: int) -> None:
        self.badge = n
        self.update()

    def mousePressEvent(self, e):
        i = 0 if e.position().x() < self.width() / 2 else 1
        if i != self.current:
            self.changed.emit(i)

    def mouseMoveEvent(self, e):
        i = 0 if e.position().x() < self.width() / 2 else 1
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        half = w / 2
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(p.slab2))
        qp.drawRoundedRect(QRectF(0, 0, w, h), 6, 6)
        x = self.marker.value * half
        qp.setBrush(QColor(p.accent))
        qp.drawRoundedRect(QRectF(x + 3, 3, half - 6, h - 6), 4, 4)
        for i, name in enumerate(self.MODES):
            r = QRectF(i * half, 0, half, h)
            active = i == self.current
            ink = QColor(p.accent_ink) if active else QColor(p.slab_ink if i == self._hover else p.slab_ink2)
            qp.setFont(font("cn", 10.5, 700))
            qp.setPen(ink)
            qp.drawText(r, Qt.AlignmentFlag.AlignCenter, name)
            if i == 1 and self.badge:
                b = QRectF(r.right() - 20, 3, 16, 14)
                qp.setPen(Qt.PenStyle.NoPen)
                qp.setBrush(QColor(p.accent2))
                qp.drawRoundedRect(b, 7, 7)
                qp.setPen(QColor("#FFFFFF"))
                qp.setFont(font("mono", 7, 700))
                qp.drawText(b, Qt.AlignmentFlag.AlignCenter, str(self.badge))
        qp.end()


class TopBar(QWidget):
    """标志 + 软件名 + 模式切换 + 插槽（修图模式下放文档标签）+ 右侧读数（照片数 / 已选 / ExifTool 缺失提示）。"""
    HEIGHT, COMPACT = 48, 38

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(10)
        self.logo = LogoMark(24)
        lay.addWidget(self.logo)
        name = QLabel("PhotoCrate" if is_en() else APP_NAME)
        name.setStyleSheet(f"color: {theme.current().slab_ink}; font-size: 12.5pt; font-weight: 800;")
        name.setToolTip(f"{APP_NAME} {APP_NAME_EN.title()} {__version__}")
        name.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        lay.addWidget(name)
        lay.addSpacing(14)
        self.mode = ModeSwitch()
        lay.addWidget(self.mode)
        lay.addSpacing(6)
        self.slot = QHBoxLayout()               # 修图模式：文档标签（add_to_slot 放进来，占满中间）
        self.slot.setContentsMargins(0, 0, 0, 0)
        self.slot.addStretch(0)                 # 插槽里没有东西时把读数推到最右边
        lay.addLayout(self.slot, 1)
        self.readouts: list[tuple[str, str, str]] = []
        self.info = QLabel("")
        self.info.setStyleSheet("font-size: 9pt;")
        self.info.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.info)

    def add_to_slot(self, w: QWidget) -> None:
        self.slot.insertWidget(0, w, 1)

    def set_compact(self, on: bool) -> None:
        self.setFixedHeight(self.COMPACT if on else self.HEIGHT)
        self.info.setVisible(not on)

    def set_readouts(self, items: list[tuple[str, str, str]]) -> None:
        """[(标签, 值, 颜色键)]"""
        p = theme.current()
        self.readouts = items
        self.info.setText("&nbsp;&nbsp;&nbsp;&nbsp;".join(
            f"<span style='color:{p.slab_ink2}'>{label}</span>&nbsp;"
            f"<span style='color:{getattr(p, colkey, p.slab_ink)}; font-weight:600'>{value}</span>"
            for label, value, colkey in items))

    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor(p.slab))
        qp.fillRect(QRectF(0, self.height() - 1, self.width(), 1), QColor(p.slab2))
        qp.end()


class NavRail(QWidget):
    changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(184)
        self.items: list[tuple[str, str, str]] = []   # (编号, 中文, 英文)；只显示中文（英文界面下是译文）
        self.current = 0
        self.marker = motion.Motion(self, 0, 220)
        self._hover = -1
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(_t("页面导航"))
        self.row_h = 42
        self.top = 12
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def add(self, idx: str, title: str, en: str) -> None:
        self.items.append((idx, title, en))
        self.update()

    def set_current(self, i: int) -> None:
        self.current = i
        self.marker.to(i)
        self.update()

    def resizeEvent(self, e):
        # 页面多、窗口矮时压缩每一行
        n = max(1, len(self.items))
        self.row_h = int(max(32, min(42, (self.height() - self.top - 12) / n)))
        super().resizeEvent(e)

    def _at(self, y: float) -> int:
        i = int((y - self.top) // self.row_h)
        return i if 0 <= i < len(self.items) else -1

    def mouseMoveEvent(self, e):
        i = self._at(e.position().y())
        if i != self._hover:
            self._hover = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._at(e.position().y())
        if i >= 0 and i != self.current:
            self.changed.emit(i)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Home, Qt.Key.Key_End):
            target = self.current + (1 if e.key() == Qt.Key.Key_Down else -1)
            if e.key() == Qt.Key.Key_Home:
                target = 0
            elif e.key() == Qt.Key.Key_End:
                target = len(self.items) - 1
            self.changed.emit(max(0, min(len(self.items) - 1, target)))
            e.accept()
            return
        super().keyPressEvent(e)

    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        qp.fillRect(self.rect(), QColor(p.slab))
        for i, (idx, title, en) in enumerate(self.items):
            y = self.top + i * self.row_h
            r = QRectF(8, y + 2, w - 16, self.row_h - 4)
            active = i == self.current
            qp.setPen(Qt.PenStyle.NoPen)
            if active or i == self._hover:
                qp.setBrush(QColor(p.slab2))
                qp.drawRoundedRect(r, 5, 5)
            qp.setFont(font("cn", 10.5, 700 if active else 500))
            qp.setPen(QColor(p.slab_ink if active or i == self._hover else p.slab_ink2))
            qp.drawText(r.adjusted(16, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, title)
            if self.hasFocus() and active:
                qp.setPen(QPen(QColor(p.accent2), 1))
                qp.setBrush(Qt.BrushStyle.NoBrush)
                qp.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        # 当前页左侧的强调色短条（跟着切换动画滑动）
        y = self.top + self.marker.value * self.row_h
        qp.fillRect(QRectF(8, y + self.row_h / 2 - 8, 3, 16), QColor(p.accent))
        qp.end()


class StatusStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self.msg = ""
        self.right = ""

    def set_message(self, msg: str) -> None:
        self.msg = msg
        self.update()

    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self)
        w, h = self.width(), self.height()
        qp.fillRect(self.rect(), QColor(p.slab))
        qp.setFont(font("cn", 8.5))
        qp.setPen(QColor(p.slab_ink))
        qp.drawText(QRectF(14, 0, w - 440, h), Qt.AlignmentFlag.AlignVCenter, self.msg)
        qp.setPen(QColor(p.slab_ink2))
        qp.drawText(QRectF(w - 420, 0, 406, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    QFontMetrics(qp.font()).elidedText(self.right, Qt.TextElideMode.ElideMiddle, 406))
        qp.end()


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        motion.set_enabled(bool(ctx.settings.get("ui_motion")))
        self.setWindowTitle("PhotoCrate" if is_en() else f"{APP_NAME} {APP_NAME_EN.title()}")
        icon = find_resource("assets", "app.png")
        if icon:
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1480, 920)
        self.setMinimumSize(1180, 720)
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.top = TopBar()
        root.addWidget(self.top)
        self.modes = QStackedWidget()           # 0 相册（导航 + 页面）/ 1 修图
        library = QWidget()
        mid = QHBoxLayout(library)
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        self.nav = NavRail()
        mid.addWidget(self.nav)
        right = QVBoxLayout()
        right.setSpacing(0)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        mid.addLayout(right, 1)
        self.modes.addWidget(library)
        self.studio = StudioMode(ctx)
        ctx.studio = self.studio                # open_editor 看到它就把修图窗口嵌进来
        self.modes.addWidget(self.studio)
        self.studio_tabs = self.studio.take_tab_bar()       # 修图的文档标签放进顶栏，省一行
        self.top.add_to_slot(self.studio_tabs)
        self.studio_tabs.hide()
        root.addWidget(self.modes, 1)
        self.status = StatusStrip()
        root.addWidget(self.status)
        self.setCentralWidget(central)

        self.pages: list[Page | QWidget] = []
        self.page_ids: list[str] = []
        for cls in (PhotosPage, SourcePage, TimePlacePage, PartitionPage, OverviewPage, CullingPage, TravelPage, AiPage,
                    PeoplePage, BlurPage, HistoryPage, SettingsPage):
            self._add_page(cls(ctx), cls.page_id, cls.index, cls.title, cls.en.split("//")[0].strip())
        # 插件页面
        for i, spec in enumerate(PAGES):
            try:
                w = spec.factory(ctx)
                self._add_page(w, f"plugin_{i}", f"{12 + i:02d}", spec.title, spec.en)
            except Exception as e:
                ctx.status.emit(_t('插件页面加载失败：{0}：{1}', spec.title, e))
        self.nav.changed.connect(self.go)
        self.top.mode.changed.connect(self.set_mode)
        self.studio.activated.connect(lambda: self.set_mode(1))
        self.studio.want_library.connect(lambda: (self.set_mode(0), self.go(0)))
        self.studio.count_changed.connect(self.top.mode.set_badge)
        QShortcut(QKeySequence("Alt+1"), self, activated=lambda: self.set_mode(0))
        QShortcut(QKeySequence("Alt+2"), self, activated=lambda: self.set_mode(1))
        # 缩略图上打分 / 双击看大图（所有页面的缩略图网格共用）
        from .widgets.thumbgrid import HOOKS
        HOOKS.rate = ctx.rate
        HOOKS.open = lambda groups, i, w: ctx.open_viewer(groups, i, self)
        HOOKS.edit = lambda g: ctx.open_editor(g, self)
        HOOKS.merge = lambda gs: ctx.merge_exposures(gs, self)
        ctx.rescan_requested.connect(self._rescan)
        ctx.ratings_changed.connect(HOOKS.repaint.emit)
        ctx.status.connect(self.status.set_message)
        ctx.library_changed.connect(self._update_readouts)
        ctx.selection_changed.connect(self._update_readouts)
        ctx.files_changed.connect(self._update_readouts)
        ctx.settings_changed.connect(lambda k: k == "exiftool_path" and self._update_readouts())
        ctx.theme_changed.connect(self._apply_theme)
        ctx.navigate.connect(lambda pid: pid in self.page_ids and (self.set_mode(0), self.go(self.page_ids.index(pid))))
        self._update_readouts()
        QTimer.singleShot(400, self._check_recovery)
        QTimer.singleShot(600, ctx.autosync.restart)     # 开着「自动同步」时开始监视（顺带按文件夹现状载入照片库）
        self._last_shot = 0.0
        from .assistant import install as install_assistant     # 顶栏「助手」+ Ctrl+K
        install_assistant(self)
        self._restore_window()
        QApplication.instance().installEventFilter(self)

    # ---- 窗口位置：记住上次的显示器 / 大小 ---------------------------------
    def _pick_screen(self, hint: str):
        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
        h = (hint or "").strip().lower()
        if not h or not screens:
            return None
        if h == "primary":
            return primary
        if h == "secondary":
            return next((sc for sc in screens if sc is not primary), None)
        if h.startswith("#") and h[1:].isdigit():
            i = int(h[1:]) - 1
            return screens[i] if 0 <= i < len(screens) else None
        for sc in screens:
            text = " ".join(x for x in (sc.name(), sc.manufacturer(), sc.model(), sc.serialNumber()) if x).lower()
            if h in text:
                return sc
        return None

    def _restore_window(self) -> None:
        state = self.ctx.settings.get("window_state") or {}
        geo = state.get("geometry")
        if geo:
            try:
                from PySide6.QtCore import QByteArray
                self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            except Exception:
                pass
        sc = self._pick_screen(self.ctx.settings.get("window_screen") or "")
        if sc is not None:
            ag = sc.availableGeometry()
            self.setGeometry(ag.adjusted(40, 40, -40, -40))
            self.move(ag.topLeft() + (ag.bottomRight() - ag.topLeft()) / 2 - self.rect().center())
            state["maximized"] = True
        if state.get("maximized"):
            QTimer.singleShot(0, self.showMaximized)

    def _save_window(self) -> None:
        try:
            geo = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self.ctx.settings.set("window_state", {"geometry": geo, "maximized": self.isMaximized()}, save=False)
            # 指定显示器只在下一次启动生效一次，之后跟随用户自己的摆放
            self.ctx.settings.set("window_screen", "")
        except Exception:
            pass

    # ---- F12 截图 --------------------------------------------------------
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_F12:
            import time
            now = time.monotonic()
            if now - self._last_shot > 0.6:     # 按键事件会沿父控件传递多次，防重复
                self._last_shot = now
                QTimer.singleShot(80, self.save_screenshot)
            return True
        return super().eventFilter(obj, ev)

    def save_screenshot(self) -> str | None:
        """把主窗口区域（连同上面弹出的对话框）保存为 PNG。返回文件路径。"""
        d = screenshots_dir()
        idx = self.stack.currentIndex()
        pid = self.page_ids[idx] if 0 <= idx < len(self.page_ids) else "page"
        if self.modes.currentIndex() == 1:
            pid = "studio"
        path = os.path.join(str(d), f"PhotoCrate_{datetime.now():%Y%m%d_%H%M%S}_{pid}.png")
        pm = None
        scr = self.screen() or QGuiApplication.primaryScreen()
        if scr is not None:
            g = self.frameGeometry()
            pm = scr.grabWindow(0, g.x() - scr.geometry().x(), g.y() - scr.geometry().y(), g.width(), g.height())
        if pm is None or pm.isNull():
            pm = self.grab()
        if not pm.save(path, "PNG"):
            self.ctx.status.emit(_t("截图保存失败"))
            return None
        self.ctx.status.emit(_t('截图已保存：{0}', path))
        return path

    def _add_page(self, w, pid, idx, title, en):
        self.pages.append(w)
        self.page_ids.append(pid)
        self.stack.addWidget(w)
        self.nav.add(idx, title, en)

    def _rescan(self) -> None:
        src = self.page("source")
        if src is not None and not self.ctx.busy:
            QTimer.singleShot(0, src.start_scan)

    def page(self, pid: str):
        return self.pages[self.page_ids.index(pid)] if pid in self.page_ids else None

    def mode(self) -> int:
        return self.modes.currentIndex()

    def set_mode(self, i: int) -> None:
        """切换主界面：0 相册 / 1 修图。"""
        if i == self.modes.currentIndex():
            self.top.mode.set_current(i)
            return
        snapshot = self.modes.grab() if motion.enabled() and self.isVisible() else None
        self.modes.setCurrentIndex(i)
        self.top.mode.set_current(i)
        self.top.set_compact(i == 1)
        self.studio_tabs.setVisible(i == 1)
        self.status.setVisible(i == 0)
        if i == 0:
            w = self.stack.currentWidget()
            if hasattr(w, "on_show"):
                w.on_show()
        else:
            self.studio.home.refresh()
        if snapshot is not None:
            motion.PageVeil(self.modes, snapshot)

    def go(self, i: int) -> None:
        if not 0 <= i < len(self.pages):
            return
        for veil in self.stack.findChildren(motion.PageVeil):
            veil.hide()
        snapshot = self.stack.grab() if motion.enabled() and self.isVisible() and i != self.stack.currentIndex() else None
        self.nav.set_current(i)
        self.stack.setCurrentIndex(i)
        w = self.pages[i]
        if hasattr(w, "on_show"):
            w.on_show()
        if snapshot is not None:
            motion.PageVeil(self.stack, snapshot)

    def _update_readouts(self):
        lib = self.ctx.library
        n = sum(1 for g in lib.groups if g.files)
        items = [] if lib.exiftool_path else [("ExifTool", _t("未找到"), "danger")]
        items += [(_t("照片"), f"{n:,}", "slab_ink"), (_t("已选"), f"{len(lib.selected):,}", "accent")]
        self.top.set_readouts(items)
        self.top.info.setToolTip(f"ExifTool {lib.exiftool_ver}" if lib.exiftool_path else "")
        self.status.right = _t("数据：{0}", os.path.dirname(str(self.ctx.journal.path)))
        self.status.update()

    restart_requested = False

    def _apply_theme(self):
        from .dialogs import ask
        if ask(self, _t("切换主题"), _t("主题已保存，重启软件后生效。现在重启吗？<br>（扫描结果有缓存，重启后再次扫描很快）"),
               ok=_t("立即重启"), cancel=_t("稍后"), en="THEME"):
            self.restart_requested = True
            self.close()

    def _check_recovery(self):
        inc = self.ctx.journal.incomplete()
        for b in inc:
            choice = choose(self, _t("发现未完成的操作"),
                            _t('批次 #{0}（{1}，{2} 个文件）上次没有正常结束，可能是程序意外关闭。<br><br>· <b>继续完成</b>：把剩下的文件按原计划处理完<br>· <b>全部回滚</b>：把这一批已处理的文件移回原处<br>· <b>稍后</b>：暂不处理（可在「记录」页查看）', b['id'], (b['created'] or '').replace('T', ' '), b['total']),
                            [("later", _t("稍后"), "secondary"), ("rollback", _t("全部回滚"), "danger"),
                             ("resume", _t("继续完成"), "primary")], en="RECOVERY")
            if choice in ("resume", "rollback"):
                mover = self.ctx.mover
                bid = b["id"]
                fn = mover.resume if choice == "resume" else mover.rollback

                def job(prog, cancel, fn=fn, bid=bid):
                    return fn(bid, lambda d, t, c: prog((d, t, c)), cancel)

                res, tb = run_with_progress(self.ctx, self, _t("正在恢复"), "RECOVERY", job)
                self.ctx.files_changed.emit()
                if tb or res is None:
                    info(self, _t("恢复出错"), f"<pre style='font-size:8pt'>{(tb or '')[-1500:]}</pre>", "ERROR")
                else:
                    info(self, _t("恢复完成"), _t('已处理 {0} 个文件。', res.done_files)
                         + ("<br>" + "<br>".join(res.errors[:8]) if res.errors else ""))

    def closeEvent(self, e):
        if self.ctx.busy:
            from .dialogs import ask
            if not ask(self, _t("任务进行中"), _t("还有任务在运行，确定要退出吗？移动操作会在当前一组完成后停止，下次启动可继续或回滚。"),
                       ok=_t("退出"), danger=True):
                e.ignore()
                return
        if not self.studio.close_all():          # 修图标签各自问要不要保存；有一个取消就不退出
            self.set_mode(1)
            e.ignore()
            return
        QApplication.instance().removeEventFilter(self)
        self._save_window()
        self.ctx.shutdown()
        self.ctx.library.close()
        self.ctx.journal.close()
        super().closeEvent(e)
