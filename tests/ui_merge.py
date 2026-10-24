"""合并曝光（HDR）的界面自动化测试（无界面模式）。

用法：QT_QPA_PLATFORM=offscreen python tests/ui_merge.py <输出目录> [light|dark]
流程：生成一组手持拍的包围曝光（−2 / 0 / +2 EV，带 EXIF 快门，有一个在动的物体）→ 扫描 → 选片页选中三张
     →「合并曝光…」对话框 → 后台合并 → 修图窗口打开结果（图层、参考帧、对齐、清晰度、去重影）
     → 圈一块 →「修补重影」挑一张源图补上（新图层 + 蒙版，可撤销）→ 保存工程再打开（源图层还在）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="pc_ui_merge_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")

W, H = 1500, 1000
OBJ = [(200, 520), (640, 500), (1080, 540)]          # 移动物体每张的位置（左上角）
OW, OH = 150, 110
SHIFT = [(5.4, -3.2, 0.25), (0, 0, 0), (-4.1, 6.3, -0.3)]   # 每张的手抖：平移 x, y（像素）、旋转（度）


def scene():
    """有纹理、动态范围很大的场景（线性亮度）。"""
    import cv2
    import numpy as np
    rng = np.random.default_rng(11)
    big = (H + 80, W + 80)
    base = np.zeros(big + (3,), np.float32)
    for sigma, amp in ((40, 0.5), (10, 0.25), (2.5, 0.15), (0.9, 0.08)):
        n = rng.random(big + (3,)).astype(np.float32)
        base += amp * cv2.GaussianBlur(n, (0, 0), sigma) * (1 + sigma / 10)
    base = (base - base.min()) / (base.max() - base.min())
    for _ in range(60):                                       # 一些清晰的边（细节 / 对齐用）
        x, y = rng.integers(0, big[1] - 60), rng.integers(0, big[0] - 60)
        base[y:y + rng.integers(8, 60), x:x + rng.integers(8, 60)] = rng.random(3)
    gain = np.linspace(4, 0.1, big[0], dtype=np.float32)[:, None, None]
    return (base ** 2.2) * gain


def build_samples(root: str) -> str:
    import cv2
    import numpy as np
    from PIL import Image
    os.makedirs(root, exist_ok=True)
    rad = scene()
    obj = np.random.default_rng(5).random((OH, OW, 3)).astype(np.float32) * 0.6 + 0.2
    for i, (ev, t) in enumerate(((-2, 1 / 400), (0, 1 / 100), (2, 1 / 25))):
        r = rad.copy()
        x, y = OBJ[i]
        r[y + 40:y + 40 + OH, x + 40:x + 40 + OW] = obj * 2.0
        dx, dy, a = SHIFT[i]
        M = cv2.getRotationMatrix2D(((W + 80) / 2, (H + 80) / 2), a, 1.0)
        M[:, 2] += (dx, dy)
        r = cv2.warpAffine(r, M, (W + 80, H + 80), flags=cv2.INTER_CUBIC)[40:40 + H, 40:40 + W]
        v = np.clip(r * 2.0 ** ev, 0, 1) ** (1 / 2.2)
        im = Image.fromarray(np.clip(v * 255 + 0.5, 0, 255).astype(np.uint8))
        ex = Image.Exif()
        ex[0x010F], ex[0x0110] = "SONY", "ILCE-7M4"
        sub = ex.get_ifd(0x8769)
        sub[0x9003] = f"2026:05:01 18:30:0{i}"
        sub[0x829A], sub[0x829D], sub[0x8827] = t, 8.0, 100
        im.save(os.path.join(root, f"DSC0010{i + 1}.JPG"), "JPEG", quality=95, exif=ex.tobytes())
    return root


def main(out: str, theme_name: str = "light") -> int:
    import cv2
    import numpy as np
    from PySide6.QtCore import QTimer
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

    os.makedirs(out, exist_ok=True)
    lib_dir = build_samples(os.path.join(HOME, "photos", "包围曝光"))
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
    ed_ref: dict = {}
    side = []

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

    def acutance(img, m):
        """锐度：细节（0.8–2 像素）能量 ÷ 中等尺度（2–5 像素）能量。和整体明暗、对比度无关，变糊才会变小。"""
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32)
        b1, b2, b3 = (cv2.GaussianBlur(g, (0, 0), s) for s in (0.8, 2.0, 5.0))
        return float(np.mean((b1 - b2)[m] ** 2) / np.mean((b2 - b3)[m] ** 2))

    def script():
        win.page("source").start_scan()
        while ctx.busy or ctx._workers:
            yield 200
        yield 300
        groups = sorted((g for g in lib.groups if g.files), key=lambda g: g.name)
        assert len(groups) == 3, [g.name for g in groups]
        win.go(win.page_ids.index("culling"))
        yield 300
        page = win.page("culling")
        page.grid.select_gids({g.gid for g in groups})
        page.sel = [g.gid for g in groups]
        yield 200

        # ---- 合并曝光对话框 ----
        def accept_merge():
            m = yield from wait_modal(_t("合并曝光（HDR）"))
            yield 300
            shot("merge_01_dialog", m)
            assert m.ref.count() == 4 and m.deghost.currentData() == "normal"
            assert m.align.isChecked() and m.keep.isChecked()
            m.accept()
        _run_side(accept_merge())
        page.b_merge.click()                      # 对话框 → 后台合并（进度窗口）→ 打开修图窗口
        yield 300
        from photocrate.ui.editor.window import _open
        for _ in range(100):
            if _open:
                break
            yield 200
        assert _open, "合并后没有打开修图窗口"
        ew = next(iter(_open))
        ed_ref["w"] = ew
        ew.showNormal()
        ew.resize(1560, 960)
        yield 600
        doc = ew.doc
        from photocrate.edit import hdr
        names = [l.name for l in doc.layers]
        assert len(doc.layers) == 4 and doc.layers[-1].params.get("merge_result"), names
        srcs = hdr.merge_sources(doc)
        assert len(srcs) == 3 and srcs[0].params.get("ref") and srcs[0].visible
        assert not srcs[1].visible and not srcs[2].visible
        assert "DSC00102" in srcs[0].name, srcs[0].name           # 曝光适中的那张当参考帧
        assert doc.source.suffix == "_HDR" and doc.modified
        from photocrate.edit import io as eio
        assert eio.suggest_export_path(doc).endswith("DSC00102_HDR.jpg")
        assert eio.default_project_path(doc).endswith("DSC00102_HDR.pcedit")
        log("合并完成：" + " | ".join(names))
        log("状态栏：" + ew.msg.text())
        assert ew.msg.text()
        shot("merge_02_result")

        # 对齐：对齐后的 −2 EV 那张和参考帧的物体以外区域，结构应该完全重合
        ref = srcs[0].pixels[..., :3]
        res = doc.layers[-1].pixels[..., :3]
        grey = lambda a: cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).astype(np.float32), (0, 0), 1)  # noqa: E731
        okm = np.zeros((H, W), bool)
        okm[60:-60, 60:-60] = True
        for x, y in OBJ:
            okm[y - 40:y + OH + 40, x - 40:x + OW + 40] = False
        g0 = grey(ref)
        for l in srcs[1:]:
            gi = grey(l.pixels[..., :3])
            both = okm & (g0 > 20) & (g0 < 235) & (gi > 20) & (gi < 235)
            gx0, gy0 = cv2.Sobel(g0, cv2.CV_32F, 1, 0), cv2.Sobel(g0, cv2.CV_32F, 0, 1)
            gxi, gyi = cv2.Sobel(gi, cv2.CV_32F, 1, 0), cv2.Sobel(gi, cv2.CV_32F, 0, 1)
            a = np.concatenate([gx0[both], gy0[both]])
            b = np.concatenate([gxi[both], gyi[both]])
            c = float(np.corrcoef(a, b)[0, 1])
            log(f"对齐后 {l.name} 与参考帧的梯度相关 {c:.3f}")
            assert c > 0.9, c
        # 清晰度：参考帧曝光正常的地方，合并结果不比参考帧糊
        well = okm & (g0 > 50) & (g0 < 200)
        ratio = acutance(res, well) / acutance(ref, well)
        log(f"锐度（合并结果 / 参考帧）：{ratio:.3f}")
        assert ratio > 0.95, ratio
        # 去重影：另外两张里物体所在的位置，合并结果应该和参考帧一样是背景（结构相关很高）
        for x, y in (OBJ[0], OBJ[2]):
            rg = grey(res)[y:y + OH, x:x + OW]
            fg = g0[y:y + OH, x:x + OW]
            c = float(np.corrcoef(cv2.Laplacian(rg, cv2.CV_32F).ravel(), cv2.Laplacian(fg, cv2.CV_32F).ravel())[0, 1])
            log(f"物体位置 ({x}, {y})：合并结果与参考帧的结构相关 {c:.3f}（1 = 没有重影）")
            assert c > 0.8, c

        # ---- 修补重影：圈出来，挑 −2 EV 那张补上 ----
        sel = np.zeros((H, W), np.uint8)
        sel[200:380, 300:600] = 255                     # 一块背景（没有移动物体），用来检查亮度匹配
        doc.set_selection(sel, _t("矩形选框"))
        n_layers = len(doc.layers)
        pick = next(i for i, l in enumerate(srcs) if l.params.get("ev", 0) < -1)

        def do_fix():
            m = yield from wait_modal(_t("修补重影"))
            yield 300
            assert len(m.tiles) == 3 and m.tiles[0].isChecked()
            shot("merge_03_ghostfix", m)
            m.tiles[pick].click()
            yield 100
            m.accept()
        _run_side(do_fix())
        ew.fix_ghost()
        yield 300
        assert len(doc.layers) == n_layers + 1
        fix = doc.layers[-1]
        assert "ghost_fix" in fix.params and fix.mask is not None, fix.params
        assert doc.history.names()[-1] == _t("修补重影")
        # 补丁亮度匹配到合并结果：选区周围不应该有明显的明暗跳变
        comp = doc.render_full()[..., :3].astype(np.float32)
        inner = comp[230:350, 330:570].mean()
        base_in = res[230:350, 330:570].astype(np.float32).mean()
        raw_in = srcs[pick].pixels[230:350, 330:570, :3].astype(np.float32).mean()
        log(f"修补后选区内平均亮度 {inner:.1f}（修补前 {base_in:.1f}；这张源图原样是 {raw_in:.1f}）")
        assert abs(inner - base_in) < 12, (inner, base_in)
        shot("merge_04_patched")
        ew.undo()
        assert len(doc.layers) == n_layers
        ew.redo()
        assert len(doc.layers) == n_layers + 1
        log("修补重影：新图层带蒙版，亮度匹配，可撤销 / 重做")

        # ---- 保存工程再打开：源图层还在，还能接着修补 ----
        path = eio.default_project_path(doc)
        assert ew.save_project()
        assert os.path.exists(path)
        d2 = eio.load_project(path)
        assert d2.source.suffix == "_HDR" and len(hdr.merge_sources(d2)) == 3 and hdr.merge_result_layer(d2)
        log(f"保存工程：{os.path.basename(path)}，再打开源图层、合并结果都在")
        doc.modified = False
        ew.close()
        yield 300
        result["ok"] = True

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
                    shot("merge_FAILED_modal", m)
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
            shot("merge_FAILED")
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
