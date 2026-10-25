"""修图进阶功能（v1.8.0）：16 位、RAW 显影、图层组、剪贴蒙版、透视 / 变形、减淡加深等修饰工具。不需要界面。"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from photocrate.edit import warp                                          # noqa: E402
from photocrate.edit.document import Document, Layer, convert, to8         # noqa: E402
from photocrate.edit.io import decode16, export_image, load_project, open_file, save_project  # noqa: E402
from photocrate.edit.paint import Stroke, tone_target                     # noqa: E402


def _photo(w=320, h=200, seed=1, depth=8) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.dstack([40 + 180 * xx / w, 60 + 120 * yy / h, 120 + 60 * np.sin(xx / 17) * np.cos(yy / 23)])
    img = img + rng.normal(0, 3, (h, w, 3))
    if depth == 16:
        return np.dstack([np.clip(img * 257, 0, 65535), np.full((h, w), 65535)]).astype(np.uint16)
    return np.dstack([np.clip(img, 0, 255), np.full((h, w), 255)]).astype(np.uint8)


def _doc(rgba=None, depth=8) -> Document:
    rgba = _photo(depth=depth) if rgba is None else rgba
    d = Document(rgba.shape[1], rgba.shape[0], 16 if rgba.dtype == np.uint16 else 8)
    l = d._register(Layer("pixel", "背景", rgba))
    d.layers.append(l)
    d.active_id = l.id
    return d


# ---------------------------------------------------------------------------
# 16 位
# ---------------------------------------------------------------------------

def test_16bit_render_is_exact_and_smooth():
    ramp = np.zeros((8, 4096, 4), np.uint16)
    ramp[..., :3] = np.linspace(20000, 24000, 4096).astype(np.uint16)[None, :, None]
    ramp[..., 3] = 65535
    d = _doc(ramp)
    assert d.depth == 16
    out = d.render_full()
    assert out.dtype == np.uint16 and np.array_equal(out, ramp)
    # 强烈拉伸（色阶）后：8 位只剩约 16 级，16 位仍然几乎每个像素都不同
    d.new_adjust_layer({"lv_lo": 78, "lv_hi": 94})
    hi = d.render_full()
    assert len(np.unique(hi[0, :, 0])) > 1500
    d8 = _doc(to8(ramp))
    d8.new_adjust_layer({"lv_lo": 78, "lv_hi": 94})
    assert len(np.unique(d8.render_full()[0, :, 0])) < 40


def test_convert_depth_roundtrip_and_undo():
    d = _doc()
    src = d.layers[0].pixels
    d.convert_depth(16)
    assert d.depth == 16 and d.layers[0].pixels.dtype == np.uint16
    assert np.array_equal(to8(d.layers[0].pixels), src)
    d.new_pixel_layer()
    assert d.active.pixels.dtype == np.uint16
    d.history.undo()
    d.history.undo()
    assert d.depth == 8 and d.layers[0].pixels is src


def test_16bit_ops_keep_depth():
    d = _doc(depth=16)
    l = d.layers[0]
    d.apply_filter(l.id, "gaussian", {"radius": 2})
    assert l.pixels.dtype == np.uint16
    d.set_selection(np.pad(np.full((40, 40), 255, np.uint8), ((50, 110), (60, 220))))
    d.content_fill(l.id)
    assert l.pixels.dtype == np.uint16
    nl = d.layer_via_copy()
    assert nl.pixels.dtype == np.uint16
    d.merge_down(nl.id)
    assert d.layers[0].pixels.dtype == np.uint16
    s = Stroke(d, d.layers[0], "paint", color=(255, 0, 0), size=10)
    s.add(100, 100)
    s.finish()
    assert d.layers[0].pixels.dtype == np.uint16
    d.flatten()
    assert d.layers[0].pixels.dtype == np.uint16


def test_16bit_export_png_tiff_and_project(tmp_path):
    d = _doc(depth=16)
    d.source.exif = None
    for ext in (".png", ".tif"):
        p = str(tmp_path / f"out{ext}")
        export_image(d, p)
        back = decode16(open(p, "rb").read())
        assert back is not None and back.dtype == np.uint16
        assert np.array_equal(back[..., :3], d.layers[0].pixels[..., :3])
        d2 = open_file(p)
        assert d2.depth == 16
    p8 = str(tmp_path / "out8.png")
    export_image(d, p8, deep=False)
    assert decode16(open(p8, "rb").read()) is None
    proj = str(tmp_path / "x.pcedit")
    save_project(d, proj)
    d3 = load_project(proj)
    assert d3.depth == 16 and np.array_equal(d3.layers[0].pixels, d.layers[0].pixels)


def test_tiff16_with_alpha_and_icc(tmp_path):
    from PIL import ImageCms
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    arr = _photo(depth=16)
    arr[:20, :20, 3] = 1000
    d = _doc(arr)
    d.source.icc = icc
    p = str(tmp_path / "a.tif")
    export_image(d, p)
    back = decode16(open(p, "rb").read())
    assert back.shape[2] == 4 and np.array_equal(back, arr)


# ---------------------------------------------------------------------------
# RAW 显影
# ---------------------------------------------------------------------------

def test_raw_develop_16bit_and_white_balance(tmp_path):
    from raw_fixture import make_dng, scene

    from photocrate.edit import raw
    p = make_dng(str(tmp_path / "IMG_0001.DNG"), scene())
    img = raw.develop(p, raw.RawOptions(auto_bright=False))
    assert img.dtype == np.uint16 and img.shape == (400, 600, 3)
    gray = img[143:173, 470:550].reshape(-1, 3).mean(axis=0)
    assert np.ptp(gray) / gray.mean() < 0.02                 # 拍摄时白平衡：中性灰还是中性
    red = img[143:173, 50:130].reshape(-1, 3).mean(axis=0)
    assert red[0] > 2 * red[1] and red[0] > 2 * red[2]
    brighter = raw.develop(p, raw.RawOptions(auto_bright=False, exposure=1.0))
    assert brighter[143:173, 470:550].mean() > gray.mean() * 1.25
    # 自定义白平衡：故意偏色后，用吸管点中性灰找回来
    info = raw.camera_wb(p)
    off = list(info["camera"])
    off[0] *= 1.4
    warm = raw.develop(p, raw.RawOptions(wb="custom", wb_mul=off, auto_bright=False))
    g2 = warm[143:173, 470:550].reshape(-1, 3).mean(axis=0)
    assert g2[0] > g2[2] * 1.1
    fixed = raw.pick_white(warm, 510, 158, off, r=10)
    ok = raw.develop(p, raw.RawOptions(wb="custom", wb_mul=fixed, auto_bright=False))
    g3 = ok[143:173, 470:550].reshape(-1, 3).mean(axis=0)
    assert np.ptp(g3) / g3.mean() < 0.03
    d = open_file(p)
    assert d.depth == 16 and d.layers[0].pixels.dtype == np.uint16


# ---------------------------------------------------------------------------
# 图层组 / 剪贴蒙版
# ---------------------------------------------------------------------------

def _solid(d, color, rect=None, name="L"):
    px = np.zeros((d.height, d.width, 4), np.uint8)
    if rect is None:
        px[...] = (*color, 255)
    else:
        x, y, w, h = rect
        px[y:y + h, x:x + w] = (*color, 255)
    return d.add_layer(Layer("pixel", name, px))


def test_clipping_mask_limits_layer_and_adjustment():
    d = _doc()
    base = _solid(d, (200, 200, 200), (50, 50, 100, 80), "base")
    red = _solid(d, (255, 0, 0), None, "red")
    d.toggle_clip(red.id)
    assert red.clip and d.clip_base(red) is base
    out = d.render_full()
    assert tuple(out[60, 60, :3]) == (255, 0, 0)                        # 基底里面：红色
    assert np.array_equal(out[10, 10], d.layers[0].pixels[10, 10])     # 基底外面：不受影响
    # 剪贴的调整图层只调基底
    d.toggle_clip(red.id)
    d.delete_layer(red.id)
    d.set_active(base.id)
    adj = d.new_adjust_layer({"invert": True})
    d.toggle_clip(adj.id)
    out = d.render_full()
    assert tuple(out[60, 60, :3]) == (55, 55, 55)
    assert np.array_equal(out[10, 10], d.layers[0].pixels[10, 10])
    # 释放剪贴：调整作用到整张
    d.toggle_clip(adj.id)
    out = d.render_full()
    assert np.array_equal(out[10, 10, :3], 255 - d.layers[0].pixels[10, 10, :3])


def test_clip_base_opacity_applies_to_whole_stack():
    d = _doc()
    base = _solid(d, (0, 0, 255), (0, 0, 320, 200), "base")
    red = _solid(d, (255, 0, 0), None, "red")
    d.toggle_clip(red.id)
    d.set_props(base.id, opacity=0.5)
    out = d.render_full().astype(int)
    bg = d.layers[0].pixels[100, 100, :3].astype(int)
    assert np.allclose(out[100, 100, :3], (255 + bg) / 2 * np.array([1, 0, 0]) + bg / 2 * np.array([0, 1, 1]),
                       atol=2)


def test_groups_pass_through_isolated_opacity_mask():
    d = _doc()
    a = _solid(d, (255, 0, 0), (20, 20, 60, 60), "a")
    g = d.group_layers(a.id)
    assert g.kind == "group" and a.parent == g.id and d.unit(g.id) == (1, 3)
    before = d.render_full()
    ref = _doc()
    _solid(ref, (255, 0, 0), (20, 20, 60, 60))
    assert np.array_equal(before, ref.render_full())                  # 穿透 + 100%：和不编组一样
    d.set_props(g.id, opacity=0.5)
    out = d.render_full().astype(int)
    exp = (d.layers[0].pixels[40, 40, :3].astype(int) + np.array([255, 0, 0])) / 2
    assert np.allclose(out[40, 40, :3], exp, atol=2)
    d.set_props(g.id, visible=False)
    assert np.array_equal(d.render_full(), d.layers[0].pixels)
    d.set_props(g.id, visible=True, opacity=1.0)
    d.add_mask(g.id, "hide")
    assert np.array_equal(d.render_full(), d.layers[0].pixels)
    d.delete_mask(g.id)
    # 组里的调整图层：穿透模式下调到组外下面的图层；“正常”模式（隔离）只调组里的
    d.set_active(a.id)
    adj = d.new_adjust_layer({"invert": True})
    assert adj.parent == g.id
    out = d.render_full()
    assert np.array_equal(out[150, 250, :3], 255 - d.layers[0].pixels[150, 250, :3])
    d.set_props(g.id, blend="normal")
    out = d.render_full()
    assert np.array_equal(out[150, 250], d.layers[0].pixels[150, 250])
    assert tuple(out[40, 40, :3]) == (0, 255, 255)


def test_group_move_in_out_duplicate_delete_merge_ungroup():
    d = _doc()
    a = _solid(d, (255, 0, 0), (0, 0, 10, 10), "a")
    b = _solid(d, (0, 255, 0), (10, 0, 10, 10), "b")
    g = d.group_layers(a.id)                      # 背景, a, [g], b
    assert [l.name for l in d.layers] == ["背景", "a", g.name, "b"]
    d.move_layer(b.id, -1)                        # b 下移：进入展开的组（放在组里最上面）
    assert b.parent == g.id and d.layers.index(b) == 2
    d.move_layer(b.id, 1)                         # 再上移：出组，到组的上面
    assert b.parent is None and d.layers[-1] is b
    d.move_layer(g.id, 1)                         # 组整个上移，越过 b
    assert d.layers[-1] is g and d.layers[-2] is a
    d.translate(g.id, 5, 7)
    assert (a.x, a.y) == (5, 7)
    d.translate(g.id, -5, -7)
    dup = d.duplicate_layer(g.id)
    assert dup.kind == "group" and len(d.layers) == 6
    assert d.layers[-2].parent == dup.id and d.layers[-2].pixels is not a.pixels
    d.delete_layer(dup.id)
    assert len(d.layers) == 4
    before = d.render_full()
    d.merge_down(g.id)                            # 合并组
    assert d.active.kind == "pixel" and len(d.layers) == 3
    assert np.array_equal(d.render_full(), before)
    d.history.undo()
    d.ungroup(g.id)
    assert all(l.kind != "group" for l in d.layers) and a.parent is None
    assert np.array_equal(d.render_full(), before)


def test_hot_cache_with_groups_matches_full():
    d = _doc()
    a = _solid(d, (255, 0, 0), (20, 20, 60, 60), "a")
    d.group_layers(a.id)
    d.set_active(a.id)
    adj = d.new_adjust_layer({"exposure": 0.5})
    full = d.render(0)[0]
    d._below = None
    d.render(0, hot=adj.id)
    p = dict(adj.params)
    p["exposure"] = -0.5
    d.set_params(adj.id, p)
    hot = d.render(0, hot=adj.id)[0]
    d._below = None
    cold = d.render(0)[0]
    assert np.allclose(hot, cold, atol=1e-6) and not np.allclose(full, cold)


def test_groups_and_clip_save_load(tmp_path):
    d = _doc()
    a = _solid(d, (255, 0, 0), (20, 20, 60, 60), "a")
    g = d.group_layers(a.id)
    d.set_active(a.id)
    r = _solid(d, (0, 0, 255), None, "r")
    d.toggle_clip(r.id)
    g.collapsed = True
    before = d.render_full()
    p = str(tmp_path / "g.pcedit")
    save_project(d, p)
    d2 = load_project(p)
    g2 = next(l for l in d2.layers if l.kind == "group")
    r2 = next(l for l in d2.layers if l.name == "r")
    assert r2.clip and r2.parent == g2.id and g2.collapsed
    assert np.array_equal(d2.render_full(), before)


# ---------------------------------------------------------------------------
# 透视 / 扭曲 / 变形
# ---------------------------------------------------------------------------

def test_perspective_and_mesh_identity_and_preview_match():
    d = _doc()
    l = d.layers[0]
    w, h = l.w, l.h
    H = warp.quad_h(w, h, [[0, 0], [w, 0], [w, h], [0, h]])
    assert np.allclose(H, np.eye(3), atol=1e-9)
    ctrl = warp.grid_from_h(np.eye(3), w, h)
    out = warp.mesh_warp(l.pixels, ctrl, 0, 0, w, h)
    assert np.abs(out[2:-2, 2:-2].astype(int) - l.pixels[2:-2, 2:-2].astype(int)).max() <= 1
    # 透视：预览（代理图）和提交后的结果一致
    quad = [[20, 10], [w - 40, 30], [w - 10, h - 5], [5, h - 20]]
    Hq = warp.quad_h(w, h, quad)
    l.xform = Hq
    prev = d.render(1)[0]
    l.xform = None
    d.transform_layer(l.id, Hq)
    after = d.render(1)[0]
    assert np.abs(prev[5:-5, 5:-5] - after[5:-5, 5:-5]).mean() < 0.03
    assert abs(l.x - 5) <= 3 and abs(l.y - 10) <= 3


def test_mesh_warp_moves_point():
    d = _doc()
    l = d.layers[0]
    w, h = l.w, l.h
    ctrl = warp.grid_from_h(np.eye(3), w, h)
    ctrl[1, 1] += (25, 15)
    l.xform = {"mesh": ctrl}
    prev = d.render(0)
    l.xform = None
    d.transform_layer(l.id, {"mesh": ctrl})
    after = d.render(0)
    assert d.history.names()[-1] == "变形"
    assert np.abs(prev[0][10:-10, 10:-10] - after[0][10:-10, 10:-10]).mean() < 0.02
    assert not np.array_equal(to8(d.layers[0].pixels), _photo())


# ---------------------------------------------------------------------------
# 减淡 / 加深 / 海绵 / 模糊 / 锐化 / 涂抹
# ---------------------------------------------------------------------------

def test_tone_target_ranges():
    rgb = np.array([[[0.1, 0.1, 0.1], [0.5, 0.5, 0.5], [0.9, 0.9, 0.9]]], np.float32)
    up = tone_target(rgb, "dodge", 0.5, "shadows")
    assert up[0, 0, 0] - 0.1 > up[0, 2, 0] - 0.9                    # 暗部提亮得最多
    dn = tone_target(rgb, "burn", 0.5, "highlights")
    assert 0.9 - dn[0, 2, 0] > 0.1 - dn[0, 0, 0]
    col = np.array([[[0.8, 0.4, 0.2]]], np.float32)
    assert np.ptp(tone_target(col, "sponge", 1.0)) < 1e-6          # 完全去色
    assert np.ptp(tone_target(col, "sponge", 0.5, sat_up=True)) > np.ptp(col)


@pytest.mark.parametrize("mode", ["dodge", "burn", "sponge", "blur", "sharpen", "smudge"])
def test_retouch_strokes_change_only_brushed_area_and_undo(mode):
    for depth in (8, 16):
        d = _doc(depth=depth)
        l = d.layers[0]
        orig = l.pixels.copy()
        s = Stroke(d, l, mode, size=30, strength=0.8)
        for x in range(60, 200, 4):
            s.add(x, 100)
        s.finish()
        diff = np.abs(l.pixels.astype(np.int64) - orig.astype(np.int64)).max(axis=2)
        assert diff[85:115, 80:180].max() > 0, mode
        assert diff[:70].max() == 0 and diff[140:].max() == 0
        assert np.array_equal(l.pixels[..., 3], orig[..., 3])
        d.history.undo()
        assert np.array_equal(l.pixels, orig)


def test_blur_and_sharpen_change_detail_in_expected_direction():
    d = _doc()
    l = d.layers[0]
    orig = l.pixels[..., :3].astype(np.float32)

    def hf(a):
        return np.abs(np.diff(a[90:110, 100:160].astype(np.float32), axis=1)).mean()
    for mode, cmp in (("blur", np.less), ("sharpen", np.greater)):
        s = Stroke(d, l, mode, size=60, hardness=1.0, strength=0.7)
        for x in range(90, 180, 3):
            s.add(x, 100)
        s.finish()
        assert cmp(hf(l.pixels[..., :3]), hf(orig)), mode
        d.history.undo()


def test_smudge_drags_color():
    rgba = np.zeros((100, 200, 4), np.uint8)
    rgba[..., 3] = 255
    rgba[:, :60, 0] = 255                                         # 左边红、右边黑
    d = _doc(rgba)
    l = d.layers[0]
    s = Stroke(d, l, "smudge", size=20, strength=0.9)
    for x in range(40, 120, 2):
        s.add(x, 50)
    s.finish()
    assert l.pixels[50, 80, 0] > 60                               # 红色被推到了右边
    assert l.pixels[10, 80, 0] == 0
