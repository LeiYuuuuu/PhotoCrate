"""工具栏图标：全部用 QPainter 画（和软件其他地方一样不用图片素材）。坐标按 24×24 设计，绘制时缩放。"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF


def _pen(col: QColor, w: float = 1.6, dash: bool = False) -> QPen:
    p = QPen(col, w)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if dash:
        p.setDashPattern([2.0, 1.6])
    return p


def _poly(pts) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in pts])


def icon_pixmap(kind: str, col: QColor, size: int = 16):
    """画成一张小图（按钮、面板里用；有些符号字体里没有，用画的更稳）。"""
    from PySide6.QtGui import QPixmap
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    qp = QPainter(pm)
    paint_icon(qp, QRectF(0, 0, size, size), kind, col)
    qp.end()
    return pm


def paint_icon(qp: QPainter, rect: QRectF, kind: str, col: QColor) -> None:
    qp.save()
    qp.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = min(rect.width(), rect.height()) / 24.0
    qp.translate(rect.center().x() - 12 * s, rect.center().y() - 12 * s)
    qp.scale(s, s)
    qp.setPen(_pen(col))
    qp.setBrush(Qt.BrushStyle.NoBrush)
    fill = QColor(col)
    if kind == "move":
        qp.drawLine(QPointF(12, 3), QPointF(12, 21))
        qp.drawLine(QPointF(3, 12), QPointF(21, 12))
        qp.setBrush(fill)
        for a in (0, 90, 180, 270):
            r = math.radians(a)
            c, si = math.cos(r), math.sin(r)
            tip = (12 + 9.5 * c, 12 + 9.5 * si)
            b1 = (12 + 6 * c - 2.6 * si, 12 + 6 * si + 2.6 * c)
            b2 = (12 + 6 * c + 2.6 * si, 12 + 6 * si - 2.6 * c)
            qp.drawPolygon(_poly([tip, b1, b2]))
    elif kind == "marquee":
        qp.setPen(_pen(col, 1.6, dash=True))
        qp.drawRect(QRectF(4, 5, 16, 14))
    elif kind == "lasso":
        path = QPainterPath(QPointF(8, 17))
        path.cubicTo(1, 13, 4, 4, 13, 4)
        path.cubicTo(22, 4, 23, 13, 15, 15)
        path.cubicTo(11, 16, 9, 15, 8, 17)
        path.cubicTo(7, 19, 8, 21, 10, 21)
        qp.drawPath(path)
    elif kind == "wand":
        qp.setPen(_pen(col, 2.2))
        qp.drawLine(QPointF(4, 20), QPointF(14, 10))
        qp.setPen(_pen(col, 1.3))
        for a in range(0, 360, 45):
            r = math.radians(a)
            qp.drawLine(QPointF(17 + 1.6 * math.cos(r), 7 + 1.6 * math.sin(r)),
                        QPointF(17 + 4.4 * math.cos(r), 7 + 4.4 * math.sin(r)))
    elif kind == "object":
        qp.setPen(_pen(col, 1.3, dash=True))
        qp.drawRect(QRectF(3, 3, 18, 18))
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(fill)
        qp.drawEllipse(QPointF(12, 9), 3, 3)
        qp.drawRoundedRect(QRectF(7, 13, 10, 6), 3, 3)
    elif kind == "crop":
        qp.setPen(_pen(col, 2.0))
        qp.drawPolyline(_poly([(7, 2), (7, 17), (22, 17)]))
        qp.drawPolyline(_poly([(2, 7), (17, 7), (17, 22)]))
    elif kind == "eyedropper":
        qp.setPen(_pen(col, 1.8))
        qp.drawLine(QPointF(4, 20), QPointF(13, 11))
        qp.setBrush(fill)
        qp.drawPolygon(_poly([(12, 8), (16, 12), (20, 8), (16, 4)]))
    elif kind == "heal":
        qp.translate(12, 12)
        qp.rotate(-45)
        qp.drawRoundedRect(QRectF(-9, -4, 18, 8), 4, 4)
        qp.setBrush(fill)
        qp.drawRect(QRectF(-3, -4, 6, 8))
    elif kind == "brush":
        qp.setPen(_pen(col, 2.0))
        qp.drawLine(QPointF(20, 3), QPointF(10, 13))
        qp.setBrush(fill)
        path = QPainterPath(QPointF(9, 12))
        path.cubicTo(5, 12, 5, 17, 3, 21)
        path.cubicTo(8, 21, 13, 19, 12, 15)
        path.closeSubpath()
        qp.drawPath(path)
    elif kind == "clone":
        qp.setBrush(fill)
        qp.drawRect(QRectF(5, 15, 14, 5))
        qp.drawRect(QRectF(9, 10, 6, 5))
        qp.drawEllipse(QPointF(12, 6), 3.5, 3.5)
    elif kind == "eraser":
        qp.translate(12, 12)
        qp.rotate(-35)
        qp.drawRect(QRectF(-9, -4.5, 18, 9))
        qp.setBrush(fill)
        qp.drawRect(QRectF(-9, -4.5, 7, 9))
    elif kind == "bucket":
        qp.save()
        qp.translate(11, 12)
        qp.rotate(-30)
        qp.drawRect(QRectF(-6, -5, 12, 12))
        qp.drawLine(QPointF(-6, -5), QPointF(6, -5))
        qp.restore()
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(fill)
        qp.drawEllipse(QPointF(19.5, 17), 2.2, 2.8)
    elif kind == "gradient":
        for i in range(6):
            c = QColor(col)
            c.setAlphaF(0.12 + i * 0.17)
            qp.fillRect(QRectF(3 + i * 3, 6, 3, 12), c)
        qp.drawRect(QRectF(3, 6, 18, 12))
    elif kind == "text":
        qp.setPen(_pen(col, 2.4))
        qp.drawLine(QPointF(5, 5), QPointF(19, 5))
        qp.drawLine(QPointF(12, 5), QPointF(12, 20))
        qp.setPen(_pen(col, 1.6))
        qp.drawLine(QPointF(9, 20), QPointF(15, 20))
    elif kind == "hand":
        path = QPainterPath(QPointF(7, 21))
        path.lineTo(4, 13)
        path.cubicTo(3, 11, 6, 10, 7, 12)
        path.lineTo(8, 14)
        path.lineTo(8, 5)
        path.cubicTo(8, 3, 11, 3, 11, 5)
        path.lineTo(11, 11)
        path.lineTo(11, 4)
        path.cubicTo(11, 2, 14, 2, 14, 4)
        path.lineTo(14, 11)
        path.lineTo(14, 5)
        path.cubicTo(14, 3, 17, 3, 17, 5)
        path.lineTo(17, 12)
        path.lineTo(17, 8)
        path.cubicTo(17, 6, 20, 6, 20, 8)
        path.lineTo(20, 15)
        path.cubicTo(20, 19, 18, 21, 15, 21)
        path.closeSubpath()
        qp.drawPath(path)
    elif kind == "zoom":
        qp.setPen(_pen(col, 1.8))
        qp.drawEllipse(QPointF(10, 10), 6, 6)
        qp.setPen(_pen(col, 2.6))
        qp.drawLine(QPointF(14.5, 14.5), QPointF(20, 20))
        qp.setPen(_pen(col, 1.5))
        qp.drawLine(QPointF(7.5, 10), QPointF(12.5, 10))
        qp.drawLine(QPointF(10, 7.5), QPointF(10, 12.5))
    elif kind == "transform":
        qp.setPen(_pen(col, 1.3, dash=True))
        qp.drawRect(QRectF(5, 5, 14, 14))
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(fill)
        for x, y in ((5, 5), (19, 5), (5, 19), (19, 19)):
            qp.drawRect(QRectF(x - 2, y - 2, 4, 4))
    elif kind == "eye":
        path = QPainterPath(QPointF(2, 12))
        path.cubicTo(7, 4, 17, 4, 22, 12)
        path.cubicTo(17, 20, 7, 20, 2, 12)
        qp.drawPath(path)
        qp.setBrush(fill)
        qp.drawEllipse(QPointF(12, 12), 3.2, 3.2)
    elif kind == "adjust":
        qp.drawEllipse(QPointF(12, 12), 8, 8)
        qp.setBrush(fill)
        path = QPainterPath(QPointF(12, 4))
        path.arcTo(QRectF(4, 4, 16, 16), 90, 180)
        path.closeSubpath()
        qp.drawPath(path)
    elif kind == "dodge":                                  # 减淡：棒棒糖形的遮挡板
        qp.drawEllipse(QPointF(14, 9), 5.5, 5.5)
        qp.setPen(_pen(col, 2.0))
        qp.drawLine(QPointF(10, 13), QPointF(4, 20))
    elif kind == "burn":                                   # 加深：捏起来的手
        path = QPainterPath(QPointF(6, 21))
        path.cubicTo(4, 15, 6, 11, 9, 10)
        path.cubicTo(10, 6, 13, 4, 16, 5)
        path.cubicTo(19, 6, 20, 9, 18, 12)
        path.cubicTo(17, 15, 15, 17, 15, 21)
        qp.drawPath(path)
        qp.drawEllipse(QPointF(14, 9), 1.8, 1.8)
    elif kind == "sponge":
        qp.drawRoundedRect(QRectF(4, 6, 16, 12), 3, 3)
        qp.setBrush(fill)
        for x, y in ((8, 10), (13, 9), (16, 13), (10, 14)):
            qp.drawEllipse(QPointF(x, y), 1.2, 1.2)
    elif kind == "blur":                                   # 模糊：水滴
        path = QPainterPath(QPointF(12, 3))
        path.cubicTo(15, 8, 19, 12, 19, 15)
        path.cubicTo(19, 19, 16, 21, 12, 21)
        path.cubicTo(8, 21, 5, 19, 5, 15)
        path.cubicTo(5, 12, 9, 8, 12, 3)
        qp.drawPath(path)
    elif kind == "sharpen":                                # 锐化：尖三角
        qp.drawPolygon(_poly([(12, 3), (19, 20), (5, 20)]))
        qp.drawLine(QPointF(12, 3), QPointF(12, 20))
    elif kind == "smudge":                                 # 涂抹：伸出的手指
        path = QPainterPath(QPointF(5, 21))
        path.lineTo(5, 13)
        path.cubicTo(5, 11, 8, 11, 8, 13)
        path.lineTo(8, 4)
        path.cubicTo(8, 2, 11, 2, 11, 4)
        path.lineTo(11, 11)
        path.cubicTo(13, 10, 15, 10, 15, 12)
        path.cubicTo(17, 11, 19, 12, 19, 14)
        path.lineTo(19, 17)
        path.cubicTo(19, 20, 17, 21, 14, 21)
        qp.drawPath(path)
    elif kind == "folder":
        path = QPainterPath(QPointF(3, 7))
        path.lineTo(3, 19)
        path.lineTo(21, 19)
        path.lineTo(21, 8)
        path.lineTo(11, 8)
        path.lineTo(9, 5)
        path.lineTo(3, 5)
        path.closeSubpath()
        qp.drawPath(path)
    elif kind == "clip":                                   # 剪贴蒙版：向下的折角箭头
        qp.setPen(_pen(col, 1.8))
        qp.drawPolyline(_poly([(6, 4), (6, 16), (18, 16)]))
        qp.drawPolyline(_poly([(14, 12), (18, 16), (14, 20)]))
    qp.restore()
