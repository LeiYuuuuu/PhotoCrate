"""v2.3 新功能的测试：连拍里的闭眼 / 表情、隐藏相簿与密码、拷贝 / 粘贴编辑、统一色调、负片反转、
分享前隐私体检（文字规则、二维码、人脸、打码）、二维码编码、手机互传的网页服务。python -m pytest tests -q

都不需要模型：闭眼 / 表情用合成的检查结果，网页服务只监听 127.0.0.1（不会触发防火墙提示）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

from photocrate.ai import faceq  # noqa: E402
from photocrate.analyzers.builtin import group_similar  # noqa: E402
from photocrate.core import qr, smart  # noqa: E402
from photocrate.core.models import MemberFile, PhotoGroup  # noqa: E402
from photocrate.core.places import AnnotationStore  # noqa: E402
from photocrate.edit import colormatch, negative, quick  # noqa: E402
from photocrate.ops import privacy  # noqa: E402


def make(gid, dt="2026-05-01T10:00:00", name=None, role="jpeg", path=None):
    name = name or f"IMG_{gid:04d}.JPG"
    p = path or f"/p/{name}"
    g = PhotoGroup(gid, [MemberFile(p, role, 1000 + gid, 7)], p)
    if dt:
        g.feats["date"] = {"dt": dt, "source": "exif"}
    return g


# ---------------------------------------------------------------------------
# 闭眼 / 表情
# ---------------------------------------------------------------------------

def face(x, pc=None, eo=0.15, ex=None, y=0.3, w=0.1, h=0.14):
    f = {"b": [x, y, w, h], "eo": [eo, eo]}
    if pc is not None:
        f["pc"] = pc
    if ex is not None:
        f["ex"] = ex
    return f


HAPPY = [0.01, 0.01, 0.01, 0.9, 0.05, 0.01, 0.01]
SAD = [0.05, 0.05, 0.05, 0.05, 0.1, 0.65, 0.05]


def burst(faces_per_photo, blur=(120, 110, 100, 90)):
    gs = []
    for i, faces in enumerate(faces_per_photo):
        g = make(i, f"2026-05-01T10:00:0{i}")
        g.feats["phash"] = {"phash": "00000000000000ff"}
        g.feats["blur"] = {"score": blur[i]}
        g.feats["faceq"] = {"faces": faces, "fer": True, "clip": True}
        gs.append(g)
    group_similar(gs, 10, 12)
    return gs


def test_closed_eyes_compared_with_same_person():
    # 两个人：左边的人第 0 张闭眼（最清晰的一张），右边的人一直睁着眼
    gs = burst([
        [face(0.2, pc=0.95, eo=0.03), face(0.6, pc=0.2)],
        [face(0.21, pc=0.3, eo=0.16), face(0.61, pc=0.25)],
        [face(0.2, pc=0.35, eo=0.15), face(0.6, pc=0.2)],
    ])
    faceq.apply(gs)
    assert gs[0].feats["faceq"]["closed"] == [0]
    assert gs[1].feats["faceq"]["closed"] == [] and gs[2].feats["faceq"]["closed"] == []
    # 最清晰的第 0 张有人闭眼 → “最佳”换成没人闭眼里最清晰的第 1 张
    assert gs[1].feats["similar"]["best"] and not gs[0].feats["similar"]["best"]
    assert faceq.describe(gs[0]) == "1 人闭眼"


def test_squint_everywhere_is_not_closed():
    # 一直眯着眼笑（每张分都偏高、差不多）：不算闭眼
    gs = burst([[face(0.3, pc=0.8, eo=0.05)], [face(0.3, pc=0.82, eo=0.05)], [face(0.3, pc=0.78, eo=0.06)]])
    faceq.apply(gs)
    assert all(not g.feats["faceq"]["closed"] for g in gs)


def test_no_clip_no_eye_verdict_but_expression_works():
    gs = burst([[face(0.3, eo=0.02, ex=SAD)], [face(0.3, eo=0.2, ex=HAPPY)]], blur=(120, 100))
    faceq.apply(gs)
    assert gs[0].feats["faceq"]["closed"] == []                   # 没有 CLIP 的判断：不标闭眼
    assert gs[0].feats["faceq"]["bad"] == [0] and gs[1].feats["faceq"]["bad"] == []
    assert gs[1].feats["similar"]["best"]


NEUTRAL = [0.01, 0.01, 0.01, 0.05, 0.9, 0.01, 0.01]


def test_expression_needs_a_smiling_reference():
    # 别的几张只是平静（没在笑）：严肃 / 低头被模型看成“难过”也不提醒
    gs = burst([[face(0.3, ex=SAD)], [face(0.3, ex=NEUTRAL)]], blur=(120, 100))
    faceq.apply(gs)
    assert all(not g.feats["faceq"]["bad"] for g in gs)
    # 唯一在笑的那张闭着眼：不拿它当表情的参照
    gs = burst([[face(0.3, pc=0.2, eo=0.16, ex=SAD)], [face(0.3, pc=0.95, eo=0.02, ex=HAPPY)],
                [face(0.3, pc=0.25, eo=0.15, ex=NEUTRAL)]])
    faceq.apply(gs)
    assert gs[1].feats["faceq"]["closed"] == [0] and not gs[0].feats["faceq"]["bad"]


def test_single_person_tracked_through_zoom():
    # 连拍中拉近了一倍：只有一个人，仍然算同一个人
    gs = burst([[face(0.4, pc=0.2, w=0.1, h=0.14)], [face(0.3, pc=0.95, eo=0.02, w=0.25, h=0.35)]], blur=(100, 120))
    faceq.apply(gs)
    assert gs[1].feats["faceq"]["closed"] == [0] and gs[0].feats["similar"]["best"]


def test_lone_face_and_non_burst_not_judged():
    gs = burst([[face(0.1, pc=0.95, eo=0.02), face(0.5, pc=0.2)], [face(0.75, pc=0.2), face(0.9, pc=0.2)]])  # 位置差很远：不是同一个人
    faceq.apply(gs)
    assert all(not g.feats["faceq"]["closed"] for g in gs)
    for g in gs:                                                   # 不再是连拍组：旧的标记清掉
        g.feats.pop("similar")
        g.feats["faceq"]["closed"] = [0]
    faceq.apply(gs)
    assert all(g.feats["faceq"]["closed"] == [] for g in gs)


def test_pending_respects_models(monkeypatch):
    gs = burst([[face(0.3, pc=0.2)], [face(0.3, pc=0.2)]])
    gs.append(make(9, "2026-05-01T10:00:02"))
    gs[-1].feats["phash"] = {"phash": "00000000000000ff"}
    gs[-1].feats["faces"] = {"n": 0, "faces": []}                 # 扫描时没有脸：不用再看
    group_similar(gs, 10, 12)
    monkeypatch.setattr(faceq, "expr_ready", lambda: True)
    assert faceq.pending(gs, clip=True) == []
    gs[0].feats["faceq"]["clip"] = False
    assert faceq.pending(gs, clip=True) == [gs[0]]
    assert faceq.pending(gs, clip=False) == []


def test_eye_openness_on_drawn_eyes():
    import cv2
    open_eye = np.full((224, 224), 200, np.uint8)
    closed_eye = open_eye.copy()
    for cx, cy in faceq.TEMPLATE[:2] * 2:
        cv2.ellipse(open_eye, (int(cx), int(cy)), (22, 9), 0, 0, 360, 245, -1)       # 眼白
        cv2.circle(open_eye, (int(cx), int(cy)), 8, 40, -1)                           # 虹膜
        cv2.line(closed_eye, (int(cx) - 20, int(cy)), (int(cx) + 20, int(cy)), 40, 2)  # 只有睫毛线
    o, c = faceq.openness(faceq.eye_openness(open_eye)), faceq.openness(faceq.eye_openness(closed_eye))
    assert o > 0.18 and c < 0.06
    assert faceq.eye_openness(np.full((224, 224), 128, np.uint8)) == [None, None]


def test_similarity_transform_recovers_alignment():
    import cv2
    src = faceq.TEMPLATE * 3 + np.array([40, 25], np.float32)
    M = faceq.similarity_transform(src, faceq.TEMPLATE)
    back = cv2.transform(src[None], M)[0]
    assert np.abs(back - faceq.TEMPLATE).max() < 1e-3


# ---------------------------------------------------------------------------
# 隐藏相簿
# ---------------------------------------------------------------------------

def test_hidden_album_and_password(tmp_path):
    gs = [make(i, f"2026-0{i + 1}-01T10:00:00") for i in range(3)]
    for g in gs:
        g.feats["ocr"] = [{"t": "发票", "s": 0.9, "b": [0, 0, 1, 0, 1, 1, 0, 1]}]
    store = AnnotationStore(Path(tmp_path) / "ann.db")
    lib = SimpleNamespace(groups=gs, ai=SimpleNamespace(people=[]), trips=[], annotations=store)
    m = smart.Marks(store)
    m.set_fav([gs[0]], True)
    m.set_hidden([gs[0], gs[1]], True)
    m2 = smart.Marks(store)                                        # 重新载入也还在
    assert m2.is_hidden(gs[0]) and not m2.is_hidden(gs[2])
    assert [g.gid for g in smart.shown(gs, m2)] == [2]
    assert smart.search(lib, m2, "发票").gids == [2]
    sh = {a.key: a for s in smart.shelves(lib, m2) for a in s.albums}
    assert sh["media:favorite"].gids == []                         # 隐藏的收藏不出现在「收藏」里
    assert sorted(sh[smart.HIDDEN_KEY].gids) == [0, 1]             # 没设密码：直接能看
    m2.set_password("1234")
    assert m2.has_password() and m2.check_password("1234") and not m2.check_password("4321")
    raw = store.all("lock")["hidden"]
    assert "1234" not in json.dumps(raw)                           # 只存哈希
    sh = {a.key: a for s in smart.shelves(lib, m2) for a in s.albums}
    assert sh[smart.HIDDEN_KEY].gids == [] and sh[smart.HIDDEN_KEY].sub == "已锁定"
    m2.unlocked = True
    assert sorted(smart.album_by_key(lib, m2, smart.HIDDEN_KEY).gids) == [0, 1]
    m2.set_password(None)
    assert not m2.has_password() and m2.check_password("")
    m2.set_hidden([gs[0]], False)
    assert [g.gid for g in smart.shown(gs, m2)] == [2, 0] or sorted(g.gid for g in smart.shown(gs, m2)) == [0, 2]


# ---------------------------------------------------------------------------
# 编辑：负片、统一色调、拷贝 / 粘贴
# ---------------------------------------------------------------------------

def _fake_negative(pos):
    lin = negative.to_linear(pos)
    t = 10 ** (-(0.2 + 1.5 * lin)) * np.array([0.85, 0.55, 0.3], np.float32)     # 越亮越密 + 橙色片基
    return np.where(t <= 0.0031308, t * 12.92, 1.055 * t ** (1 / 2.4) - 0.055).astype(np.float32)


def test_negative_inversion_removes_orange_mask():
    rng = np.random.default_rng(1)
    pos = (rng.random((120, 160, 3)) * 0.85 + 0.05).astype(np.float32)
    out = negative.invert(_fake_negative(pos), "color")
    gray = out.mean(axis=2)
    assert np.corrcoef(gray.ravel(), pos.mean(axis=2).ravel())[0, 1] > 0.9      # 亮的还是亮的
    assert np.ptp(out.reshape(-1, 3).mean(axis=0)) < 0.05                       # 不再偏橙
    bw = negative.invert(_fake_negative(pos), "bw")
    assert np.allclose(bw[..., 0], bw[..., 1]) and np.allclose(bw[..., 1], bw[..., 2])


def test_color_match_moves_towards_reference():
    rng = np.random.default_rng(2)
    src = (rng.random((80, 100, 3)) * 0.6 + 0.2).astype(np.float32)
    warm = np.clip(src * np.array([1.25, 1.0, 0.7], np.float32), 0, 1)
    ref = colormatch.lab_stats(warm)
    full = colormatch.lab_stats(colormatch.transfer(src, ref, 1.0))
    half = colormatch.lab_stats(colormatch.transfer(src, ref, 0.5))
    s0 = colormatch.lab_stats(src)
    assert abs(full["mean"][2] - ref["mean"][2]) < 1.5
    assert abs(half["mean"][2] - (s0["mean"][2] + ref["mean"][2]) / 2) < 3
    assert np.allclose(colormatch.transfer(src, ref, 0.0), src)


def test_paste_state_parts_and_reauto():
    st = quick.QuickState(sliders={"exposure": 0.4}, auto={"shadows": 10.0}, preset=next(iter(quick.adjust.PRESETS)),
                          amount=60, rot=1, crop=(0.1, 0.1, 0.9, 0.9), negative="color",
                          match={"mean": [50, 0, 0], "std": [20, 5, 5], "amount": 0.5, "ref": "a.jpg"})
    assert set(quick.parts_of(st)) == {"adjust", "auto", "filter", "negative", "match", "geometry"}
    p = quick.paste_state(st, ["adjust", "auto", "negative"])
    assert p.sliders == {"exposure": 0.4} and p.reauto and not p.auto and p.negative == "color"
    assert p.preset is None and p.rot == 0 and not p.match_on() and not p.is_identity()
    rgb = np.full((60, 80, 3), 0.2, np.float32)
    one = quick.for_photo(p, rgb)
    assert one.auto and not one.reauto
    out = quick.apply(rgb, one)
    assert out.shape == rgb.shape and float(out.mean()) != pytest.approx(0.2, abs=1e-3)
    assert quick.paste_state(st, []).is_identity()
    assert "负片反转" in quick.describe(st) and "统一色调 50%" in quick.describe(st)


# ---------------------------------------------------------------------------
# 隐私体检
# ---------------------------------------------------------------------------

def test_privacy_text_rules():
    kinds = lambda s: {k for k, _ in privacy.text_hits(s)}            # noqa: E731
    assert kinds("电话：138 1234 5678") == {"phone"}
    assert kinds("身份证 11010519491231002X") == {"idcard"}
    assert privacy.id_checksum_ok("11010519491231002X") and not privacy.id_checksum_ok("110105194912310021")
    assert "疑似" in dict(privacy.text_hits("110105194912310021"))["idcard"]
    assert kinds("卡号 6222021234567890128") == {"bank"}
    assert kinds("京A·12345") == {"plate"}
    assert "express" in kinds("运单号：SF1234567890123") and "express" in kinds("收件人：张三")
    assert kinds("北京市朝阳区建国路88号") == {"address"}
    assert kinds("abc.de@qq.com") == {"email"}
    for ok in ("拉面 38元", "2026年10月26日 12:30", "订单号 202610261234567", "ISO 3200 1/250"):
        assert privacy.text_hits(ok) == [], ok
    assert privacy.mask("13812345678") == "138****5678"
    f = privacy.scan_text([{"t": "手机 13812345678", "b": [0.1, 0.2, 0.5, 0.2, 0.5, 0.3, 0.1, 0.3]}])
    assert f[0].kind == "phone" and f[0].box == (0.1, 0.2, 0.5, 0.3) and f[0].on


def test_privacy_finds_qr_code_and_redacts_only_there():
    from PIL import Image
    code = qr.to_image(qr.encode("https://example.com/pay?id=12345"), 6)
    canvas = np.full((600, 800, 3), 180, np.uint8)
    canvas[100:100 + code.shape[0], 400:400 + code.shape[1]] = code[..., None]
    found = privacy.scan_codes(canvas)
    assert [f.kind for f in found] == ["qr"]
    x0, y0, x1, y1 = found[0].box                                      # 框住码本身（不含四周 4 格白边）
    q = 4 * 6
    assert x0 <= (400 + q) / 800 and x1 >= (400 + code.shape[1] - q) / 800
    assert y0 <= (100 + q) / 600 and y1 >= (100 + code.shape[0] - q) / 600
    im = Image.fromarray(canvas)
    out = np.asarray(privacy.redact(im, [found[0].box], "block"))
    assert np.array_equal(out[400:, :300], canvas[400:, :300])         # 别处不动
    assert not np.array_equal(out[110:150, 410:450], canvas[110:150, 410:450])
    for style in ("mosaic", "blur"):
        assert np.asarray(privacy.redact(im, [found[0].box], style)).shape == canvas.shape


def test_privacy_faces_stranger_vs_main_and_named():
    g = make(1)
    sig = g.primary_file.sig
    faces = [{"box": [0.4, 0.2, 0.2, 0.3]}, {"box": [0.05, 0.1, 0.05, 0.07]}, {"box": [0.8, 0.1, 0.04, 0.06]}]
    named = SimpleNamespace(named=True, name="小王", gids=[1])
    out = privacy.scan_faces(g, faces, {f"{sig}#2": named}, set())
    assert [(f.kind, f.on) for f in out] == [("face", False), ("stranger", True), ("face", False)]
    assert out[2].label == "小王"
    assert len(privacy.scan_faces(g, faces, {}, {f"{sig}#1"})) == 2       # 不是真人的脸跳过


def test_export_render_applies_redaction(tmp_path):
    from PIL import Image
    from photocrate.ops.exporter import ExportOptions, render_one
    p = str(tmp_path / "a.jpg")
    Image.new("RGB", (400, 300), (40, 160, 220)).save(p, quality=95)
    g = make(3, path=p)
    plain, _, _ = render_one(g, ExportOptions(long_edge=0))
    red, _, _ = render_one(g, ExportOptions(long_edge=0, redact={3: [(0.0, 0.0, 0.5, 0.5)]}, redact_style="block"))
    a, b = np.asarray(plain).astype(int), np.asarray(red).astype(int)
    assert np.abs(a[200:, 250:] - b[200:, 250:]).max() < 3
    assert np.abs(a[20:100, 20:150] - b[20:100, 20:150]).mean() > 50


# ---------------------------------------------------------------------------
# 二维码
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ecl", ["L", "M", "Q", "H"])
def test_qr_roundtrip_with_opencv(ecl):
    import cv2
    det = cv2.QRCodeDetector()
    for text in ("http://192.168.1.23:8765/Ab3_xYz9QwEr/", "PhotoCrate", "手机互传 http://10.0.0.5:8765/k/"):
        m = qr.encode(text, ecl)
        assert len(m) == len(m[0]) and (len(m) - 17) % 4 == 0
        assert det.detectAndDecode(qr.to_image(m, 8))[0] == text
    with pytest.raises(ValueError):
        qr.encode("x" * 400, ecl)


# ---------------------------------------------------------------------------
# 手机互传
# ---------------------------------------------------------------------------

def test_safe_name():
    from photocrate.lan.server import safe_name
    assert safe_name("IMG_0001.JPG") == "IMG_0001.jpg"
    assert safe_name("..\\..\\Windows\\evil.jpg") == "evil.jpg"
    assert safe_name("../../a.HEIC") == "a.heic"
    assert safe_name("con.mp4") == "_con.mp4"
    assert safe_name("x.exe") is None and safe_name("photo") is None


@pytest.fixture
def server(tmp_path):
    from PIL import Image
    from photocrate.lan.server import Item, LanServer, Snapshot
    p = str(tmp_path / "a.jpg")
    Image.new("RGB", (900, 600), (200, 50, 50)).save(p)
    s = LanServer(str(tmp_path / "up"), host="127.0.0.1", port=0)
    s.set_snapshot(Snapshot([Item(1, "a.jpg", "2026-10-25T10:00:00", False, p, "jpeg")], [("favorite", "收藏", [1])]))
    s.start()
    yield s
    s.stop()


def _req(s, path, **kw):
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{s.port}/{path}", **kw), timeout=10)
        return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_lan_server_browse_and_token(server):
    s = server
    code, body, _ = _req(s, s.token + "/")
    assert code == 200 and b"api/photos" in body
    assert _req(s, "nottoken/")[0] == 404 and _req(s, "nottoken/api/photos")[0] == 404
    data = json.loads(_req(s, s.token + "/api/photos")[1])
    assert data["items"][0]["id"] == 1 and data["albums"][0]["ids"] == [1]
    code, body, h = _req(s, s.token + "/t/1")
    assert code == 200 and h["Content-Type"] == "image/jpeg" and body[:2] == b"\xff\xd8"
    code, body, h = _req(s, s.token + "/o/1", headers={"Range": "bytes=10-19"})
    assert code == 206 and len(body) == 10 and h["Content-Range"].startswith("bytes 10-19/")
    assert _req(s, s.token + "/t/2")[0] == 404 and _req(s, s.token + "/o/../a")[0] == 404
    s.allow_browse = False
    assert _req(s, s.token + "/t/1")[0] == 404
    assert json.loads(_req(s, s.token + "/api/photos")[1])["items"] == []


def test_lan_server_upload(server, tmp_path):
    s = server
    got = []
    s.on_upload = got.append
    data = b"\xff\xd8" + os.urandom(5000)

    def put(name, body=data):
        q = urllib.parse.urlencode({"name": name, "mtime": "1700000000000"})
        return _req(s, f"{s.token}/up?{q}", data=body, method="PUT")
    code, body, _ = put("IMG_0001.JPG")
    assert code == 200 and json.loads(body)["name"] == "IMG_0001.jpg"
    dst = tmp_path / "up" / "IMG_0001.jpg"
    assert dst.read_bytes() == data and abs(dst.stat().st_mtime - 1700000000) < 2
    assert json.loads(put("IMG_0001.JPG")[1]).get("dup")             # 同名同大小：跳过
    assert json.loads(put("IMG_0001.JPG", data + b"x")[1])["name"] == "IMG_0001 (1).jpg"   # 不覆盖
    assert put("evil.exe")[0] == 400
    assert json.loads(put("..\\..\\x.png")[1])["name"] == "x.png"
    assert sorted(os.listdir(tmp_path / "up")) == ["IMG_0001 (1).jpg", "IMG_0001.jpg", "x.png"]   # 没有残留的临时文件
    assert len(got) == 3
    s.allow_upload = False
    assert put("b.jpg")[0] == 400
