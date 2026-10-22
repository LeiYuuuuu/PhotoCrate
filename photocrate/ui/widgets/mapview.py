"""内置地图（瓦片地图，纯 Qt 绘制，不依赖浏览器内核）。

- 底图：OpenStreetMap（深色主题下压暗成夜间风格）、高德地图（中国境内坐标按 GCJ-02 偏移），或不显示底图（离线）。
  瓦片缓存在 %LOCALAPPDATA%\\PhotoCrate\\tiles\\，看过的区域离线也能显示；没有网络时退化为经纬网格。
- 照片：按屏幕位置就近聚合，显示代表照片的缩略图和数量；点一下 = 选中这一堆照片（发出 cluster_clicked），
  双击 = 放大到这一堆。
- 路线：传入按时间排序的坐标，画成连线（旅行路线）。
- 操作：拖动平移，滚轮缩放（以鼠标处为中心），左上角 + / − / 全部。
"""
from __future__ import annotations

import math
import os
from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QToolTip, QWidget

from .. import theme
from ..theme import font

TILE = 256
MIN_Z, MAX_Z = 1, 18

PROVIDERS = {
    # (显示名, URL 模板, 子域名, 版权说明, 是否 GCJ-02 坐标)
    "osm": ("OpenStreetMap", ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], "",
            "© OpenStreetMap contributors", False),
    "amap": ("高德地图（中国）", ["https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8"
                               "&x={x}&y={y}&z={z}"], "1234", "© 高德地图", True),
    "none": ("不显示底图（离线）", [], "", "", False),
}
USER_AGENT = b"PhotoCrate/1.4 (local photo organizer; desktop app)"


def provider_for(setting: str, dark: bool = False) -> str:
    """auto → OpenStreetMap（深色主题下绘制时自动压暗、去饱和）。旧版本的 carto_* 设置也落到这里。"""
    return setting if setting in PROVIDERS else "osm"


# ---------------------------------------------------------------------------
# 坐标
# ---------------------------------------------------------------------------

def lonlat_to_world(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = TILE * (2 ** z)
    lat = max(-85.0511, min(85.0511, lat))
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def world_to_lonlat(x: float, y: float, z: int) -> tuple[float, float]:
    n = TILE * (2 ** z)
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def _out_of_china(lat, lon):
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)


def wgs84_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    """高德底图用的“火星坐标”（只在中国境内偏移）。"""
    if _out_of_china(lat, lon):
        return lat, lon
    a, ee = 6378245.0, 0.00669342162296594323
    x, y = lon - 105.0, lat - 35.0
    dlat = (-100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
            + (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
            + (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
            + (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0)
    dlon = (300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
            + (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
            + (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
            + (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0)
    radlat = lat / 180.0 * math.pi
    magic = 1 - ee * math.sin(radlat) ** 2
    sq = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sq) * math.pi)
    dlon = (dlon * 180.0) / (a / sq * math.cos(radlat) * math.pi)
    return lat + dlat, lon + dlon


@dataclass
class MapPoint:
    gid: int
    lat: float
    lon: float
    thumb: str | None = None
    label: str = ""


# ---------------------------------------------------------------------------
# 瓦片
# ---------------------------------------------------------------------------

class TileStore:
    def __init__(self, cache_dir: str, parent_widget):
        self.cache_dir = cache_dir
        self.widget = parent_widget
        self.mem: OrderedDict[tuple, QPixmap] = OrderedDict()
        self.pending: dict[tuple, QNetworkReply] = {}
        self.failed: set[tuple] = set()
        self.ok_count = 0
        self.err_count = 0
        self.nam = QNetworkAccessManager(parent_widget)
        self.retina = False

    def path(self, prov: str, z: int, x: int, y: int) -> str:
        return os.path.join(self.cache_dir, prov, str(z), str(x), f"{y}.png")

    def get(self, prov: str, z: int, x: int, y: int) -> QPixmap | None:
        key = (prov, z, x, y, self.retina)
        pm = self.mem.get(key)
        if pm is not None:
            self.mem.move_to_end(key)
            return pm
        p = self.path(prov, z, x, y)
        if os.path.exists(p):
            pm = QPixmap(p)
            if not pm.isNull():
                self._put(key, pm)
                return pm
        self._fetch(prov, z, x, y, key)
        return None

    def peek(self, prov: str, z: int, x: int, y: int) -> QPixmap | None:
        """只看内存 / 磁盘缓存，不发请求（缩放时用上一级瓦片放大临时补位）。"""
        key = (prov, z, x, y, self.retina)
        pm = self.mem.get(key)
        if pm is not None:
            return pm
        p = self.path(prov, z, x, y)
        if os.path.exists(p):
            pm = QPixmap(p)
            if not pm.isNull():
                self._put(key, pm)
                return pm
        return None

    def _put(self, key, pm):
        self.mem[key] = pm
        while len(self.mem) > 500:
            self.mem.popitem(last=False)

    def _fetch(self, prov, z, x, y, key):
        if key in self.pending or key in self.failed or len(self.pending) >= 12:
            return
        info = PROVIDERS.get(prov)
        if not info or not info[1]:
            return
        subs = info[2] or " "
        url = info[1][0].format(s=subs[(x + y) % len(subs)].strip(), z=z, x=x, y=y)
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", USER_AGENT)
        req.setTransferTimeout(15000)
        reply = self.nam.get(req)
        self.pending[key] = reply
        reply.finished.connect(lambda r=reply, k=key, a=(prov, z, x, y): self._done(r, k, a))

    def _done(self, reply: QNetworkReply, key, args):
        self.pending.pop(key, None)
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.failed.add(key)
                self.err_count += 1
                return
            data = bytes(reply.readAll())
            pm = QPixmap()
            if not data or not pm.loadFromData(data):
                self.failed.add(key)
                self.err_count += 1
                return
            p = self.path(*args)
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                tmp = p + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, p)
            except OSError:
                pass
            self.ok_count += 1
            self._put(key, pm)
            self.widget.update()
        finally:
            reply.deleteLater()

    @property
    def offline(self) -> bool:
        return self.ok_count == 0 and self.err_count >= 6

    def reset_errors(self):
        self.failed.clear()
        self.err_count = 0


# ---------------------------------------------------------------------------
# 地图控件
# ---------------------------------------------------------------------------

class MapView(QWidget):
    cluster_clicked = Signal(list)        # 被点中的那一堆照片的 gid

    def __init__(self, cache_dir: str, provider: str = "osm", parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.provider = provider
        self.tiles = TileStore(cache_dir, self)
        self.zoom = 2
        self.cx, self.cy = lonlat_to_world(25, 120, self.zoom)      # 视图中心（当前缩放下的世界像素）
        self.points: list[MapPoint] = []
        self.route: list[tuple[float, float]] = []
        self.clusters: list[tuple[QPointF, list[MapPoint]]] = []
        self.selected: set[int] = set()
        self.thumb_loader = None            # fn(path) -> QPixmap | None
        self._drag = None
        self._moved = False
        self._hover = -1
        self.cell = 62

    # ---- 数据 ------------------------------------------------------------
    def set_provider(self, prov: str) -> None:
        if prov != self.provider:
            self.provider = prov
            self.tiles.reset_errors()
            self.update()

    def _proj(self, lat: float, lon: float) -> tuple[float, float]:
        if PROVIDERS.get(self.provider, (0, 0, 0, 0, False))[4]:
            lat, lon = wgs84_to_gcj02(lat, lon)
        return lat, lon

    def set_points(self, points: list[MapPoint], route: list[tuple[float, float]] | None = None,
                   fit: bool = True) -> None:
        self.points = list(points)
        self.route = list(route or [])
        self.selected = set()
        if fit:
            self.fit()
        self.update()

    def set_selected(self, gids) -> None:
        self.selected = set(gids)
        self.update()

    def fit(self) -> None:
        pts = [(p.lat, p.lon) for p in self.points] or self.route
        if not pts:
            return
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        w, h = max(100, self.width() - 80), max(100, self.height() - 80)
        z = MAX_Z - 3
        while z > MIN_Z:
            x0, y0 = lonlat_to_world(*self._proj(max(lats), min(lons)), z)
            x1, y1 = lonlat_to_world(*self._proj(min(lats), max(lons)), z)
            if x1 - x0 <= w and y1 - y0 <= h:
                break
            z -= 1
        self.zoom = z
        x0, y0 = lonlat_to_world(*self._proj(max(lats), min(lons)), z)
        x1, y1 = lonlat_to_world(*self._proj(min(lats), max(lons)), z)
        self.cx, self.cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.update()

    def set_zoom(self, z: int, anchor: QPointF | None = None) -> None:
        z = max(MIN_Z, min(MAX_Z, z))
        if z == self.zoom:
            return
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        # 鼠标下的那一点保持不动
        wx = self.cx + anchor.x() - self.width() / 2
        wy = self.cy + anchor.y() - self.height() / 2
        f = 2 ** (z - self.zoom)
        self.cx = wx * f - (anchor.x() - self.width() / 2)
        self.cy = wy * f - (anchor.y() - self.height() / 2)
        self.zoom = z
        self.update()

    def screen_of(self, lat: float, lon: float) -> QPointF:
        x, y = lonlat_to_world(*self._proj(lat, lon), self.zoom)
        return QPointF(x - self.cx + self.width() / 2, y - self.cy + self.height() / 2)

    # ---- 聚合 ------------------------------------------------------------
    def _cluster(self) -> None:
        cells: dict[tuple[int, int], list] = {}
        for p in self.points:
            s = self.screen_of(p.lat, p.lon)
            if not (-80 < s.x() < self.width() + 80 and -80 < s.y() < self.height() + 80):
                continue
            key = (int(s.x() // self.cell), int(s.y() // self.cell))
            cells.setdefault(key, []).append((s, p))
        out = []
        for items in cells.values():
            x = sum(s.x() for s, _ in items) / len(items)
            y = sum(s.y() for s, _ in items) / len(items)
            out.append((QPointF(x, y), [p for _, p in items]))
        out.sort(key=lambda c: len(c[1]))
        self.clusters = out

    # ---- 绘制 ------------------------------------------------------------
    def _buttons(self) -> list[tuple[QRectF, str]]:
        return [(QRectF(10, 10, 28, 28), "+"), (QRectF(10, 42, 28, 28), "−"), (QRectF(10, 74, 28, 28), "⤢")]

    def _tone(self, pm: QPixmap, dark: bool) -> QPixmap:
        """深色主题：底图去饱和 + 反相 + 压暗，变成深灰底、浅色道路的“夜间地图”（结果缓存）。"""
        if not dark:
            return pm
        cache = self.__dict__.setdefault("_dark_tiles", OrderedDict())
        k = pm.cacheKey()
        hit = cache.get(k)
        if hit is not None:
            return hit
        from PySide6.QtGui import QImage
        img = pm.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
        img.invertPixels()
        img = img.convertToFormat(QImage.Format.Format_RGB32)
        out = QPixmap(img.size())
        out.fill(QColor("#15171A"))
        qp = QPainter(out)
        qp.setOpacity(0.78)
        qp.drawImage(0, 0, img)
        qp.end()
        cache[k] = out
        while len(cache) > 400:
            cache.popitem(last=False)
        return out

    def paintEvent(self, e):
        p = theme.current()
        dark = p.name == "dark"
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        qp.fillRect(self.rect(), QColor("#1B1D20" if dark else "#D9DBD6"))
        n = 2 ** self.zoom
        left, top = self.cx - self.width() / 2, self.cy - self.height() / 2
        info = PROVIDERS.get(self.provider)
        have_tiles = False
        if info and info[1]:
            tx0, ty0 = int(left // TILE), int(top // TILE)
            tx1, ty1 = int((left + self.width()) // TILE), int((top + self.height()) // TILE)
            for ty in range(max(0, ty0), min(n - 1, ty1) + 1):
                for tx in range(tx0, tx1 + 1):
                    x = tx % n
                    pm = self.tiles.get(self.provider, self.zoom, x, ty)
                    dst = QRectF(tx * TILE - left, ty * TILE - top, TILE, TILE)
                    if pm is not None:
                        have_tiles = True
                        qp.drawPixmap(dst, self._tone(pm, dark), QRectF(pm.rect()))
                    elif self.zoom > MIN_Z:
                        par = self.tiles.peek(self.provider, self.zoom - 1, x // 2, ty // 2)
                        if par is not None:          # 新瓦片还没到：先用上一级的四分之一放大顶上
                            have_tiles = True
                            hw, hh = par.width() / 2, par.height() / 2
                            qp.drawPixmap(dst, self._tone(par, dark), QRectF((x % 2) * hw, (ty % 2) * hh, hw, hh))
        if not have_tiles:
            self._graticule(qp, left, top, dark)
        # 路线
        if len(self.route) > 1:
            path = QPainterPath()
            for i, (lat, lon) in enumerate(self.route):
                s = self.screen_of(lat, lon)
                path.moveTo(s) if i == 0 else path.lineTo(s)
            pen = QPen(QColor(p.accent2), 2.2)
            pen.setStyle(Qt.PenStyle.DashLine)
            qp.setPen(pen)
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.drawPath(path)
        # 照片
        self._cluster()
        for i, (c, items) in enumerate(self.clusters):
            self._draw_cluster(qp, c, items, i == self._hover)
        # 按钮与说明
        for r, t in self._buttons():
            qp.fillRect(r, QColor(p.slab))
            qp.setPen(QColor(p.slab_ink))
            qp.setFont(font("display", 12, 700))
            qp.drawText(r, Qt.AlignmentFlag.AlignCenter, t)
        qp.setFont(font("mono", 7))
        attr = info[3] if info else ""
        note = ""
        if info and info[1] and self.tiles.offline:
            note = "底图加载失败（没有网络？）· 显示经纬网格"
        elif not (info and info[1]):
            note = "离线模式 · 经纬网格"
        txt = "   ".join(x for x in (note, attr, f"Z{self.zoom}") if x)
        fm = QFontMetrics(qp.font())
        tw = fm.horizontalAdvance(txt) + 12
        r = QRectF(self.width() - tw - 4, self.height() - 18, tw, 16)
        bg = QColor(p.surface)
        bg.setAlpha(210)
        qp.fillRect(r, bg)
        qp.setPen(QColor(p.ink2))
        qp.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        if not self.points:
            qp.setFont(font("cn", 10))
            qp.setPen(QColor(p.ink3 if not have_tiles else p.ink2))
            qp.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "没有带位置的照片")
        qp.end()

    def _graticule(self, qp: QPainter, left: float, top: float, dark: bool) -> None:
        col = QColor("#2C3036" if dark else "#C2C5BE")
        qp.setPen(QPen(col, 1))
        lat_top, lon_left = world_to_lonlat(left, top, self.zoom)
        lat_bot, lon_right = world_to_lonlat(left + self.width(), top + self.height(), self.zoom)
        span = max(1e-6, lon_right - lon_left)
        step = next((s for s in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30) if span / s <= 12), 45)
        qp.setFont(font("mono", 7))
        lon = math.floor(lon_left / step) * step
        while lon <= lon_right:
            x, _ = lonlat_to_world(0, lon, self.zoom)
            qp.drawLine(QPointF(x - left, 0), QPointF(x - left, self.height()))
            qp.drawText(QPointF(x - left + 3, self.height() - 22), f"{lon:.4g}°")
            lon += step
        lat = math.floor(lat_bot / step) * step
        while lat <= lat_top:
            _, y = lonlat_to_world(lat, 0, self.zoom)
            qp.drawLine(QPointF(0, y - top), QPointF(self.width(), y - top))
            qp.drawText(QPointF(44, y - top - 3), f"{lat:.4g}°")
            lat += step

    def _thumb(self, path):
        if self.thumb_loader is not None:
            return self.thumb_loader(path)
        pm = QPixmap(path) if path else None
        return pm if pm is not None and not pm.isNull() else None

    def _draw_cluster(self, qp: QPainter, c: QPointF, items: list[MapPoint], hover: bool) -> None:
        p = theme.current()
        size = 46 if len(items) == 1 else 54
        r = QRectF(c.x() - size / 2, c.y() - size / 2, size, size)
        sel = any(m.gid in self.selected for m in items)
        if len(items) > 1:
            back = r.adjusted(4, -4, 4, -4)
            qp.fillRect(back, QColor(p.slab2))
        pm = self._thumb(items[0].thumb)
        qp.fillRect(r, QColor(p.slab))
        if pm is not None:
            sc = pm.scaled(QSize(int(size), int(size)), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
            sx, sy = (sc.width() - size) / 2, (sc.height() - size) / 2
            qp.drawPixmap(r, sc, QRectF(sx, sy, size, size))
        pen = QPen(QColor(p.accent) if (sel or hover) else QColor("#FFFFFF"), 3 if (sel or hover) else 2)
        qp.setPen(pen)
        qp.setBrush(Qt.BrushStyle.NoBrush)
        qp.drawRect(r)
        if len(items) > 1:
            txt = str(len(items))
            qp.setFont(font("display", 9, 700))
            tw = QFontMetrics(qp.font()).horizontalAdvance(txt) + 10
            br = QRectF(r.right() - tw + 4, r.top() - 8, tw, 17)
            qp.fillRect(br, QColor(p.accent))
            qp.setPen(QColor(p.accent_ink))
            qp.drawText(br, Qt.AlignmentFlag.AlignCenter, txt)

    # ---- 鼠标 ------------------------------------------------------------
    def _cluster_at(self, pos: QPointF) -> int:
        for i in range(len(self.clusters) - 1, -1, -1):
            c, items = self.clusters[i]
            size = 46 if len(items) == 1 else 54
            if abs(pos.x() - c.x()) <= size / 2 + 2 and abs(pos.y() - c.y()) <= size / 2 + 2:
                return i
        return -1

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            for r, t in self._buttons():
                if r.contains(e.position()):
                    if t == "+":
                        self.set_zoom(self.zoom + 1)
                    elif t == "−":
                        self.set_zoom(self.zoom - 1)
                    else:
                        self.fit()
                    return
            self._drag = e.position()
            self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            d = e.position() - self._drag
            if abs(d.x()) + abs(d.y()) > 2:
                self._moved = True
            self._drag = e.position()
            self.cx -= d.x()
            self.cy -= d.y()
            self.update()
            return
        i = self._cluster_at(e.position())
        if i != self._hover:
            self._hover = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.OpenHandCursor)
            self.update()
            if i >= 0:
                items = self.clusters[i][1]
                labels = sorted({m.label for m in items if m.label})
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"{len(items)} 张照片" + ("<br>" + "、".join(labels[:4]) if labels else ""), self)

    def mouseReleaseEvent(self, e):
        if self._drag is not None and not self._moved:
            i = self._cluster_at(e.position())
            if i >= 0:
                gids = [m.gid for m in self.clusters[i][1]]
                self.selected = set(gids)
                self.cluster_clicked.emit(gids)
                self.update()
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.set_zoom(self.zoom + (2 if self._cluster_at(e.position()) >= 0 else 1), e.position())

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if d:
            self.set_zoom(self.zoom + (1 if d > 0 else -1), e.position())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.update()
