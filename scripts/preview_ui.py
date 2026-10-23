"""隔离数据的原生预览：.venv/Scripts/python scripts/preview_ui.py [--hold]。

生成六张标有 UI TEST 的测试图片，扫描并截图；不读取用户照片或用户设置。
--hold 在截图后保留窗口供交互查看；预览里的操作只影响临时测试图片。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    os.environ["PHOTOCRATE_HOME"] = tempfile.mkdtemp(prefix="photocrate-ui-preview-")
    from PIL import Image, ImageDraw
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from photocrate.config import Settings
    from photocrate.bootstrap import bootstrap
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal, journal_path
    from photocrate.ui.context import AppContext
    from photocrate.ui.main_window import MainWindow
    from photocrate.ui import theme

    app = QApplication([])
    settings = bootstrap(Settings(), load_user_plugins=False)
    settings.set("parallel_mode", "thread")
    settings.set("ai_enabled", False)
    sample = Path(os.environ["PHOTOCRATE_HOME"]) / "demo"
    sample.mkdir()
    for i, color in enumerate(("#83A6AA", "#D4AC7B", "#6D8C81", "#8999B2", "#C8ABAD", "#94A8B1")):
        picture = Image.new("RGB", (640, 420), color)
        draw = ImageDraw.Draw(picture)
        draw.ellipse((400, 40, 480, 120), fill="#EEE6C8")
        draw.polygon([(0, 330), (160, 160), (290, 300), (470, 190), (640, 350), (640, 420), (0, 420)], fill="#344C55")
        draw.text((24, 24), f"UI TEST IMAGE / {i+1:02}", fill="white")
        picture.save(sample / f"202610{10+i}_120000.jpg")
    settings.set("source_dirs", [str(sample)])
    library = Library(settings)
    theme.apply(app, "light")
    window = MainWindow(AppContext(settings, library, Journal(journal_path())))
    window.show()
    app.processEvents()
    window.page("source")._on_done(library.scan([str(sample)], True))
    output = ROOT / "docs" / "ui-review"
    output.mkdir(exist_ok=True)
    for name in ("light", "dark"):
        theme.apply(app, name)
        window.page("settings").t_light.blockSignals(True)
        settings.set("theme", name)
        (window.page("settings").t_dark if name == "dark" else window.page("settings").t_light).setChecked(True)
        window.page("settings").t_light.blockSignals(False)
        for page_id in ("source", "partition", "settings"):
            window.resize(1480, 920)
            window.go(window.page_ids.index(page_id))
            QTest.qWait(300)
            assert window.grab().save(str(output / f"{page_id}-{name}.png"))
        window.resize(1180, 720)
        window.go(0)
        QTest.qWait(300)
        assert window.grab().save(str(output / f"source-small-{name}.png"))
    print(f"Preview screenshots: {output}")
    if "--hold" in sys.argv:
        window.resize(1480, 920)
        return app.exec()
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
