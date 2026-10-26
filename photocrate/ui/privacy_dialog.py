"""分享前隐私体检的确认窗口：左边是有发现的照片，右边大图上框出每一处，勾选的在导出时打码。

默认勾上：号码、证件、车牌、快递单、地址、邮箱、二维码 / 条形码、路人的脸；主要人物的脸列出来但不勾。
只影响这次导出的副本，原片不动。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout)

from .dialogs import _Base
from .photos.canvas import qimage_from_rgb
from .widgets.deco import TacButton, caption
from ..i18n import t as _t
from ..ops import privacy

VIEW = 560
COLORS = {"stranger": "#E0533D", "face": "#8A8F98", "qr": "#7A4DD8", "barcode": "#7A4DD8"}


class PrivacyDialog(_Base):
    """exec() 后：result_code = "redact"（打码并导出）/ "plain"（不打码直接导出）/ None（取消导出）。"""

    def __init__(self, parent, groups: list, found: dict[int, list[privacy.Finding]], text_model: bool = True):
        super().__init__(parent, _t("分享前隐私体检"), "PRIVACY CHECK")
        self.resize(1040, 720)
        self.groups = [g for g in groups if g.gid in found]
        self.found = found
        self.result_code: str | None = None
        self._img: dict[int, object] = {}
        n = sum(len(v) for v in found.values())
        self.body.addWidget(QLabel(_t("在 <b>{0}</b> 张照片里找到 <b>{1}</b> 处可能泄露隐私的地方。勾选的会在导出的照片上打码（原片不变）：",
                                      len(self.groups), n)))
        if not text_model:
            self.body.addWidget(caption(_t("没有下载文字识别模型，这次只检查了二维码、条形码和人脸；下载后还能找出照片里的手机号、证件号、车牌…"),
                                        "dim", 8.5))
        main = QHBoxLayout()
        self.photos = QListWidget()
        self.photos.setFixedWidth(220)
        self.photos.setIconSize(QPixmap(64, 64).size())
        for g in self.groups:
            on = sum(1 for f in found[g.gid] if f.on)
            it = QListWidgetItem(f"{g.name}\n" + _t("{0} 处 · 打码 {1}", len(found[g.gid]), on))
            pm = QPixmap(g.thumb) if getattr(g, "thumb", None) else QPixmap()
            if not pm.isNull():
                it.setIcon(QIcon(pm))
            self.photos.addItem(it)
        self.photos.currentRowChanged.connect(self._show)
        main.addWidget(self.photos)
        mid = QVBoxLayout()
        self.view = QLabel()
        self.view.setFixedSize(VIEW, VIEW)
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mid.addWidget(self.view)
        main.addLayout(mid)
        right = QVBoxLayout()
        right.addWidget(QLabel(_t("这张照片里：")))
        self.items = QListWidget()
        self.items.itemChanged.connect(self._toggled)
        right.addWidget(self.items, 1)
        sr = QHBoxLayout()
        sr.addWidget(QLabel(_t("打码方式")))
        self.style = QComboBox()
        for k, name in privacy.STYLES:
            self.style.addItem(name, k)
        sr.addWidget(self.style, 1)
        right.addLayout(sr)
        right.addWidget(caption(_t("灰框 = 主要人物的脸，默认不打码；需要时勾上。"), "faint", 8))
        main.addLayout(right, 1)
        self.body.addLayout(main, 1)
        bc = TacButton(_t("取消导出"), "secondary")
        bp = TacButton(_t("不打码，直接导出"), "secondary")
        bo = TacButton(_t("打码并导出"), "primary", en="REDACT")
        bc.clicked.connect(self.reject)
        bp.clicked.connect(lambda: self._done("plain"))
        bo.clicked.connect(lambda: self._done("redact"))
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bp)
        self.buttons.addWidget(bo)
        self._mute = False
        if self.groups:
            self.photos.setCurrentRow(0)

    def _done(self, code: str) -> None:
        self.result_code = code
        self.accept()

    @property
    def redact_style(self) -> str:
        return self.style.currentData()

    def boxes(self) -> dict[int, list[tuple]]:
        return {gid: [f.box for f in fs if f.on] for gid, fs in self.found.items() if any(f.on for f in fs)}

    def _image(self, g):
        if g.gid not in self._img:
            import numpy as np
            from ..core.imaging import load_preview
            try:
                im = load_preview(g.primary, g.primary_file.role, VIEW)[0].convert("RGB")
                self._img[g.gid] = QPixmap.fromImage(qimage_from_rgb(np.asarray(im)))
            except Exception:
                self._img[g.gid] = QPixmap()
        return self._img[g.gid]

    def _show(self, row: int) -> None:
        if not 0 <= row < len(self.groups):
            return
        g = self.groups[row]
        self._mute = True
        self.items.clear()
        for i, f in enumerate(self.found[g.gid]):
            it = QListWidgetItem(f"{i + 1}. {f.kind_name}" + (f"  {f.label}" if f.label != f.kind_name else ""))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if f.on else Qt.CheckState.Unchecked)
            self.items.addItem(it)
        self._mute = False
        self._draw(g)

    def _toggled(self, it: QListWidgetItem) -> None:
        if self._mute:
            return
        row = self.photos.currentRow()
        g = self.groups[row]
        f = self.found[g.gid][self.items.row(it)]
        f.on = it.checkState() == Qt.CheckState.Checked
        on = sum(1 for x in self.found[g.gid] if x.on)
        self.photos.item(row).setText(f"{g.name}\n" + _t("{0} 处 · 打码 {1}", len(self.found[g.gid]), on))
        self._draw(g)

    def _draw(self, g) -> None:
        base = self._image(g)
        if base.isNull():
            self.view.setText(_t("预览失败"))
            return
        pm = base.scaled(VIEW, VIEW, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = pm.width(), pm.height()
        f_font = QFont()
        f_font.setPointSizeF(9)
        f_font.setBold(True)
        qp.setFont(f_font)
        for i, f in enumerate(self.found[g.gid]):
            col = QColor(COLORS.get(f.kind, "#E0A21B"))
            r = QRectF(f.box[0] * W, f.box[1] * H, (f.box[2] - f.box[0]) * W, (f.box[3] - f.box[1]) * H)
            if f.on:
                fill = QColor(col)
                fill.setAlpha(110)
                qp.fillRect(r, fill)
            qp.setPen(QPen(col, 2, Qt.PenStyle.SolidLine if f.on else Qt.PenStyle.DashLine))
            qp.drawRect(r)
            tag = QRectF(r.left(), max(0.0, r.top() - 16), 22, 15)
            qp.fillRect(tag, col)
            qp.setPen(QColor("#FFFFFF"))
            qp.drawText(tag, Qt.AlignmentFlag.AlignCenter, str(i + 1))
        qp.end()
        self.view.setPixmap(pm)


def check_before_export(ctx, parent, groups: list):
    """导出前跑一遍体检。返回 (要打码的区域 {gid: 框}, 打码方式)；用户取消导出时返回 None。"""
    from .dialogs import info, run_with_progress
    lib = ctx.library

    def job(prog, cancel):
        return privacy.scan(groups, lib, lambda d, t, c: prog((d, t, c)), cancel)

    found, tb = run_with_progress(ctx, parent, _t("正在做隐私体检"), "PRIVACY", job)
    if tb:
        info(parent, _t("隐私体检出错"), f"<pre style='font-size:8pt'>{tb[-1200:]}</pre>", "ERROR")
        return None
    if found is None:
        return None
    text_model = not privacy.needs_text_model(lib)
    if not found:
        ctx.status.emit(_t("隐私体检：没有发现号码、证件、车牌、二维码或路人脸") +
                        ("" if text_model else _t("（没有文字识别模型，只查了二维码和人脸）")))
        return {}, "mosaic"
    d = PrivacyDialog(parent, groups, found, text_model)
    if d.exec() != QDialog.DialogCode.Accepted or d.result_code is None:
        return None
    if d.result_code == "plain":
        return {}, d.redact_style
    return d.boxes(), d.redact_style
