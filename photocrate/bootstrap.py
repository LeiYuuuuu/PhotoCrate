"""启动准备：日志、内置分析器/维度注册、插件加载。界面和测试都从这里开始。"""
from __future__ import annotations

import logging
import logging.handlers
import sys

from .config import Settings, plugin_dirs, user_data_dir

_done = False


def setup_logging(level: int = logging.INFO) -> None:
    logs = user_data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(level)
    fh = logging.handlers.RotatingFileHandler(logs / "photocrate.log", maxBytes=2_000_000, backupCount=3,
                                              encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(sh)


def bootstrap(settings: Settings | None = None, load_user_plugins: bool = True) -> Settings:
    global _done
    settings = settings or Settings()
    if _done:
        return settings
    from .analyzers.builtin import register_builtin as reg_analyzers
    from .partition.dimensions import register_builtin as reg_dims
    from .plugins import load_plugins

    reg_analyzers()
    reg_dims()
    if load_user_plugins:
        load_plugins([str(d) for d in plugin_dirs()], headless=False)
    _done = True
    return settings
