"""原生 Qt 交互回归，数据与缓存全部使用临时目录。"""
import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QAbstractAnimation, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from photocrate.ui import motion, theme
from photocrate.ui.widgets.deco import TacButton, SegProgress


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_motion_finishes_when_hidden_or_disabled(app):
    widget = QWidget()
    widget.show()
    QTest.qWait(20)
    motion.set_enabled(True)
    value = motion.Motion(widget, duration=200)
    value.to(1)
    QTest.qWait(30)
    assert 0 < value.value < 1
    widget.hide()
    assert value.value == 1
    assert value.animation.state() == QAbstractAnimation.State.Stopped
    widget.show()
    value.to(0)
    motion.set_enabled(False)
    assert value.value == 0
    assert value.animation.state() == QAbstractAnimation.State.Stopped
    widget.close()
    motion.set_enabled(True)


def test_button_activation_and_progress_are_not_driven_by_animation(app):
    button = TacButton("开始扫描", "primary")
    clicked = []
    button.clicked.connect(lambda: clicked.append(True))
    button.show()
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert clicked == [True]
    button.setEnabled(False)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert clicked == [True]
    progress = SegProgress()
    progress.show()
    progress.set_value(.42)
    progress.busy = True
    progress.tick()
    assert progress.value == .42
    progress.hide()
    phase = progress._phase
    progress.tick()
    assert progress._phase == phase
    button.close()
    progress.close()


@pytest.mark.parametrize("name", ["light", "dark"])
def test_tactical_button_mouse_and_keyboard_focus(app, tmp_path, monkeypatch, name):
    from PySide6.QtWidgets import QVBoxLayout
    monkeypatch.setenv("PHOTOCRATE_HOME", str(tmp_path))
    theme.apply(app, name)
    host = QWidget()
    layout = QVBoxLayout(host)
    first, button = TacButton("清除选择"), TacButton("全选")
    layout.addWidget(first)
    layout.addWidget(button)
    clicks = []
    button.clicked.connect(lambda: clicks.append(True))
    host.show()
    host.activateWindow()
    QTest.qWait(50)
    try:
        first.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(first, Qt.Key.Key_Tab)
        assert button.hasFocus() and button._focus_visible
        keyboard = button.grab().toImage()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert button.hasFocus() and not button._focus_visible
        assert clicks == [True]
        mouse = button.grab().toImage()
        assert mouse != keyboard
        QTest.keyClick(button, Qt.Key.Key_Space)
        assert button._focus_visible and clicks == [True, True]
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert not button._focus_visible and clicks == [True, True, True]
    finally:
        host.close()


@pytest.mark.parametrize("name", ["light", "dark"])
def test_native_pages_navigation_and_motion_preference(app, tmp_path, monkeypatch, name):
    monkeypatch.setenv("PHOTOCRATE_HOME", str(tmp_path))
    from photocrate.bootstrap import bootstrap
    from photocrate.config import Settings
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal
    from photocrate.ui.context import AppContext
    from photocrate.ui.main_window import MainWindow

    settings = bootstrap(Settings(tmp_path / "settings.json"), load_user_plugins=False)
    settings.set("theme", name)
    theme.apply(app, name)
    ctx = AppContext(settings, Library(settings), Journal(tmp_path / "journal.db"))
    window = MainWindow(ctx)
    try:
        window.resize(1480, 920)
        window.show()
        QTest.qWait(450)
        window.nav.setFocus()
        QTest.keyClick(window.nav, Qt.Key.Key_Down)
        assert window.stack.currentIndex() == 1
        QTest.qWait(250)
        assert not window.stack.findChildren(motion.PageVeil)
        # 快速切换后只留下最终页面，不留下旧截图遮住交互。
        window.go(2)
        window.go(4)
        QTest.qWait(250)
        assert window.stack.currentIndex() == 4
        assert not window.stack.findChildren(motion.PageVeil)
        page = window.page("settings")
        window.go(window.page_ids.index("settings"))
        QTest.qWait(200)
        page.motion_toggle.click()
        assert settings.get("ui_motion") is False
        assert Settings(tmp_path / "settings.json").get("ui_motion") is False
        window.go(0)
        assert window.nav.marker.value == 0
        assert not window.stack.findChildren(motion.PageVeil)
        for width, height in ((1480, 920), (1180, 720)):
            window.resize(width, height)
            for i in range(len(window.pages)):
                window.go(i)
                app.processEvents()
                assert window.stack.currentWidget().isVisible()
                assert window.nav.top + len(window.nav.items) * window.nav.row_h <= window.nav.height()
            window.go(0)
            app.processEvents()
            source = window.page("source")
            assert source.c_total.geometry().bottom() < source.c_date.geometry().top()
            assert source.c_date.height() == 92
        window.go(window.page_ids.index("settings"))
        app.processEvents()
        screenshot = tmp_path / f"settings-{name}.png"
        assert window.grab().save(str(screenshot))
    finally:
        window.close()
        motion.set_enabled(True)
