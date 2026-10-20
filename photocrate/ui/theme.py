"""视觉主题：工业科幻终端风格。

两套配色：
- light「终末地·浅」：浅灰工业底色 + 高可见度黄色强调 + 深色机体条（导航/标题）。
- dark 「方舟·暗」 ：深色终端底色 + 金黄强调 + 青色信息色。
设计语言：直角与切角、细线框 + 角标、英文等宽小标签、编号章节、斜纹警示带、分段进度条。
字体：中文用微软雅黑；数字/英文标签用 Windows 自带的 Bahnschrift（DIN 风格窄体）。
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

    def c(self, key: str, alpha: int | None = None) -> QColor:
        col = QColor(getattr(self, key))
        if alpha is not None:
            col.setAlpha(alpha)
        return col


LIGHT = Palette(
    name="light",
    bg="#E6E6E1", surface="#F4F4F0", surface2="#DADAD3", line="#C3C3BA",
    ink="#151515", ink2="#55554E", ink3="#8D8D84",
    slab="#1A1B1C", slab2="#2A2C2E", slab_ink="#EEEEE8", slab_ink2="#8A8C8E",
    accent="#FFE100", accent_hover="#F2D400", accent_ink="#151515",
    accent2="#16A9BC", danger="#E2453A", ok="#2F9E62", warn="#EE8A1E",
)

DARK = Palette(
    name="dark",
    bg="#111214", surface="#181A1D", surface2="#202327", line="#2E3237",
    ink="#ECECE8", ink2="#A3A7AC", ink3="#686C73",
    slab="#090A0B", slab2="#16181B", slab_ink="#ECECE8", slab_ink2="#7B8086",
    accent="#F7D23E", accent_hover="#FFDD55", accent_ink="#111214",
    accent2="#1EC6E6", danger="#F25A4E", ok="#3FBF7F", warn="#FF9F1C",
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
    }


def stylesheet(p: Palette) -> str:
    a = _make_assets(p)
    return f"""
* {{ outline: none; }}
QWidget {{ background: transparent; color: {p.ink}; font-size: 10pt; }}
QMainWindow, QDialog, #PageRoot {{ background: {p.bg}; }}
QToolTip {{ background: {p.slab}; color: {p.slab_ink}; border: 1px solid {p.accent}; padding: 5px 8px; }}

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
    background: {p.surface}; border: 1px solid {p.ink}; padding: 2px;
    selection-background-color: {p.accent}; selection-color: {p.accent_ink};
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

QPushButton {{ background: {p.surface}; border: 1px solid {p.ink2}; padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ border: 1px solid {p.ink}; background: {p.surface2}; }}
QPushButton:pressed {{ background: {p.accent}; color: {p.accent_ink}; }}
QPushButton:disabled {{ color: {p.ink3}; border-color: {p.line}; }}
QPushButton:checked {{ background: {p.ink}; color: {p.bg}; border: 1px solid {p.ink}; }}

QSlider::groove:horizontal {{ height: 4px; background: {p.line}; }}
QSlider::sub-page:horizontal {{ background: {p.ink}; }}
QSlider::handle:horizontal {{ width: 10px; margin: -8px 0; background: {p.accent}; border: 1px solid {p.ink}; }}

QScrollBar:vertical {{ width: 10px; background: transparent; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p.line}; min-height: 30px; margin: 2px 2px; }}
QScrollBar::handle:vertical:hover {{ background: {p.ink3}; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {p.line}; min-width: 30px; margin: 2px 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QTreeView, QTableView, QListView, QListWidget, QTreeWidget, QTableWidget {{
    background: {p.surface}; border: 1px solid {p.line}; alternate-background-color: {p.bg};
    selection-background-color: {p.accent}; selection-color: {p.accent_ink};
}}
QTreeView::item, QListWidget::item {{ padding: 4px 2px; }}
QTreeView::item:hover, QTableView::item:hover, QListWidget::item:hover {{ background: {p.surface2}; }}
QTreeView::item:selected, QTableView::item:selected, QListWidget::item:selected {{ background: {p.accent}; color: {p.accent_ink}; }}
QTreeView::branch:has-children:closed {{ image: url("{a['branch_closed']}"); }}
QTreeView::branch:has-children:open {{ image: url("{a['branch_open']}"); }}
QHeaderView::section {{ background: {p.slab}; color: {p.slab_ink}; border: none; border-right: 1px solid {p.slab2};
    padding: 5px 8px; font-size: 8.5pt; }}
QTableCornerButton::section {{ background: {p.slab}; border: none; }}

QScrollArea {{ border: none; }}
QSplitter::handle {{ background: {p.bg}; }}
QSplitter::handle:hover {{ background: {p.accent}; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.ink}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; }}
QMenu::item:selected {{ background: {p.accent}; color: {p.accent_ink}; }}
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
    pal.setColor(QPalette.ColorRole.Highlight, QColor(p.accent))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(p.accent_ink))
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
