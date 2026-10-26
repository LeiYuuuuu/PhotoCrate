"""07 AI 搜图：模型下载与状态、文字搜图 / 以图搜图、场景分类。全部在本机离线运行。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from ...ai import models
from ...ai.scenes import SCENE_GROUPS, SCENES
from .. import theme
from ..dialogs import ask, info, run_with_progress
from ..widgets.deco import BarList, Panel, StatCard, TacButton, caption
from ..widgets.thumbgrid import ThumbGrid
from .base import Page
from ...i18n import is_en, t as _t

GROUP_COLORS = {"自然风光": "#3FAE5A", "城市建筑": "#3C7BD9", "人物": "#E2453A", "美食": "#EE8A1E",
                "动物植物": "#8E5AC9", "室内": "#8D8D84", "文档截图": "#55554E"}


class AiPage(Page):
    page_id = "ai"
    index = "07"
    title = _t("AI 搜图")
    en = "AI search // scenes · text · similar"
    sub = (_t("输入一句话找照片（如“铁塔夜景”“一碗拉面”“海边日落”），或选中一张照片找相似的；照片按画面内容自动分成场景。"
           "使用开源中文 CLIP 模型，全部在本机运行，照片不会上传。"))

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        p = theme.current()
        top = QHBoxLayout()
        top.setSpacing(12)

        # ---- 模型 ----
        mp = Panel(_t("模型"), "Models // offline", "")
        mp.setFixedWidth(560 if is_en() else 520)
        self.model_panel = mp
        self.rows: dict[str, tuple[QLabel, TacButton]] = {}
        for key in ("clip", "face", "expr", "ocr"):
            r = QHBoxLayout()
            name = QLabel(models.MODELS[key]["label"])
            name.setMinimumWidth(220)
            st = QLabel("")
            st.setProperty("role", "dim")
            b = TacButton(_t("下载"), "primary", height=28)
            b.clicked.connect(lambda _=False, k=key: self.download(k))
            r.addWidget(name)
            r.addWidget(st, 1)
            r.addWidget(b)
            mp.body.addLayout(r)
            self.rows[key] = (st, b)
        r = QHBoxLayout()
        r.addWidget(QLabel(_t("下载源")))
        self.mirror = QComboBox()
        for v, t in (("auto", _t("自动（官方失败时用国内镜像）")), ("hf", _t("Hugging Face 官方")), ("mirror", _t("国内镜像 hf-mirror.com"))):
            self.mirror.addItem(t, v)
        self.mirror.setCurrentIndex(max(0, self.mirror.findData(ctx.settings.get("ai_mirror", "auto"))))
        self.mirror.currentIndexChanged.connect(lambda _: ctx.set_setting("ai_mirror", self.mirror.currentData()))
        r.addWidget(self.mirror, 1)
        self.b_run = TacButton(_t("分析全部照片"), "secondary", en="ANALYZE", height=30)
        self.b_run.clicked.connect(self.analyze)
        r.addWidget(self.b_run)
        mp.body.addLayout(r)
        mp.body.addWidget(caption(_t("模型下载到 ") + str(models.models_dir()) + _t("；之后每次扫描会自动识别新照片"
                                  "（结果有缓存）。人脸模型下载后需要重新分析一次。"), "faint", 8))
        top.addWidget(mp)

        sp = Panel(_t("索引"), "Index", "")
        cr = QHBoxLayout()
        self.c_idx = StatCard(_t("已识别场景"), "INDEXED", accent=p.accent)
        self.c_faces = StatCard(_t("人脸"), "FACES", accent=p.accent2)
        self.c_people = StatCard(_t("人物"), "PEOPLE", accent=p.ok)
        for c in (self.c_idx, self.c_faces, self.c_people):
            cr.addWidget(c)
        sp.body.addLayout(cr)
        top.addWidget(sp, 1)
        self.root.addLayout(top)

        # ---- 搜索 ----
        mid = QHBoxLayout()
        mid.setSpacing(12)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        scp = Panel(_t("场景"), "Scenes", "")
        self.scene_panel = scp
        self.mode = QComboBox()
        self.mode.addItem(_t("细分场景"), "scene")
        self.mode.addItem(_t("大类"), "group")
        self.mode.currentIndexChanged.connect(lambda _: self.refresh())
        scp.add_header_widget(self.mode)
        self.bars = BarList(max_items=24, row_h=22, label_w=96)
        self.bars.clicked.connect(self.show_scene)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.bars)
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        scp.body.addWidget(sc, 1)
        lv.addWidget(scp, 1)
        left.setFixedWidth(330)
        mid.addWidget(left)

        rp = Panel(_t("搜索结果"), "Results", "")
        self.res_panel = rp
        sr = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText(_t("输入要找的画面：铁塔夜景、拉面、海边日落、穿红衣服的人、猫…"))
        self.query.returnPressed.connect(self.search)
        sr.addWidget(self.query, 1)
        self.b_search = TacButton(_t("搜索"), "primary", en="SEARCH", height=32)
        self.b_search.clicked.connect(self.search)
        sr.addWidget(self.b_search)
        self.b_similar = TacButton(_t("找相似（选中一张）"), "secondary", height=32)
        self.b_similar.clicked.connect(self.similar)
        sr.addWidget(self.b_similar)
        sr.addWidget(QLabel(_t("显示前")))
        self.topn = QSpinBox()
        self.topn.setRange(10, 1000)
        self.topn.setValue(60)
        self.topn.setSuffix(_t(" 张"))
        sr.addWidget(self.topn)
        rp.body.addLayout(sr)
        # 找相似时显示“目标照片”
        self.target = QFrame()
        self.target.setObjectName("simTarget")
        self.target.setStyleSheet(f"#simTarget {{ border: 1px solid {p.accent}; border-left: 4px solid {p.accent}; }}")
        tl = QHBoxLayout(self.target)
        tl.setContentsMargins(8, 6, 8, 6)
        tl.setSpacing(12)
        self.t_img = QLabel()
        self.t_img.setFixedSize(150, 100)
        self.t_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.t_img.setStyleSheet("background: #0B0C0D;")
        tl.addWidget(self.t_img)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(caption(_t("目标照片 · TARGET"), "faint", 8))
        self.t_name = QLabel("")
        self.t_name.setStyleSheet("font-size: 13pt; font-weight: 600;")
        tv.addWidget(self.t_name)
        self.t_info = caption("", "dim", 9)
        self.t_info.setWordWrap(True)
        tv.addWidget(self.t_info)
        tv.addStretch(1)
        tl.addLayout(tv, 1)
        tb = QVBoxLayout()
        self.b_tview = TacButton(_t("大图对比"), "secondary", height=28)
        self.b_tview.setToolTip(_t("在大图里依次查看目标和结果；按 C 可以并排对比"))
        self.b_tview.clicked.connect(self.view_target)
        self.b_tclose = TacButton(_t("清除"), "secondary", height=28)
        self.b_tclose.clicked.connect(self.clear_results)
        tb.addWidget(self.b_tview)
        tb.addWidget(self.b_tclose)
        tb.addStretch(1)
        tl.addLayout(tb)
        self.target.hide()
        self.target_gid: int | None = None
        rp.body.addWidget(self.target)
        self.grid = ThumbGrid(selectable=True)
        self.grid.set_cell_width(150)
        rp.body.addWidget(self.grid, 1)
        mid.addWidget(rp, 1)
        self.root.addLayout(mid, 1)

        self.grid.selection_changed.connect(lambda g: self.b_similar.setEnabled(len(g) == 1 and self._ready()))
        ctx.library_changed.connect(self.refresh)
        ctx.files_changed.connect(self.refresh)

    def on_show(self):
        self.refresh()

    def _ready(self) -> bool:
        return self.ctx.library.ai.clip_ready() and bool(self.ctx.library.ai.emb)

    # ---- 状态 ------------------------------------------------------------
    def refresh(self):
        lib = self.ctx.library
        for key, (st, b) in self.rows.items():
            ready = models.ready(key)
            text = models.status_text(key)
            st.setText(text)
            col = theme.current().ok if ready and "onnxruntime" not in text else theme.current().warn
            st.setStyleSheet(f"color: {col};")
            b.setVisible(not ready)
        s = lib.ai.stats(lib.groups)
        n = s["photos"] or 1
        self.c_idx.set(f"{s['indexed']}", s["indexed"] / n, f"/ {s['photos']}")
        self.c_faces.set(str(s["faces"]), s["faces_done"] / n, _t('张脸 · {0} 张已检测', s['faces_done']))
        self.c_people.set(str(s["people"]), None, _t('人 · {0} 已命名', s['named']))
        mode = self.mode.currentData()
        counts: dict[str, int] = {}
        for g in lib.groups:
            sc = g.feats.get("scene") if g.files else None
            if sc:
                k = sc["group"] if mode == "group" else sc["scene"]
                counts[k] = counts.get(k, 0) + 1
        gcol = {name: GROUP_COLORS.get(grp) for name, grp, _ in SCENES}
        gcol.update({g: GROUP_COLORS.get(g) for g in SCENE_GROUPS})
        self.bars.set_items(sorted(((k, v, gcol.get(k)) for k, v in counts.items()), key=lambda t: -t[1]))
        self.scene_panel.set_meta("")
        ok = self._ready()
        self.b_search.setEnabled(ok)
        self.query.setEnabled(ok)
        self.b_similar.setEnabled(ok and len(self.grid.selected_gids()) == 1)
        if not ok:
            hint = (_t("先下载“中文 CLIP”模型，再点“分析全部照片”。") if not lib.ai.clip_ready()
                    else _t("还没有分析过照片：点“分析全部照片”。"))
            self.res_panel.set_meta(hint)
        elif not self.grid.model_.groups:
            self.res_panel.set_meta(_t("输入描述后按回车搜索，或点左边的场景"))

    # ---- 下载 / 分析 -----------------------------------------------------
    def download(self, key: str):
        size = models.download_size(key)
        if not ask(self, _t("下载 AI 模型"), _t('将下载 <b>{0}</b>（约 {1:.0f} MB）到<br>{2}<br><br>来源：{3}；许可：{4}。只需下载一次。', models.MODELS[key]['label'], size / 1e6, models.models_dir(), 'Hugging Face' if key in ('clip', 'ocr') else 'GitHub（OpenCV Zoo）', models.MODELS[key]['license']), ok=_t("下载"), en="DOWNLOAD"):
            return
        mirror = self.ctx.settings.get("ai_mirror", "auto")

        def job(prog, cancel):
            return models.download(key, lambda d, t, c: prog((d, t, c)), cancel, mirror)

        res, tb = run_with_progress(self.ctx, self, _t("正在下载模型"), "DOWNLOADING", job)
        self.ctx.library.ai.unload()
        self.ctx.library.ocr.unload()
        self.refresh()
        if tb:
            last = tb.strip().splitlines()[-1]
            info(self, _t("下载失败"), _t('{0}<br><br>可以在“下载源”里换成国内镜像再试；或手动下载后放进 {1}。', last, models.models_dir()),
                 "ERROR")
            return
        if models.ready(key) and key == "expr":          # 只在挑片时用，不用重新分析全部照片
            info(self, _t("下载完成"), _t("表情识别模型已就绪。到「05 挑片」页，连拍组会自动重新检查表情。"), "READY")
        elif models.ready(key):
            if ask(self, _t("下载完成"), _t("模型已就绪。现在分析全部照片吗？（会重新扫描一遍，已有结果走缓存）"),
                   ok=_t("开始分析"), cancel=_t("稍后"), en="READY"):
                self.analyze()

    def analyze(self):
        lib = self.ctx.library
        if not lib.groups and not self.ctx.settings.get("source_dirs"):
            info(self, _t("还没有照片"), _t("请先在「资源接入」页添加照片文件夹。"))
            return
        if not (lib.ai.clip_ready() or models.ready("face")):
            info(self, _t("还没有模型"), _t("请先下载模型。"))
            return
        src = self.window().page("source") if hasattr(self.window(), "page") else None
        if src is None:
            return
        self.ctx.navigate.emit("source")
        src.start_scan()

    # ---- 搜索 ------------------------------------------------------------
    def _show(self, ranked: list[tuple[int, float]], title: str, fmt: str = "{:.2f}", target=None):
        lib = self.ctx.library
        n = int(self.topn.value())
        groups, labels = [], {}
        for gid, score in ranked[:n]:
            g = lib.by_gid(gid)
            if g is None or not g.files:
                continue
            groups.append(g)
            labels[gid] = {"tag": fmt.format(score)}
        self.grid.settings_snapshot = self.ctx.settings.snapshot()
        self.grid.set_groups(groups, labels)
        self.res_panel.set_meta(f"{len(groups)} ITEMS · {title}")
        self._set_target(target)

    def _set_target(self, g) -> None:
        self.target_gid = g.gid if g is not None else None
        if g is None:
            self.target.hide()
            return
        pm = QPixmap(g.thumb) if g.thumb else QPixmap()
        if pm.isNull():
            self.t_img.setText(_t("无预览"))
        else:
            self.t_img.setPixmap(pm.scaled(self.t_img.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
        self.t_name.setText(g.name)
        bits = [g.date.strftime("%Y-%m-%d %H:%M")] if g.date else []
        sc = g.feats.get("scene")
        if sc:
            bits.append(_t('场景：{0}', _t(sc['scene'])))
        bits.append(_t('找到 {0} 张相似的照片，按相似度从高到低排列', len(self.grid.model_.groups))
                    if self.grid.model_.groups else _t("没有找到明显相似的照片"))
        self.t_info.setText("   ·   ".join(bits))
        self.target.show()

    def view_target(self):
        g = self.ctx.library.by_gid(self.target_gid) if self.target_gid is not None else None
        if g is not None:
            self.ctx.open_viewer([g] + list(self.grid.model_.groups), 0, self.window())

    def clear_results(self):
        self.grid.set_groups([], {})
        self._set_target(None)
        self.res_panel.set_meta(_t("输入描述后按回车搜索，或点左边的场景"))

    def search(self):
        q = self.query.text().strip()
        if not q or not self._ready():
            return
        try:
            ranked = self.ctx.library.ai.search_text(q)
        except Exception as e:
            info(self, _t("搜索出错"), str(e), "ERROR")
            return
        self._show(ranked, _t('“{0}” · 只显示明显相关的，按相关度排序（分数越高越像）', q) if ranked
                   else _t('“{0}” · 没有找到明显相关的照片，换个说法试试', q))

    def similar(self):
        gids = self.grid.selected_gids()
        if len(gids) != 1:
            return
        lib = self.ctx.library
        g = lib.by_gid(gids[0])
        try:
            ranked = lib.ai.search_similar(gids[0])
        except Exception as e:
            info(self, _t("找相似出错"), str(e), "ERROR")
            return
        self._show(ranked, _t('与 {0} 相似 · 百分比 = 综合相似度（内容 + 颜色构图）', g.name if g else ''),
                   fmt="{:.0%}", target=g)

    def show_scene(self, label: str):
        lib = self.ctx.library
        mode = self.mode.currentData()
        gs = [g for g in lib.groups if g.files and g.feats.get("scene")
              and (g.feats["scene"]["group"] if mode == "group" else g.feats["scene"]["scene"]) == label]
        gs.sort(key=lambda g: -float(g.feats["scene"].get("prob") or 0))
        self.grid.settings_snapshot = self.ctx.settings.snapshot()
        self.grid.set_groups(gs, {g.gid: {"tag": f"{label} {g.feats['scene']['prob']:.0%}"} for g in gs})
        self._set_target(None)
        self.res_panel.set_meta(_t('{0} ITEMS · 场景：{1}（按把握度排序）', len(gs), label))
