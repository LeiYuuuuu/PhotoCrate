"""视觉主题：简洁的工具风格。

两套配色，底色都是中性灰（不带色相），看照片、调色时周围的界面不会让人对色温产生偏差：
- light：浅灰底色 + 黄色强调 + 深色机体条（导航/标题）。
- dark ：深灰底色 + 金黄强调 + 青色信息色。
强调色只用在主按钮、焦点和勾选上；列表 / 菜单里选中的一项用浅一档的灰（sel）+ 左侧细色条（sel_edge）。
字体：中文用微软雅黑；数字用 Windows 自带的 Bahnschrift（DIN 风格窄体）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from ..config import user_cache_dir

CN_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC",
               "Source Han Sans SC", "WenQuanYi Micro Hei"]
DISPLAY_FAMILIES = ["Bahnschrift", "DIN Alternate", "Barlow Condensed", "Oswald", "Arial Narrow",
                    "Microsoft YaHei UI", "Noto Sans CJK SC"]
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "Noto Sans Mono CJK SC",
                 "Microsoft YaHei UI"]


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface2: str
    line: str
    ink: str
    ink2: str
    ink3: str
    slab: str
    slab2: str
    slab_ink: str
    slab_ink2: str
    accent: str
    accent_hover: str
    accent_ink: str
    accent2: str
    danger: str
    ok: str
    warn: str
    sel: str
    sel_edge: str

    def c(self, key: str, alpha: int | None = None) -> QColor:
        col = QColor(getattr(self, key))
        if alpha is not None:
            col.setAlpha(alpha)
        return col


LIGHT = Palette(
    name="light",
    bg="#ECECEC", surface="#FAFAFA", surface2="#E3E3E3", line="#CFCFCF",
    ink="#1F1F1F", ink2="#565656", ink3="#737373",
    slab="#1C1C1C", slab2="#2E2E2E", slab_ink="#F2F2F2", slab_ink2="#A3A3A3",
    accent="#F2DD46", accent_hover="#FFE96C", accent_ink="#151515",
    accent2="#16A9BC", danger="#E2453A", ok="#2F9E62", warn="#EE8A1E",
    sel="#D6D6D6", sel_edge="#C9A800",
)

DARK = Palette(
    name="dark",
    bg="#161616", surface="#202020", surface2="#2A2A2A", line="#3A3A3A",
    ink="#E8E8E8", ink2="#B3B3B3", ink3="#8C8C8C",
    slab="#101010", slab2="#262626", slab_ink="#E8E8E8", slab_ink2="#9A9A9A",
    accent="#E9CF72", accent_hover="#F7E19B", accent_ink="#111214",
    accent2="#1EC6E6", danger="#F25A4E", ok="#3FBF7F", warn="#FF9F1C",
    sel="#363636", sel_edge="#E9CF72",
)

_current: Palette = LIGHT


def current() -> Palette:
    return _current


def font(role: str = "cn", size: float = 10, weight: int = QFont.Weight.Normal, spacing: float = 0) -> QFont:
    fams = {"cn": CN_FAMILIES, "display": DISPLAY_FAMILIES, "mono": MONO_FAMILIES}[role]
    f = QFont()
    f.setFamilies(fams)
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def _asset_dir() -> Path:
    d = user_cache_dir() / "ui_assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _svg(name: str, body: str) -> str:
    p = _asset_dir() / name
    p.write_text(body, "utf-8")
    return p.as_posix()


def _make_assets(p: Palette) -> dict[str, str]:
    tag = p.name
    return {
        "check": _svg(f"check_{tag}.svg",
                      f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
                      f'<path d="M2.5 7.2 L5.6 10.2 L11.6 3.6" fill="none" stroke="{p.accent_ink}" stroke-width="2.2"/></svg>'),
        "down": _svg(f"down_{tag}.svg",
                     f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                     f'<path d="M1 3 L5 7 L9 3" fill="none" stroke="{p.ink}" stroke-width="1.6"/></svg>'),
        "up": _svg(f"up_{tag}.svg",
                   f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                   f'<path d="M1 7 L5 3 L9 7" fill="none" stroke="{p.ink}" stroke-width="1.6"/></svg>'),
        "branch_closed": _svg(f"bc_{tag}.svg",
                              f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                              f'<path d="M3 1.5 L7 5 L3 8.5 Z" fill="{p.ink2}"/></svg>'),
        "branch_open": _svg(f"bo_{tag}.svg",
                            f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                            f'<path d="M1.5 3 L8.5 3 L5 7 Z" fill="{p.ink}"/></svg>'),
        "radio_on": _svg(f"radio_{tag}.svg",
                         f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
                         f'<circle cx="7" cy="7" r="3.2" fill="{p.accent_ink}"/></svg>'),
        "close": _svg(f"close_{tag}.svg",                  # 标签页的关闭按钮（画在深色机体条上）
                      f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
                      f'<path d="M3 3 L9 9 M9 3 L3 9" fill="none" stroke="{p.slab_ink2}" stroke-width="1.4"/></svg>'),
    }


def asset(name: str) -> str:
    """当前配色下某个小图标文件的路径（给样式表里的 url() 用）。"""
    return _make_assets(_current)[name]


def stylesheet(p: Palette) -> str:
    a = _make_assets(p)
    return f"""
QPushButton:focus {{ border: 1px solid {p.accent2}; }}
QWidget {{ background: transparent; color: {p.ink}; font-size: 10pt; }}
QMainWindow, QDialog, #PageRoot {{ background: {p.bg}; }}
QToolTip {{ background: {p.slab}; color: {p.slab_ink}; border: 1px solid {p.slab2}; padding: 5px 8px; }}

QLabel[role="dim"] {{ color: {p.ink2}; }}
QLabel[role="faint"] {{ color: {p.ink3}; }}
QLabel[role="danger"] {{ color: {p.danger}; }}
QLabel[role="warn"] {{ color: {p.warn}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.surface}; border: 1px solid {p.line}; padding: 5px 8px; border-radius: 0px;
    selection-background-color: {p.accent}; selection-color: {p.accent_ink};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QComboBox:on {{
    border: 1px solid {p.ink}; border-bottom: 2px solid {p.accent};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {p.ink3}; background: {p.surface2}; }}
QComboBox {{ padding-right: 26px; min-height: 20px; }}
QComboBox::drop-down {{ border: none; width: 24px; border-left: 1px solid {p.line}; }}
QComboBox::down-arrow {{ image: url("{a['down']}"); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {p.surface}; border: 1px solid {p.line}; padding: 2px;
    selection-background-color: {p.sel}; selection-color: {p.ink};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; border-left: 1px solid {p.line}; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; border-left: 1px solid {p.line}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{a['up']}"); width: 8px; height: 8px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{a['down']}"); width: 8px; height: 8px; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {p.ink2}; background: {p.surface}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border: 1px solid {p.ink}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border: 1px solid {p.ink}; image: url("{a['check']}"); }}
QRadioButton::indicator:checked {{ background: {p.accent}; border: 1px solid {p.ink}; image: url("{a['radio_on']}"); }}
QRadioButton::indicator {{ border-radius: 7px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {p.ink3}; }}

QPushButton {{ background: {p.surface}; border: 1px solid {p.line}; padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ border: 1px solid {p.ink}; background: {p.surface2}; }}
QPushButton:pressed {{ background: {p.sel}; }}
QPushButton:disabled {{ color: {p.ink3}; border-color: {p.line}; }}
QPushButton:checked {{ background: {p.sel}; color: {p.ink}; border: 1px solid {p.sel_edge}; }}

QSlider::groove:horizontal {{ height: 4px; background: {p.line}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p.ink3}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; background: {p.ink}; }}
QSlider::handle:horizontal:hover {{ background: {p.accent}; }}

QScrollBar:vertical {{ width: 10px; background: transparent; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p.line}; min-height: 30px; margin: 2px 2px; }}
QScrollBar::handle:vertical:hover {{ background: {p.ink3}; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {p.line}; min-width: 30px; margin: 2px 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QTreeView, QTableView, QListView, QListWidget, QTreeWidget, QTableWidget {{
    background: {p.surface}; border: 1px solid {p.line}; alternate-background-color: {p.bg};
    selection-background-color: {p.sel}; selection-color: {p.ink};
}}
QTreeView::item, QListWidget::item {{ padding: 4px 2px; }}
QTreeView::item:hover, QTableView::item:hover, QListWidget::item:hover {{ background: {p.surface2}; }}
QTreeView::item:selected, QTableView::item:selected {{ background: {p.sel}; color: {p.ink}; }}
QListWidget::item:selected {{ background: {p.sel}; color: {p.ink}; border-left: 2px solid {p.sel_edge}; }}
QTreeView::branch:has-children:closed {{ image: url("{a['branch_closed']}"); }}
QTreeView::branch:has-children:open {{ image: url("{a['branch_open']}"); }}
QHeaderView::section {{ background: {p.slab}; color: {p.slab_ink}; border: none; border-right: 1px solid {p.slab2};
    padding: 7px 10px; font-size: 9pt; }}
QTableCornerButton::section {{ background: {p.slab}; border: none; }}

QScrollArea {{ border: none; }}
QSplitter::handle {{ background: {p.bg}; }}
QSplitter::handle:hover {{ background: {p.line}; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.line}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; }}
QMenu::item:selected {{ background: {p.sel}; color: {p.ink}; }}
QMenu::separator {{ height: 1px; background: {p.line}; margin: 4px 8px; }}
QMessageBox {{ background: {p.bg}; }}
QProgressBar {{ border: 1px solid {p.line}; background: {p.surface}; height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {p.accent}; }}
"""


def apply(app: QApplication, name: str = "light") -> Palette:
    global _current
    _current = DARK if name == "dark" else LIGHT
    p = _current
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(p.bg))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(p.ink))
    pal.setColor(QPalette.ColorRole.Base, QColor(p.surface))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(p.bg))
    pal.setColor(QPalette.ColorRole.Text, QColor(p.ink))
    pal.setColor(QPalette.ColorRole.Button, QColor(p.surface))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(p.ink))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(p.sel))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(p.ink))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.slab))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(p.slab_ink))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.ink3))
    pal.setColor(QPalette.ColorRole.Mid, QColor(p.line))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.ink3))
    app.setPalette(pal)
    app.setFont(font("cn", 10))
    app.setStyleSheet(stylesheet(p))
    return p
