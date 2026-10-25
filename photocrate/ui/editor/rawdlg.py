"""RAW 显影对话框（类似 Camera Raw 的第一步）：白平衡、曝光、高光、解马赛克、降噪、色差，左边是实时预览。

预览用半尺寸显影（快很多），点「打开」时按原尺寸 16 位显影。显影之后的调色交给修图窗口的调整图层。
"""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from ...edit import raw as R
from ...i18n import t as _t
from ..dialogs import _Base, info
from ..widgets.deco import TacButton, caption
from .panels import ParamSlider


class _Preview(QLabel):
    picked = Signal(float, float)          # 点击位置（0–1 的相对坐标）

    def __init__(self):
        super().__init__()
        self.setMinimumSize(QSize(560, 420))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: #1E2023;")
        self.picking = False
        self._img_rect = None

    def set_image(self, rgb8: np.ndarray) -> None:
        h, w = rgb8.shape[:2]
        img = QImage(rgb8.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
        pm = QPixmap.fromImage(img).scaled(self.width() - 8, self.height() - 8, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(pm)
        x0, y0 = (self.width() - pm.width()) / 2, (self.height() - pm.height()) / 2
        self._img_rect = (x0, y0, pm.width(), pm.height())

    def mousePressEvent(self, e):
        if self.picking and self._img_rect is not None:
            x0, y0, w, h = self._img_rect
            u, v = (e.position().x() - x0) / w, (e.position().y() - y0) / h
            if 0 <= u <= 1 and 0 <= v <= 1:
                self.picked.emit(u, v)
        super().mousePressEvent(e)


class RawDialog(_Base):
    PREVIEW_SIDE = 1400

    def __init__(self, parent, path: str, opts: R.RawOptions | None = None):
        super().__init__(parent, _t("RAW 显影"), "CAMERA RAW")
        self.path = path
        self.opts = opts or R.RawOptions()
        self.result_rgb: np.ndarray | None = None
        self._prev16: np.ndarray | None = None
        self._prev_mul: list[float] | None = None
        self.setModal(True)
        try:
            self.info = R.camera_wb(path)
        except Exception as e:                                 # LibRaw 打不开：调用方退回内嵌预览
            self.info = None
            self.error = str(e)
            return
        self.error = None
        row = QHBoxLayout()
        self.view = _Preview()
        self.view.picked.connect(self._picked)
        row.addWidget(self.view, 1)
        side = QVBoxLayout()
        side.setSpacing(8)
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        self.wb = QComboBox()
        for k, t in R.WB_MODES:
            self.wb.addItem(t, k)
        self.wb.setCurrentIndex(max(0, self.wb.findData(self.opts.wb)))
        self.pick = TacButton(_t("吸管：点一下画面里的灰色 / 白色"), "secondary", height=28)
        self.pick.clicked.connect(self._start_pick)
        self.hl = QComboBox()
        for k, t in R.HIGHLIGHTS:
            self.hl.addItem(t, k)
        self.hl.setCurrentIndex(max(0, self.hl.findData(self.opts.highlight)))
        self.algo = QComboBox()
        for k, t in R.DEMOSAIC:
            self.algo.addItem(t, k)
        self.algo.setCurrentIndex(max(0, self.algo.findData(self.opts.demosaic)))
        self.nr = QComboBox()
        for k, t in R.DENOISE:
            self.nr.addItem(t, k)
        self.nr.setCurrentIndex(max(0, self.nr.findData(self.opts.denoise)))
        for r, (label, w) in enumerate(((_t("白平衡"), self.wb), (_t("高光"), self.hl), (_t("解马赛克"), self.algo),
                                        (_t("降噪"), self.nr))):
            g.addWidget(QLabel(label), r, 0)
            g.addWidget(w, r, 1)
        side.addLayout(g)
        side.addWidget(self.pick)
        self.exposure = ParamSlider(_t("曝光"), -2.0, 3.0, 0.0, 2, " EV")
        self.exposure.set_value(self.opts.exposure)
        side.addWidget(self.exposure)
        self.auto = QCheckBox(_t("自动亮度（只让 0.1% 的高光溢出）"))
        self.auto.setChecked(self.opts.auto_bright)
        side.addWidget(self.auto)
        self.ca_r = ParamSlider(_t("红 / 青边"), -10, 10, 0, 0, "")
        self.ca_b = ParamSlider(_t("蓝 / 黄边"), -10, 10, 0, 0, "")
        side.addWidget(self.ca_r)
        side.addWidget(self.ca_b)
        self.status = caption("", "faint", 8.5)
        self.status.setWordWrap(True)
        side.addWidget(self.status)
        side.addWidget(caption(_t("显影成 16 位图像：高光、暗部的细节都保留，之后在修图窗口里用调整图层继续调色不会出断层。"
                                  "原 RAW 文件不会被修改。"), "faint", 8.5))
        side.addStretch(1)
        row.addLayout(side)
        self.body.addLayout(row)
        bc = TacButton(_t("取消"), "secondary")
        bo = TacButton(_t("打开"), "primary", en="OPEN")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self._ok)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(250)
        self._t.timeout.connect(self._update)
        for w in (self.wb, self.hl, self.algo, self.nr):
            w.currentIndexChanged.connect(lambda _=0: self._t.start())
        self.auto.toggled.connect(lambda _=False: self._t.start())
        for s in (self.exposure, self.ca_r, self.ca_b):
            s.changed.connect(lambda _=0: self._t.start())
        self.resize(1080, 640)
        QTimer.singleShot(0, self._update)

    def exec(self):
        if self.info is None:
            info(self.parent(), _t("RAW 显影"), _t('LibRaw 打不开这个 RAW 文件：<br>{0}', self.error or ""), "ERROR")
            return _Base.DialogCode.Rejected
        return super().exec()

    def options(self, half: bool = False) -> R.RawOptions:
        wb = self.wb.currentData()
        return R.RawOptions(wb=wb, wb_mul=self.opts.wb_mul if wb == "custom" else None,
                            exposure=float(self.exposure.value()), auto_bright=self.auto.isChecked(),
                            highlight=self.hl.currentData(), demosaic="LINEAR" if half else self.algo.currentData(),
                            denoise=self.nr.currentData(), ca_red=1 + 0.0005 * self.ca_r.value(),
                            ca_blue=1 + 0.0005 * self.ca_b.value(), half=half)

    def _base_mul(self) -> list[float]:
        o = self.options()
        if o.wb == "custom" and o.wb_mul:
            return list(o.wb_mul)
        if o.wb == "daylight" and self.info and any(self.info["daylight"]):
            return list(self.info["daylight"])
        return list(self.info["camera"]) if self.info and any(self.info["camera"]) else [1.0, 1.0, 1.0, 1.0]

    def _update(self) -> None:
        o = self.options(half=True)
        if o.wb == "custom" and not o.wb_mul:
            self.status.setText(_t("自定义白平衡：点「吸管」，再点一下画面里本来应该是灰色或白色的地方。"))
            o.wb = "camera"
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._prev16 = R.develop(self.path, o, self.PREVIEW_SIDE)
            self._prev_mul = self._base_mul() if o.wb != "auto" else None
        except Exception as e:
            self.status.setText(_t('预览失败：{0}', e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        rgb8 = np.ascontiguousarray((self._prev16 >> 8).astype(np.uint8))
        self.view.set_image(rgb8)
        m = self._prev_mul
        clip = float((self._prev16.max(axis=2) >= 65000).mean()) * 100
        txt = _t('预览 {0} × {1}（原尺寸 {2} × {3}）', self._prev16.shape[1], self._prev16.shape[0],
                 self.info["width"], self.info["height"])
        if m:
            txt += _t("  ·  白平衡系数 R {0:.2f} G {1:.2f} B {2:.2f}", m[0], m[1], m[2])
        if clip > 0.05:
            txt += _t("  ·  {0:.1f}% 的像素过曝", clip)
        self.status.setText(txt)

    def _start_pick(self) -> None:
        self.view.picking = True
        self.view.setCursor(Qt.CursorShape.CrossCursor)
        self.status.setText(_t("点一下画面里本来应该是灰色或白色的地方（不要点过曝的纯白）"))

    def _picked(self, u: float, v: float) -> None:
        self.view.picking = False
        self.view.unsetCursor()
        if self._prev16 is None:
            return
        h, w = self._prev16.shape[:2]
        x, y = int(u * (w - 1)), int(v * (h - 1))
        if self._prev16[y, x].max() >= 64000:
            self.status.setText(_t("这里过曝了，看不出颜色：换一个稍暗的灰色 / 白色的地方"))
            return
        base = self._prev_mul or self._base_mul()
        self.opts.wb_mul = R.pick_white(self._prev16, x, y, base, r=max(2, w // 300))
        self.wb.blockSignals(True)
        self.wb.setCurrentIndex(self.wb.findData("custom"))
        self.wb.blockSignals(False)
        self._update()

    def _ok(self) -> None:
        o = self.options()
        if o.wb == "custom" and not o.wb_mul:
            o.wb = "camera"
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.result_rgb = R.develop(self.path, o)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            info(self, _t("RAW 显影"), _t('显影失败：{0}', e), "ERROR")
            return
        QApplication.restoreOverrideCursor()
        self.final_opts = o
        self.accept()


def develop_doc(parent, path: str, name: str | None = None, gid=None, exif: bytes | None = None):
    """弹出 RAW 显影对话框，确定后返回 16 位文档；取消返回 None；LibRaw 打不开时返回 False（调用方退回内嵌预览）。"""
    from ...edit import io as eio
    from ...edit.document import SourceInfo
    dlg = RawDialog(parent, path)
    if dlg.info is None:
        return False
    if dlg.exec() != RawDialog.DialogCode.Accepted or dlg.result_rgb is None:
        return None
    rgb = dlg.result_rgb
    rgba = np.ascontiguousarray(np.dstack([rgb, np.full(rgb.shape[:2], 65535, np.uint16)]))
    doc = eio._new_doc(rgba)
    doc.source = SourceInfo(path=path, name=name or os.path.basename(path),
                            exif=exif if exif is not None else R.embedded_exif(path), gid=gid)
    return doc
