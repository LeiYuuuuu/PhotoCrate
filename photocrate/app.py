"""程序入口：初始化 → 主题 → 主窗口。"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import traceback
from .i18n import t as _t


def _excepthook(etype, value, tb):
    text = "".join(traceback.format_exception(etype, value, tb))
    logging.getLogger("photocrate").error("未捕获的异常：\n%s", text)
    try:
        from .ui.dialogs import info
        from PySide6.QtWidgets import QApplication
        if QApplication.instance():
            info(None, _t("程序出错"), _t("<pre style='font-size:8pt'>{0}</pre><br>详细信息已写入日志（设置 → 打开日志文件夹）。", text[-2000:]), "ERROR")
    except Exception:
        pass


def run() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    # 界面语言要在导入界面 / 分析器模块之前确定（它们的模块级文字在导入时翻译）
    from . import i18n
    from .config import Settings
    settings = Settings()
    lang = i18n.set_language(i18n.resolve(settings))
    if settings.get("ui_language") not in ("zh", "en") and not os.environ.get("PHOTOCRATE_LANG"):
        settings.set("ui_language", lang)              # 记下来：以后添加了文件夹也不会因为“老用户”判断而变

    from .bootstrap import bootstrap, setup_logging
    from .core.library import Library
    from .ops.journal import Journal, journal_path

    setup_logging()
    sys.excepthook = _excepthook
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("PhotoCrate")
    app.setOrganizationName("PhotoCrate")

    settings = bootstrap(settings)
    from .ui import theme
    theme.apply(app, settings.get("theme"))

    from .ui.context import AppContext
    from .ui.main_window import MainWindow

    library = Library(settings)
    journal = Journal(journal_path())
    ctx = AppContext(settings, library, journal)
    win = MainWindow(ctx)
    win.show()
    code = app.exec()
    if getattr(win, "restart_requested", False):
        args = [sys.executable] + (sys.argv[1:] if getattr(sys, "frozen", False) or "__compiled__" in globals()
                                   else ["-m", "photocrate"])
        try:
            subprocess.Popen(args, close_fds=True)
        except Exception:
            pass
    return code


def main() -> None:
    import multiprocessing
    multiprocessing.freeze_support()   # Windows 打包后的多进程必须
    sys.exit(run())
