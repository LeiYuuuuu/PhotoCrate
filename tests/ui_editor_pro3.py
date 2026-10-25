"""修图进阶第三档（v2.0.0）的界面自动化测试（无界面模式）。

用法：QT_QPA_PLATFORM=offscreen python tests/ui_editor_pro3.py <输出目录> [light|dark]
流程：标尺 + 从标尺拖出参考线 + 吸附 → 钢笔画路径 → 建立选区 → 矩形工具画形状图层、改颜色 → 智能对象（缩放、智能滤镜）
     → 录制动作并播放 → 校样颜色 / 色域警告 → 天空替换 → 切片 → 时间轴 → 存 PSD 再打开。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="pc_ui_pro3_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")


def build(path: str) -> str:
    import numpy as np
    from PIL import Image
    h, w = 800, 1200
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.float32)
    img[:] = np.stack([120 + 0 * xx, 170 + 0 * xx, 235 - 0.05 * yy], -1)          # 天空
    hor = 430 + 25 * np.sin(xx / 60)
    rng = np.random.default_rng(4)
    tex = rng.random((h, w)) * 70
    g = yy > hor
    img[g] = np.stack([70 + tex, 100 + tex, 45 + tex / 2], -1)[g]                    # 草地
    img[560:700, 200:400] = (0, 230, 40)                                            # 很鲜艳的绿（色域警告）
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
    src = build(os.path.join(HOME, "DSC00077.JPG"))
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
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
        raise AssertionError(f"没有出现对话框：{sub}")

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

    def script():
        from photocrate.ui.editor.window import open_editor, _open
        ew = open_editor(ctx, None, path=src)
        ed_ref["w"] = ew
        ew.showNormal()
        ew.resize(1560, 960)
        yield 500
        doc, cv = ew.doc, ew.canvas

        # ---- 标尺 / 参考线 / 吸附 ----
        ew.toggle_view("show_rulers")
        ew.set_tool("move")
        y_r = cv.RULER / 2
        x_tgt = cv.to_widget(600, 0).x()
        mouse(cv, "press", QPointF(x_tgt, y_r))
        mouse(cv, "move", QPointF(x_tgt, cv.to_widget(0, 200).y()))
        mouse(cv, "release", QPointF(x_tgt, cv.to_widget(0, 300).y()))
        app.processEvents()
        assert doc.guides and doc.guides[0][0] == "h", doc.guides
        doc.add_guide("v", 600)
        sv, ok = cv.snap_value(603, "x")
        assert ok and sv == 600
        log(f"标尺 + 从标尺拖出参考线 {doc.guides}，吸附到参考线")

        # ---- 钢笔 ----
        ew.set_tool("pen")
        t = ew.tool
        for x, y in ((300, 150), (500, 120)):
            drag(cv, [(x, y)])
        drag(cv, [(520, 300), (580, 360)])                  # 拖出曲线控制柄
        drag(cv, [(300, 150)])                               # 点回第一个点：闭合
        assert doc.work_path and doc.work_path[0]["closed"], doc.work_path
        shot("pro3_01_pen_guides")
        t.to_selection()
        assert doc.selection is not None and doc.selection[200, 420] == 255
        doc.deselect()
        t.clear()
        log("钢笔：三个点（一个拉出曲线）闭合 → 建立选区")

        # ---- 形状 ----
        ew.set_tool("rect")
        ew.set_fg(type(ew.fg)("#E5484D"))
        drag(cv, [(700, 100), (800, 160), (900, 220)])
        sh = doc.active
        assert sh.kind == "shape" and sh.params["type"] == "rect"
        ed = ew.props.editor
        ed.params["stroke"], ed.params["stroke_w"] = "#FFFFFF", 8.0
        ew.shape_edited(sh.id, "stroke")
        assert sh.params["stroke"] == "#FFFFFF"
        ew.set_tool("ellipse")
        drag(cv, [(950, 100), (1050, 200)])
        assert doc.active.params["type"] == "ellipse"
        shot("pro3_02_shapes")
        log("矩形 / 椭圆形状图层，属性面板改描边")

        # ---- 智能对象 ----
        doc.set_active(doc.layers[0].id)
        nl = doc.duplicate_layer(doc.layers[0].id)
        ew.to_smart()
        assert nl.kind == "smart"
        doc.transform_layer(nl.id, np.array([[0.5, 0, 100], [0, 0.5, 100]]))
        ew.run_filter("gaussian", {"radius": 3}, ask=False)
        yield 200
        assert nl.params["filters"] and ew.props.kind == "smart"
        shot("pro3_03_smart")
        ew.smart_filter(nl.id, 0, "toggle", False)
        assert nl.params["filters"][0][2] is False
        doc.delete_layer(nl.id)
        log("转换为智能对象 → 缩小 → 智能滤镜（高斯模糊）→ 关掉滤镜")

        # ---- 动作 ----
        ew.start_recording("测试动作")
        ew.run_filter("unsharp", {"amount": 80}, ask=False)
        ew._rot(1)
        name, steps = ew.stop_recording()
        assert name == "测试动作" and [st["op"] for st in steps] == ["filter", "rotate"]
        doc.history.undo()
        doc.history.undo()
        ew.show_actions()
        dlg = ew._actions_dlg
        dlg.acts["测试动作"] = steps
        dlg._refresh("测试动作")
        yield 200
        shot("pro3_04_actions", dlg)
        dlg._play()
        assert (doc.width, doc.height) == (800, 1200)
        doc.history.undo()
        doc.history.undo()
        dlg.close()
        log("录制动作（USM 锐化 + 旋转）→ 动作面板播放")

        # ---- 校样 / 色域警告 ----
        from photocrate.edit import color
        if any(sp == "CMYK" for _, _, sp in color.system_profiles()):
            ew.toggle_proof("gamut_on")
            yield 400
            buf = cv.display_rgba()
            assert buf is not None
            shot("pro3_05_gamut")
            ew.toggle_proof("gamut_on")
            log("色域警告（系统自带的 CMYK 配置文件）")

        # ---- 天空替换 ----
        def skyd():
            for d in wait_modal(_t("天空替换")):
                yield d
            m = modal()
            m.kind.setCurrentIndex(1)
            yield 400
            shot("pro3_06_sky", m)
            m.accept()
        _run_side(skyd())
        ew.sky_replace()
        yield 300
        assert any(l.name == _t("天空替换") for l in doc.layers)
        out_px = doc.render_full()
        log("天空替换（黄昏）")

        # ---- 切片 / 时间轴 / PSD ----
        ew.set_tool("slice")
        drag(cv, [(0, 0), (300, 200)])
        assert any(r["kind"] == "slice" for r in doc.regions)
        out_dir = os.path.join(HOME, "slices")
        os.makedirs(out_dir)
        assert len(doc.export_regions("slice", out_dir)) == 1
        ew.show_timeline()
        tl = ew._timeline
        tl._from_layers()
        assert len(doc.frames) >= 2
        yield 200
        shot("pro3_07_timeline", tl)
        tl.list.setCurrentRow(0)
        tl.close()
        from photocrate.edit import anim, psd
        anim.export(doc, os.path.join(HOME, "a.gif"), max_side=400)
        for l in doc.layers:
            l.visible = True
        doc.version += 1
        p = os.path.join(HOME, "t.psd")
        psd.write(doc, p)
        ew2 = open_editor(ctx, None, path=p)
        yield 400
        assert ew2 is not None and len(ew2.doc.layers) >= 4
        ew2.doc.modified = False
        ew2.close()
        log("切片导出、时间轴（从图层建立帧）、导出 GIF、存 PSD 再用修图打开")
        doc.modified = False
        ew.close()
        yield 300
        _ = out_px
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
                    shot("pro3_FAILED_modal", m)
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
                shot("pro3_FAILED")
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
