"""补全工作流的对话框：导入向导、查找重复照片、导出分享 / 网页相册。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QRadioButton, QSlider, QSpinBox, QVBoxLayout)

from ..ops.pathsafe import human_size
from . import theme
from .culling_dialogs import _split_size, _WarnBox
from .dialogs import _Base
from .theme import font
from .timeplace_dialogs import _RowsModel, _table
from .widgets.deco import Panel, StatCard, TacButton, caption


def _folder_row(parent, label: str, value: str, title: str) -> tuple[QHBoxLayout, QLineEdit]:
    r = QHBoxLayout()
    r.addWidget(QLabel(label))
    ed = QLineEdit(value)
    r.addWidget(ed, 1)
    b = TacButton("浏览…", "secondary", height=30)

    def pick():
        d = QFileDialog.getExistingDirectory(parent, title, ed.text() or os.path.expanduser("~"))
        if d:
            ed.setText(os.path.normpath(d))
    b.clicked.connect(pick)
    r.addWidget(b)
    return r, ed


# ---------------------------------------------------------------------------
# 导入向导
# ---------------------------------------------------------------------------

class ImportDialog(_Base):
    """选来源 → 读取（只读元数据，不做画面分析）→ 看哪些是新的 → 选目标与规则 → 预览 → 开始导入。"""

    def __init__(self, parent, ctx):
        super().__init__(parent, "导入照片", "IMPORT // CARD · PHONE · FOLDER")
        self.ctx = ctx
        self.groups: list = []
        self.check = None
        self.plan = None
        self._worker = None
        self.resize(1060, 720)
        s = ctx.settings
        r = QHBoxLayout()
        r.addWidget(QLabel("来源："))
        self.src = QComboBox()
        self.src.setMinimumWidth(380)
        r.addWidget(self.src, 1)
        b_more = TacButton("选择文件夹…", "secondary", height=30)
        b_more.clicked.connect(self._pick_src)
        b_ref = TacButton("刷新", "secondary", height=30)
        b_ref.clicked.connect(self._detect)
        self.b_read = TacButton("读取", "primary", en="READ", height=30)
        self.b_read.clicked.connect(self._read)
        for b in (b_more, b_ref, self.b_read):
            r.addWidget(b)
        self.body.addLayout(r)
        self.status = caption("插入相机存储卡或连接手机（选择手机里的 DCIM 文件夹），选好来源后点“读取”。", "dim", 9)
        self.body.addWidget(self.status)
        cards = QGridLayout()
        cards.setSpacing(8)
        p = theme.current()
        self.c_new = StatCard("新照片", "NEW")
        self.c_vid = StatCard("新视频", "VIDEO", accent=p.accent2)
        self.c_old = StatCard("已导入（跳过）", "IMPORTED", accent=p.ink3)
        self.c_size = StatCard("要复制", "VOLUME", accent=p.ok)
        for i, c in enumerate((self.c_new, self.c_vid, self.c_old, self.c_size)):
            cards.addWidget(c, 0, i)
        self.body.addLayout(cards)
        fr, self.dst = _folder_row(self, "导入到：", s.get("import_dir") or s.get("target_dir") or "",
                                   "照片导入到哪个文件夹？")
        self.body.addLayout(fr)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("文件夹："))
        from ..ops.importer import TEMPLATES
        self.tpl = QComboBox()
        self.tpl.setEditable(True)
        for t, desc in TEMPLATES:
            self.tpl.addItem(f"{t}", t)
        self.tpl.setCurrentText(s.get("import_template") or TEMPLATES[0][0])
        self.tpl.setMinimumWidth(260)
        r2.addWidget(self.tpl)
        r2.addSpacing(16)
        self.cb_rename = QCheckBox("同时改名：")
        self.rename = QLineEdit(s.get("rename_template") or "{日期}_{序号}")
        self.cb_rename.setChecked(False)
        r2.addWidget(self.cb_rename)
        r2.addWidget(self.rename, 1)
        self.body.addLayout(r2)
        self.cb_add = QCheckBox("导入后把目标文件夹加入「资源接入」并扫描（之后就能挑片、分区、搜图）")
        self.cb_add.setChecked(True)
        self.body.addWidget(self.cb_add)
        self.warns = _WarnBox()
        self.body.addLayout(self.warns)
        self.model = _RowsModel(["存储卡上的文件", "→ 导入后的位置（相对目标文件夹）", "大小"], [], mono=(0, 1, 2))
        self.panel = Panel("导入清单", "IMPORT MANIFEST", "", pad=6)
        self.panel.body.addWidget(_table(self.model))
        self.body.addWidget(self.panel, 1)
        self.body.addWidget(caption("只复制、不改动来源里的文件；以前导入过的（或照片库里已有的）自动跳过。复制先写操作日志，"
                                    "可在「操作记录」页撤销（复制出来的文件进回收站）。", "faint", 8.5))
        bc = TacButton("取消", "secondary")
        self.bo = TacButton("开始导入", "primary", en="IMPORT")
        bc.clicked.connect(self.reject)
        self.bo.clicked.connect(self._go)
        self.bo.setEnabled(False)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(self.bo)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._rebuild)
        for w in (self.dst.textChanged, self.tpl.currentTextChanged, self.rename.textChanged):
            w.connect(lambda _=None: self._timer.start())
        self.cb_rename.toggled.connect(lambda _: self._timer.start())
        self._detect()

    # ---- 来源 ----
    def _detect(self):
        from ..ops.importer import detect_sources
        cur = self.src.currentData()
        self.src.clear()
        try:
            found = detect_sources()
        except Exception:
            found = []
        for s in found:
            self.src.addItem(s.label, s.path)
        extra = self.ctx.settings.get("import_last_src")
        if extra and os.path.isdir(extra) and self.src.findData(extra) < 0:
            self.src.addItem(extra, extra)
        if cur and self.src.findData(cur) >= 0:
            self.src.setCurrentIndex(self.src.findData(cur))
        if not found:
            self.status.setText("没有检测到存储卡。插入后点“刷新”，或点“选择文件夹…”（手机：选择手机里的 DCIM 文件夹）。")

    def _pick_src(self):
        d = QFileDialog.getExistingDirectory(self, "从哪个文件夹导入？", self.src.currentData() or os.path.expanduser("~"))
        if d:
            d = os.path.normpath(d)
            if self.src.findData(d) < 0:
                self.src.addItem(d, d)
            self.src.setCurrentIndex(self.src.findData(d))

    # ---- 读取 ----
    def _read(self):
        src = self.src.currentData()
        if not src or not os.path.isdir(src):
            self.status.setText("请先选择来源。")
            return
        from ..core.library import Library
        from ..ops.importer import card_roots
        self.ctx.settings.set("import_last_src", src)
        roots = card_roots(src)
        self.b_read.setEnabled(False)
        self.bo.setEnabled(False)
        self.status.setText("正在读取 " + "、".join(roots) + " …")
        settings = self.ctx.settings

        def job(prog, cancel):
            lib = Library(settings)
            try:
                lib.scan(roots, True, lambda pg: prog(pg), cancel, pixels=False)
                return [g for g in lib.groups if g.files]
            finally:
                lib.close()

        def on_prog(pg):
            self.status.setText(f"正在读取：{pg.groups} 组 · 元数据 {pg.meta_done}/{pg.meta_total}")

        def on_done(groups):
            self.b_read.setEnabled(True)
            self.groups = groups or []
            self._classify()

        def on_err(tb):
            self.b_read.setEnabled(True)
            self.status.setText("读取出错：" + tb.strip().splitlines()[-1])
        self._worker = self.ctx.run(job, on_prog, on_done, on_err)

    def _classify(self):
        from ..ops.importer import ImportHistory, classify, history_path
        hist = ImportHistory(history_path())
        try:
            lib_sigs = {f.sig for g in self.ctx.library.groups for f in g.files}
            self.check = classify(self.groups, hist, lib_sigs)
        finally:
            hist.close()
        c = self.check
        n = len(self.groups) or 1
        self.c_new.set(str(c.photos), c.photos / n, "张")
        self.c_vid.set(str(c.videos), c.videos / n, "段")
        self.c_old.set(str(len(c.already)), len(c.already) / n, "张")
        v, u = _split_size(c.bytes)
        self.c_size.set(v, unit=u)
        dates = sorted(g.date for g in c.new if g.date)
        span = f"，拍摄于 {dates[0]:%Y-%m-%d} ~ {dates[-1]:%Y-%m-%d}" if dates else ""
        self.status.setText(f"共 {len(self.groups)} 组：新的 {len(c.new)} 组{span}；已导入过 {len(c.already)} 组（跳过）。")
        self._rebuild()

    def _rebuild(self):
        if self.check is None:
            return
        from ..ops.importer import build_import_plan
        from ..partition.base import PartitionContext
        dst = self.dst.text().strip()
        if not dst:
            self.warns.set(["请选择“导入到”的文件夹。"])
            self.model.set_rows([])
            self.bo.setEnabled(False)
            return
        tpl = self.tpl.currentText().strip()
        rn = self.rename.text().strip() if self.cb_rename.isChecked() else ""
        plan = build_import_plan(self.check.new, dst, tpl, PartitionContext(self.ctx.settings.snapshot()), rn)
        self.plan = plan
        rows = [(op.src, os.path.relpath(op.dst, plan.target_root or dst), human_size(op.size))
                for it in plan.items for op in it.ops][:3000]
        self.model.set_rows(rows)
        self.panel.set_meta(f"{plan.file_count} FILES · {human_size(plan.total_bytes)}")
        self.warns.set(plan.errors + plan.warnings[:4])
        self.bo.setEnabled(plan.ok)

    def _go(self):
        self._timer.stop()
        self._rebuild()
        if not self.plan or not self.plan.ok:
            return
        s = self.ctx.settings
        s.set("import_dir", self.dst.text().strip())
        s.set("import_template", self.tpl.currentText().strip())
        if self.cb_rename.isChecked():
            s.set("rename_template", self.rename.text().strip())
        self.accept()


# ---------------------------------------------------------------------------
# 重复照片
# ---------------------------------------------------------------------------

class DedupeDialog(_Base):
    def __init__(self, parent, ctx, groups: list):
        super().__init__(parent, "查找重复照片", "DUPLICATES // CONTENT HASH", hazard=True)
        self.ctx = ctx
        self.groups = [g for g in groups if g.files]
        self.result = None
        self.plan = None
        self.resize(1040, 680)
        from ..ops.dedupe import KEEP_RULES
        r = QHBoxLayout()
        r.addWidget(QLabel(f"范围：照片库全部 {len(self.groups)} 组　　每组保留："))
        self.rule = QComboBox()
        for k, v in KEEP_RULES.items():
            self.rule.addItem(v, k)
        r.addWidget(self.rule, 1)
        self.b_find = TacButton("开始查找", "primary", en="SCAN", height=30)
        self.b_find.clicked.connect(self._find)
        r.addWidget(self.b_find)
        self.body.addLayout(r)
        self.status = caption("先按文件大小筛选，大小相同的再比对内容，完全一致才算重复（改了名的副本也能找到）。", "dim", 9)
        self.body.addWidget(self.status)
        cards = QGridLayout()
        p = theme.current()
        self.c_sets = StatCard("重复组", "SETS")
        self.c_rm = StatCard("可删除的副本", "COPIES", accent=p.danger)
        self.c_bytes = StatCard("释放空间", "FREED", accent=p.ok)
        self.c_skip = StatCard("带修图记录（保留）", "KEPT", accent=p.ink3)
        for i, c in enumerate((self.c_sets, self.c_rm, self.c_bytes, self.c_skip)):
            cards.addWidget(c, 0, i)
        self.body.addLayout(cards)
        self.warns = _WarnBox()
        self.body.addLayout(self.warns)
        self.model = _RowsModel(["删除（副本）", "保留", "大小"], [], mono=(0, 1, 2))
        self.panel = Panel("清单", "DUPLICATE MANIFEST", "", pad=6)
        self.panel.body.addWidget(_table(self.model))
        self.body.addWidget(self.panel, 1)
        self.body.addWidget(caption("副本一律放入系统回收站，可在「操作记录」页撤销。比对只看照片 / 视频本身的内容，"
                                    "副本带有原件没有的修图记录（.xmp 等）时不删。", "faint", 8.5))
        bc = TacButton("取消", "secondary")
        self.bo = TacButton("把副本放入回收站", "danger", en="TRASH")
        bc.clicked.connect(self.reject)
        self.bo.clicked.connect(self._confirm)
        self.bo.setEnabled(False)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(self.bo)
        self.rule.currentIndexChanged.connect(lambda _: self.result is not None and self._find())

    def _find(self):
        from ..ops.dedupe import find_duplicates
        self.b_find.setEnabled(False)
        self.bo.setEnabled(False)
        rule = self.rule.currentData()
        tgt = self.ctx.settings.get("target_dir") or ""
        groups = self.groups

        def job(prog, cancel):
            return find_duplicates(groups, rule, tgt, lambda d, t, c: prog((d, t, c)), cancel)

        def on_prog(v):
            d, t, c = v
            self.status.setText(f"正在比对内容：{d}/{t}  {c}")
        self.ctx.run(job, on_prog, self._done, lambda tb: (self.b_find.setEnabled(True),
                                                          self.status.setText("出错：" + tb.strip().splitlines()[-1])))

    def _done(self, res):
        from ..ops.dedupe import build_dedupe_plan
        self.b_find.setEnabled(True)
        self.result = res
        by = {g.gid: g for g in self.groups}
        self.plan = build_dedupe_plan(self.groups, res)
        n = len(self.groups) or 1
        self.c_sets.set(str(len(res.sets)), unit="组")
        self.c_rm.set(str(res.total_groups), res.total_groups / n, "张")
        v, u = _split_size(res.total_bytes)
        self.c_bytes.set(v, unit=u)
        self.c_skip.set(str(len(res.skipped_sidecar)), unit="张")
        rows = []
        for s in res.sets:
            for gid in s.remove:
                g = by[gid]
                rows.append((g.primary, by[s.keep].primary, human_size(g.total_size)))
        self.model.set_rows(rows)
        self.panel.set_meta(f"{len(rows)} COPIES · {human_size(res.total_bytes)}")
        self.warns.set(self.plan.warnings)
        self.status.setText(f"比对完成：读取了 {human_size(res.hashed_bytes)} 的内容。")
        self.bo.setEnabled(bool(self.plan.items))

    def _confirm(self):
        from .dialogs import ask
        if self.plan and self.plan.items and ask(
                self, "确认删除副本", f"将把 <b>{len(self.plan.items)}</b> 张重复照片的副本（{self.plan.file_count} 个文件，"
                f"{human_size(self.plan.total_bytes)}）放入回收站，每组保留一份。<br><br>可在「操作记录」页撤销。",
                ok="放入回收站", danger=True, en="CONFIRM"):
            self.accept()


# ---------------------------------------------------------------------------
# 导出分享 / 网页相册
# ---------------------------------------------------------------------------

class ExportDialog(_Base):
    def __init__(self, parent, ctx, groups: list, scope_text: str, album: bool = False, title: str = ""):
        super().__init__(parent, "导出与分享", "EXPORT // SHARE · WEB ALBUM")
        from ..ops.exporter import POSITIONS, SIZES
        self.ctx = ctx
        self.groups = [g for g in groups if g.files]
        self.resize(980, 640)
        s = ctx.settings
        self.body.addWidget(QLabel(f"<b>范围：</b>{scope_text}"
                                   + (f"（其中 {sum(1 for g in self.groups if g.is_video)} 段视频不导出）"
                                      if any(g.is_video for g in self.groups) else "")))
        main = QHBoxLayout()
        main.setSpacing(16)
        left = QVBoxLayout()
        mr = QHBoxLayout()
        self.m_jpg = QRadioButton("分享用 JPG（朋友圈 / 小红书 / 发给朋友）")
        self.m_web = QRadioButton("网页相册（按天分组 + 地图，双击 index.html 打开）")
        bg = QButtonGroup(self)
        bg.addButton(self.m_jpg)
        bg.addButton(self.m_web)
        (self.m_web if album else self.m_jpg).setChecked(True)
        mv = QVBoxLayout()
        mv.addWidget(self.m_jpg)
        mv.addWidget(self.m_web)
        mr.addLayout(mv)
        left.addLayout(mr)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.addWidget(QLabel("尺寸"), 0, 0)
        self.size = QComboBox()
        for v, t in SIZES:
            self.size.addItem(t, v)
        self.size.setCurrentIndex(max(0, self.size.findData(int(s.get("export_long_edge", 2048)))))
        g.addWidget(self.size, 0, 1)
        g.addWidget(QLabel("质量"), 1, 0)
        self.quality = QSpinBox()
        self.quality.setRange(60, 100)
        self.quality.setValue(int(s.get("export_quality", 90)))
        g.addWidget(self.quality, 1, 1)
        g.addWidget(QLabel("水印"), 2, 0)
        self.wm = QLineEdit(s.get("export_watermark", ""))
        self.wm.setPlaceholderText("留空 = 不加水印，例：© 你的名字")
        g.addWidget(self.wm, 2, 1)
        wr = QHBoxLayout()
        self.wm_pos = QComboBox()
        for k, v in POSITIONS.items():
            self.wm_pos.addItem(v, k)
        self.wm_pos.setCurrentIndex(max(0, self.wm_pos.findData(s.get("export_wm_pos", "br"))))
        self.wm_op = QSlider(Qt.Orientation.Horizontal)
        self.wm_op.setRange(10, 100)
        self.wm_op.setValue(int(float(s.get("export_wm_opacity", 0.7)) * 100))
        wr.addWidget(self.wm_pos)
        wr.addWidget(QLabel("透明度"))
        wr.addWidget(self.wm_op, 1)
        g.addLayout(wr, 3, 1)
        g.addWidget(QLabel("隐私"), 4, 0)
        self.cb_gps = QCheckBox("去掉 GPS 位置（保留拍摄时间、相机）")
        self.cb_gps.setChecked(bool(s.get("export_strip_gps", True)))
        self.cb_all = QCheckBox("去掉全部 EXIF 信息")
        self.cb_all.setChecked(bool(s.get("export_strip_all", False)))
        pv = QVBoxLayout()
        pv.addWidget(self.cb_gps)
        pv.addWidget(self.cb_all)
        g.addLayout(pv, 4, 1)
        g.addWidget(QLabel("文件名"), 5, 0)
        nr = QHBoxLayout()
        self.cb_name = QCheckBox("按模板改名")
        self.name = QLineEdit(s.get("export_name_template") or "{日期}_{市|省|国家|}_{序号}")
        nr.addWidget(self.cb_name)
        nr.addWidget(self.name, 1)
        g.addLayout(nr, 5, 1)
        g.addWidget(QLabel("相册标题"), 6, 0)
        self.title = QLineEdit(title or "我的相册")
        g.addWidget(self.title, 6, 1)
        self.cb_map = QCheckBox("相册里显示地图（只精确到约 1 km）")
        self.cb_map.setChecked(True)
        g.addWidget(self.cb_map, 7, 1)
        left.addLayout(g)
        fr, self.dst = _folder_row(self, "导出到：", s.get("export_dir") or os.path.join(os.path.expanduser("~"), "Pictures"),
                                   "导出到哪个文件夹？")
        left.addLayout(fr)
        left.addStretch(1)
        main.addLayout(left, 1)
        prev = Panel("预览", "PREVIEW", "", pad=6)
        self.preview = QLabel("")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(380, 320)
        prev.body.addWidget(self.preview, 1)
        self.pinfo = caption("", "faint", 8)
        prev.body.addWidget(self.pinfo)
        main.addWidget(prev)
        self.body.addLayout(main, 1)
        bc = TacButton("取消", "secondary")
        self.bo = TacButton("导出", "primary", en="EXPORT")
        bc.clicked.connect(self.reject)
        self.bo.clicked.connect(self._go)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(self.bo)
        self.bo.setEnabled(any(not g.is_video for g in self.groups))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._preview)
        for sig in (self.wm.textChanged, self.size.currentIndexChanged, self.wm_pos.currentIndexChanged,
                    self.wm_op.valueChanged, self.quality.valueChanged):
            sig.connect(lambda *_: self._timer.start())
        for b in (self.m_jpg, self.m_web, self.cb_all, self.cb_name):
            b.toggled.connect(lambda *_: self._modes())
        self._modes()
        QTimer.singleShot(50, self._preview)

    def options(self):
        from ..ops.exporter import ExportOptions
        return ExportOptions(long_edge=int(self.size.currentData() or 0), quality=int(self.quality.value()),
                             strip_gps=self.cb_gps.isChecked(), strip_all=self.cb_all.isChecked(),
                             watermark=self.wm.text().strip(), wm_pos=self.wm_pos.currentData(),
                             wm_opacity=self.wm_op.value() / 100,
                             name_template=self.name.text().strip() if self.cb_name.isChecked() else "")

    @property
    def album(self) -> bool:
        return self.m_web.isChecked()

    def _modes(self):
        web = self.album
        for w in (self.cb_name, self.name, self.cb_gps, self.cb_all):
            w.setEnabled(not web)
        self.name.setEnabled(not web and self.cb_name.isChecked())
        self.cb_gps.setEnabled(not web and not self.cb_all.isChecked())
        for w in (self.title, self.cb_map):
            w.setEnabled(web)

    def _preview(self):
        from ..ops.exporter import render_one
        sample = next((g for g in self.groups if not g.is_video), None)
        if sample is None:
            self.preview.setText("没有可导出的照片")
            return
        try:
            o = self.options()
            le = o.long_edge or 0
            im, _, _ = render_one(sample, o, self.ctx.library.exiftool_path)
            w, h = im.size
            show = im.copy()
            show.thumbnail((380, 320))
            data = show.convert("RGB").tobytes("raw", "RGB")
            qi = QImage(data, show.width, show.height, show.width * 3, QImage.Format.Format_RGB888).copy()
            self.preview.setPixmap(QPixmap.fromImage(qi))
            self.pinfo.setText(f"{sample.name} → {w} × {h}" + ("" if le else "（原始尺寸）"))
        except Exception as e:
            self.preview.setText(f"预览失败：{e}")

    def _go(self):
        d = self.dst.text().strip()
        if not d:
            return
        s = self.ctx.settings
        o = self.options()
        s.set("export_dir", d)
        s.set("export_long_edge", o.long_edge)
        s.set("export_quality", o.quality)
        s.set("export_watermark", o.watermark)
        s.set("export_wm_pos", o.wm_pos)
        s.set("export_wm_opacity", o.wm_opacity)
        s.set("export_strip_gps", o.strip_gps)
        s.set("export_strip_all", o.strip_all)
        if self.cb_name.isChecked():
            s.set("export_name_template", o.name_template)
        self.accept()


def run_export(ctx, parent, dlg: ExportDialog) -> None:
    """对话框确认后：后台导出 → 结果（可打开文件夹 / 网页相册）。"""
    from ..ops.exporter import export_album, export_jpegs
    from ..partition.base import PartitionContext
    from .dialogs import ask, info, run_with_progress
    from .widgets.thumbgrid import open_folder
    o = dlg.options()
    d = dlg.dst.text().strip()
    groups = dlg.groups
    et = ctx.library.exiftool_path
    if dlg.album:
        title = dlg.title.text().strip() or "相册"
        show_map = dlg.cb_map.isChecked()
        sub = ""
        dates = sorted(g.date for g in groups if g.date)
        if dates:
            sub = f"{dates[0]:%Y-%m-%d}" + (f" ~ {dates[-1]:%Y-%m-%d}" if dates[-1].date() != dates[0].date() else "")

        def job(prog, cancel):
            return export_album(groups, d, title, o, sub, et, show_map, lambda a, b, c: prog((a, b, c)), cancel)
    else:
        pctx = PartitionContext(ctx.settings.snapshot())

        def job(prog, cancel):
            return export_jpegs(groups, d, o, pctx, et, lambda a, b, c: prog((a, b, c)), cancel)
    out, tb = run_with_progress(ctx, parent, "正在导出", "EXPORTING", job)
    if tb or out is None:
        info(parent, "导出出错", f"<pre style='font-size:8pt'>{(tb or '')[-1500:]}</pre>", "ERROR")
        return
    if dlg.album:
        index, res = out
        target = index
    else:
        res, target = out, d
    msg = [f"已导出 <b>{len(res.written)}</b> 张（{human_size(res.bytes)}）。"]
    if res.skipped_video:
        msg.append(f"{res.skipped_video} 段视频没有导出。")
    if res.failed:
        msg.append(f"{res.failed} 张失败：<br>" + "<br>".join(res.errors[:6]))
    if res.cancelled:
        msg.append("已取消，剩余照片未导出。")
    ctx.status.emit(f"导出完成：{len(res.written)} 张 → {d}")
    if ask(parent, "导出完成", "<br><br>".join(msg), ok="打开网页相册" if dlg.album else "打开文件夹",
           cancel="关闭", en="COMPLETE"):
        if dlg.album:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))
        else:
            open_folder(target)
