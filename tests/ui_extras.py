"""v2.3 新功能的界面自动化测试（无界面模式），走一遍并截图。

用法：QT_QPA_PLATFORM=offscreen python tests/ui_extras.py <输出目录> [light|dark]
流程：生成样例（ui_photos 的 20 张 + 一组 3 连拍 + 一张带二维码的）→ 扫描 →
     隐藏两张（图库里消失、出现「已隐藏」相簿）→ 设密码、锁上、输入密码打开 →
     单张编辑里开负片反转、拷贝编辑 → 粘贴编辑到两张（存成新照片）→ 统一色调窗口 →
     导出窗口的隐私体检开关（默认关）→ 体检找到二维码 → 确认窗口 →
     挑片页：连拍组标出闭眼、“最佳”换人 →
     手机互传：开服务（只听 127.0.0.1）、取网页和相册数据、上传一张 → 自动同步进相册。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="pc_ui_extras_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")


def build_more(root: str) -> None:
    """一组 3 连拍（画面几乎一样、相隔 1 秒）+ 一张带二维码和手机号的“快递单”。"""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from photocrate.core import qr
    rng = np.random.default_rng(3)
    base = rng.uniform(60, 190, (900, 1200, 3))
    for k in range(3):
        img = np.clip(base + rng.normal(0, 2, base.shape), 0, 255).astype(np.uint8)
        ex = Image.Exif()
        ex.get_ifd(0x8769)[0x9003] = f"2026:05:01 10:00:0{k}"
        Image.fromarray(img).save(os.path.join(root, f"BURST_{k}.JPG"), "JPEG", quality=90, exif=ex.tobytes())
    im = Image.new("RGB", (1200, 900), (235, 232, 225))
    code = qr.to_image(qr.encode("https://example.com/parcel/SF1234567890123"), 8)
    im.paste(Image.fromarray(code).convert("RGB"), (720, 160))
    fpath = next((p for p in (r"C:\Windows\Fonts\msyh.ttc",) if os.path.exists(p)), None)
    f = ImageFont.truetype(fpath, 56) if fpath else ImageFont.load_default()
    d = ImageDraw.Draw(im)
    d.text((80, 200), "收件人：张三", fill=(20, 20, 20), font=f)
    d.text((80, 300), "电话 13812345678", fill=(20, 20, 20), font=f)
    ex = Image.Exif()
    ex.get_ifd(0x8769)[0x9003] = "2026:05:02 12:00:00"
    im.save(os.path.join(root, "PARCEL.JPG"), "JPEG", quality=92, exif=ex.tobytes())


def main(out: str, theme_name: str = "light") -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLineEdit

    from photocrate import i18n
    i18n.set_language(os.environ.get("PHOTOCRATE_LANG"))
    from photocrate.bootstrap import bootstrap
    from photocrate.config import Settings
    from photocrate.core import smart
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal, journal_path
    from photocrate.ui import theme
    from photocrate.ui.context import AppContext
    from photocrate.ui.main_window import MainWindow
    from tests.ui_photos import build_samples

    os.makedirs(out, exist_ok=True)
    lib_dir = build_samples(os.path.join(HOME, "photos", "相册测试"))
    build_more(lib_dir)
    app = QApplication(sys.argv)
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("parallel_mode", "thread")
    s.set("source_dirs", [lib_dir])
    s.set("target_dir", lib_dir)
    s.set("editor_tour_done", True)
    s.set("lan_host", "127.0.0.1")
    s.set("lan_upload_dir", os.path.join(lib_dir, "手机上传"))
    s.set("faceq_auto", False)
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

    def idle():
        while ctx.busy or ctx._workers:
            yield 150

    def modal(fn, delay=250):
        """稍后对弹出的模态窗口做点什么（确认 / 输入密码），不然 exec() 会一直等。"""
        def go():
            w = app.activeModalWidget()
            if w is not None:
                fn(w)
        QTimer.singleShot(delay, go)

    def script():
        page = win.page("photos")
        win.page("source").start_scan()
        yield 300
        yield from idle()
        yield 300
        groups = [g for g in lib.groups if g.files]
        assert len(groups) == 24, len(groups)
        win.go(0)
        yield 200

        # ---- 隐藏 ----
        first = page.grid.groups()[:2]
        modal(lambda w: w.accept())                       # “隐藏照片”说明
        page.set_hidden(first, True)
        yield 300
        shown = {g.gid for g in page.grid.groups()}
        assert not shown & {g.gid for g in first} and len(shown) == 22, len(shown)
        page.set_tab("albums")
        yield 200
        keys = [c.key for sec in page.albums.sections for c in sec.items]
        assert smart.HIDDEN_KEY in keys, keys
        page.open_album(smart.HIDDEN_KEY)
        yield 200
        assert page.stack.currentWidget() is page.album_view and len(page.album_grid.groups()) == 2
        assert page.b_album_lock.isVisible()
        shot("extras_01_hidden_album")
        page.marks.set_password("pc-test")
        page.go_back()
        yield 200
        assert not page.marks.unlocked
        hid = next(c for sec in page.albums.sections for c in sec.items if c.key == smart.HIDDEN_KEY)
        assert hid.count == 0 and hid.cover is None, (hid.count, hid.cover)
        shot("extras_02_hidden_locked")

        def type_pw(w, pw):
            e = w.findChild(QLineEdit)
            e.setText(pw)
            e.returnPressed.emit()
        modal(lambda w: type_pw(w, "wrong"))
        modal(lambda w: w.reject(), 700)                  # 密码不对：窗口不关，取消
        page.open_album(smart.HIDDEN_KEY)
        yield 300
        assert page.stack.currentWidget() is not page.album_view and not page.marks.unlocked
        modal(lambda w: type_pw(w, "pc-test"))
        page.open_album(smart.HIDDEN_KEY)
        yield 300
        assert page.stack.currentWidget() is page.album_view and len(page.album_grid.groups()) == 2
        log("隐藏：图库里不显示、「已隐藏」相簿可设密码（锁着时不显示张数和封面）、密码错打不开")
        page.set_hidden(page.album_grid.groups()[:1], False)
        yield 200
        assert len(page.album_grid.groups()) == 1
        page.set_tab("library")
        yield 200
        assert len(page.grid.groups()) == 23

        # ---- 单张编辑：负片反转 + 拷贝编辑 ----
        g0 = next(g for g in page.grid.groups() if g.stem == "IMG_0004")
        page.open_detail(page.grid, g0.gid)
        yield 600
        d = page.detail
        d.start_edit()
        yield 300
        d.edit.b_neg.setChecked(True)
        s0 = d.edit.sliders["exposure"][0]
        s0.setValue(int(0.5 * d.edit.sliders["exposure"][2]))
        yield 500
        assert d.edit.state.negative == "color" and d.edit.state.sliders.get("exposure")
        shot("extras_03_edit_negative")
        d.copy_edit()
        assert page.copied_edit and page.copied_edit[0].negative == "color"
        modal(lambda w: w.accept())                       # 放弃修改？
        d.cancel_edit()
        yield 200
        page.go_back()
        yield 200

        # ---- 粘贴编辑 / 统一色调 ----
        from photocrate.ui.photos.batch import MatchDialog, PasteDialog, save_batch
        targets = [g for g in page.grid.groups() if g.stem in ("IMG_0010", "IMG_0011")]
        pd = PasteDialog(page, page.copied_edit[0], page.copied_edit[1], targets)
        pd.show()
        yield 300
        assert set(pd.checks) == {"adjust", "negative"} and pd.bo.isEnabled()
        shot("extras_04_paste_dialog", pd)
        st = pd.state()
        pd.close()
        modal(lambda w: w.accept(), 2500)                 # 结果提示
        paths = save_batch(ctx, page, targets, st, "粘贴编辑")
        assert len(paths) == 2 and all(os.path.exists(p) and i18n.t("_编辑") in p for p in paths), paths
        yield 400
        yield from idle()
        log("粘贴编辑：选两张，另存为「原名_编辑.jpg」")
        md = MatchDialog(page, page.grid.groups()[:5])
        md.show()
        yield 600
        assert md.state().match_on() and len(md.targets()) == 4
        shot("extras_05_match_dialog", md)
        md.close()

        # ---- 导出：隐私体检 ----
        from photocrate.ops import privacy
        from photocrate.ui.privacy_dialog import PrivacyDialog
        from photocrate.ui.workflow_dialogs import ExportDialog
        parcel = next(g for g in lib.groups if g.files and g.stem == "PARCEL")
        ed = ExportDialog(page, ctx, [parcel], "1")
        ed.show()
        yield 200
        assert not ed.cb_priv.isChecked()
        shot("extras_06_export_dialog", ed)
        ed.close()
        found = privacy.scan([parcel], lib)
        kinds = {f.kind for f in found.get(parcel.gid, [])}
        assert "qr" in kinds, kinds
        if lib.ocr.ready():
            assert {"phone", "express"} <= kinds, kinds
        pv = PrivacyDialog(page, [parcel], found, lib.ocr.ready())
        pv.show()
        yield 400
        shot("extras_07_privacy_review", pv)
        assert pv.boxes()[parcel.gid]
        pv.close()
        log(f"隐私体检：快递单照片找到 {sorted(kinds)}")

        # ---- 挑片：连拍里的闭眼 ----
        cp = win.page("culling")
        bursts = sorted((g for g in lib.groups if g.files and g.stem.startswith("BURST_")), key=lambda g: g.stem)
        assert all(g.feats.get("similar") for g in bursts), [g.feats.get("similar") for g in bursts]
        sharp = max(bursts, key=lambda g: g.feat("blur", "score") or 0)
        for g in bursts:                                    # 合成的检查结果：最清晰的那张闭着眼
            closed = g is sharp
            g.feats["faceq"] = {"fer": False, "clip": True,
                                "faces": [{"b": [0.4, 0.3, 0.12, 0.16], "eo": [0.03 if closed else 0.16] * 2,
                                           "pc": 0.95 if closed else 0.3}]}
        lib.regroup_similar()
        assert sharp.feats["faceq"]["closed"] == [0] and not sharp.feats["similar"]["best"]
        win.go(win.page_ids.index("culling"))
        yield 300
        cp.view_btns["faces"].setChecked(True)
        yield 300
        assert {g.gid for g in cp.shown} == {g.gid for g in bursts}, [g.stem for g in cp.shown]
        shot("extras_08_culling_eyes")
        log("挑片：最清晰的那张有人闭眼 → 标“闭眼 1”，“最佳”换成睁着眼的")
        cp.view_btns["all"].setChecked(True)
        win.go(0)
        yield 200

        # ---- 手机互传 ----
        s.set("auto_sync", True)
        ctx.settings_changed.emit("auto_sync")
        yield 1500
        from photocrate.ui.photos.phone import open_phone
        dlg = open_phone(page)
        assert dlg is not None and dlg.server is not None
        yield 300
        assert dlg.qr.pixmap() is not None and not dlg.qr.pixmap().isNull()
        url = dlg.server.url("127.0.0.1")
        shot("extras_09_phone_dialog", dlg)
        html = urllib.request.urlopen(url, timeout=10).read().decode("utf-8")
        assert "api/photos" in html
        data = json.loads(urllib.request.urlopen(url + "api/photos", timeout=10).read())
        ids = {it["id"] for it in data["items"]}
        hidden_now = {g.gid for g in lib.groups if g.files and page.marks.is_hidden(g)}
        assert hidden_now and not ids & hidden_now and len(ids) == len(page.groups()), (len(ids), hidden_now)
        assert dlg.b_sync.isHidden(), "上传文件夹在照片文件夹里、自动同步开着：不用再加"
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new("RGB", (640, 480), (30, 120, 200)).save(buf, "JPEG")
        q = urllib.parse.urlencode({"name": "IMG_PHONE.JPG", "mtime": "1761460000000"})
        req = urllib.request.Request(url + "up?" + q, data=buf.getvalue(), method="PUT")
        res = json.loads(urllib.request.urlopen(req, timeout=10).read())
        assert res["ok"], res
        for _ in range(80):
            yield 150
            if any(g.files and g.stem == "IMG_PHONE" for g in lib.groups):
                break
        assert any(g.files and g.stem == "IMG_PHONE" for g in lib.groups), "上传的照片没有同步进相册"
        assert "IMG_PHONE" in dlg.recv.text()
        shot("extras_10_phone_received", dlg)
        dlg.close()
        yield 200
        assert dlg.server is None
        log("手机互传：网页和相册数据（不含隐藏的）、上传一张 → 自动同步进相册、关窗口就停止")
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
            shot("extras_FAILED")
            app.quit()
            return
        QTimer.singleShot(delay or 50, pump)

    QTimer.singleShot(400, pump)
    QTimer.singleShot(240_000, app.quit)
    app.exec()
    ctx.shutdown()
    lib.close()
    print("RESULT:", "PASS" if result["ok"] else "FAIL", "· screenshots in", out)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "light"))
