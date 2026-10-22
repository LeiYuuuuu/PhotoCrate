"""第三梯队界面自动化测试（无界面模式）：旅行与地图、AI 搜图、人物。

用法（Linux）：QT_QPA_PLATFORM=offscreen python tests/ui_tier3.py <输出目录> [light|dark]
需要 onnx + onnxruntime（生成“假 CLIP”模型）；设置 PHOTOCRATE_TEST_MODELS 指向放有 YuNet / SFace 与 messi5.jpg 的文件夹时
也测试人物分组。
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
HOME = tempfile.mkdtemp(prefix="pc_ui_t3_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")
os.environ["PHOTOCRATE_MODELS"] = os.path.join(HOME, "models")

BOCA = (26.3587, -80.0831)
SPOTS = [("2026-01-01", (35.6586, 139.7454)), ("2026-01-02", (35.7148, 139.7967)),
         ("2026-01-03", (35.0116, 135.7681)), ("2026-01-04", (34.9671, 135.7727)),
         ("2026-01-05", (34.6873, 135.5262))]
COLORS = [(220, 30, 30), (30, 200, 40), (30, 40, 220), (200, 200, 40)]


def build(root: Path, faces_dir: str | None) -> dict:
    from test_timeplace import make_jpeg
    lib = root / "相机"
    n = 1
    for d in range(3):                                     # 在家
        make_jpeg(lib / f"DSC{n:05d}.JPG", datetime(2025, 12, 20 + d, 12), "-05:00", gps=BOCA,
                  color=COLORS[n % 4])
        n += 1
    for day, (lat, lon) in SPOTS:                          # 日本 5 天，每天 3 张有 GPS + 1 张没有
        y, m, dd = map(int, day.split("-"))
        for h in (10, 13, 16):
            make_jpeg(lib / f"DSC{n:05d}.JPG", datetime(y, m, dd, h), "+09:00",
                      gps=(lat + 0.002 * h, lon + 0.001 * h), color=COLORS[n % 4])
            n += 1
        make_jpeg(lib / f"DSC{n:05d}.JPG", datetime(y, m, dd, 19), "+09:00", color=COLORS[n % 4])
        n += 1
    make_jpeg(lib / f"DSC{n:05d}.JPG", datetime(2026, 1, 8, 12), "-05:00", gps=BOCA, color=COLORS[0])
    out = {"lib": str(lib), "faces": False}
    if faces_dir:
        try:
            import skimage.data as sd
            from PIL import Image
            a = Image.fromarray(sd.astronaut())
            fd = root / "人像"
            fd.mkdir(parents=True)
            for i, im in enumerate([a, a.transpose(Image.Transpose.FLIP_LEFT_RIGHT), a.resize((400, 400))]):
                im.save(fd / f"ASTRO_{i}.JPG", quality=92)
            m = Image.open(os.path.join(faces_dir, "messi5.jpg")).convert("RGB")
            m.save(fd / "OTHER_0.JPG", quality=92)
            m.resize((int(m.width * 1.6), int(m.height * 1.6))).save(fd / "OTHER_1.JPG", quality=92)
            out["faces"] = True
            out["faces_dir"] = str(fd)
        except Exception as e:
            print("  · 跳过人脸样例：", e)
    return out


def main(out: str, theme_name: str = "light") -> int:
    from PySide6.QtCore import QPointF, Qt, QTimer
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from photocrate.bootstrap import bootstrap
    from photocrate.config import Settings, find_resource
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal, journal_path
    from photocrate.ui import theme
    from photocrate.ui.context import AppContext
    from photocrate.ui.main_window import MainWindow

    os.makedirs(out, exist_ok=True)
    md = Path(os.environ["PHOTOCRATE_MODELS"])
    md.mkdir(parents=True, exist_ok=True)
    from photocrate.ai.clip import BertTokenizer
    from test_ai import make_fake_clip
    make_fake_clip(md / "chinese_clip_vitb16_q8.onnx", BertTokenizer(find_resource("data", "clip_vocab_zh.txt")))
    fdir = os.environ.get("PHOTOCRATE_TEST_MODELS")
    if fdir and os.path.exists(os.path.join(fdir, "face_recognition_sface_2021dec.onnx")):
        for f in ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx"):
            shutil.copy(os.path.join(fdir, f), md / f)
    else:
        fdir = None
    data = build(Path(HOME) / "photos", fdir)

    app = QApplication(sys.argv)
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("parallel_mode", "thread")
    s.set("exiftool_path", os.environ.get("PHOTOCRATE_TEST_EXIFTOOL", ""))
    s.set("source_dirs", [str(Path(HOME) / "photos")])
    s.set("face_min_size", 20)
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

    def wait_modal(sub, timeout=80):
        for _ in range(timeout):
            m = modal()
            if m is not None and sub in m.windowTitle():
                return m
            yield 150
        raise AssertionError(f"没有出现对话框：{sub}")

    def wait_idle():
        while ctx.busy or ctx._workers:
            yield 200
        yield 200

    def fake_tiles(mv):
        """把地图当前可见范围的瓦片预先放进磁盘缓存（测试环境没有网络）。"""
        from photocrate.ui.widgets.mapview import TILE
        n = 2 ** mv.zoom
        left, top = mv.cx - mv.width() / 2, mv.cy - mv.height() / 2
        for ty in range(max(0, int(top // TILE)), min(n - 1, int((top + mv.height()) // TILE)) + 1):
            for tx in range(int(left // TILE), int((left + mv.width()) // TILE) + 1):
                x = tx % n
                p = mv.tiles.path(mv.provider, mv.zoom, x, ty)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                img = QImage(256, 256, QImage.Format.Format_RGB32)
                dark = theme_name == "dark"
                img.fill(QColor("#22262B" if dark else "#E9ECE6"))
                qp = QPainter(img)
                qp.setPen(QColor("#2E333A" if dark else "#CDD3CB"))
                for k in range(0, 256, 32):
                    qp.drawLine(k, 0, k, 256)
                    qp.drawLine(0, k, 256, k)
                qp.end()
                img.save(p, "PNG")
        mv.tiles.mem.clear()
        mv.update()

    def script():
        win.page("source").start_scan()
        yield from wait_idle()
        groups = [g for g in lib.groups if g.files]
        n_photos = 24 + (5 if data["faces"] else 0)
        assert len(groups) == n_photos, len(groups)
        assert len(lib.ai.emb) == n_photos, "CLIP 索引没有建立"

        # ---- 旅行与地图 ----
        tp = win.page("travel")
        win.go(win.page_ids.index("travel"))
        yield 400
        assert len(lib.trips) >= 1 and lib.trips[0].start <= datetime(2025, 12, 20, 12)   # 没设常住地：全部算出行
        tp.home_edit.setText("Boca Raton, US")
        tp.add_home()
        yield from wait_idle()
        yield 300
        assert s.get("home_places") and abs(s.get("home_places")[0]["lat"] - 26.36) < 0.1
        jp = [t for t in lib.trips if "日本" in t.name]
        assert len(lib.trips) == 1 and jp, [t.name for t in lib.trips]
        t = jp[0]
        assert t.days == 5 and t.photos == 20 and t.away_photos == 15, (t.days, t.photos, t.away_photos)
        assert "东京" in t.name and "京都" in t.name and "大阪" in t.name, t.name
        log(f"旅行分段：设常住地 Boca Raton 后得到 1 次旅行「{t.name}」，{t.days} 天 {t.photos} 张（含 5 张没有 GPS 的）")
        tp.table.selectRow(1)
        yield 400
        mv = tp.map
        assert len(mv.points) == 15 and len(mv.route) == 15
        fake_tiles(mv)
        yield 300
        assert len(mv.clusters) >= 2
        c, items = max(mv.clusters, key=lambda ci: len(ci[1]))
        QTest.mouseClick(mv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c.toPoint())
        yield 300
        assert len(tp.grid.model_.groups) == len(items), (len(tp.grid.model_.groups), len(items))
        shot("t3_01_travel")
        log(f"地图：15 个带位置的点聚成 {len(mv.clusters)} 堆，点一堆在下方显示 {len(items)} 张")
        # 改名
        it = tp.table.item(1, 0)
        it.setText("日本跨年")
        yield 400
        assert s.get("trip_names") == {t.key: "日本跨年"} and lib.trips[0].name == "日本跨年"
        assert all(g.feats["trip"]["name"] == "日本跨年" for g in lib.groups if g.gid in lib.trips[0].gids)
        # 按旅行分区
        tp.to_partition()
        yield 600
        pp = win.page("partition")
        assert pp.rows and pp.rows[0].rule.dim == "trip"
        from photocrate.ops.planner import build_plan
        from photocrate.partition.base import PartitionContext
        plan = build_plan(lib.groups, pp.spec(), PartitionContext(s.snapshot()), os.path.join(HOME, "out"))
        folders = sorted({it.folder if not os.path.isabs(it.folder) else os.path.relpath(it.folder, os.path.join(HOME, "out"))
                          for it in plan.items})
        assert any(f.startswith("日本跨年") and "第3天" in f for f in folders) and "日常" in folders, folders
        shot("t3_02_partition_trip")
        log("改名“日本跨年”后，分区预览：" + "；".join(folders[:4]) + " …")
        tp.reset_name()

        # ---- AI 搜图 ----
        ap = win.page("ai")
        win.go(win.page_ids.index("ai"))
        yield 300
        assert ap.b_search.isEnabled()
        ap.query.setText("红")
        ap.search()
        yield 200
        top = [g for g in ap.grid.model_.groups if g.stem.startswith("DSC")][:3]   # 假模型只认颜色：只比较纯色样例
        reds = [g for g in groups if g.stem.startswith("DSC") and int(g.stem[3:]) % 4 == 0]
        assert {g.gid for g in top} <= {g.gid for g in reds}, [g.name for g in top]
        assert ap.bars.items, "场景列表为空"
        ap.grid.select_gids({top[0].gid})
        yield 100
        ap.similar()
        yield 200
        assert ap.grid.model_.groups and ap.grid.model_.groups[0].gid != top[0].gid
        assert ap.target.isVisible() and ap.target_gid == top[0].gid and ap.t_name.text() == top[0].name
        tags = [ap.grid.model_.labels[g.gid]["tag"] for g in ap.grid.model_.groups]
        vals = [int(x.rstrip("%")) for x in tags]
        assert vals == sorted(vals, reverse=True) and vals[0] >= 30, tags
        shot("t3_03_ai_search")
        log(f"AI 搜图：“红” → 前 3 张都是红色图；场景 {len(ap.bars.items)} 类；以图搜图正常")

        # ---- 人物 ----
        if data["faces"]:
            pg = win.page("people")
            win.go(win.page_ids.index("people"))
            yield 400
            assert pg.list.count() >= 2, pg.list.count()
            astro = next(c for c in lib.ai.people
                         if all(lib.by_gid(g).stem.startswith("ASTRO") for g in c.gids))
            for i in range(pg.list.count()):
                if pg.list.item(i).data(Qt.ItemDataRole.UserRole) == astro.cid:
                    pg.list.setCurrentRow(i)
            yield 200
            assert len(pg.grid.model_.groups) == 3
            pg.name.setText("宇航员")
            pg.apply_name()
            yield 300
            named = [c for c in lib.ai.people if c.named]
            assert len(named) == 1 and named[0].name == "宇航员" and len(named[0].gids) == 3
            assert pg.list.item(0).text().startswith("宇航员")
            shot("t3_04_people")
            log("人物：宇航员 3 张 / 另一人 2 张，命名后排在最前")
            from photocrate.partition.base import DIMENSIONS
            g = next(g for g in lib.groups if g.stem == "ASTRO_1")
            assert DIMENSIONS["person"].segments(g, {"mode": "main"}, PartitionContext(s.snapshot())) == ["宇航员"]
            # “不是人脸”：选中另一组里的一张照片标掉，再从“不是人脸”里恢复
            from photocrate.ui.pages.people_page import NONFACE
            other = next(c for c in lib.ai.people if not c.named)
            for i in range(pg.list.count()):
                if pg.list.item(i).data(Qt.ItemDataRole.UserRole) == other.cid:
                    pg.list.setCurrentRow(i)
            yield 200
            victim = pg.grid.model_.groups[0]
            pg.grid.select_gids({victim.gid})
            pg.toggle_face()
            yield 300
            assert [f.gid for f in lib.ai.nonfaces] == [victim.gid]
            items = [pg.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(pg.list.count())]
            assert items[-1] == NONFACE and pg.list.item(len(items) - 1).text() == "不是人脸 · 1"
            pg.list.setCurrentRow(len(items) - 1)
            yield 200
            assert pg.b_notface.text() == "是人脸（恢复）" and not pg.b_remove.isVisible()
            shot("t3_05_people_nonface")
            pg.grid.select_gids({victim.gid})
            pg.toggle_face()
            yield 300
            assert not lib.ai.nonfaces
            log("人物：选中照片标“不是人脸”→ 进入“不是人脸”组；再恢复回来")
            lib.ai.clear_people()
            lib.ai.recluster(lib.groups)
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
            shot("t3_FAILED", modal())
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
