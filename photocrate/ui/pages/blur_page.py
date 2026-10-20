"""04 清晰度检测：调节阈值、实时查看被判为模糊的照片，可一键移入「待确认_模糊」文件夹。"""
from __future__ import annotations

import math
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QRadioButton, QSlider)

from ...analyzers.builtin import blur_state
from ...ops.planner import build_plan
from ...partition.base import PartitionContext
from ...partition.engine import PartitionSpec, Rule
from .. import theme
from ..dialogs import PlanDialog, info
from ..widgets.deco import Panel, StatCard, TacButton, caption, micro_label
from ..widgets.thumbgrid import ThumbGrid
from .base import Page

LO, HI = 5.0, 2000.0


def _to_slider(v: float) -> int:
    return int(round(1000 * math.log(max(LO, min(HI, v)) / LO) / math.log(HI / LO)))


def _from_slider(i: int) -> float:
    return LO * (HI / LO) ** (i / 1000)


class BlurPage(Page):
    page_id = "blur"
    index = "04"
    title = "清晰度检测"
    en = "Sharpness scan // laplacian variance"
    sub = "分数越低越模糊。拖动阈值，观察哪些照片被标红，找到适合你照片的值；结果只是建议，绝不会自动删除。"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        p = theme.current()
        top = QHBoxLayout()
        top.setSpacing(12)
        ctl = Panel("阈值", "Threshold")
        ctl.setFixedWidth(430)
        r = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(LO, HI)
        self.spin.setDecimals(0)
        self.spin.setFixedWidth(90)
        thr = float(ctx.settings.get("blur_threshold", 60))
        self.slider.setValue(_to_slider(thr))
        self.spin.setValue(thr)
        r.addWidget(micro_label("blurry"))
        r.addWidget(self.slider, 1)
        r.addWidget(micro_label("sharp"))
        r.addWidget(self.spin)
        ctl.body.addLayout(r)
        fr = QHBoxLayout()
        self.f_all = QRadioButton("全部")
        self.f_blur = QRadioButton("只看模糊")
        self.f_unk = QRadioButton("无法判断")
        bg = QButtonGroup(self)
        for b in (self.f_all, self.f_blur, self.f_unk):
            bg.addButton(b)
            fr.addWidget(b)
        self.f_blur.setChecked(True)
        fr.addStretch(1)
        ctl.body.addLayout(fr)
        ctl.body.addWidget(caption("算法：在长边 1024 像素的预览上计算拉普拉斯方差，分 4×4 小块取最清晰的两块，"
                                   "因此“主体清晰、背景虚化”不会被误判。夜景高噪点照片分数会偏高。预览太小（<640px）的标为“无法判断”。",
                                   "faint", 8))
        top.addWidget(ctl)

        cards = Panel("结果", "Result")
        cr = QHBoxLayout()
        self.c_blur = StatCard("模糊", "Blurry", accent=p.danger)
        self.c_sharp = StatCard("清晰", "Sharp", accent=p.ok)
        self.c_unk = StatCard("无法判断", "Unknown", accent=p.ink3)
        for c in (self.c_blur, self.c_sharp, self.c_unk):
            cr.addWidget(c)
        cards.body.addLayout(cr)
        ar = QHBoxLayout()
        ar.addWidget(QLabel("移入文件夹名："))
        self.folder_name = QLineEdit("待确认_模糊")
        self.folder_name.setFixedWidth(160)
        ar.addWidget(self.folder_name)
        ar.addStretch(1)
        self.b_move = TacButton("把模糊照片移入文件夹…", "primary", en="ISOLATE", height=36)
        self.b_move.clicked.connect(self.isolate)
        ar.addWidget(self.b_move)
        cards.body.addLayout(ar)
        top.addWidget(cards, 1)
        self.root.addLayout(top)

        gp = Panel("照片", "Gallery // sorted by score", "")
        self.gpanel = gp
        self.grid = ThumbGrid(selectable=False)
        self.grid.delegate.show_score = True
        self.grid.delegate.highlight_blurry = True
        gp.body.addWidget(self.grid, 1)
        self.root.addWidget(gp, 1)

        self._save = QTimer(self)
        self._save.setSingleShot(True)
        self._save.setInterval(400)
        self._save.timeout.connect(lambda: self.ctx.set_setting("blur_threshold", float(self.spin.value())))
        self.slider.valueChanged.connect(self._slider_moved)
        self.spin.valueChanged.connect(self._spin_changed)
        for b in (self.f_all, self.f_blur, self.f_unk):
            b.toggled.connect(lambda on: on and self.refresh())
        ctx.library_changed.connect(self.refresh)
        ctx.files_changed.connect(self.refresh)

    def on_show(self):
        self.refresh()

    def _slider_moved(self, i):
        v = _from_slider(i)
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self._changed()

    def _spin_changed(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(_to_slider(v))
        self.slider.blockSignals(False)
        self._changed()

    def _changed(self):
        self.refresh()
        self._save.start()

    def threshold(self) -> float:
        return float(self.spin.value())

    def refresh(self):
        thr = self.threshold()
        gs = [g for g in self.ctx.library.groups if g.files]
        states = {g.gid: blur_state(g, thr) for g in gs}
        n = len(gs) or 1
        nb = sum(1 for v in states.values() if v == "blurry")
        ns = sum(1 for v in states.values() if v == "sharp")
        nu = len(gs) - nb - ns
        self.c_blur.set(str(nb), nb / n)
        self.c_sharp.set(str(ns), ns / n)
        self.c_unk.set(str(nu), nu / n)
        if self.f_blur.isChecked():
            show = [g for g in gs if states[g.gid] == "blurry"]
        elif self.f_unk.isChecked():
            show = [g for g in gs if states[g.gid] == "unknown"]
        else:
            show = gs
        show.sort(key=lambda g: (g.feat("blur", "score") is None, g.feat("blur", "score") or 0))
        self.grid.delegate.blur_threshold = thr
        self.grid.settings_snapshot = self.ctx.settings.snapshot()
        self.grid.set_groups(show)
        self.gpanel.set_meta(f"{len(show)} ITEMS · THRESHOLD {thr:.0f}")
        self.b_move.setEnabled(nb > 0)

    def isolate(self):
        thr = self.threshold()
        self.ctx.set_setting("blur_threshold", thr)
        groups = [g for g in self.ctx.library.groups if g.files and blur_state(g, thr) == "blurry"]
        if not groups:
            return
        start = self.ctx.settings.get("target_dir") or (groups[0].folder if groups else os.path.expanduser("~"))
        d = QFileDialog.getExistingDirectory(self, "模糊照片的「待确认」文件夹放在哪里？", start)
        if not d:
            return
        name = self.folder_name.text().strip() or "待确认_模糊"
        spec = PartitionSpec("rules", [Rule("fixed", {"name": name})])
        plan = build_plan(groups, spec, PartitionContext(self.ctx.settings.snapshot()), os.path.normpath(d),
                          "move", "rename", "none")
        dlg = PlanDialog(self, plan)
        if dlg.exec() != PlanDialog.DialogCode.Accepted:
            return
        # 复用分区页的执行流程
        page = self.window().page("partition") if hasattr(self.window(), "page") else None
        if page is not None:
            page.execute(plan)
        else:
            info(self, "无法执行", "未找到执行器")
