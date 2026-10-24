"""合并曝光（HDR）引擎测试：对齐精度、清晰度、去重影、手动修补、工程文件。python -m pytest tests -q"""
from __future__ import annotations

import math
import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

cv2 = pytest.importorskip("cv2")

from photocrate.edit import hdr  # noqa: E402

W, H, PAD = 900, 600, 40
OBJ = [(120, 300), (390, 290), (650, 320)]
OW, OH = 110, 80
SHAKE = [(4.3, -2.7, 0.3), (0.0, 0.0, 0.0), (-3.6, 5.1, -0.25)]


def _scene(seed=11):
    rng = np.random.default_rng(seed)
    big = (H + 2 * PAD, W + 2 * PAD)
    base = np.zeros(big + (3,), np.float32)
    for sigma, amp in ((30, 0.5), (8, 0.25), (2.5, 0.15), (0.9, 0.08)):
        base += amp * cv2.GaussianBlur(rng.random(big + (3,)).astype(np.float32), (0, 0), sigma) * (1 + sigma / 10)
    base = (base - base.min()) / (base.max() - base.min())
    for _ in range(40):
        x, y = rng.integers(0, big[1] - 40), rng.integers(0, big[0] - 40)
        base[y:y + rng.integers(6, 40), x:x + rng.integers(6, 40)] = rng.random(3)
    return base ** 2.2 * np.linspace(4, 0.1, big[0], dtype=np.float32)[:, None, None]


def _matrix(i):
    dx, dy, a = SHAKE[i]
    M = np.vstack([cv2.getRotationMatrix2D(((W + 2 * PAD) / 2, (H + 2 * PAD) / 2), a, 1.0), [0, 0, 1]])
    M[:2, 2] += (dx, dy)
    T = np.array([[1, 0, PAD], [0, 1, PAD], [0, 0, 1.0]])
    return np.linalg.inv(T) @ M @ T


def _bracket(moving=True, same_object=False):
    rad = _scene()
    obj = np.random.default_rng(5).random((OH, OW, 3)).astype(np.float32) * 0.6 + 0.2
    frames = []
    for i, ev in enumerate((-2, 0, 2)):
        r = rad.copy()
        x, y = OBJ[1 if same_object or not moving else i]
        r[y + PAD:y + PAD + OH, x + PAD:x + PAD + OW] = obj * 2.0
        M = _matrix(i)
        T = np.array([[1, 0, PAD], [0, 1, PAD], [0, 0, 1.0]])
        r = cv2.warpAffine(r, (T @ M @ np.linalg.inv(T))[:2], (W + 2 * PAD, H + 2 * PAD),
                           flags=cv2.INTER_CUBIC)[PAD:PAD + H, PAD:PAD + W]
        v = np.clip(r * 2.0 ** ev, 0, 1) ** (1 / 2.2)
        frames.append(hdr.Frame(np.clip(v * 255 + 0.5, 0, 255).astype(np.uint8), f"F{i}", math.log2(1 / 100) + ev))
    return frames


@pytest.fixture(scope="module")
def frames():
    return _bracket()


def _err(H_est, i):
    true = _matrix(1) @ np.linalg.inv(_matrix(i))
    return hdr._max_shift(np.linalg.inv(true) @ H_est, W, H)


def test_alignment_subpixel_across_exposures(frames):
    for i in (0, 2):
        H_est, how, resid = hdr.estimate_alignment(frames[1].rgb, frames[i].rgb)
        assert H_est is not None and how.endswith("+lk"), how
        assert _err(H_est, i) < 0.35, (i, _err(H_est, i))
        assert resid < 0.5


def test_still_frames_are_not_resampled(frames):
    img = frames[1].rgb
    out, valid = hdr.warp(img, np.eye(3))
    assert out is img and valid is None              # 三脚架：没动就原样用，不重新取样


def test_reference_is_middle_exposure(frames):
    smalls = [hdr._small(f.rgb, 800)[0] for f in frames]
    assert hdr.pick_reference(smalls, frames) == 1
    assert hdr.pick_reference(smalls) == 1           # 没有 EXIF 时按亮度


def _acutance(img, m):
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32)
    b1, b2, b3 = (cv2.GaussianBlur(g, (0, 0), s) for s in (0.8, 2.0, 5.0))
    return float(np.mean((b1 - b2)[m] ** 2) / np.mean((b2 - b3)[m] ** 2))


def test_merge_keeps_sharpness_and_removes_ghosts(frames):
    res = hdr.merge(frames)
    assert res.ref == 1 and not res.skipped
    assert [round(e) for e in res.evs] == [-2, 0, 2]
    ref = frames[1].rgb
    g = cv2.cvtColor(ref, cv2.COLOR_RGB2GRAY)
    m = np.zeros((H, W), bool)
    m[40:-40, 40:-40] = True
    for x, y in OBJ:
        m[y - 30:y + OH + 30, x - 30:x + OW + 30] = False
    well = m & (g > 50) & (g < 200)
    assert _acutance(res.image, well) / _acutance(ref, well) > 0.95     # 不比参考帧糊
    # 另外两张里物体所在的地方：合并结果和参考帧一样是背景
    lap = lambda a: cv2.Laplacian(cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).astype(np.float32), (0, 0), 1), cv2.CV_32F)  # noqa: E731
    for x, y in (OBJ[0], OBJ[2]):
        c = np.corrcoef(lap(res.image)[y:y + OH, x:x + OW].ravel(), lap(ref)[y:y + OH, x:x + OW].ravel())[0, 1]
        assert c > 0.8, ((x, y), c)
    assert 0.005 < res.ghost_fraction < 0.2


def test_deghost_off_leaves_ghosts(frames):
    res = hdr.merge(frames, deghost="off")
    x, y = OBJ[0]
    lap = lambda a: cv2.Laplacian(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).astype(np.float32), cv2.CV_32F)  # noqa: E731
    c = np.corrcoef(lap(res.image)[y:y + OH, x:x + OW].ravel(), lap(frames[1].rgb)[y:y + OH, x:x + OW].ravel())[0, 1]
    assert c < 0.8 and res.ghost_fraction == 0


def test_no_false_ghosts_on_static_scene():
    res = hdr.merge(_bracket(same_object=True))
    assert res.ghost_fraction < 0.01, res.ghost_fraction


def test_merge_errors():
    f = _bracket()
    with pytest.raises(hdr.MergeError):
        hdr.merge(f[:1])
    small = hdr.Frame(f[0].rgb[:300, :400], "small")
    with pytest.raises(hdr.MergeError):
        hdr.merge([f[1], small])


def test_cancel():
    import threading
    ev = threading.Event()
    ev.set()
    with pytest.raises(hdr.Cancelled):
        hdr.merge(_bracket(), cancel=ev)


def test_document_patch_and_project_roundtrip(frames, tmp_path):
    from photocrate.edit import io as eio
    from photocrate.edit.document import Layer, SourceInfo
    res = hdr.merge(frames)
    doc = hdr.to_document(res, frames)
    assert [l.visible for l in doc.layers] == [True, False, False, True]
    srcs = hdr.merge_sources(doc)
    assert srcs[0].params["ref"] and hdr.merge_result_layer(doc) is doc.layers[-1]
    # 手动修补：用 −2 EV 那张，亮度匹配后和合并结果接近、补丁像素来自源图（没有重新取样）
    sel = np.zeros((H, W), np.uint8)
    sel[100:220, 200:420] = 255
    dark = next(l for l in srcs if l.params["ev"] < -1)
    target = doc.layers[-1].pixels
    px, mk, x, y = hdr.ghost_patch(dark.pixels, target, sel, 8.0, True)
    assert mk[100 - y + 10, 200 - x + 10] == 255 and mk[0, 0] < 255
    inner = (slice(130 - y, 190 - y), slice(240 - x, 380 - x))
    assert abs(float(px[inner][..., :3].mean()) - float(target[130:190, 240:380, :3].mean())) < 12
    raw, _, _, _ = hdr.ghost_patch(dark.pixels, target, sel, 8.0, False)
    assert np.array_equal(raw[..., :3], dark.pixels[y:y + raw.shape[0], x:x + raw.shape[1], :3])
    l = Layer("pixel", "fix", px, x, y, params={"ghost_fix": dark.params["merge_source"]})
    l.mask = mk
    doc.add_layer(l, len(doc.layers))
    doc.source = SourceInfo(path=str(tmp_path / "IMG_1.JPG"), name="IMG_1", suffix="_HDR")
    assert eio.default_project_path(doc).endswith("IMG_1_HDR.pcedit")
    assert eio.suggest_export_path(doc).endswith("IMG_1_HDR.jpg")
    p = eio.default_project_path(doc)
    eio.save_project(doc, p)
    d2 = eio.load_project(p)
    assert d2.source.suffix == "_HDR" and len(hdr.merge_sources(d2)) == 3
    assert hdr.merge_result_layer(d2) is not None and d2.layers[-1].params.get("ghost_fix") is not None
