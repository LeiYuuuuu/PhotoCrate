"""修图画布：缩放 / 平移、按缩放级别渲染、画笔时只重算一小块、选区蚂蚁线、工具的叠加层。

渲染：缩放 < 100% 时取 2^-level 的代理图（level 让代理图不小于屏幕像素）；整张代理图不超过 600 万像素时
整张算好（平移不用重算），否则只算可见区域外加一圈。拖滑块等连续操作时，如果上一次渲染超过 90 ms，
先用低一级的代理图出结果，停手 250 ms 后再按原级别补一次。
"""
from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QWidget

from ...edit.document import to_uint8
from ...i18n import t as _t

BG = QColor("#1E2023")


def checker_brush(size: int = 8) -> QBrush:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(QColor("#FFFFFF"))
    qp = QPainter(pm)
    c = QColor("#D6D8DA")
    qp.fillRect(0, 0, size, size, c)
    qp.fillRect(size, size, size, size, c)
    qp.end()
    return QBrush(pm)


class CanvasView(QWidget):
    view_changed = Signal()
    cursor_info = Signal(object)      # (x, y, (r, g, b, a)) 或 None
    rendered = Signal()

    MAX_FULL = 6_000_000

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.doc = None
        self.zoom = 1.0
        self.cx = self.cy = 0.0
        self.fit_mode = True
        self.hot: int | None = None
        self.show_original = False
        self._img: QImage | None = None
        self._buf: np.ndarray | None = None
        self._lvl = 0
        self._rect = (0, 0, 0, 0)
        self._coarse = False
        self._last_ms = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._render)
        self._refine = QTimer(self)
        self._refine.setSingleShot(True)
        self._refine.setInterval(250)
        self._refine.timeout.connect(lambda: self.request_render(False))
        self._ants = QTimer(self)
        self._ants.setInterval(140)
        self._ants.timeout.connect(self._tick_ants)
        self._phase = 0.0
        self._sel_path: tuple | None = None
        self._pan: QPointF | None = None
        self._space = False
        self.hover: QPointF | None = None
        self.checker = checker_brush()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(300, 200)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    # ---- 文档 ----
    def set_doc(self, doc) -> None:
        self.doc = doc
        self._img = self._buf = None
        self._sel_path = None
        self.fit()
        self.selection_changed()

    # ---- 坐标 ----
    def to_canvas(self, p: QPointF) -> tuple[float, float]:
        return (p.x() - self.width() / 2) / self.zoom + self.cx, (p.y() - self.height() / 2) / self.zoom + self.cy

    def to_widget(self, x: float, y: float) -> QPointF:
        return QPointF((x - self.cx) * self.zoom + self.width() / 2, (y - self.cy) * self.zoom + self.height() / 2)

    def canvas_transform(self) -> QTransform:
        t = QTransform()
        t.translate(self.width() / 2 - self.cx * self.zoom, self.height() / 2 - self.cy * self.zoom)
        t.scale(self.zoom, self.zoom)
        return t

    def image_rect(self) -> QRectF:
        a = self.to_widget(0, 0)
        return QRectF(a.x(), a.y(), self.doc.width * self.zoom, self.doc.height * self.zoom)

    # ---- 视图 ----
    def fit(self) -> None:
        if self.doc is None:
            return
        w, h = max(50, self.width() - 48), max(50, self.height() - 48)
        self.zoom = min(w / self.doc.width, h / self.doc.height)
        self.cx, self.cy = self.doc.width / 2, self.doc.height / 2
        self.fit_mode = True
        self.request_render()
        self.view_changed.emit()

    def set_zoom(self, z: float, at: QPointF | None = None) -> None:
        if self.doc is None:
            return
        z = max(0.01, min(32.0, z))
        at = at if at is not None else QPointF(self.width() / 2, self.height() / 2)
        x, y = self.to_canvas(at)
        self.zoom = z
        self.cx = x - (at.x() - self.width() / 2) / z
        self.cy = y - (at.y() - self.height() / 2) / z
        self.fit_mode = False
        self._clamp()
        self.request_render()
        self.view_changed.emit()

    def zoom_step(self, up: bool, at: QPointF | None = None) -> None:
        steps = [0.02, 0.03, 0.05, 0.0667, 0.0833, 0.125, 0.1667, 0.25, 0.3333, 0.5, 0.6667, 1, 1.5, 2, 3, 4, 6, 8,
                 12, 16, 24, 32]
        z = self.zoom
        if up:
            nz = next((s for s in steps if s > z * 1.01), steps[-1])
        else:
            nz = next((s for s in reversed(steps) if s < z / 1.01), steps[0])
        self.set_zoom(nz, at)

    def pan_by(self, dx: float, dy: float) -> None:
        self.cx -= dx / self.zoom
        self.cy -= dy / self.zoom
        self.fit_mode = False
        self._clamp()
        self.update()
        self._ensure_coverage()
        self.view_changed.emit()

    def _clamp(self) -> None:
        if self.doc is None:
            return
        mx = self.width() / 2 / self.zoom
        my = self.height() / 2 / self.zoom
        self.cx = min(max(self.cx, -mx * 0.8), self.doc.width + mx * 0.8)
        self.cy = min(max(self.cy, -my * 0.8), self.doc.height + my * 0.8)

    def level(self) -> int:
        if self.zoom >= 1 or self.doc is None:
            return 0
        return max(0, min(6, int(math.floor(math.log2(1 / self.zoom)))))

    # ---- 渲染 ----
    def request_render(self, interactive: bool = False) -> None:
        self._coarse = interactive and self._last_ms > 90
        if self._coarse:
            self._refine.start()
        self._timer.start()

    def _visible_level_rect(self, lvl: int) -> tuple[int, int, int, int]:
        f = 1 << lvl
        x0, y0 = self.to_canvas(QPointF(0, 0))
        x1, y1 = self.to_canvas(QPointF(self.width(), self.height()))
        mx, my = (x1 - x0) * 0.3, (y1 - y0) * 0.3
        LW, LH = self.doc.level_size(lvl)
        a, b = max(0, int((x0 - mx) / f)), max(0, int((y0 - my) / f))
        c, d = min(LW, int(math.ceil((x1 + mx) / f)) + 1), min(LH, int(math.ceil((y1 + my) / f)) + 1)
        return a, b, max(0, c - a), max(0, d - b)

    def _render(self) -> None:
        if self.doc is None or self.width() < 2:
            return
        lvl = self.level()
        if self._coarse:
            lvl = min(6, lvl + 1)
        LW, LH = self.doc.level_size(lvl)
        rect = (0, 0, LW, LH) if LW * LH <= self.MAX_FULL else self._visible_level_rect(lvl)
        if rect[2] <= 0 or rect[3] <= 0:
            self._img = None
            self.update()
            return
        t0 = time.perf_counter()
        layers = self.doc.layers[:1] if self.show_original else None
        try:
            rgb, a = self.doc.render(lvl, rect, hot=self.hot, layers=layers)
        except MemoryError:
            self.win.status_msg(_t("内存不足：画面太大，请缩小后再看"))
            return
        buf = np.ascontiguousarray(self._proof(to_uint8(rgb, a)))
        if not self._coarse:
            self._last_ms = (time.perf_counter() - t0) * 1000
        self._buf = buf
        self._img = QImage(buf.data, buf.shape[1], buf.shape[0], buf.shape[1] * 4, QImage.Format.Format_RGBA8888)
        self._lvl, self._rect = lvl, rect
        self.update()
        self.rendered.emit()

    def _ensure_coverage(self) -> None:
        """平移后可见区域超出已渲染的范围时补算。"""
        if self.doc is None or self._img is None:
            return
        f = 1 << self._lvl
        x0, y0 = self.to_canvas(QPointF(0, 0))
        x1, y1 = self.to_canvas(QPointF(self.width(), self.height()))
        rx, ry, rw, rh = self._rect
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(float(self.doc.width), x1), min(float(self.doc.height), y1)
        if x0 < rx * f - 1 or y0 < ry * f - 1 or x1 > (rx + rw) * f + 1 or y1 > (ry + rh) * f + 1:
            self.request_render()

    def update_canvas_rect(self, rect) -> None:
        """画布上 rect = (x, y, w, h) 变了：只重算这一块，直接写进已渲染的图里。"""
        if self.doc is None:
            return
        if rect is None or self._img is None or self.show_original:
            self.request_render()
            return
        f = 1 << self._lvl
        x, y, w, h = rect
        rx, ry, rw, rh = self._rect
        a, b = max(rx, int(x // f)), max(ry, int(y // f))
        c, d = min(rx + rw, int(math.ceil((x + w) / f))), min(ry + rh, int(math.ceil((y + h) / f)))
        if a >= c or b >= d:
            return
        rgb, al = self.doc.render(self._lvl, (a, b, c - a, d - b))
        self._buf[b - ry:d - ry, a - rx:c - rx] = self._proof(to_uint8(rgb, al))
        p0 = self.to_widget(a * f, b * f)
        self.update(QRectF(p0.x() - 2, p0.y() - 2, (c - a) * f * self.zoom + 4,
                           (d - b) * f * self.zoom + 4).toAlignedRect())

    def _proof(self, buf: np.ndarray) -> np.ndarray:
        """校样颜色（Ctrl+Y）/ 色域警告（Ctrl+Shift+Y）：只改显示，不改像素。"""
        pf = getattr(self.win, "proof", None)
        if pf is None or not (self._opt("proof_on", False) or self._opt("gamut_on", False)):
            return buf
        if self._opt("proof_on", False):
            out = pf.apply(buf, self._opt("gamut_on", False))
        else:
            out = pf.apply(buf, True)
            keep = out[..., :3] != (128, 128, 128)
            out[..., :3] = np.where(keep.any(axis=-1, keepdims=True), buf[..., :3], out[..., :3])
        return out

    def sample(self, x: float, y: float):
        """取已渲染图上 (x, y) 处的颜色（吸管、状态栏用）。"""
        if self._buf is None or self.doc is None:
            return None
        f = 1 << self._lvl
        px, py = int(x // f) - self._rect[0], int(y // f) - self._rect[1]
        if 0 <= py < self._buf.shape[0] and 0 <= px < self._buf.shape[1] and 0 <= x < self.doc.width \
                and 0 <= y < self.doc.height:
            return tuple(int(v) for v in self._buf[py, px])
        return None

    def display_rgba(self) -> np.ndarray | None:
        return self._buf

    # ---- 选区蚂蚁线 ----
    def selection_changed(self) -> None:
        self._sel_path = None
        if self.doc is not None and self.doc.selection is not None:
            self._ants.start()
        else:
            self._ants.stop()
        self.update()

    def _tick_ants(self) -> None:
        self._phase = (self._phase + 1) % 8
        self.update()

    def _selection_path(self) -> QPainterPath | None:
        import cv2
        sel = self.doc.selection if self.doc is not None else None
        if sel is None:
            return None
        lvl = max(0, min(4, self.level()))
        key = (id(sel), lvl)
        if self._sel_path is not None and self._sel_path[0] == key:
            return self._sel_path[1]
        small = self.doc._sel_level(lvl) if lvl else sel
        contours, _ = cv2.findContours((small > 127).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        f = 1 << lvl
        path = QPainterPath()
        for c in contours:
            pts = c.reshape(-1, 2)
            if len(pts) < 2:
                continue
            path.moveTo(pts[0][0] * f, pts[0][1] * f)
            for px, py in pts[1:]:
                path.lineTo(px * f, py * f)
            path.closeSubpath()
        self._sel_path = (key, path)
        return path

    def _quick_mask_image(self):
        """快速蒙版的红色叠加层（按显示级别缩小），画笔正在涂时用工具手里那份正在改的蒙版。"""
        d = self.doc
        if d is None or not d.quick_mask:
            return None
        from ...edit.document import _downscale
        t = self._tool()
        q = getattr(t, "qmask", None) if t is not None and getattr(t, "stroke", None) is not None else None
        m = q.mask if q is not None else d.selection
        lv = max(0, min(4, self.level()))
        key = (id(m), lv, None if q is None else d.version + len(getattr(t.stroke, "points", [])))
        if getattr(self, "_qm", None) is not None and self._qm[0] == key:
            return self._qm[1], lv
        if m is None:
            small = np.zeros(d.level_size(lv)[::-1], np.uint8)
        else:
            small = _downscale(m, lv)
        h, w = small.shape
        buf = np.zeros((h, w, 4), np.uint8)
        buf[..., 0] = 255
        buf[..., 3] = ((255 - small.astype(np.uint16)) * 128 // 255).astype(np.uint8)
        buf = np.ascontiguousarray(buf)
        img = QImage(buf.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        self._qm = (key, img)
        return img, lv

    # ---- 事件 ----
    def resizeEvent(self, e):
        if self.fit_mode:
            self.fit()
        else:
            self._ensure_coverage()
        super().resizeEvent(e)

    def wheelEvent(self, e):
        if self.doc is None:
            return
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier or not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            dy = e.angleDelta().y()
            if dy:
                self.set_zoom(self.zoom * (1.2 if dy > 0 else 1 / 1.2), e.position())
        else:
            self.pan_by(e.angleDelta().y(), 0)

    def _tool(self):
        return self.win.tool

    def _panning_tool(self) -> bool:
        return self._space or (self._tool() is not None and self._tool().id == "hand")

    # ---- 标尺 / 参考线 / 网格 / 对齐 ----
    RULER = 18

    def _opt(self, k, d=False):
        return self.win.opts.get(k, d)

    def snap_value(self, v: float, axis: str, extra=()) -> tuple[float, bool]:
        """把画布坐标 v 吸附到最近的参考线 / 网格 / 画布边缘和中线（屏幕上 6 像素以内）。"""
        if not self._opt("snap", True) or self.doc is None:
            return v, False
        tol = 6 / max(self.zoom, 1e-3)
        size = self.doc.width if axis == "x" else self.doc.height
        cands = [0.0, size / 2, float(size)] + list(extra)
        if self._opt("show_guides", True):
            cands += [p for o, p in self.doc.guides if (o == "v") == (axis == "x")]
        if self._opt("show_grid", False):
            step = float(self._opt("grid_step", 100)) / max(1, int(self._opt("grid_sub", 4)))
            cands.append(round(v / step) * step)
        best = min(cands, key=lambda c: abs(c - v))
        return (best, True) if abs(best - v) <= tol else (v, False)

    def snap_delta(self, edges, axis: str) -> float:
        """移动一组边（比如图层的左 / 中 / 右）时，让其中离吸附目标最近的那条对齐：返回要额外挪的量。"""
        best = None
        for e in edges:
            s, ok = self.snap_value(e, axis)
            if ok and (best is None or abs(s - e) < abs(best)):
                best = s - e
        return 0.0 if best is None else best

    def _ruler_hit(self, p: QPointF) -> str | None:
        if not self._opt("show_rulers", False):
            return None
        if p.y() < self.RULER and p.x() >= self.RULER:
            return "h"
        if p.x() < self.RULER and p.y() >= self.RULER:
            return "v"
        return None

    def _guide_hit(self, p: QPointF) -> int | None:
        if self.doc is None or not self._opt("show_guides", True):
            return None
        x, y = self.to_canvas(p)
        for i, (o, pos) in enumerate(self.doc.guides):
            q = self.to_widget(pos, 0).x() if o == "v" else self.to_widget(0, pos).y()
            if abs((p.x() if o == "v" else p.y()) - q) <= 4:
                return i
        return None

    def _guide_press(self, e) -> bool:
        p = e.position()
        hit = self._ruler_hit(p)
        if hit is not None:
            self._gdrag = {"new": hit, "pos": None}
            return True
        t = self._tool()
        if t is not None and t.id == "move":
            i = self._guide_hit(p)
            if i is not None:
                self._gdrag = {"i": i, "before": self.doc.state(), "orient": self.doc.guides[i][0]}
                return True
        return False

    def _guide_move(self, e) -> None:
        g = self._gdrag
        x, y = self.to_canvas(e.position())
        o = g.get("new") or g.get("orient")
        v = x if o == "v" else y
        if not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            v = round(v)
        if "i" in g:
            gs = list(self.doc.guides)
            gs[g["i"]] = (o, v)
            self.doc.guides = gs
        else:
            g["pos"] = v
        self.update()

    def _guide_release(self, e) -> None:
        g, self._gdrag = self._gdrag, None
        over_ruler = self._ruler_hit(e.position()) is not None or not self.rect().contains(e.position().toPoint())
        if "new" in g:
            if g["pos"] is not None and not over_ruler:
                self.doc.add_guide(g["new"], g["pos"])
        else:
            pos = None if over_ruler else self.doc.guides[g["i"]][1]
            self.doc.guides = list(g["before"]["guides"])
            self.doc.move_guide(g["i"], pos, g["before"])
        self.update()

    def _paint_overlays(self, qp: QPainter, r: QRectF) -> None:
        d = self.doc
        if self._opt("show_grid", False):
            step = float(self._opt("grid_step", 100))
            sub = max(1, int(self._opt("grid_sub", 4)))
            x0, y0 = self.to_canvas(QPointF(0, 0))
            x1, y1 = self.to_canvas(QPointF(self.width(), self.height()))
            qp.save()
            qp.setClipRect(r)
            for k, st in ((sub, step / sub), (1, step)):
                if st * self.zoom < 6:
                    continue
                qp.setPen(QPen(QColor(120, 160, 255, 70 if k > 1 else 140), 1))
                gx = math.floor(max(0, x0) / st) * st
                while gx <= min(d.width, x1):
                    p = self.to_widget(gx, 0)
                    qp.drawLine(QPointF(p.x(), r.top()), QPointF(p.x(), r.bottom()))
                    gx += st
                gy = math.floor(max(0, y0) / st) * st
                while gy <= min(d.height, y1):
                    p = self.to_widget(0, gy)
                    qp.drawLine(QPointF(r.left(), p.y()), QPointF(r.right(), p.y()))
                    gy += st
            qp.restore()
        if self._opt("show_guides", True):
            gs = list(d.guides)
            g = getattr(self, "_gdrag", None)
            if g and g.get("new") and g.get("pos") is not None:
                gs.append((g["new"], g["pos"]))
            qp.setPen(QPen(QColor("#00C8FF"), 1))
            for o, pos in gs:
                if o == "v":
                    xw = self.to_widget(pos, 0).x()
                    qp.drawLine(QPointF(xw, 0), QPointF(xw, self.height()))
                else:
                    yw = self.to_widget(0, pos).y()
                    qp.drawLine(QPointF(0, yw), QPointF(self.width(), yw))
        for reg in d.regions:                                # 画板 / 切片
            a, b = self.to_widget(reg["x"], reg["y"]), self.to_widget(reg["x"] + reg["w"], reg["y"] + reg["h"])
            col = QColor("#F08A24") if reg["kind"] == "slice" else QColor("#B0B4BA")
            qp.setPen(QPen(col, 1.2, Qt.PenStyle.DashLine if reg["kind"] == "slice" else Qt.PenStyle.SolidLine))
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.drawRect(QRectF(a, b))
            qp.setPen(col)
            qp.drawText(QPointF(a.x() + 3, a.y() - 4 if reg["kind"] == "artboard" else a.y() + 13), reg["name"])
        if self._opt("show_rulers", False):
            self._paint_rulers(qp)

    def _paint_rulers(self, qp: QPainter) -> None:
        R = self.RULER
        qp.save()
        qp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        qp.fillRect(QRectF(0, 0, self.width(), R), QColor("#2A2D31"))
        qp.fillRect(QRectF(0, 0, R, self.height()), QColor("#2A2D31"))
        f = qp.font()
        f.setPixelSize(9)
        qp.setFont(f)
        steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]
        step = next((s for s in steps if s * self.zoom >= 60), steps[-1])
        qp.setPen(QColor("#9AA0A6"))
        x0, _ = self.to_canvas(QPointF(R, 0))
        x1, _ = self.to_canvas(QPointF(self.width(), 0))
        v = math.floor(x0 / step) * step
        while v <= x1:
            xw = self.to_widget(v, 0).x()
            qp.drawLine(QPointF(xw, R - 7), QPointF(xw, R))
            qp.drawText(QPointF(xw + 2, 9), str(int(v)))
            for k in range(1, 10):
                xs = self.to_widget(v + step * k / 10, 0).x()
                qp.drawLine(QPointF(xs, R - (4 if k == 5 else 2)), QPointF(xs, R))
            v += step
        _, y0 = self.to_canvas(QPointF(0, R))
        _, y1 = self.to_canvas(QPointF(0, self.height()))
        v = math.floor(y0 / step) * step
        while v <= y1:
            yw = self.to_widget(0, v).y()
            qp.drawLine(QPointF(R - 7, yw), QPointF(R, yw))
            qp.save()
            qp.translate(9, yw + 2)
            qp.rotate(90)
            qp.drawText(QPointF(0, 0), str(int(v)))
            qp.restore()
            for k in range(1, 10):
                ys = self.to_widget(0, v + step * k / 10).y()
                qp.drawLine(QPointF(R - (4 if k == 5 else 2), ys), QPointF(R, ys))
            v += step
        if self.hover is not None:                        # 鼠标位置
            qp.setPen(QColor("#00C8FF"))
            qp.drawLine(QPointF(self.hover.x(), 0), QPointF(self.hover.x(), R))
            qp.drawLine(QPointF(0, self.hover.y()), QPointF(R, self.hover.y()))
        qp.fillRect(QRectF(0, 0, R, R), QColor("#1E2023"))
        qp.restore()

    def mousePressEvent(self, e):
        self.setFocus()
        if self.doc is None:
            return
        if e.button() == Qt.MouseButton.LeftButton and self._guide_press(e):
            return
        if e.button() == Qt.MouseButton.MiddleButton or (e.button() == Qt.MouseButton.LeftButton
                                                          and self._panning_tool()):
            self._pan = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        t = self._tool()
        if t is not None and e.button() == Qt.MouseButton.LeftButton:
            x, y = self.to_canvas(e.position())
            t.press(x, y, e)
            self.update()

    def mouseMoveEvent(self, e):
        if self.doc is None:
            return
        self.hover = e.position()
        x, y = self.to_canvas(e.position())
        if getattr(self, "_gdrag", None) is not None:
            self._guide_move(e)
            return
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.pan_by(d.x(), d.y())
            return
        t = self._tool()
        if t is not None:
            if e.buttons() & Qt.MouseButton.LeftButton:
                t.move(x, y, e)
            else:
                t.hover(x, y, e)
        self.cursor_info.emit((x, y, self.sample(x, y)))
        self.update()

    def mouseReleaseEvent(self, e):
        if getattr(self, "_gdrag", None) is not None:
            self._guide_release(e)
            return
        if self._pan is not None:
            self._pan = None
            self._update_cursor()
            return
        t = self._tool()
        if t is not None and e.button() == Qt.MouseButton.LeftButton:
            x, y = self.to_canvas(e.position())
            t.release(x, y, e)
            self.update()

    def mouseDoubleClickEvent(self, e):
        t = self._tool()
        if t is not None and e.button() == Qt.MouseButton.LeftButton:
            x, y = self.to_canvas(e.position())
            t.double_click(x, y, e)

    def enterEvent(self, e):
        # 拿到键盘焦点，按住空格就能拖动画面；正在输入文字 / 数字时不抢
        from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit, QPlainTextEdit, QTextEdit
        if not isinstance(QApplication.focusWidget(), (QLineEdit, QAbstractSpinBox, QPlainTextEdit, QTextEdit))                 and self.window().isActiveWindow():
            self.setFocus()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hover = None
        self.cursor_info.emit(None)
        self.update()
        super().leaveEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = True
            self._update_cursor()
            return
        t = self._tool()
        if t is not None and t.key(e):
            self.update()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = False
            self._update_cursor()
            return
        super().keyReleaseEvent(e)

    def _update_cursor(self) -> None:
        if self._space or (self._tool() is not None and self._tool().id == "hand"):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self._tool() is not None:
            self.setCursor(self._tool().cursor)
        else:
            self.unsetCursor()

    # ---- 绘制 ----
    def paintEvent(self, e):
        qp = QPainter(self)
        qp.fillRect(self.rect(), BG)
        if self.doc is None:
            qp.end()
            return
        r = self.image_rect()
        qp.save()
        qp.setBrushOrigin(r.topLeft())
        qp.fillRect(r, self.checker)
        if self._img is not None:
            f = 1 << self._lvl
            rx, ry, rw, rh = self._rect
            p0 = self.to_widget(rx * f, ry * f)
            target = QRectF(p0.x(), p0.y(), rw * f * self.zoom, rh * f * self.zoom)
            qp.setClipRect(r)
            qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom * f < 2.5)
            qp.drawImage(target, self._img)
        qp.restore()
        qp.setPen(QPen(QColor(0, 0, 0, 160), 1))
        qp.drawRect(r.adjusted(-0.5, -0.5, 0.5, 0.5))
        if self.zoom >= 8:                                   # 放得很大时画像素网格
            qp.setPen(QPen(QColor(128, 128, 128, 60), 1))
            x0, y0 = self.to_canvas(QPointF(0, 0))
            x1, y1 = self.to_canvas(QPointF(self.width(), self.height()))
            for gx in range(max(0, int(x0)), min(self.doc.width, int(x1) + 1) + 1):
                p = self.to_widget(gx, 0)
                qp.drawLine(QPointF(p.x(), max(0, r.top())), QPointF(p.x(), min(self.height(), r.bottom())))
            for gy in range(max(0, int(y0)), min(self.doc.height, int(y1) + 1) + 1):
                p = self.to_widget(0, gy)
                qp.drawLine(QPointF(max(0, r.left()), p.y()), QPointF(min(self.width(), r.right()), p.y()))
        qm = self._quick_mask_image()
        if qm is not None:                                   # 快速蒙版：没选中的地方盖一层半透明红色
            img, lv = qm
            f = 1 << lv
            qp.save()
            qp.setClipRect(r)
            qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            qp.drawImage(QRectF(r.left(), r.top(), img.width() * f * self.zoom, img.height() * f * self.zoom), img)
            qp.restore()
        path = None if self.doc.quick_mask else self._selection_path()
        if path is not None:
            qp.save()
            qp.setTransform(self.canvas_transform())
            qp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            pen = QPen(QColor("#FFFFFF"), 1)
            pen.setCosmetic(True)
            qp.setPen(pen)
            qp.drawPath(path)
            pen = QPen(QColor("#000000"), 1)
            pen.setCosmetic(True)
            pen.setDashPattern([4, 4])
            pen.setDashOffset(self._phase)
            qp.setPen(pen)
            qp.drawPath(path)
            qp.restore()
        self._paint_overlays(qp, r)
        t = self._tool()
        if t is not None:
            qp.setRenderHint(QPainter.RenderHint.Antialiasing)
            t.paint(qp)
        if self.show_original:
            qp.setFont(self.font())
            qp.fillRect(QRectF(12, 12, 96, 24), QColor(0, 0, 0, 170))
            qp.setPen(QColor("#FFFFFF"))
            qp.drawText(QRectF(12, 12, 96, 24), Qt.AlignmentFlag.AlignCenter, _t("原图（背景层）"))
        qp.end()
