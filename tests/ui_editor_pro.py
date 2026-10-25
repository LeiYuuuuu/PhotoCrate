"""修图进阶功能（v1.8.0）的界面自动化测试（无界面模式）。

用法：QT_QPA_PLATFORM=offscreen python tests/ui_editor_pro.py <输出目录> [light|dark]
流程：生成一张 DNG（RAW）→ 扫描 → 挑片页「修图…」→ RAW 显影对话框（吸管点灰色定白平衡）→ 16 位文档
     → 减淡工具（O / Shift+O 轮换到加深）→ 新图层 + 填色 → Ctrl+G 编组、折叠 → 剪贴的调整图层
     → 透视（拖角）→ 变形（拖网格点）→ 16 位导出 TIFF → 转 8 位并撤销 → 保存工程再打开。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="pc_ui_pro_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")


def main(out: str, theme_name: str = "light") -> int:
    import numpy as np
    from PySide6.QtCore import QPointF, Qt, QTimer
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from photocrate import i18n
    i18n.set_language(os.environ.get("PHOTOCRATE_LANG"))
    from photocrate.i18n import t as _t
    from photocrate.bootstrap import bootstrap
    from photocrate.config import Settings
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal, journal_path
    from photocrate.ui import theme
    from photocrate.ui.context import AppContext
    from photocrate.ui.main_window import MainWindow
    from raw_fixture import make_dng, scene

    os.makedirs(out, exist_ok=True)
    lib_dir = os.path.join(HOME, "photos", "raw")
    os.makedirs(lib_dir)
    make_dng(os.path.join(lib_dir, "IMG_0042.DNG"), scene(800, 1200))
    app = QApplication(sys.argv)
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("parallel_mode", "thread")
    s.set("source_dirs", [lib_dir])
    s.set("target_dir", lib_dir)
    s.set("editor_tour_done", True)
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

    def wait_modal(sub, timeout=100):
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

    def trigger(ew, text):
        for a in ew.findChildren(type(ew.a_undo)):
            if a.text().split("\t")[0] == text:
                a.trigger()
                app.processEvents()
                return
        raise AssertionError(f"找不到菜单项：{text}")

    def script():
        win.page("source").start_scan()
        while ctx.busy or ctx._workers:
            yield 200
        yield 300
        groups = [g for g in lib.groups if g.files]
        assert len(groups) == 1 and groups[0].files[0].role == "raw", [(g.stem, [f.role for f in g.files]) for g in groups]
        win.go(win.page_ids.index("culling"))
        yield 300
        page = win.page("culling")
        page.grid.select_gids({groups[0].gid})
        page.sel = [groups[0].gid]

        # ---- RAW 显影对话框 ----
        def raw_dialog():
            for d in wait_modal(_t("RAW 显影")):
                yield d
            m = modal()
            yield 1500
            assert m._prev16 is not None and m._prev16.dtype == np.uint16
            m.pick.click()
            yield 50
            # 中性灰块在 scene 里的位置：行 h//3+10 … h//3+50，列 460–560（宽 1200 时 ×2）
            m._picked(1020 / 1200, (800 // 3 + 30) / 800)
            yield 1200
            assert m.wb.currentData() == "custom" and m.opts.wb_mul
            g = m._prev16[(800 // 3 + 10) * m._prev16.shape[0] // 800:(800 // 3 + 50) * m._prev16.shape[0] // 800,
                          930 * m._prev16.shape[1] // 1200:1110 * m._prev16.shape[1] // 1200].reshape(-1, 3).mean(0)
            assert np.ptp(g) / g.mean() < 0.05, g
            m.exposure.set_value(0.5)
            m._update()
            yield 300
            shot("pro_01_raw", m)
            m._ok()
        _run_side(raw_dialog())
        page.b_edit.click()
        yield 800
        from photocrate.ui.editor.window import _open
        for _ in range(100):
            if _open:
                break
            yield 100
        assert _open, "修图窗口没有打开"
        ew = next(iter(_open))
        ed_ref["w"] = ew
        ew.showNormal()
        ew.resize(1560, 960)
        yield 400
        doc, cv = ew.doc, ew.canvas
        assert doc.depth == 16 and doc.layers[0].pixels.dtype == np.uint16 and (doc.width, doc.height) == (1200, 800)
        assert _t("（16 位）") in ew.windowTitle(), ew.windowTitle()
        log("RAW 显影对话框：吸管点灰色定白平衡、曝光 +0.5 → 16 位文档")

        # ---- 减淡 / 加深（工具组、快捷键轮换）----
        bg = doc.layers[0]
        before = bg.pixels.copy()
        ew.set_tool("dodge")
        assert ew.toolbar.slot_of("dodge").isChecked() and ew.toolbar.slot_of("dodge").tool.id == "dodge"
        drag(cv, [(300 + i * 10, 600) for i in range(40)])
        yield 100
        assert np.abs(bg.pixels.astype(int) - before.astype(int))[590:610, 320:680].max() > 0
        assert bg.pixels[598:602, 400:600, :3].mean() > before[598:602, 400:600, :3].mean()
        shot("pro_02_dodge")
        ew._key_tool(["dodge", "burn", "sponge"], True)
        assert ew.tool.id == "burn" and ew.toolbar.slot_of("burn").tool.id == "burn"
        ew._key_tool(["dodge", "burn", "sponge"], False)
        assert ew.tool.id == "burn"                           # 不按 Shift：还是这一格当前的工具
        ew.set_tool("blur")
        drag(cv, [(200 + i * 8, 120) for i in range(30)])
        assert doc.history.names()[-1] == _t("模糊")
        log("减淡、加深（Shift+O 轮换、工具栏一格多个工具）、模糊")

        # ---- 图层组 / 剪贴蒙版 ----
        doc.new_pixel_layer()
        red = doc.active
        assert red.pixels.dtype == np.uint16
        doc.set_selection(np.pad(np.full((240, 360), 255, np.uint8), ((280, 280), (420, 420))))
        doc.fill(red.id, (230, 40, 40, 255))
        doc.deselect()
        trigger(ew, _t("图层编组"))
        g = doc.active
        assert g.kind == "group" and red.parent == g.id
        ew.layers.toggle_collapse(g.id)
        yield 100
        assert red.id not in ew.layers._ids and g.id in ew.layers._ids
        ew.layers.toggle_collapse(g.id)
        doc.set_active(red.id)
        ew.new_adjust("黑白", {"saturation": -100})
        adj = doc.active
        assert adj.parent == g.id
        trigger(ew, _t("创建 / 释放剪贴蒙版"))
        assert adj.clip
        yield 300
        flat = doc.render_full(np.uint8)
        c_in, c_out = flat[400, 600, :3].astype(int), flat[300, 180, :3].astype(int)   # 红块里 / 场景里的红色色块
        assert np.ptp(c_in) <= 2 and np.ptp(c_out) > 10, (c_in, c_out)
        shot("pro_03_groups")
        log("新建图层（16 位）→ Ctrl+G 编组、折叠 / 展开 → 剪贴的黑白调整图层只作用于红块")

        # ---- 透视 / 变形 ----
        doc.set_active(red.id)
        ew.transform_mode("perspective")
        t = ew.tool
        assert t.id == "transform" and t.layer is red and t.mode == "perspective"
        x0, y0 = red.x, red.y                                 # 新图层和画布一样大：拖左上角
        drag(cv, [(x0, y0), (x0 + 30, y0), (x0 + 60, y0)])
        yield 200
        c = np.array(t.corners())
        assert c[0][0] > x0 + 40 and c[1][0] < x0 + red.w - 40 and abs(c[0][1] - y0) < 1, c
        shot("pro_04_perspective")
        ew.transform_mode("warp")
        assert t.ctrl is not None
        p = t.ctrl[1, 1].copy()
        drag(cv, [tuple(p), tuple(p + (20, 20)), tuple(p + (40, 50))])
        yield 200
        assert np.allclose(t.ctrl[1, 1], p + (40, 50))
        shot("pro_05_warp")
        t.commit()
        yield 300
        assert doc.history.names()[-1] == _t("变形") and ew.tool.id != "transform"
        assert red.pixels.dtype == np.uint16
        log("透视（拖角，两角对称收拢）→ 切到变形（拖网格点）→ 应用")

        # ---- 16 位导出 / 转 8 位 / 工程 ----
        from photocrate.edit import io as eio
        tif = os.path.join(HOME, "out16.tif")
        eio.export_image(doc, tif)
        back = eio.decode16(open(tif, "rb").read())
        assert back is not None and back.shape[:2] == (800, 1200)
        trigger(ew, _t("8 位/通道")) if False else None
        ew.set_depth(16)                                      # 已经是 16 位：什么都不做
        doc.convert_depth(8)
        assert doc.depth == 8 and _t("（16 位）") not in ew.windowTitle()
        ew.undo()
        assert doc.depth == 16 and _t("（16 位）") in ew.windowTitle()
        proj = os.path.join(HOME, "pro.pcedit")
        eio.save_project(doc, proj)
        d2 = eio.load_project(proj)
        assert d2.depth == 16 and any(l.kind == "group" for l in d2.layers) and any(l.clip for l in d2.layers)
        assert np.abs(d2.render_full(np.uint8).astype(int) - doc.render_full(np.uint8).astype(int)).max() <= 1
        log("16 位 TIFF 导出、转 8 位 + 撤销、工程保存再打开（组、剪贴、16 位都还在）")
        shot("pro_06_final")
        doc.modified = False
        ew.close()
        yield 300
        result["ok"] = True

    side: list = []

    def _run_side(gen):
        def step():
            try:
                d = next(gen)
            except StopIteration:
                return
            except Exception:
                traceback.print_exc()
                m = modal()
                if m is not None:
                    shot("pro_FAILED_modal", m)
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
            shot("pro_FAILED")
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
