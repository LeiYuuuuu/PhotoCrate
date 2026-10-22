"""“分完一次，重新扫描，再按新需求分”的界面自动化测试（无界面模式）。

用法（Linux）：QT_QPA_PLATFORM=offscreen python tests/ui_repartition.py <输出目录> [light|dark]
流程：照片在“卡”文件夹 → 分区页按日期移动到“整理” → 数据源自动跟到“整理” → 重新扫描（缓存命中）
     → 改成按相机再分（旧日期文件夹被清理）→ 同样规则再生成计划（全部已在目标位置）
     → 在「操作记录」先选第一次的批次撤销：提示依赖，依次撤销后照片回到“卡”。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME = tempfile.mkdtemp(prefix="pc_ui_rp_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")


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
    from test_repartition import _media, _shoot

    os.makedirs(out, exist_ok=True)
    src, tgt = Path(HOME) / "卡", Path(HOME) / "整理"
    _shoot(src)
    before = _media(src)
    app = QApplication(sys.argv)
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("parallel_mode", "thread")
    s.set("exiftool_path", os.environ.get("PHOTOCRATE_TEST_EXIFTOOL", ""))
    s.set("source_dirs", [str(src)])
    s.set("target_dir", str(tgt))
    s.set("operation", "move")
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

    def partition(rules, shot_name):
        pp = win.page("partition")
        win.go(win.page_ids.index("partition"))
        yield 300
        pp.apply_preset_rules(rules)
        pp.target.setText(str(tgt))
        pp.op_move.setChecked(True)
        yield 300
        QTimer.singleShot(0, pp.b_plan.click)
        dlg = yield from wait_modal("计划")
        plan = dlg.plan
        shot(shot_name, dlg)
        if not plan.items:
            dlg.reject()
            return plan, None
        dlg.accept()
        m = yield from wait_modal("执行完成")
        text = m.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)
        msg = " ".join(t.text() for t in text)
        m.reject()
        yield from wait_idle()
        return plan, msg

    def script():
        sp = win.page("source")
        sp.start_scan()
        yield from wait_idle()
        assert len([g for g in lib.groups if g.files]) == 5

        # ---- 第一次：按日期 ----
        plan, msg = yield from partition([{"dim": "date", "opts": {"granularity": "ymd"}}], "rp_01_plan_date")
        assert len(plan.items) == 5 and "加入「资源接入」的数据源" in msg, msg
        assert sp._dirs() == [str(src), str(tgt)] and s.get("source_dirs") == [str(src), str(tgt)]
        assert not (src / "DCIM").exists() and src.is_dir()
        log("第一次按日期移动到“整理”：数据源自动加上“整理”，搬空的 卡/DCIM 已清理（卡 本身保留）")

        # ---- 重新扫描 ----
        win.go(win.page_ids.index("source"))
        sp.start_scan()
        yield from wait_idle()
        assert len([g for g in lib.groups if g.files]) == 5, "重新扫描后照片不见了"
        shot("rp_02_rescan")
        log("重新扫描：5 组照片都在（从“整理”读到）")

        # ---- 第二次：按相机 ----
        plan, msg = yield from partition([{"dim": "camera", "opts": {"level": "model"}}], "rp_03_plan_camera")
        assert len(plan.items) == 5 and "搬空的旧文件夹" in msg, msg
        tops = sorted(os.listdir(tgt))
        assert len(tops) == 2 and not any("2026" in t for t in tops), tops
        log(f"第二次按相机重新分：整理/ 下只剩 {tops}，旧日期文件夹已清理")

        # ---- 同样规则再来一次 ----
        plan, _ = yield from partition([{"dim": "camera", "opts": {"level": "model"}}], "rp_04_plan_again")
        assert not plan.items and plan.skipped_inplace == 5
        log("同样规则再生成计划：5 组都已在目标位置，没有要移动的")

        # ---- 撤销：先选第一次 ----
        hp = win.page("history")
        win.go(win.page_ids.index("history"))
        yield 400
        moves = [b for b in ctx.journal.batches() if b["operation"] == "move"]
        first = min(moves, key=lambda b: b["id"])
        hp.table.selectRow([x["id"] for x in hp._batches].index(first["id"]))
        yield 200
        QTimer.singleShot(0, hp._undo)
        m = yield from wait_modal(f"撤销批次 #{first['id']}")
        shot("rp_05_undo_chain", m)
        btn = next(b for b in m.findChildren(__import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton)
                   if "依次撤销" in b.text())
        btn.click()
        m = yield from wait_modal("撤销完成")
        m.accept()
        yield from wait_idle()
        assert _media(src) == before, _media(src)
        assert not _media(tgt)
        assert all(ctx.journal.batch(b["id"])["status"] == "undone" for b in moves)
        log("先撤销第一次：提示依赖 → 依次撤销 2 个批次，照片全部回到“卡”的原位置")
        shot("rp_06_after_undo")
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
            shot("rp_FAILED", modal())
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
