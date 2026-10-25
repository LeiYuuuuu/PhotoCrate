"""修图工具。每个工具处理画布上的鼠标 / 键盘，并在画布上画自己的辅助线（选框、裁剪框、笔刷圈…）。

坐标：press / move / release 收到的是画布坐标（可以是小数、可以在画布外）。
选区工具的组合方式与 Photoshop 相同：按住 Shift 添加、Alt 减去、Shift+Alt 交叉，也可以在选项栏里固定。
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QApplication

from ...edit import paint as P
from ...edit import warp as W
from ...i18n import t as _t

SEL_MODES = [("new", _t("新选区")), ("add", _t("添加")), ("sub", _t("减去")), ("and", _t("交叉"))]


def _mods(e) -> tuple[bool, bool, bool]:
    m = e.modifiers()
    return (bool(m & Qt.KeyboardModifier.ShiftModifier), bool(m & Qt.KeyboardModifier.AltModifier),
            bool(m & Qt.KeyboardModifier.ControlModifier))


class _Busy:
    """耗时操作时显示等待光标。"""

    def __init__(self, win, msg: str = ""):
        self.win, self.msg = win, msg

    def __enter__(self):
        if self.msg:
            self.win.status_msg(self.msg)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

    def __exit__(self, *a):
        QApplication.restoreOverrideCursor()


def _dash_pen(dark: bool = False) -> QPen:
    pen = QPen(QColor("#000000" if dark else "#FFFFFF"), 1)
    if dark:
        pen.setDashPattern([4, 4])
    return pen


class Tool:
    id = ""
    name = ""
    shortcut = ""
    icon = ""
    tip = ""
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, win):
        self.win = win

    @property
    def doc(self):
        return self.win.doc

    @property
    def cv(self):
        return self.win.canvas

    def opt(self, k, default=None):
        return self.win.opts.get(k, default)

    def options(self, bar) -> None:
        pass

    def activate(self) -> None:
        pass

    def deactivate(self) -> None:
        pass

    def busy(self) -> bool:
        return False

    def press(self, x, y, e): pass

    def move(self, x, y, e): pass

    def release(self, x, y, e): pass

    def hover(self, x, y, e): pass

    def double_click(self, x, y, e): pass

    def key(self, e) -> bool:
        return False

    def paint(self, qp: QPainter) -> None:
        pass

    def _sel_mode(self, e) -> str:
        shift, alt, _ = _mods(e)
        if shift and alt:
            return "and"
        if shift:
            return "add"
        if alt:
            return "sub"
        return self.opt("sel_mode", "new")

    def _apply_selection(self, m, mode: str, name: str) -> None:
        self.doc.set_selection(P.combine(self.doc.selection, m, mode), name)


# ---------------------------------------------------------------------------
# 移动 / 抓手 / 缩放
# ---------------------------------------------------------------------------

class MoveTool(Tool):
    id, name, shortcut, icon = "move", _t("移动"), "V", "move"
    tip = _t("移动工具（V）：拖动当前图层；方向键微移 1 像素（Shift 10 像素）")
    cursor = Qt.CursorShape.SizeAllCursor

    def __init__(self, win):
        super().__init__(win)
        self.drag = None

    def options(self, bar):
        bar.check(_t("自动选择图层"), "move_auto", False)
        bar.label(_t("勾选后点哪里就选中那里最上面的图层"))

    def busy(self):
        return self.drag is not None

    def press(self, x, y, e):
        d = self.doc
        if self.opt("move_auto", False):
            for l in reversed(d.layers):
                if l.visible and l.has_pixels:
                    lx, ly = int(x - l.x), int(y - l.y)
                    if 0 <= lx < l.w and 0 <= ly < l.h and l.pixels[ly, lx, 3] > 10:
                        if l.id != d.active_id:
                            d.set_active(l.id)
                        break
        l = d.active
        if l is None or not d.movable(l):
            self.win.status_msg(_t("调整图层不能移动；选中一个像素 / 文字图层再拖"))
            return
        if self.win._locked(l, "position"):
            return
        self.drag = (x, y, [(m, m.x, m.y) for m in d.movable(l)], d.state(), l)

    def move(self, x, y, e):
        if self.drag is None:
            return
        x0, y0, items, _, l = self.drag
        dx, dy = int(round(x - x0)), int(round(y - y0))
        shift, _, _ = _mods(e)
        if shift:                                       # 只沿水平或垂直方向
            if abs(dx) > abs(dy):
                dy = 0
            else:
                dx = 0
        m0, mx, my = items[0]
        if (mx + dx, my + dy) != (m0.x, m0.y):
            for m, ox, oy in items:
                m.x, m.y = ox + dx, oy + dy
                m.version += 1
            l.version += 1
            self.doc.version += 1
            self.cv.request_render(True)

    def release(self, x, y, e):
        if self.drag is None:
            return
        _, _, items, before, l = self.drag
        self.drag = None
        m0, mx, my = items[0]
        if (m0.x, m0.y) != (mx, my):
            self.doc.translate(l.id, 0, 0, before=before)

    def key(self, e):
        k = e.key()
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        dx, dy = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0), Qt.Key.Key_Up: (0, -step),
                  Qt.Key.Key_Down: (0, step)}.get(k, (0, 0))
        if (dx or dy) and self.doc.active is not None and self.doc.movable(self.doc.active):
            self.doc.translate(self.doc.active_id, dx, dy)
            return True
        return False

    def paint(self, qp):
        l = self.doc.active if self.doc is not None else None
        if l is None or l.canvas_sized or l.xform is not None:
            return
        a = self.cv.to_widget(l.x, l.y)
        b = self.cv.to_widget(l.x + l.w, l.y + l.h)
        r = QRectF(a, b)
        qp.setPen(_dash_pen())
        qp.drawRect(r)
        qp.setPen(_dash_pen(True))
        qp.drawRect(r)


class HandTool(Tool):
    id, name, shortcut, icon = "hand", _t("抓手"), "H", "hand"
    tip = _t("抓手工具（H）：拖动画面。任何工具下按住空格也可以拖动")
    cursor = Qt.CursorShape.OpenHandCursor


class ZoomTool(Tool):
    id, name, shortcut, icon = "zoom", _t("缩放"), "Z", "zoom"
    tip = _t("缩放工具（Z）：单击放大，Alt + 单击缩小。滚轮也能缩放")
    cursor = Qt.CursorShape.CrossCursor

    def press(self, x, y, e):
        _, alt, _ = _mods(e)
        self.cv.zoom_step(not alt, e.position())


# ---------------------------------------------------------------------------
# 选区工具
# ---------------------------------------------------------------------------

class MarqueeTool(Tool):
    id, name, shortcut, icon = "marquee", _t("选框"), "M", "marquee"
    tip = _t("选框工具（M）：拖出矩形 / 椭圆选区；拖动中按 Shift 为正方形 / 正圆；单击空白处取消选择")

    def __init__(self, win):
        super().__init__(win)
        self.a = self.b = None
        self.mode = "new"
        self.free_shift = False

    def options(self, bar):
        bar.seg("sel_mode", SEL_MODES, "new")
        bar.combo(_t("形状"), "marquee_shape", [("rect", _t("矩形")), ("ellipse", _t("椭圆"))], "rect")
        bar.spin(_t("羽化"), "feather", 0, 250, 0, " px")

    def busy(self):
        return self.a is not None

    def press(self, x, y, e):
        self.a = self.b = (x, y)
        self.mode = self._sel_mode(e)
        self.free_shift = not _mods(e)[0]

    def move(self, x, y, e):
        if self.a is None:
            return
        if self.free_shift and _mods(e)[0]:
            s = max(abs(x - self.a[0]), abs(y - self.a[1]))
            x = self.a[0] + math.copysign(s, x - self.a[0])
            y = self.a[1] + math.copysign(s, y - self.a[1])
        self.b = (x, y)

    def release(self, x, y, e):
        if self.a is None:
            return
        a, b = self.a, self.b
        self.a = self.b = None
        if math.hypot(b[0] - a[0], b[1] - a[1]) * self.cv.zoom < 3:
            if self.mode == "new":
                self.doc.deselect()
            return
        d = self.doc
        ell = self.opt("marquee_shape", "rect") == "ellipse"
        m = P.rect_mask(d.width, d.height, a[0], a[1], b[0], b[1], ell, float(self.opt("feather", 0)))
        self._apply_selection(m, self.mode, _t("椭圆选框") if ell else _t("矩形选框"))

    def paint(self, qp):
        if self.a is None:
            return
        r = QRectF(self.cv.to_widget(*self.a), self.cv.to_widget(*self.b)).normalized()
        ell = self.opt("marquee_shape", "rect") == "ellipse"
        for pen in (_dash_pen(), _dash_pen(True)):
            qp.setPen(pen)
            qp.drawEllipse(r) if ell else qp.drawRect(r)


class LassoTool(Tool):
    id, name, shortcut, icon = "lasso", _t("套索"), "L", "lasso"
    tip = _t("套索工具（L）：按住拖动圈出选区；多边形模式下逐点单击，双击或回车闭合，Esc 取消，Backspace 撤一个点")

    def __init__(self, win):
        super().__init__(win)
        self.pts: list[tuple[float, float]] = []
        self.mode = "new"
        self.cur = None

    def options(self, bar):
        bar.seg("sel_mode", SEL_MODES, "new")
        bar.combo(_t("方式"), "lasso_kind", [("free", _t("自由")), ("poly", _t("多边形"))], "free")
        bar.spin(_t("羽化"), "feather", 0, 250, 0, " px")

    def busy(self):
        return bool(self.pts)

    def deactivate(self):
        self.pts = []

    def _poly(self) -> bool:
        return self.opt("lasso_kind", "free") == "poly"

    def press(self, x, y, e):
        if self._poly():
            if not self.pts:
                self.mode = self._sel_mode(e)
            elif len(self.pts) >= 3:
                p0 = self.cv.to_widget(*self.pts[0])
                if math.hypot(e.position().x() - p0.x(), e.position().y() - p0.y()) < 9:
                    self._close()
                    return
            self.pts.append((x, y))
            return
        self.mode = self._sel_mode(e)
        self.pts = [(x, y)]

    def move(self, x, y, e):
        if self._poly():
            self.cur = (x, y)
            return
        if self.pts:
            lx, ly = self.pts[-1]
            if math.hypot(x - lx, y - ly) * self.cv.zoom >= 1.5:
                self.pts.append((x, y))

    def hover(self, x, y, e):
        self.cur = (x, y)

    def release(self, x, y, e):
        if not self._poly() and self.pts:
            self._close()

    def double_click(self, x, y, e):
        if self._poly() and len(self.pts) >= 3:
            self._close()

    def _close(self):
        pts, self.pts = self.pts, []
        d = self.doc
        if len(pts) < 3:
            if self.mode == "new":
                d.deselect()
            return
        m = P.polygon_mask(d.width, d.height, pts, float(self.opt("feather", 0)))
        self._apply_selection(m, self.mode, _t("多边形套索") if self._poly() else _t("套索"))

    def key(self, e):
        if not self.pts:
            return False
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._close()
        elif e.key() == Qt.Key.Key_Escape:
            self.pts = []
        elif e.key() == Qt.Key.Key_Backspace:
            self.pts.pop()
        else:
            return False
        return True

    def paint(self, qp):
        if not self.pts:
            return
        pts = [self.cv.to_widget(*p) for p in self.pts]
        if self._poly() and self.cur is not None:
            pts.append(self.cv.to_widget(*self.cur))
        poly = QPolygonF(pts)
        for pen in (_dash_pen(), _dash_pen(True)):
            qp.setPen(pen)
            qp.drawPolyline(poly)


class WandTool(Tool):
    id, name, shortcut, icon = "wand", _t("魔棒"), "W", "wand"
    tip = _t("魔棒工具（W）：单击选中颜色相近的区域。容差越大选得越多；取消“连续”会选中全图所有相近颜色")

    def options(self, bar):
        bar.seg("sel_mode", SEL_MODES, "new")
        bar.spin(_t("容差"), "wand_tol", 0, 255, 32)
        bar.check(_t("连续"), "wand_contig", True)
        bar.check(_t("对所有图层取样"), "wand_all", False)

    def press(self, x, y, e):
        d = self.doc
        if not (0 <= x < d.width and 0 <= y < d.height):
            return
        mode = self._sel_mode(e)
        with _Busy(self.win, _t("魔棒…")):
            src = d.flat_rgba() if self.opt("wand_all", False) or d.active is None else d.sample_layer_rgba(d.active)
            m = P.wand(src, int(x), int(y), float(self.opt("wand_tol", 32)), bool(self.opt("wand_contig", True)))
        self._apply_selection(m, mode, _t("魔棒"))
        self.win.status_msg("")


class ObjectTool(Tool):
    id, name, shortcut, icon = "object", _t("对象选择"), "W", "object"
    tip = _t("对象选择工具（W，Shift+W 和魔棒轮换）：框住一个物体 / 人，自动沿边缘选出来（GrabCut，几秒钟）。之后可以用 Shift / Alt 补选或减选")

    def __init__(self, win):
        super().__init__(win)
        self.a = self.b = None
        self.mode = "new"

    def options(self, bar):
        bar.seg("sel_mode", SEL_MODES, "new")
        bar.label(_t("框选时留一点余量，主体边缘不要被框线切到"))

    def busy(self):
        return self.a is not None

    def press(self, x, y, e):
        self.a = self.b = (x, y)
        self.mode = self._sel_mode(e)

    def move(self, x, y, e):
        if self.a is not None:
            self.b = (x, y)

    def release(self, x, y, e):
        if self.a is None:
            return
        a, b = self.a, self.b
        self.a = self.b = None
        if abs(b[0] - a[0]) * self.cv.zoom < 6 or abs(b[1] - a[1]) * self.cv.zoom < 6:
            return
        with _Busy(self.win, _t("正在识别主体…")):
            m = P.object_select_doc(self.doc, (a[0], a[1], b[0], b[1]))
        if not m.any():
            self.win.status_msg(_t("没有识别出主体：框得再贴近一些试试"))
            return
        self._apply_selection(m, self.mode, _t("对象选择"))
        self.win.status_msg(_t("已选出主体：可以 Ctrl+J 抠出来，或新建调整图层只调它"))

    def paint(self, qp):
        if self.a is None:
            return
        r = QRectF(self.cv.to_widget(*self.a), self.cv.to_widget(*self.b)).normalized()
        qp.setPen(QPen(QColor("#1EC6E6"), 1.5))
        qp.drawRect(r)


# ---------------------------------------------------------------------------
# 裁剪（含拉直）
# ---------------------------------------------------------------------------

RATIOS = [("free", _t("自由")), ("orig", _t("原始比例")), ("1:1", "1 : 1"), ("4:3", "4 : 3"), ("3:2", "3 : 2"),
          ("16:9", "16 : 9"), ("5:4", "5 : 4"), ("3:4", "3 : 4"), ("2:3", "2 : 3"), ("9:16", "9 : 16"),
          ("4:5", "4 : 5")]
_HANDLES = [(-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0)]


class CropTool(Tool):
    id, name, shortcut, icon = "crop", _t("裁剪"), "C", "crop"
    tip = (_t("裁剪工具（C）：拖动边框或角调整，框内拖动移动；在框外拖出新的裁剪框；"
           "按住 Ctrl 沿地平线拖一条线自动拉直。回车或双击应用，Esc 复位"))

    def __init__(self, win):
        super().__init__(win)
        self.box = (0.0, 0.0, 1.0, 1.0)       # 中心 x, y, 宽, 高（未旋转时的尺寸）
        self.base = self.box
        self.angle = 0.0
        self.drag = None
        self.line = None

    def options(self, bar):
        bar.combo(_t("比例"), "crop_ratio", RATIOS, "free", on_change=lambda _: self._ratio_changed())
        bar.spin(_t("拉直"), "crop_angle", -45, 45, 0, "°", decimals=1, on_change=self._angle_changed)
        bar.button(_t("复位"), self.reset)
        bar.button(_t("应用裁剪"), self.apply, "primary")

    def activate(self):
        self.reset()

    def busy(self):
        return self.drag is not None or self.line is not None

    def reset(self):
        d = self.doc
        if d is None:
            return
        self.box = self.base = (d.width / 2, d.height / 2, float(d.width), float(d.height))
        self.angle = 0.0
        self.win.set_opt("crop_angle", 0.0)
        self._ratio_changed()
        self.cv.update()

    def ratio(self) -> float | None:
        r = self.opt("crop_ratio", "free")
        if r == "free":
            return None
        if r == "orig":
            return self.doc.width / self.doc.height
        a, b = r.split(":")
        return float(a) / float(b)

    def _ratio_changed(self):
        r = self.ratio()
        if r is None:
            return
        cx, cy, w, h = self.base
        if w / h > r:
            w = h * r
        else:
            h = w / r
        self.base = (cx, cy, w, h)
        self._fit_rotation()
        self.cv.update()

    def _angle_changed(self, v):
        self.angle = float(v)
        self._fit_rotation()
        self.cv.update()

    def _fit_rotation(self):
        """拉直时把裁剪框缩到旋转后仍在画面以内（四个角不露出透明）。"""
        cx, cy, w, h = self.base
        a = math.radians(self.angle)
        c, s = abs(math.cos(a)), abs(math.sin(a))
        W, H = self.doc.width, self.doc.height
        bw, bh = w * c + h * s, w * s + h * c
        lim_x, lim_y = 2 * min(cx, W - cx), 2 * min(cy, H - cy)
        k = min(1.0, lim_x / bw if bw else 1, lim_y / bh if bh else 1)
        self.box = (cx, cy, max(1.0, w * k), max(1.0, h * k))

    # ---- 几何 ----
    def _R(self, ang=None):
        a = math.radians(self.angle if ang is None else ang)
        return math.cos(a), math.sin(a)

    def _to_local(self, x, y, box=None):
        cx, cy, _, _ = box or self.box
        c, s = self._R()
        dx, dy = x - cx, y - cy
        return dx * c + dy * s, -dx * s + dy * c

    def _to_canvas(self, lx, ly, box=None):
        cx, cy, _, _ = box or self.box
        c, s = self._R()
        return cx + lx * c - ly * s, cy + lx * s + ly * c

    def corners(self):
        _, _, w, h = self.box
        return [self._to_canvas(sx * w / 2, sy * h / 2) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]

    def _hit(self, e) -> object:
        _, _, w, h = self.box
        p = e.position()
        for hx, hy in _HANDLES:
            q = self.cv.to_widget(*self._to_canvas(hx * w / 2, hy * h / 2))
            if abs(q.x() - p.x()) <= 9 and abs(q.y() - p.y()) <= 9:
                return (hx, hy)
        x, y = self.cv.to_canvas(p)
        lx, ly = self._to_local(x, y)
        if abs(lx) <= w / 2 and abs(ly) <= h / 2:
            return "move"
        return "new"

    # ---- 鼠标 ----
    def press(self, x, y, e):
        if _mods(e)[2]:                                  # Ctrl：拉直线
            self.line = [(x, y), (x, y)]
            return
        h = self._hit(e)
        if h == "new":
            self.box = (x, y, 0.0, 0.0)
            h = (1, 1)
        self.drag = (h, x, y, self.box)

    def move(self, x, y, e):
        if self.line is not None:
            self.line[1] = (x, y)
            return
        if self.drag is None:
            return
        h, x0, y0, box0 = self.drag
        if h == "move":
            cx, cy, w, hh = box0
            self.box = (cx + x - x0, cy + y - y0, w, hh)
        else:
            self._resize(h, x, y, box0, new=box0[2] == 0)
        self.base = self.box

    def _resize(self, hnd, x, y, box0, new=False):
        hx, hy = hnd
        cx0, cy0, w0, h0 = box0
        lx, ly = self._to_local(x, y, box0)
        left, right, top, bottom = -w0 / 2, w0 / 2, -h0 / 2, h0 / 2
        if new:
            hx, hy = (1 if lx >= 0 else -1), (1 if ly >= 0 else -1)
        if hx == 1:
            right = lx
        elif hx == -1:
            left = lx
        if hy == 1:
            bottom = ly
        elif hy == -1:
            top = ly
        r = self.ratio()
        if r is not None:
            w, h = abs(right - left), abs(bottom - top)
            if hx and hy:
                if w / r > h:
                    h = w / r
                else:
                    w = h * r
                if hx == 1:
                    right = left + math.copysign(w, right - left or 1)
                else:
                    left = right - math.copysign(w, right - left or 1)
                if hy == 1:
                    bottom = top + math.copysign(h, bottom - top or 1)
                else:
                    top = bottom - math.copysign(h, bottom - top or 1)
            elif hx:
                h = w / r
                top, bottom = -h / 2, h / 2
            elif hy:
                w = h * r
                left, right = -w / 2, w / 2
        left, right = sorted((left, right))
        top, bottom = sorted((top, bottom))
        ncx, ncy = self._to_canvas((left + right) / 2, (top + bottom) / 2, box0)
        self.box = (ncx, ncy, max(1.0, right - left), max(1.0, bottom - top))

    def release(self, x, y, e):
        if self.line is not None:
            (x0, y0), (x1, y1) = self.line
            self.line = None
            if math.hypot(x1 - x0, y1 - y0) * self.cv.zoom > 10:
                a = math.degrees(math.atan2(y1 - y0, x1 - x0))
                while a > 45:
                    a -= 90
                while a < -45:
                    a += 90
                self.win.set_opt("crop_angle", round(a, 1))
                self._angle_changed(round(a, 1))
            return
        self.drag = None

    def double_click(self, x, y, e):
        lx, ly = self._to_local(x, y)
        if abs(lx) <= self.box[2] / 2 and abs(ly) <= self.box[3] / 2:
            self.apply()

    def key(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.apply()
            return True
        if e.key() == Qt.Key.Key_Escape:
            self.reset()
            return True
        return False

    def apply(self):
        cx, cy, w, h = self.box
        d = self.doc
        if w < 2 or h < 2:
            return
        if abs(self.angle) < 1e-3 and abs(cx - d.width / 2) < 0.5 and abs(cy - d.height / 2) < 0.5 \
                and abs(w - d.width) < 0.5 and abs(h - d.height) < 0.5:
            self.win.status_msg(_t("裁剪框和画布一样大，没有需要裁掉的"))
            return
        with _Busy(self.win, _t("正在裁剪…")):
            d.crop(cx - w / 2, cy - h / 2, w, h, self.angle)
        self.win.status_msg(_t('已裁剪为 {0} × {1}', d.width, d.height))
        self.reset()

    def paint(self, qp):
        if self.doc is None:
            return
        poly = QPolygonF([self.cv.to_widget(*p) for p in self.corners()])
        path = QPainterPath()
        path.addRect(QRectF(self.cv.rect()))
        path.addPolygon(poly)
        path.setFillRule(Qt.FillRule.OddEvenFill)
        qp.fillPath(path, QColor(0, 0, 0, 130))
        qp.setPen(QPen(QColor(255, 255, 255, 90), 1))
        _, _, w, h = self.box
        for k in (1, 2):
            a = self.cv.to_widget(*self._to_canvas(-w / 2 + w * k / 3, -h / 2))
            b = self.cv.to_widget(*self._to_canvas(-w / 2 + w * k / 3, h / 2))
            qp.drawLine(a, b)
            a = self.cv.to_widget(*self._to_canvas(-w / 2, -h / 2 + h * k / 3))
            b = self.cv.to_widget(*self._to_canvas(w / 2, -h / 2 + h * k / 3))
            qp.drawLine(a, b)
        qp.setPen(QPen(QColor("#FFFFFF"), 1.2))
        qp.drawPolygon(poly)
        qp.setBrush(QColor("#FFFFFF"))
        qp.setPen(QPen(QColor("#000000"), 1))
        for hx, hy in _HANDLES:
            q = self.cv.to_widget(*self._to_canvas(hx * w / 2, hy * h / 2))
            qp.drawRect(QRectF(q.x() - 4, q.y() - 4, 8, 8))
        qp.setBrush(Qt.BrushStyle.NoBrush)
        c = self.cv.to_widget(self.box[0], self.box[1] + h / 2)
        txt = f"{int(round(w))} × {int(round(h))}" + (f"  ·  {self.angle:+.1f}°" if abs(self.angle) > 0.05 else "")
        qp.fillRect(QRectF(c.x() - 70, c.y() + 10, 140, 22), QColor(0, 0, 0, 170))
        qp.setPen(QColor("#FFFFFF"))
        qp.drawText(QRectF(c.x() - 70, c.y() + 10, 140, 22), Qt.AlignmentFlag.AlignCenter, txt)
        if self.line is not None:
            qp.setPen(QPen(QColor("#1EC6E6"), 2))
            qp.drawLine(self.cv.to_widget(*self.line[0]), self.cv.to_widget(*self.line[1]))


# ---------------------------------------------------------------------------
# 吸管
# ---------------------------------------------------------------------------

class EyedropperTool(Tool):
    id, name, shortcut, icon = "eyedropper", _t("吸管"), "I", "eyedropper"
    tip = _t("吸管工具（I）：单击取前景色，Alt + 单击取背景色。用画笔时按住 Alt 也能临时吸色")

    def press(self, x, y, e):
        c = self.cv.sample(x, y)
        if c is None:
            return
        if _mods(e)[1]:
            self.win.set_bg(QColor(c[0], c[1], c[2]))
        else:
            self.win.set_fg(QColor(c[0], c[1], c[2]))

    move = press


# ---------------------------------------------------------------------------
# 画笔类
# ---------------------------------------------------------------------------

class PaintTool(Tool):
    id, name, shortcut, icon = "brush", _t("画笔"), "B", "brush"
    tip = _t("画笔工具（B）：用前景色画。[ ] 调大小，Shift + 单击画直线，Alt 临时吸色。选中蒙版时黑色隐藏、白色显示")
    mode = "paint"

    def __init__(self, win):
        super().__init__(win)
        self.stroke = None
        self.last_end = None
        self.pos = None

    def options(self, bar):
        bar.spin(_t("大小"), "brush_size", 1, 2000, 40, " px")
        bar.spin(_t("硬度"), "brush_hard", 0, 100, 80, "%")
        bar.spin(_t("不透明度"), "brush_opacity", 1, 100, 100, "%")

    def busy(self):
        return self.stroke is not None

    def deactivate(self):
        if self.stroke is not None:
            self.stroke.cancel()
            self.stroke = None
            self.cv.request_render()

    def _target(self):
        d = self.doc
        if d.quick_mask and self.mode in ("paint", "erase"):        # 快速蒙版：画在选区上
            from ...edit.document import Layer
            q = Layer("adjust", _t("快速蒙版"))
            q.mask = d.selection.copy() if d.selection is not None else np.zeros((d.height, d.width), np.uint8)
            self.qmask = q
            return q, True
        self.qmask = None
        l = d.active
        if l is None:
            return None
        to_mask = d.mask_edit and l.mask is not None
        if not to_mask and self.win._locked(l, "pixels"):
            return None
        if not to_mask and l.canvas_sized:
            self.win.status_msg(_t("调整图层没有像素：先给它加蒙版，再在蒙版上画（黑色隐藏、白色显示）")
                                if l.kind == "adjust" else _t("图层组没有像素：选中组里的一个图层，或给组加蒙版后在蒙版上画"))
            return None
        if not l.visible:
            self.win.status_msg(_t("当前图层是隐藏的，先点眼睛图标显示它"))
            return None
        return l, to_mask

    def _size(self) -> float:
        return float(self.opt("brush_size", 40))

    def _stroke_kw(self) -> dict:
        fg = self.win.fg
        return {"color": (fg.red(), fg.green(), fg.blue()), "size": self._size(),
                "hardness": float(self.opt("brush_hard", 80)) / 100,
                "opacity": float(self.opt("brush_opacity", 100)) / 100}

    def _begin(self, x, y, e) -> bool:
        return True

    def press(self, x, y, e):
        shift, alt, _ = _mods(e)
        if alt and self.mode == "paint":                  # Alt：临时吸管
            c = self.cv.sample(x, y)
            if c is not None:
                self.win.set_fg(QColor(c[0], c[1], c[2]))
            return
        if not self._begin(x, y, e):
            return
        tg = self._target()
        if tg is None:
            return
        l, to_mask = tg
        kw = self._stroke_kw()
        if getattr(self, "qmask", None) is not None:
            kw["record"] = False
        self.stroke = P.Stroke(self.doc, l, self.mode, to_mask=to_mask, **kw)
        if shift and self.last_end is not None:
            self.stroke.add(*self.last_end)
        r = self.stroke.add(x, y)
        if r:
            self.cv.update_canvas_rect(r)

    def move(self, x, y, e):
        self.pos = (x, y)
        if self.stroke is None:
            return
        r = self.stroke.add(x, y)
        if r:
            self.cv.update_canvas_rect(r)

    def hover(self, x, y, e):
        self.pos = (x, y)

    def release(self, x, y, e):
        if self.stroke is None:
            return
        s, self.stroke = self.stroke, None
        with _Busy(self.win, _t("正在修复…") if self.mode == "heal" else ""):
            r = s.finish()
        q, self.qmask = getattr(self, "qmask", None), None
        if q is not None and r:
            self.doc.set_selection(q.mask.copy() if q.mask.any() else None, _t("快速蒙版"))
            self.cv.update()
            r = None
        self.last_end = (x, y)
        if r:
            self.cv.update_canvas_rect(r)
        self.win.after_stroke()

    def paint(self, qp):
        if self.cv.hover is None:
            return
        x, y = self.cv.to_canvas(self.cv.hover)
        c = self.cv.to_widget(x, y)
        r = max(1.5, self._size() / 2 * self.cv.zoom)
        if self.stroke is not None and self.mode == "heal" and len(self.stroke.points) > 0:
            l = self.stroke.layer
            ox, oy = (0, 0) if l.canvas_sized else (l.x, l.y)
            pen = QPen(QColor(255, 90, 90, 110), r * 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            qp.setPen(pen)
            pts = [self.cv.to_widget(px + ox, py + oy) for px, py in self.stroke.points]
            if len(pts) == 1:
                pts.append(pts[0] + QPointF(0.1, 0))
            qp.drawPolyline(QPolygonF(pts))
        qp.setBrush(Qt.BrushStyle.NoBrush)
        qp.setPen(QPen(QColor(0, 0, 0, 200), 1))
        qp.drawEllipse(c, r + 0.5, r + 0.5)
        qp.setPen(QPen(QColor(255, 255, 255, 220), 1))
        qp.drawEllipse(c, r - 0.5, r - 0.5)


class EraserTool(PaintTool):
    id, name, shortcut, icon = "eraser", _t("橡皮擦"), "E", "eraser"
    tip = _t("橡皮擦工具（E）：把当前图层擦成透明（在蒙版上擦 = 涂黑隐藏）")
    mode = "erase"


class HealTool(PaintTool):
    id, name, shortcut, icon = "heal", _t("污点修复画笔"), "J", "heal"
    tip = _t("污点修复画笔（J）：涂抹痘痘、灰尘、电线等，松开后用周围的画面自动补上。大面积去除可以先选区再用「编辑 → 内容识别填充」")
    mode = "heal"

    def options(self, bar):
        bar.spin(_t("大小"), "heal_size", 2, 1000, 30, " px")
        bar.label(_t("涂抹要去掉的地方，比瑕疵稍大一点；一次修不干净可以再涂一遍"))

    def _size(self) -> float:
        return float(self.opt("heal_size", 30))

    def _stroke_kw(self) -> dict:
        return {"size": self._size(), "hardness": 0.5, "opacity": 1.0}

    def _target(self):
        tg = super()._target()
        if tg is not None and tg[1]:
            self.win.status_msg(_t("修复画笔只能用在像素上：先在图层面板里点图层缩略图（不是蒙版）"))
            return None
        return tg


class PatchTool(Tool):
    id, name, shortcut, icon = "patch", _t("修补"), "J", "patch"
    tip = _t("修补工具（J）：先圈出要修掉的地方（或用任何选区工具选好），再把选区拖到干净的地方，松开后用那里的画面补上，"
             "边缘的颜色和亮度自动过渡")

    def __init__(self, win):
        super().__init__(win)
        self.pts: list = []
        self.drag = None

    def options(self, bar):
        bar.label(_t("在选区外拖动 = 圈出要修的地方；在选区里拖动 = 拖到用来补的干净区域"))

    def busy(self):
        return bool(self.pts) or self.drag is not None

    def deactivate(self):
        self.pts, self.drag = [], None

    def _inside(self, x, y) -> bool:
        sel = self.doc.selection
        return sel is not None and 0 <= int(x) < sel.shape[1] and 0 <= int(y) < sel.shape[0] and sel[int(y), int(x)] > 127

    def press(self, x, y, e):
        if self._inside(x, y):
            self.drag = [(x, y), (x, y)]
        else:
            self.pts = [(x, y)]

    def move(self, x, y, e):
        if self.drag is not None:
            self.drag[1] = (x, y)
        elif self.pts:
            lx, ly = self.pts[-1]
            if math.hypot(x - lx, y - ly) * self.cv.zoom >= 1.5:
                self.pts.append((x, y))

    def release(self, x, y, e):
        d = self.doc
        if self.pts:
            pts, self.pts = self.pts, []
            if len(pts) >= 3:
                d.set_selection(P.polygon_mask(d.width, d.height, pts, 0), _t("修补：选区"))
            return
        if self.drag is None:
            return
        (x0, y0), (x1, y1) = self.drag
        self.drag = None
        dx, dy = int(round(x1 - x0)), int(round(y1 - y0))
        if abs(dx) + abs(dy) < 2:
            return
        l = d.active
        if l is None or not l.has_pixels or self.win._locked(l, "pixels"):
            self.win.status_msg(_t("修补要用在像素图层上：在图层面板里选中它"))
            return
        sel = d.sel_local(l)
        m = (sel * 255).astype(np.uint8)
        with _Busy(self.win, _t("正在修补…")):
            out = P.patch(l.pixels, m, dx, dy)
        if out is None:
            self.win.status_msg(_t("修补失败：拖到的位置超出了图层，或选区碰到了图层边缘"))
            return
        d.replace_pixels(l.id, out, _t("修补"))
        self.win.status_msg(_t("已修补：不满意 Ctrl+Z，换个地方再拖"))

    def paint(self, qp):
        pen = (_dash_pen(), _dash_pen(True))
        if self.pts:
            poly = QPolygonF([self.cv.to_widget(*p) for p in self.pts])
            for p in pen:
                qp.setPen(p)
                qp.drawPolyline(poly)
        if self.drag is not None and self.doc.selection is not None:
            bb = self.doc.selection_bbox()
            if bb:
                (x0, y0), (x1, y1) = self.drag
                r = QRectF(self.cv.to_widget(bb[0] + x1 - x0, bb[1] + y1 - y0),
                           self.cv.to_widget(bb[0] + bb[2] + x1 - x0, bb[1] + bb[3] + y1 - y0))
                qp.setPen(QPen(QColor("#1EC6E6"), 1.5, Qt.PenStyle.DashLine))
                qp.drawRect(r)


class RedEyeTool(Tool):
    id, name, shortcut, icon = "redeye", _t("红眼"), "J", "redeye"
    tip = _t("红眼工具（J）：在红眼上单击（或拖一个框框住眼睛），把发红的瞳孔变成自然的暗色")

    def __init__(self, win):
        super().__init__(win)
        self.a = self.b = None

    def options(self, bar):
        bar.spin(_t("瞳孔大小"), "redeye_size", 4, 400, 30, " px")
        bar.spin(_t("变暗量"), "redeye_dark", 0, 100, 50, "%")

    def busy(self):
        return self.a is not None

    def press(self, x, y, e):
        self.a = self.b = (x, y)

    def move(self, x, y, e):
        if self.a is not None:
            self.b = (x, y)

    def release(self, x, y, e):
        if self.a is None:
            return
        a, b = self.a, self.b
        self.a = self.b = None
        d = self.doc
        l = d.active
        if l is None or not l.has_pixels or self.win._locked(l, "pixels"):
            self.win.status_msg(_t("红眼工具要用在像素图层上"))
            return
        if math.hypot(b[0] - a[0], b[1] - a[1]) * self.cv.zoom > 6:
            cx, cy, r = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, max(abs(b[0] - a[0]), abs(b[1] - a[1])) / 2
        else:
            cx, cy, r = a[0], a[1], float(self.opt("redeye_size", 30)) / 2
        out = P.red_eye(l.pixels, cx - l.x, cy - l.y, r, float(self.opt("redeye_dark", 50)) / 100)
        if out is None:
            self.win.status_msg(_t("这里没有找到发红的瞳孔：点准一点，或把「瞳孔大小」调大"))
            return
        d.replace_pixels(l.id, out, _t("去红眼"))

    def paint(self, qp):
        if self.a is None:
            return
        qp.setPen(QPen(QColor("#1EC6E6"), 1.5))
        qp.drawRect(QRectF(self.cv.to_widget(*self.a), self.cv.to_widget(*self.b)).normalized())


class CloneTool(PaintTool):
    id, name, shortcut, icon = "clone", _t("仿制图章"), "S", "clone"
    tip = _t("仿制图章（S）：先按住 Alt 单击要复制的地方（取样点），再在别处涂抹，把取样点的画面画过来")
    mode = "clone"

    def __init__(self, win):
        super().__init__(win)
        self.src = None
        self.offset = None

    def options(self, bar):
        super().options(bar)
        bar.check(_t("对齐"), "clone_aligned", True)

    def _begin(self, x, y, e):
        if _mods(e)[1]:
            self.src = (x, y)
            self.offset = None
            self.win.status_msg(_t('取样点：{0}, {1}。在要修补的地方涂抹', int(x), int(y)))
            return False
        if self.src is None:
            self.win.status_msg(_t("先按住 Alt 单击，设置取样点"))
            return False
        if self.offset is None or not self.opt("clone_aligned", True):
            self.offset = (int(round(self.src[0] - x)), int(round(self.src[1] - y)))
        return True

    def press(self, x, y, e):
        if _mods(e)[1]:
            self._begin(x, y, e)
            return
        super().press(x, y, e)

    def _stroke_kw(self) -> dict:
        kw = super()._stroke_kw()
        kw["clone_offset"] = self.offset or (0, 0)
        return kw

    def paint(self, qp):
        super().paint(qp)
        if self.src is None:
            return
        if self.offset is not None and self.cv.hover is not None and (self.stroke is not None or
                                                                       self.opt("clone_aligned", True)):
            x, y = self.cv.to_canvas(self.cv.hover)
            sx, sy = x + self.offset[0], y + self.offset[1]
        else:
            sx, sy = self.src
        c = self.cv.to_widget(sx, sy)
        qp.setPen(QPen(QColor(0, 0, 0, 200), 3))
        qp.drawLine(QPointF(c.x() - 8, c.y()), QPointF(c.x() + 8, c.y()))
        qp.drawLine(QPointF(c.x(), c.y() - 8), QPointF(c.x(), c.y() + 8))
        qp.setPen(QPen(QColor("#FFFFFF"), 1))
        qp.drawLine(QPointF(c.x() - 8, c.y()), QPointF(c.x() + 8, c.y()))
        qp.drawLine(QPointF(c.x(), c.y() - 8), QPointF(c.x(), c.y() + 8))


class BucketTool(Tool):
    id, name, shortcut, icon = "bucket", _t("油漆桶"), "K", "bucket"
    tip = _t("油漆桶工具（K）：用前景色填充颜色相近的区域")

    def options(self, bar):
        bar.spin(_t("容差"), "bucket_tol", 0, 255, 32)
        bar.check(_t("连续"), "bucket_contig", True)
        bar.check(_t("对所有图层取样"), "bucket_all", False)
        bar.spin(_t("不透明度"), "bucket_opacity", 1, 100, 100, "%")

    def press(self, x, y, e):
        l = self.doc.active
        if l is None or not l.has_pixels:
            self.win.status_msg(_t("选中一个像素图层再填充"))
            return
        if self.win._locked(l, "pixels"):
            return
        fg = self.win.fg
        with _Busy(self.win):
            ok = P.apply_bucket(self.doc, l, int(x), int(y), (fg.red(), fg.green(), fg.blue()),
                                float(self.opt("bucket_tol", 32)), bool(self.opt("bucket_contig", True)),
                                float(self.opt("bucket_opacity", 100)) / 100, bool(self.opt("bucket_all", False)))
        if not ok:
            self.win.status_msg(_t("这里不在当前图层上，或没有可填充的区域"))


class GradientTool(Tool):
    id, name, shortcut, icon = "gradient", _t("渐变"), "G", "gradient"
    tip = _t("渐变工具（G）：拖一条线，在当前图层（或选区）里画前景色 → 背景色 / 透明的渐变。常配合蒙版做渐隐")

    def __init__(self, win):
        super().__init__(win)
        self.a = self.b = None

    def options(self, bar):
        bar.combo(_t("类型"), "grad_kind", [("linear", _t("线性")), ("radial", _t("径向"))], "linear")
        bar.combo(_t("颜色"), "grad_colors", [("fgbg", _t("前景色 → 背景色")), ("fgclear", _t("前景色 → 透明"))], "fgbg")
        bar.spin(_t("不透明度"), "grad_opacity", 1, 100, 100, "%")

    def busy(self):
        return self.a is not None

    def press(self, x, y, e):
        self.a = self.b = (x, y)

    def move(self, x, y, e):
        if self.a is not None:
            if _mods(e)[0]:                              # Shift：45° 的倍数
                dx, dy = x - self.a[0], y - self.a[1]
                ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
                L = math.hypot(dx, dy)
                x, y = self.a[0] + L * math.cos(ang), self.a[1] + L * math.sin(ang)
            self.b = (x, y)

    def release(self, x, y, e):
        if self.a is None:
            return
        a, b = self.a, self.b
        self.a = self.b = None
        if math.hypot(b[0] - a[0], b[1] - a[1]) * self.cv.zoom < 4:
            return
        d = self.doc
        l = d.active
        fg, bg = self.win.fg, self.win.bg
        c0 = (fg.red(), fg.green(), fg.blue(), 255)
        c1 = (fg.red(), fg.green(), fg.blue(), 0) if self.opt("grad_colors") == "fgclear" else \
            (bg.red(), bg.green(), bg.blue(), 255)
        if d.quick_mask:
            self._qmask_gradient(a, b, c0, c1)
            return
        if l is not None and d.mask_edit and l.mask is not None:
            self._mask_gradient(l, a, b, c0, c1)
            return
        if self.win._locked(l, "pixels"):
            return
        if l is None or not l.has_pixels:
            self.win.status_msg(_t("选中一个像素图层（或图层蒙版）再画渐变"))
            return
        with _Busy(self.win):
            P.apply_gradient(d, l, a, b, c0, c1, self.opt("grad_kind") == "radial",
                             float(self.opt("grad_opacity", 100)) / 100)

    def _qmask_gradient(self, a, b, c0, c1):
        d = self.doc
        g = P.gradient(d.width, d.height, a, b, c0, c1, self.opt("grad_kind") == "radial")
        gray = (g[..., 0] * 0.299 + g[..., 1] * 0.587 + g[..., 2] * 0.114) * g[..., 3] * 255
        op = float(self.opt("grad_opacity", 100)) / 100
        old = d.selection.astype(np.float32) if d.selection is not None else np.zeros((d.height, d.width), np.float32)
        new = np.clip(old * (1 - op) + gray * op + 0.5, 0, 255).astype(np.uint8)
        d.set_selection(new, _t("快速蒙版渐变"))

    def _mask_gradient(self, l, a, b, c0, c1):
        """在蒙版上画渐变：灰度 = 颜色的亮度（透明端按黑色算）。"""
        d = self.doc
        x, y, w, h = l.bounds(d)
        g = P.gradient(w, h, a, b, c0, c1, self.opt("grad_kind") == "radial", x, y)
        gray = (g[..., 0] * 0.299 + g[..., 1] * 0.587 + g[..., 2] * 0.114) * g[..., 3] * 255
        op = float(self.opt("grad_opacity", 100)) / 100
        sel = d.sel_local(l)
        k = op if sel is None else op * sel
        new = np.clip(l.mask * (1 - k) + gray * k + 0.5, 0, 255).astype(np.uint8)
        with d.edit(_t("蒙版渐变")):
            l.mask = new
            l.touch_mask()

    def paint(self, qp):
        if self.a is None:
            return
        pa, pb = self.cv.to_widget(*self.a), self.cv.to_widget(*self.b)
        qp.setPen(QPen(QColor(0, 0, 0, 180), 3))
        qp.drawLine(pa, pb)
        qp.setPen(QPen(QColor("#FFFFFF"), 1.2))
        qp.drawLine(pa, pb)
        qp.setBrush(QColor("#FFFFFF"))
        qp.drawEllipse(pa, 3.5, 3.5)
        qp.drawEllipse(pb, 3.5, 3.5)


class TextTool(Tool):
    id, name, shortcut, icon = "text", _t("文字"), "T", "text"
    tip = _t("文字工具（T）：单击放置文字；点已有的文字可以修改。文字图层随时可以改内容、字体、颜色")
    cursor = Qt.CursorShape.IBeamCursor

    def options(self, bar):
        from ...edit.text import available_fonts
        fonts = available_fonts()
        bar.combo(_t("字体"), "text_font", [(f, _t(f)) for f in fonts], fonts[0])
        bar.spin(_t("字号"), "text_size", 4, 2000, max(24, int(self.doc.height / 18) if self.doc else 96), " px")
        bar.label(_t("颜色用前景色"))

    def press(self, x, y, e):
        d = self.doc
        for l in reversed(d.layers):
            if l.kind == "text" and l.visible and l.x <= x < l.x + l.w and l.y <= y < l.y + l.h:
                d.set_active(l.id)
                self.win.edit_text(l)
                return
        self.win.edit_text(None, (int(x), int(y)))


# ---------------------------------------------------------------------------
# 减淡 / 加深 / 海绵 / 模糊 / 锐化 / 涂抹
# ---------------------------------------------------------------------------

TONE_RANGES = [("shadows", _t("阴影")), ("midtones", _t("中间调")), ("highlights", _t("高光"))]


class _RetouchTool(PaintTool):
    """修饰类画笔：只改像素（不能画在蒙版上），强度 = 选项栏里的“曝光度 / 流量 / 强度”。"""
    strength_label = _t("强度")
    strength_key = "retouch_strength"
    strength_default = 50

    def options(self, bar):
        bar.spin(_t("大小"), "brush_size", 1, 2000, 40, " px")
        bar.spin(_t("硬度"), "brush_hard", 0, 100, 50, "%")
        bar.spin(self.strength_label, self.strength_key, 1, 100, self.strength_default, "%")
        self.extra_options(bar)

    def extra_options(self, bar):
        pass

    def _target(self):
        tg = super()._target()
        if tg is None:
            return None
        l, to_mask = tg
        if to_mask or not l.has_pixels:
            self.win.status_msg(_t('{0}只能用在像素上：在图层面板里点图层缩略图（不是蒙版）', self.name))
            return None
        return l, False

    def press(self, x, y, e):
        if _mods(e)[1]:                                    # 修饰工具没有临时吸管：Alt 直接忽略
            return
        super().press(x, y, e)

    def _stroke_kw(self) -> dict:
        return {"size": self._size(), "hardness": float(self.opt("brush_hard", 50)) / 100, "opacity": 1.0,
                "strength": float(self.opt(self.strength_key, self.strength_default)) / 100}


class DodgeTool(_RetouchTool):
    id, name, shortcut, icon = "dodge", _t("减淡"), "O", "dodge"
    tip = _t("减淡工具（O）：涂抹的地方变亮。范围选“阴影 / 中间调 / 高光”只提亮那一段，常用来提亮眼睛、补暗部")
    mode = "dodge"
    strength_label, strength_key = _t("曝光度"), "dodge_exposure"

    def extra_options(self, bar):
        bar.combo(_t("范围"), "tone_range", TONE_RANGES, "midtones")

    def _stroke_kw(self) -> dict:
        kw = super()._stroke_kw()
        kw["tone_range"] = self.opt("tone_range", "midtones")
        return kw


class BurnTool(DodgeTool):
    id, name, shortcut, icon = "burn", _t("加深"), "O", "burn"
    tip = _t("加深工具（O）：涂抹的地方变暗。压暗过亮的天空边缘、加重轮廓")
    mode = "burn"
    strength_key = "burn_exposure"


class SpongeTool(_RetouchTool):
    id, name, shortcut, icon = "sponge", _t("海绵"), "O", "sponge"
    tip = _t("海绵工具（O）：涂抹的地方去色（降低饱和度）或加色（提高饱和度）")
    mode = "sponge"
    strength_label, strength_key = _t("流量"), "sponge_flow"

    def extra_options(self, bar):
        bar.combo(_t("模式"), "sponge_mode", [("desat", _t("去色")), ("sat", _t("加色"))], "desat")

    def _stroke_kw(self) -> dict:
        kw = super()._stroke_kw()
        kw["sat_up"] = self.opt("sponge_mode", "desat") == "sat"
        return kw


class BlurTool(_RetouchTool):
    id, name, shortcut, icon = "blur", P.BLUR, "R", "blur"
    tip = _t("模糊工具（R）：涂抹的地方变柔和（磨皮、虚化背景的一小块）")
    mode = "blur"


class SharpenTool(_RetouchTool):
    id, name, shortcut, icon = "sharpen", _t("锐化"), "R", "sharpen"
    tip = _t("锐化工具（R）：涂抹的地方更清晰（眼睛、睫毛、头发）。强度不要太高，否则会出白边")
    mode = "sharpen"
    strength_default = 35
    strength_key = "sharpen_strength"


class SmudgeTool(_RetouchTool):
    id, name, shortcut, icon = "smudge", _t("涂抹"), "R", "smudge"
    tip = _t("涂抹工具（R）：像用手指抹湿颜料一样，把起点的颜色往拖动的方向推开")
    mode = "smudge"
    strength_default = 60
    strength_key = "smudge_strength"


# ---------------------------------------------------------------------------
# 自由变换（Ctrl+T）：缩放 / 旋转 / 斜切 / 扭曲 / 透视 / 变形
# ---------------------------------------------------------------------------

TRANSFORM_MODES = [("free", _t("自由变换")), ("skew", _t("斜切")), ("distort", _t("扭曲")),
                   ("perspective", _t("透视")), ("warp", _t("变形"))]
_CORNER = {(-1, -1): 0, (1, -1): 1, (1, 1): 2, (-1, 1): 3}


def _T(dx, dy):
    return np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1]], np.float64)


class TransformTool(Tool):
    id, name, shortcut, icon = "transform", _t("自由变换"), "", "transform"
    tip = _t("自由变换（Ctrl+T）：拖角缩放（等比，按住 Shift 不等比），拖边单向缩放，框内拖动移动，框外拖动旋转（Shift 每 15°）；"
             "按住 Ctrl 拖角 = 扭曲、拖边 = 斜切。选项栏里可以切到透视 / 变形。回车应用，Esc 取消")
    cursor = Qt.CursorShape.ArrowCursor

    def __init__(self, win):
        super().__init__(win)
        self.layer = None
        self.drag = None
        self.ctrl = None
        self.H = np.eye(3)
        self.angle = 0.0

    def options(self, bar):
        bar.combo(_t("变换方式"), "xf_mode", TRANSFORM_MODES, "free", on_change=self._mode_changed)
        self.info = bar.label("")
        bar.button(_t("取消"), self.cancel)
        bar.button(_t("应用变换"), self.commit, "primary")
        self._update_info()

    def activate(self):
        self.info = None
        l = self.doc.active
        if l is None or not l.has_pixels:
            self.layer = None
            self.win.status_msg(_t("自由变换需要一个像素 / 文字图层"))
            return
        if l.locked_pos or l.locked_pixels:
            self.layer = None
            self.win._locked(l, "position" if l.locked_pos else "pixels")
            return
        self.layer = l
        self.w, self.h = l.w, l.h
        self.H = _T(l.x, l.y)
        self.ctrl = None
        self.angle = 0.0
        if self.mode == "warp":
            self.ctrl = W.grid_from_h(self.H, self.w, self.h)
        self._preview()

    def deactivate(self):
        if self.layer is not None:
            self.commit(switch=False)

    def busy(self):
        return self.drag is not None

    @property
    def mode(self) -> str:
        return self.opt("xf_mode", "free")

    def _mode_changed(self, v):
        if self.layer is None:
            return
        if v == "warp" and self.ctrl is None:
            self.ctrl = W.grid_from_h(self.H, self.w, self.h)
        elif v != "warp" and self.ctrl is not None:          # 离开变形：只保留四个角（中间的弯曲不要了）
            c = self.ctrl
            self.H = W.quad_h(self.w, self.h, [c[0, 0], c[0, 3], c[3, 3], c[3, 0]])
            self.ctrl = None
        self._preview()

    # ---- 几何 ----
    def xform(self):
        if self.ctrl is not None:
            return {"mesh": self.ctrl.copy()}
        return self.H[:2].copy() if W.is_affine(self.H) else self.H.copy()

    def matrix(self) -> np.ndarray:
        return self.H[:2].copy()

    def _pt(self, u, v, H=None):
        p = W.apply_h(self.H if H is None else H, [[u, v]])[0]
        return float(p[0]), float(p[1])

    def corners(self, H=None):
        if self.ctrl is not None and H is None:
            c = self.ctrl
            return [tuple(c[0, 0]), tuple(c[0, 3]), tuple(c[3, 3]), tuple(c[3, 0])]
        w, h = self.w, self.h
        return [self._pt(u, v, H) for u, v in ((0, 0), (w, 0), (w, h), (0, h))]

    def _preview(self):
        if self.layer is None:
            return
        self.layer.xform = self.xform()
        self.layer.version += 1
        self.doc.version += 1
        self.cv.request_render(True)
        self._update_info()

    def _update_info(self):
        if getattr(self, "info", None) is None or self.layer is None:
            return
        c = np.array(self.corners())
        sw = (np.linalg.norm(c[1] - c[0]) + np.linalg.norm(c[2] - c[3])) / 2 / max(1, self.w)
        sh = (np.linalg.norm(c[3] - c[0]) + np.linalg.norm(c[2] - c[1])) / 2 / max(1, self.h)
        ang = math.degrees(math.atan2(c[1][1] - c[0][1], c[1][0] - c[0][0]))
        self.info.setText(_t('宽 {0:.1f}%   高 {1:.1f}%   角度 {2:+.1f}°', sw * 100, sh * 100, ang))

    def _handles(self):
        w, h = self.w, self.h
        return [((hx, hy), self._pt((hx + 1) / 2 * w, (hy + 1) / 2 * h)) for hx, hy in _HANDLES]

    def _near(self, e, pt) -> bool:
        q = self.cv.to_widget(*pt)
        p = e.position()
        return abs(q.x() - p.x()) <= 9 and abs(q.y() - p.y()) <= 9

    # ---- 鼠标 ----
    def press(self, x, y, e):
        if self.layer is None:
            return
        _, _, ctrl = _mods(e)
        if self.ctrl is not None:
            hit = None
            for j in range(4):
                for i in range(4):
                    if self._near(e, self.ctrl[j, i]):
                        hit = ("pt", j, i)
            if hit is None:
                hit = ("all",)
            self.drag = (hit, x, y, self.ctrl.copy())
            return
        hit = None
        for hnd, pt in self._handles():
            if self._near(e, pt):
                hit = hnd
                break
        if hit is None:
            poly = QPolygonF([self.cv.to_widget(*p) for p in self.corners()])
            hit = "move" if poly.containsPoint(e.position(), Qt.FillRule.OddEvenFill) else "rotate"
        self.drag = (hit, x, y, self.H.copy(), self.angle, ctrl)

    def move(self, x, y, e):
        if self.drag is None:
            return
        shift = _mods(e)[0]
        if self.ctrl is not None:
            hit, x0, y0, c0 = self.drag
            d = np.array([x - x0, y - y0])
            c = c0.copy()
            if hit[0] == "pt":
                c[hit[1], hit[2]] += d
            else:
                c += d
            self.ctrl = c
            self._preview()
            return
        hit, x0, y0, H0, a0, ctrl = self.drag
        d = np.array([x - x0, y - y0])
        w, h = self.w, self.h
        mode = self.mode
        if hit == "move":
            self.H = _T(*d) @ H0
        elif hit == "rotate":
            cx, cy = self._pt(w / 2, h / 2, H0)
            da = math.degrees(math.atan2(y - cy, x - cx) - math.atan2(y0 - cy, x0 - cx))
            ang = a0 + da
            if shift:
                ang = round(ang / 15) * 15
            ang = (ang + 180) % 360 - 180
            r = math.radians(ang - a0)
            co, si = math.cos(r), math.sin(r)
            R = np.array([[co, -si, cx - co * cx + si * cy], [si, co, cy - si * cx - co * cy], [0, 0, 1]])
            self.H = R @ H0
            self.angle = ang
        else:
            hx, hy = hit
            corner = bool(hx and hy)
            quad = [np.array(p) for p in self.corners(H0)]
            if corner and (mode in ("distort", "skew") or (mode == "free" and ctrl)):
                quad[_CORNER[(hx, hy)]] = np.array([x, y])
                self.H = self._quad(quad, H0)
            elif corner and mode == "perspective":
                i = _CORNER[(hx, hy)]
                jh, jv = _CORNER[(-hx, hy)], _CORNER[(hx, -hy)]
                eh, ev = quad[jh] - quad[i], quad[jv] - quad[i]
                eh /= max(1e-9, np.linalg.norm(eh))
                ev /= max(1e-9, np.linalg.norm(ev))
                ph, pv = float(d @ eh), float(d @ ev)
                k, jj, ee = (ph, jh, eh) if abs(ph) >= abs(pv) else (pv, jv, ev)
                quad[i] = quad[i] + k * ee
                quad[jj] = quad[jj] - k * ee
                self.H = self._quad(quad, H0)
            elif not corner and (mode in ("skew", "perspective", "distort") or (mode == "free" and ctrl)):
                side = [_CORNER[(hx or s, hy or s)] for s in (-1, 1)]       # 这条边的两个角
                a, b = quad[side[0]], quad[side[1]]
                if mode == "distort":
                    mv = d
                else:                                                    # 斜切：只沿边的方向移动
                    ed = (b - a) / max(1e-9, np.linalg.norm(b - a))
                    mv = (d @ ed) * ed
                for k in side:
                    quad[k] = quad[k] + mv
                self.H = self._quad(quad, H0)
            else:                                                        # 缩放：在图层自己的坐标里算
                u, v = W.apply_h(np.linalg.inv(H0), [[x, y]])[0]
                ax, ay = (-hx + 1) / 2 * w, (-hy + 1) / 2 * h
                px, py = (hx + 1) / 2 * w, (hy + 1) / 2 * h
                sx = (u - ax) / (px - ax) if hx else 1.0
                sy = (v - ay) / (py - ay) if hy else 1.0
                if corner and not shift:
                    k = sx if abs(sx) > abs(sy) else sy
                    sx = sy = k
                sx = math.copysign(max(0.01, abs(sx)), sx)
                sy = math.copysign(max(0.01, abs(sy)), sy)
                S = _T(ax, ay) @ np.diag([sx, sy, 1.0]) @ _T(-ax, -ay)
                self.H = H0 @ S
        self._preview()

    def _quad(self, quad, H0):
        try:
            H = W.quad_h(self.w, self.h, [tuple(p) for p in quad])
            W.xform_bounds(H, self.w, self.h)                    # 角拖过了头（图层翻到“地平线”后面）：不接受
            return H
        except Exception:
            return H0

    def release(self, x, y, e):
        self.drag = None

    def key(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commit()
            return True
        if e.key() == Qt.Key.Key_Escape:
            self.cancel()
            return True
        return False

    def double_click(self, x, y, e):
        self.commit()

    def commit(self, switch: bool = True):
        l, self.layer = self.layer, None
        if l is None:
            return
        xf = self.xform()
        l.xform = None
        l.version += 1
        ident = self.ctrl is None and np.allclose(self.H, _T(l.x, l.y), atol=1e-6)
        self.ctrl = None
        if not ident:
            with _Busy(self.win, _t("正在变换…")):
                self.doc.transform_layer(l.id, xf)
        else:
            self.doc.version += 1
            self.cv.request_render()
        if switch:
            self.win.restore_tool()

    def cancel(self):
        l, self.layer = self.layer, None
        self.ctrl = None
        if l is not None:
            l.xform = None
            l.version += 1
            self.doc.version += 1
            self.cv.request_render()
        self.win.restore_tool()

    def paint(self, qp):
        if self.layer is None:
            return
        qp.setBrush(Qt.BrushStyle.NoBrush)
        if self.ctrl is not None:
            P = W.surface(self.ctrl, 24, 24)
            lines = [P[k * 8, :] for k in range(4)] + [P[:, k * 8] for k in range(4)]
            for pen in (QPen(QColor(0, 0, 0, 140), 2.6), QPen(QColor("#1EC6E6"), 1)):
                qp.setPen(pen)
                for ln in lines:
                    qp.drawPolyline(QPolygonF([self.cv.to_widget(*p) for p in ln]))
            qp.setPen(QPen(QColor(255, 255, 255, 110), 1, Qt.PenStyle.DashLine))
            for j in range(4):
                qp.drawPolyline(QPolygonF([self.cv.to_widget(*self.ctrl[j, i]) for i in range(4)]))
                qp.drawPolyline(QPolygonF([self.cv.to_widget(*self.ctrl[i, j]) for i in range(4)]))
            qp.setBrush(QColor("#FFFFFF"))
            qp.setPen(QPen(QColor("#000000"), 1))
            for j in range(4):
                for i in range(4):
                    q = self.cv.to_widget(*self.ctrl[j, i])
                    qp.drawRect(QRectF(q.x() - 3.5, q.y() - 3.5, 7, 7))
            return
        poly = QPolygonF([self.cv.to_widget(*p) for p in self.corners()])
        qp.setPen(QPen(QColor(0, 0, 0, 160), 3))
        qp.drawPolygon(poly)
        qp.setPen(QPen(QColor("#1EC6E6"), 1.2))
        qp.drawPolygon(poly)
        qp.setBrush(QColor("#FFFFFF"))
        qp.setPen(QPen(QColor("#000000"), 1))
        for _, pt in self._handles():
            q = self.cv.to_widget(*pt)
            qp.drawRect(QRectF(q.x() - 4, q.y() - 4, 8, 8))
        c = self.cv.to_widget(*self._pt(self.w / 2, self.h / 2))
        qp.drawEllipse(c, 3, 3)


TOOLS = [MoveTool, MarqueeTool, LassoTool, WandTool, ObjectTool, CropTool, EyedropperTool, HealTool, PatchTool,
         RedEyeTool, PaintTool, CloneTool, EraserTool, BucketTool, GradientTool, DodgeTool, BurnTool, SpongeTool, BlurTool, SharpenTool,
         SmudgeTool, TextTool, HandTool, ZoomTool]

# 工具栏的格子：同一格里的工具共用一个按钮（右键 / 长按换），和 Photoshop 一样按住 Shift + 快捷键在组里轮换
TOOL_SLOTS = [["move"], ["marquee"], ["lasso"], ["wand", "object"], ["crop"], ["eyedropper"],
              ["heal", "patch", "redeye"], ["brush"], ["clone"], ["eraser"], ["gradient", "bucket"], ["blur", "sharpen", "smudge"],
              ["dodge", "burn", "sponge"], ["text"], ["hand"], ["zoom"]]
