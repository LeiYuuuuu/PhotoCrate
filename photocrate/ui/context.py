"""界面共享上下文：设置、照片库、操作日志、后台任务，以及页面间通信用的信号。

页面（包括插件注册的页面）都会拿到同一个 AppContext。
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from ..config import Settings
from ..core.library import Library
from ..ops.journal import Journal
from ..ops.mover import Mover


class Worker(QThread):
    progress = Signal(object)
    done = Signal(object)
    error = Signal(str)

    def __init__(self, fn: Callable[[Callable[[Any], None], threading.Event], Any], parent=None):
        super().__init__(parent)
        self.fn = fn
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            r = self.fn(self.progress.emit, self.cancel_event)
            self.done.emit(r)
        except Exception:
            self.error.emit(traceback.format_exc())


class AppContext(QObject):
    library_changed = Signal()          # 重新扫描 / 重新分析后
    selection_changed = Signal()        # 用户在缩略图中的选择变化
    settings_changed = Signal(str)      # 某个设置项变化
    files_changed = Signal()            # 执行移动 / 撤销之后
    theme_changed = Signal()
    status = Signal(str)                # 状态栏消息
    navigate = Signal(str)              # 请求跳转页面（页面 id）
    ratings_changed = Signal()          # 星级 / 色标 / 留弃标记变化（只需重绘）

    def __init__(self, settings: Settings, library: Library, journal: Journal):
        super().__init__()
        self.settings = settings
        self.library = library
        self.journal = journal
        self.mover = Mover(journal, bool(settings.get("verify_hash")))
        self._workers: set[Worker] = set()
        self.busy = False

    def run(self, fn, on_progress=None, on_done=None, on_error=None) -> Worker:
        w = Worker(fn)
        if on_progress:
            w.progress.connect(on_progress)
        if on_done:
            w.done.connect(on_done)
        if on_error:
            w.error.connect(on_error)
        else:
            w.error.connect(lambda tb: self.status.emit("出错：" + tb.strip().splitlines()[-1]))
        w.finished.connect(lambda: self._workers.discard(w))
        self._workers.add(w)
        w.start()
        return w

    # ---- 打分 / 大图 ------------------------------------------------------
    def rate(self, gids: list[int], change: dict[str, Any] | None) -> int:
        n = self.library.set_rating(gids, change)
        if n:
            self.ratings_changed.emit()
            parts = []
            if change is None:
                parts.append("已清除标记")
            else:
                from ..analyzers.builtin import FLAG_NAMES, LABEL_NAMES
                if "rating" in change:
                    parts.append(f"{change['rating']} 星" if change["rating"] else "清除星级")
                if "label" in change:
                    parts.append(f"色标 {LABEL_NAMES.get(change['label'], change['label'])}" if change["label"] else "清除色标")
                if "flag" in change:
                    parts.append(FLAG_NAMES.get(change["flag"], "取消留用 / 弃用") if change["flag"] else "取消留用 / 弃用")
            self.status.emit(f"{n} 张照片：" + "、".join(parts) + "（写入 .xmp 后 Lightroom / Bridge 才能看到）")
        return n

    def open_viewer(self, groups: list, index: int, parent=None):
        from .viewer import ViewerWindow
        v = ViewerWindow(self, groups, index, parent)
        v.showMaximized()
        v.raise_()
        v.activateWindow()
        return v

    def set_setting(self, key: str, value: Any) -> None:
        self.settings.set(key, value)
        if key == "verify_hash":
            self.mover.verify_hash = bool(value)
        self.settings_changed.emit(key)

    def shutdown(self) -> None:
        for w in list(self._workers):
            w.cancel()
            w.wait(5000)
