"""自动同步（界面一侧）：开着时监视「资源接入」里的文件夹，照片新增 / 删除 / 改动后在后台增量更新照片库。

- 监视在 core/watch.FolderWatcher 的线程里做，变化经信号回到界面线程，再交给后台任务 Library.sync。
- 有任务在跑（扫描、移动、导入…）或开着对话框时先不同步，结束后按最新的文件夹状态补一次——
  本软件自己搬动 / 删除的文件照片库早已知道，补同步时什么都不会变。
- 同步不打断浏览：没变的照片编号不变，打开着的照片、选择都还在；新照片先显示，AI 场景随后补上。
"""
from __future__ import annotations

import copy
import os
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QFileDialog

from ..core.watch import Change, FolderWatcher
from ..i18n import t as _t

SETTINGS_KEYS = ("auto_sync", "source_dirs", "recursive", "include_videos", "pair_live_photo", "pair_across_folders")


def covered(path: str, dirs: list[str], recursive: bool = True) -> bool:
    """path 已经在某个数据源里（同一个文件夹，或开着“包含子文件夹”时在它下面）。"""
    p = os.path.normcase(os.path.normpath(path))
    for d in dirs:
        d = os.path.normcase(os.path.normpath(d))
        if p == d or (recursive and p.startswith(d.rstrip("\\/") + os.sep)):
            return True
    return False


class AutoSync(QObject):
    changed = Signal()                       # 状态变了（开关、正在同步、上次结果），界面据此刷新文字
    _incoming = Signal(object, object)       # (监视器, Change)：监视线程 → 界面线程

    RETRY_MS = 1200                          # 忙的时候隔多久再看

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.watcher: FolderWatcher | None = None
        self.worker = None
        self.state = "off"                   # off / empty / starting / watching / syncing / error
        self.last_report = None
        self.last_time: float | None = None
        self.error = ""
        self._latest: Change | None = None   # 还没同步的最新快照
        self._dirty = False                  # 忙的时候来过变化：空下来后按最新状态补同步
        self._progress_at = 0.0
        self._restart_pending = False
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(self.RETRY_MS)
        self._retry.timeout.connect(self._kick)
        self._incoming.connect(self._on_change)
        ctx.settings_changed.connect(self._on_setting)

    # ---- 设置 ------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.ctx.settings.get("auto_sync"))

    def roots(self) -> list[str]:
        return [os.path.normpath(d) for d in self.ctx.settings.get("source_dirs") or [] if d]

    def set_enabled(self, on: bool) -> None:
        if bool(on) != self.enabled:
            self.ctx.set_setting("auto_sync", bool(on))

    def add_folder(self, parent=None) -> str | None:
        """选一个文件夹加入数据源并开启自动同步（「相册」里的入口）。返回选中的文件夹。"""
        dirs = self.roots()
        start = dirs[-1] if dirs else os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(parent, _t("选择要自动同步的照片文件夹"), start)
        if not d:
            return None
        d = os.path.normpath(d)
        new = not covered(d, dirs, bool(self.ctx.settings.get("recursive", True)))
        if new:
            dirs.append(d)
            self.ctx.settings.set("source_dirs", dirs)
        if not self.enabled:
            self.ctx.settings.set("auto_sync", True)
            self.ctx.settings_changed.emit("auto_sync")
        if new:
            self.ctx.settings_changed.emit("source_dirs")
        elif self.watcher is not None:
            self.watcher.poke(full=True)
        self.ctx.status.emit(_t("自动同步：{0}", d))
        return d

    def _on_setting(self, key: str) -> None:
        # 同一轮事件里可能连着改几项（先开关、再加文件夹），合成一次重启
        if key in SETTINGS_KEYS and not self._restart_pending:
            self._restart_pending = True
            QTimer.singleShot(0, self._restart_once)

    def _restart_once(self) -> None:
        self._restart_pending = False
        self.restart()

    # ---- 监视 ------------------------------------------------------------
    def restart(self) -> None:
        """按当前设置重新开始监视；开始时会按文件夹现状同步一次（照片库已经是最新的就什么都不变）。"""
        self._stop_watcher()
        self._latest = None
        self._dirty = False
        self.error = ""
        if not self.enabled:
            self._set_state("off")
            return
        roots = self.roots()
        if not roots:
            self._set_state("empty")
            return
        w = FolderWatcher(roots, bool(self.ctx.settings.get("recursive", True)))
        w.on_change = lambda ch, w=w: self._incoming.emit(w, ch)
        self.watcher = w
        self._set_state("starting")
        w.start()

    def check_now(self) -> None:
        """立即按文件夹现状同步一次。"""
        if self.watcher is not None:
            self.watcher.poke(full=True)
        elif self.enabled:
            self.restart()

    def cancel(self) -> None:
        """要做全量扫描：停掉正在进行的同步（扫描完按最新状态补一次）。"""
        if self.worker is not None:
            self.worker.cancel()
            self._dirty = True

    def shutdown(self) -> None:
        self._retry.stop()
        self._stop_watcher()
        if self.worker is not None:
            self.worker.cancel()

    def _stop_watcher(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None

    def _on_change(self, w, ch: Change) -> None:
        if w is not self.watcher:            # 已经换了监视器（改了文件夹 / 关了）
            return
        if self.state == "starting":
            self._set_state("watching")
        self._latest = ch
        self._kick()

    # ---- 同步 ------------------------------------------------------------
    def busy(self) -> bool:
        return bool(self.ctx.busy) or QApplication.activeModalWidget() is not None

    def _kick(self) -> None:
        if self.worker is not None or (self._latest is None and not self._dirty):
            return
        if self.busy():
            self._dirty = True
            self._retry.start()
            return
        if self._dirty:
            # 忙的那段时间里拿到的快照可能早于本软件自己的移动 / 改名：丢掉，让监视器按现状再给一份
            self._dirty = False
            self._latest = None
            if self.watcher is not None:
                self.watcher.poke(full=True)
            return
        ch, self._latest = self._latest, None
        lib = self.ctx.library
        roots = self.roots()
        self._set_state("syncing")
        # Progress 是同一个对象反复改写，跨线程排队送到界面时要复制一份，否则 "ready" 到达时已经变成 "done"
        self.worker = self.ctx.run(lambda prog, cancel: lib.sync(ch.files, roots, lambda pg: prog(copy.copy(pg)),
                                                                 cancel, ch.unsettled),
                                   self._on_progress, self._on_done, self._on_error)

    def _on_progress(self, pg) -> None:
        if pg.stage == "ready":
            self.ctx.library_changed.emit()
            return
        now = time.monotonic()
        if pg.stage in ("meta", "pixel") and pg.pix_total + pg.meta_total > 20 and now - self._progress_at > 0.5:
            self._progress_at = now
            self.ctx.status.emit(_t("自动同步：正在分析新照片…  元数据 {0}/{1}  画面 {2}/{3}",
                                    pg.meta_done, pg.meta_total, pg.pix_done, pg.pix_total))
        elif pg.stage == "ai" and pg.ai_total and now - self._progress_at > 0.5:
            self._progress_at = now
            self.ctx.status.emit(_t("自动同步：AI 识别新照片 {0}/{1}", pg.ai_done, pg.ai_total))

    def _on_done(self, rep) -> None:
        self.worker = None
        if rep.cancelled:
            self._dirty = True
        else:
            if rep.ai_new:
                self.ctx.library_changed.emit()          # 新照片的场景 / 智能相簿
            if rep.changed:
                self.last_report, self.last_time = rep, time.time()
                self.ctx.status.emit(_t("自动同步：") + self.summary(rep))
        self._set_state("watching" if self.watcher is not None else ("off" if not self.enabled else "empty"))
        self._kick()

    def _on_error(self, tb: str) -> None:
        self.worker = None
        self.error = tb.strip().splitlines()[-1] if tb.strip() else ""
        self._set_state("error")
        self.ctx.status.emit(_t("自动同步出错：") + self.error)
        self._kick()

    # ---- 显示 ------------------------------------------------------------
    def _set_state(self, s: str) -> None:
        self.state = s
        self.changed.emit()

    @staticmethod
    def summary(rep) -> str:
        parts = []
        if rep.added:
            parts.append(_t("新增 {0} 张", rep.added))
        if rep.removed:
            parts.append(_t("移除 {0} 张", rep.removed))
        if rep.updated:
            parts.append(_t("更新 {0} 张", rep.updated))
        return _t("、").join(parts) or _t("没有变化")

    def describe(self) -> str:
        """一行状态文字。"""
        n = len(self.roots())
        if self.state == "off":
            return _t("自动同步已关闭")
        if self.state == "empty":
            return _t("自动同步已开启，还没有文件夹")
        if self.state == "starting":
            return _t("自动同步：正在读取 {0} 个文件夹…", n)
        if self.state == "syncing":
            return _t("自动同步：正在更新照片库…")
        if self.state == "error":
            return _t("自动同步出错：{0}", self.error)
        how = "" if self.watcher is None or self.watcher.notifying else _t("（定时检查）")
        text = _t("● 自动同步中：{0} 个文件夹{1}", n, how)
        if self.last_report is not None and self.last_time is not None:
            text += _t(" · {0} {1}", time.strftime("%H:%M", time.localtime(self.last_time)), self.summary(self.last_report))
        return text
