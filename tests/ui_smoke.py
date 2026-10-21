"""界面冒烟测试：无界面模式启动 → 扫描样例 → 依次截图每个页面。

用法（Linux 下）：QT_QPA_PLATFORM=offscreen python tests/ui_smoke.py <样例目录> <截图输出目录> [theme]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(samples: str, out: str, theme_name: str = "light") -> None:
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
    app = QApplication(sys.argv)
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("source_dirs", [samples])
    s.set("target_dir", os.path.join(samples, "已整理"))
    s.set("partition_rules", [{"dim": "date", "opts": {"granularity": "y/ym"}},
                              {"dim": "region", "opts": {"level": "province/city"}}])
    theme.apply(app, theme_name)
    lib = Library(s)
    ctx = AppContext(s, lib, Journal(journal_path()))
    win = MainWindow(ctx)
    win.resize(1560, 960)
    win.show()


    def shot(name):
        def f():
            app.processEvents()
            win.grab().save(os.path.join(out, f"{theme_name}_{name}.png"))
        return f

    def scan():
        src = win.page("source")
        src.start_scan()

    def wait_scan():
        if ctx.busy:
            QTimer.singleShot(300, wait_scan)
            return
        run_steps()

    def go(pid):
        return lambda: win.go(win.page_ids.index(pid))

    def select_some():
        src = win.page("source")
        src.grid.select_gids({g.gid for g in lib.groups[:5]})

    def drill():
        ov = win.page("overview")
        ov._drill("warmth", "暖色调")

    def tree_pick():
        pp = win.page("partition")
        top = pp.tree.topLevelItem(0)
        if top and top.childCount():
            ch = top.child(min(1, top.childCount() - 1))
            pp.tree.setCurrentItem(ch)

    seq = [
        (select_some, 200), (shot("01_source"), 300),
        (go("partition"), 300), (tree_pick, 300), (shot("02_partition"), 300),
        (go("overview"), 300), (drill, 300), (shot("03_overview"), 300),
        (go("culling"), 300), (shot("04_culling"), 300),
        (go("blur"), 300), (shot("04_blur"), 300),
        (go("history"), 300), (shot("05_history"), 300),
        (go("settings"), 300), (shot("06_settings"), 300),
        (app.quit, 100),
    ]

    def run_steps():
        t = 0
        for fn, delay in seq:
            t += delay
            QTimer.singleShot(t, fn)

    QTimer.singleShot(300, scan)
    QTimer.singleShot(900, wait_scan)
    app.exec()
    print("screenshots saved to", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "light")
