"""11 设置：外观、ExifTool、日期识别规则、地区、色调阈值、文件安全、性能、插件、缓存、关于。"""
from __future__ import annotations

import os
import shutil

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QRadioButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ... import __version__
from ...config import plugin_dirs, thumbs_dir, user_data_dir
from ...core.dates import DEFAULT_FILENAME_PATTERNS, compile_patterns, parse_filename_date
from ...ops.pathsafe import human_size
from ...plugins import LOADED
from .. import theme, motion
from ..dialogs import ask
from ..theme import font
from ..widgets.deco import Panel, TacButton, caption
from ..widgets.thumbgrid import open_folder
from .base import Page


class SettingsPage(Page):
    page_id = "settings"
    index = "11"
    title = "系统设置"
    en = "System config"
    sub = "修改日期 / 地区相关设置后会自动重新整理（无需重新扫描）。"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.s = ctx.settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("PageRoot")
        cols = QHBoxLayout(inner)
        cols.setContentsMargins(0, 0, 8, 0)
        cols.setSpacing(12)
        left = QVBoxLayout()
        right = QVBoxLayout()
        left.setSpacing(12)
        right.setSpacing(12)
        lw, rw = QWidget(), QWidget()
        lw.setLayout(left)
        rw.setLayout(right)
        left.setContentsMargins(0, 0, 0, 0)
        right.setContentsMargins(0, 0, 0, 0)
        from PySide6.QtWidgets import QSizePolicy
        for w in (lw, rw):
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        cols.addWidget(lw, 1)
        cols.addWidget(rw, 1)

        self._reanalyze_timer = QTimer(self)
        self._reanalyze_timer.setSingleShot(True)
        self._reanalyze_timer.setInterval(500)
        self._reanalyze_timer.timeout.connect(self._reanalyze)

        # ---- 外观 ----
        ap = Panel("外观", "Appearance")
        r = QHBoxLayout()
        self.t_light = QRadioButton("终末地 · 浅色工业")
        self.t_dark = QRadioButton("方舟 · 深色终端")
        g = QButtonGroup(self)
        g.addButton(self.t_light)
        g.addButton(self.t_dark)
        (self.t_dark if self.s.get("theme") == "dark" else self.t_light).setChecked(True)
        self.t_light.toggled.connect(self._theme)
        r.addWidget(self.t_light)
        r.addWidget(self.t_dark)
        r.addStretch(1)
        ap.body.addLayout(r)
        self.motion_toggle = QCheckBox("界面动效 · 导航、切页与按钮反馈")
        self.motion_toggle.setChecked(bool(self.s.get("ui_motion")))
        self.motion_toggle.toggled.connect(self._motion)
        ap.body.addWidget(self.motion_toggle)
        left.addWidget(ap)

        # ---- ExifTool ----
        ep = Panel("元数据引擎", "ExifTool")
        self.et_status = QLabel()
        self.et_status.setFont(font("mono", 9))
        ep.body.addWidget(self.et_status)
        r = QHBoxLayout()
        self.et_path = QLineEdit(self.s.get("exiftool_path") or "")
        self.et_path.setPlaceholderText("留空 = 自动查找（程序目录 tools\\exiftool\\exiftool.exe 或 PATH）")
        self.et_path.setFont(font("mono", 8.5))
        b1 = TacButton("选择…", "secondary", height=30)
        b2 = TacButton("重新检测", "secondary", height=30)
        b1.clicked.connect(self._pick_exiftool)
        b2.clicked.connect(self._detect_exiftool)
        r.addWidget(self.et_path, 1)
        r.addWidget(b1)
        r.addWidget(b2)
        ep.body.addLayout(r)
        ep.body.addWidget(caption("ExifTool 能读取几乎所有相机 RAW 的拍摄时间、GPS、镜头信息。没有它时会退回内置读取。"
                                  "运行程序目录下 tools\\get_exiftool.ps1 可自动下载。", "faint", 8.5))
        left.addWidget(ep)
        self._update_et()

        # ---- 日期 ----
        dp = Panel("日期识别", "Date resolution")
        self.cb_fn = QCheckBox("没有 EXIF 时，从文件名推测日期（微信、截图、手机命名）")
        self.cb_fn.setChecked(bool(self.s.get("date_use_filename", True)))
        self.cb_mt = QCheckBox("仍然没有时，用文件修改时间（标记为推测）")
        self.cb_mt.setChecked(bool(self.s.get("date_use_mtime", True)))
        self.cb_fn.toggled.connect(lambda v: self._set("date_use_filename", v, True))
        self.cb_mt.toggled.connect(lambda v: self._set("date_use_mtime", v, True))
        dp.body.addWidget(self.cb_fn)
        dp.body.addWidget(self.cb_mt)
        dp.body.addWidget(caption("文件名日期规则（从上到下依次尝试；正则命名分组 y m d H M S，或 ts13 毫秒时间戳 / ts10 秒时间戳）：",
                                  "dim", 8.5))
        self.pat = QTableWidget(0, 2)
        self.pat.setHorizontalHeaderLabels(["名称", "正则表达式"])
        self.pat.verticalHeader().setVisible(False)
        self.pat.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.pat.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.pat.setMinimumHeight(190)
        self.pat.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.pat.setColumnWidth(0, 200)
        self.pat.setWordWrap(False)
        self.pat.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._load_patterns()
        self.pat.itemChanged.connect(self._patterns_changed)
        dp.body.addWidget(self.pat)
        r = QHBoxLayout()
        ba = TacButton("添加规则", "secondary", height=28)
        bd = TacButton("删除选中", "secondary", height=28)
        br = TacButton("恢复默认", "secondary", height=28)
        ba.clicked.connect(self._add_pattern)
        bd.clicked.connect(self._del_pattern)
        br.clicked.connect(self._reset_patterns)
        for b in (ba, bd, br):
            r.addWidget(b)
        r.addStretch(1)
        dp.body.addLayout(r)
        r = QHBoxLayout()
        self.test_in = QLineEdit()
        self.test_in.setPlaceholderText("输入文件名测试，例如：微信图片_20231001123045.jpg")
        self.test_out = QLabel("")
        self.test_out.setFont(font("mono", 9))
        self.test_in.textChanged.connect(self._test_pattern)
        r.addWidget(self.test_in, 1)
        r.addWidget(self.test_out)
        dp.body.addLayout(r)
        left.addWidget(dp)

        # ---- 地区 ----
        gp = Panel("地区识别", "Geocoding")
        self.cb_gcj = QCheckBox("这些照片的 GPS 是 GCJ-02（火星坐标）")
        self.cb_gcj.setChecked(bool(self.s.get("gps_assume_gcj02")))
        self.cb_gcj.toggled.connect(lambda v: self._set("gps_assume_gcj02", v, True))
        gp.body.addWidget(self.cb_gcj)
        gp.body.addWidget(caption("手机和相机原图通常是 WGS-84，保持不勾选即可；只有少数在大陆写入 GCJ-02 的第三方 App 需要勾选。"
                                  "对省 / 市级分区影响很小。", "faint", 8.5))
        r = QHBoxLayout()
        self.cb_inf = QCheckBox("没有 GPS 的照片，按拍摄时间就近推测地点，时间窗口")
        self.cb_inf.setChecked(bool(self.s.get("geo_infer")))
        self.sp_inf = QSpinBox()
        self.sp_inf.setRange(5, 720)
        self.sp_inf.setSuffix(" 分钟")
        self.sp_inf.setValue(int(self.s.get("geo_infer_minutes", 30)))
        self.cb_inf.toggled.connect(lambda v: self._set("geo_infer", v, True))
        self.sp_inf.valueChanged.connect(lambda v: self._set("geo_infer_minutes", v, True))
        r.addWidget(self.cb_inf)
        r.addWidget(self.sp_inf)
        r.addStretch(1)
        gp.body.addLayout(r)
        gp.body.addWidget(caption("更准确的做法：在「时间与地点」页添加手机照片文件夹或 GPX 轨迹，按拍摄时间（考虑时区）补全 GPS；"
                                  "也可以按天手动指定地点。推测的地点在缩略图上有青色标记。", "faint", 8.5))
        left.addWidget(gp)
        left.addStretch(1)

        # ---- 色调阈值 ----
        cp = Panel("色调阈值", "Color thresholds")
        grid = QGridLayout()
        self._spin(grid, 0, "黑白判定（色度 P90 低于）", "color_mono_chroma", 0, 40, 0.5)
        self._spin(grid, 1, "低饱和判定（平均色度低于）", "color_low_sat_chroma", 0, 40, 0.5)
        self._spin(grid, 2, "冷暖差值超过", "color_warm_margin", 0, 1, 0.01)
        self._spin(grid, 3, "暗调（亮度中位数低于）", "color_dark_l", 0, 100, 1)
        self._spin(grid, 4, "亮调（亮度中位数高于）", "color_bright_l", 0, 100, 1)
        cp.body.addLayout(grid)
        cp.body.addWidget(caption("调整后预览和统计立即更新，不需要重新分析。", "faint", 8.5))
        right.addWidget(cp)

        # ---- 文件与安全 ----
        fp = Panel("文件与安全", "File safety")
        self.cb_live = QCheckBox("把同名 .MOV（实况照片视频）与照片一起移动（下次扫描生效）")
        self.cb_live.setChecked(bool(self.s.get("pair_live_photo", True)))
        self.cb_live.toggled.connect(lambda v: self._set("pair_live_photo", v))
        self.cb_pair = QCheckBox("RAW 与 JPG 在不同文件夹时，按文件名 + 拍摄时间重新配对（下次扫描生效）")
        self.cb_pair.setChecked(bool(self.s.get("pair_across_folders", True)))
        self.cb_pair.toggled.connect(lambda v: self._set("pair_across_folders", v))
        self.cb_hash = QCheckBox("跨磁盘移动时额外校验 SHA-256（更安全，更慢）")
        self.cb_hash.setChecked(bool(self.s.get("verify_hash")))
        self.cb_hash.toggled.connect(lambda v: self._set("verify_hash", v))
        fp.body.addWidget(self.cb_live)
        fp.body.addWidget(self.cb_pair)
        fp.body.addWidget(self.cb_hash)
        fp.body.addWidget(caption("始终生效的保护：先写日志再移动；RAW+JPG 与 .xmp/.aae 成组移动、失败整组退回；"
                                  "重名只加编号不覆盖；跨盘先复制校验再删源；删除一律进回收站。", "faint", 8.5))
        right.addWidget(fp)

        # ---- 性能 ----
        pp = Panel("性能", "Performance")
        grid = QGridLayout()
        grid.addWidget(QLabel("并行方式"), 0, 0)
        self.par = QComboBox()
        self.par.addItem("多进程（推荐，最快）", "process")
        self.par.addItem("多线程（兼容模式）", "thread")
        self.par.setCurrentIndex(max(0, self.par.findData(self.s.get("parallel_mode"))))
        self.par.currentIndexChanged.connect(lambda: self._set("parallel_mode", self.par.currentData()))
        grid.addWidget(self.par, 0, 1)
        grid.addWidget(QLabel("后台工作数"), 1, 0)
        self.wk = QSpinBox()
        self.wk.setRange(0, 32)
        self.wk.setSpecialValueText("自动")
        self.wk.setValue(int(self.s.get("workers") or 0))
        self.wk.valueChanged.connect(lambda v: self._set("workers", v))
        grid.addWidget(self.wk, 1, 1)
        grid.setColumnStretch(1, 1)
        pp.body.addLayout(grid)
        right.addWidget(pp)

        # ---- 插件 ----
        xp = Panel("扩展插件", "Plugins")
        self.pl = QTableWidget(0, 3)
        self.pl.setHorizontalHeaderLabels(["插件", "版本", "状态 / 说明"])
        self.pl.verticalHeader().setVisible(False)
        self.pl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.pl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pl.setMinimumHeight(110)
        self.pl.setRowCount(len(LOADED))
        for i, p in enumerate(LOADED):
            self.pl.setItem(i, 0, QTableWidgetItem(p.name))
            self.pl.setItem(i, 1, QTableWidgetItem(p.version))
            self.pl.setItem(i, 2, QTableWidgetItem(("加载失败：" + p.error) if p.error else (p.description or "已加载")))
        xp.body.addWidget(self.pl)
        r = QHBoxLayout()
        bo = TacButton("打开插件文件夹", "secondary", height=30)
        bo.clicked.connect(lambda: open_folder(str(plugin_dirs()[-1])))
        r.addWidget(bo)
        r.addStretch(1)
        xp.body.addLayout(r)
        xp.body.addWidget(caption("把 .py 插件放进插件文件夹并重启即可加载（新分析器 / 新分区条件 / 新页面）。"
                                  "示例见程序目录 plugins_examples/。", "faint", 8.5))
        right.addWidget(xp)

        # ---- 缓存 ----
        kp = Panel("缓存", "Cache")
        self.cache_lab = QLabel()
        self.cache_lab.setFont(font("mono", 9))
        kp.body.addWidget(self.cache_lab)
        r = QHBoxLayout()
        bc = TacButton("清空缓存", "secondary", height=30)
        bc.clicked.connect(self._clear_cache)
        bl = TacButton("打开日志文件夹", "secondary", height=30)
        bl.clicked.connect(lambda: open_folder(str(user_data_dir() / "logs")))
        r.addWidget(bc)
        r.addWidget(bl)
        r.addStretch(1)
        kp.body.addLayout(r)
        right.addWidget(kp)
        self._update_cache()

        # ---- 关于 ----
        ab = Panel("关于", "About")
        ab.body.addWidget(caption(
            f"影匣 PhotoCrate v{__version__} — 本地照片分区整理终端。<br>"
            "元数据：ExifTool（Phil Harvey）· RAW 预览：LibRaw / rawpy · HEIC：libheif / pillow-heif<br>"
            "国内行政区划：GeoToolCN（边界数据源自 DataV.GeoAtlas / 高德开放平台，仅供学习交流，商用前需取得授权）<br>"
            "海外地名：GeoNames（CC BY 4.0）", "dim", 8.5))
        ab.body.itemAt(0).widget().setTextFormat(Qt.TextFormat.RichText)
        right.addWidget(ab)
        right.addStretch(1)

        scroll.setWidget(inner)
        self.root.addWidget(scroll, 1)

    # ---- 通用 ------------------------------------------------------------
    def _set(self, key, value, reanalyze=False):
        self.ctx.set_setting(key, value)
        if reanalyze:
            self._reanalyze_timer.start()

    def _spin(self, grid, row, label, key, lo, hi, step):
        grid.addWidget(QLabel(label), row, 0)
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setDecimals(2 if step < 0.1 else 1)
        sp.setValue(float(self.s.get(key)))
        sp.valueChanged.connect(lambda v, k=key: self._set(k, float(v)))
        grid.addWidget(sp, row, 1)

    def _reanalyze(self):
        lib = self.ctx.library
        if not lib.groups:
            return
        self.ctx.status.emit("正在按新设置重新整理日期与地点…")
        self.ctx.run(lambda prog, cancel: lib.run_meta_analyzers(), None,
                     lambda _: (self.ctx.library_changed.emit(), self.ctx.status.emit("已按新设置更新")))

    def _motion(self, enabled):
        self.ctx.set_setting("ui_motion", enabled)
        motion.set_enabled(enabled)

    def _theme(self):
        name = "light" if self.t_light.isChecked() else "dark"
        self.ctx.set_setting("theme", name)
        self.ctx.theme_changed.emit()

    # ---- ExifTool ----
    def _update_et(self):
        lib = self.ctx.library
        p = theme.current()
        if lib.exiftool_path:
            self.et_status.setText(f"● ONLINE   v{lib.exiftool_ver}")
            self.et_status.setToolTip(lib.exiftool_path)
            if not self.et_path.text().strip():
                self.et_path.setPlaceholderText(f"自动找到：{lib.exiftool_path}")
            self.et_status.setStyleSheet(f"color: {p.ok};")
        else:
            self.et_status.setText("● OFFLINE  未找到 ExifTool，使用内置读取")
            self.et_status.setStyleSheet(f"color: {p.danger};")

    def _pick_exiftool(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 exiftool.exe", "", "ExifTool (exiftool*.exe exiftool);;所有文件 (*)")
        if path:
            self.et_path.setText(path)
            self._detect_exiftool()

    def _detect_exiftool(self):
        self.s.set("exiftool_path", self.et_path.text().strip())
        self.ctx.library.refresh_exiftool()
        self._update_et()
        self.ctx.settings_changed.emit("exiftool_path")

    # ---- 文件名规则 ----
    def _load_patterns(self):
        pats = self.s.get("filename_patterns") or DEFAULT_FILENAME_PATTERNS
        self.pat.blockSignals(True)
        self.pat.setRowCount(len(pats))
        for i, p in enumerate(pats):
            self.pat.setItem(i, 0, QTableWidgetItem(p.get("name", "")))
            it = QTableWidgetItem(p.get("regex", ""))
            it.setFont(font("mono", 8.5))
            self.pat.setItem(i, 1, it)
        self.pat.blockSignals(False)

    def _collect_patterns(self):
        out = []
        for i in range(self.pat.rowCount()):
            n = self.pat.item(i, 0).text() if self.pat.item(i, 0) else ""
            rx = self.pat.item(i, 1).text() if self.pat.item(i, 1) else ""
            if rx.strip():
                out.append({"name": n, "regex": rx})
        return out

    def _patterns_changed(self, *_):
        pats = self._collect_patterns()
        bad = [p["name"] or p["regex"] for p in pats if not compile_patterns([p])]
        if bad:
            self.test_out.setText("正则有误：" + "、".join(bad))
            return
        self._set("filename_patterns", pats, True)
        self._test_pattern()

    def _add_pattern(self):
        r = self.pat.rowCount()
        self.pat.insertRow(r)
        self.pat.setItem(r, 0, QTableWidgetItem("新规则"))
        self.pat.setItem(r, 1, QTableWidgetItem(r"(?P<y>20\d{2})(?P<m>\d{2})(?P<d>\d{2})"))

    def _del_pattern(self):
        rows = sorted({i.row() for i in self.pat.selectedItems()}, reverse=True)
        for r in rows:
            self.pat.removeRow(r)
        self._patterns_changed()

    def _reset_patterns(self):
        self.s.set("filename_patterns", None)
        self._load_patterns()
        self._reanalyze_timer.start()

    def _test_pattern(self):
        name = self.test_in.text().strip()
        if not name:
            self.test_out.setText("")
            return
        dt, rule = parse_filename_date(name, compile_patterns(self._collect_patterns()))
        self.test_out.setText(f"→ {dt:%Y-%m-%d %H:%M:%S}（{rule}）" if dt else "→ 未识别")

    # ---- 缓存 ----
    def _update_cache(self):
        st = self.ctx.library.cache.stats()
        thumbs = 0
        try:
            for root, _, files in os.walk(thumbs_dir()):
                thumbs += sum(os.path.getsize(os.path.join(root, f)) for f in files)
        except OSError:
            pass
        self.cache_lab.setText(f"元数据 {st['meta']} 条 · 预览 {st['preview']} 条 · 数据库 {human_size(st['bytes'])} · 缩略图 {human_size(thumbs)}")

    def _clear_cache(self):
        if not ask(self, "清空缓存", "清空后下次扫描需要重新读取全部元数据和生成缩略图（不会影响你的照片）。", ok="清空"):
            return
        self.ctx.library.cache.clear()
        shutil.rmtree(thumbs_dir(), ignore_errors=True)
        thumbs_dir()
        self._update_cache()
