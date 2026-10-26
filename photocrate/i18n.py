"""界面语言：简体中文（原文）/ English。

- 所有界面文字在代码里写中文原文，显示时经过 t()；英文模式下按原文查 locale_en.EN 表，查不到就原样显示。
- 带变量的文字写成 t("已选 {0} 张", n)：英文表里同样用 {0} 占位。
- 软件内部（缓存、标注库、分析结果、地名）一律保存中文，只在显示和生成文件夹名时翻译，所以切换语言不影响已有数据。
- 语言在启动时确定（set_language 要在导入界面和分析器模块之前调用），切换后需要重启。
- 地名：国家名用 Qt 自带的英文国家 / 地区名，海外的州 / 城市用 data/place_names_zh.json 反查回英文原名，
  中国的省级行政区用 locale_en.PROVINCES；中国的市、区县没有英文数据，保持中文。
"""
from __future__ import annotations

import json
import locale
import os
from typing import Any

LANGUAGES = [("zh", "简体中文"), ("en", "English")]

LANG = "zh"
_EN: dict[str, str] = {}


def set_language(lang: str | None) -> str:
    global LANG, _EN
    LANG = "en" if lang == "en" else "zh"
    _EN = {}
    if LANG == "en":
        from .locale_en import EN, PROVINCES
        table: dict[str, str] = {}
        table.update(_place_table())
        table.update(PROVINCES)
        from .locale_en_photos import EN_PHOTOS
        table.update(EN_PHOTOS)          # 相册 / 修图工作台 / 文字识别（与 locale_en 同名的条目以 locale_en 为准）
        from .locale_en_sync import EN_SYNC
        table.update(EN_SYNC)            # 自动同步
        from .locale_en_safety import EN_SAFETY
        table.update(EN_SAFETY)          # 安全检查新增的提示
        from .locale_en_extras import EN_EXTRAS
        table.update(EN_EXTRAS)          # 闭眼 / 表情、隐藏、批量编辑、隐私体检、手机互传
        table.update(EN)
        _EN = table
    return LANG


def system_language() -> str:
    """Windows 显示语言是中文时用中文，否则英文。"""
    try:
        import ctypes
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "zh" if (lid & 0x3FF) == 0x04 else "en"
    except Exception:
        loc = (locale.getlocale()[0] or "").lower()
        return "zh" if loc.startswith(("zh", "chinese")) else "en"


def resolve(settings) -> str:
    """环境变量 PHOTOCRATE_LANG=zh/en 优先（测试、截图用）；其次是设置里选的；
    没选过的新用户跟随系统语言，老用户（已经有其他设置）保持中文。"""
    env = os.environ.get("PHOTOCRATE_LANG")
    if env in ("zh", "en"):
        return env
    v = settings.get("ui_language")
    if v in ("zh", "en"):
        return v
    existing = bool(settings.get("source_dirs")) or bool(settings.get("window_state"))
    return "zh" if existing else system_language()


def is_en() -> bool:
    return LANG == "en"


def t(text: Any, *args: Any) -> str:
    """翻译一段界面文字。有 args 时按 {0} {1} … 填入。"""
    if not isinstance(text, str):
        return text
    s = _EN.get(text, text) if LANG == "en" else text
    if args:
        try:
            return s.format(*args)
        except (IndexError, KeyError, ValueError):
            return text.format(*args)
    return s


def tl(items):
    """翻译 [(值, 中文名)] 列表里的名字。"""
    return [(k, t(v)) for k, v in items]


def _place_table() -> dict[str, str]:
    """中文地名 → 英文：国家（Qt 的国家 / 地区名）+ place_names_zh.json 里海外州 / 城市的反查。"""
    out: dict[str, str] = {"中国": "China"}
    try:
        from .config import find_resource
        p = find_resource("data", "place_names_zh.json")
        d = json.load(open(p, encoding="utf-8")) if p else {}
    except Exception:
        d = {}
    countries = d.get("countries") or {}
    try:
        from PySide6.QtCore import QLocale
        for cc, zh in countries.items():
            try:
                terr = QLocale.codeToTerritory(cc)
                name = QLocale.territoryToString(terr)
                if name and terr.value != 0:  # 0 = AnyTerritory（未知代码）
                    out[zh] = name
            except Exception:
                continue
    except Exception:
        pass
    for key in ("admin1", "cities"):
        for k, zh in (d.get(key) or {}).items():
            eng = k.split("/", 1)[-1] if "/" in k else k
            if zh and eng and zh not in out:
                out[zh] = eng
    return out


# “未知 / 其他 / 其余”这类兜底条目（排序放最后、画斜纹）：两种语言的写法都认
UNKNOWN_PREFIXES = ("未知", "无法", "未分析", "其他", "其余", "日期待确认",
                    "Unknown", "Cannot", "Not analyzed", "Other", "Rest", "Date to confirm")


def is_unknown(label) -> bool:
    return isinstance(label, str) and label.startswith(UNKNOWN_PREFIXES)


def matches(template: str, text: str) -> bool:
    """text 是不是按 template（当前语言）生成的，{0} 之类的位置可以是任何内容。"""
    import re
    pat = re.sub(r"\\{\d+\\}", ".+?", re.escape(t(template)))
    return re.fullmatch(pat, text or "", re.S) is not None
