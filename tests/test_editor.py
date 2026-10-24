"""修图核心（photocrate/edit）的单元测试：不需要界面。"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from photocrate.edit import adjust, filters                     # noqa: E402
from photocrate.edit.blend import BLEND_MODES, blend, composite   # noqa: E402
from photocrate.edit.document import Document, Layer, crop_canvas  # noqa: E402
from photocrate.edit.io import export_image, load_project, open_file, save_project  # noqa: E402
from photocrate.edit.paint import Stroke, combine, inpaint, object_select, polygon_mask, rect_mask, wand  # noqa: E402


def _photo(w=320, h=200, seed=1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = 40 + 180 * xx / w
    g = 60 + 120 * yy / h
    b = 120 + 60 * np.sin(xx / 17) * np.cos(yy / 23)
    img = np.dstack([r, g, b]) + rng.normal(0, 3, (h, w, 3))
    rgba = np.dstack([np.clip(img, 0, 255), np.full((h, w), 255)]).astype(np.uint8)
    return rgba


def _doc(rgba=None) -> Document:
    rgba = _photo() if rgba is None else rgba
    d = Document(rgba.shape[1], rgba.shape[0])
    l = d._register(Layer("pixel", "背景", rgba))
    d.layers.append(l)
    d.active_id = l.id
    return d


def _flat(d: Document) -> np.ndarray:
    return d.render_full()


# ---------------------------------------------------------------------------
# 调整 / 混合 / 滤镜
# ---------------------------------------------------------------------------

def test_develop_identity_and_exposure():
    rgb = _photo()[..., :3].astype(np.float32) / 255
    p = adjust.default_params()
    assert adjust.is_identity(p)
    assert np.allclose(adjust.develop(rgb, p), rgb, atol=1e-5)
    p["exposure"] = 1.0
    out = adjust.develop(rgb, p)
    lin_in = np.power(rgb, 2.2)
    lin_out = np.power(out, 2.2)
    m = lin_in < 0.4                       # 没溢出的像素：线性亮度翻倍
    assert np.allclose(lin_out[m], np.minimum(1, lin_in[m] * 2), atol=2e-3)


@pytest.mark.parametrize("key,val", [("contrast", 50), ("highlights", -60), ("shadows", 70), ("whites", 40),
                                     ("blacks", -40), ("temp", 30), ("tint", -20), ("vibrance", 50),
                                     ("saturation", -100), ("clarity", 60), ("sharpen", 50), ("denoise", 50),
                                     ("vignette", -60), ("grain", 40), ("fade", 40), ("lv_gamma", 1.6)])
def test_develop_each_slider_changes_and_stays_in_range(key, val):
    rgb = _photo()[..., :3].astype(np.float32) / 255
    p = adjust.default_params()
    p[key] = val
    out = adjust.develop(rgb, p)
    assert out.shape == rgb.shape and out.dtype == np.float32
    assert 0 <= out.min() and out.max() <= 1
    assert np.abs(out - rgb).mean() > 1e-3


def test_saturation_minus_100_is_gray_and_presets_render():
    rgb = _photo()[..., :3].astype(np.float32) / 255
    p = adjust.default_params()
    p["saturation"] = -100
    out = adjust.develop(rgb, p)
    assert np.abs(out[..., 0] - out[..., 1]).max() < 1e-4
    for name in adjust.PRESETS:
        o = adjust.develop(rgb, adjust.preset(name))
        assert np.isfinite(o).all() and 0 <= o.min() <= o.max() <= 1


def test_curve_lut_identity_monotone_and_hsl_band():
    lut = adjust.curve_lut([[0, 0], [255, 255]])
    assert np.allclose(lut, np.linspace(0, 1, len(lut)), atol=1e-6)
    lut = adjust.curve_lut([[0, 0], [64, 40], [190, 220], [255, 255]])
    assert (np.diff(lut) >= -1e-6).all()
    # 只调红色的饱和度：蓝色像素不变
    rgb = np.zeros((2, 2, 3), np.float32)
    rgb[0, 0] = (0.9, 0.1, 0.1)
    rgb[1, 1] = (0.1, 0.2, 0.9)
    p = adjust.default_params()
    p["hsl"]["red"] = [0, -100, 0]
    out = adjust.develop(rgb, p)
    assert np.ptp(out[0, 0]) < 0.05
    assert np.allclose(out[1, 1], rgb[1, 1], atol=1e-3)


def test_blend_modes_known_values():
    cb = np.full((1, 1, 3), 0.5, np.float32)
    cs = np.full((1, 1, 3), 0.5, np.float32)
    assert np.allclose(blend(cb, cs, "multiply"), 0.25)
    assert np.allclose(blend(cb, cs, "screen"), 0.75)
    assert np.allclose(blend(cb, cs, "difference"), 0)
    for mode, _ in BLEND_MODES:
        out = blend(np.random.rand(4, 4, 3).astype(np.float32), np.random.rand(4, 4, 3).astype(np.float32), mode)
        assert out.shape == (4, 4, 3) and np.isfinite(out).all()
    # 50% 不透明的白色叠到不透明黑色上 = 灰；叠到透明底上 = 白色、50% 不透明
    co, ao = composite(np.zeros((1, 1, 3), np.float32), np.ones((1, 1), np.float32),
                       np.ones((1, 1, 3), np.float32), np.full((1, 1), 0.5, np.float32))
    assert np.allclose(co, 0.5) and np.allclose(ao, 1)
    co, ao = composite(np.zeros((1, 1, 3), np.float32), np.zeros((1, 1), np.float32),
                       np.ones((1, 1, 3), np.float32), np.full((1, 1), 0.5, np.float32))
    assert np.allclose(co, 1) and np.allclose(ao, 0.5)


@pytest.mark.parametrize("fid", list(filters.FILTERS))
def test_filters_run(fid):
    rgba = _photo().astype(np.float32) / 255
    out = filters.run(fid, rgba, 1.0)
    assert out.shape == rgba.shape and 0 <= out.min() and out.max() <= 1


# ---------------------------------------------------------------------------
# 文档：渲染、代理图、历史
# ---------------------------------------------------------------------------

def test_render_single_layer_matches_pixels_and_levels_consistent():
    d = _doc()
    assert np.array_equal(_flat(d), d.layers[0].pixels)
    # 代理图：整张算和只算一块完全一致
    full, _ = d.render(2)
    part, _ = d.render(2, (10, 7, 30, 20))
    assert np.allclose(full[7:27, 10:40], part)
    lw, lh = d.level_size(2)
    assert full.shape[:2] == (lh, lw)


def test_proxy_region_update_matches_rebuild():
    d = _doc(_photo(333, 211))
    l = d.layers[0]
    l.proxy(1)
    l.proxy(3)
    l.pixels[50:90, 100:170] = (255, 0, 0, 255)
    l.touch((100, 50, 70, 40))
    p1, p3 = l._px[1].copy(), l._px[3].copy()
    l.touch()
    assert np.array_equal(p1, l.proxy(1)[0]) and np.array_equal(p3, l.proxy(3)[0])


def test_adjust_layer_with_mask_and_opacity_and_hot_cache():
    d = _doc()
    a = d.new_adjust_layer({"saturation": -100}, "黑白")
    out = _flat(d)
    assert np.abs(out[..., 0].astype(int) - out[..., 2].astype(int)).max() <= 2
    d.add_mask(a.id, "hide")
    assert np.array_equal(_flat(d), d.layers[0].pixels)
    # 选区当蒙版：左半边黑白
    d.delete_mask(a.id)
    d.set_selection(rect_mask(d.width, d.height, 0, 0, d.width // 2, d.height))
    d.add_mask(a.id, "selection")
    d.deselect()
    out = _flat(d)
    assert np.abs(out[:, :100, 0].astype(int) - out[:, :100, 2].astype(int)).max() <= 2
    assert np.array_equal(out[:, -50:], d.layers[0].pixels[:, -50:])
    # 拖滑块时用缓存：结果与不用缓存一致
    p = dict(a.params)
    p["exposure"] = 0.5
    d.set_params(a.id, p, key="x")
    r1, _ = d.render(1, hot=a.id)
    r2, _ = d.render(1, hot=a.id)
    d._below = None
    r3, _ = d.render(1)
    assert np.allclose(r1, r3) and np.allclose(r2, r3)


def test_spatial_adjust_region_render_matches_full():
    d = _doc(_photo(400, 260))
    d.new_adjust_layer({"clarity": 80, "shadows": 60, "vignette": -50, "sharpen": 40}, "细节")
    full, _ = d.render(0)
    part, _ = d.render(0, (150, 90, 80, 60))
    assert np.allclose(full[90:150, 150:230], part, atol=1e-4)


def test_big_radius_region_render_close_to_full():
    """大图上高光 / 阴影的分区模糊半径很大（走缩小再模糊），只算一块和整张算的差别要看不出来。"""
    d = _doc(_photo(2400, 1600))
    d.new_adjust_layer({"highlights": -60, "shadows": 60, "clarity": 40}, "影调")
    full, _ = d.render(0, (900, 500, 400, 300))
    whole, _ = d.render(0)
    assert np.abs(whole[500:800, 900:1300] - full).max() < 2 / 255


def test_undo_redo_state_and_coalesce():
    d = _doc()
    l = d.new_pixel_layer()
    d.set_props(l.id, key="op", opacity=0.5)
    d.set_props(l.id, key="op", opacity=0.3)
    assert d.history.names()[-1] == "图层属性" and len(d.history.steps) == 2
    d.history.undo()
    assert d.layer(l.id).opacity == 1.0
    d.history.undo()
    assert d.layer(l.id) is None and len(d.layers) == 1
    d.history.redo()
    d.history.redo()
    assert d.layer(l.id).opacity == 0.3


def test_brush_stroke_patch_undo_and_mask_paint():
    d = _doc()
    l = d.new_pixel_layer()
    orig = l.pixels.copy()
    s = Stroke(d, l, "paint", color=(255, 0, 0), size=20, hardness=1.0, opacity=1.0)
    s.add(50, 50)
    s.add(150, 60)
    rect = s.finish()
    assert rect is not None
    assert l.pixels[55, 100, 0] == 255 and l.pixels[55, 100, 3] == 255
    assert d.history.steps[-1].kind == "patch"
    d.history.undo()
    assert np.array_equal(l.pixels, orig)
    d.history.redo()
    assert l.pixels[55, 100, 3] == 255
    # 橡皮擦
    s = Stroke(d, l, "erase", size=30, hardness=1.0)
    s.add(100, 55)
    s.finish()
    assert l.pixels[55, 100, 3] == 0
    # 蒙版上画黑 = 隐藏
    bg = d.layers[0]
    d.set_active(bg.id)
    d.add_mask(bg.id)
    s = Stroke(d, bg, "paint", color=(0, 0, 0), size=40, hardness=1.0, to_mask=True)
    s.add(200, 100)
    s.finish()
    assert bg.mask[100, 200] == 0 and bg.mask[10, 10] == 255
    assert _flat(d)[100, 200, 3] == 0


def test_brush_respects_selection_and_clone():
    d = _doc()
    l = d.layers[0]
    d.set_selection(rect_mask(d.width, d.height, 0, 0, 100, 200))
    s = Stroke(d, l, "paint", color=(0, 255, 0), size=40, hardness=1.0)
    s.add(90, 100)
    s.add(130, 100)
    s.finish()
    assert tuple(l.pixels[100, 95, :3]) == (0, 255, 0)
    assert tuple(l.pixels[100, 120, :3]) != (0, 255, 0)
    d.deselect()
    before = l.pixels.copy()
    s = Stroke(d, l, "clone", size=10, hardness=1.0, clone_offset=(-100, 0))
    s.add(250, 50)
    s.finish()
    assert np.array_equal(l.pixels[50, 250], before[50, 150])


def test_heal_and_content_fill():
    rgba = _photo()
    rgba[90:100, 150:160] = (255, 255, 255, 255)        # 一个白点
    d = _doc(rgba)
    l = d.layers[0]
    s = Stroke(d, l, "heal", size=24, hardness=0.5)
    s.add(155, 95)
    s.finish()
    assert l.pixels[95, 155, :3].mean() < 240
    d.history.undo()
    assert tuple(l.pixels[95, 155, :3]) == (255, 255, 255)
    d.set_selection(rect_mask(d.width, d.height, 140, 80, 170, 110))
    assert d.content_fill(l.id)
    assert l.pixels[95, 155, :3].mean() < 240


def test_text_layer_rerender_merge_flatten():
    d = _doc()
    from photocrate.edit.text import default_params
    p = default_params()
    p.update(text="影匣 PhotoCrate", size=28, color="#FF0000")
    t = d.new_text_layer(p, 20, 30)
    assert t.pixels is not None and t.pixels[..., 3].max() == 255
    w0 = t.w
    p2 = dict(t.params)
    p2["size"] = 40
    d.set_params(t.id, p2)
    assert t.w > w0
    out = _flat(d)
    d.merge_down(t.id)
    assert len(d.layers) == 1
    assert np.abs(_flat(d).astype(int) - out.astype(int)).max() <= 1
    d.new_adjust_layer({"invert": True}, "反相")
    out = _flat(d)
    d.flatten()
    assert len(d.layers) == 1 and np.abs(_flat(d).astype(int) - out.astype(int)).max() <= 1


def test_blend_mode_layer_and_merge_down_equivalence():
    d = _doc()
    top = d.add_layer(Layer("pixel", "叠加", _photo(seed=5)[:120, :200], 50, 40))
    d.set_props(top.id, blend="multiply", opacity=0.7)
    out = _flat(d)
    d.merge_down(top.id)
    assert np.abs(_flat(d).astype(int) - out.astype(int)).max() <= 1


def test_crop_rotate_flip_resize():
    d = _doc()
    small = d.add_layer(Layer("pixel", "小图", np.full((20, 30, 4), 200, np.uint8), 10, 10))
    d._crop_straight(5, 5, 100, 80)
    assert (d.width, d.height) == (100, 80)
    assert (small.x, small.y) == (5, 5)
    ref = _flat(d)
    d.rotate(1)
    assert (d.width, d.height) == (80, 100)
    assert np.array_equal(_flat(d), np.rot90(ref, -1))
    d.rotate(3)
    assert np.array_equal(_flat(d), ref)
    d.flip(True)
    assert np.array_equal(_flat(d), ref[:, ::-1])
    d.history.undo()
    d.resize_image(50, 40)
    assert (d.width, d.height) == (50, 40) and d.layers[0].pixels.shape[:2] == (40, 50)
    d.history.undo()
    assert (d.width, d.height) == (100, 80)
    d.crop(10, 10, 60, 40, angle=10)
    assert (d.width, d.height) == (60, 40)
    for _ in range(4):
        d.history.undo()
    assert (d.width, d.height) == (320, 200)


def test_layer_via_copy_and_transform():
    d = _doc()
    d.set_selection(rect_mask(d.width, d.height, 20, 30, 60, 70))
    nl = d.layer_via_copy()
    assert (nl.x, nl.y, nl.w, nl.h) == (20, 30, 40, 40)
    M = np.array([[2, 0, 100], [0, 2, 50]], np.float64)
    d.transform_layer(nl.id, M)
    assert (nl.w, nl.h) == (80, 80) and (nl.x, nl.y) == (100, 50)
    d.history.undo()
    assert (nl.w, nl.h) == (40, 40)


def test_selection_ops_and_wand_and_object_select():
    a = rect_mask(100, 80, 10, 10, 50, 50)
    b = rect_mask(100, 80, 30, 30, 70, 70)
    assert combine(a, b, "add")[60, 60] == 255
    assert combine(a, b, "sub")[40, 40] == 0 and combine(a, b, "sub")[20, 20] == 255
    assert combine(a, b, "and")[20, 20] == 0 and combine(a, b, "and")[40, 40] == 255
    tri = polygon_mask(100, 80, [(10, 10), (90, 10), (50, 70)])
    assert tri[20, 50] == 255 and tri[70, 5] == 0
    img = np.zeros((60, 80, 4), np.uint8)
    img[..., 3] = 255
    img[10:40, 10:40, :3] = (200, 30, 30)
    img[10:40, 50:70, :3] = (200, 30, 30)
    m = wand(img, 20, 20, 10, contiguous=True, antialias=False)
    assert m[20, 20] == 255 and m[20, 60] == 0
    m = wand(img, 20, 20, 10, contiguous=False, antialias=False)
    assert m[20, 60] == 255 and m[50, 5] == 0
    # 对象选择：灰底上的一个彩色方块
    scene = np.full((200, 260, 4), 128, np.uint8)
    scene[60:140, 90:170, :3] = (230, 60, 40)
    om = object_select(scene, (70, 40, 190, 160))
    assert om[100, 130] > 200 and om[50, 80] < 50
    d = _doc()
    d.set_selection(rect_mask(d.width, d.height, 50, 50, 100, 100))
    d.grow_selection(10)
    assert d.selection[45, 75] == 255
    d.invert_selection()
    assert d.selection[75, 75] == 0
    d.history.undo()
    d.history.undo()
    assert d.selection[45, 75] == 0


def test_filter_on_selection_and_preview():
    d = _doc()
    l = d.layers[0]
    d.set_selection(rect_mask(d.width, d.height, 0, 0, 160, 200))
    d.set_filter_preview(l.id, "invert", {})
    prev, _ = d.render(0)
    d.set_filter_preview(None)
    d.apply_filter(l.id, "invert")
    out = _flat(d)
    assert np.abs(out[:, :150, :3].astype(int) + d.history.steps[-1].before["layers"][0]["pixels"][:, :150, :3]
                  - 255).max() <= 1
    assert np.abs(prev * 255 - out[..., :3]).max() <= 1.01


def test_export_and_project_roundtrip(tmp_path):
    from PIL import Image
    src = tmp_path / "IMG_0001.JPG"
    im = Image.fromarray(_photo()[..., :3])
    ex = Image.Exif()
    ex[0x0110] = "TestCam"
    ex.get_ifd(0x8769)[0x9003] = "2026:04:02 10:00:00"
    ex.get_ifd(0x8825)[2] = (35.0, 0.0, 0.0)
    im.save(src, "JPEG", exif=ex.tobytes())
    d = open_file(str(src))
    assert d.layers[0].pixels.flags.writeable
    d.new_adjust_layer({"exposure": 0.5}, "提亮")
    t = d.new_text_layer({"text": "Hi", "size": 30, "color": "#00FF00"}, 5, 5)
    with pytest.raises(ValueError):
        export_image(d, str(src))
    out = tmp_path / "IMG_0001_修图.jpg"
    export_image(d, str(out), strip_gps=True)
    with Image.open(out) as o:
        e = o.getexif()
        assert e.get(0x0110) == "TestCam" and e.get_ifd(0x8769).get(0x9003) == "2026:04:02 10:00:00"
        assert not e.get_ifd(0x8825)
        assert o.size == (320, 200)
    png = tmp_path / "x.png"
    export_image(d, str(png))
    proj = tmp_path / "IMG_0001.pcedit"
    save_project(d, str(proj))
    d2 = load_project(str(proj))
    assert [l.kind for l in d2.layers] == ["pixel", "adjust", "text"]
    assert all(l.pixels.flags.writeable for l in d2.layers if l.pixels is not None)
    assert t.pixels.flags.writeable
    assert d2.layers[2].params["text"] == "Hi"
    assert np.array_equal(_flat(d2), _flat(d))
    assert d2.source.exif == d.source.exif


def test_pcedit_is_sidecar():
    from photocrate.core.formats import file_kind
    assert file_kind("DSC01.pcedit") == "sidecar"


def test_crop_canvas_helper():
    a = np.arange(12, dtype=np.uint8).reshape(3, 4)
    out = crop_canvas(a, -1, 1, 3, 3, 9)
    assert out.tolist() == [[9, 4, 5], [9, 8, 9], [9, 9, 9]]


def test_inpaint_large_region():
    rgba = _photo(400, 300)
    m = np.zeros((300, 400), np.uint8)
    m[50:250, 100:300] = 255
    out = inpaint(rgba, m)
    assert out.shape == rgba.shape and np.isfinite(out).all()
    assert np.array_equal(out[:40, :80], rgba[:40, :80])
