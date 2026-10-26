"""安全检查与 bug 修复的回归测试（v2.2.1）。python -m pytest tests/test_security.py -q

- ExifTool 参数注入：值 / 文件名里的换行不能变成额外的 ExifTool 参数
- 目录联接 / 硬链接：同一个文件经由两条路径出现，不能被当成“重复照片”删掉
- 崩溃恢复：源和目标都在时，逐字节相同才删除源文件
- 网页相册：标题 / 地名里的 HTML 不会进到页面里
- 导入：空间检查只算真正要复制的文件；跨盘统计只算跨盘的文件
- PSD：灰度 + 透明的合成图、损坏的文件头
- 模型下载：校验值不对的文件不使用，也不留下半截文件
"""
from __future__ import annotations

import io
import os
import struct
import sys
import tempfile
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

from photocrate.core.models import MemberFile, PhotoGroup  # noqa: E402


def _member(p: Path, role: str = "jpeg") -> MemberFile:
    st = p.stat()
    return MemberFile(str(p), role, st.st_size, st.st_mtime_ns)


def _group(gid: int, *members: MemberFile) -> PhotoGroup:
    return PhotoGroup(gid, list(members), members[0].path)


# ---------------------------------------------------------------------------
# ExifTool 参数注入
# ---------------------------------------------------------------------------

def test_exiftool_args_keep_each_value_on_one_line():
    from photocrate.ops.xmpwrite import exiftool_args
    args = exiftool_args({"XMP-xmp:Label": "Red\n-if\n1", "XMP-dc:Subject": ["东京\r\n-echo1\nx", "\n"]})
    assert all("\n" not in a and "\r" not in a for a in args)
    assert args[0] == "-XMP-xmp:Label=Red -if 1"
    assert len(args) == 3                        # 只剩空白的关键词不写


def _exiftool():
    from photocrate.core.metadata import find_exiftool
    return os.environ.get("PHOTOCRATE_TEST_EXIFTOOL") or find_exiftool()


@pytest.mark.skipif(not _exiftool(), reason="需要 ExifTool")
def test_write_xmp_value_with_newline_is_not_an_exiftool_argument(tmp_path):
    from photocrate.ops.journal import Journal
    from photocrate.ops.xmpwrite import XmpPlan, XmpTask, write_xmp
    raw = tmp_path / "DSC00001.ARW"
    raw.write_bytes(os.urandom(2000))
    xmp = tmp_path / "DSC00001.xmp"
    # 注入成功时 ExifTool 会把 -echo1 当成参数，色标只剩 “Red”
    plan = XmpPlan([XmpTask(0, str(raw), str(xmp), {"XMP-xmp:Label": "Red\n-echo1\nINJECTED"}, ["x"])])
    j = Journal(tmp_path / "j.db")
    try:
        res = write_xmp(plan, _exiftool(), j)
    finally:
        j.close()
    assert res.failed == 0, res.errors
    assert "Red -echo1 INJECTED" in xmp.read_text("utf-8")


def test_metadata_reader_skips_file_names_with_newlines(monkeypatch):
    from photocrate.core import metadata
    sent = []
    backend = object.__new__(metadata.ExifToolBackend)
    monkeypatch.setattr(backend, "_read_batch", lambda batch: sent.extend(batch) or [], raising=False)
    counted = []
    backend.read_many(["/a/ok.jpg", "/a/bad\n-if\n1.jpg"], progress=counted.append)
    assert sent == ["/a/ok.jpg"]
    assert counted == [2]


# ---------------------------------------------------------------------------
# 同一个文件经由两条路径出现
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="目录联接只有 Windows 有")
def test_scan_does_not_follow_junctions(tmp_path):
    import _winapi
    from photocrate.core.scanner import scan
    real = tmp_path / "src" / "2026"
    real.mkdir(parents=True)
    (real / "IMG_0001.JPG").write_bytes(b"\xff\xd8" + os.urandom(500))
    _winapi.CreateJunction(str(real), str(tmp_path / "src" / "link"))
    groups = scan([str(tmp_path / "src")])
    assert [os.path.basename(os.path.dirname(g.primary)) for g in groups] == ["2026"]


def test_duplicates_ignore_the_same_file_seen_twice(tmp_path):
    from photocrate.ops.dedupe import find_duplicates
    a = tmp_path / "a" / "IMG_0001.JPG"
    a.parent.mkdir()
    a.write_bytes(b"\xff\xd8" + os.urandom(3000))
    b = tmp_path / "b" / "IMG_0001.JPG"
    b.parent.mkdir()
    try:
        os.link(a, b)                            # 硬链接：两条路径，磁盘上只有一份
    except OSError:
        pytest.skip("这个文件系统不支持硬链接")
    res = find_duplicates([_group(0, _member(a)), _group(1, _member(b))])
    assert not res.sets
    # 真正的副本照样找得出来
    c = tmp_path / "c" / "IMG_0001.JPG"
    c.parent.mkdir()
    c.write_bytes(a.read_bytes())
    res = find_duplicates([_group(0, _member(a)), _group(2, _member(c))])
    assert [(s.keep, s.remove) for s in res.sets] == [(0, [2])]


# ---------------------------------------------------------------------------
# 崩溃恢复
# ---------------------------------------------------------------------------

def _move_batch(tmp_path, src: Path, dst: Path):
    from photocrate.ops.journal import Journal
    from photocrate.ops.planner import Plan, PlanItem, PlanOp
    st = src.stat()
    plan = Plan("test", str(dst.parent), "move", [PlanItem(0, [PlanOp(str(src), str(dst), st.st_size, st.st_mtime_ns, "jpeg")], ".")])
    j = Journal(tmp_path / "j.db")
    return j, j.create_batch(plan)


def test_reconcile_keeps_source_when_target_differs(tmp_path):
    from photocrate.ops.mover import Mover
    src, dst = tmp_path / "a.jpg", tmp_path / "t" / "a.jpg"
    dst.parent.mkdir()
    src.write_bytes(b"A" * 1000)
    dst.write_bytes(b"B" * 1000)                 # 同样大小、内容不同：不是这次复制出来的
    j, bid = _move_batch(tmp_path, src, dst)
    try:
        fixed = Mover(j).reconcile(bid)
        assert src.exists() and fixed["src_removed"] == 0 and fixed["pending"] == 1
        assert j.ops(bid)[0]["status"] == "pending"
    finally:
        j.close()


def test_reconcile_finishes_an_interrupted_cross_disk_move(tmp_path):
    from photocrate.ops.mover import Mover
    src, dst = tmp_path / "a.jpg", tmp_path / "t" / "a.jpg"
    dst.parent.mkdir()
    src.write_bytes(b"A" * 1000)
    dst.write_bytes(b"A" * 1000)                 # 复制完了、源文件还没删
    j, bid = _move_batch(tmp_path, src, dst)
    try:
        fixed = Mover(j).reconcile(bid)
        assert not src.exists() and dst.exists() and fixed["src_removed"] == 1
    finally:
        j.close()


# ---------------------------------------------------------------------------
# 网页相册
# ---------------------------------------------------------------------------

def test_album_json_cannot_break_out_of_the_script():
    from photocrate.ops.exporter import _album_html, _script_json
    s = _script_json({"title": "</script><img src=x onerror=alert(1)>", "p": "<!--<script>", "x": "a&b "})
    assert "<" not in s and ">" not in s and "&" not in s and " " not in s
    import json
    assert json.loads(s)["title"] == "</script><img src=x onerror=alert(1)>"
    page = _album_html()
    assert "bindTooltip(esc(" in page and "+ esc(d.date) +" in page


def test_album_title_placeholder_is_replaced_once(tmp_path, monkeypatch):
    from photocrate.ops import exporter
    out, _ = exporter.export_album([], str(tmp_path), "相册 __DATA__ <b>", exporter.ExportOptions())
    text = Path(out).read_text("utf-8")
    assert "<title>相册 __DATA__ &lt;b&gt;</title>" in text
    assert text.count("const D = {") == 1


# ---------------------------------------------------------------------------
# 导入的空间检查、跨盘统计
# ---------------------------------------------------------------------------

def _card(tmp_path, n: int) -> list[PhotoGroup]:
    card = tmp_path / "card"
    card.mkdir(exist_ok=True)
    out = []
    for i in range(n):
        p = card / f"IMG_{i:04d}.JPG"
        p.write_bytes(os.urandom(4000 + i))
        out.append(_group(i, _member(p)))
    return out


def test_import_space_check_ignores_already_imported(tmp_path, monkeypatch):
    import shutil
    from photocrate.config import Settings
    from photocrate.ops.importer import build_import_plan
    from photocrate.partition.base import PartitionContext
    groups = _card(tmp_path, 4)
    tgt = tmp_path / "lib"
    tgt.mkdir()
    for g in groups[:3]:                         # 前 3 张导入过
        shutil.copy2(g.primary, tgt / g.name)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: types.SimpleNamespace(total=1, used=1, free=0))
    ctx = PartitionContext(Settings().snapshot())
    plan = build_import_plan(groups[:3], str(tgt), "", ctx)
    assert not plan.items and not plan.errors, plan.errors
    # 有一张新的、磁盘又满了 → 才报空间不足
    plan = build_import_plan(groups, str(tgt), "", ctx)
    assert len(plan.items) == 1 and plan.errors


def test_plan_counts_only_cross_disk_files_as_needing_space(tmp_path, monkeypatch):
    from photocrate.config import Settings
    from photocrate.ops import planner
    from photocrate.partition.base import PartitionContext
    from photocrate.partition.engine import PartitionSpec
    far, near = tmp_path / "far", tmp_path / "near"
    far.mkdir()
    near.mkdir()
    (far / "IMG_1.ARW").write_bytes(os.urandom(5000))
    (near / "IMG_1.JPG").write_bytes(os.urandom(700))
    g = PhotoGroup(0, [_member(far / "IMG_1.ARW", "raw"), _member(near / "IMG_1.JPG")], str(near / "IMG_1.JPG"))
    real = planner.device_of
    monkeypatch.setattr(planner, "device_of", lambda p: -1 if "far" in os.path.basename(p) else real(p))
    plan = planner.build_plan([g], PartitionSpec("rules", []), PartitionContext(Settings().snapshot()),
                              str(tmp_path / "out"), verify_free_space=False)
    assert plan.cross_bytes == 5000


# ---------------------------------------------------------------------------
# PSD
# ---------------------------------------------------------------------------

def _psd(w: int, h: int, planes: list[bytes], mode: int = 1) -> bytes:
    hdr = b"8BPS" + struct.pack(">H", 1) + b"\0" * 6 + struct.pack(">HIIHH", len(planes), h, w, 8, mode)
    return hdr + struct.pack(">III", 0, 0, 0) + struct.pack(">H", 0) + b"".join(planes)


def test_psd_gray_with_alpha_composite(tmp_path):
    from photocrate.edit import psd
    gray = np.arange(12, dtype=np.uint8).reshape(3, 4) * 20
    alpha = np.full((3, 4), 128, np.uint8)
    p = tmp_path / "g.psd"
    p.write_bytes(_psd(4, 3, [gray.tobytes(), alpha.tobytes()]))
    px = psd.read(str(p)).layers[0].pixels
    for c in range(3):
        assert (px[..., c] == gray).all()
    assert (px[..., 3] == 128).all()


def test_psd_rejects_absurd_size(tmp_path):
    from photocrate.edit import psd
    p = tmp_path / "big.psd"
    p.write_bytes(_psd(400000, 400000, [b""]))
    with pytest.raises(psd.PSDError):
        psd.read(str(p))


# ---------------------------------------------------------------------------
# 模型下载的校验
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, data: bytes):
        self._b = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n: int) -> bytes:
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_model_download_rejects_wrong_checksum(tmp_path, monkeypatch):
    import hashlib
    from photocrate.ai import models
    monkeypatch.setenv("PHOTOCRATE_MODELS", str(tmp_path))
    data = b"\x08\x07" + os.urandom(5000)
    monkeypatch.setattr(models.urllib.request, "urlopen", lambda req, timeout=30: _Resp(data))
    name = "face_detection_yunet_2023mar.onnx"
    monkeypatch.setitem(models.FILES, name, (["https://example.invalid/x.onnx"], len(data)))
    monkeypatch.setitem(models.FILES, "face_recognition_sface_2021dec.onnx", (["https://example.invalid/y.onnx"], 1))
    (tmp_path / "face_recognition_sface_2021dec.onnx").write_bytes(os.urandom(2000))      # 已经有了，不下载
    with pytest.raises(IOError) as ei:
        models.download("face")
    assert "example.invalid" in str(ei.value)
    assert not (tmp_path / name).exists() and not list(tmp_path.glob("*.part"))
    # 校验值对得上 → 正常放进 models 文件夹
    monkeypatch.setitem(models.SHA256, name, hashlib.sha256(data).hexdigest())
    assert models.download("face") == [name]
    assert (tmp_path / name).read_bytes() == data


def test_cancelled_download_leaves_no_partial_file(tmp_path):
    import threading
    from photocrate.ai import models
    cancel = threading.Event()
    cancel.set()
    orig = models.urllib.request.urlopen
    models.urllib.request.urlopen = lambda req, timeout=30: _Resp(b"x" * 100)
    try:
        with pytest.raises(InterruptedError):
            models._fetch("https://example.invalid/x", tmp_path / "m.onnx", 0, 100, "m", None, cancel)
    finally:
        models.urllib.request.urlopen = orig
    assert not list(tmp_path.iterdir())


def test_pinned_checksums_cover_every_download():
    from photocrate.ai import models
    assert set(models.SHA256) == set(models.FILES)
    assert all(len(h) == 64 for h in models.SHA256.values())
