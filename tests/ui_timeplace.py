"""「时间与地点」页的界面自动化测试（无界面模式），覆盖第一梯队全部功能并截图。

用法（Linux）：QT_QPA_PLATFORM=offscreen python tests/ui_timeplace.py <输出目录> [light|dark] [CR2 样例]
需要 ExifTool：设置环境变量 PHOTOCRATE_TEST_EXIFTOOL。
流程：生成样例（相机 4 天 + 手机参考照片 + GPX + 跨文件夹 RAW/JPG）→ 扫描 → 检查配对与 GPS 补全
     → 时区换算校正 → 手动指定地点 → 按行程批量指定 → 写入 XMP → 撤销 XMP → 清除标注。每一步都断言结果。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="pc_ui_tp_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")

from test_timeplace import make_jpeg, write_gpx  # noqa: E402  (同目录)


def build_samples(root: str, cr2: str | None) -> dict:
    lib = os.path.join(root, "日本2026")
    cam = os.path.join(lib, "相机")
    ref = os.path.join(root, "手机参考")
    from pathlib import Path
    # 3/8 东京、3/9 京都：手机参考照片覆盖；3/10 大阪：GPX 覆盖；3/11：相机时区设成 +08:00（时间慢 1 小时），无参考
    plan = [("2026-03-08", (35.6812, 139.7671), [9, 11, 14, 17]),
            ("2026-03-09", (35.0116, 135.7681), [8, 10, 13, 16, 19]),
            ("2026-03-10", (34.6937, 135.5023), [10, 12, 15]),
            ("2026-03-11", None, [8, 9, 23])]
    n = 1
    for day, gps, hours in plan:
        y, m, d = map(int, day.split("-"))
        for h in hours:
            tz = "+08:00" if day == "2026-03-11" else "+09:00"
            make_jpeg(Path(cam) / f"DSC{n:05d}.JPG", datetime(y, m, d, h, 7), tz, color=(60 + 9 * n, 120, 160))
            if gps and day != "2026-03-10":
                make_jpeg(Path(ref) / f"IMG_{n:04d}.JPG", datetime(y, m, d, h, 15), "+09:00",
                          gps=(gps[0] + 0.002 * h, gps[1]), make="Apple", model="iPhone 15 Pro")
            n += 1
    gpx = write_gpx(Path(root) / "大阪.gpx", [
        (datetime(2026, 3, 10, h - 9, mi, tzinfo=timezone.utc), 34.6937 + 0.001 * mi, 135.5023)
        for h in (9, 10, 11, 12, 13, 14, 15, 16) for mi in (0, 20, 40)])
    out = {"lib": lib, "cam": cam, "ref": ref, "gpx": str(gpx), "cr2": None}
    if cr2 and os.path.exists(cr2):
        # 跨文件夹：RAW/ 与 JPG/ 各放一份（同名、同拍摄时间）
        rdir, jdir = os.path.join(lib, "RAW"), os.path.join(lib, "JPG")
        os.makedirs(rdir, exist_ok=True)
        shutil.copy2(cr2, os.path.join(rdir, "IMG_4001.CR2"))
        make_jpeg(Path(jdir) / "IMG_4001.JPG", datetime(2007, 9, 14, 11, 32, 2), None, make="Canon",
                  model="Canon EOS 40D")
        out["cr2"] = os.path.join(rdir, "IMG_4001.CR2")
    return out


def main(out: str, theme_name: str = "light", cr2: str | None = None) -> int:
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
    data = build_samples(os.path.join(HOME, "photos"), cr2)
    app = QApplication(sys.argv)
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("parallel_mode", "thread")
    s.set("exiftool_path", os.environ.get("PHOTOCRATE_TEST_EXIFTOOL", ""))
    s.set("source_dirs", [data["lib"]])
    s.set("target_dir", data["lib"])
    s.set("gps_ref_dirs", [data["ref"]])
    s.set("gps_track_files", [data["gpx"]])
    theme.apply(app, theme_name)
    lib = Library(s)
    ctx = AppContext(s, lib, Journal(journal_path()))
    win = MainWindow(ctx)
    win.resize(1600, 980)
    win.show()
    result = {"ok": False, "log": []}

    def log(msg):
        print("  ·", msg, flush=True)
        result["log"].append(msg)

    def shot(name, widget=None):
        app.processEvents()
        (widget or win).grab().save(os.path.join(out, f"{theme_name}_{name}.png"))

    def modal():
        return QApplication.activeModalWidget()

    def by_day(day):
        return [g for g in lib.groups if g.files and g.date and g.date.strftime("%Y-%m-%d") == day]

    page = win.page("timeplace")

    def select_days(keys):
        from PySide6.QtCore import QItemSelectionModel
        sm = page.days.selectionModel()
        sm.clearSelection()
        for i, r in enumerate(page._days):
            if r.key in keys:
                sm.select(page.day_model.index(i, 0),
                          QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        app.processEvents()

    def script():
        # ---- 扫描 ----
        win.page("source").start_scan()
        while ctx.busy or ctx._workers:
            yield 200
        yield 300
        groups = [g for g in lib.groups if g.files]
        n_expected = 15 + (1 if data["cr2"] else 0)
        assert len(groups) == n_expected, f"组数 {len(groups)} != {n_expected}"
        if data["cr2"]:
            assert lib.paired_across == 1, "跨文件夹配对失败"
            pair = next(g for g in groups if g.stem == "IMG_4001")
            assert pair.kind == "raw+jpeg" and pair.cross_folder
            log("跨文件夹配对：IMG_4001.CR2（RAW/）+ IMG_4001.JPG（JPG/）→ 1 组 RAW+JPG")
        for day, prov in (("2026-03-08", "东京都"), ("2026-03-09", "京都府"), ("2026-03-10", "大阪府")):
            gs = by_day(day)
            assert gs and all(g.feat("geo", "source") == "track" and g.feat("geo", "province") == prov for g in gs), \
                f"{day} GPS 补全失败：{[(g.name, g.feat('geo', 'source'), g.feat('geo', 'province')) for g in gs]}"
        kinds = {g.feat("geo", "match_kind") for g in by_day("2026-03-10")}
        assert kinds == {"track"}, kinds
        log(f"GPS 补全：{lib.fill_stats}")
        assert all(g.feat("geo", "located") is False for g in by_day("2026-03-11"))

        win.go(win.page_ids.index("timeplace"))
        yield 600
        while page._place_index is None:
            yield 200
        shot("tp_01_overview")

        # ---- 时间校正：相机时区 +08:00 → 实际 +09:00 ----
        select_days({"2026-03-11"})
        page._set_tz_combo(page.cv_from, "+08:00")
        page._set_tz_combo(page.cv_to, "+09:00")
        page._convert_tz()
        assert page._shift_seconds() == 3600
        QTimer.singleShot(0, page.b_shift.click)
        yield 500
        dlg = modal()
        assert dlg is not None and "时间校正" in dlg.windowTitle(), "没有弹出时间校正预览"
        shot("tp_02_shift_dialog", dlg)
        dlg.accept()
        yield 300
        while ctx._workers:
            yield 150
        gs = by_day("2026-03-11")
        nxt = by_day("2026-03-12")
        assert len(gs) == 2 and len(nxt) == 1, [(g.name, g.date) for g in gs + nxt]
        assert all(g.feat("date", "corrected") and g.feat("date", "tz") == "+09:00" for g in gs + nxt)
        assert nxt[0].date == datetime(2026, 3, 12, 0, 7)
        log("时间校正：3/11 三张 +1:00、时区改为 +09:00（23:07 那张跨天变为 3/12 00:07）")

        # ---- 手动指定地点 ----
        select_days({"2026-03-11"})
        page.place_edit.setText("日本/奈良")
        page._resolve_place()
        yield 100
        assert "奈良" in page.place_res.text(), page.place_res.text()
        page.b_place.click()
        yield 200
        while ctx._workers:
            yield 150
        gs = by_day("2026-03-11")
        assert all(g.feat("geo", "source") == "manual" and "奈良" in (g.feat("geo", "province") or "") for g in gs)
        select_days({"2026-03-11"})
        yield 200
        shot("tp_03_manual_place")
        log("手动地点：3/11 → " + "/".join(x for x in (gs[0].feat("geo", "country"), gs[0].feat("geo", "province"),
                                                     gs[0].feat("geo", "city")) if x))

        # ---- 按行程批量指定（只补没有地点的：3/8~3/10 已有轨迹地点，不应被覆盖）----
        QTimer.singleShot(0, page.b_itin.click)
        yield 500
        dlg = modal()
        assert dlg is not None and "行程" in dlg.windowTitle()
        lines = ["2026-03-08 ~ 2026-03-10 日本/神户", "3/12 奈良", "2007-09-14 日本/横滨", "乱写一行", "2026-03-13 乌有之乡"]
        dlg.edit.setPlainText("\n".join(lines))
        dlg._parse()
        yield 200
        shot("tp_04_itinerary", dlg)
        assert len(dlg._plan) == (2 if data["cr2"] else 1), dlg._plan
        errs = [r for r in dlg.model.rows if "看不懂" in str(r[4]) or "国家" in str(r[4])]
        assert len(errs) == 2, dlg.model.rows
        dlg._apply()
        yield 200
        while ctx._workers:
            yield 150
        assert all(g.feat("geo", "source") == "track" for g in by_day("2026-03-08"))
        assert by_day("2026-03-12")[0].feat("geo", "source") == "manual"
        if data["cr2"]:
            pair = next(g for g in lib.groups if g.stem == "IMG_4001")
            assert pair.feat("geo", "source") == "manual" and "神奈川" in (pair.feat("geo", "province") or ""), \
                pair.feats.get("geo")
            log("行程批量：2007-09-14 → 横滨（已有轨迹地点的 3/8~3/10 未被覆盖）")

        # ---- 写入 XMP（全部照片；只有 RAW 会生成）----
        page.days.clearSelection()
        page.grid.clearSelection()
        yield 100
        if data["cr2"]:
            QTimer.singleShot(0, page.b_xmp.click)
            yield 600
            dlg = modal()
            assert dlg is not None and "XMP" in dlg.windowTitle(), dlg
            shot("tp_05_xmp_dialog", dlg)
            dlg.accept()
            for _ in range(60):
                yield 200
                m = modal()
                if m is not None and "写入完成" in m.windowTitle():
                    shot("tp_06_xmp_done", m)
                    m.accept()
                    break
            xmp = os.path.splitext(data["cr2"])[0] + ".xmp"
            assert os.path.exists(xmp), "没有生成 .xmp"
            text = open(xmp, encoding="utf-8").read()
            assert "横滨" in text or "神奈川" in text, text[:400]
            pair = next(g for g in lib.groups if g.stem == "IMG_4001")
            assert any(f.path == xmp for f in pair.files), "新 .xmp 没有加入照片组"
            log("写入 XMP：IMG_4001.xmp（地点名称）已生成并加入照片组")
            b = ctx.journal.batches()[0]
            assert b["operation"] == "xmp" and b["status"] == "done"
            win.go(win.page_ids.index("history"))
            yield 400
            shot("tp_07_history")
            res = ctx.mover.undo(b["id"])
            lib.apply_moves(res.mapping)
            assert lib.prune_missing() == 1 and not os.path.exists(xmp)
            ctx.files_changed.emit()
            log("撤销 XMP 批次：.xmp 已放入回收站，照片组恢复")
            win.go(win.page_ids.index("timeplace"))
            yield 300

        # ---- 分区计划能看到校正与地点 ----
        from photocrate.ops.planner import build_plan
        from photocrate.partition.base import PartitionContext
        from photocrate.partition.engine import PartitionSpec, Rule
        plan = build_plan(lib.groups, PartitionSpec("rules", [Rule("date", {"granularity": "ymd"}),
                                                              Rule("region", {"level": "province/city"})]),
                          PartitionContext(s.snapshot()), os.path.join(HOME, "out"))
        folders = sorted({it.folder for it in plan.items})
        log("分区预览：" + "；".join(folders))
        assert any("2026-03-11" in f and "奈良" in f for f in folders)
        assert any("2026-03-10" in f and "大阪" in f for f in folders)

        # ---- 清除全部标注 ----
        page.days.selectAll()
        yield 100
        page.b_place_clear.click()
        yield 200
        while ctx._workers:
            yield 150
        page.days.selectAll()
        yield 100
        page.b_shift_clear.click()
        yield 200
        while ctx._workers:
            yield 150
        assert lib.annotations.count("places") == 0 and lib.annotations.count("timeshift") == 0
        assert not any(g.feat("date", "corrected") for g in lib.groups)
        log("清除：手动地点与时间校正全部移除")
        page.days.clearSelection()
        select_days({"2026-03-09"})
        yield 300
        shot("tp_08_final")
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
            shot("tp_FAILED")
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
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "light",
                  sys.argv[3] if len(sys.argv) > 3 else None))
