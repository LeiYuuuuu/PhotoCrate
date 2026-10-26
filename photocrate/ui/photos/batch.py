"""「相册」里的批量编辑：粘贴编辑（把拷贝的调整套到很多张）、统一色调（一组照片的颜色往一张参考照片靠）。

都和单张编辑一样：在原尺寸上重新计算，另存为「原名_编辑.jpg」（保留 EXIF），原片不动；做完重新扫描让新照片进相册。
预览用缩略图算，只是看个大概。
"""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QSlider,
                               QVBoxLayout)

from ..dialogs import _Base, info, run_with_progress
from ..widgets.deco import TacButton, caption
from .canvas import qimage_from_rgb
from ...edit import quick
from ...i18n import t as _t

PREVIEW = 300


def thumb_rgb(g, side: int = PREVIEW) -> np.ndarray | None:
    """缩略图 → float32 RGB（没有缩略图时读预览）。"""
    from PIL import Image
    try:
        if getattr(g, "thumb", None) and os.path.exists(g.thumb):
            im = Image.open(g.thumb)
        else:
            from ...core.imaging import load_preview
            im = load_preview(g.primary, g.primary_file.role, side)[0]
        im = im.convert("RGB")
        im.thumbnail((side, side))
        return np.asarray(im, np.float32) / 255.0
    except Exception:
        return None


def _pix(rgb: np.ndarray | None, side: int) -> QPixmap:
    if rgb is None:
        return QPixmap()
    pm = QPixmap.fromImage(qimage_from_rgb(rgb))
    return pm.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def save_batch(ctx, parent, groups: list, st: quick.QuickState, title: str) -> list[str]:
    """按 st 编辑每一张，另存新文件。返回写出的路径。"""
    from .detail import unique_path
    lib = ctx.library
    todo = [g for g in groups if g.files and not g.is_video]

    def job(prog, cancel):
        from ...edit import io as eio
        out, errors = [], []
        for i, g in enumerate(todo):
            if cancel.is_set():
                break
            prog((i, len(todo), g.name))
            try:
                doc = eio.open_group(g, lib.exiftool_path)
                src = quick.to_float(doc.layers[0].pixels)
                one = quick.for_photo(st, src)
                res = quick.apply(src, one, 1.0)
                path = unique_path(doc.source.path or g.primary, _t("_编辑"))
                eio.export_image(doc, path, quality=95, rgba=quick.to_rgba8(res))
                out.append(path)
            except Exception as e:
                errors.append(_t("{0}：{1}", g.name, e))
        prog((len(todo), len(todo), ""))
        return out, errors

    res, tb = run_with_progress(ctx, parent, title, "BATCH", job)
    if tb or res is None:
        info(parent, _t("存储失败"), f"<pre style='font-size:8pt'>{(tb or '')[-1200:]}</pre>", "ERROR")
        return []
    paths, errors = res
    msg = _t("已存储 <b>{0}</b> 张新照片（原名_编辑.jpg，原片未修改）。", len(paths))
    if errors:
        msg += _t("<br><br>{0} 张失败：<br>", len(errors)) + "<br>".join(errors[:6])
    ctx.status.emit(_t("{0}：已存储 {1} 张新照片", title, len(paths)))
    if paths:
        ctx.rescan_requested.emit()
    info(parent, title, msg, "COMPLETE")
    return paths


class PasteDialog(_Base):
    """粘贴编辑：勾选要套用的部分（裁剪与旋转默认不勾：每张构图不同），看第一张的效果，确认后批量存储。"""

    def __init__(self, parent, st: quick.QuickState, src_name: str, groups: list):
        super().__init__(parent, _t("粘贴编辑"), "PASTE EDITS")
        self.st = st
        self.groups = [g for g in groups if g.files and not g.is_video]
        self.body.addWidget(QLabel(_t("把「{0}」的编辑套到选中的 <b>{1}</b> 张照片上：", src_name, len(self.groups))))
        self.checks: dict[str, QCheckBox] = {}
        for key, name in quick.PASTE_PARTS:
            if key not in quick.parts_of(st):
                continue
            cb = QCheckBox(_t(name))
            cb.setChecked(key != "geometry")
            cb.toggled.connect(lambda _: self._timer.start())
            self.checks[key] = cb
            self.body.addWidget(cb)
        pr = QHBoxLayout()
        self.before = QLabel()
        self.after = QLabel()
        for lab in (self.before, self.after):
            lab.setFixedSize(PREVIEW, PREVIEW)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pr.addWidget(lab)
        self.body.addLayout(pr)
        self.note = caption(_t("左：原图　右：粘贴后（按缩略图预览）。每张另存为新照片，原片不会被修改。"), "faint", 8)
        self.body.addWidget(self.note)
        bc = TacButton(_t("取消"), "secondary")
        self.bo = TacButton(_t("存储 {0} 张新照片", len(self.groups)), "primary", en="SAVE")
        bc.clicked.connect(self.reject)
        self.bo.clicked.connect(self.accept)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(self.bo)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._preview)
        self._src = thumb_rgb(self.groups[0]) if self.groups else None
        self.before.setPixmap(_pix(self._src, PREVIEW))
        self._preview()

    def state(self) -> quick.QuickState:
        return quick.paste_state(self.st, [k for k, cb in self.checks.items() if cb.isChecked()])

    def _preview(self) -> None:
        st = self.state()
        self.bo.setEnabled(bool(self.groups) and not st.is_identity())
        if self._src is None:
            return
        one = quick.for_photo(st, self._src)
        self.after.setPixmap(_pix(quick.apply(self._src, one, PREVIEW / 4000), PREVIEW))


class MatchDialog(_Base):
    """统一色调：在选中的照片里点一张当参考，其余照片的颜色往它靠（强度可调）。"""

    def __init__(self, parent, groups: list):
        super().__init__(parent, _t("统一色调"), "MATCH COLORS")
        self.groups = [g for g in groups if g.files and not g.is_video]
        self.resize(900, 620)
        self.body.addWidget(QLabel(_t("点一张当<b>参考照片</b>，其余 {0} 张的颜色和明暗往它靠：", max(0, len(self.groups) - 1))))
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(120, 120))
        self.list.setFixedHeight(170)
        self.list.setFlow(QListWidget.Flow.LeftToRight)
        self.list.setWrapping(False)
        self.small: list[np.ndarray | None] = []
        for g in self.groups:
            rgb = thumb_rgb(g, 220)
            self.small.append(rgb)
            it = QListWidgetItem(QIcon(_pix(rgb, 120)), g.name)
            self.list.addItem(it)
        self.list.currentRowChanged.connect(lambda _: self._timer.start())
        self.body.addWidget(self.list)
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("强度")))
        self.amount = QSlider(Qt.Orientation.Horizontal)
        self.amount.setRange(10, 100)
        self.amount.setValue(70)
        self.amount_lab = QLabel("70%")
        self.amount.valueChanged.connect(lambda v: (self.amount_lab.setText(f"{v}%"), self._timer.start()))
        r.addWidget(self.amount, 1)
        r.addWidget(self.amount_lab)
        self.body.addLayout(r)
        self.prev_row = QHBoxLayout()
        self.prev_labels = []
        for _ in range(4):
            lab = QLabel()
            lab.setFixedSize(200, 200)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.prev_row.addWidget(lab)
            self.prev_labels.append(lab)
        self.body.addLayout(self.prev_row)
        self.body.addWidget(caption(_t("下面是调整后的其余几张（按缩略图预览）。存成新照片后，还可以在单张编辑里接着调；"
                                       "这份“统一色调”也会记成拷贝的编辑，可以再粘贴到别的照片。"), "faint", 8))
        bc = TacButton(_t("取消"), "secondary")
        self.bo = TacButton(_t("存储新照片"), "primary", en="SAVE")
        bc.clicked.connect(self.reject)
        self.bo.clicked.connect(self.accept)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(self.bo)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._preview)
        self.list.setCurrentRow(0)
        self._ref_stats: dict | None = None
        self._ref_row = -1

    def reference(self):
        i = self.list.currentRow()
        return self.groups[i] if 0 <= i < len(self.groups) else None

    def ref_stats(self) -> dict | None:
        """参考照片的 Lab 统计（用比缩略图大一些的预览算，存储时也用它）。"""
        from ...edit.colormatch import lab_stats
        i = self.list.currentRow()
        if i != self._ref_row:
            self._ref_row = i
            rgb = thumb_rgb(self.groups[i], 640) if 0 <= i < len(self.groups) else None
            self._ref_stats = lab_stats(rgb) if rgb is not None else None
        return self._ref_stats

    def state(self) -> quick.QuickState:
        ref = self.reference()
        stats = self.ref_stats()
        if ref is None or stats is None:
            return quick.QuickState()
        return quick.QuickState(match={**stats, "amount": self.amount.value() / 100.0, "ref": ref.name})

    def targets(self) -> list:
        ref = self.reference()
        return [g for g in self.groups if g is not ref]

    def _preview(self) -> None:
        st = self.state()
        others = [i for i in range(len(self.groups)) if i != self.list.currentRow()]
        self.bo.setText(_t("存储 {0} 张新照片", len(others)))
        self.bo.setEnabled(bool(others) and st.match_on())
        for k, lab in enumerate(self.prev_labels):
            if k < len(others) and self.small[others[k]] is not None:
                lab.setPixmap(_pix(quick.apply(self.small[others[k]], st, 0.05), 200))
            else:
                lab.clear()


def paste_edits(page, groups: list) -> None:
    if not page.copied_edit or not groups:
        return
    st, name = page.copied_edit
    d = PasteDialog(page, st, name, groups)
    if d.exec() == QDialog.DialogCode.Accepted:
        save_batch(page.ctx, page, d.groups, d.state(), _t("粘贴编辑"))


def match_colors(page, groups: list) -> None:
    stills = [g for g in groups if g.files and not g.is_video]
    if len(stills) < 2:
        info(page, _t("统一色调"), _t("至少选两张照片：一张当参考，其余的往它靠。"))
        return
    d = MatchDialog(page, stills)
    if d.exec() == QDialog.DialogCode.Accepted:
        st = d.state()
        page.copied_edit = (st.copy(), d.reference().name)
        save_batch(page.ctx, page, d.targets(), st, _t("统一色调"))
