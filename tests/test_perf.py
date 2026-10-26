"""性能与稳定性优化的回归测试（v2.2.2）。python -m pytest tests/test_perf.py -q

- ExifTool：一批里很多读不了 / 不存在的文件时不会卡死；超时后能恢复
- 扫描进程池：已经在子进程里时不再套一层进程池（防止进程成倍增长）
- 地名反查：城市索引与查询结果在 GeoService 之间共用，调用方拿到的是副本
- 操作日志：按路径追踪用的索引
- 文件指纹有缓存，结果不变
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))


def _exiftool():
    from photocrate.core.metadata import find_exiftool
    return os.environ.get("PHOTOCRATE_TEST_EXIFTOOL") or find_exiftool()


def _within(seconds: float, fn):
    """在后台线程里跑 fn，超时就判失败（卡死时测试不会跟着永远停住）。"""
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("r", fn()), daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f"{seconds} 秒内没有返回（卡死了）"
    return box.get("r")


# ---------------------------------------------------------------------------
# ExifTool
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _exiftool(), reason="需要 ExifTool")
def test_many_unreadable_files_do_not_hang_the_reader(tmp_path):
    from photocrate.core.metadata import ExifToolBackend
    bad = []
    for i in range(200):                      # 每个都会让 ExifTool 写一行错误：以前写满 4 KB 的 stderr 管道就卡死
        p = tmp_path / f"long_name_to_make_the_error_line_longer_{i:04d}.ARW"
        p.write_bytes(b"\0" * 4096)
        bad.append(str(p))
    missing = [str(tmp_path / f"gone_{i:04d}.ARW") for i in range(200)]
    b = ExifToolBackend(_exiftool())
    try:
        out = _within(120, lambda: b.read_many(bad + missing))
        assert set(out) == set(bad)           # 坏文件也有结果（字段为空），不存在的没有
    finally:
        b.close()


@pytest.mark.skipif(not _exiftool(), reason="需要 ExifTool")
def test_reader_times_out_and_recovers(tmp_path):
    from photocrate.core.metadata import StayOpen
    from test_timeplace import make_jpeg
    from datetime import datetime
    jpg = str(make_jpeg(tmp_path / "a.JPG", datetime(2026, 1, 2, 3, 4, 5)))
    c = StayOpen(_exiftool(), ["-charset", "filename=utf8"])
    try:
        with pytest.raises(OSError):
            c.execute_json(["-j", *[jpg] * 50], timeout=0.001)
        r = _within(60, lambda: c.execute_json(["-j", "-DateTimeOriginal", jpg], timeout=30))
        assert r[0]["DateTimeOriginal"] == "2026:01:02 03:04:05"
    finally:
        c.close()


# ---------------------------------------------------------------------------
# 进程池
# ---------------------------------------------------------------------------

def test_no_nested_process_pool_inside_a_child_process(tmp_path, monkeypatch):
    import concurrent.futures as cf
    import multiprocessing as mp
    from test_culling import _library
    lib = _library(tmp_path)
    lib.settings.set("parallel_mode", "process")
    monkeypatch.setattr(mp, "parent_process", lambda: object())      # 假装自己是某个 spawn 出来的子进程
    tasks = [{"sig": f"s{i}", "gid": i, "path": str(tmp_path / f"x{i}.jpg")} for i in range(20)]
    try:
        pool, futures = lib._start_pool(tasks, threading.Event())
        assert isinstance(pool, cf.ThreadPoolExecutor)
        pool.shutdown(wait=True)
    finally:
        lib.close()


# ---------------------------------------------------------------------------
# 地名反查
# ---------------------------------------------------------------------------

def test_geo_index_and_results_are_shared_but_copied():
    from photocrate.core.geo import GeoService
    a, b = GeoService(), GeoService()
    if a._world_index() is None:
        pytest.skip("缺少 GeoNames 数据")
    assert a._world_index() is b._world_index()
    r1 = a.lookup(35.6586, 139.7454)          # 东京
    r1["city"] = "被调用方改掉"
    r2 = b.lookup(35.6586, 139.7454)
    assert r2["city"] != "被调用方改掉" and r2["country"]
    # GCJ-02 设置不同，结果分开缓存
    assert GeoService(True).lookup(39.9087, 116.3975)["country"] == "中国"


# ---------------------------------------------------------------------------
# 操作日志 / 指纹
# ---------------------------------------------------------------------------

def test_journal_has_path_indexes_and_uses_them(tmp_path):
    from photocrate.ops.journal import Journal
    j = Journal(tmp_path / "j.db")
    try:
        names = {r[0] for r in j._db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"ops_src_lower", "ops_dst_lower"} <= names
        plan = " ".join(r[-1] for r in j._db.execute(
            "EXPLAIN QUERY PLAN SELECT dst FROM ops WHERE lower(src) = lower(?)", ("x",)))
        assert "ops_src_lower" in plan
    finally:
        j.close()


def test_file_signature_cache_keeps_results():
    from photocrate.core.models import MemberFile, file_signature
    m = MemberFile("D:/a/IMG_1.JPG", "jpeg", 10, 20)
    s = m.sig
    assert s == file_signature("E:/b/img_1.jpg", 10, 20)       # 与路径、大小写无关
    m.path = "D:/a/IMG_2.JPG"                                   # 改名（apply_moves 会直接改 path）→ 指纹跟着变
    assert m.sig != s
    assert m.sig == file_signature("X:/IMG_2.JPG", 10, 20)


def test_search_text_hits_only_for_results():
    from photocrate.core import smart

    class _G:
        def __init__(self, gid):
            self.gid, self.files, self.date = gid, [1], None

    class _Idx:
        groups = [_G(1), _G(2)]
        text = {1: "发票", 2: "发票"}

        def match(self, tok):
            return {"text": {1, 2}} if tok == "发票" else {}

    r = smart.search(None, None, "发票", index=_Idx())
    assert sorted(r.gids) == [1, 2] and set(r.text_hits) == {1, 2}
    r = smart.search(None, None, "发票 东京", index=_Idx())
    assert not r.gids and not r.text_hits
