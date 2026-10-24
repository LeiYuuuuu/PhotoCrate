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

SEL_MODES = [("new", "新选区"), ("add", "添加"), ("sub", "减去"), ("and", "交叉")]


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
    id, name, shortcut, icon = "move", "移动", "V", "move"
    tip = "移动工具（V）：拖动当前图层；方向键微移 1 像素（Shift 10 像素）"
    cursor = Qt.CursorShape.SizeAllCursor

    def __init__(self, win):
        super().__init__(win)
        self.drag = None

    def options(self, bar):
        bar.check("自动选择图层", "move_auto", False)
        bar.label("勾选后点哪里就选中那里最上面的图层")

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
        if l is None or l.kind == "adjust":
            self.win.status_msg("调整图层不能移动；选中一个像素 / 文字图层再拖")
            return
        self.drag = (x, y, l.x, l.y, d.state(), l)

    def move(self, x, y, e):
        if self.drag is None:
            return
        x0, y0, lx, ly, _, l = self.drag
        nx, ny = lx + int(round(x - x0)), ly + int(round(y - y0))
        shift, _, _ = _mods(e)
        if shift:                                       # 只沿水平或垂直方向
            if abs(nx - lx) > abs(ny - ly):
                ny = ly
            else:
                nx = lx
        if (nx, ny) != (l.x, l.y):
            l.x, l.y = nx, ny
            l.version += 1
            self.doc.version += 1
            self.cv.request_render(True)

    def release(self, x, y, e):
        if self.drag is None:
            return
        _, _, lx, ly, before, l = self.drag
        self.drag = None
        if (l.x, l.y) != (lx, ly):
            self.doc.translate(l.id, 0, 0, before=before)

    def key(self, e):
        k = e.key()
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        dx, dy = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0), Qt.Key.Key_Up: (0, -step),
                  Qt.Key.Key_Down: (0, step)}.get(k, (0, 0))
        if (dx or dy) and self.doc.active is not None and self.doc.active.kind != "adjust":
            self.doc.translate(self.doc.active_id, dx, dy)
            return True
        return False

    def paint(self, qp):
        l = self.doc.active if self.doc is not None else None
        if l is None or l.kind == "adjust" or l.xform is not None:
            return
        a = self.cv.to_widget(l.x, l.y)
        b = self.cv.to_widget(l.x + l.w, l.y + l.h)
        r = QRectF(a, b)
        qp.setPen(_dash_pen())
        qp.drawRect(r)
        qp.setPen(_dash_pen(True))
        qp.drawRect(r)


class HandTool(Tool):
    id, name, shortcut, icon = "hand", "抓手", "H", "hand"
    tip = "抓手工具（H）：拖动画面。任何工具下按住空格也可以拖动"
    cursor = Qt.CursorShape.OpenHandCursor


class ZoomTool(Tool):
    id, name, shortcut, icon = "zoom", "缩放", "Z", "zoom"
    tip = "缩放工具（Z）：单击放大，Alt + 单击缩小。滚轮也能缩放"
    cursor = Qt.CursorShape.CrossCursor

    def press(self, x, y, e):
        _, alt, _ = _mods(e)
        self.cv.zoom_step(not alt, e.position())


# ---------------------------------------------------------------------------
# 选区工具
# ---------------------------------------------------------------------------

class MarqueeTool(Tool):
    id, name, shortcut, icon = "marquee", "选框", "M", "marquee"
    tip = "选框工具（M）：拖出矩形 / 椭圆选区；拖动中按 Shift 为正方形 / 正圆；单击空白处取消选择"

    def __init__(self, win):
        super().__init__(win)
        self.a = self.b = None
        self.mode = "new"
        self.free_shift = False

    def options(self, bar):
        bar.seg("sel_mode", SEL_MODES, "new")
        bar.combo("形状", "marquee_shape", [("rect", "矩形"), ("ellipse", "椭圆")], "rect")
        bar.spin("羽化", "feather", 0, 250, 0, " px")

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
        self._apply_selection(m, self.mode, "椭圆选框" if ell else "矩形选框")

    def paint(self, qp):
        if self.a is None:
            return
        r = QRectF(self.cv.to_widget(*self.a), self.cv.to_widget(*self.b)).normalized()
        ell = self.opt("marquee_shape", "rect") == "ellipse"
        for pen in (_dash_pen(), _dash_pen(True)):
            qp.setPen(pen)
            qp.drawEllipse(r) if ell else qp.drawRect(r)


class LassoTool(Tool):
    id, name, shortcut, icon = "lasso", "套索", "L", "lasso"
    tip = "套索工具（L）：按住拖动圈出选区；多边形模式下逐点单击，双击或回车闭合，Esc 取消，Backspace 撤一个点"

    def __init__(self, win):
        super().__init__(win)
        self.pts: list[tuple[float, float]] = []
        self.mode = "new"
        self.cur = None

    def options(self, bar):
        bar.seg("sel_mode", SEL_MODES, "new")
        bar.combo("方式", "lasso_kind", [("free", "自由"), ("poly", "多边形")], "free")
        bar.spin("羽化", "feather", 0, 250, 0, " px")

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
        self._apply_selection(m, self.mode, "多边形套索" if self._poly() else "套索")

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
    id, name, shortcut, icon = "wand", "魔棒", "W", "wand"
    tip = "魔棒工具（W）：单击选中颜色相近的区域。容差越大选得越多；取消“连续”会选中全图所有相近颜色"

    def options(self, bar):
        bar.seg("sel_mode", SEL_MODES, "new")
        bar.spin("容差", "wand_tol", 0, 255, 32)
        bar.check("连续", "wand_contig", True)
        bar.check("对所有图层取样", "wand_all", False)

    def press(self, x, y, e):
        d = self.doc
        if not (0 <= x < d.width and 0 <= y < d.height):
            return
        mode = self._sel_mode(e)
        with _Busy(self.win, "魔棒…"):
            src = d.flat_rgba() if self.opt("wand_all", False) or d.active is None else d.sample_layer_rgba(d.active)
            m = P.wand(src, int(x), int(y), float(self.opt("wand_tol", 32)), bool(self.opt("wand_contig", True)))
        self._apply_selection(m, mode, "魔棒")
        self.win.status_msg("")


class ObjectTool(Tool):
    id, name, shortcut, icon = "object", "对象选择", "Q", "object"
    tip = "对象选择工具（Q）：框住一个物体 / 人，自动沿边缘选出来（GrabCut，几秒钟）。之后可以用 Shift / Alt 补选或减选"

    def __init__(self, win):
        super().__init__(win)
        self.a = self.b = None
        self.mode = "new"

    def options(self, bar):
        bar.seg("sel_mode", SEL_MODES, "new")
        bar.label("框选时留一点余量，主体边缘不要被框线切到")

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
        with _Busy(self.win, "正在识别主体…"):
            m = P.object_select_doc(self.doc, (a[0], a[1], b[0], b[1]))
        if not m.any():
            self.win.status_msg("没有识别出主体：框得再贴近一些试试")
            return
        self._apply_selection(m, self.mode, "对象选择")
        self.win.status_msg("已选出主体：可以 Ctrl+J 抠出来，或新建调整图层只调它")

    def paint(self, qp):
        if self.a is None:
            return
        r = QRectF(self.cv.to_widget(*self.a), self.cv.to_widget(*self.b)).normalized()
        qp.setPen(QPen(QColor("#1EC6E6"), 1.5))
        qp.drawRect(r)


# ---------------------------------------------------------------------------
# 裁剪（含拉直）
# ---------------------------------------------------------------------------

RATIOS = [("free", "自由"), ("orig", "原始比例"), ("1:1", "1 : 1"), ("4:3", "4 : 3"), ("3:2", "3 : 2"),
          ("16:9", "16 : 9"), ("5:4", "5 : 4"), ("3:4", "3 : 4"), ("2:3", "2 : 3"), ("9:16", "9 : 16"),
          ("4:5", "4 : 5")]
_HANDLES = [(-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0)]


class CropTool(Tool):
    id, name, shortcut, icon = "crop", "裁剪", "C", "crop"
    tip = ("裁剪工具（C）：拖动边框或角调整，框内拖动移动；在框外拖出新的裁剪框；"
           "按住 Ctrl 沿地平线拖一条线自动拉直。回车或双击应用，Esc 复位")

    def __init__(self, win):
        super().__init__(win)
        self.box = (0.0, 0.0, 1.0, 1.0)       # 中心 x, y, 宽, 高（未旋转时的尺寸）
        self.base = self.box
        self.angle = 0.0
        self.drag = None
        self.line = None

    def options(self, bar):
        bar.combo("比例", "crop_ratio", RATIOS, "free", on_change=lambda _: self._ratio_changed())
        bar.spin("拉直", "crop_angle", -45, 45, 0, "°", decimals=1, on_change=self._angle_changed)
        bar.button("复位", self.reset)
        bar.button("应用裁剪", self.apply, "primary")

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
            self.win.status_msg("裁剪框和画布一样大，没有需要裁掉的")
            return
        with _Busy(self.win, "正在裁剪…"):
            d.crop(cx - w / 2, cy - h / 2, w, h, self.angle)
        self.win.status_msg(f"已裁剪为 {d.width} × {d.height}")
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
    id, name, shortcut, icon = "eyedropper", "吸管", "I", "eyedropper"
    tip = "吸管工具（I）：单击取前景色，Alt + 单击取背景色。用画笔时按住 Alt 也能临时吸色"

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
    id, name, shortcut, icon = "brush", "画笔", "B", "brush"
    tip = "画笔工具（B）：用前景色画。[ ] 调大小，Shift + 单击画直线，Alt 临时吸色。选中蒙版时黑色隐藏、白色显示"
    mode = "paint"

    def __init__(self, win):
        super().__init__(win)
        self.stroke = None
        self.last_end = None
        self.pos = None

    def options(self, bar):
        bar.spin("大小", "brush_size", 1, 2000, 40, " px")
        bar.spin("硬度", "brush_hard", 0, 100, 80, "%")
        bar.spin("不透明度", "brush_opacity", 1, 100, 100, "%")

    def busy(self):
        return self.stroke is not None

    def deactivate(self):
        if self.stroke is not None:
            self.stroke.cancel()
            self.stroke = None
            self.cv.request_render()

    def _target(self):
        d = self.doc
        l = d.active
        if l is None:
            return None
        to_mask = d.mask_edit and l.mask is not None
        if not to_mask and l.kind == "adjust":
            self.win.status_msg("调整图层没有像素：先给它加蒙版，再在蒙版上画（黑色隐藏、白色显示）")
            return None
        if not l.visible:
            self.win.status_msg("当前图层是隐藏的，先点眼睛图标显示它")
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
        self.stroke = P.Stroke(self.doc, l, self.mode, to_mask=to_mask, **self._stroke_kw())
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
        with _Busy(self.win, "正在修复…" if self.mode == "heal" else ""):
            r = s.finish()
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
            ox, oy = (0, 0) if l.kind == "adjust" else (l.x, l.y)
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
    id, name, shortcut, icon = "eraser", "橡皮擦", "E", "eraser"
    tip = "橡皮擦工具（E）：把当前图层擦成透明（在蒙版上擦 = 涂黑隐藏）"
    mode = "erase"


class HealTool(PaintTool):
    id, name, shortcut, icon = "heal", "污点修复画笔", "J", "heal"
    tip = "污点修复画笔（J）：涂抹痘痘、灰尘、电线等，松开后用周围的画面自动补上。大面积去除可以先选区再用「编辑 → 内容识别填充」"
    mode = "heal"

    def options(self, bar):
        bar.spin("大小", "heal_size", 2, 1000, 30, " px")
        bar.label("涂抹要去掉的地方，比瑕疵稍大一点；一次修不干净可以再涂一遍")

    def _size(self) -> float:
        return float(self.opt("heal_size", 30))

    def _stroke_kw(self) -> dict:
        return {"size": self._size(), "hardness": 0.5, "opacity": 1.0}

    def _target(self):
        tg = super()._target()
        if tg is not None and tg[1]:
            self.win.status_msg("修复画笔只能用在像素上：先在图层面板里点图层缩略图（不是蒙版）")
            return None
        return tg


class CloneTool(PaintTool):
    id, name, shortcut, icon = "clone", "仿制图章", "S", "clone"
    tip = "仿制图章（S）：先按住 Alt 单击要复制的地方（取样点），再在别处涂抹，把取样点的画面画过来"
    mode = "clone"

    def __init__(self, win):
        super().__init__(win)
        self.src = None
        self.offset = None

    def options(self, bar):
        super().options(bar)
        bar.check("对齐", "clone_aligned", True)

    def _begin(self, x, y, e):
        if _mods(e)[1]:
            self.src = (x, y)
            self.offset = None
            self.win.status_msg(f"取样点：{int(x)}, {int(y)}。在要修补的地方涂抹")
            return False
        if self.src is None:
            self.win.status_msg("先按住 Alt 单击，设置取样点")
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
    id, name, shortcut, icon = "bucket", "油漆桶", "K", "bucket"
    tip = "油漆桶工具（K）：用前景色填充颜色相近的区域"

    def options(self, bar):
        bar.spin("容差", "bucket_tol", 0, 255, 32)
        bar.check("连续", "bucket_contig", True)
        bar.check("对所有图层取样", "bucket_all", False)
        bar.spin("不透明度", "bucket_opacity", 1, 100, 100, "%")

    def press(self, x, y, e):
        l = self.doc.active
        if l is None or not l.has_pixels:
            self.win.status_msg("选中一个像素图层再填充")
            return
        fg = self.win.fg
        with _Busy(self.win):
            ok = P.apply_bucket(self.doc, l, int(x), int(y), (fg.red(), fg.green(), fg.blue()),
                                float(self.opt("bucket_tol", 32)), bool(self.opt("bucket_contig", True)),
                                float(self.opt("bucket_opacity", 100)) / 100, bool(self.opt("bucket_all", False)))
        if not ok:
            self.win.status_msg("这里不在当前图层上，或没有可填充的区域")


class GradientTool(Tool):
    id, name, shortcut, icon = "gradient", "渐变", "G", "gradient"
    tip = "渐变工具（G）：拖一条线，在当前图层（或选区）里画前景色 → 背景色 / 透明的渐变。常配合蒙版做渐隐"

    def __init__(self, win):
        super().__init__(win)
        self.a = self.b = None

    def options(self, bar):
        bar.combo("类型", "grad_kind", [("linear", "线性"), ("radial", "径向")], "linear")
        bar.combo("颜色", "grad_colors", [("fgbg", "前景色 → 背景色"), ("fgclear", "前景色 → 透明")], "fgbg")
        bar.spin("不透明度", "grad_opacity", 1, 100, 100, "%")

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
        if l is not None and d.mask_edit and l.mask is not None:
            self._mask_gradient(l, a, b, c0, c1)
            return
        if l is None or not l.has_pixels:
            self.win.status_msg("选中一个像素图层（或图层蒙版）再画渐变")
            return
        with _Busy(self.win):
            P.apply_gradient(d, l, a, b, c0, c1, self.opt("grad_kind") == "radial",
                             float(self.opt("grad_opacity", 100)) / 100)

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
        with d.edit("蒙版渐变"):
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
    id, name, shortcut, icon = "text", "文字", "T", "text"
    tip = "文字工具（T）：单击放置文字；点已有的文字可以修改。文字图层随时可以改内容、字体、颜色"
    cursor = Qt.CursorShape.IBeamCursor

    def options(self, bar):
        from ...edit.text import available_fonts
        fonts = available_fonts()
        bar.combo("字体", "text_font", [(f, f) for f in fonts], fonts[0])
        bar.spin("字号", "text_size", 4, 2000, max(24, int(self.doc.height / 18) if self.doc else 96), " px")
        bar.label("颜色用前景色")

    def press(self, x, y, e):
        d = self.doc
        for l in reversed(d.layers):
            if l.kind == "text" and l.visible and l.x <= x < l.x + l.w and l.y <= y < l.y + l.h:
                d.set_active(l.id)
                self.win.edit_text(l)
                return
        self.win.edit_text(None, (int(x), int(y)))


# ---------------------------------------------------------------------------
# 自由变换（Ctrl+T）
# ---------------------------------------------------------------------------

class TransformTool(Tool):
    id, name, shortcut, icon = "transform", "自由变换", "", "transform"
    tip = "自由变换（Ctrl+T）：拖角缩放（等比，按住 Shift 不等比），拖边单向缩放，框内拖动移动，框外拖动旋转（Shift 每 15°）。回车应用，Esc 取消"
    cursor = Qt.CursorShape.ArrowCursor

    def __init__(self, win):
        super().__init__(win)
        self.layer = None
        self.drag = None

    def options(self, bar):
        self.info = bar.label("")
        bar.button("取消", self.cancel)
        bar.button("应用变换", self.commit, "primary")
        self._update_info()

    def activate(self):
        self.info = None
        l = self.doc.active
        if l is None or not l.has_pixels:
            self.layer = None
            self.win.status_msg("自由变换需要一个像素 / 文字图层")
            return
        self.layer = l
        self.w, self.h = l.w, l.h
        self.c = (l.x + l.w / 2, l.y + l.h / 2)
        self.sx = self.sy = 1.0
        self.angle = 0.0
        self._preview()

    def deactivate(self):
        if self.layer is not None:
            self.commit(switch=False)

    def busy(self):
        return self.drag is not None

    def matrix(self) -> np.ndarray:
        a = math.radians(self.angle)
        c, s = math.cos(a), math.sin(a)
        A = np.array([[c * self.sx, -s * self.sy], [s * self.sx, c * self.sy]])
        t = np.array(self.c) - A @ np.array([self.w / 2, self.h / 2])
        return np.hstack([A, t[:, None]])

    def _pt(self, u, v):
        M = self.matrix()
        return M[0, 0] * u + M[0, 1] * v + M[0, 2], M[1, 0] * u + M[1, 1] * v + M[1, 2]

    def _preview(self):
        if self.layer is None:
            return
        self.layer.xform = self.matrix()
        self.layer.version += 1
        self.doc.version += 1
        self.cv.request_render(True)
        self._update_info()

    def _update_info(self):
        if getattr(self, "info", None) is not None and self.layer is not None:
            self.info.setText(f"宽 {self.sx * 100:.1f}%   高 {self.sy * 100:.1f}%   角度 {self.angle:+.1f}°")

    def _handles(self):
        w, h = self.w, self.h
        return [((hx, hy), self._pt((hx + 1) / 2 * w, (hy + 1) / 2 * h)) for hx, hy in _HANDLES]

    def press(self, x, y, e):
        if self.layer is None:
            return
        p = e.position()
        hit = None
        for hnd, (hx, hy) in self._handles():
            q = self.cv.to_widget(hx, hy)
            if abs(q.x() - p.x()) <= 9 and abs(q.y() - p.y()) <= 9:
                hit = hnd
                break
        if hit is None:
            poly = QPolygonF([self.cv.to_widget(*self._pt(u, v)) for u, v in
                              ((0, 0), (self.w, 0), (self.w, self.h), (0, self.h))])
            hit = "move" if poly.containsPoint(p, Qt.FillRule.OddEvenFill) else "rotate"
        self.drag = (hit, x, y, self.c, self.sx, self.sy, self.angle)

    def move(self, x, y, e):
        if self.drag is None:
            return
        hit, x0, y0, c0, sx0, sy0, a0 = self.drag
        shift = _mods(e)[0]
        if hit == "move":
            self.c = (c0[0] + x - x0, c0[1] + y - y0)
        elif hit == "rotate":
            da = math.degrees(math.atan2(y - c0[1], x - c0[0]) - math.atan2(y0 - c0[1], x0 - c0[0]))
            ang = a0 + da
            if shift:
                ang = round(ang / 15) * 15
            self.angle = (ang + 180) % 360 - 180
        else:
            hx, hy = hit
            a = math.radians(a0)
            c, s = math.cos(a), math.sin(a)
            W0, H0 = self.w * sx0, self.h * sy0
            ax, ay = -hx * W0 / 2, -hy * H0 / 2                      # 对边 / 对角（不动的那一点），局部坐标
            A = (c0[0] + ax * c - ay * s, c0[1] + ax * s + ay * c)
            dx, dy = x - A[0], y - A[1]
            vx, vy = dx * c + dy * s, -dx * s + dy * c
            nW = max(1.0, hx * vx) if hx else W0
            nH = max(1.0, hy * vy) if hy else H0
            if hx and hy and not shift:                            # 角：默认等比
                k = max(nW / W0, nH / H0)
                nW, nH = W0 * k, H0 * k
            self.sx, self.sy = nW / self.w, nH / self.h
            lx, ly = hx * nW / 2, hy * nH / 2
            self.c = (A[0] + lx * c - ly * s, A[1] + lx * s + ly * c)
        self._preview()

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
        M = self.matrix()
        l.xform = None
        l.version += 1
        ident = np.allclose(M, [[1, 0, l.x], [0, 1, l.y]], atol=1e-3)
        if not ident:
            with _Busy(self.win, "正在变换…"):
                self.doc.transform_layer(l.id, M)
        else:
            self.doc.version += 1
            self.cv.request_render()
        if switch:
            self.win.restore_tool()

    def cancel(self):
        l, self.layer = self.layer, None
        if l is not None:
            l.xform = None
            l.version += 1
            self.doc.version += 1
            self.cv.request_render()
        self.win.restore_tool()

    def paint(self, qp):
        if self.layer is None:
            return
        poly = QPolygonF([self.cv.to_widget(*self._pt(u, v)) for u, v in
                          ((0, 0), (self.w, 0), (self.w, self.h), (0, self.h))])
        qp.setBrush(Qt.BrushStyle.NoBrush)
        qp.setPen(QPen(QColor(0, 0, 0, 160), 3))
        qp.drawPolygon(poly)
        qp.setPen(QPen(QColor("#1EC6E6"), 1.2))
        qp.drawPolygon(poly)
        qp.setBrush(QColor("#FFFFFF"))
        qp.setPen(QPen(QColor("#000000"), 1))
        for _, (hx, hy) in self._handles():
            q = self.cv.to_widget(hx, hy)
            qp.drawRect(QRectF(q.x() - 4, q.y() - 4, 8, 8))
        c = self.cv.to_widget(*self.c)
        qp.drawEllipse(c, 3, 3)


TOOLS = [MoveTool, MarqueeTool, LassoTool, WandTool, ObjectTool, CropTool, EyedropperTool, HealTool, PaintTool,
         CloneTool, EraserTool, BucketTool, GradientTool, TextTool, HandTool, ZoomTool]
