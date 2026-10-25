"""「相册 / 修图」两个主界面的界面自动化测试（无界面模式），走一遍并截图。

用法：QT_QPA_PLATFORM=offscreen python tests/ui_photos.py <输出目录> [light|dark]
需要文字识别模型：PHOTOCRATE_MODELS 指向放着 ch_PP-OCRv4_det_infer.onnx / ch_PP-OCRv4_rec_infer.onnx 的文件夹
（没有时跳过文字相关的检查）。
流程：生成样例（几天、几个地点，有的照片上有字）→ 扫描（顺带识别文字）→ 图库 日 / 月 / 年 → 搜“拉面”“发票”
     → 单张照片（命中的文字框高亮）→ 收藏、新建相簿 → 相簿页 → 基础编辑（自动增强 + 滤镜 + 裁剪）存成新照片
     → 在修图中打开（切到「修图」模式、标签页）→ 关闭标签。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="pc_ui_photos_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")

TEXTS = {3: "一兰拉面 ICHIRAN", 7: "发票 合计 128.00 元", 11: "东京塔 TOKYO TOWER", 15: "出口 EXIT"}
PLACES = {"tokyo": (35.6586, 139.7454), "kyoto": (34.9671, 135.7727), "changsha": (28.2282, 112.9388)}


def _gps(lat: float, lon: float) -> dict:
    def dms(v):
        v = abs(v)
        d = int(v)
        m = int((v - d) * 60)
        s = round(((v - d) * 60 - m) * 60, 2)
        return (float(d), float(m), float(s))
    return {1: "N" if lat >= 0 else "S", 2: dms(lat), 3: "E" if lon >= 0 else "W", 4: dms(lon)}


def build_samples(root: str) -> str:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(root, exist_ok=True)
    rng = np.random.default_rng(7)
    fpath = next((p for p in (r"C:\Windows\Fonts\msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
                  if os.path.exists(p)), None)
    big = ImageFont.truetype(fpath, 72) if fpath else ImageFont.load_default()
    days = [("2026-04-02", "tokyo"), ("2026-04-03", "kyoto"), ("2025-10-01", "changsha"), ("2024-08-15", "changsha")]
    n = 0
    for di, (day, place) in enumerate(days):
        for k in range(5):
            h, w = (900, 1200) if k % 3 else (1200, 900)
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            base = rng.uniform(40, 200, 3)
            img = np.dstack([base[0] + 50 * xx / w, base[1] + 40 * yy / h, base[2] - 30 * xx / w])
            img += rng.normal(0, 3, (h, w, 3))
            im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
            if n in TEXTS:
                d = ImageDraw.Draw(im)
                d.rectangle([80, 260, w - 80, 420], fill=(245, 245, 240))
                d.text((110, 290), TEXTS[n], fill=(20, 20, 20), font=big)
            ex = Image.Exif()
            ex[0x010F], ex[0x0110] = ("Apple", "iPhone 15 Pro") if k % 2 else ("SONY", "ILCE-7M4")
            ex.get_ifd(0x8769)[0x9003] = f"{day.replace('-', ':')} {9 + k * 2:02d}:15:00"
            gps = ex.get_ifd(0x8825)
            gps.update(_gps(*PLACES[place]))
            im.save(os.path.join(root, f"IMG_{n:04d}.JPG"), "JPEG", quality=90, exif=ex.tobytes())
            n += 1
    return root


def main(out: str, theme_name: str = "light") -> int:
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
    lib_dir = build_samples(os.path.join(HOME, "photos", "相册测试"))
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
    ocr_ok = lib.ocr.ready()

    def log(msg):
        print("  ·", msg, flush=True)

    def shot(name, widget=None):
        app.processEvents()
        (widget or win).grab().save(os.path.join(out, f"{theme_name}_{name}.png"))

    def idle():
        while ctx.busy or ctx._workers:
            yield 150

    def script():
        page = win.page("photos")
        assert win.page_ids[0] == "photos" and win.stack.currentWidget() is page
        assert win.mode() == 0
        shot("photos_00_empty")
        win.page("source").start_scan()
        yield 300
        yield from idle()
        yield 300
        groups = [g for g in lib.groups if g.files]
        assert len(groups) == 20, len(groups)
        if ocr_ok:
            texty = sorted(g.stem for g in groups if g.feats.get("ocr"))
            assert texty == [f"IMG_{k:04d}" for k in sorted(TEXTS)], texty
            g3 = next(g for g in groups if g.stem == "IMG_0003")
            assert "拉面" in "".join(l["t"] for l in g3.feats["ocr"]), g3.feats["ocr"]
            log(f"扫描时识别了照片里的文字：{len(texty)} 张有字")
        win.go(0)
        yield 200
        # ---- 图库：日 / 月 / 年 ----
        assert page.stack.currentWidget() is page.grid
        secs = page.grid.sections
        assert len(secs) == 4 and secs[0].key == "d2026-04-03", [s.key for s in secs]
        assert "京都" in secs[0].sub or "Kyoto" in secs[0].sub, secs[0].sub
        shot("photos_01_days")
        page.set_level("month")
        yield 150
        assert page.stack.currentWidget() is page.cards
        shot("photos_02_months")
        page.set_level("year")
        yield 150
        assert [c.key for c in page.cards.sections[0].items] == ["y2026", "y2025", "y2024"]
        shot("photos_03_years")
        page._card_clicked(page.cards.sections[0].items[0])          # 年 → 月
        yield 100
        assert page.level == "month"
        page._card_clicked(page.cards.sections[0].items[0])          # 月 → 日
        yield 100
        assert page.level == "day" and page.stack.currentWidget() is page.grid
        log("图库：年 → 月 → 日 逐级进入，按天分段并显示地点")

        # ---- 搜索 ----
        page.search.setText("东京")
        page.run_search()
        yield 150
        assert page.stack.currentWidget() is page.search_grid
        assert len(page.result.gids) >= 5, page.result.gids
        assert any(sg.cat == "place" for sg in page.result.suggestions)
        shot("photos_04_search_place")
        if ocr_ok:
            page.search.setText("拉面")
            page.run_search()
            yield 150
            names = [g.stem for g in page.search_grid.groups()[:len(page.result.gids)]]
            assert names == ["IMG_0003"], names
            shot("photos_05_search_text")
            page.search.setText("发票 128")
            page.run_search()
            yield 150
            assert [lib.by_gid(i).stem for i in page.result.gids] == ["IMG_0007"], page.result.gids
            page.open_detail(page.search_grid, page.result.gids[0])
            yield 600
            d = page.detail
            assert page.stack.currentWidget() is d and d.canvas.hits, d.canvas.hits
            shot("photos_06_detail_text_hit")
            log("搜照片里的文字：“拉面”“发票 128”都找到对应照片，单张页高亮命中的文字框")
            d.back.emit()
            yield 150
            assert page.stack.currentWidget() is page.search_grid
        page.search.setText("2025 晚上")
        page.run_search()
        yield 100
        assert all(lib.by_gid(i).date.year == 2025 and lib.by_gid(i).date.hour >= 18 for i in page.result.gids)
        page.search.clear()
        yield 400
        assert page.stack.currentWidget() is not page.search_grid
        log("日期 + 时段搜索（“2025 晚上”）")

        # ---- 收藏 / 相簿 ----
        page.set_tab("library")
        yield 100
        two = page.grid.groups()[:2]
        page.toggle_fav(two)
        assert all(page.marks.is_fav(g) for g in two)
        aid = page.marks.create("测试相簿")
        page.marks.add(two, aid)
        page.grid.selected = {g.gid for g in two}
        page.grid.selection_changed.emit()
        yield 100
        assert page.sel_bar.isVisible()
        shot("photos_07_selection")
        page.grid.clear_selection()
        page.set_tab("albums")
        yield 200
        keys = [c.key for sec in page.albums.sections for c in sec.items]
        assert "media:favorite" in keys and f"user:{aid}" in keys and any(k.startswith("place:") for k in keys), keys
        if ocr_ok:
            assert "media:text" in keys
        shot("photos_08_albums")
        page.open_album(f"user:{aid}")
        yield 150
        assert len(page.album_grid.groups()) == 2
        shot("photos_09_album")
        log("收藏、新建相簿；相簿页有媒体类型 / 我的相簿 / 地点 / 事物与场景")

        # ---- 基础编辑 ----
        page.set_tab("library")
        yield 100
        g0 = page.grid.groups()[0]
        page.open_detail(page.grid, g0.gid)
        yield 800
        d = page.detail
        assert d.base is not None
        d.start_edit()
        yield 200
        d.edit.b_auto.setChecked(True)
        d.edit.sliders["saturation"][0].setValue(30)
        d.edit.preset_btns["胶片"].click()
        yield 500
        shot("photos_10_edit_adjust")
        d.edit._tab("crop", 2)
        d.edit.aspect.setCurrentIndex(2)        # 正方形
        yield 400
        assert d.canvas.crop is not None
        shot("photos_11_edit_crop")
        d.edit._tab("adjust", 0)
        before = {x.stem for x in lib.groups if x.files}
        d.save_edit()
        yield 500
        yield from idle()
        yield 500
        after = {x.stem for x in lib.groups if x.files}
        new = after - before
        assert new == {g0.stem + _t("_编辑")}, new
        from PIL import Image
        with Image.open(os.path.join(lib_dir, g0.stem + _t("_编辑") + ".jpg")) as im:
            assert im.width == im.height, im.size
        assert os.path.getsize(g0.primary) > 0
        log("基础编辑（自动增强 + 饱和度 + 胶片滤镜 + 1:1 裁剪）存成新照片，原片不变，自动进相册")
        yield 300

        # ---- 在修图中打开 ----
        if page.stack.currentWidget() is not page.detail:
            page.open_detail(page.grid, page.grid.groups()[0].gid)
            yield 300
        page.detail.open_studio()
        yield 600
        assert win.mode() == 1 and len(win.studio.editors) == 1, (win.mode(), win.studio.editors)
        ew = win.studio.editors[0]
        assert ew.parent() is win.studio.stack and not ew.isWindow()
        shot("photos_12_studio")
        win.set_mode(0)
        yield 200
        shot("photos_13_back_to_library")
        win.set_mode(1)
        win.studio.tabs.setCurrentIndex(0)
        yield 200
        shot("photos_14_studio_home")
        ew.close()
        yield 400
        assert not win.studio.editors and win.studio.tabs.count() == 1
        log("在修图中打开：切到「修图」模式、成为标签页；关闭后标签消失")
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
            shot("photos_FAILED")
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
