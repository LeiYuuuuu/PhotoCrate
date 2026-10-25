"""修图进阶第三档（v2.0.0）：参考线、钢笔 / 形状、智能对象、动作 / 批处理、全景 / 景深、色彩管理、PSD、
画板 / 切片、时间轴、天空替换、皮肤平滑。不需要界面。"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from photocrate.edit import actions, anim, color, psd, sky, smart, stitch, vector   # noqa: E402
from photocrate.edit.document import Layer                                         # noqa: E402
from photocrate.edit.filters import run                                            # noqa: E402
from photocrate.edit.io import export_image, load_project, save_project            # noqa: E402
from test_editor_pro import _doc, _photo, _solid                                   # noqa: E402


def _textured(h=300, w=900, seed=0):
    import cv2
    rng = np.random.default_rng(seed)
    base = (rng.random((h // 8, w // 8, 3)) * 255).astype(np.uint8)
    base = cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC)
    yy, xx = np.mgrid[0:h, 0:w]
    return np.clip(base.astype(int) + (40 * np.sin(xx / 13.0) * np.cos(yy / 17.0))[..., None], 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# 参考线 / 画板 / 切片
# ---------------------------------------------------------------------------

def test_guides_undo_crop_and_roundtrip(tmp_path):
    d = _doc()
    d.add_guide("v", 100)
    d.add_guide("h", 50)
    d.move_guide(0, 120)
    assert d.guides == [("v", 120.0), ("h", 50.0)]
    d.move_guide(1, None)
    assert d.guides == [("v", 120.0)]
    d.history.undo()
    assert len(d.guides) == 2
    d.crop(20, 10, 200, 150)
    assert d.guides == [("v", 100.0), ("h", 40.0)]
    d.flip(True)
    assert d.guides[0] == ("v", 100.0)
    d.add_region("slice", 10, 10, 50, 40)
    d.add_region("artboard", 0, 0, 100, 150, "手机")
    p = str(tmp_path / "g.pcedit")
    save_project(d, p)
    d2 = load_project(p)
    assert d2.guides == d.guides and [r["name"] for r in d2.regions] == ["切片 1", "手机"]
    out = d2.export_regions("slice", str(tmp_path))
    from PIL import Image
    assert len(out) == 1 and Image.open(out[0]).size == (50, 40)


# ---------------------------------------------------------------------------
# 钢笔 / 形状
# ---------------------------------------------------------------------------

def test_shape_paths_and_masks():
    r = vector.shape_path({"type": "rect", "x0": 10, "y0": 10, "x1": 50, "y1": 30})
    m = vector.path_mask(r, 60, 40)
    assert m[20, 30] == 255 and m[5, 5] == 0 and abs((m > 127).sum() - 800) < 20
    e = vector.path_mask(vector.shape_path({"type": "ellipse", "x0": 0, "y0": 0, "x1": 100, "y1": 100}), 100, 100)
    assert abs((e > 127).mean() - np.pi / 4) < 0.02
    poly = vector.shape_path({"type": "polygon", "x0": 0, "y0": 0, "x1": 100, "y1": 100, "sides": 5})
    assert len(poly[0]["pts"]) == 5
    curve = [{"pts": [[0, 50, 0, 50, 30, 0], [100, 50, 70, 0, 100, 50]], "closed": True}]
    f = vector.flatten(curve[0])
    assert f[:, 1].min() < 20                               # 曲线鼓起来了


def test_shape_layer_live_edit_move_transform_resize():
    d = _doc()
    l = d.new_shape_layer({"type": "rrect", "x0": 20, "y0": 20, "x1": 120, "y1": 90, "radius": 15,
                           "fill": "#FF0000", "stroke": "#0000FF", "stroke_w": 6})
    assert l.kind == "shape" and tuple(l.pixels[40 - l.y, 70 - l.x, :3]) == (255, 0, 0)
    assert tuple(l.pixels[20 - l.y, 70 - l.x, :3]) == (0, 0, 255)
    d.translate(l.id, 30, 10)
    p = dict(l.params)
    p["fill"] = "#00FF00"
    d.set_params(l.id, p)
    assert tuple(d.render_full()[50, 100, :3]) == (0, 255, 0)            # 移动之后改颜色：位置不变
    M = np.array([[2.0, 0, -50], [0, 2.0, -40]])
    d.transform_layer(l.id, M)
    assert l.kind == "shape" and l.params["type"] == "path" and abs(l.params["stroke_w"] - 12) < 1e-6
    edge = l.pixels[..., 3]
    assert edge.max() == 255 and ((edge > 0) & (edge < 255)).sum() < edge.size * 0.05   # 放大后边缘仍然清晰
    d.resize_image(160, 100)
    assert l.kind == "shape"
    d.rasterize(l.id)
    assert l.kind == "pixel"


# ---------------------------------------------------------------------------
# 智能对象
# ---------------------------------------------------------------------------

def test_smart_object_lossless_scale_filters_replace_roundtrip(tmp_path):
    d = _doc()
    src = d.layers[0].pixels.copy()
    s = d.to_smart(d.layers[0].id)
    assert s.kind == "smart" and np.array_equal(s.source, src)
    d.transform_layer(s.id, np.array([[0.25, 0, 0], [0, 0.25, 0]]))
    assert (s.w, s.h) == (80, 50)
    d.transform_layer(s.id, np.array([[4.0, 0, 0], [0, 4.0, 0]]))
    assert np.array_equal(s.pixels, src)                                   # 缩小再放大：一点不糊
    d.apply_filter(s.id, "gaussian", {"radius": 6})
    assert s.params["filters"] and s.pixels[..., :3].std() < src[..., :3].std()
    p = dict(s.params)
    p["filters"] = [[*p["filters"][0][:2], False]]
    d.set_params(s.id, p)
    assert np.array_equal(s.pixels, src)                                   # 关掉智能滤镜
    d.translate(s.id, 7, 5)
    d.set_params(s.id, dict(s.params))
    assert (s.x, s.y) == (7, 5)
    path = str(tmp_path / "s.pcedit")
    save_project(d, path)
    d2 = load_project(path)
    s2 = d2.layers[0]
    assert s2.kind == "smart" and np.array_equal(s2.source, src) and np.array_equal(s2.pixels, s.pixels)
    smart.replace_contents(s, np.full((100, 100, 4), 200, np.uint8))
    assert s.source.shape == (100, 100, 4)


# ---------------------------------------------------------------------------
# 动作 / 批处理
# ---------------------------------------------------------------------------

def test_actions_play_and_batch(tmp_path):
    steps = [{"op": "long_side", "px": 160}, {"op": "rotate", "k": 1}, {"op": "adjust", "name": "黑白",
             "params": {"bw": {"on": True}}}, {"op": "filter", "fid": "unsharp", "params": {"amount": 50}},
             {"op": "flatten"}]
    d = _doc()
    assert actions.play(d, steps) == 5
    assert (d.width, d.height) == (100, 160) and len(d.layers) == 1
    assert np.ptp(d.render_full()[..., :3].astype(int), axis=-1).max() <= 2
    assert all(actions.describe(s) for s in steps)
    from PIL import Image
    srcs = []
    for i in range(2):
        p = str(tmp_path / f"in{i}.png")
        Image.fromarray(_photo(seed=i)).save(p)
        srcs.append(p)
    res = actions.batch(srcs, steps, str(tmp_path / "out"), ".jpg", 85, "_bw")
    assert all(not r[1].startswith("ERROR") for r in res)
    assert Image.open(res[0][1]).size == (100, 160)


# ---------------------------------------------------------------------------
# 全景 / 景深合成
# ---------------------------------------------------------------------------

def test_panorama_and_focus_stack():
    base = _textured()
    parts = [base[:, 0:400], base[:, 250:650], base[:, 500:900]]
    pano = stitch.panorama(parts, "scans", crop=True)
    assert pano.shape[1] > 750 and pano[..., 3].min() == 255
    import cv2
    a = base[:, :400].copy()
    bl = cv2.GaussianBlur(a, (0, 0), 4)
    f1, f2 = a.copy(), a.copy()
    f1[:, 200:] = bl[:, 200:]
    f2[:, :200] = bl[:, :200]
    out, idx, aligned = stitch.focus_stack([f1, f2], align=False)
    assert np.abs(out.astype(int) - a.astype(int)).mean() < 2
    assert len(aligned) == 2 and idx[:, :150].mean() < 0.2 and idx[:, 250:].mean() > 0.8
    with pytest.raises(stitch.StitchError):
        stitch.panorama([parts[0]])


# ---------------------------------------------------------------------------
# 色彩管理
# ---------------------------------------------------------------------------

def _cmyk_profile():
    return next((p for p, _, sp in color.system_profiles() if sp == "CMYK"), None)


@pytest.mark.skipif(_cmyk_profile() is None, reason="系统里没有 CMYK 配置文件")
def test_soft_proof_gamut_and_cmyk_export(tmp_path):
    pf = color.Proof(_cmyk_profile(), 1)
    img = np.zeros((4, 8, 4), np.uint8)
    img[...] = (0, 255, 0, 255)
    img[:, 4:] = (128, 128, 128, 255)
    out = pf.apply(img, gamut=True)
    assert tuple(out[0, 0, :3]) == (128, 128, 128)                        # 纯绿印不出来
    assert np.abs(out[0, 6, :3].astype(int) - 128).max() < 20
    d = _doc()
    p = str(tmp_path / "c.jpg")
    export_image(d, p, cmyk=_cmyk_profile())
    from PIL import Image
    im = Image.open(p)
    assert im.mode == "CMYK" and im.info.get("icc_profile")


def test_convert_profile_identity_and_lut():
    d = _doc()
    before = d.layers[0].pixels.copy()
    color.convert_doc(d, None)
    assert np.abs(d.layers[0].pixels.astype(int) - before.astype(int)).max() <= 1
    lut = color._lut(None, None, 0, 9)
    f = np.random.default_rng(0).random((5, 5, 3)).astype(np.float32)
    assert np.abs(color.apply_lut(f, lut) - f).max() < 0.01


# ---------------------------------------------------------------------------
# PSD
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("depth", [8, 16])
def test_psd_roundtrip(tmp_path, depth):
    d = _doc(depth=depth)
    a = _solid(d, (255, 0, 0), (20, 20, 60, 60), "红块")
    if depth == 16:
        d.convert_depth(16)
    g = d.group_layers(a.id)
    d.set_active(a.id)
    r = _solid(d, (0, 0, 255), None, "蓝")
    d.toggle_clip(r.id)
    d.set_props(g.id, opacity=0.7)
    b = _solid(d, (0, 255, 0), (100, 100, 50, 50), "绿")
    d.add_mask(b.id, "hide")
    d.set_props(b.id, blend="multiply")
    before = d.render_full(np.uint8)
    p = str(tmp_path / "t.psd")
    psd.write(d, p)
    d2 = psd.read(p)
    assert d2.depth == depth
    names = [l.name for l in d2.layers]
    assert names == ["背景", "红块", "蓝", "绿", "组 1"]
    g2 = d2.layers[-1]
    assert g2.kind == "group" and abs(g2.opacity - 0.7) < 0.01
    assert d2.layers[2].clip and d2.layers[3].blend == "multiply" and d2.layers[3].mask is not None
    assert np.abs(d2.render_full(np.uint8).astype(int) - before.astype(int)).max() <= 1
    if depth == 8:                                          # Pillow 只读得了 8 位的 PSD
        from PIL import Image
        assert Image.open(p).size == (320, 200)


# ---------------------------------------------------------------------------
# 时间轴 / 视频帧
# ---------------------------------------------------------------------------

def test_frames_gif_mp4_and_video_import(tmp_path):
    d = _doc()
    _solid(d, (255, 0, 0), (0, 0, 100, 100), "a")
    _solid(d, (0, 0, 255), (100, 0, 100, 100), "b")
    anim.frames_from_layers(d, 120)
    assert len(d.frames) == 3
    f1 = anim.render_frame(d, d.frames[1])
    assert tuple(f1[50, 50, :3]) == (255, 0, 0) and tuple(f1[50, 150, :3]) != (0, 0, 255)
    gif = str(tmp_path / "a.gif")
    assert anim.export(d, gif) == 3
    from PIL import Image
    assert Image.open(gif).n_frames == 3
    mp4 = str(tmp_path / "a.mp4")
    anim.export(d, mp4)
    assert os.path.getsize(mp4) > 1000
    d2 = _doc()
    n = anim.import_video(d2, mp4, every=1, max_frames=5)
    assert n >= 3 and len(d2.frames) == n and len(d2.layers) == 1 + n


# ---------------------------------------------------------------------------
# 天空替换 / 皮肤平滑
# ---------------------------------------------------------------------------

def test_sky_detect_and_replace():
    H, W = 300, 450
    yy, xx = np.mgrid[0:H, 0:W]
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = np.stack([120 + 0 * xx, 170 + 0 * xx, 230 - 0.1 * yy], -1).astype(np.uint8)
    ground = yy > (170 + 15 * np.sin(xx / 25.0)).astype(int)
    tex = (np.random.default_rng(1).random((H, W)) * 80).astype(np.uint8)
    img[ground] = np.stack([60 + tex, 90 + tex, 40 + tex // 2], -1)[ground]
    m = sky.detect(img)
    iou = ((m > 127) & ~ground).sum() / ((m > 127) | ~ground).sum()
    assert iou > 0.9
    d = _doc(np.dstack([img, np.full((H, W), 255, np.uint8)]))
    sky.replace(d, m, sky.make_sky("sunset", 600, 300))
    assert [l.kind for l in d.layers[-3:]] == ["adjust", "pixel", "group"]
    out = d.render_full()
    assert np.abs(out[20, 200, :3].astype(int) - img[20, 200].astype(int)).max() > 40
    assert np.abs(out[280, 200, :3].astype(int) - img[280, 200].astype(int)).max() < 60
    for k, _ in sky.PRESETS:
        assert sky.make_sky(k, 80, 40).shape == (40, 80, 3)


def test_skin_smoothing_only_on_skin():
    rgba = np.zeros((120, 200, 4), np.float32)
    rgba[..., 3] = 1
    rng = np.random.default_rng(2)
    rgba[:, :100, :3] = np.array([0.85, 0.62, 0.5]) + rng.normal(0, 0.04, (120, 100, 3))     # “皮肤”
    rgba[:, 100:, :3] = np.array([0.2, 0.4, 0.8]) + rng.normal(0, 0.04, (120, 100, 3))      # 蓝衣服
    out = run("skin", np.clip(rgba, 0, 1), 1.0, {"amount": 100, "detail": 0})
    assert out[10:110, 10:80, :3].std(axis=(0, 1)).mean() < rgba[10:110, 10:80, :3].std(axis=(0, 1)).mean() * 0.6
    assert np.abs(out[:, 130:, :3] - np.clip(rgba[:, 130:, :3], 0, 1)).mean() < 0.005
