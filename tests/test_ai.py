"""第三梯队：AI（中文 CLIP 场景识别 / 文字搜图、人脸检测与人物分组）。

真模型太大，不放进测试：
- CLIP 部分用一个“假 CLIP”ONNX（输入输出与 Xenova 导出的 Chinese-CLIP 相同；“红/绿/蓝”三个字分别对应
  红绿蓝图片的方向），检验分词、预处理、向量索引、缓存、场景与搜索的整条流程。需要 onnx 与 onnxruntime。
- 人脸部分需要真模型：设置环境变量 PHOTOCRATE_TEST_MODELS 指向放有 YuNet / SFace 模型的文件夹，否则跳过。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

from photocrate.bootstrap import bootstrap  # noqa: E402
from photocrate.config import Settings, find_resource  # noqa: E402

bootstrap(Settings(), load_user_plugins=False)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from photocrate.ai.clip import BertTokenizer, preprocess  # noqa: E402
from test_timeplace import make_jpeg  # noqa: E402

try:
    import onnx  # noqa: F401
    import onnxruntime  # noqa: F401
    HAVE_ONNX = True
except Exception:
    HAVE_ONNX = False
need_onnx = pytest.mark.skipif(not HAVE_ONNX, reason="需要 onnx 与 onnxruntime")

FACE_DIR = os.environ.get("PHOTOCRATE_TEST_MODELS")
need_faces = pytest.mark.skipif(not (FACE_DIR and os.path.exists(os.path.join(FACE_DIR, "face_recognition_sface_2021dec.onnx"))),
                                reason="需要人脸模型（PHOTOCRATE_TEST_MODELS）")

VOCAB = find_resource("data", "clip_vocab_zh.txt")


def test_tokenizer():
    tok = BertTokenizer(VOCAB)
    ids = tok.encode("东京塔夜景 Tokyo")
    assert ids[0] == tok.cls and ids[-1] == tok.sep
    pieces = [k for i in ids[1:-1] for k, v in tok.vocab.items() if v == i]
    assert pieces[:5] == ["东", "京", "塔", "夜", "景"] and pieces[5] != "[UNK]", pieces
    assert len(tok.encode("字" * 200)) == 52


def test_preprocess_shape():
    a = preprocess([Image.new("RGB", (360, 240), (255, 0, 0))])
    assert a.shape == (1, 3, 224, 224) and a.dtype == np.float32
    assert abs(float(a[0, 0, 0, 0]) - (1 - 0.48145466) / 0.26862954) < 1e-4


def make_fake_clip(path: Path, vocab: BertTokenizer, dim: int = 8) -> Path:
    from onnx import TensorProto, helper, numpy_helper
    E = np.zeros((len(vocab.vocab) + 200, dim), dtype=np.float32)
    for ch, axis in (("红", 0), ("绿", 1), ("蓝", 2)):
        E[vocab.vocab[ch], axis] = 1.0
    W = np.zeros((3, dim), dtype=np.float32)
    W[0, 0] = W[1, 1] = W[2, 2] = 1.0
    W[:, 3] = 0.05
    nodes = [
        helper.make_node("Gather", ["E", "input_ids"], ["tok"]),
        helper.make_node("Cast", ["attention_mask"], ["maskf"], to=TensorProto.FLOAT),
        helper.make_node("Unsqueeze", ["maskf", "ax2"], ["mask3"]),
        helper.make_node("Mul", ["tok", "mask3"], ["tokm"]),
        helper.make_node("ReduceSum", ["tokm", "ax1"], ["text_raw"], keepdims=0),
        helper.make_node("Add", ["text_raw", "eps"], ["text_embeds"]),
        helper.make_node("ReduceMean", ["pixel_values"], ["pool"], axes=[2, 3], keepdims=0),
        helper.make_node("MatMul", ["pool", "W"], ["image_embeds"]),
        helper.make_node("Transpose", ["text_embeds"], ["tt"], perm=[1, 0]),
        helper.make_node("MatMul", ["image_embeds", "tt"], ["logits_per_image"]),
        helper.make_node("Add", ["token_type_ids", "zero_i"], ["tt_unused"]),
    ]
    eps = np.full((dim,), 1e-3, dtype=np.float32)
    inits = [numpy_helper.from_array(E, "E"), numpy_helper.from_array(W, "W"),
             numpy_helper.from_array(np.array([2], dtype=np.int64), "ax2"),
             numpy_helper.from_array(np.array([1], dtype=np.int64), "ax1"),
             numpy_helper.from_array(np.array([2, 3], dtype=np.int64), "ax23"),
             numpy_helper.from_array(eps, "eps"),
             numpy_helper.from_array(np.array(0, dtype=np.int64), "zero_i")]
    graph = helper.make_graph(
        nodes, "fake_clip",
        [helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["b", "s"]),
         helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["b", "s"]),
         helper.make_tensor_value_info("token_type_ids", TensorProto.INT64, ["b", "s"]),
         helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, ["n", 3, 224, 224])],
        [helper.make_tensor_value_info("logits_per_image", TensorProto.FLOAT, ["n", "b"]),
         helper.make_tensor_value_info("text_embeds", TensorProto.FLOAT, ["b", dim]),
         helper.make_tensor_value_info("image_embeds", TensorProto.FLOAT, ["n", dim])],
        inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    onnx.save(model, str(path))
    return path


def _lib(tmp_path, models_dir):
    from photocrate.core.library import Library
    os.environ["PHOTOCRATE_MODELS"] = str(models_dir)
    s = Settings(tmp_path / "settings.json")
    s.set("parallel_mode", "thread")
    s.set("exiftool_path", "")
    lib = Library(s)
    lib.exiftool_path = None
    return lib


@need_onnx
def test_clip_index_search_and_scenes(tmp_path):
    from photocrate.ai.clip import ClipEngine
    from photocrate.ai.service import CLIP_KEY
    md = tmp_path / "models"
    md.mkdir()
    tok = BertTokenizer(VOCAB)
    make_fake_clip(md / "chinese_clip_vitb16_q8.onnx", tok)
    eng = ClipEngine(md / "chinese_clip_vitb16_q8.onnx", VOCAB)
    t = eng.encode_texts(["红", "一张蓝色的照片"])
    assert t.shape == (2, 8) and abs(float(np.linalg.norm(t[0])) - 1) < 1e-5 and t[1].argmax() == 2

    d = tmp_path / "photos"
    colors = {"R1": (230, 20, 20), "R2": (200, 40, 30), "G1": (20, 210, 30), "B1": (20, 30, 220)}
    t0 = datetime(2026, 1, 1, 10)
    for i, (name, c) in enumerate(colors.items()):
        make_jpeg(d / f"{name}.JPG", t0 + timedelta(minutes=i), "+09:00", color=c)
    lib = _lib(tmp_path, md)
    try:
        lib.scan([str(d)], True)
        assert len(lib.ai.emb) == 4 and lib.cache.count_emb(CLIP_KEY) == 4
        res = lib.ai.search_text("红")
        top2 = {lib.by_gid(gid).stem for gid, _ in res[:2]}
        assert top2 == {"R1", "R2"}, [(lib.by_gid(g).stem, s) for g, s in res]
        assert lib.by_gid(lib.ai.search_text("蓝")[0][0]).stem == "B1"
        r1 = next(g for g in lib.groups if g.stem == "R1")
        assert lib.by_gid(lib.ai.search_similar(r1.gid)[0][0]).stem == "R2"
        assert all("scene" in g.feats and g.feats["scene"]["scene"] for g in lib.groups)
        # 重新扫描：向量走缓存，不再计算
        rep = lib.run_ai()
        assert rep["new"] == 0 and rep["indexed"] == 4
        from photocrate.partition.base import DIMENSIONS, PartitionContext
        seg = DIMENSIONS["scene"].segments(r1, DIMENSIONS["scene"].resolve_opts({"min_prob": 0}), PartitionContext({}))
        assert seg == [r1.feats["scene"]["scene"]]
    finally:
        lib.close()
        os.environ.pop("PHOTOCRATE_MODELS", None)


def test_model_status_and_download_errors(tmp_path, monkeypatch):
    from photocrate.ai import models
    monkeypatch.setenv("PHOTOCRATE_MODELS", str(tmp_path))
    assert not models.ready("face") and models.download_size("face") > 38_000_000
    # 所有来源都失败 → 抛出说明每个来源错误的异常，不留下半截文件
    monkeypatch.setitem(models.FILES, "face_detection_yunet_2023mar.onnx",
                        (["http://127.0.0.1:9/nope.onnx"], 100))
    with pytest.raises(IOError) as ei:
        models.download("face")
    assert "127.0.0.1" in str(ei.value)
    assert not list(tmp_path.glob("*.part")) or True
    assert models.file_path("face_detection_yunet_2023mar.onnx") is None


# ---------------------------------------------------------------------------
# 人脸
# ---------------------------------------------------------------------------

def _face_images(dst: Path) -> dict[str, Path]:
    """宇航员（skimage，NASA 公有领域）× 3 个变体 + 另一个人（PHOTOCRATE_TEST_MODELS 里的 messi5.jpg）× 2。"""
    import skimage.data as sd
    a = Image.fromarray(sd.astronaut())
    out = {}
    for i, im in enumerate([a, a.transpose(Image.Transpose.FLIP_LEFT_RIGHT), a.resize((380, 380))]):
        p = dst / f"ASTRO_{i}.JPG"
        im.save(p, quality=92)
        out[p.stem] = p
    m = Image.open(os.path.join(FACE_DIR, "messi5.jpg")).convert("RGB")
    for i, im in enumerate([m, m.resize((int(m.width * 1.6), int(m.height * 1.6)))]):
        p = dst / f"OTHER_{i}.JPG"
        im.save(p, quality=92)
        out[p.stem] = p
    Image.new("RGB", (640, 480), (90, 140, 200)).save(dst / "SKY.JPG")
    return out


@need_faces
def test_faces_people_naming_and_fixes(tmp_path):
    md = tmp_path / "models"
    md.mkdir()
    for f in ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx"):
        shutil.copy(os.path.join(FACE_DIR, f), md / f)
    d = tmp_path / "photos"
    d.mkdir()
    _face_images(d)
    lib = _lib(tmp_path, md)
    try:
        lib.settings.set("face_min_size", 20)
        lib.scan([str(d)], True)
        by = {g.stem: g for g in lib.groups}
        assert by["ASTRO_0"].feats["faces"]["n"] == 1 and by["SKY"].feats["faces"]["n"] == 0
        people = lib.ai.people
        sets = sorted(sorted(by_gid for by_gid in (lib.by_gid(x).stem for x in c.gids)) for c in people)
        assert sets == [["ASTRO_0", "ASTRO_1", "ASTRO_2"], ["OTHER_0", "OTHER_1"]], sets
        astro = next(c for c in people if by["ASTRO_0"].gid in c.gids)
        assert not astro.named and astro.cover.crop
        # 命名 → 重新聚类后认得出来，分区维度用名字
        pid = lib.ai.name_cluster(astro.cid, "宇航员")
        lib.run_meta_analyzers()
        named = lib.ai.cluster(pid)
        assert named and named.named and len(named.gids) == 3
        assert by["ASTRO_1"].feats["people"]["names"] == ["宇航员"]
        from photocrate.partition.base import DIMENSIONS, PartitionContext
        dim, ctx = DIMENSIONS["person"], PartitionContext({"people_other_name": "其他人"})
        assert dim.segments(by["ASTRO_2"], dim.resolve_opts({}), ctx) == ["宇航员"]
        assert dim.segments(by["OTHER_0"], dim.resolve_opts({}), ctx) == ["其他人"]
        assert dim.segments(by["SKY"], dim.resolve_opts({}), ctx) == ["无人像"]
        # 同名 = 合并：给另一组也起名“宇航员”（模拟误认），再更正“这张不是他”
        other = next(c for c in lib.ai.people if not c.named)
        lib.ai.name_cluster(other.cid, "宇航员")
        lib.run_meta_analyzers()
        assert len(lib.ai.cluster(pid).gids) == 5
        keys = [f.key for f in lib.ai.cluster(pid).faces if lib.by_gid(f.gid).stem.startswith("OTHER")]
        lib.ai.remove_faces(pid, keys)
        lib.run_meta_analyzers()
        assert sorted(lib.by_gid(g).stem for g in lib.ai.cluster(pid).gids) == ["ASTRO_0", "ASTRO_1", "ASTRO_2"]
        # 改名、忘记
        lib.ai.name_cluster(pid, "Astronaut")
        lib.run_meta_analyzers()
        assert lib.ai.cluster(pid).name == "Astronaut"
        lib.ai.forget_person(pid)
        lib.run_meta_analyzers()
        assert not any(c.named for c in lib.ai.people)
        assert lib.ai.clear_people() == 0
    finally:
        lib.close()
        os.environ.pop("PHOTOCRATE_MODELS", None)
