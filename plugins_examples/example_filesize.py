"""示例插件：按文件大小分区 + 「大文件排行」页面。

演示三个扩展点：
  - Dimension（分区条件）：segments() 返回文件夹名，tokens() 提供模板占位符 {大小档}
  - 预设：一键套用
  - 页面：出现在左侧导航
复制到 plugins/ 目录并重启程序即可。
"""
from __future__ import annotations

from photocrate.partition.base import Dimension, Option

PLUGIN_INFO = {
    "name": "文件大小分区",
    "version": "1.0",
    "description": "按文件大小分档；附带“大文件排行”页面",
}


class FileSizeDimension(Dimension):
    id = "filesize"
    label = "文件大小"
    en = "FILE SIZE"
    description = "按整组文件（含 RAW 与附属文件）的总大小分档。"
    unknown = "未知大小"
    order_by = "name"
    options = [
        Option("unit", "分档方式", default="coarse", choices=[
            ("coarse", "小 / 中 / 大（2MB、10MB 为界）"),
            ("fine", "细分（1、5、20、50MB 为界）"),
        ]),
    ]

    def _label(self, g, unit="coarse"):
        mb = g.total_size / 1024 / 1024
        if unit == "fine":
            edges = [(1, "1_小于1MB"), (5, "2_1-5MB"), (20, "3_5-20MB"), (50, "4_20-50MB")]
            last = "5_50MB以上"
        else:
            edges = [(2, "1_小(2MB以下)"), (10, "2_中(2-10MB)")]
            last = "3_大(10MB以上)"
        for e, lab in edges:
            if mb < e:
                return lab
        return last

    def segments(self, g, opts, ctx):
        return [self._label(g, opts.get("unit", "coarse"))]

    def tokens(self):
        return {"大小档": lambda g, ctx: self._label(g)}


def make_page(ctx):
    """返回一个 QWidget。ctx 是 AppContext：ctx.library.groups 是全部照片组。"""
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

    from photocrate.ops.pathsafe import human_size
    from photocrate.ui.pages.base import Page
    from photocrate.ui.widgets.deco import Panel

    class BigFilesPage(Page):
        page_id = "plugin_bigfiles"
        index = "07"
        title = "大文件排行"
        en = "Plugin // largest items"
        sub = "插件页面示例：列出占用空间最大的 50 组照片。"

        def __init__(self, ctx):
            super().__init__(ctx)
            pnl = Panel("最大的照片组", "Top 50")
            self.table = QTableWidget(0, 3)
            self.table.setHorizontalHeaderLabels(["文件", "大小", "位置"])
            self.table.horizontalHeader().setStretchLastSection(True)
            pnl.body.addWidget(self.table)
            self.root.addWidget(pnl, 1)
            ctx.library_changed.connect(self.on_show)
            ctx.files_changed.connect(self.on_show)

        def on_show(self):
            gs = sorted((g for g in self.ctx.library.groups if g.files), key=lambda g: -g.total_size)[:50]
            self.table.setRowCount(len(gs))
            for r, g in enumerate(gs):
                self.table.setItem(r, 0, QTableWidgetItem(g.name))
                self.table.setItem(r, 1, QTableWidgetItem(human_size(g.total_size)))
                self.table.setItem(r, 2, QTableWidgetItem(g.folder))

    return BigFilesPage(ctx)


def register(api):
    api.register_dimension(FileSizeDimension())
    api.register_preset("插件：按文件大小", rules=[{"dim": "filesize", "opts": {"unit": "coarse"}}])
    api.register_page("大文件排行", "Largest", make_page)
