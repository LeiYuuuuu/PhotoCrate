"""00 照片：存储模式的主界面（类似 iOS 照片，但更会找）。

- 图库：年 / 月 / 日 / 全部 四级，点年进月、点月进日；正方形缩略图按天分段，段头是日期和地点。
- 相簿：自动归类的智能相簿（媒体类型与收藏、我的相簿、人物、地点、旅行、事物与场景），点开看照片。
- 搜索：一个框搜照片里的文字、地点、人物、场景、日期 / 时段、相机、相簿…；下面给出每个词命中的类别（点一下只按那类找），
  模型就绪时再补上 AI 画面搜索的结果。照片里的文字识别在后台进行（第一次下载约 16 MB 的模型）。
- 单张照片：大图 + 信息 + 照片里的文字（命中的行高亮），可收藏、编辑（基础调整 / 滤镜 / 裁剪）、送去「修图」模式。
- 自动同步：顶栏右边选一个文件夹，往里面拷进 / 删掉照片，这里自动跟着变（ui/autosync.py）。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QDesktopServices, QFontMetrics, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSlider,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import theme
from ..dialogs import _Base, ask, info
from ..pages.base import Page
from ..theme import font
from ..widgets.deco import TacButton, caption
from ..widgets.thumbgrid import HOOKS, reveal_in_explorer
from .detail import PhotoDetail
from .grid import Card, CardGrid, GridSection, PhotoGrid
from ...ai import models
from ...core import smart
from ...i18n import t as _t

V_CARDS, V_GRID, V_ALBUMS, V_ALBUM, V_SEARCH, V_DETAIL, V_EMPTY = range(7)


def ask_text(parent, title: str, label: str, value: str = "", ok: str = _t("确定")) -> str | None:
    d = _Base(parent, title, "INPUT")
    d.body.addWidget(QLabel(label))
    e = QLineEdit(value)
    e.setMinimumWidth(360)
    e.selectAll()
    d.body.addWidget(e)
    bc = TacButton(_t("取消"), "secondary")
    bo = TacButton(ok, "primary")
    bc.clicked.connect(d.reject)
    bo.clicked.connect(d.accept)
    e.returnPressed.connect(d.accept)
    d.buttons.addWidget(bc)
    d.buttons.addWidget(bo)
    e.setFocus()
    if d.exec() != QDialog.DialogCode.Accepted:
        return None
    return e.text().strip() or None


class Chip(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("chip", True)


class PhotosPage(Page):
    page_id = "photos"
    index = "00"
    title = _t("照片")
    en = "Photos // library · albums · search"
    sub = _t("像手机相册一样浏览：按时间、自动归类的相簿；搜索照片里的文字、地点、人物、场景。")

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.header.hide()
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(0)
        self.marks = smart.Marks(ctx.library.annotations)
        self.level = "day"
        self.tab = "library"
        self.album_key: str | None = None
        self.restrict: dict[int, str] = {}
        self.result: smart.SearchResult | None = None
        self.ai_hits: list[int] = []
        self._index: smart.SearchIndex | None = None
        self._by_gid: dict[int, object] = {}
        self._history: list[int] = []
        self._ocr_worker = None
        self._dl_worker = None
        self._ai_token = 0
        self.pending_open: str | None = None
        p = theme.current()
        self.setStyleSheet(f"""
QPushButton[seg="true"] {{ background: {p.surface2}; color: {p.ink2}; border: 1px solid {p.line}; padding: 4px 14px; }}
QPushButton[seg="true"]:checked {{ background: {p.slab}; color: {p.accent}; border-color: {p.slab}; font-weight: 700; }}
QPushButton[chip="true"] {{ background: {p.surface}; color: {p.ink}; border: 1px solid {p.line}; border-radius: 13px;
                            padding: 3px 12px; }}
QPushButton[chip="true"]:checked {{ background: {p.accent}; color: {p.accent_ink}; border-color: {p.accent}; }}
QPushButton[chip="true"]:hover {{ border-color: {p.accent}; }}
#photosTop, #photosBanner, #photosSel, #photosChips {{ background: {p.surface}; border-bottom: 1px solid {p.line}; }}
""")

        # ---- 顶栏 ----
        top = QWidget()
        top.setObjectName("photosTop")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(18, 10, 18, 10)
        tl.setSpacing(10)
        self.tab_group = QButtonGroup(self)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        for i, (key, name) in enumerate((("library", _t("图库")), ("albums", _t("相簿")))):
            b = QPushButton(name)
            b.setProperty("seg", True)
            b.setCheckable(True)
            b.setMinimumHeight(32)
            b.setFont(font("cn", 10.5, 700))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_tab(k))
            self.tab_group.addButton(b, i)
            seg.addWidget(b)
        self.tab_group.button(0).setChecked(True)
        tl.addLayout(seg)
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(32)
        self.search.setPlaceholderText(_t("搜索：照片里的文字、地点、人物、场景、日期（如“发票”“东京 拉面”“去年 夏天”“晚上”）"))
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        self.search.returnPressed.connect(self.run_search)
        tl.addWidget(self.search, 1)
        self.level_group = QButtonGroup(self)
        self.level_box = QWidget()
        lv = QHBoxLayout(self.level_box)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        for i, (key, name) in enumerate((("year", _t("年")), ("month", _t("月")), ("day", _t("日")), ("all", _t("全部")))):
            b = QPushButton(name)
            b.setProperty("seg", True)
            b.setCheckable(True)
            b.setMinimumHeight(32)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_level(k))
            self.level_group.addButton(b, i)
            lv.addWidget(b)
        self.level_group.button(2).setChecked(True)
        tl.addWidget(self.level_box)
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(3, 12)
        self.zoom.setInvertedAppearance(True)
        self.zoom.setFixedWidth(90)
        self.zoom.setToolTip(_t("每行几张（Ctrl+滚轮也可以）"))
        self.zoom.setValue(int(ctx.settings.get("photos_zoom") or 5))
        self.zoom.valueChanged.connect(self._zoom)
        tl.addWidget(self.zoom)
        self.b_sync = TacButton(_t("自动同步 ▾"), "secondary", height=32)
        self.b_sync.clicked.connect(lambda: self._sync_menu(self.b_sync.mapToGlobal(self.b_sync.rect().bottomLeft())))
        tl.addWidget(self.b_sync)
        self.root.addWidget(top)
        self.top_bar = top

        # ---- 文字识别横幅 ----
        self.banner = QWidget()
        self.banner.setObjectName("photosBanner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(18, 6, 18, 6)
        self.banner_text = QLabel("")
        self.banner_text.setWordWrap(True)
        self.banner_btn = TacButton(_t("开始"), "primary", height=28)
        self.banner_btn.clicked.connect(self._banner_action)
        self.banner_close = TacButton("×", "secondary", height=28)
        self.banner_close.setToolTip(_t("这次先不提醒"))
        self.banner_close.clicked.connect(lambda: (setattr(self, "_banner_hidden", True), self.banner.hide()))
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(self.banner_btn)
        bl.addWidget(self.banner_close)
        self._banner_hidden = False
        self.root.addWidget(self.banner)

        # ---- 搜索建议 ----
        self.chips_w = QWidget()
        self.chips_w.setObjectName("photosChips")
        self.chips = QHBoxLayout(self.chips_w)
        self.chips.setContentsMargins(18, 6, 18, 6)
        self.chips.setSpacing(6)
        self.chips_w.hide()
        self.root.addWidget(self.chips_w)

        # ---- 选择操作栏 ----
        self.sel_bar = QWidget()
        self.sel_bar.setObjectName("photosSel")
        sl = QHBoxLayout(self.sel_bar)
        sl.setContentsMargins(18, 5, 18, 5)
        sl.setSpacing(6)
        self.sel_label = QLabel("")
        self.sel_label.setFont(font("cn", 10, 700))
        sl.addWidget(self.sel_label)
        sl.addStretch(1)
        self.b_sel_fav = TacButton(_t("♥ 收藏"), "secondary", height=28)
        self.b_sel_fav.clicked.connect(lambda: self.toggle_fav(self.current_grid().selected_groups()))
        self.b_sel_album = TacButton(_t("加入相簿 ▾"), "secondary", height=28)
        self.b_sel_album.clicked.connect(lambda: self._album_menu(self.b_sel_album.mapToGlobal(self.b_sel_album.rect().bottomLeft()),
                                                                  self.current_grid().selected_groups()))
        self.b_sel_remove = TacButton(_t("移出相簿"), "secondary", height=28)
        self.b_sel_remove.clicked.connect(self._remove_from_album)
        self.b_sel_studio = TacButton(_t("在修图中打开"), "dark", height=28)
        self.b_sel_studio.clicked.connect(lambda: self._studio(self.current_grid().selected_groups()))
        self.b_sel_merge = TacButton(_t("合并曝光"), "secondary", height=28)
        self.b_sel_merge.clicked.connect(lambda: self._merge(self.current_grid().selected_groups()))
        self.b_sel_clear = TacButton(_t("取消选择"), "secondary", height=28)
        self.b_sel_clear.clicked.connect(lambda: self.current_grid().clear_selection())
        for b in (self.b_sel_fav, self.b_sel_album, self.b_sel_remove, self.b_sel_studio, self.b_sel_merge, self.b_sel_clear):
            sl.addWidget(b)
        self.sel_bar.hide()
        self.root.addWidget(self.sel_bar)

        # ---- 内容 ----
        self.stack = QStackedWidget()
        self.cards = CardGrid(min_w=200, ratio=1.0)
        self.cards.activated.connect(self._card_clicked)
        self.grid = PhotoGrid()
        self.albums = CardGrid(min_w=170, ratio=1.0)
        self.albums.activated.connect(self._album_clicked)
        self.albums.menu_requested.connect(self._album_card_menu)
        self.albums.empty_text = _t("还没有相簿")
        self.album_view = QWidget()
        av = QVBoxLayout(self.album_view)
        av.setContentsMargins(0, 0, 0, 0)
        av.setSpacing(0)
        ah = QHBoxLayout()
        ah.setContentsMargins(18, 10, 18, 0)
        self.b_album_back = TacButton(_t("‹ 相簿"), "secondary", height=30)
        self.b_album_back.clicked.connect(self.go_back)
        self.album_title = QLabel("")
        self.album_title.setFont(font("cn", 15, 800))
        self.b_album_rename = TacButton(_t("重命名"), "secondary", height=30)
        self.b_album_rename.clicked.connect(lambda: self._rename_album(self.album_key))
        self.b_album_delete = TacButton(_t("删除相簿"), "danger", height=30)
        self.b_album_delete.clicked.connect(lambda: self._delete_album(self.album_key))
        ah.addWidget(self.b_album_back)
        ah.addSpacing(8)
        ah.addWidget(self.album_title, 1)
        ah.addWidget(self.b_album_rename)
        ah.addWidget(self.b_album_delete)
        av.addLayout(ah)
        self.album_grid = PhotoGrid()
        av.addWidget(self.album_grid, 1)
        self.search_grid = PhotoGrid()
        self.search_grid.empty_text = _t("没有找到")
        self.detail = PhotoDetail(self)
        self.detail.back.connect(self.go_back)
        self.detail.changed.connect(self._repaint)
        self.empty = self._empty_state()
        for w in (self.cards, self.grid, self.albums, self.album_view, self.search_grid, self.detail, self.empty):
            self.stack.addWidget(w)
        for g in (self.grid, self.album_grid, self.search_grid):
            g.cols_pref = self.zoom.value()
            g.is_fav = self.marks.is_fav
            g.activated.connect(lambda gid, grid=g: self.open_detail(grid, gid))
            g.selection_changed.connect(self._sel_changed)
            g.menu_requested.connect(self.photo_menu)
            g.zoom_changed.connect(self._grid_zoomed)
            g.fav_requested.connect(lambda grid=g: self.toggle_fav(grid.selected_groups()))
        self.root.addWidget(self.stack, 1)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(280)
        self._search_timer.timeout.connect(self.run_search)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (self.search.setFocus(), self.search.selectAll()))
        ctx.library_changed.connect(self._library_changed)
        ctx.files_changed.connect(self._library_changed)
        ctx.ratings_changed.connect(self._repaint)
        ctx.autosync.changed.connect(self._update_sync)
        self._library_changed()
        self._update_sync()

    # ---- 基础 ----
    def status(self, text: str) -> None:
        self.ctx.status.emit(text)

    def groups(self) -> list:
        return [g for g in self.ctx.library.groups if g.files]

    def search_index(self) -> smart.SearchIndex:
        if self._index is None:
            self._index = smart.SearchIndex(self.ctx.library, self.marks)
        return self._index

    def current_grid(self) -> PhotoGrid:
        i = self.stack.currentIndex()
        return {V_ALBUM: self.album_grid, V_SEARCH: self.search_grid}.get(i, self.grid)

    def show_view(self, v: int, push: bool = True) -> None:
        cur = self.stack.currentIndex()
        if push and cur != v and cur != V_EMPTY:
            self._history.append(cur)
            self._history = self._history[-20:]
        self.stack.setCurrentIndex(v)
        self.level_box.setVisible(v in (V_CARDS, V_GRID))
        self.zoom.setVisible(v in (V_GRID, V_ALBUM, V_SEARCH))
        self.top_bar.setVisible(v != V_DETAIL)
        self.chips_w.setVisible(v == V_SEARCH and self.chips.count() > 0)
        self._sel_changed()
        self._update_banner()
        if v == V_DETAIL:
            self.detail.setFocus()
        elif v in (V_GRID, V_ALBUM, V_SEARCH):
            self.current_grid().setFocus()

    def go_back(self) -> None:
        while self._history:
            v = self._history.pop()
            if v == V_SEARCH and not self.search.text().strip():
                continue
            if v == V_ALBUM and self.album_key is None:
                continue
            self.show_view(v, push=False)
            if v in (V_GRID, V_ALBUM, V_SEARCH) and self.detail.group is not None:
                self._reveal(self.current_grid(), self.detail.group.gid)
            return
        self.set_tab(self.tab)

    def on_show(self) -> None:
        self._update_banner()
        self._maybe_auto_ocr()

    # ---- 照片库变化 ----
    def _library_changed(self) -> None:
        self._index = None
        self._by_gid = {g.gid: g for g in self.groups()}
        self.marks.reload()
        if not self._by_gid:
            self.show_view(V_EMPTY, push=False)
            self._update_banner()
            return
        was_empty = self.stack.currentIndex() == V_EMPTY
        self._rebuild(keep_scroll=True)
        if was_empty:
            self.set_tab(self.tab)
        self._update_banner()
        if self.pending_open:
            target = self.pending_open
            self.pending_open = None
            g = next((g for g in self._by_gid.values()
                      if os.path.normcase(os.path.abspath(g.primary)) == target), None)
            if g is not None:
                self.set_tab("library")
                self.open_detail(self.grid, g.gid)
        QTimer.singleShot(1500, self._maybe_auto_ocr)

    def _rebuild(self, keep_scroll: bool = False) -> None:
        v = self.stack.currentIndex()
        if self.search.text().strip():
            self.run_search(keep_scroll=keep_scroll)
        self._build_library(keep_scroll)
        self._build_albums(keep_scroll)
        if self.album_key:
            self._fill_album(self.album_key, keep_scroll)
        if v == V_DETAIL and self.detail.group is not None:
            gid = self.detail.group.gid
            gs = [self._by_gid[g.gid] for g in self.detail.groups if g.gid in self._by_gid]
            idx = next((i for i, g in enumerate(gs) if g.gid == gid), 0)
            if gs:
                self.detail.show_photos(gs, idx, self.detail.hits)
            else:
                self.go_back()

    def _repaint(self) -> None:
        for w in (self.grid, self.album_grid, self.search_grid, self.cards, self.albums):
            w.viewport().update()
        if self.album_key and self.album_key in ("media:favorite",):
            self._fill_album(self.album_key, True)

    # ---- 图库 ----
    def set_tab(self, tab: str) -> None:
        self.tab = tab
        self.tab_group.button(0 if tab == "library" else 1).setChecked(True)
        if self.search.text().strip():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self._clear_chips()
        if not self._by_gid:
            self.show_view(V_EMPTY, push=False)
            return
        self._history.clear()
        if tab == "library":
            self.show_view(V_CARDS if self.level in ("year", "month") else V_GRID, push=False)
        else:
            self.album_key = None
            self._build_albums(keep_scroll=True)
            self.show_view(V_ALBUMS, push=False)

    def set_level(self, level: str, scroll_key: str | None = None) -> None:
        self.level = level
        self.level_group.button(("year", "month", "day", "all").index(level)).setChecked(True)
        self._build_library(keep_scroll=False)
        self.show_view(V_CARDS if level in ("year", "month") else V_GRID, push=False)
        if scroll_key:
            w = self.cards if level in ("year", "month") else self.grid
            i = next((i for i, s in enumerate(w.sections) if s.key.startswith(scroll_key)), -1)
            if i < 0 and level in ("year", "month"):          # 月视图按年分段：在段里找那张卡片
                for si, s in enumerate(w.sections):
                    j = next((j for j, c in enumerate(s.items) if c.key.startswith(scroll_key)), -1)
                    if j >= 0:
                        w.scroll_to(si, j)
                        break
            elif i >= 0:
                w.verticalScrollBar().setValue(int(w._tops[i]))

    def _build_library(self, keep_scroll: bool) -> None:
        gs = self.groups()
        if self.level == "year":
            secs = smart.timeline(gs, "year")
            cards = [Card(s.key, s.title, s.sub, len(s.gids), self._by_gid.get(s.cover)) for s in secs]
            self.cards.set_sections([GridSection("years", "", "", cards, header=False)], keep_scroll)
        elif self.level == "month":
            secs = smart.timeline(gs, "month")
            by_year: dict[str, list] = {}
            for s in secs:
                y = s.key[1:5] if s.key.startswith("m") else _t("没有拍摄日期")
                by_year.setdefault(y, []).append(Card(s.key, s.title, s.sub, len(s.gids), self._by_gid.get(s.cover)))
            self.cards.set_sections([GridSection(f"y{y}", smart.year_title(int(y)) if y.isdigit() else y, "", cs)
                                     for y, cs in by_year.items()], keep_scroll)
        else:
            secs = smart.timeline(gs, self.level)
            self.grid.set_sections([GridSection(s.key, s.title, s.sub, [self._by_gid[i] for i in s.gids])
                                    for s in secs], keep_scroll)

    def _card_clicked(self, c: Card) -> None:
        if c.key.startswith("y"):
            self.set_level("month", "m" + c.key[1:])
        elif c.key.startswith("m"):
            self.set_level("day", "d" + c.key[1:])
        elif c.key == "undated":
            self.set_level("day", "undated")

    def _zoom(self, n: int) -> None:
        self.ctx.settings.set("photos_zoom", n)
        for g in (self.grid, self.album_grid, self.search_grid):
            g.set_columns(n)

    def _grid_zoomed(self, n: int) -> None:
        if self.zoom.value() != n:
            self.zoom.blockSignals(True)
            self.zoom.setValue(n)
            self.zoom.blockSignals(False)
            self.ctx.settings.set("photos_zoom", n)
            for g in (self.grid, self.album_grid, self.search_grid):
                if g.cols_pref != n:
                    g.cols_pref = n
                    g._relayout()

    # ---- 相簿 ----
    def _build_albums(self, keep_scroll: bool) -> None:
        secs = []
        for sh in smart.shelves(self.ctx.library, self.marks):
            cards = [Card(a.key, a.title, a.sub, len(a.gids), self._by_gid.get(a.cover), a.icon, a) for a in sh.albums]
            if sh.key == "user":
                cards.append(Card("new_album", _t("新建相簿…"), "", 0, None, "+"))
            secs.append(GridSection(sh.key, sh.title, "", cards))
        self.albums.set_sections(secs, keep_scroll)

    def _album_clicked(self, c: Card) -> None:
        if c.key == "new_album":
            name = ask_text(self, _t("新建相簿"), _t("相簿名称"), _t("新相簿"), _t("创建"))
            if name:
                self.marks.create(name)
                self._index = None
                self._build_albums(True)
            return
        self.open_album(c.key)

    def open_album(self, key: str) -> None:
        self.album_key = key
        self._fill_album(key, keep_scroll=False)
        self.show_view(V_ALBUM)

    def _fill_album(self, key: str, keep_scroll: bool) -> None:
        a = smart.album_by_key(self.ctx.library, self.marks, key)
        if a is None:
            self.album_key = None
            if self.stack.currentIndex() == V_ALBUM:
                self.set_tab("albums")
            return
        self.album_title.setText(f"{a.title}   " + _t("{0} 张", len(a.gids)) + (f" · {a.sub}" if a.sub else ""))
        user = key.startswith("user:")
        self.b_album_rename.setVisible(user)
        self.b_album_delete.setVisible(user)
        gs = [self._by_gid[i] for i in a.gids if i in self._by_gid]
        if key.startswith(("people:", "scene:", "user:", "media:")) and key != "media:recent":
            secs = []
            for s in smart.timeline(gs, "month"):
                secs.append(GridSection(s.key, s.title, s.sub, [self._by_gid[i] for i in s.gids]))
        elif key.startswith(("trip:",)):
            secs = [GridSection(s.key, s.title, s.sub, [self._by_gid[i] for i in s.gids])
                    for s in reversed(smart.timeline(gs, "day"))]          # 旅行按时间顺序看
        else:
            secs = [GridSection(key, "", "", gs, header=False)]
        self.album_grid.empty_text = _t("这个相簿还是空的：在图库里选中照片，点「加入相簿」") if user else _t("这里还没有照片")
        self.album_grid.set_sections(secs, keep_scroll)

    def _album_card_menu(self, pos: QPoint, c: Card) -> None:
        if not c.key.startswith("user:"):
            return
        m = QMenu(self)
        m.addAction(_t("重命名…"), lambda: self._rename_album(c.key))
        m.addAction(_t("删除相簿（照片不会删除）"), lambda: self._delete_album(c.key))
        m.exec(pos)

    def _rename_album(self, key: str | None) -> None:
        if not key or not key.startswith("user:"):
            return
        aid = key[5:]
        a = self.marks.albums.get(aid)
        name = ask_text(self, _t("重命名相簿"), _t("相簿名称"), a.name if a else "")
        if name:
            self.marks.rename(aid, name)
            self._index = None
            self._build_albums(True)
            if self.album_key == key:
                self._fill_album(key, True)

    def _delete_album(self, key: str | None) -> None:
        if not key or not key.startswith("user:"):
            return
        a = self.marks.albums.get(key[5:])
        if a is None or not ask(self, _t("删除相簿"), _t("删除相簿「{0}」？<br>只删除相簿本身，里面的照片不会被删除或移动。", a.name),
                                ok=_t("删除"), danger=True):
            return
        self.marks.delete(a.aid)
        self._index = None
        if self.album_key == key:
            self.album_key = None
            self.set_tab("albums")
        else:
            self._build_albums(True)

    def _album_menu(self, pos: QPoint, groups: list) -> None:
        if not groups:
            return
        m = QMenu(self)
        self._fill_album_menu(m, groups)
        m.exec(pos)

    def _fill_album_menu(self, m: QMenu, groups: list) -> None:
        for a in self.marks.album_list():
            m.addAction(a.name, lambda aid=a.aid: self._add_to_album(groups, aid))
        if self.marks.albums:
            m.addSeparator()
        m.addAction(_t("新建相簿…"), lambda: self._add_to_album(groups, None))

    def _add_to_album(self, groups: list, aid: str | None) -> None:
        if aid is None:
            name = ask_text(self, _t("新建相簿"), _t("相簿名称（选中的 {0} 张照片会放进去）", len(groups)), _t("新相簿"), _t("创建"))
            if not name:
                return
            aid = self.marks.create(name)
        n = self.marks.add(groups, aid)
        self._index = None
        self._build_albums(True)
        self.status(_t("已把 {0} 张照片加入相簿「{1}」", n, self.marks.albums[aid].name))

    def _remove_from_album(self) -> None:
        if not (self.album_key or "").startswith("user:"):
            return
        gs = self.album_grid.selected_groups()
        n = self.marks.remove(gs, self.album_key[5:])
        self._index = None
        self._fill_album(self.album_key, True)
        self._build_albums(True)
        self.status(_t("已从相簿移出 {0} 张（照片本身还在）", n))

    # ---- 收藏 ----
    def toggle_fav(self, groups: list) -> None:
        if not groups:
            return
        on = self.marks.toggle_fav(groups)
        self._index = None
        self.status(_t("已收藏 {0} 张", len(groups)) if on else _t("已取消收藏 {0} 张", len(groups)))
        self._repaint()
        self._build_albums(True)

    # ---- 选择 ----
    def _sel_changed(self) -> None:
        v = self.stack.currentIndex()
        grid = self.current_grid()
        n = len(grid.selected) if v in (V_GRID, V_ALBUM, V_SEARCH) else 0
        self.sel_bar.setVisible(n > 0)
        if n:
            gs = grid.selected_groups()
            self.sel_label.setText(_t("已选 {0} 张", n))
            self.b_sel_fav.setText(_t("♡ 取消收藏") if all(self.marks.is_fav(g) for g in gs) else _t("♥ 收藏"))
            self.b_sel_remove.setVisible(v == V_ALBUM and (self.album_key or "").startswith("user:"))
            stills = [g for g in gs if not g.is_video]
            self.b_sel_studio.setVisible(len(stills) == 1)
            self.b_sel_merge.setVisible(len(stills) >= 2)
            self.ctx.library.selected = {g.gid for g in gs}
            self.ctx.selection_changed.emit()

    def _reveal(self, grid: PhotoGrid, gid: int) -> None:
        for si, s in enumerate(grid.sections):
            for ii, g in enumerate(s.items):
                if g.gid == gid:
                    grid.selected = {gid}
                    grid.current = (si, ii)
                    grid.scroll_to(si, ii)
                    grid.selection_changed.emit()
                    return

    # ---- 单张 ----
    def open_detail(self, grid: PhotoGrid, gid: int) -> None:
        gs = grid.groups()
        idx = next((i for i, g in enumerate(gs) if g.gid == gid), -1)
        if idx < 0:
            return
        hits = self.result.text_hits if grid is self.search_grid and self.result else {}
        self.detail.show_photos(gs, idx, hits)
        self.show_view(V_DETAIL)

    def photo_menu(self, pos: QPoint, groups: list) -> None:
        if not groups:
            return
        g = groups[0]
        m = QMenu(self)
        grid = self.current_grid()
        if len(groups) == 1 and self.stack.currentIndex() != V_DETAIL:
            m.addAction(_t("打开"), lambda: self.open_detail(grid, g.gid))
        fav = all(self.marks.is_fav(x) for x in groups)
        m.addAction(_t("取消收藏") if fav else _t("收藏  F"), lambda: self.toggle_fav(groups))
        sub = m.addMenu(_t("加入相簿"))
        self._fill_album_menu(sub, groups)
        if (self.album_key or "").startswith("user:") and self.stack.currentIndex() == V_ALBUM:
            m.addAction(_t("从这个相簿移出"), self._remove_from_album)
        m.addSeparator()
        stills = [x for x in groups if not x.is_video]
        if len(stills) == 1:
            if self.stack.currentIndex() != V_DETAIL:
                m.addAction(_t("编辑（调整 / 滤镜 / 裁剪）"), lambda: (self.open_detail(grid, g.gid), self.detail.start_edit()))
            m.addAction(_t("在修图中打开（图层 / 蒙版 / 修复）"), lambda: self._studio(stills))
        if len(stills) >= 2:
            m.addAction(_t("合并曝光（HDR，{0} 张）…", len(stills)), lambda: self._merge(stills))
        if HOOKS.rate is not None:
            rs = m.addMenu(_t("打星"))
            for n in range(0, 6):
                rs.addAction(("★" * n) if n else _t("清除星级"),
                             lambda n=n: (HOOKS.rate([x.gid for x in groups], {"rating": n}), self._repaint()))
        m.addSeparator()
        texts = ["\n".join(l["t"] for l in (x.feats.get("ocr") or [])) for x in groups]
        if any(texts):
            m.addAction(_t("复制照片里的文字"), lambda: QGuiApplication.clipboard().setText("\n\n".join(t for t in texts if t)))
        if self.ctx.library.ocr.ready() and stills:
            m.addAction(_t("重新识别文字"), lambda: self._reocr(stills))
        m.addAction(_t("在文件夹中显示"), lambda: reveal_in_explorer(g.primary))
        m.addAction(_t("用系统程序打开"), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(g.primary)))
        m.addAction(_t("复制文件路径"), lambda: QGuiApplication.clipboard().setText("\n".join(x.primary for x in groups)))
        m.exec(pos)

    def _studio(self, groups: list) -> None:
        if groups:
            self.ctx.open_editor(groups[0], self.window())

    def _merge(self, groups: list) -> None:
        if len(groups) >= 2:
            self.ctx.merge_exposures(groups, self.window())

    def _reocr(self, groups: list) -> None:
        for g in groups:
            g.feats.pop("ocr", None)
        self.start_ocr(groups)

    # ---- 搜索 ----
    def run_search(self, keep_scroll: bool = False) -> None:
        q = self.search.text().strip()
        if not q:
            self.result = None
            self.restrict = {}
            self._clear_chips()
            if self.stack.currentIndex() == V_SEARCH:
                self.go_back()
            return
        if self.result is None or self.result.query != q:
            if self.result is not None and smart.tokenize(self.result.query) != smart.tokenize(q):
                self.restrict = {}
            self.ai_hits = []
        same = self.result is not None and self.result.query == q
        self.result = smart.search(self.ctx.library, self.marks, q, self.restrict, self.search_index())
        self._show_results(keep_scroll or same)
        self._build_chips()
        if self.stack.currentIndex() == V_DETAIL:          # 正在看某一张：结果在后面更新，不抢画面
            self.chips_w.hide()
            return
        if self.stack.currentIndex() != V_SEARCH:
            self.show_view(V_SEARCH)
        else:
            self.chips_w.setVisible(self.chips.count() > 0)
        self._start_ai(q)

    def _show_results(self, keep_scroll: bool = False) -> None:
        r = self.result
        if r is None:
            return
        gs = [self._by_gid[i] for i in r.gids if i in self._by_gid]
        secs = [GridSection("match", _t("“{0}”", r.query), _t("{0} 张", len(gs)), gs)]
        shown = set(r.gids)
        extra = [self._by_gid[i] for i in self.ai_hits if i in self._by_gid and i not in shown]
        if extra:
            secs.append(GridSection("ai", _t("AI 画面搜索"), _t("画面看起来像“{0}”的照片（按相似度）", r.query), extra))
        self.search_grid.badges = {gid: _t("文字") for gid in r.text_hits}
        ocr_ready = self.ctx.library.ocr.ready()
        hint = "" if ocr_ready else _t("\n（下载文字识别模型后，还能搜到照片里出现的文字）")
        self.search_grid.empty_text = _t("没有找到“{0}”", r.query) + hint
        if not gs and not extra:
            secs = []
        self.search_grid.set_sections(secs, keep_scroll)

    def _start_ai(self, q: str) -> None:
        ai = self.ctx.library.ai
        if not (ai.clip_ready() and ai.emb):
            return
        self._ai_token += 1
        tok = self._ai_token

        def job(prog, cancel):
            return [gid for gid, _ in ai.search_text(q, limit=150)]

        def done(gids, tok=tok, q=q):
            if tok != self._ai_token or self.result is None or self.result.query != q:
                return
            self.ai_hits = gids or []
            self._show_results(keep_scroll=True)
        self.ctx.run(job, on_done=done, on_error=lambda tb: None)

    def _clear_chips(self) -> None:
        while self.chips.count():
            it = self.chips.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self.chips_w.hide()

    def _build_chips(self) -> None:
        self._clear_chips()
        r = self.result
        if r is None:
            return
        multi = len(r.tokens) > 1
        lab = caption(_t("找到的类别："), "faint", 8.5)
        self.chips.addWidget(lab)
        n = 0
        for s in r.suggestions[:14]:
            text = f"{s.label}  {s.count:,}"
            if multi:
                text = f"{s.token + 1}. " + text
            c = Chip(text)
            c.setChecked(self.restrict.get(s.token) == s.cat)
            c.setToolTip(_t("只按这一类找“{0}”（再点一次取消）", r.tokens[s.token]))
            c.clicked.connect(lambda _=False, s=s: self._toggle_restrict(s))
            self.chips.addWidget(c)
            n += 1
        if not n:
            self.chips.addWidget(caption(_t("没有完全匹配的地点、人物、文字…") + (_t("下面是 AI 按画面内容找的结果")
                                         if self.ctx.library.ai.clip_ready() else ""), "faint", 8.5))
        self.chips.addStretch(1)
        self.chips_w.setVisible(True)

    def _toggle_restrict(self, s: smart.Suggestion) -> None:
        if self.restrict.get(s.token) == s.cat:
            self.restrict.pop(s.token, None)
        else:
            self.restrict[s.token] = s.cat
        self.result = smart.search(self.ctx.library, self.marks, self.search.text().strip(), self.restrict, self.search_index())
        self._show_results()
        self._build_chips()

    # ---- 文字识别 ----
    def _update_banner(self) -> None:
        lib = self.ctx.library
        if self._banner_hidden or not self._by_gid or self.stack.currentIndex() == V_DETAIL:
            self.banner.hide()
            return
        if self._dl_worker is not None or self._ocr_worker is not None:
            self.banner.show()
            return
        if not models.onnxruntime_ok():
            self.banner.hide()
            return
        if not lib.ocr.ready():
            self.banner_text.setText(_t("下载文字识别模型（约 {0:.0f} MB，本机离线运行）后，就能搜到照片里出现的文字：招牌、菜单、票据、截图…",
                                        models.download_size("ocr") / 1e6))
            self.banner_btn.setText(_t("下载"))
            self.banner_btn.setEnabled(True)
            self.banner.show()
            return
        pending = lib.ocr.pending(self.groups())
        if pending:
            self.banner_text.setText(_t("还有 {0} 张照片没有识别文字。", len(pending)))
            self.banner_btn.setText(_t("开始识别"))
            self.banner_btn.setEnabled(True)
            self.banner.show()
        else:
            self.banner.hide()

    def _banner_action(self) -> None:
        if self._ocr_worker is not None:
            self._ocr_worker.cancel()
            return
        if self._dl_worker is not None:
            return
        if not self.ctx.library.ocr.ready():
            self.download_ocr()
        else:
            self.start_ocr()

    def download_ocr(self) -> None:
        mirror = self.ctx.settings.get("ai_mirror", "auto")
        self.banner_btn.setEnabled(False)
        self.banner_text.setText(_t("正在下载文字识别模型…"))

        def job(prog, cancel):
            return models.download("ocr", lambda d, t, m: prog((d, t, m)), cancel, mirror=mirror)

        def on_prog(x):
            d, t, m = x
            self.banner_text.setText(_t("正在下载文字识别模型… {0:.0%}  {1}", d / max(1, t), m))

        def done(_):
            self._dl_worker = None
            self.status(_t("文字识别模型已就绪"))
            self.start_ocr()

        def err(tb):
            self._dl_worker = None
            self.banner_btn.setEnabled(True)
            self._update_banner()
            info(self, _t("下载失败"), _t("文字识别模型下载失败（可在「AI 搜图」页把下载源改成国内镜像再试）：<br>")
                 + f"<pre style='font-size:8pt'>{tb.strip().splitlines()[-1][:600]}</pre>", "ERROR")
        self._dl_worker = self.ctx.run(job, on_prog, done, err)
        self.banner.show()

    def _maybe_auto_ocr(self) -> None:
        lib = self.ctx.library
        if (self._ocr_worker is None and not self.ctx.busy and lib.ocr.ready() and self.ctx.settings.get("ocr_enabled", True)
                and self.isVisible() and lib.ocr.pending(self.groups())):
            self.start_ocr()

    def start_ocr(self, groups: list | None = None) -> None:
        lib = self.ctx.library
        if self._ocr_worker is not None or not lib.ocr.ready():
            return
        todo = groups if groups is not None else self.groups()
        if not lib.ocr.pending(todo):
            self._update_banner()
            return
        self.banner_btn.setText(_t("暂停"))
        self.banner_btn.setEnabled(True)
        self.banner_text.setText(_t("正在识别照片里的文字…"))
        self.banner.show()
        busy = lambda: self.ctx.busy           # noqa: E731  扫描开始时让路（扫描自己会识别）

        def job(prog, cancel):
            def p(d, t):
                if busy():
                    cancel.set()
                prog((d, t))
            # 只识别还没结果的；已有缓存的先载入
            return lib.ocr.index(todo, p, cancel) if groups is None else self._index_only(todo, p, cancel)

        def on_prog(x):
            d, t = x
            self.banner_text.setText(_t("正在识别照片里的文字… {0} / {1}（可以继续浏览；随时暂停，下次接着识别）", d, t))
            if d % 40 == 0:
                self._index = None

        def finish(n):
            self._ocr_worker = None
            self._index = None
            if n:
                self.status(_t("文字识别完成：新识别 {0} 张", n))
            if self.search.text().strip():
                self.run_search(keep_scroll=True)
            self._build_albums(True)
            if self.stack.currentIndex() == V_DETAIL:
                self.detail._show_current()
            self._update_banner()

        def err(tb):
            self._ocr_worker = None
            self._update_banner()
            self.status(_t("文字识别出错：") + tb.strip().splitlines()[-1])
        self._ocr_worker = self.ctx.run(job, on_prog, finish, err)

    def _index_only(self, groups, prog, cancel) -> int:
        """重新识别几张（不先载入缓存，否则刚清掉的结果又回来了）。"""
        ocr = self.ctx.library.ocr
        cache = self.ctx.library.cache
        from ...ai.ocrindex import OCR_KEY
        n = 0
        for i, g in enumerate(groups):
            if cancel.is_set():
                break
            try:
                lines = ocr.read_group(g)
            except Exception:
                lines = []
            g.feats["ocr"] = lines
            cache.put_ocr_many({g.primary_file.sig: lines}, OCR_KEY)
            n += 1
            prog(i + 1, len(groups))
        return n

    # ---- 自动同步 ----
    def _update_sync(self) -> None:
        a = self.ctx.autosync
        text = {"off": _t("自动同步：关 ▾"), "syncing": _t("同步中… ▾"), "starting": _t("同步中… ▾"),
                "error": _t("▲ 自动同步 ▾")}.get(a.state, _t("● 自动同步 ▾"))
        self.b_sync.setText(text)
        self.b_sync.setMinimumWidth(QFontMetrics(self.b_sync.font()).horizontalAdvance(text) + 40)
        self.b_sync.setToolTip(a.describe() + "\n" + _t("选一个照片文件夹：往里面拷进、删掉照片，这里自动跟着更新。"))
        loading = a.state in ("starting", "syncing") and not self._by_gid
        self.empty_title.setText(_t("正在载入照片…") if loading else _t("相册还是空的"))

    def _sync_menu(self, pos: QPoint) -> None:
        a = self.ctx.autosync
        m = QMenu(self)
        m.addAction(_t("添加要同步的文件夹…"), lambda: a.add_folder(self))
        act = m.addAction(_t("自动同步文件夹里的变化"))
        act.setCheckable(True)
        act.setChecked(a.enabled)
        act.toggled.connect(a.set_enabled)
        now = m.addAction(_t("立即检查"), a.check_now)
        now.setEnabled(a.enabled)
        roots = a.roots()
        if roots:
            m.addSeparator()
            head = m.addAction(a.describe() if a.enabled else _t("照片文件夹（自动同步已关闭）："))
            head.setEnabled(False)
            for d in roots[:8]:
                m.addAction("    " + d, lambda d=d: QDesktopServices.openUrl(QUrl.fromLocalFile(d)))
            if len(roots) > 8:
                m.addAction(_t("    …还有 {0} 个", len(roots) - 8)).setEnabled(False)
        m.addSeparator()
        m.addAction(_t("管理文件夹（资源接入）…"), lambda: self.ctx.navigate.emit("source"))
        m.exec(pos)

    # ---- 空状态 ----
    def _empty_state(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addStretch(1)
        t = QLabel(_t("相册还是空的"))
        t.setFont(font("cn", 18, 800))
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title = t
        v.addWidget(t)
        s = caption(_t("选一个照片文件夹，影匣会载入里面的照片，之后往文件夹里拷进、删掉照片也会自动同步。"
                       "也可以在「01 资源接入」添加多个文件夹；扫描过的话，点「扫描照片库」重新载入（有缓存，很快）。"), "dim", 10)
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(s)
        r = QHBoxLayout()
        r.addStretch(1)
        b1 = TacButton(_t("选择照片文件夹…"), "primary", height=36)
        b1.clicked.connect(lambda: self.ctx.autosync.add_folder(self))
        b2 = TacButton(_t("扫描照片库"), "secondary", height=36)
        b2.clicked.connect(self.ctx.rescan_requested.emit)
        r.addWidget(b1)
        r.addWidget(b2)
        r.addStretch(1)
        v.addLayout(r)
        v.addStretch(2)
        return w
