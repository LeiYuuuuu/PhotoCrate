"""修图进阶第二档（v1.9.0）：新调整、选区精修、新滤镜、液化、修补 / 红眼、图层样式、锁定、填充、存储选区。"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from photocrate.edit import adjust, coloradj, filters, select, styles      # noqa: E402
from photocrate.edit.document import Document, Layer                        # noqa: E402
from photocrate.edit.io import load_project, save_project                   # noqa: E402
from photocrate.edit.liquify import Liquify                                 # noqa: E402
from photocrate.edit.paint import Stroke, patch, red_eye                    # noqa: E402
from test_editor_pro import _doc, _photo, _solid                            # noqa: E402


def _rgb(*cols):
    return np.array([cols], np.float32)


# ---------------------------------------------------------------------------
# 新调整
# ---------------------------------------------------------------------------

def test_black_white_matches_photoshop_defaults():
    q = coloradj.defaults()["bw"]
    out = coloradj.black_white(_rgb((1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1), (1, 0, 1), (0.5, 0.5, 0.5)), q)
    assert np.allclose(out[0, :, 0], [0.4, 0.6, 0.4, 0.6, 0.2, 0.8, 0.5], atol=1e-5)
    assert np.ptp(out, axis=-1).max() == 0
    q["tint"] = True
    t = coloradj.black_white(_rgb((0.5, 0.5, 0.5)), q)
    assert t[0, 0, 0] > t[0, 0, 2]                                   # 默认色调偏暖


def test_channel_mixer_identity_swap_and_mono():
    q = coloradj.defaults()["mixer"]
    rgb = np.random.default_rng(1).random((4, 4, 3)).astype(np.float32)
    assert np.allclose(coloradj.channel_mixer(rgb, q), rgb)
    q["r"], q["b"] = [0, 0, 100, 0], [100, 0, 0, 0]
    assert np.allclose(coloradj.channel_mixer(rgb, q)[..., 0], rgb[..., 2])
    q["mono"] = True
    m = coloradj.channel_mixer(rgb, q)
    assert np.allclose(m[..., 0], m[..., 1])


def test_selective_color_only_touches_its_range():
    q = coloradj.defaults()["selective"]
    q["reds"] = [100.0, 0, 0, 0]                                      # 红色里加青 = 红变暗
    rel = coloradj.selective_color(_rgb((0.9, 0.1, 0.1)), q)
    assert 0.75 < rel[0, 0, 0] < 0.85                                 # 相对：按已有的 10% 青成比例加
    q["relative"] = False
    out = coloradj.selective_color(_rgb((0.9, 0.1, 0.1), (0.1, 0.1, 0.9), (0.5, 0.5, 0.5)), q)
    assert out[0, 0, 0] < 0.3 and np.allclose(out[0, 1:], _rgb((0.1, 0.1, 0.9), (0.5, 0.5, 0.5))[0], atol=1e-6)


def test_photo_filter_gradient_map_color_balance():
    pf = coloradj.defaults()["photo_filter"]
    pf["density"] = 60
    g = _rgb((0.5, 0.5, 0.5))
    w = coloradj.photo_filter(g, pf)
    assert w[0, 0, 0] > w[0, 0, 2]
    assert abs(float(w[0, 0] @ coloradj.LUMA) - 0.5) < 1e-4           # 保留明度
    gm = coloradj.defaults()["gradient_map"]
    gm["stops"] = [[0, "#FF0000"], [1, "#0000FF"]]
    out = coloradj.gradient_map(_rgb((0, 0, 0), (1, 1, 1)), gm)
    assert np.allclose(out[0, 0], [1, 0, 0], atol=1e-3) and np.allclose(out[0, 1], [0, 0, 1], atol=1e-3)
    cb = coloradj.defaults()["color_balance"]
    cb["shadows"] = [60.0, 0, 0]
    cb["preserve"] = False
    out = coloradj.color_balance(_rgb((0.1, 0.1, 0.1), (0.9, 0.9, 0.9)), cb)
    assert out[0, 0, 0] - 0.1 > 0.15 and abs(out[0, 1, 0] - 0.9) < 0.02


def test_new_adjustments_in_adjust_layer_and_normalize():
    p = adjust.normalize({"bw": {"on": True}})
    assert not adjust.is_identity(p) and p["bw"]["reds"] == 40
    assert adjust.is_identity(adjust.normalize({"bw": {"on": False, "reds": 0}}))
    d = _doc()
    d.new_adjust_layer({"bw": {"on": True}})
    out = d.render_full()
    assert np.ptp(out[..., :3].astype(int), axis=-1).max() <= 1


# ---------------------------------------------------------------------------
# 新滤镜
# ---------------------------------------------------------------------------

def test_new_filters_identity_cases_and_whole_apply_matches_run():
    rgba = np.random.default_rng(3).random((90, 130, 4)).astype(np.float32)
    rgba[..., 3] = 1
    for fid, p in (("lens", {}), ("twirl", {"angle": 0}), ("spherize", {"amount": 0}), ("pinch", {"amount": 0})):
        out = filters.run(fid, rgba, 1.0, p)
        assert np.abs(out[3:-3, 3:-3] - rgba[3:-3, 3:-3]).mean() < 0.01, fid
    d = _doc()
    l = d.layers[0]
    src = l.pixels.copy()
    d.apply_filter(l.id, "twirl", {"angle": 120})
    ref = filters.run("twirl", src.astype(np.float32) / 255, 1.0, {"angle": 120})
    assert np.abs(l.pixels.astype(np.float32) / 255 - ref).max() < 2 / 255   # 原尺寸应用 = 整张算（不分条）
    sb = filters.run("surface", rgba, 1.0, {"radius": 4, "threshold": 30})
    assert sb[..., :3].std() < rgba[..., :3].std()
    lb = filters.run("lensblur", rgba, 1.0, {"radius": 6})
    assert lb[..., :3].std() < rgba[..., :3].std() * 0.6


def test_lens_correction_straightens_barrel():
    h, w = 200, 300
    img = np.zeros((h, w, 4), np.float32)
    img[..., 3] = 1
    img[:, ::20, :3] = 1                                            # 竖线
    from photocrate.edit.filters2 import lens_correct
    bent = lens_correct(img, 1.0, distortion=-40, fill=False)        # 人为加上桶形
    back = lens_correct(bent, 1.0, distortion=40, fill=False)
    col = back[:, 40, 0]
    assert col[20:-20].mean() > bent[20:-20, 40, 0].mean() * 0.9


# ---------------------------------------------------------------------------
# 液化
# ---------------------------------------------------------------------------

def test_liquify_push_reconstruct_and_full_apply():
    rgba = np.zeros((300, 400, 4), np.uint8)
    rgba[..., 3] = 255
    rgba[:, 180:220, 0] = 255                                       # 一条红色竖带
    lq = Liquify(rgba, max_side=200)
    assert lq.s == 0.5
    lq.begin()
    lq.stroke_to("push", 100, 75, 140, 75, 60, 1.0)                  # 往右推
    prev = lq.preview()
    assert prev[75, 110:130, 0].mean() > 0.5                        # 红带被推到了右边
    out = lq.apply()
    assert out.dtype == np.uint8 and out[150, 220:260, 0].mean() > 120
    assert out[10, 50, 0] == 0
    before = np.abs(lq.D).max()
    for _ in range(40):
        lq.dab("reconstruct", 120, 75, 160, 1.0)
    assert np.abs(lq.D).max() < before * 0.1
    assert lq.undo() and not lq.undo() is None


# ---------------------------------------------------------------------------
# 修补 / 红眼
# ---------------------------------------------------------------------------

def test_patch_removes_spot_and_keeps_16bit():
    px = _photo()
    clean = px.copy()
    px[60:80, 60:80, :3] = 255
    m = np.zeros(px.shape[:2], np.uint8)
    m[55:86, 55:86] = 255
    out = patch(px, m, 0, 90)
    assert np.abs(out[62:78, 62:78, :3].astype(int) - clean[62:78, 62:78, :3].astype(int)).mean() < 20
    assert np.array_equal(out[:40], px[:40])
    p16 = _photo(depth=16)
    p16[60:80, 60:80, :3] = 65535
    o16 = patch(p16, m, 0, 90)
    assert o16.dtype == np.uint16 and o16[70, 70, 0] < 60000
    assert patch(px, m, 0, 500) is None                               # 拖出了图层


def test_red_eye():
    px = _photo()
    px[90:110, 140:160] = (220, 30, 30, 255)
    out = red_eye(px, 150, 100, 14, 0.5)
    assert out[100, 150, 0] < 80 and np.array_equal(out[:60], px[:60])
    assert red_eye(_photo(), 150, 100, 14) is None


# ---------------------------------------------------------------------------
# 图层样式 / 填充 / 锁定
# ---------------------------------------------------------------------------

def test_layer_styles_render_and_fill():
    d = _doc(np.dstack([np.full((200, 300, 3), 255, np.uint8), np.full((200, 300), 255, np.uint8)]))
    sq = _solid(d, (0, 0, 255), (100, 60, 60, 60), "sq")
    sq.style = {"drop_shadow": dict(styles.DEFAULTS["drop_shadow"], on=True, distance=20, size=4, opacity=100)}
    out = d.render_full()
    dx, dy = -np.cos(np.radians(120)) * 20, np.sin(np.radians(120)) * 20
    sx, sy = int(130 + dx), int(90 + dy + 35)                       # 方块下方偏右：影子里
    assert out[min(199, 60 + 60 + 8), 100 + 30 + int(dx), 0] < 120, "投影应该在方块右下方"
    assert tuple(out[90, 130, :3]) == (0, 0, 255)
    sq.style = {"stroke": dict(styles.DEFAULTS["stroke"], on=True, color="#FF0000", size=6)}
    sq.version += 1
    out = d.render_full()
    assert tuple(out[90, 97, :3]) == (255, 0, 0) and tuple(out[90, 130, :3]) == (0, 0, 255)
    d.set_props(sq.id, fill=0.0)                                    # 填充 0：方块不见了，描边还在
    out = d.render_full()
    assert tuple(out[90, 130, :3]) == (255, 255, 255) and tuple(out[90, 97, :3]) == (255, 0, 0)
    for k, _ in styles.EFFECTS:
        sq.style = {k: dict(styles.DEFAULTS[k], on=True)}
        sq.version += 1
        assert d.render(1)[0].shape[:2] == d.level_size(1)[::-1]
    d.set_props(sq.id, fill=1.0, style={"stroke": dict(styles.DEFAULTS["stroke"], on=True, color="#FF0000")})
    before = d.render_full()
    d.merge_down(sq.id)
    assert np.array_equal(d.render_full(), before) and not d.layers[0].style


def test_locks():
    d = _doc()
    l = d.layers[0]
    orig = l.pixels.copy()
    d.set_props(l.id, lock_pixels=True)
    d.fill(l.id, (255, 0, 0, 255))
    d.apply_filter(l.id, "gaussian", {"radius": 3})
    assert np.array_equal(l.pixels, orig)
    d.set_props(l.id, lock_pixels=False, lock_pos=True)
    d.translate(l.id, 10, 10)
    assert (l.x, l.y) == (0, 0)
    a = np.zeros((200, 320, 4), np.uint8)
    a[50:100, 50:100] = (0, 255, 0, 255)
    top = d.add_layer(Layer("pixel", "a", a))
    d.set_props(top.id, lock_alpha=True)
    s = Stroke(d, top, "paint", color=(255, 0, 0), size=80)
    s.add(75, 75)
    s.add(140, 140)
    s.finish()
    assert np.array_equal(top.pixels[..., 3], a[..., 3]) and tuple(top.pixels[75, 75, :3]) == (255, 0, 0)
    d.fill(top.id, (0, 0, 255, 255))
    assert np.array_equal(top.pixels[..., 3], a[..., 3])


def test_style_fill_locks_roundtrip(tmp_path):
    d = _doc()
    sq = _solid(d, (0, 0, 255), (100, 60, 60, 60), "sq")
    d.set_props(sq.id, fill=0.4, lock_pos=True,
                style={"outer_glow": dict(styles.DEFAULTS["outer_glow"], on=True, size=10)})
    d.set_selection(np.pad(np.full((50, 50), 255, np.uint8), ((10, 140), (10, 260))))
    d.save_selection("头")
    before = d.render_full()
    p = str(tmp_path / "s.pcedit")
    save_project(d, p)
    d2 = load_project(p)
    l2 = next(l for l in d2.layers if l.name == "sq")
    assert l2.fill == 0.4 and l2.lock_pos and l2.style["outer_glow"]["on"]
    assert "头" in d2.channels and np.array_equal(d2.channels["头"], d.channels["头"])
    assert np.array_equal(d2.render_full(), before)


# ---------------------------------------------------------------------------
# 选区：色彩范围 / 选择并遮住 / 通道
# ---------------------------------------------------------------------------

def test_color_range_sampled_and_presets():
    px = _photo()
    px[50:100, 50:100, :3] = (200, 30, 30)
    lab = select.sample_lab(px, 70, 70)
    m = select.color_range(px, "sampled", [lab], 30)
    assert m[70, 70] == 255 and m[150, 250] == 0
    assert select.color_range(px, "reds")[70, 70] > 200
    hi = select.color_range(np.full((4, 4, 4), 250, np.uint8), "highlights")
    assert hi.min() == 255
    assert select.color_range(px, "sampled", [lab], 30, invert=True)[70, 70] == 0


def test_refine_follows_image_edge():
    h, w = 160, 200
    img = np.zeros((h, w, 4), np.uint8)
    img[..., 3] = 255
    yy, xx = np.mgrid[0:h, 0:w]
    edge = 100 + 6 * np.sin(yy / 7.0)                               # 波浪形的真实边缘
    img[xx < edge] = (240, 240, 240, 255)
    rough = np.zeros((h, w), np.uint8)
    rough[:, :100] = 255                                             # 粗糙的直边选区
    truth = (xx < edge)
    ref = select.refine(img, rough, radius=10)
    err0 = np.abs((rough > 127) != truth).mean()
    err1 = np.abs((ref > 127) != truth).mean()
    assert err1 < err0 * 0.5, (err0, err1)
    sm = select.refine(img, rough, radius=0, feather=6)
    assert 0 < sm[80, 100] < 255


def test_channels_save_load_modes_undo_and_canvas_ops():
    d = _doc()
    a = np.zeros((200, 320), np.uint8)
    a[20:60, 20:60] = 255
    b = np.zeros((200, 320), np.uint8)
    b[40:80, 40:80] = 255
    d.set_selection(a)
    d.save_selection("A")
    d.set_selection(b)
    d.load_selection("A", "add")
    assert d.selection[30, 30] == 255 and d.selection[70, 70] == 255
    d.load_selection("A", "sub")
    assert d.selection[50, 50] == 0 and d.selection[70, 70] == 255
    d.load_selection("A", "new", invert=True)
    assert d.selection[30, 30] == 0 and d.selection[150, 150] == 255
    d.history.undo()
    d.history.undo()
    d.history.undo()
    d.history.undo()
    d.history.undo()
    assert "A" not in d.channels
    d.history.redo()
    assert "A" in d.channels
    d.rotate(1)
    assert d.channels["A"].shape == (320, 200)
    d.crop(0, 0, 100, 100)
    assert d.channels["A"].shape == (100, 100)
    d.delete_channel("A")
    assert not d.channels
