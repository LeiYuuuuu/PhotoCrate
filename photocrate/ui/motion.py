"""短时、可取消的 UI 动效；隐藏控件不继续刷新。"""
from weakref import WeakSet
from shiboken6 import isValid

from PySide6.QtCore import QObject, QEvent, QEasingCurve, QVariantAnimation
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt

_enabled = True
_motions = WeakSet()


def enabled():
    return _enabled


def set_enabled(value):
    global _enabled
    _enabled = bool(value)
    if not _enabled:
        for motion in list(_motions):
            if isValid(motion) and isValid(motion.widget):
                motion.finish()


class Motion(QObject):
    def __init__(self, widget, value=0.0, duration=180):
        super().__init__(widget)
        self.widget, self.value, self.target = widget, float(value), float(value)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(duration)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._changed)
        widget.installEventFilter(self)
        _motions.add(self)

    def _changed(self, value):
        self.value = float(value)
        self.widget.update()

    def to(self, value):
        self.animation.stop()
        self.target = float(value)
        if not enabled() or not self.widget.isVisible():
            self.finish()
            return
        self.animation.setStartValue(self.value)
        self.animation.setEndValue(self.target)
        self.animation.start()

    def finish(self):
        self.animation.stop()
        self._changed(self.target)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Hide:
            self.finish()
        return False


class PageVeil(QWidget):
    """旧页面截图淡出，不给实际页面添加图形特效或改变布局。"""
    def __init__(self, parent, snapshot):
        super().__init__(parent)
        self.snapshot = snapshot
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        self.fade = Motion(self, 1, 160)
        self.fade.animation.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        self.fade.to(0)

    def paintEvent(self, event):
        if self.fade.value <= 0:
            self.deleteLater()
            return
        painter = QPainter(self)
        painter.setOpacity(self.fade.value)
        painter.drawPixmap(0, 0, self.snapshot)

    def hideEvent(self, event):
        self.deleteLater()
        super().hideEvent(event)
