"""大图查看与对比。

- 打开：缩略图上双击 / 选中后按空格或回车。
- 浏览：← → 上一张 / 下一张，Home / End 第一张 / 最后一张，Esc 关闭（对比模式下先退出对比），F 全屏切换。
- 放大：Z 或双击在“适合窗口”与 100% 之间切换（双击时以点击处为中心），滚轮缩放，按住左键拖动平移。
- 对比：C 进入 / 退出并排对比——左边固定为当前照片，右边用 ← → 换下一张候选；两边缩放和位置同步，
  方便比较对焦。单击某一边使它成为“当前”（打分作用于当前这边，边框高亮）。
- 打分：1–5 星、0 清除星级，6 红 7 黄 8 绿 9 蓝 色标（再按一次取消），P 留用，X 弃用，U 取消留弃。
- I 显示 / 隐藏信息条。

读图：有 JPG/HEIC/PNG 时读它（全尺寸）；只有 RAW 时取相机内嵌的大预览（Sony/Canon/Nikon 通常是全尺寸 JPEG）。
在后台线程解码，附近几张预先加载，翻页不卡。
"""
from __future__ import annotations

import os
from collections import OrderedDict

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QImage, QImageReader, QPainter, QPen
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget

from ..analyzers.builtin import FLAG_NAMES, LABEL_COLORS, LABEL_NAMES, blur_state, place_label
from . import theme
from .theme import font

KEY_LABELS = {Qt.Key.Key_6: "Red", Qt.Key.Key_7: "Yellow", Qt.Key.Key_8: "Green", Qt.Key.Key_9: "Blue"}


def rating_change_for_key(key, current: dict | None) -> dict | None:
    """把按键翻译成星级 / 色标 / 留弃的改动；不是打分键返回 None。"""
    cur = current or {}
    if Qt.Key.Key_0 <= key <= Qt.Key.Key_5:
        return {"rating": int(key - Qt.Key.Key_0)}
    if key in KEY_LABELS:
        lab = KEY_LABELS[key]
        return {"label": None if cur.get("label") == lab else lab}
    if key == Qt.Key.Key_P:
        return {"flag": "pick"}
    if key == Qt.Key.Key_X:
        return {"flag": "reject"}
    if key == Qt.Key.Key_U:
        return {"flag": None}
    return None


# ---------------------------------------------------------------------------
# 读图
# ---------------------------------------------------------------------------

def fmt_duration(sec) -> str:
    try:
        s = int(round(float(sec)))
    except (TypeError, ValueError):
        return ""
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def play_video(group) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    f = best_file(group)
    QDesktopServices.openUrl(QUrl.fromLocalFile(f.path))


def best_file(group):
    imgs = [f for f in group.image_files if f.role != "raw"]
    if imgs:
        return max(imgs, key=lambda f: (f.role == "jpeg", f.size))
    return next((f for f in group.image_files), group.primary_file)


def _pil_to_qimage(im) -> QImage:
    im = im.convert("RGB")
    data = im.tobytes("raw", "RGB")
    return QImage(data, im.width, im.height, im.width * 3, QImage.Format.Format_RGB888).copy()


def load_full_image(group, exiftool: str | None = None) -> QImage:
    f = best_file(group)
    if f.role == "video":
        from ..core.imaging import load_video_frame
        im, _ = load_video_frame(f.path, 2560)
        return _pil_to_qimage(im)
    if f.role in ("jpeg", "png", "image"):
        r = QImageReader(f.path)
        r.setAutoTransform(True)
        r.setAllocationLimit(0)
        img = r.read()
        if not img.isNull():
            return img
    from ..core.imaging import _ensure_heif, load_raw_preview
    from PIL import Image, ImageOps
    if f.role == "raw":
        im, _ = load_raw_preview(f.path, 100000, exiftool)
        return _pil_to_qimage(im)
    _ensure_heif()
    with Image.open(f.path) as im:
        im = ImageOps.exif_transpose(im)
        return _pil_to_qimage(im)


class _Signals(QObject):
    loaded = Signal(str, QImage)


class _LoadJob(QRunnable):
    def __init__(self, key: str, group, exiftool, sig: _Signals):
        super().__init__()
        self.key, self.group, self.exiftool, self.sig = key, group, exiftool, sig

    def run(self):
        try:
            img = load_full_image(self.group, self.exiftool)
        except Exception:
            img = QImage()
        self.sig.loaded.emit(self.key, img)


class ImageCache(QObject):
    """后台解码 + 最近用过的几张留在内存里。"""
    ready = Signal(str)

    def __init__(self, exiftool: str | None, cap: int = 8, parent=None):
        super().__init__(parent)
        self.exiftool = exiftool
        self.cap = cap
        self._d: OrderedDict[str, QImage] = OrderedDict()
        self._pending: set[str] = set()
        self._sig = _Signals()
        self._sig.loaded.connect(self._done)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)

    @staticmethod
    def key(group) -> str:
        return best_file(group).path

    def get(self, group) -> QImage | None:
        k = self.key(group)
        img = self._d.get(k)
        if img is not None:
            self._d.move_to_end(k)
        return img

    def request(self, group) -> None:
        k = self.key(group)
        if k in self._d or k in self._pending:
            return
        self._pending.add(k)
        self.pool.start(_LoadJob(k, group, self.exiftool, self._sig))

    def _done(self, k: str, img: QImage) -> None:
        self._pending.discard(k)
        self._d[k] = img
        while len(self._d) > self.cap:
            self._d.popitem(last=False)
        self.ready.emit(k)


# ---------------------------------------------------------------------------
# 单张图的显示区
# ---------------------------------------------------------------------------

class ImagePane(QWidget):
    view_changed = Signal(object)     # (zoom or None, cx, cy)
    activated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.img: QImage | None = None
        self.group = None
        self.zoom: float | None = None       # None = 适合窗口；否则 1.0 = 100%
        self.cx = self.cy = 0.5              # 视图中心（相对图像宽高的比例）
        self.active = False
        self.loading = False
        self.title = ""
        self._drag: QPointF | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(200, 200)

    def set_image(self, group, img: QImage | None) -> None:
        self.group = group
        self.img = img if img is not None and not img.isNull() else None
        self.loading = img is None
        self.update()

    def scale(self) -> float:
        if self.zoom is None:
            if not self.img:
                return 1.0
            return min(self.width() / self.img.width(), self.height() / self.img.height())
        return self.zoom

    def _clamp(self, zoom, cx, cy) -> tuple[float, float]:
        """放大后不让图像边缘离开窗口（图比窗口小的方向居中）。"""
        if zoom is None or not self.img:
            return 0.5, 0.5
        w, h = self.img.width() * zoom, self.img.height() * zoom
        hx, hy = self.width() / 2 / w, self.height() / 2 / h
        cx = 0.5 if hx >= 0.5 else min(1 - hx, max(hx, cx))
        cy = 0.5 if hy >= 0.5 else min(1 - hy, max(hy, cy))
        return cx, cy

    def set_view(self, zoom, cx, cy, emit=False) -> None:
        self.zoom = zoom
        self.cx, self.cy = self._clamp(zoom, cx, cy)
        self.update()
        if emit:
            self.view_changed.emit((self.zoom, self.cx, self.cy))

    def zoom_at(self, new: float | None, at: QPointF | None) -> None:
        """缩放到 new（None = 适合窗口），并让鼠标所指的那一点停在原处。"""
        if new is None or not self.img:
            self.set_view(None, 0.5, 0.5, emit=True)
            return
        if at is None:
            at = QPointF(self.width() / 2, self.height() / 2)
        ix, iy = self._to_img(at)
        cx = ix - (at.x() - self.width() / 2) / (self.img.width() * new)
        cy = iy - (at.y() - self.height() / 2) / (self.img.height() * new)
        self.set_view(new, cx, cy, emit=True)

    def toggle_100(self, at: QPointF | None = None) -> None:
        self.zoom_at(1.0 if self.zoom is None and self.img else None, at)

    def _img_rect(self) -> QRectF:
        s = self.scale()
        w, h = self.img.width() * s, self.img.height() * s
        if self.zoom is None:
            return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        x = self.width() / 2 - self.cx * w
        y = self.height() / 2 - self.cy * h
        if w <= self.width():
            x = (self.width() - w) / 2
        if h <= self.height():
            y = (self.height() - h) / 2
        return QRectF(x, y, w, h)

    def _to_img(self, p: QPointF) -> tuple[float, float]:
        r = self._img_rect()
        return (p.x() - r.left()) / max(1.0, r.width()), (p.y() - r.top()) / max(1.0, r.height())

    # ---- 鼠标 ----
    def mousePressEvent(self, e):
        self.activated.emit(self)
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.position()

    def mouseMoveEvent(self, e):
        if self._drag is not None and self.img and self.zoom is not None:
            d = e.position() - self._drag
            self._drag = e.position()
            r = self._img_rect()
            self.set_view(self.zoom, self.cx - d.x() / max(1.0, r.width()), self.cy - d.y() / max(1.0, r.height()),
                          emit=True)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        if self.group is not None and getattr(self.group, "is_video", False):
            play_video(self.group)
            return
        self.toggle_100(e.position())

    def _paint_play(self, qp: QPainter) -> None:
        """视频：中间画播放按钮和时长，双击 / 回车用系统播放器播放。"""
        from PySide6.QtGui import QPolygonF
        c = QRectF(self.rect()).center()
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(0, 0, 0, 150))
        qp.drawEllipse(c, 42, 42)
        qp.setBrush(QColor("#FFFFFF"))
        qp.drawPolygon(QPolygonF([QPointF(c.x() - 13, c.y() - 20), QPointF(c.x() - 13, c.y() + 20),
                                  QPointF(c.x() + 22, c.y())]))
        dur = (self.group.meta or {}).get("duration")
        txt = ("视频 " + fmt_duration(dur) if dur else "视频") + " · 双击 / 回车 播放"
        qp.setFont(font("cn", 10, 600))
        tw = QFontMetrics(qp.font()).horizontalAdvance(txt) + 20
        r = QRectF(c.x() - tw / 2, c.y() + 54, tw, 26)
        qp.fillRect(r, QColor(0, 0, 0, 160))
        qp.setPen(QColor("#FFFFFF"))
        qp.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)

    def wheelEvent(self, e):
        if not self.img:
            return
        cur = self.scale()
        f = 1.25 if e.angleDelta().y() > 0 else 0.8
        fit = min(self.width() / self.img.width(), self.height() / self.img.height())
        new = max(fit, min(8.0, cur * f))
        self.zoom_at(None if new <= fit * 1.001 else new, e.position())

    def resizeEvent(self, e):
        self.cx, self.cy = self._clamp(self.zoom, self.cx, self.cy)
        super().resizeEvent(e)

    # ---- 绘制 ----
    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor("#0B0C0D"))
        if self.img:
            qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, (self.zoom or 0) < 1.0)
            qp.drawImage(self._img_rect(), self.img)
        else:
            qp.setPen(QColor("#8A8C8E"))
            qp.setFont(font("mono", 10))
            qp.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "LOADING…" if self.loading else "无法读取这张照片")
        if self.group is not None and getattr(self.group, "is_video", False) and self.img:
            self._paint_play(qp)
        if self.zoom is not None:
            qp.setFont(font("display", 10, 600))
            txt = f"{self.zoom * 100:.0f}%"
            tw = QFontMetrics(qp.font()).horizontalAdvance(txt) + 14
            qp.fillRect(QRectF(self.width() - tw - 10, 10, tw, 22), QColor(0, 0, 0, 160))
            qp.setPen(QColor(p.accent))
            qp.drawText(QRectF(self.width() - tw - 10, 10, tw, 22), Qt.AlignmentFlag.AlignCenter, txt)
        if self.active:
            qp.setPen(QPen(QColor(p.accent), 3))
            qp.drawRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5))
        qp.end()


# ---------------------------------------------------------------------------
# 信息条
# ---------------------------------------------------------------------------

class InfoBar(QWidget):
    def __init__(self, parent=None, bottom=False):
        super().__init__(parent)
        self.bottom = bottom
        self.setFixedHeight(46 if not bottom else 40)
        self.items: list[tuple[str, object]] = []    # [(text/kind, group)]
        self.left_text = ""
        self.right_text = ""
        self.groups = []

    def paintEvent(self, e):
        p = theme.current()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.fillRect(self.rect(), QColor("#111214"))
        qp.fillRect(QRectF(0, self.height() - 2 if not self.bottom else 0, self.width(), 2), QColor(p.accent))
        if not self.bottom:
            qp.setFont(font("cn", 10.5, 700))
            qp.setPen(QColor("#ECECE8"))
            qp.drawText(QRectF(16, 0, self.width() * 0.6, self.height()), Qt.AlignmentFlag.AlignVCenter, self.left_text)
            qp.setFont(font("mono", 8.5))
            qp.setPen(QColor("#A3A7AC"))
            qp.drawText(QRectF(self.width() * 0.4, 0, self.width() * 0.6 - 16, self.height()),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.right_text)
        else:
            n = max(1, len(self.groups))
            w = self.width() / n
            for i, g in enumerate(self.groups):
                self._draw_marks(qp, QRectF(i * w, 0, w, self.height()), g)
            if n > 1:
                qp.end()
                return
            qp.setFont(font("mono", 7.5))
            qp.setPen(QColor("#686C73"))
            hint = "← → 翻页  ·  Z/双击 100%  ·  C 对比  ·  1–5 星  0 清除  ·  6–9 色标  ·  P 留用  X 弃用  U 取消  ·  Esc 关闭"
            qp.drawText(QRectF(0, 0, self.width() - 12, self.height()),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, hint)
        qp.end()

    def _draw_marks(self, qp: QPainter, r: QRectF, g) -> None:
        p = theme.current()
        rt = g.feats.get("rating") or {}
        x = r.left() + 16
        qp.setFont(font("cn", 14))
        for k in range(5):
            qp.setPen(QColor(p.accent) if k < int(rt.get("rating") or 0) else QColor("#3A3D42"))
            qp.drawText(QRectF(x, r.top(), 20, r.height()), Qt.AlignmentFlag.AlignVCenter, "★")
            x += 19
        x += 10
        flag = rt.get("flag")
        if flag:
            qp.setFont(font("cn", 9, 700))
            txt = FLAG_NAMES[flag]
            tw = QFontMetrics(qp.font()).horizontalAdvance(txt) + 14
            qp.fillRect(QRectF(x, r.center().y() - 10, tw, 20), QColor(p.ok if flag == "pick" else p.danger))
            qp.setPen(QColor("#FFFFFF"))
            qp.drawText(QRectF(x, r.center().y() - 10, tw, 20), Qt.AlignmentFlag.AlignCenter, txt)
            x += tw + 8
        lab = rt.get("label")
        if lab:
            qp.fillRect(QRectF(x, r.center().y() - 7, 14, 14), QColor(LABEL_COLORS.get(lab, "#888888")))
            qp.setFont(font("cn", 9))
            qp.setPen(QColor("#ECECE8"))
            qp.drawText(QRectF(x + 20, r.top(), 60, r.height()), Qt.AlignmentFlag.AlignVCenter,
                        LABEL_NAMES.get(lab, lab))


# ---------------------------------------------------------------------------
# 查看器窗口
# ---------------------------------------------------------------------------

class ViewerWindow(QDialog):
    def __init__(self, ctx, groups: list, index: int = 0, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.groups = [g for g in groups if g.files]
        self.index = max(0, min(index, len(self.groups) - 1))
        self.compare = False
        self.left_index = self.index          # 对比模式左边固定的照片
        self.setWindowTitle("影匣 · 大图查看")
        self.setStyleSheet("background: #0B0C0D;")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.top = InfoBar(self)
        lay.addWidget(self.top)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.pane_a = ImagePane(self)
        self.pane_b = ImagePane(self)
        self.pane_b.hide()
        row.addWidget(self.pane_a, 1)
        row.addWidget(self.pane_b, 1)
        lay.addLayout(row, 1)
        self.bottom = InfoBar(self, bottom=True)
        lay.addWidget(self.bottom)
        self.cache = ImageCache(ctx.library.exiftool_path, parent=self)
        self.cache.ready.connect(lambda k: self._refresh_images())
        for pane in (self.pane_a, self.pane_b):
            pane.view_changed.connect(lambda v, src=pane: self._sync(src, v))
            pane.activated.connect(self._activate)
        self.active = self.pane_a
        self.pane_a.active = False
        if hasattr(ctx, "ratings_changed"):
            ctx.ratings_changed.connect(self._ratings_changed)
        self.resize(1400, 900)
        self._show()

    # ---- 状态 ----
    def current_group(self):
        if self.compare and self.active is self.pane_a:
            return self.groups[self.left_index]
        return self.groups[self.index] if self.groups else None

    def _show(self) -> None:
        if not self.groups:
            return
        g = self.groups[self.index]
        if self.compare:
            ga = self.groups[self.left_index]
            self.pane_a.set_image(ga, self.cache.get(ga))
            self.pane_b.set_image(g, self.cache.get(g))
            self.cache.request(ga)
        else:
            self.pane_a.set_image(g, self.cache.get(g))
        self.cache.request(g)
        for k in (self.index + 1, self.index - 1, self.index + 2):     # 预读附近的
            if 0 <= k < len(self.groups):
                self.cache.request(self.groups[k])
        self._update_info()

    def _refresh_images(self) -> None:
        if self.compare:
            ga = self.groups[self.left_index]
            if self.pane_a.img is None:
                self.pane_a.set_image(ga, self.cache.get(ga))
            gb = self.groups[self.index]
            if self.pane_b.img is None:
                self.pane_b.set_image(gb, self.cache.get(gb))
        else:
            g = self.groups[self.index]
            if self.pane_a.img is None or self.pane_a.group is not g:
                self.pane_a.set_image(g, self.cache.get(g))

    def _describe(self, g) -> str:
        parts = []
        if g.date:
            parts.append(g.date.strftime("%Y-%m-%d %H:%M:%S"))
        cam = g.feat("camera", "model")
        if cam:
            parts.append(cam)
        m = g.meta
        exp = []
        if m.get("focal"):
            exp.append(f"{m['focal']:.0f}mm")
        if m.get("fnumber"):
            exp.append(f"f/{m['fnumber']:.1f}")
        if m.get("exposure"):
            e = m["exposure"]
            exp.append(f"1/{1 / e:.0f}s" if e < 1 else f"{e:.1f}s")
        if m.get("iso"):
            exp.append(f"ISO {m['iso']:.0f}")
        if exp:
            parts.append(" ".join(exp))
        pl = place_label(g.feats.get("geo"))
        if pl:
            parts.append(pl)
        sc = g.feat("blur", "score")
        if sc is not None:
            st = blur_state(g, float(self.ctx.settings.get("blur_threshold", 60)))
            parts.append(f"清晰度 {sc:.0f}" + ("（模糊）" if st == "blurry" else ""))
        sim = g.feats.get("similar")
        if sim:
            parts.append(f"相似组 #{sim['cid']}：第 {sim['rank']}/{sim['size']}" + (" · 最佳" if sim["best"] else ""))
        return "   ·   ".join(parts)

    def _update_info(self) -> None:
        g = self.groups[self.index]
        if self.compare:
            ga = self.groups[self.left_index]
            self.top.left_text = f"对比  {ga.name}  ⟷  {g.name}"
            self.top.right_text = f"右侧 {self.index + 1} / {len(self.groups)}   ·   {self._describe(self.current_group())}"
            self.bottom.groups = [ga, g]
        else:
            self.top.left_text = f"{g.name}" + (f"   ({len(g.files)} 个文件)" if len(g.files) > 1 else "")
            self.top.right_text = f"{self.index + 1} / {len(self.groups)}   ·   {self._describe(g)}"
            self.bottom.groups = [g]
        self.top.update()
        self.bottom.update()

    # ---- 操作 ----
    def go(self, i: int) -> None:
        if not self.groups:
            return
        self.index = max(0, min(len(self.groups) - 1, i))
        if not self.compare:
            self.pane_a.set_view(None, 0.5, 0.5)
        self._show()

    def toggle_compare(self) -> None:
        if not self.compare:
            if len(self.groups) < 2:
                return
            self.compare = True
            self.left_index = self.index
            self.index = self.index + 1 if self.index + 1 < len(self.groups) else self.index - 1
            self.pane_b.show()
            self.pane_b.set_view(self.pane_a.zoom, self.pane_a.cx, self.pane_a.cy)
            self._activate(self.pane_b)
        else:
            self.compare = False
            self.pane_b.hide()
            self.pane_a.active = self.pane_b.active = False
            self.active = self.pane_a
            if self.active_was_left:
                self.index = self.left_index
        self.pane_a.img = None
        self.pane_b.img = None
        self._show()

    @property
    def active_was_left(self) -> bool:
        return getattr(self, "_left_active", False)

    def _activate(self, pane) -> None:
        if not self.compare:
            return
        self.active = pane
        self._left_active = pane is self.pane_a
        self.pane_a.active = pane is self.pane_a
        self.pane_b.active = pane is self.pane_b
        self.pane_a.update()
        self.pane_b.update()
        self._update_info()

    def _sync(self, src, v) -> None:
        if not self.compare:
            return
        other = self.pane_b if src is self.pane_a else self.pane_a
        other.set_view(*v)

    def _ratings_changed(self) -> None:
        self.bottom.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            if self.compare:
                self.toggle_compare()
            else:
                self.close()
            return
        if k in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown):
            self.go(self.index + 1)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            self.go(self.index - 1)
        elif k == Qt.Key.Key_Home:
            self.go(0)
        elif k == Qt.Key.Key_End:
            self.go(len(self.groups) - 1)
        elif k == Qt.Key.Key_Z:
            pane = self.active if self.compare else self.pane_a
            pane.toggle_100()
        elif k == Qt.Key.Key_C:
            self.toggle_compare()
        elif k == Qt.Key.Key_F:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            g = self.current_group()
            if g is not None and getattr(g, "is_video", False):
                play_video(g)
        elif k == Qt.Key.Key_I:
            self.top.setVisible(not self.top.isVisible())
        else:
            g = self.current_group()
            change = rating_change_for_key(k, g.feats.get("rating") if g else None)
            if change is not None and g is not None and hasattr(self.ctx, "rate"):
                self.ctx.rate([g.gid], change)
                self.bottom.update()
                return
            super().keyPressEvent(e)
