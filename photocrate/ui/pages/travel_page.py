"""06 旅行与地图：自动分出的每次旅行（可设常住地、改名），地图上看照片位置与旅行路线。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ...config import user_cache_dir
from ...core.trips import Home, photo_coords, suggest_home
from .. import theme
from ..dialogs import info
from ..widgets.deco import Panel, TacButton, caption
from ..widgets.mapview import PROVIDERS, MapPoint, MapView, provider_for
from ..widgets.thumbgrid import PIXMAPS, ThumbGrid
from .base import Page

ALL_ROW = "__all__"


class TravelPage(Page):
    page_id = "travel"
    index = "06"
    title = "旅行与地图"
    en = "Trips & map // auto segmentation"
    sub = ("按“离开常住地 + 时间连续”自动切出每次旅行并命名；地图上按位置聚合显示照片，选中旅行时连出路线。"
           "分区编排里可用「旅行」条件（或模板 {旅行}/{第几天}）按旅行归档。")

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self._current_key = ALL_ROW
        self._filling = False
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ======== 左：常住地 + 旅行列表 ========
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(10)
        hp = Panel("常住地", "Home // not a trip", "")
        self.home_panel = hp
        self.homes = QListWidget()
        self.homes.setFixedHeight(64)
        hp.body.addWidget(self.homes)
        r = QHBoxLayout()
        self.home_edit = QLineEdit()
        self.home_edit.setPlaceholderText("如 Boca Raton, US · 湖南/邵阳 · 26.36,-80.10")
        self.home_edit.returnPressed.connect(self.add_home)
        r.addWidget(self.home_edit, 1)
        self.b_add = TacButton("添加", "secondary", height=30)
        self.b_del = TacButton("删除", "secondary", height=30)
        self.b_guess = TacButton("从照片识别", "secondary", height=30)
        for b in (self.b_add, self.b_del, self.b_guess):
            r.addWidget(b)
        hp.body.addLayout(r)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("间隔超过"))
        self.gap = QSpinBox()
        self.gap.setRange(6, 240)
        self.gap.setSuffix(" 小时")
        self.gap.setValue(int(ctx.settings.get("trip_gap_hours", 36)))
        r2.addWidget(self.gap)
        r2.addWidget(QLabel("算新旅行；至少"))
        self.minp = QSpinBox()
        self.minp.setRange(1, 50)
        self.minp.setSuffix(" 张")
        self.minp.setValue(int(ctx.settings.get("trip_min_photos", 3)))
        r2.addWidget(self.minp)
        r2.addStretch(1)
        hp.body.addLayout(r2)
        hp.body.addWidget(caption("在常住地（半径 50 km）拍的照片不算旅行；不设常住地时，按时间间隔把所有有地点的照片分成一次次出行。"
                                  "没有地点的照片若在旅途中拍摄，也会归入这次旅行。", "faint", 8))
        lv.addWidget(hp)

        tp = Panel("旅行", "Trips", "")
        self.trip_panel = tp
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["旅行（双击改名）", "日期", "天", "照片"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 136), (2, 34), (3, 50)):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            h.resizeSection(c, w)
        self.table.itemSelectionChanged.connect(self._trip_selected)
        self.table.itemChanged.connect(self._renamed)
        tp.body.addWidget(self.table, 1)
        tr = QHBoxLayout()
        self.b_reset = TacButton("恢复自动名", "secondary", height=28)
        self.b_reset.clicked.connect(self.reset_name)
        tr.addWidget(self.b_reset)
        tr.addStretch(1)
        self.b_part = TacButton("按旅行分区…", "primary", en="PARTITION", height=28)
        self.b_part.clicked.connect(self.to_partition)
        tr.addWidget(self.b_part)
        tp.body.addLayout(tr)
        lv.addWidget(tp, 1)
        left.setMinimumWidth(440)
        left.setMaximumWidth(560)
        split.addWidget(left)

        # ======== 右：地图 + 照片 ========
        vs = QSplitter(Qt.Orientation.Vertical)
        vs.setChildrenCollapsible(False)
        mp = Panel("地图", "Map", "")
        self.map_panel = mp
        self.tiles = QComboBox()
        self.tiles.addItem("底图：随主题", "auto")
        for k, v in PROVIDERS.items():
            self.tiles.addItem("底图：" + v[0], k)
        self.tiles.setCurrentIndex(max(0, self.tiles.findData(ctx.settings.get("map_tiles", "auto"))))
        self.tiles.currentIndexChanged.connect(self._tiles_changed)
        mp.add_header_widget(self.tiles)
        self.map = MapView(str(user_cache_dir() / "tiles"), self._provider())
        self.map.thumb_loader = PIXMAPS.get
        self.map.cluster_clicked.connect(self._cluster_clicked)
        mp.body.addWidget(self.map, 1)
        vs.addWidget(mp)
        gp = Panel("照片", "Photos", "")
        self.gpanel = gp
        self.grid = ThumbGrid(selectable=True)
        self.grid.set_cell_width(130)
        gp.body.addWidget(self.grid, 1)
        vs.addWidget(gp)
        vs.setSizes([520, 260])
        split.addWidget(vs)
        split.setSizes([470, 900])
        self.root.addWidget(split, 1)

        self._regroup = QTimer(self)
        self._regroup.setSingleShot(True)
        self._regroup.setInterval(300)
        self._regroup.timeout.connect(self._apply_params)
        self.gap.valueChanged.connect(lambda _: self._regroup.start())
        self.minp.valueChanged.connect(lambda _: self._regroup.start())
        self.b_add.clicked.connect(self.add_home)
        self.b_del.clicked.connect(self.del_home)
        self.b_guess.clicked.connect(self.guess_home)
        ctx.library_changed.connect(self.refresh)
        ctx.files_changed.connect(self.refresh)
        ctx.theme_changed.connect(lambda: self.map.set_provider(self._provider()))
        self._fill_homes()

    def on_show(self):
        self.refresh()

    # ---- 常住地 ----------------------------------------------------------
    def _home_dicts(self) -> list[dict]:
        return list(self.ctx.settings.get("home_places") or [])

    def _fill_homes(self):
        self.homes.clear()
        hs = self._home_dicts()
        for h in hs:
            hm = Home.from_dict(h)
            where = f"{hm.lat:.3f}, {hm.lon:.3f} · 半径 {hm.radius_km:.0f} km" if hm.lat is not None else "按地名判断"
            it = QListWidgetItem(f"{hm.label}    （{where}）")
            self.homes.addItem(it)
        if not hs:
            it = QListWidgetItem("（未设置：所有有地点的照片都按时间分段）")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.homes.addItem(it)
        self.home_panel.set_meta(f"{len(hs)} SET")

    def _save_homes(self, hs: list[dict]):
        self.ctx.set_setting("home_places", hs)
        self._fill_homes()
        self._retrip()

    def add_home(self):
        text = self.home_edit.text().strip()
        if not text:
            return
        self.b_add.setEnabled(False)

        def job(prog, cancel):
            from ...core.places import PlaceIndex
            from ...core.trips import resolve_home
            return resolve_home(text, PlaceIndex.get())

        def done(h):
            self.b_add.setEnabled(True)
            if h is None:
                return
            if h.lat is None and not h.country:
                info(self, "没有找到这个地点", f"“{text}”没有找到坐标，将按地名判断（照片地点的省 / 市 / 区名与它相同即算在家）。"
                     "<br>更准确的写法：中文“省/市”（如 湖南/邵阳），英文城市名加国家代码（如 Boca Raton, US），"
                     "或直接填“纬度,经度”。")
            self.home_edit.clear()
            self._save_homes(self._home_dicts() + [h.to_dict()])

        self.ctx.run(job, None, done, lambda tb: (self.b_add.setEnabled(True),
                                                  info(self, "出错", f"<pre>{tb[-800:]}</pre>")))

    def del_home(self):
        i = self.homes.currentRow()
        hs = self._home_dicts()
        if 0 <= i < len(hs):
            del hs[i]
            self._save_homes(hs)

    def guess_home(self):
        s = suggest_home([g for g in self.ctx.library.groups if g.files])
        if not s:
            info(self, "无法识别常住地", "照片里没有一个地方占到拍摄天数的三分之一以上（或照片少于 20 天），"
                 "看起来这批照片大多在旅途中拍的。请手动添加常住地，或者不设常住地。")
            return
        if any(abs((h.get("lat") or 999) - s["lat"]) < 0.3 and abs((h.get("lon") or 999) - s["lon"]) < 0.3
               for h in self._home_dicts()):
            info(self, "已经设置过", f"识别到的常住地 {s['label']} 已在列表中。")
            return
        self._save_homes(self._home_dicts() + [{k: v for k, v in s.items() if k != "share"}])
        self.ctx.status.emit(f"识别到常住地：{s['label']}（占拍摄天数 {s['share']:.0%}）")

    def _apply_params(self):
        self.ctx.set_setting("trip_gap_hours", int(self.gap.value()))
        self.ctx.set_setting("trip_min_photos", int(self.minp.value()))
        self._retrip()

    def _retrip(self):
        trips = self.ctx.library.retrip()
        self.ctx.status.emit(f"旅行分段：{len(trips)} 次旅行")
        self.refresh()

    # ---- 旅行列表 --------------------------------------------------------
    def refresh(self):
        lib = self.ctx.library
        trips = lib.trips
        self._filling = True
        self.table.setRowCount(len(trips) + 1)
        n_loc = sum(1 for g in lib.groups if g.files and photo_coords(g))
        p = theme.current()
        rows = [(ALL_ROW, f"全部照片（{n_loc} 张有位置）", "", "", "")] + \
               [(t.key, t.name, t.span_text, str(t.days), str(t.photos)) for t in trips]
        sel_row = 0
        for i, (key, name, span, days, n) in enumerate(rows):
            for c, v in enumerate((name, span, days, n)):
                it = QTableWidgetItem(v)
                it.setToolTip(v)
                it.setData(Qt.ItemDataRole.UserRole, key)
                flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                if c == 0 and key != ALL_ROW:
                    flags |= Qt.ItemFlag.ItemIsEditable
                    t = next(t for t in trips if t.key == key)
                    if t.name != t.auto_name:
                        it.setToolTip(f"自动名：{t.auto_name}")
                        it.setForeground(theme.current().c("accent2"))
                    else:
                        it.setToolTip("双击改名")
                if key == ALL_ROW:
                    it.setForeground(theme.current().c("ink2"))
                it.setFlags(flags)
                self.table.setItem(i, c, it)
            if key == self._current_key:
                sel_row = i
        self._filling = False
        self.trip_panel.set_meta(f"{len(trips)} TRIPS")
        self.table.selectRow(sel_row)
        self._trip_selected()

    def _row_key(self) -> str:
        items = self.table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else ALL_ROW

    def current_trip(self):
        key = self._row_key()
        return next((t for t in self.ctx.library.trips if t.key == key), None)

    def _trip_selected(self):
        if self._filling:
            return
        lib = self.ctx.library
        self._current_key = self._row_key()
        t = self.current_trip()
        if t is None:
            groups = [g for g in lib.groups if g.files]
            title = "全部照片"
        else:
            groups = [g for gid in t.gids if (g := lib.by_gid(gid)) is not None and g.files]
            title = t.name
        groups.sort(key=lambda g: (g.date is None, g.date or 0, g.name))
        pts, route = [], []
        for g in groups:
            c = photo_coords(g)
            if c is None:
                continue
            geo = g.feats.get("geo") or {}
            pts.append(MapPoint(g.gid, c[0], c[1], g.thumb, geo.get("city") or geo.get("province") or ""))
            if t is not None:
                route.append(c)
        self.map.set_points(pts, route if len(route) > 1 else None, fit=True)
        self.map_panel.set_meta(f"{len(pts)} PINNED · {title}")
        self._show(groups, title)
        self.b_reset.setEnabled(t is not None and t.name != t.auto_name)

    def _show(self, groups, title):
        self.grid.settings_snapshot = self.ctx.settings.snapshot()
        self.grid.set_groups(groups)
        self.gpanel.set_meta(f"{len(groups)} ITEMS · {title}")

    def _cluster_clicked(self, gids):
        lib = self.ctx.library
        groups = sorted((g for gid in gids if (g := lib.by_gid(gid)) is not None),
                        key=lambda g: (g.date is None, g.date or 0))
        geo = (groups[0].feats.get("geo") or {}) if groups else {}
        self._show(groups, f"地图上选中 · {geo.get('city') or geo.get('province') or ''}")

    def _renamed(self, item: QTableWidgetItem):
        if self._filling or item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key == ALL_ROW:
            return
        names = dict(self.ctx.settings.get("trip_names") or {})
        text = item.text().strip()
        t = next((t for t in self.ctx.library.trips if t.key == key), None)
        if not text or (t and text == t.auto_name):
            names.pop(key, None)
        else:
            names[key] = text
        self.ctx.set_setting("trip_names", names)
        QTimer.singleShot(0, self._retrip)

    def reset_name(self):
        t = self.current_trip()
        if t is None:
            return
        names = dict(self.ctx.settings.get("trip_names") or {})
        names.pop(t.key, None)
        self.ctx.set_setting("trip_names", names)
        self._retrip()

    def to_partition(self):
        page = self.window().page("partition") if hasattr(self.window(), "page") else None
        if page is not None and hasattr(page, "apply_preset_rules"):
            page.apply_preset_rules([{"dim": "trip", "opts": {"mode": "trip/daydate"}}])
        self.ctx.navigate.emit("partition")

    # ---- 底图 ------------------------------------------------------------
    def _provider(self) -> str:
        return provider_for(self.ctx.settings.get("map_tiles", "auto"), theme.current().name == "dark")

    def _tiles_changed(self, _):
        self.ctx.set_setting("map_tiles", self.tiles.currentData())
        self.map.set_provider(self._provider())
