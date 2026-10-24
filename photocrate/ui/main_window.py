"""主窗口：顶部机体条 + 左侧导航 + 页面区 + 底部状态条。"""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QIcon, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMainWindow, QSizePolicy, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import APP_NAME, APP_NAME_EN, APP_TAGLINE, __version__
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
from .theme import font
from .widgets.deco import Barcode, LogoMark
from ..i18n import is_en, t as _t


class TopBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(68)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(12)
        lay.addWidget(LogoMark(34))
        self.readouts: list[tuple[str, str, str]] = []
        lay.addStretch(1)
        self.bc = Barcode(seed=11, w=84, h=20)
        lay.addWidget(self.bc)

    def set_readouts(self, items: list[tuple[str, str, str]]) -> None:
        """[(标签, 值, 颜色键)]"""
        self.readouts = items
        self.update()

    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        qp.fillRect(self.rect(), QColor(p.slab))
        x = 64
        f1 = font("cn", 17, 800)
        qp.setFont(f1)
        qp.setPen(QColor(p.slab_ink))
        tw = QFontMetrics(f1).horizontalAdvance(APP_NAME)
        qp.drawText(QRectF(x, 0, tw + 4, h), Qt.AlignmentFlag.AlignVCenter, APP_NAME)
        x += tw + 12
        qp.fillRect(QRectF(x, 16, 1, h - 32), QColor(p.slab_ink2))
        x += 12
        f2 = font("display", 14, 700, 2.5)
        qp.setFont(f2)
        qp.setPen(QColor(p.accent))
        qp.drawText(QRectF(x, 9, 300, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, APP_NAME_EN)
        qp.setFont(font("mono", 7, 400, 1.6))
        qp.setPen(QColor(p.slab_ink2))
        qp.drawText(QRectF(x, 32, 400, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f"{APP_TAGLINE} // V{__version__}")
        # 右侧读数
        rx = w - 120
        fl = font("mono", 7, 400, 1.2)
        fv = font("display", 10.5, 600)
        for label, value, colkey in reversed(self.readouts):
            vw = QFontMetrics(fv).horizontalAdvance(value)
            lw = QFontMetrics(fl).horizontalAdvance(label)
            bw = max(vw, lw) + 22
            rx -= bw
            qp.setPen(QPen(QColor(p.slab2), 1))
            qp.drawLine(QPointF(rx, 14), QPointF(rx, h - 14))
            qp.setFont(fl)
            qp.setPen(QColor(p.slab_ink2))
            qp.drawText(QRectF(rx + 11, 12, bw, 14), Qt.AlignmentFlag.AlignLeft, label)
            qp.setFont(fv)
            qp.setPen(QColor(getattr(p, colkey, p.slab_ink)))
            qp.drawText(QRectF(rx + 11, 27, bw, 20), Qt.AlignmentFlag.AlignLeft, value)
        # 底部强调线
        qp.fillRect(QRectF(0, h - 2, w, 2), QColor(p.slab2))
        qp.fillRect(QRectF(0, h - 2, 180, 2), QColor(p.accent))
        qp.end()


class NavRail(QWidget):
    changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(196)
        self.items: list[tuple[str, str, str]] = []   # (编号, 中文, 英文)
        self.current = 0
        self.marker = motion.Motion(self, 0, 220)
        self._hover = -1
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(_t("页面导航"))
        self.row_h = 58
        self.top = 18
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
        # 页面多、窗口矮时压缩每一行，给底部装饰留出位置
        n = max(1, len(self.items))
        self.row_h = int(max(42, min(58, (self.height() - self.top - 64) / n)))
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
        w, h = self.width(), self.height()
        qp.fillRect(self.rect(), QColor(p.slab))
        qp.fillRect(QRectF(0, self.top + self.marker.value * self.row_h + 8, 3, self.row_h - 16), QColor(p.accent))
        for i, (idx, title, en) in enumerate(self.items):
            y = self.top + i * self.row_h
            r = QRectF(10, y + 3, w - 20, self.row_h - 6)
            active = i == self.current
            if active:
                poly = QPolygonF([QPointF(r.left(), r.top()), QPointF(r.right() - 10, r.top()),
                                  QPointF(r.right(), r.top() + 10), QPointF(r.right(), r.bottom()),
                                  QPointF(r.left(), r.bottom())])
                qp.setPen(Qt.PenStyle.NoPen)
                qp.setBrush(QColor(p.slab2))
                qp.drawPolygon(poly)
                ink, ink2 = QColor(p.slab_ink), QColor(p.accent)
            else:
                if i == self._hover:
                    qp.fillRect(r, QColor(p.slab2))
                ink, ink2 = QColor(p.slab_ink), QColor(p.slab_ink2)
            qp.setFont(font("display", 16, 700))
            qp.setPen(ink2 if not active else ink)
            qp.drawText(QRectF(r.left() + 12, r.top(), 34, r.height()), Qt.AlignmentFlag.AlignVCenter, idx)
            qp.setFont(font("cn", 11, 700))
            qp.setPen(ink)
            ty = r.top() + max(1.0, (r.height() - 37) / 2)       # 标题 22 + 英文 15，整体垂直居中
            qp.drawText(QRectF(r.left() + 52, ty, r.width() - 56, 22), Qt.AlignmentFlag.AlignVCenter, title)
            qp.setFont(font("mono", 7, 400, 0.5))
            qp.setPen(ink2)
            qp.drawText(QRectF(r.left() + 52, ty + 20, r.width() - 56, 15), Qt.AlignmentFlag.AlignVCenter, en.upper())
            if active:
                qp.fillRect(QRectF(r.right() - 5, r.top() + 12, 2, r.height() - 24), QColor(p.accent))
            if self.hasFocus() and active:
                qp.setPen(QPen(QColor(p.accent2), 1))
                qp.setBrush(Qt.BrushStyle.NoBrush)
                qp.drawRect(r.adjusted(1, 1, -1, -1))
        # 只在导航下方有空间时绘制页脚，不与长菜单重叠。
        if self.top + len(self.items) * self.row_h < h - 48:
            qp.setFont(font("mono", 7, 400, 0.8))
            qp.setPen(QColor(p.slab_ink2))
            qp.drawText(QRectF(22, h - 40, w - 40, 16), Qt.AlignmentFlag.AlignLeft, "LOCAL / ARCHIVE")
            qp.fillRect(QRectF(22, h - 16, 26, 2), QColor(p.accent))
            qp.fillRect(QRectF(52, h - 16, w - 74, 2), QColor(p.slab2))
        qp.end()


class StatusStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self.msg = "READY"
        self.right = ""

    def set_message(self, msg: str) -> None:
        self.msg = msg
        self.update()

    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self)
        w, h = self.width(), self.height()
        qp.fillRect(self.rect(), QColor(p.slab))
        qp.fillRect(QRectF(0, 0, 196, h), QColor(p.slab2))
        qp.setFont(font("mono", 7.5, 400, 1.2))
        qp.setPen(QColor(p.accent))
        qp.drawText(QRectF(14, 0, 180, h), Qt.AlignmentFlag.AlignVCenter, f"SYS // {datetime.now():%H:%M}")
        qp.setFont(font("cn", 8.5))
        qp.setPen(QColor(p.slab_ink))
        qp.drawText(QRectF(210, 0, w - 420, h), Qt.AlignmentFlag.AlignVCenter, self.msg)
        qp.setFont(font("mono", 7.5))
        qp.setPen(QColor(p.slab_ink2))
        qp.drawText(QRectF(w - 400, 0, 386, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.right)
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
        mid = QHBoxLayout()
        mid.setSpacing(0)
        self.nav = NavRail()
        mid.addWidget(self.nav)
        right = QVBoxLayout()
        right.setSpacing(0)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        mid.addLayout(right, 1)
        root.addLayout(mid, 1)
        self.status = StatusStrip()
        root.addWidget(self.status)
        self.setCentralWidget(central)

        self.pages: list[Page | QWidget] = []
        self.page_ids: list[str] = []
        for cls in (SourcePage, TimePlacePage, PartitionPage, OverviewPage, CullingPage, TravelPage, AiPage,
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
        # 缩略图上打分 / 双击看大图（所有页面的缩略图网格共用）
        from .widgets.thumbgrid import HOOKS
        HOOKS.rate = ctx.rate
        HOOKS.open = lambda groups, i, w: ctx.open_viewer(groups, i, self)
        HOOKS.edit = lambda g: ctx.open_editor(g, self)
        ctx.rescan_requested.connect(self._rescan)
        ctx.ratings_changed.connect(HOOKS.repaint.emit)
        ctx.status.connect(self.status.set_message)
        ctx.library_changed.connect(self._update_readouts)
        ctx.selection_changed.connect(self._update_readouts)
        ctx.files_changed.connect(self._update_readouts)
        ctx.settings_changed.connect(lambda k: k == "exiftool_path" and self._update_readouts())
        ctx.theme_changed.connect(self._apply_theme)
        ctx.navigate.connect(lambda pid: pid in self.page_ids and self.go(self.page_ids.index(pid)))
        self._update_readouts()
        self._clock = QTimer(self)
        self._clock.timeout.connect(self.status.update)
        self._clock.start(30_000)
        QTimer.singleShot(400, self._check_recovery)
        self._last_shot = 0.0
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
        et = f"V{lib.exiftool_ver}" if lib.exiftool_path else "OFFLINE"
        self.top.set_readouts([
            ("EXIFTOOL", et, "ok" if lib.exiftool_path else "danger"),
            ("LIBRARY", f"{n:,}", "slab_ink"),
            ("SELECTED", f"{len(lib.selected):,}", "accent"),
        ])
        self.status.right = f"DATA {os.path.dirname(str(self.ctx.journal.path))}"
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
        QApplication.instance().removeEventFilter(self)
        self._save_window()
        self.ctx.shutdown()
        self.ctx.library.close()
        self.ctx.journal.close()
        super().closeEvent(e)
