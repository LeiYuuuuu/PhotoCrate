"""修图的 AI 抠图：模型登记 / 下载（子文件夹、校验）、对象选择（SAM 2.1）、选择主体（BiRefNet）、人像（MODNet）、
天空（U²-Net）、人像虚化，以及没有模型时退回旧方法。python -m pytest tests/test_matting.py -q

用到模型的测试需要 PHOTOCRATE_MODELS 指向放着这几个模型的文件夹（和 models.py 里的文件名一致，SAM 在 sam21_tiny\\ 里），
没有时跳过；其余的不需要模型。图都是合成的。
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

from photocrate.ai import matting, models  # noqa: E402
from photocrate.edit import paint, sky  # noqa: E402
from photocrate.edit.io import _new_doc  # noqa: E402

AI_KEYS = ("sam", "subject", "portrait", "sky")


def _need(key):
    return pytest.mark.skipif(not matting.ready(key), reason=f"没有 {key} 模型（设置 PHOTOCRATE_MODELS）")


def _iou(a, b):
    a, b = a > 127, b > 127
    return (a & b).sum() / max(1, (a | b).sum())


def _scene(w=640, h=480):
    """灰色渐变背景上一个红色圆盘（物体），上面三分之一是蓝天。"""
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3), np.uint8)
    img[..., 0] = 90 + (xx * 40 // w)
    img[..., 1] = 100 + (yy * 30 // h)
    img[..., 2] = 95
    disk = (xx - w * 0.5) ** 2 + (yy - h * 0.6) ** 2 < (h * 0.2) ** 2
    img[disk] = (200, 30, 40)
    return img, disk


# ---- 模型登记与下载 --------------------------------------------------------------

def test_registry_consistent():
    for key in AI_KEYS:
        m = models.MODELS[key]
        assert m["label"] and m["license"]
        for f in m["files"] + m["optional"]:
            urls, size = models.FILES[f]
            assert size > 0 and urls and all(u.startswith("https://") for u in urls)
            assert len(models.SHA256[f]) == 64
            assert any("hf-mirror.com" in u for u in urls)          # 国内能用镜像
    # SAM 的 .onnx 按原文件名引用旁边的 .onnx_data：两个必须在同一个子文件夹里、名字不能改
    sam = models.MODELS["sam"]["files"]
    for f in sam:
        assert f.startswith("sam21_tiny/")
        if f.endswith(".onnx"):
            assert f + "_data" in sam


def test_download_into_subfolder_with_checksum(tmp_path, monkeypatch):
    """下载到子文件夹、按“相对路径”核对 SHA-256；校验不对的文件不会留下。"""
    src = tmp_path / "src"
    src.mkdir()
    good, bad = b"model-bytes" * 300, b"tampered" * 300
    (src / "a.onnx").write_bytes(good)
    (src / "b.onnx").write_bytes(bad)
    dst = tmp_path / "models"
    monkeypatch.setenv("PHOTOCRATE_MODELS", str(dst))
    monkeypatch.setitem(models.FILES, "sub/a.onnx", ([(src / "a.onnx").as_uri()], len(good)))
    monkeypatch.setitem(models.FILES, "sub/b.onnx", ([(src / "b.onnx").as_uri()], len(bad)))
    monkeypatch.setitem(models.SHA256, "sub/a.onnx", hashlib.sha256(good).hexdigest())
    monkeypatch.setitem(models.SHA256, "sub/b.onnx", hashlib.sha256(good).hexdigest())   # 故意不对
    monkeypatch.setitem(models.MODELS, "t1", {"label": "t", "files": ["sub/a.onnx"], "optional": [], "license": "x"})
    monkeypatch.setitem(models.MODELS, "t2", {"label": "t", "files": ["sub/b.onnx"], "optional": [], "license": "x"})
    assert models.download_size("t1") == len(good)
    assert models.download("t1") == ["sub/a.onnx"]
    assert (dst / "sub" / "a.onnx").read_bytes() == good and models.ready("t1")
    assert models.download_size("t1") == 0
    with pytest.raises(IOError):
        models.download("t2")
    assert not (dst / "sub" / "b.onnx").exists() and not list((dst / "sub").glob("*.part"))


# ---- 没有模型时退回旧方法 -----------------------------------------------------

def test_sky_falls_back_without_model(monkeypatch):
    img, _ = _scene()
    img[:160] = (110, 160, 230)
    monkeypatch.setattr(matting, "ready", lambda key: False)
    m = sky.detect(img)
    assert m.shape == img.shape[:2] and m.dtype == np.uint8
    assert (m[:120] > 127).mean() > 0.9 and (m[300:] > 127).mean() < 0.05
    assert np.array_equal(m, sky.detect(img, model=False))


def test_matte_found_ignores_specks():
    m = np.zeros((600, 800), np.uint8)
    assert not paint.matte_found(m)
    for y, x in ((50, 60), (300, 500), (520, 120)):                       # 零星几小块（没人的照片上常见）
        m[y:y + 20, x:x + 20] = 255
    assert not paint.matte_found(m)
    assert paint.matte_found(m, 0.0005)
    m[200:420, 300:420] = 255                                             # 一个人那么大的一块
    assert paint.matte_found(m)


# ---- 人像虚化（只用给定的 alpha，不需要模型）-----------------------------------

@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
def test_portrait_blur_keeps_person_and_blurs_background(dtype):
    h, w = 300, 400
    rng = np.random.default_rng(1)
    top = 65535 if dtype == np.uint16 else 255
    px = np.zeros((h, w, 4), dtype)
    px[..., :3] = (rng.random((h, w, 3)) * top).astype(dtype)          # 背景：高频噪点
    px[..., 3] = top
    alpha = np.zeros((h, w), np.uint8)
    alpha[100:220, 150:250] = 255
    px[100:220, 150:250, :3] = (top, 0, 0)                               # 人：纯红
    src = px.copy()
    out = paint.portrait_blur(px, alpha, 12)
    assert np.array_equal(px, src)                                        # 原数组不动
    assert out.dtype == dtype and out.shape == px.shape
    assert np.array_equal(out[100:220, 150:250], px[100:220, 150:250])  # 人原样
    assert np.array_equal(out[..., 3], px[..., 3])
    bgo, bgi = out[:80, :, :3].astype(np.float64), px[:80, :, :3].astype(np.float64)
    assert bgo.std() < bgi.std() * 0.3                                    # 背景糊了
    # 人旁边的背景不该被红色“晕”到：红色通道不比绿、蓝明显高
    ring = out[90:98, 150:250, :3].astype(np.float64) / top
    assert ring[..., 0].mean() - ring[..., 1:].mean() < 0.08


# ---- 用到模型的 -------------------------------------------------------------------

@_need("sam")
def test_sam_click_and_box_select_object():
    img, disk = _scene()
    doc = _new_doc(np.dstack([img, np.full(img.shape[:2], 255, np.uint8)]))
    m = paint.ai_object_select_doc(doc, [(320, 290)], [1])
    assert m.shape == (doc.height, doc.width) and m.dtype == np.uint8
    assert _iou(m, disk * 255) > 0.85
    b = paint.ai_object_select_doc(doc, box=(210, 180, 430, 400))
    assert _iou(b, disk * 255) > 0.85
    # 同一张图接着点：编码走缓存
    key = matting._sam_cache["key"]
    paint.ai_object_select_doc(doc, [(320, 300)], [1])
    assert matting._sam_cache["key"] == key


@_need("subject")
def test_subject_matte_finds_object():
    img, disk = _scene()
    a = matting.subject(img)
    assert a.shape == img.shape[:2] and 0 <= a.min() and a.max() <= 1
    assert _iou((a * 255).astype(np.uint8), disk * 255) > 0.7


@_need("portrait")
def test_portrait_matte_shape_and_range():
    img, _ = _scene(500, 380)
    a = matting.portrait(img)
    assert a.shape == img.shape[:2] and a.dtype == np.float32
    assert 0 <= a.min() and a.max() <= 1


@_need("sky")
def test_sky_model_finds_sky():
    img, _ = _scene()
    yy = np.arange(160)[:, None]
    img[:160] = np.stack([100 + yy * 0 + 10, 150 + yy // 4, 225 + yy * 0], -1).astype(np.uint8)
    m = sky.detect(img)
    assert (m[:120] > 127).mean() > 0.8 and (m[300:] > 127).mean() < 0.05
