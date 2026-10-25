"""修图进阶第二档（v1.9.0）的界面自动化测试（无界面模式）。

用法：QT_QPA_PLATFORM=offscreen python tests/ui_editor_pro2.py <输出目录> [light|dark]
流程：打开样例照片 → 新调整（色彩平衡 / 黑白 / 渐变映射）→ 色彩范围选红色方块 → 选择并遮住 → 存储 / 载入选区
     → 快速蒙版（Q）涂掉一块 → 修补工具去掉白点 → 红眼 → 镜头校正（对话框）→ 液化（对话框里推一下）
     → 图层样式（投影 + 描边）→ 锁定 / 填充 → 保存工程再打开。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="pc_ui_pro2_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")


def build(path: str) -> str:
    import numpy as np
    from PIL import Image
    h, w = 800, 1200
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rng = np.random.default_rng(3)
    img = np.dstack([90 + 80 * xx / w, 110 + 60 * yy / h, 150 - 40 * xx / w]) + rng.normal(0, 4, (h, w, 3))
    img[300:500, 700:900] = (220, 40, 40)                 # 红色方块（色彩范围）
    img[180:196, 240:256] = (255, 255, 255)               # 白点（修补）
    img[600:630, 300:330] = (230, 25, 25)                 # “红眼”
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(path, "JPEG", quality=94)
    return path


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

    os.makedirs(out, exist_ok=True)
    src = build(os.path.join(HOME, "DSC00042.JPG"))
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)            # 没有主窗口：关掉修图窗口时不要退出
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("editor_tour_done", True)
    theme.apply(app, theme_name)
    lib = Library(s)
    ctx = AppContext(s, lib, Journal(journal_path()))
    result = {"ok": False}
    ed_ref = {}

    def log(msg):
        print("  ·", msg, flush=True)

    def shot(name, widget=None):
        app.processEvents()
        (widget or ed_ref.get("w")).grab().save(os.path.join(out, f"{theme_name}_{name}.png"))

    def modal():
        return QApplication.activeModalWidget()

    def wait_modal(sub, timeout=100):
        for _ in range(timeout):
            m = modal()
            if m is not None and sub in m.windowTitle():
                return m
            yield 100
        raise AssertionError(f"没有出现对话框：{sub}（当前 {modal() and modal().windowTitle()}）")

    def mouse(w, kind, p, mods=Qt.KeyboardModifier.NoModifier):
        t = {"press": QMouseEvent.Type.MouseButtonPress, "move": QMouseEvent.Type.MouseMove,
             "release": QMouseEvent.Type.MouseButtonRelease}[kind]
        btn = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
        btns = Qt.MouseButton.NoButton if kind == "release" else Qt.MouseButton.LeftButton
        QApplication.sendEvent(w, QMouseEvent(t, QPointF(p), QPointF(w.mapToGlobal(p)), btn, btns, mods))

    def drag(cv, pts, mods=Qt.KeyboardModifier.NoModifier):
        mouse(cv, "press", cv.to_widget(*pts[0]), mods)
        for x, y in pts[1:]:
            mouse(cv, "move", cv.to_widget(x, y), mods)
        mouse(cv, "release", cv.to_widget(*pts[-1]), mods)
        app.processEvents()

    def trigger(ew, text):
        for a in ew.findChildren(type(ew.a_undo)):
            if a.text().split("\t")[0] == text:
                a.trigger()
                app.processEvents()
                return
        raise AssertionError(f"找不到菜单项：{text}")

    def script():
        from photocrate.ui.editor.window import open_editor
        ew = open_editor(ctx, None, path=src)
        ed_ref["w"] = ew
        ew.showNormal()
        ew.resize(1560, 960)
        yield 500
        doc, cv = ew.doc, ew.canvas
        bg = doc.layers[0]

        # ---- 新调整 ----
        ew.new_adjust(_t("色彩平衡"), {"color_balance": {"on": True}}, "色彩平衡")
        yield 200
        ed = ew.props.editor
        assert ed.sections["色彩平衡"].btn.isChecked()
        sl = [w for w in ed.sections["色彩平衡"].body_w.findChildren(type(ed.sliders["exposure"]))][0]
        sl.set_value(40)
        sl.changed.emit(40.0)
        yield 200
        cbal = doc.active
        assert cbal.params["color_balance"]["midtones"][0] == 40
        shot("pro2_01_color_balance")
        doc.delete_layer(cbal.id)
        trigger(ew, _t("黑白"))
        assert doc.active.params["bw"]["on"]
        yield 200
        flat = doc.render_full()
        assert np.ptp(flat[400, 800, :3].astype(int)) <= 1
        doc.delete_layer(doc.active_id)
        log("新调整：色彩平衡（滑块）、黑白（菜单）")

        # ---- 色彩范围 ----
        def cr():
            for d in wait_modal(_t("色彩范围")):
                yield d
            m = modal()
            yield 300
            m._click(800 / 1200, 400 / 800, type("E", (), {"modifiers": lambda self: Qt.KeyboardModifier.NoModifier})())
            m.fuzz.set_value(30)
            m._update()
            yield 200
            shot("pro2_02_color_range", m)
            m.accept()
        _run_side(cr())
        ew.color_range()
        yield 300
        sel = doc.selection
        assert sel is not None and sel[400, 800] == 255 and sel[100, 100] == 0
        log("色彩范围：点一下红色方块 → 选中方块")

        # ---- 选择并遮住 ----
        def ref():
            for d in wait_modal(_t("选择并遮住")):
                yield d
            m = modal()
            m.radius.set_value(12)
            m.feather.set_value(2)
            m.view.setCurrentIndex(1)                     # 黑底：截图里一眼看出选中了什么
            m._update()
            yield 300
            shot("pro2_03_refine", m)
            m.accept()
        _run_side(ref())
        ew.select_and_mask()
        yield 300
        assert doc.selection is not None and doc.selection[400, 800] > 200
        doc.save_selection("方块")
        doc.deselect()
        doc.load_selection("方块")
        assert doc.selection is not None and doc.selection[400, 800] > 200
        log("选择并遮住（边缘半径 12、羽化 2）→ 存储选区 → 载入选区")

        # ---- 快速蒙版 ----
        ew.toggle_quick_mask()
        assert doc.quick_mask and ew.tool.id == "brush"
        ew.set_fg(type(ew.fg)("#000000"))
        drag(cv, [(720 + i * 10, 400) for i in range(10)])
        yield 200
        shot("pro2_04_quick_mask")
        assert doc.selection[400, 760] < 60 and doc.selection[320, 800] > 200
        ew.toggle_quick_mask()
        assert not doc.quick_mask
        doc.deselect()
        log("快速蒙版（Q）：用黑色画笔在选区里涂掉一条")

        # ---- 修补 / 红眼 ----
        ew.set_tool("patch")
        drag(cv, [(232, 172), (264, 172), (264, 204), (232, 204), (232, 172)])
        assert doc.selection is not None
        drag(cv, [(248, 188), (248, 230), (248, 268)])
        yield 300
        assert bg.pixels[188, 248, :3].mean() < 230, bg.pixels[188, 248]
        doc.deselect()
        ew.set_tool("redeye")
        drag(cv, [(315, 615)])
        yield 200
        assert bg.pixels[615, 315, 0] < 100, bg.pixels[615, 315]
        log("修补工具去掉白点、红眼工具")

        # ---- 镜头校正 / 液化 ----
        def lens():
            for d in wait_modal(_t("镜头校正")):
                yield d
            m = modal()
            m.params["vignette"] = 40
            m._update()
            yield 300
            m.accept()
        _run_side(lens())
        ew.run_filter("lens")
        yield 300
        assert doc.history.names()[-1] == _t("镜头校正")

        def liq():
            for d in wait_modal(_t("液化")):
                yield d
            m = modal()
            yield 300
            v = m.view
            r = v._rect()
            a = QPointF(r.left() + r.width() * 0.62, r.top() + r.height() * 0.5)
            mouse(v, "press", a)
            for i in range(1, 8):
                mouse(v, "move", a + QPointF(i * 6, 0))
            mouse(v, "release", a + QPointF(42, 0))
            yield 200
            assert m.lq.changed()
            shot("pro2_05_liquify", m)
            m._ok()
        _run_side(liq())
        ew.liquify()
        yield 400
        assert doc.history.names()[-1] == _t("液化")
        log("镜头校正（暗角）、液化（向前变形推一下）")

        # ---- 图层样式 / 锁定 / 填充 ----
        doc.set_selection(np.pad(np.full((200, 300), 255, np.uint8), ((300, 300), (450, 450))))
        nl = doc.layer_via_copy()
        doc.deselect()

        def sty():
            for d in wait_modal(_t("图层样式")):
                yield d
            m = modal()
            m.list.item(0).setCheckState(Qt.CheckState.Checked)            # 投影
            m.list.item(7).setCheckState(Qt.CheckState.Checked)            # 描边
            m._set("stroke", "color", "#FFFFFF")
            yield 300
            shot("pro2_06_style", m)
            m.accept()
        _run_side(sty())
        ew.layer_style()
        yield 300
        assert nl.style.get("drop_shadow", {}).get("on") and nl.style.get("stroke", {}).get("on")
        assert "fx" in ew.layers._rows[nl.id].sub.text()
        ew.layers.locks["lock_pos"].click()
        assert nl.lock_pos
        x0 = nl.x
        ew.set_tool("move")
        drag(cv, [(600, 400), (650, 450)])
        assert nl.x == x0
        ew.layers.fill.setValue(30)
        assert abs(nl.fill - 0.3) < 1e-6
        yield 300
        shot("pro2_07_final")
        log("图层样式（投影 + 白色描边）、锁定位置（拖不动）、填充 30%")

        from photocrate.edit import io as eio
        proj = os.path.join(HOME, "pro2.pcedit")
        eio.save_project(doc, proj)
        d2 = eio.load_project(proj)
        l2 = next(l for l in d2.layers if l.style)
        assert l2.lock_pos and abs(l2.fill - 0.3) < 1e-6 and "方块" in d2.channels
        assert np.abs(d2.render_full().astype(int) - doc.render_full().astype(int)).max() <= 1
        log("保存工程再打开：样式、锁定、填充、存储的选区都在")
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
                    shot("pro2_FAILED_modal", m)
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
            if ed_ref.get("w") is not None:
                shot("pro2_FAILED")
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
