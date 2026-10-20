"""插件系统 —— 不改核心代码即可加入新功能。

把一个 .py 文件放进 plugins/ 目录（程序目录或 %APPDATA%\\PhotoCrate\\plugins），
文件里提供：

    PLUGIN_INFO = {"name": "我的插件", "version": "1.0", "description": "..."}

    def register(api):
        api.register_analyzer(MyAnalyzer())       # 新的特征（可以是 AI 模型）
        api.register_dimension(MyDimension())     # 新的分区条件
        api.register_preset("名称", rules=[...])   # 新的快速预设
        api.register_page("标题", "EN", factory)   # 新的界面页面（factory(app_ctx) -> QWidget）

后台分析进程也会加载插件（此时 api.headless 为 True，register_page 会被忽略）。
完整示例见 plugins_examples/ 目录和 ARCHITECTURE.md。
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class PluginInfo:
    name: str
    path: str
    version: str = ""
    description: str = ""
    error: str | None = None


@dataclass
class PageSpec:
    title: str
    en: str
    factory: Callable[[Any], Any]


@dataclass
class PresetSpec:
    name: str
    rules: list[dict[str, Any]] | None = None
    template: str | None = None


LOADED: list[PluginInfo] = []
PAGES: list[PageSpec] = []
PRESETS: list[PresetSpec] = []
_loaded_paths: set[str] = set()


class PluginAPI:
    def __init__(self, headless: bool):
        self.headless = headless

    def register_analyzer(self, analyzer) -> None:
        from .analyzers.base import register_analyzer
        register_analyzer(analyzer)

    def register_dimension(self, dimension) -> None:
        from .partition.base import register_dimension
        register_dimension(dimension)

    def register_preset(self, name: str, rules: list[dict[str, Any]] | None = None, template: str | None = None) -> None:
        PRESETS.append(PresetSpec(name, rules, template))

    def register_page(self, title: str, en: str, factory: Callable[[Any], Any]) -> None:
        if not self.headless:
            PAGES.append(PageSpec(title, en, factory))


def load_plugins(dirs: list[str] | list[Path], headless: bool = False) -> list[PluginInfo]:
    api = PluginAPI(headless)
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.py")):
            if f.name.startswith("_"):
                continue
            key = str(f.resolve()).lower()
            if key in _loaded_paths:
                continue
            _loaded_paths.add(key)
            info = PluginInfo(name=f.stem, path=str(f))
            try:
                mod_name = f"photocrate_plugin_{f.stem}"
                spec = importlib.util.spec_from_file_location(mod_name, f)
                if spec is None or spec.loader is None:
                    raise ImportError("无法加载")
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                spec.loader.exec_module(mod)
                meta = getattr(mod, "PLUGIN_INFO", {}) or {}
                info.name = meta.get("name", f.stem)
                info.version = str(meta.get("version", ""))
                info.description = meta.get("description", "")
                reg = getattr(mod, "register", None)
                if callable(reg):
                    reg(api)
                log.info("已加载插件：%s (%s)", info.name, f)
            except Exception as e:
                info.error = f"{type(e).__name__}: {e}"
                log.error("插件加载失败 %s：\n%s", f, traceback.format_exc())
            LOADED.append(info)
    return LOADED
