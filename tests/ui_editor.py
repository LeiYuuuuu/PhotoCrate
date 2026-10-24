"""修图窗口的界面自动化测试（无界面模式），从「挑片」页打开修图，走一遍主要功能并截图。

用法：QT_QPA_PLATFORM=offscreen python tests/ui_editor.py <输出目录> [light|dark]
流程：生成样例（带一个白色污点和一个红色方块的照片）→ 扫描 → 挑片页「修图…」→ 新建图层画一笔并撤销 / 重做
     → 调整图层拖滑块（连续拖动只算一步）+ 预设 → 选框 → 蒙版 → 污点修复 → 对象选择 → Ctrl+J + 自由变换
     → 文字 → 高斯模糊（对话框）→ 裁剪 1:1 → 导出（保留 EXIF、重新扫描进照片库）→ 保存工程（作为附属文件跟着照片）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="pc_ui_edit_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")


def build_samples(root: str) -> str:
    import numpy as np
    from PIL import Image
    os.makedirs(root, exist_ok=True)
    h, w = 800, 1200
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rng = np.random.default_rng(3)
    img = np.dstack([90 + 80 * xx / w, 110 + 60 * yy / h, 150 - 40 * xx / w]) + rng.normal(0, 4, (h, w, 3))
    img[300:500, 700:900] = (220, 40, 40)                 # 红色方块（对象选择）
    img[180:196, 240:256] = (255, 255, 255)               # 白色污点（修复画笔）
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    ex = Image.Exif()
    ex[0x010F], ex[0x0110] = "SONY", "ILCE-7M4"
    ex.get_ifd(0x8769)[0x9003] = "2026:04:02 10:00:00"
    p = os.path.join(root, "DSC00001.JPG")
    im.save(p, "JPEG", quality=94, exif=ex.tobytes())
    return root


def main(out: str, theme_name: str = "light") -> int:
    import numpy as np
    from PySide6.QtCore import QPointF, Qt, QTimer
    from PySide6.QtGui import QColor, QMouseEvent
    from PySide6.QtWidgets import QApplication

    from photocrate import i18n
    i18n.set_language(os.environ.get("PHOTOCRATE_LANG"))   # PHOTOCRATE_LANG=en 时用英文界面跑一遍
    from photocrate.i18n import t as _t
    from photocrate.bootstrap import bootstrap
    from photocrate.config import Settings
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal, journal_path
    from photocrate.ui import theme
    from photocrate.ui.context import AppContext
    from photocrate.ui.main_window import MainWindow

    os.makedirs(out, exist_ok=True)
    lib_dir = build_samples(os.path.join(HOME, "photos", "修图测试"))
    app = QApplication(sys.argv)
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("parallel_mode", "thread")
    s.set("source_dirs", [lib_dir])
    s.set("target_dir", lib_dir)
    s.set("editor_tour_done", False)
    theme.apply(app, theme_name)
    lib = Library(s)
    ctx = AppContext(s, lib, Journal(journal_path()))
    win = MainWindow(ctx)
    win.resize(1600, 980)
    win.show()
    result = {"ok": False}
    ed_ref = {}

    def log(msg):
        print("  ·", msg, flush=True)

    def shot(name, widget=None):
        app.processEvents()
        (widget or ed_ref.get("w") or win).grab().save(os.path.join(out, f"{theme_name}_{name}.png"))

    def modal():
        return QApplication.activeModalWidget()

    def wait_modal(sub, timeout=60):
        for _ in range(timeout):
            m = modal()
            if m is not None and sub in m.windowTitle():
                return m
            yield 100
        raise AssertionError(f"没有出现对话框：{sub}（当前 {modal() and modal().windowTitle()}）")

    def mouse(cv, kind, x, y, mods=Qt.KeyboardModifier.NoModifier):
        p = cv.to_widget(x, y)
        t = {"press": QMouseEvent.Type.MouseButtonPress, "move": QMouseEvent.Type.MouseMove,
             "release": QMouseEvent.Type.MouseButtonRelease}[kind]
        btn = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
        btns = Qt.MouseButton.NoButton if kind == "release" else Qt.MouseButton.LeftButton
        ev = QMouseEvent(t, QPointF(p), QPointF(cv.mapToGlobal(p)), btn, btns, mods)
        QApplication.sendEvent(cv, ev)

    def drag(cv, pts, mods=Qt.KeyboardModifier.NoModifier):
        mouse(cv, "press", *pts[0], mods)
        for x, y in pts[1:]:
            mouse(cv, "move", x, y, mods)
        mouse(cv, "release", *pts[-1], mods)
        app.processEvents()

    def script():
        win.page("source").start_scan()
        while ctx.busy or ctx._workers:
            yield 200
        yield 300
        groups = [g for g in lib.groups if g.files]
        assert len(groups) == 1, len(groups)
        win.go(win.page_ids.index("culling"))
        yield 300
        page = win.page("culling")
        page.grid.select_gids({groups[0].gid})
        page.sel = [groups[0].gid]
        page.b_edit.click()
        yield 600
        from photocrate.ui.editor.window import _open
        assert _open, "修图窗口没有打开"
        ew = next(iter(_open))
        ed_ref["w"] = ew
        ew.showNormal()
        ew.resize(1560, 960)
        yield 400
        doc, cv = ew.doc, ew.canvas
        assert (doc.width, doc.height) == (1200, 800) and len(doc.layers) == 1

        # ---- 第一次打开：新手教程自动开始 ----
        from photocrate.ui.editor.tutorial import STEPS
        for _ in range(20):
            if ew._tour is not None:
                break
            yield 100
        tour = ew._tour
        assert tour is not None, "第一次打开没有自动开始新手教程"
        for i, (key, title, _) in enumerate(STEPS):
            assert tour.i == i
            if key is not None:
                r = tour.target_rect(key)
                assert r is not None and r.width() > 10 and r.height() > 10, (key, r)
                assert not tour.card.geometry().intersects(r) or key == "canvas", (key, tour.card.geometry(), r)
            shot(f"tour_{i + 1:02d}")
            tour.b_next.click()
            yield 60
        assert ew._tour is None and s.get("editor_tour_done") is True
        ew.start_tour()
        yield 100
        assert ew._tour is not None
        ew._tour.b_skip.click()
        yield 100
        assert ew._tour is None
        log(f"新手教程：{len(STEPS)} 步，自动开始一次；帮助菜单可重看、可跳过")
        bg = doc.layers[0]
        orig = bg.pixels.copy()
        shot("edit_01_open")
        log("挑片页「修图…」打开了修图窗口")

        # ---- 新建图层画一笔、撤销 / 重做 ----
        doc.new_pixel_layer()
        ew.set_tool("brush")
        ew.set_fg(QColor(30, 200, 90))
        ew.set_opt("brush_size", 40)
        drag(cv, [(100, 600), (200, 620), (300, 640), (400, 600)])
        yield 200
        lay = doc.active
        assert lay.pixels[620, 200, 3] == 255 and tuple(lay.pixels[620, 200, :3]) == (30, 200, 90)
        assert doc.history.names()[-1] == _t("画笔")
        ew.undo()
        assert lay.pixels[620, 200, 3] == 0
        ew.redo()
        assert lay.pixels[620, 200, 3] == 255
        log("画笔一笔、撤销、重做")

        # ---- 调整图层：拖滑块只记一步 ----
        ew.new_adjust(_t("曲线"), {}, "曲线")
        yield 200
        ed = ew.props.editor
        assert ed is not None and ed.sections["曲线"].body_w.isVisible()
        n0 = len(doc.history.steps)
        for v in (20, 40, 60, 80):
            ed.sliders["exposure"].slider.setValue(v)
            yield 30
        assert abs(doc.active.params["exposure"] - 0.8) < 1e-6, doc.active.params["exposure"]
        assert len(doc.history.steps) == n0 + 1 and doc.history.names()[-1] == _t("调整：{0}", _t("曝光"))
        ed.curve.changed.emit([[0, 0], [128, 150], [255, 255]])
        ed.preset.emit("胶片")
        yield 300
        assert doc.active.params["grain"] == 25
        ew.undo()
        ew.props.sync()
        assert doc.active.params["grain"] == 0
        ew.redo()
        yield 300
        shot("edit_02_adjust")
        log("调整图层：曝光（连续拖动 = 1 步）、曲线、预设「胶片」")

        # ---- 选框 → 用选区做调整图层的蒙版 ----
        ew.set_tool("marquee")
        drag(cv, [(600, 100), (900, 300), (1100, 500)])
        assert doc.selection is not None and doc.selection[300, 900] == 255 and doc.selection[50, 50] == 0
        doc.add_mask(doc.active_id, "selection")
        assert doc.active.mask is not None and doc.active.mask[300, 900] == 255
        doc.deselect()
        yield 300
        shot("edit_03_mask")
        log("选框选区 → 调整图层蒙版")

        # ---- 污点修复 ----
        doc.set_active(bg.id)
        ew.set_tool("heal")
        ew.set_opt("heal_size", 30)
        drag(cv, [(244, 184), (248, 188), (252, 192)])
        yield 200
        assert bg.pixels[188, 248, :3].mean() < 235, bg.pixels[188, 248]
        log(f"污点修复：{tuple(orig[188, 248, :3])} → {tuple(bg.pixels[188, 248, :3])}")

        # ---- 对象选择 + Ctrl+J + 自由变换 ----
        ew.set_tool("object")
        drag(cv, [(660, 260), (800, 400), (940, 540)])
        yield 200
        assert doc.selection is not None and doc.selection[400, 800] > 200 and doc.selection[280, 670] < 60
        shot("edit_04_object")
        nl = doc.layer_via_copy()
        assert nl is not None and abs(nl.w - 200) <= 6 and abs(nl.h - 200) <= 6, (nl.w, nl.h)
        doc.deselect()
        ew.set_tool("transform")
        t = ew.tool
        assert t.id == "transform" and t.layer is nl
        x1, y1 = nl.x + nl.w, nl.y + nl.h
        drag(cv, [(x1, y1), (x1 + 50, y1 + 50), (x1 + 100, y1 + 100)])
        yield 300
        shot("edit_05_transform")
        t.commit()
        yield 200
        assert nl.w > 280 and ew.tool.id != "transform", (nl.w, ew.tool.id)
        log(f"对象选择 → 通过拷贝的图层 → 自由变换放大到 {nl.w} × {nl.h}")

        # ---- 文字 ----
        def type_text():
            for d in wait_modal(_t("新建文字")):
                yield d
            m = modal()
            m.ed.text.setPlainText("京都 · 2026")
            m.ed.size.setValue(72)
            m.ed.stroke.setValue(3)
            yield 200
            shot("edit_06_text", m)
            m.accept()
        gen = type_text()
        _run_side(gen)
        ew.edit_text(None, (60, 60))
        yield 300
        tl = doc.active
        assert tl.kind == "text" and tl.params["text"] == "京都 · 2026" and tl.pixels[..., 3].max() == 255
        assert doc.history.names()[-1] == _t("新建文字图层")
        log("文字图层")

        # ---- 滤镜（对话框，预览后确定）----
        doc.set_active(bg.id)

        def accept_filter():
            for d in wait_modal(_t("高斯模糊")):
                yield d
            m = modal()
            yield 300
            assert doc.previews, "滤镜没有预览"
            m.accept()
        _run_side(accept_filter())
        before = bg.pixels.copy()
        ew.run_filter("gaussian")
        yield 300
        assert not doc.previews and np.abs(bg.pixels.astype(int) - before.astype(int)).mean() > 0.5
        log("高斯模糊（预览 → 确定）")

        # ---- 裁剪 1:1 ----
        ew.set_tool("crop")
        ew.options.widgets["crop_ratio"].setCurrentIndex(ew.options.widgets["crop_ratio"].findData("1:1"))
        yield 200
        shot("edit_07_crop")
        ew.tool.apply()
        yield 300
        assert doc.width == doc.height == 800, (doc.width, doc.height)
        ew.set_tool("move")
        log("裁剪 1:1 → 800 × 800")
        shot("edit_08_final")
        stray = [w for w in QApplication.topLevelWidgets() if w.isVisible() and w not in (win, ew)]
        assert not stray, f"多出来的顶层窗口：{[type(w).__name__ for w in stray]}"

        # ---- 导出 + 保存工程 ----
        exp = os.path.join(lib_dir, f"DSC00001{_t('_修图')}.jpg")

        def accept_export():
            for d in wait_modal(_t("导出图片")):
                yield d
            m = modal()
            assert m.path.text() == exp, m.path.text()
            assert m.in_library() and not m.rescan.isHidden()
            m._ok()
        _run_side(accept_export())
        assert ew.export()
        assert os.path.exists(exp)
        from PIL import Image
        with Image.open(exp) as im:
            assert im.size == (800, 800)
            assert im.getexif().get_ifd(0x8769).get(0x9003) == "2026:04:02 10:00:00"
            assert im.getexif().get(0x0112) == 1
        assert np.array_equal(Image.open(os.path.join(lib_dir, "DSC00001.JPG")).size, (1200, 800))
        assert ew.save_project()
        proj = os.path.join(lib_dir, "DSC00001.pcedit")
        assert os.path.exists(proj)
        yield 500
        while ctx.busy or ctx._workers:                # 导出触发的那次扫描（可能早于工程文件写完）
            yield 200
        win.page("source").start_scan()
        yield 300
        while ctx.busy or ctx._workers:
            yield 200
        yield 300
        stems = sorted(g.stem for g in lib.groups if g.files)
        assert stems == ["DSC00001", "DSC00001" + _t("_修图")], stems
        g1 = next(g for g in lib.groups if g.stem == "DSC00001")
        assert any(f.path.endswith(".pcedit") and f.role == "sidecar" for f in g1.files), [f.path for f in g1.files]
        log("导出新文件（EXIF 保留、原片不变）→ 重新扫描进照片库；工程文件作为附属文件挂在原片上")

        ew.close()
        yield 300
        assert not _open, "修图窗口没有关闭"
        result["ok"] = True

    side: list = []

    def _run_side(gen):
        """在主脚本调用模态对话框之前启动一个并行的小脚本，用来操作对话框。"""
        def step():
            try:
                d = next(gen)
            except StopIteration:
                return
            except Exception:
                traceback.print_exc()
                m = modal()
                if m is not None:
                    shot("edit_FAILED_modal", m)
                    m.reject()
                return
            QTimer.singleShot(d or 30, step)
        side.append(gen)
        QTimer.singleShot(30, step)

    steps = script()

    def pump():
        try:
            delay = next(steps)
        except StopIteration:
            app.quit()
            return
        except Exception:
            traceback.print_exc()
            shot("edit_FAILED")
            app.quit()
            return
        QTimer.singleShot(delay or 50, pump)

    QTimer.singleShot(400, pump)
    QTimer.singleShot(240_000, app.quit)
    app.exec()
    lib.close()
    print("RESULT:", "PASS" if result["ok"] else "FAIL", "· screenshots in", out)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "light"))
