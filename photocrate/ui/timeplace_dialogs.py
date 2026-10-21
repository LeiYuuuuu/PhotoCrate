"""「时间与地点」页用到的对话框：时间校正预览、按行程批量指定地点、写回 XMP 预览。"""
from __future__ import annotations

import os
from collections import Counter

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                               QSplitter, QTableView, QVBoxLayout, QWidget)

from ..core.daybook import DayRow, itinerary_template, parse_itinerary
from ..core.geotime import shift_label
from . import theme
from .dialogs import _Base
from .theme import font
from .widgets.deco import Panel, StatCard, TacButton, caption

PLACE_SCOPES = [
    ("fill", "只补全没有地点的照片（推荐：不改 GPS 与轨迹匹配结果）"),
    ("track", "覆盖轨迹 / 参考照片匹配的地点（不改照片自带 GPS）"),
    ("all", "全部覆盖（包括照片自带的 GPS）"),
]


def scope_flags(scope: str) -> tuple[bool, bool]:
    """返回 (override_gps, override_track)。"""
    return {"fill": (False, False), "track": (False, True), "all": (True, True)}.get(scope, (False, False))


def _same_place(g, place: dict) -> bool:
    geo = g.feats.get("geo") or {}
    return geo.get("source") == "manual" and all((geo.get(k) or None) == (place.get(k) or None)
                                                  for k in ("country", "province", "city", "district"))


def would_apply(g, scope: str) -> bool:
    over_gps, over_track = scope_flags(scope)
    if g.feat("geo", "has_gps") and not over_gps:
        return False
    if g.feat("geo", "source") == "track" and not over_track:
        return False
    return True


class _RowsModel(QAbstractTableModel):
    def __init__(self, head: list[str], rows: list[tuple], colors: list[str | None] | None = None, mono=(), parent=None):
        super().__init__(parent)
        self.head, self.rows, self.colors = head, rows, colors or [None] * len(rows)
        self.mono = set(mono)
        self._mf = font("mono", 8.5)

    def set_rows(self, rows, colors=None):
        self.beginResetModel()
        self.rows = rows
        self.colors = colors or [None] * len(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.head)

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and o == Qt.Orientation.Horizontal:
            return self.head[s]
        return None

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        v = self.rows[idx.row()][idx.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return "" if v is None else str(v)
        if role == Qt.ItemDataRole.FontRole and idx.column() in self.mono:
            return self._mf
        if role == Qt.ItemDataRole.ForegroundRole and self.colors[idx.row()]:
            return QColor(self.colors[idx.row()])
        return None


def _table(model) -> QTableView:
    tv = QTableView()
    tv.setModel(model)
    tv.verticalHeader().setVisible(False)
    tv.verticalHeader().setDefaultSectionSize(22)
    tv.setAlternatingRowColors(True)
    tv.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    tv.setWordWrap(False)
    for c in range(model.columnCount()):
        tv.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
    return tv


# ---------------------------------------------------------------------------
# 时间校正预览
# ---------------------------------------------------------------------------

class TimeShiftDialog(_Base):
    def __init__(self, parent, rows, seconds: int, tz: str | None):
        super().__init__(parent, "确认时间校正", "TIME SHIFT // PREVIEW", hazard=False)
        self.resize(900, 600)
        changed = [(g, a, b) for g, a, b in rows if a is None or a.date() != b.date()]
        cards = QGridLayout()
        cards.setSpacing(8)
        c1 = StatCard("照片", "PHOTOS")
        c1.set(str(len(rows)), unit="张")
        c2 = StatCard("调整量", "OFFSET")
        c2.set(shift_label(seconds) or "0:00", unit="小时:分")
        c3 = StatCard("日期会变化", "DATE CHANGE", accent=theme.current().warn)
        c3.set(str(len(changed)), len(changed) / len(rows) if rows else 0, "张")
        c4 = StatCard("实际时区", "TIME ZONE")
        c4.set(f"UTC{tz}" if tz else "不变")
        for i, c in enumerate((c1, c2, c3, c4)):
            cards.addWidget(c, 0, i)
        self.body.addLayout(cards)
        moves = Counter((a.strftime("%Y-%m-%d") if a else "无", b.strftime("%Y-%m-%d")) for _, a, b in changed)
        if moves:
            lines = [f"{x} → {y}：{n} 张" for (x, y), n in sorted(moves.items())[:8]]
            if len(moves) > 8:
                lines.append(f"…另有 {len(moves) - 8} 组日期变化")
            self.body.addWidget(caption("日期变化：" + "；".join(lines), "dim", 9))
        self.body.addWidget(caption("校正后的时间会用于日期分区、按天指定地点和 GPS 轨迹匹配。照片文件不会被修改；"
                                    "随时可以「清除时间校正」恢复。新的校正会替换这些照片原来的校正（不会叠加）。",
                                    "faint", 8.5))
        p = theme.current()
        data = [(g.name, a.strftime("%Y-%m-%d %H:%M:%S") if a else "—", b.strftime("%Y-%m-%d %H:%M:%S"),
                 "日期变化" if (a is None or a.date() != b.date()) else "") for g, a, b in rows]
        colors = [p.warn if r[3] else None for r in data]
        panel = Panel("校正清单", "SHIFT MANIFEST", f"{len(rows)} ITEMS", pad=6)
        panel.body.addWidget(_table(_RowsModel(["照片", "当前时间", "校正后", ""], data, colors, mono=(1, 2))))
        self.body.addWidget(panel, 1)
        bc = TacButton("返回修改", "secondary")
        bo = TacButton("应用校正", "primary", en="APPLY")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self.accept)
        bo.setEnabled(bool(rows))
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)


# ---------------------------------------------------------------------------
# 按行程批量指定地点
# ---------------------------------------------------------------------------

class ItineraryDialog(_Base):
    def __init__(self, parent, lib, days: list[DayRow], default_year: int, place_index):
        super().__init__(parent, "按行程批量指定地点", "ITINERARY // BATCH PLACES")
        self.resize(1180, 660)
        self.lib, self.days, self.year, self.idx = lib, days, default_year, place_index
        self.by_key = {r.key: r for r in days if r.day}
        self.result: list[tuple[list[int], dict, str]] = []
        self.body.addWidget(caption("每行写“日期 地点”，日期可以是范围（2026-03-09 ~ 2026-03-11）或省略年份（3/12）。"
                                    "地点写法同上：日本/京都、湖南/邵阳、箱根……右侧实时显示识别结果。", "dim", 9))
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)
        self.edit = QPlainTextEdit(itinerary_template(days))
        self.edit.setFont(font("mono", 10))
        ll.addWidget(self.edit, 1)
        split.addWidget(left)
        panel = Panel("识别结果", "PARSED", "", pad=6)
        self.panel = panel
        self.model = _RowsModel(["行", "日期", "地点 → 识别为", "照片", "状态"], [], mono=(0, 1, 3))
        tv = _table(self.model)
        h = tv.horizontalHeader()
        for c, w in ((0, 36), (1, 170), (3, 46)):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            h.resizeSection(c, w)
        panel.body.addWidget(tv)
        split.addWidget(panel)
        split.setSizes([400, 780])
        self.body.addWidget(split, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("应用范围"))
        self.scope = QComboBox()
        for k, v in PLACE_SCOPES:
            self.scope.addItem(v, k)
        row.addWidget(self.scope, 1)
        self.body.addLayout(row)
        self.summary = caption("", "dim", 9)
        self.body.addWidget(self.summary)
        bc = TacButton("取消", "secondary")
        self.bo = TacButton("应用到这些日期", "primary", en="APPLY")
        bc.clicked.connect(self.reject)
        self.bo.clicked.connect(self._apply)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(self.bo)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._parse)
        self.edit.textChanged.connect(self._timer.start)
        self.scope.currentIndexChanged.connect(self._parse)
        self._parse()

    def _parse(self):
        p = theme.current()
        rows, colors = [], []
        self._plan: list[tuple[list[int], dict, str]] = []
        total = 0
        scope = self.scope.currentData()
        claimed: dict[str, int] = {}
        for it in parse_itinerary(self.edit.toPlainText(), self.year):
            span = (f"{it.start}" + (f" ~ {it.end}" if it.end and it.end != it.start else "")) if it.start else ""
            if it.error:
                rows.append((it.line_no, span, it.place, "", it.error))
                colors.append(p.danger)
                continue
            place = self.idx.resolve(it.place)
            if place and place.get("needs_country"):
                rows.append((it.line_no, span, it.place, "", f"不认识“{it.place}”，请写成“国家/{it.place}”"))
                colors.append(p.danger)
                continue
            gids, same = [], 0
            for d in it.days():
                r = self.by_key.get(d.strftime("%Y-%m-%d"))
                if not r:
                    continue
                for gid in r.gids:
                    g = self.lib.by_gid(gid)
                    if g is None or not would_apply(g, scope):
                        continue
                    if place and _same_place(g, place):
                        same += 1          # 已经是这个手动地点，不用再改
                        continue
                    gids.append(gid)
            dup = [d.strftime("%m-%d") for d in it.days() if d.strftime("%Y-%m-%d") in claimed]
            for d in it.days():
                claimed[d.strftime("%Y-%m-%d")] = it.line_no
            label = f"{it.place} → {place['label']}" if place else it.place
            if gids:
                status = "将应用" + (f"（另有 {same} 张已是该地点）" if same else "")
            else:
                status = f"{same} 张已是该地点" if same else "这些日期没有需要指定的照片"
            if dup:
                status = f"与前面的行重复（{', '.join(dup[:3])}），以后面为准；" + status
            rows.append((it.line_no, span, label, len(gids), status))
            colors.append(None if gids else p.ink3)
            if gids and place:
                self._plan.append((gids, place, it.place))
                total += len(gids)
        self.model.set_rows(rows, colors)
        self.panel.set_meta(f"{len(rows)} LINES")
        self.summary.setText(f"共 {len(self._plan)} 段行程、{total} 张照片将被指定地点。" if self._plan
                             else "还没有可应用的行。")
        self.bo.setEnabled(bool(self._plan))

    def _apply(self):
        self.result = self._plan
        self.accept()


# ---------------------------------------------------------------------------
# 写回 XMP 预览
# ---------------------------------------------------------------------------

class XmpDialog(_Base):
    def __init__(self, parent, plan, scope_text: str):
        super().__init__(parent, "确认写入 XMP 附属文件", "XMP SIDECAR // DRY RUN", hazard=True)
        self.resize(940, 600)
        p = theme.current()
        cards = QGridLayout()
        cards.setSpacing(8)
        c1 = StatCard("将新建 .xmp", "CREATE")
        c1.set(str(plan.created), unit="个")
        if plan.edited or not plan.skipped_exists:
            c2 = StatCard("更新已有 .xmp", "UPDATE", accent=p.warn)
            c2.set(str(plan.edited), unit="个")
        else:
            c2 = StatCard("已有 .xmp（跳过）", "EXISTS", accent=p.warn)
            c2.set(str(plan.skipped_exists), unit="张")
        c3 = StatCard("非 RAW（跳过）", "NOT RAW", accent=p.ink3)
        c3.set(str(plan.skipped_no_raw), unit="张")
        c4 = StatCard("没有可写内容", "NOTHING", accent=p.ink3)
        c4.set(str(plan.skipped_nothing), unit="张")
        for i, c in enumerate((c1, c2, c3, c4)):
            cards.addWidget(c, 0, i)
        self.body.addLayout(cards)
        self.body.addWidget(QLabel(f"<b>范围：</b>{scope_text}　　<b>内容：</b>{plan.spec_text.replace('写入 XMP：', '')}"))
        self.body.addWidget(caption("只为 RAW 写“文件名.xmp”，Lightroom / Bridge / Capture One 会读取；照片原文件不会被改动。"
                                    "已有 .xmp（可能是修图记录）时，勾选了“更新”才会改写其中这几项，其余内容原样保留，"
                                    "改之前完整备份。JPG / HEIC 不生成附属文件。写入后可在「操作记录」页撤销"
                                    "（新建的 .xmp 放入回收站，更新过的换回备份）。", "faint", 8.5))
        data = [(os.path.basename(t.raw), os.path.basename(t.xmp), "更新" if t.mode == "edit" else "新建",
                 "、".join(t.what), os.path.dirname(t.raw)) for t in plan.tasks]
        colors = [p.warn if t.mode == "edit" else None for t in plan.tasks]
        panel = Panel("写入清单", "XMP MANIFEST", f"{len(data)} FILES", pad=6)
        panel.body.addWidget(_table(_RowsModel(["RAW", ".xmp", "方式", "内容", "文件夹"], data, colors, mono=(0, 1, 4))))
        self.body.addWidget(panel, 1)
        bc = TacButton("取消", "secondary")
        bo = TacButton("确认写入", "primary", en="WRITE")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self.accept)
        bo.setEnabled(bool(plan.tasks))
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)
