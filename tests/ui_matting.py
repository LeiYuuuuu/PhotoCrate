"""修图 AI 抠图的界面自动化测试（无界面模式）。

用法：QT_QPA_PLATFORM=offscreen PHOTOCRATE_MODELS=<放模型的文件夹> python tests/ui_matting.py <输出目录> [light|dark] [照片]
流程：
  1. 先让模型文件夹为空：对象选择的选项栏有「下载 AI 模型」按钮、单击只提示；「选择主体」弹出下载询问，
     选「先用旧方法」退回 GrabCut；天空替换对话框里有「用 AI 模型找天空」按钮。
  2. 换回有模型的文件夹：对象选择单击 / 拖框 / Shift 加选；选择主体；选择人像；去除背景（加图层蒙版）；
     人像虚化；天空替换（AI）。每一步把选区 / 结果存成图片，方便人工看效果。
不给照片时用合成的图（天空 + 草地 + 一个红色圆盘），按圆盘检查选区准不准；给照片时只检查“选出了东西”。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="pc_ui_matting_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")
MODELS = os.environ.get("PHOTOCRATE_MODELS", "")
EMPTY = os.path.join(HOME, "no_models")


def build(path: str):
    import numpy as np
    from PIL import Image
    h, w = 800, 1200
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.float32)
    img[:] = np.stack([110 + 0 * xx, 160 + 0.04 * yy, 235 - 0.05 * yy], -1)
    hor = 380 + 20 * np.sin(xx / 70)
    tex = np.random.default_rng(4).random((h, w)) * 60
    g = yy > hor
    img[g] = np.stack([70 + tex, 105 + tex, 45 + tex / 2], -1)[g]
    disk = (xx - 600) ** 2 + (yy - 560) ** 2 < 150 ** 2
    img[disk] = (205, 35, 45)
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(path, "JPEG", quality=94)
    return path, disk


def main(out: str, theme_name: str = "light", photo: str | None = None) -> int:
    import numpy as np
    from PIL import Image
    from PySide6.QtCore import QPointF, Qt, QTimer
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from photocrate import i18n
    i18n.set_language(os.environ.get("PHOTOCRATE_LANG"))
    from photocrate.i18n import t as _t
    from photocrate.ai import matting
    from photocrate.bootstrap import bootstrap
    from photocrate.config import Settings
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal, journal_path
    from photocrate.ui import theme
    from photocrate.ui.context import AppContext

    os.makedirs(out, exist_ok=True)
    os.makedirs(EMPTY, exist_ok=True)
    if photo:
        src, disk = photo, None
    else:
        src, disk = build(os.path.join(HOME, "DSC00088.JPG"))
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
    times = {}

    def log(msg):
        print("  ·", msg, flush=True)

    def shot(name, widget=None):
        app.processEvents()
        (widget or ed_ref.get("w")).grab().save(os.path.join(out, f"{theme_name}_{name}.png"))

    def save_sel(name, doc, m=None):
        """选区之外压暗、染成品红，存成图片看效果。"""
        m = doc.selection if m is None else m
        rgb = doc.render_full()[..., :3].astype(np.float32)
        a = (np.zeros(rgb.shape[:2], np.float32) if m is None else m.astype(np.float32) / 255)[..., None]
        img = rgb * a + (rgb * 0.25 + np.array([180, 0, 140], np.float32) * 0.75) * (1 - a)
        im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
        im.thumbnail((1400, 1400))
        im.save(os.path.join(out, f"{theme_name}_{name}.jpg"), quality=88)

    def iou(m):
        if disk is None:
            return None
        a = m > 127
        return (a & disk).sum() / max(1, (a | disk).sum())

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

    def buttons(w):
        from photocrate.ui.widgets.deco import TacButton
        return [b for b in w.findChildren(TacButton) if b.isVisible()]

    def press_button(dlg, text):
        for b in buttons(dlg):
            if b.text() == text:
                b.click()
                return
        raise AssertionError(f"对话框里没有按钮：{text}（有 {[b.text() for b in buttons(dlg)]}）")

    def timed(name, fn):
        t = time.time()
        r = fn()
        times[name] = time.time() - t
        return r

    def script():
        from photocrate.ui.editor.window import open_editor
        ew = open_editor(ctx, None, path=src)
        ed_ref["w"] = ew
        ew.showNormal()
        ew.resize(1560, 960)
        yield 500
        doc, cv = ew.doc, ew.canvas
        W, H = doc.width, doc.height
        cx, cy = (600, 560) if disk is not None else (W * 0.5, H * 0.55)

        # ---- 1. 没有模型 ----
        os.environ["PHOTOCRATE_MODELS"] = EMPTY
        matting.unload()
        ew.set_tool("object")
        yield 100
        assert any(_t("下载 AI 模型") in b.text() for b in buttons(ew.options)), "选项栏没有下载按钮"
        shot("m01_object_no_model")
        drag(cv, [(cx, cy), (cx, cy)])
        assert doc.selection is None
        log("没有模型：对象选择的选项栏有下载按钮，单击只提示不选")

        def decline():
            for d in wait_modal(_t("下载 AI 模型")):
                yield d
            shot("m02_ask_download", modal())
            press_button(modal(), _t("先用旧方法"))
        _run_side(decline())
        timed("grabcut_subject", ew.select_subject)
        yield 200
        assert doc.selection is not None, "退回 GrabCut 后应该选出东西"
        save_sel("m03_subject_grabcut", doc)
        old_iou = iou(doc.selection)
        ew.select_subject()                       # 本窗口里不再问，直接用旧方法
        assert modal() is None
        doc.deselect()
        log(f"选择主体：不下载就退回 GrabCut（{times['grabcut_subject']:.1f} s，IoU {old_iou}）")

        def sky_no_model():
            for d in wait_modal(_t("天空替换")):
                yield d
            m = modal()
            assert any(_t("用 AI 模型找天空（更准）") == b.text() for b in buttons(m)), "天空替换里没有 AI 按钮"
            shot("m04_sky_no_model", m)
            m.reject()
        _run_side(sky_no_model())
        ew.sky_replace()
        yield 200
        log("天空替换：没有模型时有「用 AI 模型找天空」按钮")

        # ---- 2. 有模型 ----
        if not MODELS:
            log("没有设置 PHOTOCRATE_MODELS，跳过用模型的部分")
            doc.modified = False
            ew.close()
            result["ok"] = True
            return
        os.environ["PHOTOCRATE_MODELS"] = MODELS
        matting.unload()
        assert all(matting.ready(k) for k in ("sam", "subject", "portrait", "sky")), "模型文件夹里缺模型"
        ew.set_tool("wand")
        ew.set_tool("object")
        yield 100
        assert not any(_t("下载 AI 模型") in b.text() for b in buttons(ew.options))
        t = time.time()
        drag(cv, [(cx, cy), (cx, cy)])
        times["sam_click_first"] = time.time() - t
        assert doc.selection is not None, "单击没有选出东西"
        save_sel("m05_sam_click", doc)
        i1 = iou(doc.selection)
        assert i1 is None or i1 > 0.85, f"单击选得不准：IoU {i1}"
        t = time.time()
        drag(cv, [(cx + 20, cy + 10), (cx + 20, cy + 10)])
        times["sam_click_again"] = time.time() - t
        shot("m06_sam_click_canvas")
        if disk is not None:
            box = [(430, 390), (520, 470), (770, 730)]
        else:
            box = [(W * 0.2, H * 0.1), (W * 0.5, H * 0.5), (W * 0.8, H * 0.98)]
        drag(cv, box)
        save_sel("m07_sam_box", doc)
        i2 = iou(doc.selection)
        assert doc.selection is not None and (i2 is None or i2 > 0.85), f"框选不准：IoU {i2}"
        n_before = int((doc.selection > 127).sum())
        drag(cv, [(W * 0.15, H * 0.15), (W * 0.15, H * 0.15)], Qt.KeyboardModifier.ShiftModifier)
        assert int((doc.selection > 127).sum()) >= n_before, "Shift 单击应该加选"
        save_sel("m08_sam_shift_add", doc)
        log(f"对象选择（SAM）：单击 IoU {i1}，框选 IoU {i2}，Shift 加选；第一次单击 {times['sam_click_first']:.2f} s"
            f"（含编码），再点 {times['sam_click_again']:.2f} s")
        doc.deselect()

        ew._subject_old = False                 # 本窗口刚才选了“先用旧方法”：清掉这个记忆再测 AI
        timed("subject", ew.select_subject)
        assert modal() is None and doc.selection is not None
        save_sel("m09_subject_ai", doc)
        i3 = iou(doc.selection)
        # 合成图里起伏的草地本身也像个“主体”，模型会连它一起选：这里只要求圆盘完整选上
        rec = None if disk is None else ((doc.selection > 127) & disk).sum() / disk.sum()
        assert rec is None or rec > 0.95, f"选择主体没选全圆盘：{rec}"
        log(f"选择主体（BiRefNet）：圆盘选上 {rec}，IoU {i3}（旧方法 IoU {old_iou}），{times['subject']:.1f} s")
        doc.deselect()

        timed("portrait", ew.select_portrait)
        save_sel("m10_portrait", doc, doc.selection if doc.selection is not None else np.zeros((H, W), np.uint8))
        log(f"选择人像（MODNet）：{'选出了' if doc.selection is not None else '没有找到人'}，{times['portrait']:.2f} s")
        doc.deselect()

        lid = doc.active_id
        timed("remove_bg", ew.remove_background)
        l = doc.layer(lid)
        assert l.mask is not None, "去除背景应该加上图层蒙版"
        save_sel("m11_remove_bg_mask", doc, l.mask)
        shot("m11_remove_bg_canvas")
        log(f"去除背景：加了图层蒙版，{times['remove_bg']:.1f} s")
        doc.history.undo()
        assert doc.layer(lid).mask is None, "撤销后蒙版应该没了"

        if photo:
            from photocrate.edit.paint import ai_matte_doc, matte_found
            person = matte_found(ai_matte_doc(doc, "portrait"))

            def blur_ok():
                for d in wait_modal(_t("人像虚化")):
                    yield d
                shot("m12_blur_dialog", modal())
                modal().accept()
            if person:                           # 没有人时不弹半径对话框、照片不动
                _run_side(blur_ok())
            before = doc.layer(lid).pixels.copy()
            timed("portrait_blur", ew.portrait_blur)
            yield 200
            changed = not np.array_equal(before, doc.layer(lid).pixels)
            assert changed == person, f"有没有人（{person}）和照片改没改（{changed}）对不上"
            if changed:
                save_sel("m12_portrait_blur", doc, np.full((H, W), 255, np.uint8))
                doc.history.undo()
                assert np.array_equal(before, doc.layer(lid).pixels), "撤销后应该恢复原图"
            log(f"人像虚化：{'改了像素' if changed else '没有人，没改'}，{times['portrait_blur']:.1f} s，可撤销")

        def sky_ai():
            for d in wait_modal(_t("天空替换")):
                yield d
            m = modal()
            assert not any(_t("用 AI 模型找天空（更准）") == b.text() for b in buttons(m))
            shot("m13_sky_ai", m)
            m.accept()
        _run_side(sky_ai())
        timed("sky", ew.sky_replace)
        yield 300
        has_sky = any(l.name == _t("天空替换") for l in doc.layers)
        if disk is not None:
            assert has_sky, "合成图的天空应该被找到"
        save_sel("m14_sky_result", doc, np.full((H, W), 255, np.uint8))
        log(f"天空替换（AI）：{'换了天空' if has_sky else '没找到天空'}，{times['sky']:.1f} s")

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
                result["side_failed"] = True
                m = modal()
                if m is not None:
                    shot("m_FAILED_modal", m)
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
                shot("m_FAILED")
            app.quit()
            return
        QTimer.singleShot(delay or 50, pump)

    QTimer.singleShot(400, pump)
    QTimer.singleShot(300_000, app.quit)
    app.exec()
    lib.close()
    if result.get("side_failed"):                 # 处理对话框的那一路出过错，也算失败
        result["ok"] = False
    print("RESULT:", "PASS" if result["ok"] else "FAIL", "· screenshots in", out)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "light", sys.argv[3] if len(sys.argv) > 3 else None))
