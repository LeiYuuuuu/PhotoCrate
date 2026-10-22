"""补全工作流的界面自动化测试（无界面模式）：视频、查找重复、导出分享 / 网页相册、导入向导。

用法（Linux）：QT_QPA_PLATFORM=offscreen python tests/ui_workflow.py <输出目录> [light|dark]
需要 ExifTool（给测试视频写拍摄时间）：设置环境变量 PHOTOCRATE_TEST_EXIFTOOL。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="pc_ui_wf_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")
os.environ["XDG_DATA_HOME"] = os.path.join(HOME, "xdg")      # 回收站放在临时目录里


def build(root: Path) -> dict:
    from test_culling import _scene
    from test_workflow import make_video
    lib = root / "京都"
    t0 = datetime(2026, 4, 2, 10, 0, 0)
    for k in range(6):
        _scene(lib / f"DSC{k + 1:05d}.JPG", t0 + timedelta(minutes=30 * k), seed=70 + k)
    (lib / "DSC00006.ARW").write_bytes(os.urandom(5000))
    # 视频：QuickTime 时间是 UTC（10:40 +09:00 = 01:40 UTC），应按附近照片换算成当地时间
    make_video(lib / "C0001.MP4", utc="2026:04:02 01:40:00", frames=60)
    # 备份文件夹：3 张一模一样的副本（其中一张改了名）
    bak = root / "备份"
    bak.mkdir(parents=True)
    for k, name in ((1, "DSC00001.JPG"), (2, "DSC00002.JPG"), (3, "复制的照片.JPG")):
        shutil.copy2(lib / f"DSC{k:05d}.JPG", bak / name)
    # 存储卡
    card = root / "SDCARD"
    dcim = card / "DCIM" / "100MSDCF"
    for k in range(3):
        _scene(dcim / f"DSC{k + 101:05d}.JPG", datetime(2026, 4, 5, 9) + timedelta(hours=k), seed=90 + k)
    make_video(card / "PRIVATE" / "M4ROOT" / "CLIP" / "C0002.MP4", utc="2026:04:05 02:30:00", frames=40)
    return {"lib": str(lib), "bak": str(bak), "card": str(card), "root": str(root)}


def main(out: str, theme_name: str = "light") -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from photocrate.bootstrap import bootstrap
    from photocrate.config import Settings
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal, journal_path
    from photocrate.ui import theme
    from photocrate.ui.context import AppContext
    from photocrate.ui.main_window import MainWindow

    os.makedirs(out, exist_ok=True)
    data = build(Path(HOME) / "photos")
    app = QApplication(sys.argv)
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("parallel_mode", "thread")
    s.set("exiftool_path", os.environ.get("PHOTOCRATE_TEST_EXIFTOOL", ""))
    s.set("source_dirs", [data["lib"], data["bak"]])
    s.set("target_dir", data["root"])
    theme.apply(app, theme_name)
    lib = Library(s)
    ctx = AppContext(s, lib, Journal(journal_path()))
    win = MainWindow(ctx)
    win.resize(1600, 980)
    win.show()
    result = {"ok": False}

    def log(msg):
        print("  ·", msg, flush=True)

    def shot(name, widget=None):
        app.processEvents()
        (widget or win).grab().save(os.path.join(out, f"{theme_name}_{name}.png"))

    def modal():
        return QApplication.activeModalWidget()

    def wait_modal(sub, timeout=120):
        for _ in range(timeout):
            m = modal()
            if m is not None and sub in m.windowTitle():
                return m
            yield 150
        raise AssertionError(f"没有出现对话框：{sub}（当前 {modal() and modal().windowTitle()}）")

    def wait_idle():
        while ctx.busy or ctx._workers:
            yield 200
        yield 200

    def scan():
        win.page("source").start_scan()
        yield from wait_idle()

    def script():
        yield from scan()
        groups = [g for g in lib.groups if g.files]
        assert len(groups) == 10, [g.name for g in groups]
        vid = next(g for g in groups if g.is_video)
        d = vid.feats["date"]
        assert d["tz"] == "+09:00" and d["dt"].startswith("2026-04-02T10:40"), d
        assert vid.thumb and os.path.exists(vid.thumb) and (vid.meta.get("duration") or 0) > 1
        log(f"视频：C0001.MP4 时长 {vid.meta['duration']:.1f}s，UTC 01:40 → 按附近照片换算成 {d['dt'][11:16]} {d['tz']}")
        win.go(win.page_ids.index("culling"))
        yield 400
        cp = win.page("culling")
        shot("wf_01_culling_video")
        v = ctx.open_viewer(cp.shown, cp.shown.index(vid), win)
        v.resize(1200, 760)
        for _ in range(40):
            if v.pane_a.img is not None:
                break
            yield 100
        assert v.pane_a.img is not None and v.pane_a.img.width() == 320
        shot("wf_02_viewer_video", v)
        v.close()
        yield 200

        # ---- 查找重复 ----
        QTimer.singleShot(0, cp.b_dupe.click)
        dlg = yield from wait_modal("查找重复")
        dlg.b_find.click()
        for _ in range(100):
            if dlg.result is not None:
                break
            yield 100
        assert len(dlg.result.sets) == 3 and dlg.result.total_groups == 3, dlg.result
        removed = sorted(os.path.basename(op.src) for it in dlg.plan.items for op in it.ops)
        keep_dir = {os.path.dirname(lib.by_gid(s_.keep).primary) for s_ in dlg.result.sets}
        assert keep_dir == {data["lib"]}, keep_dir                 # 默认保留在主文件夹（第一个数据源）里的
        shot("wf_03_dedupe", dlg)
        QTimer.singleShot(0, dlg.bo.click)
        m = yield from wait_modal("确认删除副本")
        m.accept()
        m = yield from wait_modal("完成")
        m.accept()
        yield from wait_idle()
        assert sorted(os.listdir(data["bak"])) == [], os.listdir(data["bak"])
        b = ctx.journal.batches()[0]
        res = ctx.mover.undo(b["id"])
        assert res.done_files == 3 and len(os.listdir(data["bak"])) == 3
        log(f"查找重复：3 组（含一张改了名的副本），删除 {removed} 后从回收站撤销恢复")
        yield from scan()

        # ---- 导出分享 JPG ----
        win.go(win.page_ids.index("culling"))
        yield 300
        exp = os.path.join(HOME, "导出")
        QTimer.singleShot(0, cp.b_export.click)
        dlg = yield from wait_modal("导出与分享")
        dlg.dst.setText(exp)
        dlg.wm.setText("© PhotoCrate")
        dlg.size.setCurrentIndex(dlg.size.findData(1080))
        dlg._preview()
        yield 300
        shot("wf_04_export", dlg)
        QTimer.singleShot(0, dlg.bo.click)
        m = yield from wait_modal("导出完成")
        m.reject()
        files = sorted(os.listdir(exp))
        assert len(files) == 9 and all(f.lower().endswith(".jpg") for f in files), files      # 9 张照片，视频跳过
        from PIL import Image
        with Image.open(os.path.join(exp, files[0])) as im:
            assert max(im.size) == 640, im.size         # 原图只有 640：不放大
        log(f"导出分享：{len(files)} 张 JPG（长边上限 1080、小图不放大，带水印），视频跳过")

        # ---- 网页相册 ----
        win.go(win.page_ids.index("travel"))
        yield 400
        tp = win.page("travel")
        alb = os.path.join(HOME, "相册")
        QTimer.singleShot(0, tp.b_export.click)
        dlg = yield from wait_modal("导出与分享")
        assert dlg.album
        dlg.dst.setText(alb)
        dlg.title.setText("京都四月")
        QTimer.singleShot(0, dlg.bo.click)
        m = yield from wait_modal("导出完成")
        m.reject()
        idx = next(Path(alb).rglob("index.html"))
        text = idx.read_text("utf-8")
        assert "京都四月" in text and "2026-04-02" in text, text[:300]
        log(f"网页相册：{os.path.relpath(idx, HOME)}")

        # ---- 导入向导 ----
        win.go(win.page_ids.index("source"))
        yield 300
        sp = win.page("source")
        dest = os.path.join(HOME, "照片库")
        QTimer.singleShot(0, sp.b_import.click)
        dlg = yield from wait_modal("导入照片")
        dlg.src.addItem(data["card"], data["card"])
        dlg.src.setCurrentIndex(dlg.src.findData(data["card"]))
        dlg.dst.setText(dest)
        dlg.b_read.click()
        for _ in range(150):
            if dlg.check is not None:
                break
            yield 100
        assert dlg.check.photos == 3 and dlg.check.videos == 1, (dlg.check.photos, dlg.check.videos)
        dlg._rebuild()
        yield 100
        shot("wf_05_import", dlg)
        QTimer.singleShot(0, dlg.bo.click)
        m = yield from wait_modal("导入完成")
        m.accept()
        yield 400
        yield from wait_idle()
        got = sorted(str(p.relative_to(dest)) for p in Path(dest).rglob("*") if p.is_file())
        assert len(got) == 4 and any(x.startswith(os.path.join("2026", "2026-04-05")) for x in got), got
        assert dest in ctx.settings.get("source_dirs") and len([g for g in lib.groups if g.files]) == 14
        log(f"导入向导：存储卡 3 张照片 + 1 段视频 → {got[0].split(os.sep)[0]}/…，自动加入数据源并扫描")
        # 再插一次卡：全部已导入
        QTimer.singleShot(0, sp.b_import.click)
        dlg = yield from wait_modal("导入照片")
        dlg.src.addItem(data["card"], data["card"])
        dlg.src.setCurrentIndex(dlg.src.findData(data["card"]))
        dlg.b_read.click()
        for _ in range(150):
            if dlg.check is not None:
                break
            yield 100
        assert len(dlg.check.already) == 4 and not dlg.check.new
        shot("wf_06_import_again", dlg)
        dlg.reject()
        log("再次读取同一张卡：4 组全部识别为“已导入”")
        # 历史页显示“导入”，撤销
        win.go(win.page_ids.index("history"))
        yield 300
        hp = win.page("history")
        b = next(b for b in ctx.journal.batches() if '"import"' in (b.get("note") or ""))
        assert hp.table.item([x["id"] for x in hp._batches].index(b["id"]), 2).text() == "导入"
        shot("wf_07_history")
        res = ctx.mover.undo(b["id"])
        assert res.done_files == 4 and not any(p.is_file() for p in Path(dest).rglob("*"))
        log("撤销导入：复制出来的 4 个文件进回收站")
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
            shot("wf_FAILED", modal())
            app.quit()
            return
        QTimer.singleShot(delay or 50, pump)

    QTimer.singleShot(400, pump)
    QTimer.singleShot(300_000, app.quit)
    app.exec()
    lib.close()
    print("RESULT:", "PASS" if result["ok"] else "FAIL", "· screenshots in", out)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "light"))
