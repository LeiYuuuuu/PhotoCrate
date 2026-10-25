"""动作面板与批处理对话框。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QProgressDialog, QVBoxLayout)

from ...edit import actions as A
from ...edit.io import OPEN_FILTER, PROJECT_EXT
from ...i18n import t as _t
from ..dialogs import _Base, info
from ..widgets.deco import TacButton, caption
from .panels import ParamSlider


def load_actions(win) -> dict:
    s = win.ctx.settings if win.ctx is not None else None
    acts = (s.get("editor_actions") if s else None) or getattr(win, "_actions_mem", None)
    if not acts:
        acts = {_t("网页用（长边 2048、锐化）"): [{"op": "long_side", "px": 2048},
                                              {"op": "filter", "fid": "unsharp",
                                               "params": {"amount": 60, "radius": 0.8, "threshold": 2}}],
                _t("黑白胶片"): [{"op": "adjust", "name": _t("黑白"), "params": {"bw": {"on": True}, "contrast": 15,
                                                                            "grain": 20}}]}
    return dict(acts)


def save_actions(win, acts: dict) -> None:
    win._actions_mem = dict(acts)
    if win.ctx is not None:
        win.ctx.settings.set("editor_actions", dict(acts))


class ActionsDialog(_Base):
    def __init__(self, win):
        super().__init__(win, _t("动作"), "ACTIONS")
        self.win = win
        self.setModal(False)
        self.acts = load_actions(win)
        row = QHBoxLayout()
        self.list = QListWidget()
        self.list.setFixedWidth(240)
        self.steps = QListWidget()
        row.addWidget(self.list)
        row.addWidget(self.steps, 1)
        self.body.addLayout(row)
        self.state = caption("", "faint", 8.5)
        self.body.addWidget(self.state)
        self.body.addWidget(caption(_t("录制：点「新建并录制」，然后像平常一样操作（滤镜、调整图层、图像大小、旋转、拼合…），"
                                       "做完点「停止」。以后对别的照片点「播放」一次做完同样的事，或用「批处理」处理一整个文件夹。"),
                                    "faint", 8.5))
        r = QHBoxLayout()
        self.b_new = TacButton(_t("新建并录制"), "secondary", height=30)
        self.b_stop = TacButton(_t("停止录制"), "secondary", height=30)
        self.b_play = TacButton(_t("播放"), "primary", height=30)
        self.b_del = TacButton(_t("删除"), "secondary", height=30)
        self.b_batch = TacButton(_t("批处理…"), "secondary", height=30)
        for b in (self.b_new, self.b_stop, self.b_play, self.b_del, self.b_batch):
            r.addWidget(b)
        self.body.addLayout(r)
        self.b_new.clicked.connect(self._new)
        self.b_stop.clicked.connect(self._stop)
        self.b_play.clicked.connect(self._play)
        self.b_del.clicked.connect(self._delete)
        self.b_batch.clicked.connect(self._batch)
        self.list.currentRowChanged.connect(lambda _: self._show())
        bc = TacButton(_t("关闭"), "secondary")
        bc.clicked.connect(self.close)
        self.buttons.addWidget(bc)
        self._refresh()
        self.resize(720, 460)

    def _refresh(self, select: str | None = None):
        cur = select or (self.list.currentItem().text() if self.list.currentItem() else None)
        self.list.clear()
        for n in self.acts:
            self.list.addItem(n)
        names = list(self.acts)
        if cur in names:
            self.list.setCurrentRow(names.index(cur))
        elif names:
            self.list.setCurrentRow(0)
        rec = self.win.recording
        self.state.setText(_t('正在录制「{0}」：已录 {1} 步', rec["name"], len(rec["steps"])) if rec else "")
        self.b_stop.setEnabled(rec is not None)
        self.b_new.setEnabled(rec is None)
        self._show()

    def _show(self):
        self.steps.clear()
        it = self.list.currentItem()
        if it is None:
            return
        rec = self.win.recording
        steps = rec["steps"] if rec and rec["name"] == it.text() else self.acts.get(it.text(), [])
        for st in steps:
            self.steps.addItem(A.describe(st))

    def _new(self):
        name, ok = QInputDialog.getText(self, _t("新建动作"), _t("动作名称："), text=_t("动作 {0}", len(self.acts) + 1))
        if not ok or not name.strip():
            return
        self.win.start_recording(name.strip())
        self.acts[name.strip()] = []
        self._refresh(name.strip())

    def _stop(self):
        name, steps = self.win.stop_recording()
        if name:
            self.acts[name] = steps
            save_actions(self.win, self.acts)
        self._refresh(name)

    def _current(self):
        it = self.list.currentItem()
        return (it.text(), self.acts.get(it.text(), [])) if it else (None, [])

    def _play(self):
        name, steps = self._current()
        if not steps:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            n = A.play(self.win.doc, steps)
        finally:
            QApplication.restoreOverrideCursor()
        self.win.status_msg(_t('已播放动作「{0}」：{1} 步', name, n))
        self.win.props.rebuild()

    def _delete(self):
        name, _ = self._current()
        if name:
            self.acts.pop(name, None)
            save_actions(self.win, self.acts)
            self._refresh()

    def _batch(self):
        name, steps = self._current()
        if not steps:
            return
        BatchDialog(self.win, name, steps).exec()

    def showEvent(self, e):
        self._refresh()
        super().showEvent(e)


class BatchDialog(_Base):
    FORMATS = [(".jpg", "JPEG"), (".png", "PNG"), (".tif", "TIFF"), (".webp", "WebP")]

    def __init__(self, win, name: str, steps: list):
        super().__init__(win, _t("批处理"), "BATCH")
        self.win, self.steps = win, steps
        self.paths: list[str] = []
        self.body.addWidget(QLabel(_t('动作：{0}（{1} 步）', name, len(steps))))
        r = QHBoxLayout()
        self.src = QLabel(_t("还没有选择照片"))
        b = TacButton(_t("选择照片…"), "secondary", height=30)
        b.clicked.connect(self._pick)
        r.addWidget(self.src, 1)
        r.addWidget(b)
        self.body.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("存到：")))
        self.out = QLineEdit(os.path.join(os.path.expanduser("~"), _t("批处理结果")))
        r.addWidget(self.out, 1)
        b2 = TacButton(_t("浏览…"), "secondary", height=30)
        b2.clicked.connect(self._pick_out)
        r.addWidget(b2)
        self.body.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("格式：")))
        self.fmt = QComboBox()
        for e, t in self.FORMATS:
            self.fmt.addItem(t, e)
        r.addWidget(self.fmt)
        r.addWidget(QLabel(_t("文件名后缀：")))
        self.suffix = QLineEdit("")
        self.suffix.setFixedWidth(120)
        r.addWidget(self.suffix)
        r.addStretch(1)
        self.body.addLayout(r)
        self.quality = ParamSlider(_t("质量"), 50, 100, 90)
        self.body.addWidget(self.quality)
        self.body.addWidget(caption(_t("原片不会被修改：每张照片处理后另存到上面的文件夹（重名自动加编号）。RAW 按默认设置显影。"),
                                    "faint", 8.5))
        bc = TacButton(_t("取消"), "secondary")
        bo = TacButton(_t("开始"), "primary")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self._run)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)
        self.resize(620, 10)

    def _pick(self):
        ps, _ = QFileDialog.getOpenFileNames(self, _t("选择要批处理的照片"), os.path.expanduser("~"), OPEN_FILTER)
        self.paths = [p for p in ps if not p.lower().endswith(PROJECT_EXT)]
        self.src.setText(_t('已选 {0} 张', len(self.paths)))

    def _pick_out(self):
        p = QFileDialog.getExistingDirectory(self, _t("存到"), self.out.text())
        if p:
            self.out.setText(os.path.normpath(p))

    def run(self) -> list:
        pd = QProgressDialog(_t("正在批处理…"), _t("取消"), 0, max(1, len(self.paths)), self)
        pd.setWindowModality(Qt.WindowModality.WindowModal)
        pd.setMinimumDuration(0)

        def prog(i, n, name):
            pd.setValue(i)
            pd.setLabelText(_t('正在处理 {0} / {1}：{2}', min(i + 1, n), n, name))
            QApplication.processEvents()
        res = A.batch(self.paths, self.steps, self.out.text().strip(), self.fmt.currentData(),
                      int(self.quality.value()), self.suffix.text().strip(), progress=prog, cancel=pd.wasCanceled)
        pd.close()
        return res

    def _run(self):
        if not self.paths or not self.out.text().strip():
            self.win.status_msg(_t("先选择照片和保存位置"))
            return
        res = self.run()
        bad = [r for r in res if r[1].startswith("ERROR")]
        self.accept()
        info(self.win, _t("批处理完成"), _t('处理了 {0} 张，失败 {1} 张。<br>结果在：{2}', len(res) - len(bad), len(bad),
                                         self.out.text().strip()) + ("".join(f"<br>{os.path.basename(p)}：{e}" for p, e in bad[:5])))
