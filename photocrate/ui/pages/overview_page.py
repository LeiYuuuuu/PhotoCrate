"""03 统计概览：时间、地区、色调、相机、类型、来源、画幅、清晰度的分布；点击条目查看对应照片。"""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ...analyzers.builtin import BLUR_LABELS, ORIENTATION_LABELS, SOURCE_LABELS, blur_state, classify_color
from ...core.stats import MAIN_COLOR_SWATCH, TONE_SWATCH, WARMTH_SWATCH, compute_stats
from ...ops.pathsafe import human_size
from .. import theme
from ..widgets.deco import BarList, Panel, StatCard, TimelineChart, caption
from ..widgets.thumbgrid import ThumbGrid
from .base import Page


def _region_key(g):
    geo = g.feats.get("geo") or {}
    if not geo.get("country"):
        return "未知地点"
    return (geo.get("province") or "中国") if geo.get("in_china") else geo.get("country")


class OverviewPage(Page):
    page_id = "overview"
    index = "03"
    title = "统计概览"
    en = "Data overview // distribution"
    sub = "一眼看清照片库的构成。点击任意条目，下方会列出对应的照片。"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("PageRoot")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(12)

        cards = QHBoxLayout()
        cards.setSpacing(8)
        p = theme.current()
        self.c_total = StatCard("照片总数", "Total")
        self.c_size = StatCard("占用空间", "Volume")
        self.c_span = StatCard("时间跨度", "Span")
        self.c_place = StatCard("去过的地区", "Regions", accent=p.accent2)
        self.c_cam = StatCard("相机 / 手机", "Devices")
        self.c_blur = StatCard("疑似模糊", "Blurry", accent=p.danger)
        for c in (self.c_total, self.c_size, self.c_span, self.c_place, self.c_cam, self.c_blur):
            cards.addWidget(c)
        lay.addLayout(cards)

        tl = Panel("时间分布", "Timeline // by month")
        self.timeline = TimelineChart()
        tl.body.addWidget(self.timeline)
        lay.addWidget(tl)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.bars: dict[str, tuple[BarList, Panel]] = {}

        def add(key, title, en, r, c, label_w=120, max_items=12):
            pnl = Panel(title, en)
            b = BarList(max_items=max_items, label_w=label_w)
            b.clicked.connect(lambda lab, k=key: self._drill(k, lab))
            pnl.body.addWidget(b)
            pnl.body.addStretch(1)
            grid.addWidget(pnl, r, c)
            self.bars[key] = (b, pnl)

        add("region", "地区分布", "Regions", 0, 0, 120, 14)
        add("warmth", "色调 · 冷暖", "Color warmth", 0, 1, 90)
        add("main", "色调 · 主色", "Dominant hue", 0, 2, 90)
        add("camera", "相机型号", "Camera models", 1, 0, 150)
        add("kind", "文件类型", "File types", 1, 1, 90)
        add("tone", "明暗", "Tonal key", 1, 2, 90)
        add("source", "来源", "Source", 2, 0, 150)
        add("orientation", "横竖幅", "Orientation", 2, 1, 90)
        add("blur", "清晰度", "Sharpness", 2, 2, 90)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        lay.addLayout(grid)

        dp = Panel("筛选结果", "Drill-down", "")
        self.drill_panel = dp
        self.drill_note = caption("点击上方任意一条，在这里查看对应的照片。", "faint", 9)
        dp.body.addWidget(self.drill_note)
        self.grid = ThumbGrid(selectable=False)
        self.grid.set_cell_width(140)
        self.grid.setMinimumHeight(360)
        dp.body.addWidget(self.grid)
        lay.addWidget(dp)

        scroll.setWidget(inner)
        self.root.addWidget(scroll, 1)
        self.timeline.clicked.connect(lambda ym: self._drill("month", ym))
        ctx.library_changed.connect(self.refresh)
        ctx.files_changed.connect(self.refresh)
        ctx.settings_changed.connect(lambda k: self.refresh() if k.startswith(("color_", "blur_")) else None)

    def on_show(self):
        self.refresh()

    def refresh(self):
        lib = self.ctx.library
        s = self.ctx.settings.snapshot()
        st = compute_stats(lib.groups, s)
        n = st["total"] or 1
        self.c_total.set(f"{st['total']}", None, f"组 · {st['files']} 文件")
        sz = human_size(st["bytes"]).split()
        self.c_size.set(sz[0], None, sz[1] if len(sz) > 1 else "")
        months = st["months"]
        if months:
            ks = sorted(months)
            self.c_span.set(f"{ks[0][:4]}–{ks[-1][2:4]}", None, f"{len(ks)} 个月")
        else:
            self.c_span.set("—", None, "")
        known = [r for r in st["region"] if r[0] != "未知地点"]
        self.c_place.set(f"{len(known)}", st["gps"] / n, "个省/国家")
        self.c_cam.set(f"{len([c for c in st['camera'] if c[0] != '未知相机'])}", None, "台")
        blurry = dict(st["blur"]).get("模糊", 0)
        self.c_blur.set(f"{blurry}", blurry / n, "张")
        self.timeline.set_data(months)

        self.bars["region"][0].set_items((k, v, None) for k, v in st["region"])
        self.bars["warmth"][0].set_items((k, v, WARMTH_SWATCH.get(k)) for k, v in st["warmth"])
        self.bars["main"][0].set_items((k, v, MAIN_COLOR_SWATCH.get(k)) for k, v in st["main_color"])
        self.bars["tone"][0].set_items((k, v, TONE_SWATCH.get(k)) for k, v in st["tone"])
        self.bars["camera"][0].set_items((k, v, None) for k, v in st["camera"])
        self.bars["kind"][0].set_items((k, v, None) for k, v in st["kind"])
        self.bars["source"][0].set_items((k, v, None) for k, v in st["source"])
        self.bars["orientation"][0].set_items((k, v, None) for k, v in st["orientation"])
        self.bars["blur"][0].set_items((k, v, None) for k, v in st["blur"])
        for key, (b, pnl) in self.bars.items():
            pnl.set_meta(f"{len(b.items)} CLASSES")

    def _drill(self, key: str, label: str):
        lib = self.ctx.library
        s = self.ctx.settings.snapshot()
        thr = float(s.get("blur_threshold", 60))

        def color(g, k):
            c = classify_color(g.feats.get("color"), s)
            return c[k] if c else None

        fn = {
            "month": lambda g: g.date and f"{g.date.year:04d}-{g.date.month:02d}",
            "region": _region_key,
            "warmth": lambda g: color(g, "warmth"),
            "main": lambda g: color(g, "main"),
            "tone": lambda g: color(g, "tone"),
            "camera": lambda g: g.feat("camera", "model") or "未知相机",
            "kind": lambda g: g.kind_label,
            "source": lambda g: SOURCE_LABELS.get(g.feat("camera", "source"), "其他"),
            "orientation": lambda g: ORIENTATION_LABELS.get(g.feat("shape", "orientation"), "未知"),
            "blur": lambda g: BLUR_LABELS[blur_state(g, thr)],
        }.get(key)
        if fn is None or label.startswith("其余"):
            return
        gs = [g for g in lib.groups if g.files and fn(g) == label]
        gs.sort(key=lambda g: (g.date is None, g.date or 0))
        self.grid.settings_snapshot = s
        self.grid.delegate.highlight_blurry = key == "blur"
        self.grid.delegate.blur_threshold = thr
        self.grid.set_groups(gs)
        self.drill_note.setText(f"「{label}」共 {len(gs)} 张照片")
        self.drill_panel.set_meta(f"{len(gs)} ITEMS")
