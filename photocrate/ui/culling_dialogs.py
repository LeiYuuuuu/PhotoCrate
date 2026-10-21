"""「挑片」页的对话框：清理 RAW（放入回收站）与批量重命名。两者都是“先预览、再确认、可撤销”。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QRadioButton, QSpinBox, QVBoxLayout)

from ..ops.cleanup import MODES, build_cleanup_plan
from ..ops.pathsafe import human_size
from ..ops.renamer import build_rename_plan
from ..partition.base import PartitionContext
from . import theme
from .dialogs import _Base, ask
from .timeplace_dialogs import _RowsModel, _table
from .widgets.deco import Panel, StatCard, TacButton, caption

ROLE_NAMES = {"raw": "RAW", "jpeg": "JPG", "heif": "HEIC", "png": "PNG", "image": "图片", "sidecar": "附属文件",
              "video": "视频"}


def _split_size(n: int) -> tuple[str, str]:
    a = human_size(n).split()
    return (a[0], a[1]) if len(a) == 2 else (a[0], "")


class _WarnBox(QVBoxLayout):
    def __init__(self):
        super().__init__()
        self.setSpacing(2)
        self._labels: list[QLabel] = []

    def set(self, warnings: list[str]) -> None:
        for l in self._labels:
            l.deleteLater()
        self._labels = []
        p = theme.current()
        for w in warnings:
            l = QLabel("▲  " + w)
            l.setWordWrap(True)
            l.setStyleSheet(f"color: {p.warn};")
            self.addWidget(l)
            self._labels.append(l)


# ---------------------------------------------------------------------------
# 清理 RAW
# ---------------------------------------------------------------------------

class CleanupDialog(_Base):
    def __init__(self, parent, groups: list, scope_text: str, mode: str = "keep_jpg"):
        super().__init__(parent, "清理 RAW · 释放空间", "CLEANUP // MOVE TO RECYCLE BIN", hazard=True)
        self.groups = groups
        self.plan = None
        self.resize(980, 660)
        p = theme.current()
        self.body.addWidget(QLabel(f"<b>范围：</b>{scope_text}"))
        mr = QHBoxLayout()
        self.mode_btns: dict[str, QRadioButton] = {}
        bg = QButtonGroup(self)
        for k, text in MODES.items():
            b = QRadioButton(text)
            bg.addButton(b)
            mr.addWidget(b)
            self.mode_btns[k] = b
            b.toggled.connect(lambda on: on and self._rebuild())
        mr.addStretch(1)
        self.body.addLayout(mr)
        self.raw_only = QCheckBox("也删除没有 JPG 的 RAW（整张照片删除，谨慎）")
        self.raw_only.toggled.connect(lambda _: self._rebuild())
        self.body.addWidget(self.raw_only)
        cards = QGridLayout()
        cards.setSpacing(8)
        self.c_groups = StatCard("涉及照片", "PHOTOS", accent=p.danger)
        self.c_files = StatCard("放入回收站", "FILES", accent=p.danger)
        self.c_bytes = StatCard("释放空间", "FREED", accent=p.ok)
        self.c_keep = StatCard("范围内保留", "KEPT", accent=p.ink3)
        for i, c in enumerate((self.c_groups, self.c_files, self.c_bytes, self.c_keep)):
            cards.addWidget(c, 0, i)
        self.body.addLayout(cards)
        self.warns = _WarnBox()
        self.body.addLayout(self.warns)
        self.model = _RowsModel(["文件", "类型", "大小", "文件夹"], [], mono=(0, 2, 3))
        self.panel = Panel("删除清单", "TRASH MANIFEST", "", pad=6)
        self.panel.body.addWidget(_table(self.model))
        self.body.addWidget(self.panel, 1)
        self.body.addWidget(caption("文件一律放入系统回收站，不会直接删除；执行前先写操作日志，"
                                    "可在「操作记录」页撤销（从回收站放回原处）。清空回收站后就无法撤销了。"
                                    "RAW 旁边的修图记录（.xmp / .pp3 / .dop …）跟 RAW 一起处理。", "faint", 8.5))
        bc = TacButton("取消", "secondary")
        self.bo = TacButton("放入回收站", "danger", en="TRASH")
        bc.clicked.connect(self.reject)
        self.bo.clicked.connect(self._confirm)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(self.bo)
        self.mode_btns.get(mode, self.mode_btns["keep_jpg"]).setChecked(True)

    def mode(self) -> str:
        return next((k for k, b in self.mode_btns.items() if b.isChecked()), "keep_jpg")

    def _rebuild(self) -> None:
        m = self.mode()
        self.raw_only.setEnabled(m == "unrated_raw")
        plan = build_cleanup_plan(self.groups, m, self.raw_only.isChecked() and m == "unrated_raw")
        self.plan = plan
        n = len([g for g in self.groups if g.files])
        self.c_groups.set(str(len(plan.items)), len(plan.items) / n if n else 0, "张")
        self.c_files.set(str(plan.file_count), unit="个")
        v, u = _split_size(plan.total_bytes)
        self.c_bytes.set(v, unit=u)
        self.c_keep.set(str(n - len(plan.items)), unit="张")
        self.warns.set([w for w in plan.warnings])
        rows = []
        for it in plan.items:
            for op in it.ops:
                rows.append((os.path.basename(op.src), ROLE_NAMES.get(op.role, op.role), human_size(op.size),
                             os.path.dirname(op.src)))
        self.model.set_rows(rows)
        self.panel.set_meta(f"{len(rows)} FILES · {human_size(plan.total_bytes)}")
        self.bo.setEnabled(bool(plan.items))

    def _confirm(self) -> None:
        if not self.plan or not self.plan.items:
            return
        whole = sum(1 for it in self.plan.items
                    if not any(f.role in ("jpeg", "heif", "png", "image") for g in self.groups if g.gid == it.gid
                               for f in g.files if os.path.normcase(f.path) not in
                               {os.path.normcase(o.src) for o in it.ops}))
        extra = (f"<br><br><b style='color:{theme.current().danger}'>其中 {whole} 张照片会整张删除"
                 "（没有留下 JPG）。</b>") if whole else ""
        if ask(self, "确认放入回收站",
               f"将把 <b>{self.plan.file_count}</b> 个文件（{human_size(self.plan.total_bytes)}）放入系统回收站。{extra}"
               "<br><br>可在「操作记录」页撤销；清空回收站之前都能还原。",
               ok="放入回收站", danger=True, en="CONFIRM DELETE"):
            self.accept()


# ---------------------------------------------------------------------------
# 批量重命名
# ---------------------------------------------------------------------------

RENAME_PRESETS = [
    ("日期_城市_序号", "{日期}_{市|省|国家|未知地点}_{序号}"),
    ("日期_时间", "{日期}_{时间}"),
    ("年月日_当日序号", "{年月日}_{当日序号}"),
    ("日期_相机_原名", "{日期}_{相机}_{原名}"),
    ("城市_日期_序号（4 位）", "{市|省|国家|未知地点}_{日期}_{序号:4}"),
    ("日期_星级_原名", "{日期}_{星级}_{原名}"),
]


class RenameDialog(_Base):
    def __init__(self, parent, ctx, groups: list, scope_text: str):
        super().__init__(parent, "批量重命名", "RENAME // TEMPLATE PREVIEW", hazard=True)
        self.ctx = ctx
        self.groups = [g for g in groups if g.files]
        self.plan = None
        self.resize(1040, 700)
        p = theme.current()
        self.body.addWidget(QLabel(f"<b>范围：</b>{scope_text}"))
        r = QHBoxLayout()
        r.addWidget(QLabel("模板："))
        self.tpl = QLineEdit(ctx.settings.get("rename_template") or RENAME_PRESETS[0][1])
        r.addWidget(self.tpl, 1)
        self.preset = QComboBox()
        self.preset.addItem("常用模板…", "")
        for name, t in RENAME_PRESETS:
            self.preset.addItem(name, t)
        self.preset.activated.connect(self._preset)
        r.addWidget(self.preset)
        r.addWidget(QLabel("序号从"))
        self.start = QSpinBox()
        self.start.setRange(0, 999999)
        self.start.setValue(1)
        r.addWidget(self.start)
        self.body.addLayout(r)
        self.body.addWidget(caption(
            "占位符：{日期} 20260105 · {时间} 153012 · {年月日} 2026-01-05 · {年} {月} {日} · {市} {省} {国家} {区县} · "
            "{相机} {品牌} {镜头} · {星级} {色标} · {序号} / {序号:4}（按拍摄时间排序，补零）· {当日序号} · {原名}。"
            "用 | 写后备：{市|省|国家|未知地点} = 没有城市就用省，再没有用国家。", "dim", 8.5))
        cards = QGridLayout()
        cards.setSpacing(8)
        self.c_n = StatCard("将改名", "RENAME")
        self.c_files = StatCard("涉及文件", "FILES")
        self.c_same = StatCard("名字不变（跳过）", "UNCHANGED", accent=p.ink3)
        self.c_dup = StatCard("重名自动编号", "SUFFIXED", accent=p.warn)
        for i, c in enumerate((self.c_n, self.c_files, self.c_same, self.c_dup)):
            cards.addWidget(c, 0, i)
        self.body.addLayout(cards)
        self.warns = _WarnBox()
        self.body.addLayout(self.warns)
        self.model = _RowsModel(["原文件名", "→ 新文件名", "文件夹"], [], mono=(0, 1, 2))
        self.panel = Panel("改名预览", "RENAME PREVIEW", "", pad=6)
        self.panel.body.addWidget(_table(self.model))
        self.body.addWidget(self.panel, 1)
        self.body.addWidget(caption("同一张照片的 RAW、JPG 和 .xmp 改成同一个名字（扩展名不变），照片不换文件夹；"
                                    "与已有文件重名时自动加“ (1)”，绝不覆盖。星级、手动地点等标注跟着新名字走。"
                                    "执行前写操作日志，可在「操作记录」页一键改回原名。", "faint", 8.5))
        bc = TacButton("取消", "secondary")
        self.bo = TacButton("确认改名", "primary", en="RENAME")
        bc.clicked.connect(self.reject)
        self.bo.clicked.connect(self._confirm)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(self.bo)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._rebuild)
        self.tpl.textChanged.connect(lambda _: self._timer.start())
        self.start.valueChanged.connect(lambda _: self._timer.start())
        self._rebuild()

    def _preset(self, i: int) -> None:
        t = self.preset.itemData(i)
        if t:
            self.tpl.setText(t)
        self.preset.setCurrentIndex(0)

    def _rebuild(self) -> None:
        t = self.tpl.text().strip()
        p = theme.current()
        if not t:
            self.plan = None
            self.model.set_rows([])
            self.bo.setEnabled(False)
            return
        try:
            plan = build_rename_plan(self.groups, t, PartitionContext(self.ctx.settings.snapshot()),
                                     int(self.start.value()))
        except Exception as e:
            self.plan = None
            self.warns.set([f"模板有误：{e}"])
            self.model.set_rows([])
            self.bo.setEnabled(False)
            return
        self.plan = plan
        n = len(self.groups)
        self.c_n.set(str(len(plan.items)), len(plan.items) / n if n else 0, "张")
        self.c_files.set(str(plan.file_count), unit="个")
        unchanged = n - len(plan.items) - len(plan.skipped_conflict)
        self.c_same.set(str(max(0, unchanged)), unit="张")
        self.c_dup.set(str(plan.renamed), unit="张")
        from ..partition.engine import unknown_tokens
        unk = [u for u in unknown_tokens(t) if not u.startswith("序号") and u not in ("当日序号", "原名")]
        extra = [f"“{{{u}}}”不是占位符，会按原样当作文字。" for u in unk]
        self.warns.set(extra + [w for w in plan.warnings if "相同" not in w and "重名" not in w])
        rows, colors = [], []
        for it in plan.items:
            for op in it.ops:
                rows.append((os.path.basename(op.src), os.path.basename(op.dst), os.path.dirname(op.src)))
                colors.append(p.warn if it.renamed else None)
        self.model.set_rows(rows, colors)
        self.panel.set_meta(f"{len(rows)} FILES")
        self.bo.setEnabled(bool(plan.items))

    def _confirm(self) -> None:
        self._timer.stop()
        self._rebuild()
        if not self.plan or not self.plan.items:
            return
        self.ctx.set_setting("rename_template", self.tpl.text().strip())
        if ask(self, "确认改名", f"将重命名 <b>{len(self.plan.items)}</b> 张照片的 {self.plan.file_count} 个文件。"
               "<br><br>可在「操作记录」页撤销（改回原名）。", ok="确认改名", en="CONFIRM"):
            self.accept()
