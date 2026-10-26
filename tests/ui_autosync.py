"""自动同步的界面自动化测试（无界面模式），走一遍并截图。

用法：QT_QPA_PLATFORM=offscreen python tests/ui_autosync.py <输出目录> [light|dark]
流程：空相册 → 「选择照片文件夹…」（自动开启同步，载入 4 张）→ 往文件夹里拷进 3 张（含子文件夹）→ 删掉 2 张
     → 打开一张照片时又拷进来一张（详情页不跳）→ 慢慢写入的大文件写完才出现 → 扫描 / 移动进行中不插队
     → 「资源接入」页的开关与状态 → 关掉后不再跟着变。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="pc_ui_autosync_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")


def make_photo(path: str, i: int) -> str:
    import numpy as np
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rng = np.random.default_rng(i)
    h, w = 600, 800
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    base = rng.uniform(40, 200, 3)
    img = np.dstack([base[0] + 60 * xx / w, base[1] + 50 * yy / h, base[2] - 40 * xx / w]) + rng.normal(0, 3, (h, w, 3))
    ex = Image.Exif()
    ex[0x010F], ex[0x0110] = "SONY", "ILCE-7M4"
    ex.get_ifd(0x8769)[0x9003] = f"2026:0{1 + i % 9}:{10 + i % 18:02d} {8 + i % 12:02d}:30:00"
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(path, "JPEG", quality=88, exif=ex.tobytes())
    old = time.time() - 3600                    # 像从相机 / 手机拷来的旧照片
    os.utime(path, (old, old))
    return path


def main(out: str, theme_name: str = "light") -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QFileDialog

    from photocrate import i18n
    i18n.set_language(os.environ.get("PHOTOCRATE_LANG"))
    from photocrate.bootstrap import bootstrap
    from photocrate.config import Settings
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal, journal_path
    from photocrate.ui import theme
    from photocrate.ui.context import AppContext
    from photocrate.ui.main_window import MainWindow

    os.makedirs(out, exist_ok=True)
    folder = os.path.join(HOME, "photos", "手机同步")
    staging = os.path.join(HOME, "staging")
    for i in range(4):
        make_photo(os.path.join(folder, f"IMG_{i:04d}.JPG"), i)
    for i in range(4, 12):
        make_photo(os.path.join(staging, f"IMG_{i:04d}.JPG"), i)

    app = QApplication(sys.argv)
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("parallel_mode", "thread")
    s.set("editor_tour_done", True)
    theme.apply(app, theme_name)
    lib = Library(s)
    ctx = AppContext(s, lib, Journal(journal_path()))
    win = MainWindow(ctx)
    win.resize(1600, 980)
    win.show()
    result = {"ok": False}
    a = ctx.autosync

    def log(msg):
        print("  ·", msg, flush=True)

    def shot(name, widget=None):
        app.processEvents()
        (widget or win).grab().save(os.path.join(out, f"{theme_name}_{name}.png"))

    def live():
        return [g for g in lib.groups if g.files]

    def until(cond, ms=20000):
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            if cond():
                return
            yield 100
        assert cond(), "等待超时"

    def settled():
        return a.worker is None and a.state == "watching"

    def script():
        page = win.page("photos")
        src = win.page("source")
        assert win.stack.currentWidget() is page and not live()
        yield 300
        assert a.state == "off"
        shot("autosync_00_empty")
        # ---- 选一个文件夹（对话框换成直接返回）→ 自动开启同步并载入 ----
        QFileDialog.getExistingDirectory = staticmethod(lambda *args, **kw: folder)
        a.add_folder(page)
        assert s.get("auto_sync") and s.get("source_dirs") == [folder]
        yield from until(lambda: len(live()) == 4 and settled())
        assert src.auto.isChecked() and src.dirs.count() == 1
        assert "●" in page.b_sync.text()
        log(f"选了文件夹：自动开启同步，载入 {len(live())} 张；按钮：{page.b_sync.text()}")
        yield 300
        shot("autosync_01_loaded")
        # ---- 拷进 3 张（一张在子文件夹里）----
        gids = {g.gid: g for g in live()}
        shutil.copy2(os.path.join(staging, "IMG_0004.JPG"), folder)
        shutil.copy2(os.path.join(staging, "IMG_0005.JPG"), folder)
        os.makedirs(os.path.join(folder, "2026-01"), exist_ok=True)
        shutil.copy2(os.path.join(staging, "IMG_0006.JPG"), os.path.join(folder, "2026-01"))
        t0 = time.monotonic()
        yield from until(lambda: len(live()) == 7 and settled())
        log(f"拷进 3 张：{time.monotonic() - t0:.1f} 秒后出现在相册里（{a.describe()}）")
        assert all(lib.by_gid(gid) is g for gid, g in gids.items()), "原有照片的编号变了"
        assert all(g.thumb for g in live())
        yield 300
        shot("autosync_02_added")
        # ---- 删掉 2 张 ----
        os.remove(os.path.join(folder, "IMG_0001.JPG"))
        os.remove(os.path.join(folder, "2026-01", "IMG_0006.JPG"))
        yield from until(lambda: len(live()) == 5 and settled())
        assert not any(g.name in ("IMG_0001.JPG", "IMG_0006.JPG") for g in live())
        log(f"删掉 2 张：相册里也没了（{a.describe()}）")
        yield 300
        shot("autosync_03_removed")
        # ---- 看着一张照片时又来了新照片：详情页不跳 ----
        g = next(g for g in live() if g.name == "IMG_0002.JPG")
        page.set_tab("library")
        page.open_detail(page.grid, g.gid)
        yield 400
        assert page.detail.group is not None and page.detail.group.gid == g.gid
        shutil.copy2(os.path.join(staging, "IMG_0007.JPG"), folder)
        yield from until(lambda: len(live()) == 6 and settled())
        yield 300
        assert page.stack.currentWidget() is page.detail and page.detail.group.name == "IMG_0002.JPG"
        log("打开着一张照片时同步了新照片：详情页还停在原来那张")
        shot("autosync_04_detail_kept")
        page.go_back()
        yield 200
        # ---- 慢慢写入的大文件：写完才出现 ----
        data = open(os.path.join(staging, "IMG_0008.JPG"), "rb").read()
        dst = os.path.join(folder, "IMG_0008.JPG")
        f = open(dst, "wb")
        try:
            for k in range(0, len(data), max(1, len(data) // 10)):
                f.write(data[k:k + max(1, len(data) // 10)])
                f.flush()
                yield 300
            assert len(live()) == 6, "文件还没写完就同步进来了"
        finally:
            f.close()
        yield from until(lambda: len(live()) == 7 and settled())
        g8 = next(g for g in live() if g.name == "IMG_0008.JPG")
        assert g8.thumb and g8.date, "写完后读到的不是完整照片"
        log("边写边看的文件：写了 3 秒，关闭后才同步进来，缩略图与拍摄时间完整")
        # ---- 有任务在跑：先不同步，结束后补上 ----
        ctx.busy = True
        shutil.copy2(os.path.join(staging, "IMG_0009.JPG"), folder)
        for _ in range(25):
            yield 100
        assert len(live()) == 7, "忙的时候不应该同步"
        ctx.busy = False
        yield from until(lambda: len(live()) == 8 and settled())
        log("扫描 / 移动进行中拷进的照片：任务结束后才补同步")
        # ---- 「资源接入」页 ----
        win.go(1)
        yield 300
        assert src.sync_state.text(), src.sync_state.text()
        log(f"资源接入页：{src.auto.text()} ☑ · {src.sync_state.toolTip()}")
        shot("autosync_05_source")
        # ---- 关掉：不再跟着变 ----
        src.auto.setChecked(False)
        yield from until(lambda: a.state == "off" and a.watcher is None)
        shutil.copy2(os.path.join(staging, "IMG_0010.JPG"), folder)
        for _ in range(25):
            yield 100
        assert len(live()) == 8 and not s.get("auto_sync")
        win.go(0)
        yield 300
        assert "关" in page.b_sync.text() or "off" in page.b_sync.text()
        log(f"关掉后拷进的照片不会自动出现；按钮：{page.b_sync.text()}")
        shot("autosync_06_off")
        result["ok"] = True

    steps = script()

    def pump():
        try:
            delay = next(steps)
        except StopIteration:
            app.quit()
            return
        except Exception:
            traceback.print_exc()
            shot("autosync_FAILED")
            app.quit()
            return
        QTimer.singleShot(delay or 50, pump)

    QTimer.singleShot(400, pump)
    QTimer.singleShot(180_000, app.quit)
    app.exec()
    ctx.shutdown()
    lib.close()
    print("RESULT:", "PASS" if result["ok"] else "FAIL", "· screenshots in", out)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "light"))
