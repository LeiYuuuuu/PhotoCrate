"""「挑片」页与大图查看器的界面自动化测试（无界面模式），覆盖第二梯队全部功能并截图。

用法（Linux）：QT_QPA_PLATFORM=offscreen python tests/ui_culling.py <输出目录> [light|dark]
需要 ExifTool：设置环境变量 PHOTOCRATE_TEST_EXIFTOOL（写入 XMP 一步需要）。
流程：生成样例（两组连拍 + 单张 + RAW/JPG 配对）→ 扫描 → 相似分组 → 键盘打星 / 留弃 / 色标
     → 大图查看（100%、对比、打分）→ 非最佳移入「待确认_相似」并撤销 → 星级写入 XMP 并撤销
     → 只保留 JPG（回收站）并从「操作记录」撤销 → 批量重命名并撤销。每一步都断言结果。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="pc_ui_cull_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")
os.environ["XDG_DATA_HOME"] = os.path.join(HOME, "xdg")      # 回收站放在临时目录里（send2trash 导入时读取）


def build_samples(root: str) -> dict:
    from pathlib import Path
    from test_culling import _scene
    lib = Path(root) / "京都2026"
    t0 = datetime(2026, 4, 2, 10, 0, 0)
    n = 1
    # 连拍 A：4 张同一场景（第 3 张模糊）；连拍 B：3 张；再加 3 张各不相同的单张
    for k in range(4):
        _scene(lib / f"DSC{n:05d}.JPG", t0 + timedelta(seconds=k), seed=11, shift=k * 2, blur=(k == 2))
        n += 1
    for k in range(3):
        _scene(lib / f"DSC{n:05d}.JPG", t0 + timedelta(minutes=5, seconds=k * 2), seed=22, shift=k * 3)
        n += 1
    for k in range(3):
        _scene(lib / f"DSC{n:05d}.JPG", t0 + timedelta(minutes=20 + k * 10), seed=40 + k)
        n += 1
    # 两张 RAW+JPG（RAW 是随机字节，只用来测清理 / 改名 / XMP）
    raws = []
    for k in range(2):
        jp = _scene(lib / f"DSC{n:05d}.JPG", t0 + timedelta(hours=2, minutes=k * 7), seed=60 + k)
        raw = jp.with_suffix(".ARW")
        raw.write_bytes(os.urandom(4096 + k))
        raws.append(str(raw))
        n += 1
    return {"lib": str(lib), "raws": raws}


def main(out: str, theme_name: str = "light") -> int:
    from PySide6.QtCore import QPointF, Qt, QTimer
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog

    from photocrate.bootstrap import bootstrap
    from photocrate.config import Settings
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal, journal_path
    from photocrate.ui import theme
    from photocrate.ui.context import AppContext
    from photocrate.ui.main_window import MainWindow

    os.makedirs(out, exist_ok=True)
    data = build_samples(os.path.join(HOME, "photos"))
    app = QApplication(sys.argv)
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("parallel_mode", "thread")
    s.set("exiftool_path", os.environ.get("PHOTOCRATE_TEST_EXIFTOOL", ""))
    s.set("source_dirs", [data["lib"]])
    s.set("target_dir", data["lib"])
    theme.apply(app, theme_name)
    lib = Library(s)
    ctx = AppContext(s, lib, Journal(journal_path()))
    win = MainWindow(ctx)
    win.resize(1600, 980)
    win.show()
    result = {"ok": False}
    page = win.page("culling")

    def log(msg):
        print("  ·", msg, flush=True)

    def shot(name, widget=None):
        app.processEvents()
        (widget or win).grab().save(os.path.join(out, f"{theme_name}_{name}.png"))

    def modal():
        return QApplication.activeModalWidget()

    def wait_modal(sub, timeout=60):
        for _ in range(timeout):
            m = modal()
            if m is not None and sub in m.windowTitle():
                return m
            yield 150
        raise AssertionError(f"没有出现对话框：{sub}（当前 {modal() and modal().windowTitle()}）")

    def g_of(stem):
        return next(g for g in lib.groups if g.files and g.stem == stem)

    def rows_of(stems):
        return [i for i, g in enumerate(page.grid.model_.groups) if g.stem in stems]

    def select(stems):
        page.grid.select_gids({g_of(x).gid for x in stems})
        idx = page.grid.model_.index(rows_of(stems)[0])
        page.grid.setCurrentIndex(idx)
        page.grid.select_gids({g_of(x).gid for x in stems})
        app.processEvents()

    def script():
        win.page("source").start_scan()
        while ctx.busy or ctx._workers:
            yield 200
        yield 300
        groups = [g for g in lib.groups if g.files]
        assert len(groups) == 12, len(groups)
        win.go(win.page_ids.index("culling"))
        yield 400

        # ---- 相似分组 ----
        sims: dict[int, list] = {}
        for g in groups:
            if g.feats.get("similar"):
                sims.setdefault(g.feats["similar"]["cid"], []).append(g.stem)
        sets = sorted(sorted(v) for v in sims.values())
        assert sets == [["DSC00001", "DSC00002", "DSC00003", "DSC00004"], ["DSC00005", "DSC00006", "DSC00007"]], sets
        best = [g.stem for g in groups if (g.feats.get("similar") or {}).get("best")]
        assert "DSC00003" not in best and len(best) == 2, best      # 模糊的那张不是最佳
        log(f"相似分组：{sets}，最佳 {best}")
        page.view_btns["similar"].setChecked(True)
        yield 300
        assert len(page.shown) == 7
        shot("cull_01_similar")

        # ---- 键盘打分 ----
        page.view_btns["all"].setChecked(True)
        yield 200
        page.grid.setFocus()
        select(["DSC00008"])
        QTest.keyClick(page.grid, Qt.Key.Key_4)
        QTest.keyClick(page.grid, Qt.Key.Key_6)
        select(["DSC00009", "DSC00010"])
        QTest.keyClick(page.grid, Qt.Key.Key_P)
        select(["DSC00004"])
        QTest.keyClick(page.grid, Qt.Key.Key_X)
        yield 200
        r8 = g_of("DSC00008").feats["rating"]
        assert r8["rating"] == 4 and r8["label"] == "Red" and r8["manual"], r8
        assert g_of("DSC00009").feats["rating"]["flag"] == "pick" == g_of("DSC00010").feats["rating"]["flag"]
        assert g_of("DSC00004").feats["rating"]["flag"] == "reject"
        assert lib.annotations.count("rating") == 4
        select(["DSC00008"])
        QTest.keyClick(page.grid, Qt.Key.Key_6)                       # 再按一次取消色标
        assert g_of("DSC00008").feats["rating"]["label"] is None
        QTest.keyClick(page.grid, Qt.Key.Key_6)
        page.grid.clearSelection()
        yield 200
        assert page.c_rated.value == "1" and page.c_pick.value == "2" and page.c_reject.value == "1", \
            (page.c_rated.value, page.c_pick.value, page.c_reject.value)
        shot("cull_02_rated")
        log("键盘打分：DSC00008 ★4 红；DSC00009/10 留用（多选）；DSC00004 弃用；色标再按一次取消")

        # ---- 大图查看 ----
        select(["DSC00001"])
        QTest.keyClick(page.grid, Qt.Key.Key_Space)
        yield 300
        from photocrate.ui.viewer import ViewerWindow
        v = next(w for w in QApplication.topLevelWidgets() if isinstance(w, ViewerWindow) and w.isVisible())
        v.resize(1400, 860)
        for _ in range(40):
            if v.pane_a.img is not None:
                break
            yield 100
        assert v.pane_a.img is not None and v.pane_a.img.width() == 640, "大图没有加载"
        assert v.groups[v.index].stem == "DSC00001"
        QTest.keyClick(v, Qt.Key.Key_Right)
        yield 200
        assert v.groups[v.index].stem == "DSC00002"
        # 点照片右侧 / 左侧的箭头翻页
        from PySide6.QtCore import QPoint
        pw, ph = v.pane_a.width(), v.pane_a.height()
        QTest.mouseClick(v.pane_a, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(pw - 30, ph // 2))
        yield 200
        assert v.groups[v.index].stem == "DSC00003", v.groups[v.index].stem
        QTest.mouseClick(v.pane_a, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30, ph // 2))
        yield 200
        assert v.groups[v.index].stem == "DSC00002"
        QTest.mouseClick(v.pane_a, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(pw // 2, ph // 2))
        yield 100
        assert v.groups[v.index].stem == "DSC00002", "点中间不应该翻页"
        v.go(0)
        yield 200
        assert not v.pane_a.can_prev and v.pane_a.can_next
        QTest.mouseClick(v.pane_a, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30, ph // 2))
        yield 100
        assert v.index == 0, "第一张再点左边不动"
        v.go(1)
        yield 300
        v.pane_a._inside, v.pane_a._hover_zone = True, 1
        shot("cull_03b_viewer_nav", v)
        v.pane_a._inside, v.pane_a._hover_zone = False, 0
        QTest.keyClick(v, Qt.Key.Key_Z)
        assert v.pane_a.zoom == 1.0
        shot("cull_03_viewer_100", v)
        QTest.keyClick(v, Qt.Key.Key_Z)
        assert v.pane_a.zoom is None
        QTest.keyClick(v, Qt.Key.Key_C)                                 # 对比：左 DSC00002，右 DSC00003
        for _ in range(40):
            if v.pane_a.img is not None and v.pane_b.img is not None:
                break
            yield 100
        assert v.compare and v.groups[v.left_index].stem == "DSC00002" and v.groups[v.index].stem == "DSC00003"
        # 滚轮放大左边，右边同步
        ev = QWheelEvent(QPointF(200, 200), v.pane_a.mapToGlobal(QPointF(200, 200)), QPointF(0, 0).toPoint(),
                         QPointF(0, 120).toPoint(), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        for _ in range(6):
            QApplication.sendEvent(v.pane_a, ev)
        assert v.pane_a.zoom and v.pane_b.zoom == v.pane_a.zoom and v.pane_b.cx == v.pane_a.cx
        QTest.keyClick(v, Qt.Key.Key_Right)                            # 右边换成 DSC00004
        yield 300
        assert v.groups[v.index].stem == "DSC00004"
        QTest.keyClick(v, Qt.Key.Key_U)                                # 右边（当前）取消弃用
        QTest.keyClick(v, Qt.Key.Key_2)
        yield 300
        shot("cull_04_viewer_compare", v)
        assert g_of("DSC00004").feats["rating"]["flag"] is None and g_of("DSC00004").feats["rating"]["rating"] == 2
        QTest.mouseClick(v.pane_a, Qt.MouseButton.LeftButton)          # 点左边 → 左边成为当前
        QTest.keyClick(v, Qt.Key.Key_5)
        assert g_of("DSC00002").feats["rating"]["rating"] == 5
        QTest.keyClick(v, Qt.Key.Key_Escape)                           # 退出对比
        assert not v.compare
        QTest.keyClick(v, Qt.Key.Key_Escape)                           # 关闭
        yield 300
        import shiboken6
        assert not shiboken6.isValid(v) or not v.isVisible()
        log("大图：翻页、100%、并排对比（缩放同步）、对比中给左右两边分别打分")
        g_of("DSC00002")
        ctx.rate([g_of("DSC00002").gid, g_of("DSC00004").gid], None)

        # ---- 非最佳移入「待确认_相似」 ----
        extras = sorted(g.stem for g in __import__("photocrate.ui.pages.culling_page", fromlist=["x"])
                        .similar_extras(lib.groups))
        assert len(extras) == 5 and not set(extras) & set(best), (extras, best)
        dest = os.path.join(HOME, "review")
        os.makedirs(dest, exist_ok=True)
        orig = QFileDialog.getExistingDirectory
        QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: dest)
        try:
            QTimer.singleShot(0, page.b_isolate.click)
            yield 300
            m = yield from wait_modal("确认移动")
            shot("cull_05_isolate_plan", m)
            m.accept()
            m = yield from wait_modal("执行完成")
            m.reject()
        finally:
            QFileDialog.getExistingDirectory = orig
        yield 200
        moved = sorted(os.listdir(os.path.join(dest, "待确认_相似")))
        assert moved == sorted(x + ".JPG" for x in extras), moved
        b = ctx.journal.batches()[0]
        res = ctx.mover.undo(b["id"])
        lib.apply_moves(res.mapping)
        ctx.files_changed.emit()
        assert all(os.path.exists(os.path.join(data["lib"], x + ".JPG")) for x in extras)
        log(f"非最佳移入「待确认_相似」：{moved}，撤销后已回原处")

        # ---- 星级写入 XMP ----
        raw_stems = [os.path.splitext(os.path.basename(r))[0] for r in data["raws"]]
        if s.get("exiftool_path"):
            select([raw_stems[0]])
            QTest.keyClick(page.grid, Qt.Key.Key_3)
            QTest.keyClick(page.grid, Qt.Key.Key_8)
            QTimer.singleShot(0, page.b_xmp.click)
            m = yield from wait_modal("XMP")
            shot("cull_06_xmp", m)
            m.accept()
            m = yield from wait_modal("写入完成")
            m.accept()
            xmp = os.path.splitext(data["raws"][0])[0] + ".xmp"
            text = open(xmp, encoding="utf-8").read()
            assert "<xmp:Rating>3</xmp:Rating>" in text and "Green" in text, text[:600]
            b = ctx.journal.batches()[0]
            res = ctx.mover.undo(b["id"])
            lib.apply_moves(res.mapping)
            lib.prune_missing()
            ctx.files_changed.emit()
            assert not os.path.exists(xmp)
            log("星级写入 XMP：xmp:Rating=3、Label=Green；撤销后 .xmp 进回收站")
        page.grid.clearSelection()
        yield 100

        # ---- 清理 RAW：只保留 JPG ----
        QTimer.singleShot(0, page.b_clean.click)
        dlg = yield from wait_modal("清理 RAW")
        yield 200
        assert dlg.mode() == "keep_jpg" and dlg.plan.file_count == 2, dlg.plan.file_count
        shot("cull_07_cleanup", dlg)
        dlg.mode_btns["unrated_raw"].setChecked(True)
        yield 100
        assert dlg.plan.file_count == (1 if s.get("exiftool_path") else 2)    # 打了 3 星的保留
        dlg.mode_btns["keep_jpg"].setChecked(True)
        yield 100
        QTimer.singleShot(0, dlg.bo.click)
        m = yield from wait_modal("确认放入回收站")
        m.accept()
        m = yield from wait_modal("清理完成")
        shot("cull_08_cleanup_done", m)
        m.accept()
        yield 200
        assert not any(os.path.exists(r) for r in data["raws"])
        trash = os.path.join(os.environ["XDG_DATA_HOME"], "Trash", "files")
        assert sorted(os.listdir(trash)) == sorted(os.path.basename(r) for r in data["raws"]) or \
            all(any(n.startswith(os.path.splitext(os.path.basename(r))[0]) for n in os.listdir(trash))
                for r in data["raws"]), os.listdir(trash)
        assert g_of(raw_stems[0]).kind == "jpeg"
        # 从「操作记录」撤销
        hist = win.page("history")
        win.go(win.page_ids.index("history"))
        yield 300
        hist.table.selectRow(0)
        yield 100
        assert ctx.journal.batches()[0]["operation"] == "delete"
        shot("cull_09_history")
        QTimer.singleShot(0, hist._undo)
        m = yield from wait_modal("撤销批次")
        m.accept()
        m = yield from wait_modal("撤销完成")
        m.accept()
        yield 300
        while ctx.busy or ctx._workers:
            yield 200
        yield 300
        assert all(os.path.exists(r) for r in data["raws"]), "RAW 没有从回收站还原"
        assert g_of(raw_stems[0]).kind != "jpeg", "还原后没有重新扫描"
        log("只保留 JPG：2 个 RAW 进回收站；「操作记录」撤销后从回收站放回并自动重新扫描")
        win.go(win.page_ids.index("culling"))
        yield 300

        # ---- 批量重命名 ----
        select([raw_stems[0], raw_stems[1]])
        QTimer.singleShot(0, page.b_rename.click)
        dlg = yield from wait_modal("批量重命名")
        dlg.tpl.setText("{日期}_{市|省|国家|京都}_{序号}")
        dlg._rebuild()
        names = sorted(r[1] for r in dlg.model.rows)
        assert names == ["20260402_京都_001.ARW", "20260402_京都_001.JPG", "20260402_京都_002.ARW",
                         "20260402_京都_002.JPG"], names
        shot("cull_10_rename", dlg)
        QTimer.singleShot(0, dlg.bo.click)
        m = yield from wait_modal("确认改名")
        m.accept()
        m = yield from wait_modal("改名完成")
        m.accept()
        yield 200
        assert sorted(f for f in os.listdir(data["lib"]) if f.startswith("2026")) == names
        if s.get("exiftool_path"):
            assert g_of("20260402_京都_001").feats["rating"]["rating"] == 3, "改名后星级丢失"
        b = ctx.journal.batches()[0]
        assert b["operation"] == "rename"
        res = ctx.mover.undo(b["id"])
        lib.apply_moves(res.mapping)
        ctx.files_changed.emit()
        assert all(os.path.exists(r) for r in data["raws"])
        if s.get("exiftool_path"):
            assert g_of(raw_stems[0]).feats["rating"]["rating"] == 3
        log("批量重命名：{日期}_{城市}_{序号}，RAW 与 JPG 同名，星级跟随；撤销后改回原名")

        ctx.rate([g.gid for g in lib.groups if g.files], None)
        assert lib.annotations.count("rating") == 0
        page.view_btns["all"].setChecked(True)
        yield 300
        shot("cull_11_final")
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
            m = modal()
            shot("cull_FAILED", m)
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
