"""自动同步：文件夹快照 / 监视（core/watch.py）、照片库增量同步（Library.sync）、界面控制器（ui/autosync.py）。

python -m pytest tests/test_autosync.py -q
"""
from __future__ import annotations

import os
import queue
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from photocrate.bootstrap import bootstrap  # noqa: E402
from photocrate.config import Settings  # noqa: E402

bootstrap(Settings(), load_user_plugins=False)

from photocrate.core import watch  # noqa: E402
from photocrate.core.scanner import scan  # noqa: E402
from photocrate.core.watch import Entry, FolderWatcher, build_groups, snapshot  # noqa: E402
from test_timeplace import make_jpeg  # noqa: E402


def _lib(tmp_path):
    from photocrate.core.library import Library
    tmp_path.mkdir(parents=True, exist_ok=True)
    s = Settings(tmp_path / "settings.json")
    s.set("parallel_mode", "thread")
    s.set("exiftool_path", "")
    s.set("ai_enabled", False)
    s.set("ocr_enabled", False)
    lib = Library(s)
    lib.exiftool_path = None
    return lib


def _photos(root: Path, n: int, start: int = 0, sub: str = "") -> list[Path]:
    return [make_jpeg(root / sub / f"IMG_{i:04d}.JPG", datetime(2024, 5, 1, 9, i % 60), color=(40 + i * 7 % 200, 90, 60))
            for i in range(start, start + n)]


def _members(groups) -> set[frozenset]:
    return {frozenset(os.path.normcase(f.path) for f in g.files) for g in groups if g.files}


def _snap(root: Path) -> dict:
    return snapshot([str(root)], True)


# ---------------------------------------------------------------------------
# 快照与分组
# ---------------------------------------------------------------------------

def test_build_groups_matches_scanner(tmp_path):
    root = tmp_path / "lib"
    _photos(root, 3)
    _photos(root, 2, 10, "sub")
    (root / "DSC0001.ARW").write_bytes(b"raw")
    make_jpeg(root / "DSC0001.JPG", datetime(2024, 5, 2, 10))
    (root / "DSC0001.ARW.xmp").write_text("<x/>", "utf-8")
    (root / "IMG_0000.AAE").write_text("aae", "utf-8")
    (root / "notes.txt").write_text("not a photo", "utf-8")
    (root / "half.jpg.pctmp").write_bytes(b"tmp")
    (root / ".hidden").mkdir()
    make_jpeg(root / ".hidden" / "x.jpg", datetime(2024, 1, 1))
    snap = _snap(root)
    assert not any(k.endswith((".txt", ".pctmp")) or ".hidden" in k for k in snap)
    a = build_groups(snap)
    b = scan([str(root)], True)
    assert _members(a) == _members(b)
    assert {os.path.normcase(g.primary) for g in a} == {os.path.normcase(g.primary) for g in b}
    pair = next(g for g in a if g.has_raw)
    assert pair.kind == "raw+jpeg" and len(pair.files) == 3


def test_snapshot_keeps_offline_root(tmp_path):
    root = tmp_path / "usb"
    _photos(root, 2)
    first = _snap(root)
    shutil.rmtree(root)
    assert _snap(root) == {}
    assert snapshot([str(root)], True, keep=first) == first          # 根文件夹不在了：沿用原来的记录
    other = tmp_path / "other"
    _photos(other, 1)
    both = snapshot([str(root), str(other)], True, keep=first)
    assert len(both) == 3


# ---------------------------------------------------------------------------
# 照片库增量同步
# ---------------------------------------------------------------------------

def test_sync_from_empty_equals_scan(tmp_path):
    root = tmp_path / "lib"
    _photos(root, 4)
    a = _lib(tmp_path / "a")
    rep = a.sync(_snap(root), [str(root)])
    assert rep.added == 4 and rep.removed == 0 and rep.changed
    b = _lib(tmp_path / "b")
    b.scan([str(root)], True)
    assert _members(a.groups) == _members(b.groups)
    assert all(g.gid == i for i, g in enumerate(a.groups))
    assert all(g.thumb and g.date for g in a.groups)
    assert a.roots == [str(root)]
    # 什么都没变：不重新分析，照片对象原样保留
    objs = list(a.groups)
    rep = a.sync(_snap(root), [str(root)])
    assert not rep.changed and all(x is y for x, y in zip(objs, a.groups))


def test_sync_add_remove_update_keeps_gids(tmp_path):
    root = tmp_path / "lib"
    ps = _photos(root, 5)
    lib = _lib(tmp_path)
    lib.scan([str(root)], True)
    by_name = {g.name: g for g in lib.groups}
    keep = by_name["IMG_0000.JPG"]
    gone_gid = by_name["IMG_0001.JPG"].gid
    xmp_gid = by_name["IMG_0002.JPG"].gid
    lib.selected = {gone_gid, keep.gid}
    ps[1].unlink()
    (root / "IMG_0002.xmp").write_text("<x/>", "utf-8")
    new = _photos(root, 2, 20)
    seen = []
    rep = lib.sync(_snap(root), [str(root)], lambda pg: seen.append(pg.stage))
    assert (rep.added, rep.removed, rep.updated) == (2, 1, 1)
    assert "ready" in seen and seen[-1] == "done"
    assert lib.by_gid(keep.gid) is keep                            # 没变的照片：同一个对象、同一个编号
    assert lib.by_gid(gone_gid).files == []                        # 删掉的：留空位，编号不挪
    upd = lib.by_gid(xmp_gid)
    assert upd.name == "IMG_0002.JPG" and any(f.path.endswith(".xmp") for f in upd.files)
    assert upd.thumb and upd.date                                  # 换了新对象也带着缓存的分析结果
    added = [g for g in lib.groups if g.gid >= 5]
    assert sorted(g.name for g in added) == [p.name for p in new]
    assert all(g.thumb and g.date for g in added)
    assert all(g.gid == i for i, g in enumerate(lib.groups))
    assert lib.selected == {keep.gid}
    assert len([g for g in lib.groups if g.files]) == 6


def test_sync_leaves_unsettled_files_alone(tmp_path):
    root = tmp_path / "lib"
    ps = _photos(root, 3)
    lib = _lib(tmp_path)
    lib.scan([str(root)], True)
    g0 = next(g for g in lib.groups if g.name == "IMG_0000.JPG")
    snap = _snap(root)
    k0 = os.path.normcase(str(ps[0]))
    # 正在被覆盖的文件：快照里还是旧的记录 + unsettled → 这张照片先不动
    snap2 = dict(snap)
    snap2[k0] = Entry(str(ps[0]), 1, 1)
    rep = lib.sync(snap2, [str(root)], unsettled=frozenset({k0}))
    assert not rep.changed and lib.by_gid(g0.gid) is g0
    # 还在拷贝的新文件不会被加进来
    newp = _photos(root, 1, 30)[0]
    knew = os.path.normcase(str(newp))
    rep = lib.sync(_snap(root), [str(root)], unsettled=frozenset({knew}))
    assert not rep.changed
    rep = lib.sync(_snap(root), [str(root)])
    assert rep.added == 1


def test_sync_folder_outside_sources_is_dropped(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _photos(a, 2)
    _photos(b, 3, 10)
    lib = _lib(tmp_path)
    lib.scan([str(a), str(b)], True)
    rep = lib.sync(snapshot([str(a)], True), [str(a)])             # 从数据源里移除了 b
    assert rep.removed == 3 and len([g for g in lib.groups if g.files]) == 2


def test_sync_cross_folder_pair(tmp_path, monkeypatch):
    from photocrate.core import pairing
    monkeypatch.setattr(pairing, "same_shot",
                        lambda x, y: os.path.normcase(x.folder) != os.path.normcase(y.folder))
    ra, jb = tmp_path / "raw", tmp_path / "jpg"
    ra.mkdir()
    (ra / "DSC1.ARW").write_bytes(b"raw")
    make_jpeg(jb / "DSC1.JPG", datetime(2024, 5, 3, 8))
    roots = [str(ra), str(jb)]
    lib = _lib(tmp_path)
    lib.sync(snapshot(roots, True), roots)
    live = [g for g in lib.groups if g.files]
    assert len(live) == 1 and live[0].cross_folder
    gid = live[0].gid
    rep = lib.sync(snapshot(roots, True), roots)                   # 快照里是两组，但和照片库里的配对一致
    assert not rep.changed
    (ra / "DSC1.ARW.xmp").write_text("<x/>", "utf-8")
    rep = lib.sync(snapshot(roots, True), roots)
    live = [g for g in lib.groups if g.files]
    assert len(live) == 1 and live[0].gid == gid and len(live[0].files) == 3
    assert rep.updated == 1 and rep.added == 0


# ---------------------------------------------------------------------------
# 监视线程
# ---------------------------------------------------------------------------

def _watcher(roots, **kw):
    q: queue.Queue = queue.Queue()
    w = FolderWatcher([str(r) for r in roots], True, q.put)
    w.SETTLE, w.POLL, w.RECENT = 0.2, 0.3, kw.get("recent", 0)
    w.start()
    return w, q


def _wait(q: queue.Queue, pred, timeout: float = 10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            ch = q.get(timeout=max(0.01, end - time.monotonic()))
        except queue.Empty:
            break
        if pred(ch):
            return ch
    return None


def test_watcher_reports_add_remove_modify(tmp_path):
    root = tmp_path / "w"
    ps = _photos(root, 2)
    w, q = _watcher([root])
    try:
        first = _wait(q, lambda c: c.initial)
        assert first is not None and len(first.files) == 2 and not first.unsettled
        newp = _photos(root, 1, 5, "deep/er")[0]
        ch = _wait(q, lambda c: c.added)
        assert ch is not None and ch.added == [str(newp)] and len(ch.files) == 3
        ps[0].unlink()
        ch = _wait(q, lambda c: c.removed)
        assert ch is not None and ch.removed == [str(ps[0])]
        time.sleep(0.05)
        with open(ps[1], "ab") as f:
            f.write(b"\0" * 64)
        ch = _wait(q, lambda c: c.modified)
        assert ch is not None and ch.modified == [str(ps[1])]
        w.poke(full=True)
        ch = _wait(q, lambda c: c.empty)
        assert ch is not None and len(ch.files) == 2
    finally:
        w.stop()
        w.join(5)


def test_watcher_holds_back_recent_files(tmp_path):
    root = tmp_path / "w"
    ps = _photos(root, 2)
    w, q = _watcher([root], recent=60)
    try:
        first = _wait(q, lambda c: c.initial)
        assert first is not None and first.files == {} and len(first.unsettled) == 2
        ch = _wait(q, lambda c: len(c.files) == 2)
        assert ch is not None and sorted(ch.added) == sorted(str(p) for p in ps) and not ch.unsettled
    finally:
        w.stop()
        w.join(5)


@pytest.mark.skipif(os.name != "nt", reason="只有 Windows 能判断文件是否还开着在写")
def test_watcher_waits_until_copy_finishes(tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    w, q = _watcher([root])
    try:
        assert _wait(q, lambda c: c.initial) is not None
        p = root / "BIG.JPG"
        f = open(p, "wb")
        try:
            f.write(b"\xff\xd8" + b"\0" * 4096)
            f.flush()
            assert watch.still_writing(str(p))
            assert _wait(q, lambda c: c.added, timeout=2.0) is None     # 还开着在写：不报
        finally:
            f.close()
        assert not watch.still_writing(str(p))
        ch = _wait(q, lambda c: c.added)
        assert ch is not None and ch.added == [str(p)]
    finally:
        w.stop()
        w.join(5)


@pytest.mark.skipif(os.name != "nt", reason="Windows 的变化通知")
def test_watcher_uses_change_notifications(tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    q: queue.Queue = queue.Queue()
    w = FolderWatcher([str(root)], True, q.put)
    w.SETTLE, w.RECENT = 0.2, 0                 # POLL / SAFETY 保持默认：只靠通知也要很快发现
    w.start()
    try:
        assert _wait(q, lambda c: c.initial) is not None
        assert w.notifying
        t0 = time.monotonic()
        _photos(root, 1)
        assert _wait(q, lambda c: c.added, timeout=5.0) is not None
        assert time.monotonic() - t0 < 2.5
    finally:
        w.stop()
        w.join(5)


# ---------------------------------------------------------------------------
# 界面控制器
# ---------------------------------------------------------------------------

def test_autosync_controller(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("PHOTOCRATE_HOME", str(tmp_path / "home"))
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal
    from photocrate.ui.context import AppContext

    root = tmp_path / "lib"
    _photos(root, 3)
    s = Settings(tmp_path / "settings.json")
    for k, v in {"parallel_mode": "thread", "exiftool_path": "", "ai_enabled": False, "ocr_enabled": False,
                 "source_dirs": [str(root)]}.items():
        s.set(k, v)
    lib = Library(s)
    lib.exiftool_path = None
    ctx = AppContext(s, lib, Journal(tmp_path / "journal.db"))
    changed = []
    ctx.library_changed.connect(lambda: changed.append(1))
    a = ctx.autosync

    def live():
        return [g for g in lib.groups if g.files]

    def pump(ms):
        # 不用 QTest.qWait：它等待时不放开 GIL，后台同步线程几乎跑不动（真正的程序用 app.exec()，没有这个问题）
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    def until(cond, ms=15000):
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            if cond():
                return True
            pump(20)
        return bool(cond())

    try:
        a.restart()
        assert a.state == "off" and not live()
        ctx.set_setting("auto_sync", True)                          # 打开 → 开始监视并按现状载入
        assert until(lambda: len(live()) == 3 and a.state == "watching" and a.worker is None)
        assert changed
        for w in (a.watcher,):
            w.SETTLE, w.POLL = 0.2, 0.3
        g0 = {g.gid: g for g in live()}
        _photos(root, 1, 10)
        assert until(lambda: len(live()) == 4 and a.worker is None)
        assert all(lib.by_gid(gid) is g for gid, g in g0.items())
        assert "新增 1 张" in a.describe() or "1 added" in a.describe()
        # 有任务在跑：先不同步，结束后补上
        ctx.busy = True
        (root / "IMG_0000.JPG").unlink()
        pump(1500)
        assert len(live()) == 4
        ctx.busy = False
        assert until(lambda: len(live()) == 3)
        # 关掉：不再跟着变
        ctx.set_setting("auto_sync", False)
        assert until(lambda: a.state == "off" and a.watcher is None)
        _photos(root, 1, 20)
        pump(1200)
        assert len(live()) == 3
    finally:
        ctx.shutdown()
        lib.close()
        ctx.journal.close()


def test_sync_strings_have_matching_placeholders():
    import re
    from photocrate.locale_en_sync import EN_SYNC
    ph = re.compile(r"\{\d*(?::[^}]*)?\}")
    assert all(sorted(ph.findall(k)) == sorted(ph.findall(v)) for k, v in EN_SYNC.items())
