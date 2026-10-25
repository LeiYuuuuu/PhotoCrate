"""时间轴（帧动画）：从图层建立帧、记录当前显示状态为一帧、预览播放、导出 GIF / MP4、导入视频帧。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QListWidget, QSpinBox

from ...edit import anim
from ...i18n import t as _t
from ..dialogs import _Base, info
from ..widgets.deco import TacButton, caption


class TimelineDialog(_Base):
    def __init__(self, win):
        super().__init__(win, _t("时间轴（帧动画）"), "TIMELINE")
        self.win = win
        self.setModal(False)
        self.list = QListWidget()
        self.list.setFlow(QListWidget.Flow.LeftToRight)
        self.list.setFixedHeight(64)
        self.list.currentRowChanged.connect(self._show_frame)
        self.body.addWidget(self.list)
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("这一帧停留：")))
        self.delay = QSpinBox()
        self.delay.setRange(10, 10000)
        self.delay.setSuffix(" ms")
        self.delay.setValue(100)
        self.delay.valueChanged.connect(self._set_delay)
        r.addWidget(self.delay)
        r.addStretch(1)
        self.body.addLayout(r)
        r = QHBoxLayout()
        for text, fn in ((_t("从图层建立帧"), self._from_layers), (_t("添加帧（当前显示）"), self._add),
                         (_t("删除帧"), self._delete), (_t("播放 / 停止"), self._play),
                         (_t("导入视频帧…"), self._video), (_t("导出 GIF / MP4…"), self._export)):
            b = TacButton(text, "secondary", height=30)
            b.clicked.connect(fn)
            r.addWidget(b)
        self.body.addLayout(r)
        self.body.addWidget(caption(_t("每一帧记住“哪些图层显示”。「从图层建立帧」：每个图层一帧（最底下的图层当背景）；"
                                       "也可以手动点眼睛设好显示的图层，再「添加帧」。导入视频帧会把视频每隔几帧取一张作为图层。"),
                                    "faint", 8.5))
        bc = TacButton(_t("关闭"), "secondary")
        bc.clicked.connect(self.close)
        self.buttons.addWidget(bc)
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._refresh()
        self.resize(820, 10)

    @property
    def doc(self):
        return self.win.doc

    def _refresh(self, sel: int | None = None):
        self.list.blockSignals(True)
        self.list.clear()
        for i, f in enumerate(self.doc.frames):
            self.list.addItem(_t('帧 {0}\n{1} ms', i + 1, f.get("delay", 100)))
        self.list.blockSignals(False)
        if self.doc.frames:
            self.list.setCurrentRow(min(len(self.doc.frames) - 1, sel if sel is not None else 0))

    def _show_frame(self, i: int):
        if not 0 <= i < len(self.doc.frames):
            return
        f = self.doc.frames[i]
        show = set(f.get("show") or [])
        for l in self.doc.layers:                       # 只改显示（不进历史），方便在这一帧上修图
            if l.kind != "group":
                l.visible = l.id in show
                l.version += 1
        self.doc.version += 1
        self.doc.emit("layers")
        self.delay.blockSignals(True)
        self.delay.setValue(int(f.get("delay", 100)))
        self.delay.blockSignals(False)

    def _set_delay(self, v):
        i = self.list.currentRow()
        if 0 <= i < len(self.doc.frames):
            fr = [dict(f) for f in self.doc.frames]
            fr[i]["delay"] = int(v)
            self.doc.frames = fr
            self.list.item(i).setText(_t('帧 {0}\n{1} ms', i + 1, v))

    def _from_layers(self):
        anim.frames_from_layers(self.doc, self.delay.value())
        self._refresh()

    def _add(self):
        anim.add_current(self.doc, self.delay.value())
        self._refresh(len(self.doc.frames) - 1)

    def _delete(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.doc.frames):
            with self.doc.edit(_t("删除帧"), kind="guides"):
                self.doc.frames = [f for k, f in enumerate(self.doc.frames) if k != i]
            self._refresh(max(0, i - 1))

    def _play(self):
        if self._t.isActive() or not self.doc.frames:
            self._t.stop()
            return
        self._t.start(int(self.doc.frames[self.list.currentRow() if self.list.currentRow() >= 0 else 0].get("delay", 100)))

    def _tick(self):
        n = len(self.doc.frames)
        if not n:
            self._t.stop()
            return
        i = (self.list.currentRow() + 1) % n
        self.list.setCurrentRow(i)
        self._t.setInterval(int(self.doc.frames[i].get("delay", 100)))

    def _video(self):
        p, _ = QFileDialog.getOpenFileName(self, _t("选择视频"), os.path.expanduser("~"),
                                           _t("视频 (*.mp4 *.mov *.avi *.mkv *.m4v *.mts *.m2ts)"))
        if not p:
            return
        try:
            n = anim.import_video(self.doc, p, every=5, max_frames=60)
        except Exception as e:
            info(self, _t("导入视频帧"), str(e), "ERROR")
            return
        self.win.status_msg(_t('已导入 {0} 帧（每 5 帧取 1 帧，最多 60 帧）', n))
        self._refresh()

    def _export(self):
        start = os.path.splitext(self.doc.source.path or os.path.expanduser(_t("~/动画")))[0] + _t("_动画") + ".gif"
        p, _ = QFileDialog.getSaveFileName(self, _t("导出动画"), start, "GIF (*.gif);;MP4 (*.mp4)")
        if not p:
            return
        if not os.path.splitext(p)[1]:
            p += ".gif"
        n = anim.export(self.doc, p)
        self.win.status_msg(_t('已导出 {0} 帧：{1}', n, p))

    def closeEvent(self, e):
        self._t.stop()
        super().closeEvent(e)

