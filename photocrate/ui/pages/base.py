from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..context import AppContext
from ..widgets.deco import SectionHeader


class Page(QWidget):
    page_id = ""
    index = "00"
    title = ""
    en = ""
    sub = ""

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("PageRoot")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(26, 16, 26, 18)
        self.root.setSpacing(12)
        self.header = SectionHeader(self.index, self.title, self.en, self.sub)
        self.root.addWidget(self.header)

    def on_show(self) -> None:
        """切换到本页时调用。"""
