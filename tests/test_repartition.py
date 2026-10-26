"""分完一次之后再按新需求重新分：重新扫描、二次分区、重复执行、撤销顺序。

模拟真实用法：照片在“卡”文件夹 → 第一次按日期分到“整理”文件夹 → 重新扫描（数据源要跟着照片走）
→ 第二次改成按相机 / 星级分（旧的日期文件夹不应留下一堆空壳）→ 同样的规则再执行一次（应全部跳过）
→ 撤销：先撤第一次时应提示依赖，按从新到旧的顺序全部撤销后照片回到原处。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_culling import ET, _library  # noqa: E402
from test_timeplace import make_jpeg  # noqa: E402

from photocrate.ops.journal import Journal  # noqa: E402
from photocrate.ops.mover import Mover  # noqa: E402
from photocrate.ops.planner import build_plan, sources_after_move  # noqa: E402
from photocrate.partition.base import PartitionContext  # noqa: E402
from photocrate.partition.engine import PartitionSpec, Rule  # noqa: E402


def _media(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def _shoot(src: Path) -> None:
    for i, (day, model) in enumerate([(5, "ILCE-7CM2"), (5, "ILCE-7CM2"), (6, "ILCE-7CM2"), (6, "iPhone 15 Pro"),
                                      (7, "iPhone 15 Pro")]):
        make_jpeg(src / "DCIM" / f"IMG_{i:04d}.JPG", datetime(2026, 1, day, 10 + i), "+09:00",
                  make="Apple" if model.startswith("iPhone") else "SONY", model=model)
        if model.startswith("ILCE"):
            (src / "DCIM" / f"IMG_{i:04d}.ARW").write_bytes(os.urandom(3000 + i))
    (src / "DCIM" / "IMG_0000.xmp").write_text("<x/>", "utf-8")


def test_partition_again_after_rescan(tmp_path):
    src, tgt = tmp_path / "卡", tmp_path / "整理"
    _shoot(src)
    before = {p: (src / p).stat().st_size for p in _media(src)}
    lib = _library(tmp_path)
    j = Journal(tmp_path / "j.db")
    mv = Mover(j)
    sources = [str(src)]
    try:
        lib.scan(sources, True)
        assert len([g for g in lib.groups if g.files]) == 5
        g0 = next(g for g in lib.groups if g.stem == "IMG_0000")
        lib.set_rating([g0.gid], {"rating": 4})
        ctx = PartitionContext(lib.settings.snapshot())

        # ---- 第一次：按日期分到“整理” ----
        p1 = build_plan(lib.groups, PartitionSpec("rules", [Rule("date", {"granularity": "ymd"})]), ctx, str(tgt))
        r1 = mv.execute(p1, keep_roots=sources + [str(tgt)])
        lib.apply_moves(r1.mapping)
        assert r1.done_groups == 5 and not _media(src)
        assert os.path.isdir(src), "数据源文件夹本身要保留"
        assert not (src / "DCIM").exists(), "搬空的子文件夹应清理掉"

        # ---- 重新扫描：数据源要包含目标文件夹 ----
        sources = sources_after_move(sources, str(tgt))
        assert sources == [str(src), str(tgt)]
        rep = lib.scan(sources, True)
        # 移动不改指纹：元数据全部命中缓存（没有 ExifTool 时随机字节的 .ARW / .xmp 读不出元数据，只有 5 张 JPG 有缓存）
        assert rep.groups == 5 and rep.meta_cached >= (9 if ET else 5), rep
        g0 = next(g for g in lib.groups if g.stem == "IMG_0000")
        assert g0.feats["rating"]["rating"] == 4                      # 标注跟着照片走
        assert any(f.name == "IMG_0000.xmp" for f in g0.files)

        # ---- 第二次：改成按相机分（同一个目标文件夹）----
        p2 = build_plan(lib.groups, PartitionSpec("rules", [Rule("camera", {"level": "model"})]), ctx, str(tgt))
        assert len(p2.items) == 5 and not p2.renamed, (len(p2.items), p2.renamed)
        r2 = mv.execute(p2, keep_roots=sources)
        lib.apply_moves(r2.mapping)
        tops = sorted(os.listdir(tgt))
        assert all("2026" not in t for t in tops), f"旧的日期文件夹没清理：{tops}"
        assert len(tops) == 2, tops

        # ---- 同样的规则再执行一次：全部已在目标位置 ----
        p3 = build_plan(lib.groups, PartitionSpec("rules", [Rule("camera", {"level": "model"})]), ctx, str(tgt))
        assert not p3.items and p3.skipped_inplace == 5

        # ---- 撤销顺序：第一次的批次被第二次接着移动过 → 依赖提示 ----
        assert j.later_dependents(r1.batch_id) == [r2.batch_id]
        assert j.later_dependents(r2.batch_id) == []
        u2 = mv.undo(r2.batch_id)
        lib.apply_moves(u2.mapping)
        assert u2.failed_groups == 0
        assert j.later_dependents(r1.batch_id) == []
        u1 = mv.undo(r1.batch_id)
        assert u1.failed_groups == 0
        after = {p: (src / p).stat().st_size for p in _media(src)}
        assert after == before
        assert not _media(tgt)
    finally:
        lib.close()
        j.close()


def test_sources_after_move():
    def n(ps):                                    # Windows 上新加的目标是 E:\b，比较时统一斜杠
        return [os.path.normpath(p) for p in ps]
    assert n(sources_after_move(["D:/a"], "D:/a/整理")) == n(["D:/a"])             # 目标在数据源里面：不用加
    assert n(sources_after_move(["D:/a"], "E:/b")) == n(["D:/a", "E:/b"])
    assert n(sources_after_move(["D:/a/x", "D:/a/y"], "D:/a")) == n(["D:/a"])       # 目标包含原来的数据源：合并
    assert n(sources_after_move([], "E:/b")) == n(["E:/b"])
