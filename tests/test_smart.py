"""「相册」数据层（时间线、智能相簿、收藏 / 相簿、搜索）、基础编辑与文字识别的测试。python -m pytest tests -q

文字识别的端到端测试需要模型：PHOTOCRATE_MODELS 指向放着 PP-OCRv4 两个 ONNX 文件的文件夹，没有时跳过。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

from photocrate.ai.ocr import norm  # noqa: E402
from photocrate.core import smart  # noqa: E402
from photocrate.core.models import MemberFile, PhotoGroup  # noqa: E402
from photocrate.core.places import AnnotationStore  # noqa: E402
from photocrate.edit import adjust, quick  # noqa: E402


def make(gid, dt=None, city=None, country="日本", ocr=None, scene=None, role="jpeg", name=None, mtime=1):
    name = name or f"IMG_{gid:04d}.JPG"
    g = PhotoGroup(gid, [MemberFile(f"/p/{name}", role, 100 + gid, mtime)], f"/p/{name}")
    if dt:
        g.feats["date"] = {"dt": dt, "source": "exif"}
    if city:
        g.feats["geo"] = {"country": country, "province": None, "city": city, "has_gps": True}
    if ocr is not None:
        g.feats["ocr"] = [{"t": t, "s": 0.9, "b": [0, 0, 1, 0, 1, 1, 0, 1]} for t in ocr]
    if scene:
        g.feats["scene"] = {"scene": scene, "group": "美食", "prob": 0.6, "top3": [[scene, 0.6]]}
    g.feats["camera"] = {"make": "Sony", "model": "ILCE-7M4", "source": "camera"}
    return g


def fake_lib(groups, tmp_path=None):
    store = AnnotationStore(Path(tmp_path or tempfile.mkdtemp()) / "ann.db")
    ai = SimpleNamespace(people=[], clip_ready=lambda: False, emb={})
    return SimpleNamespace(groups=groups, ai=ai, trips=[], annotations=store)


@pytest.fixture
def lib(tmp_path):
    gs = [
        make(0, "2026-04-02T12:10:00", "东京", ocr=["一兰拉面 ICHIRAN"], scene="美食"),
        make(1, "2026-04-02T20:30:00", "东京", ocr=["东京塔"]),
        make(2, "2026-04-03T09:00:00", "京都"),
        make(3, "2025-07-10T21:00:00", "长沙市", country="中国", ocr=["发票", "合计 128.00 元"]),
        make(4, None, None, role="video", name="MOV_0001.MP4"),
        make(5, "2024-12-25T08:00:00", "长沙市", country="中国", name="Screenshot_2024.png", role="png"),
    ]
    gs[5].feats["camera"]["source"] = "screenshot"
    return fake_lib(gs, tmp_path)


def test_norm_and_tokenize():
    assert norm("ＡＢＣ　拉 面") == "abc拉面"
    assert smart.tokenize('东京  拉面，“tokyo tower”') == ["东京", "拉面", "tokyo tower"]


def test_date_matcher():
    t = date(2026, 10, 25)
    m = smart.date_matcher
    assert m("2025", t)(datetime(2025, 3, 1)) and not m("2025", t)(datetime(2024, 3, 1))
    assert m("2026年4月", t)(datetime(2026, 4, 9)) and not m("2026年4月", t)(datetime(2026, 5, 9))
    assert m("2026-04-02", t)(datetime(2026, 4, 2, 23))
    assert m("4月", t)(datetime(2019, 4, 1)) and m("十二月", t)(datetime(2019, 12, 1))
    assert m("去年", t)(datetime(2025, 1, 1)) and m("昨天", t)(datetime(2026, 10, 24, 8))
    assert m("夏天", t)(datetime(2020, 7, 1)) and not m("夏天", t)(datetime(2020, 10, 1))
    assert m("晚上", t)(datetime(2020, 7, 1, 20)) and not m("晚上", t)(datetime(2020, 7, 1, 9))
    assert m("拉面", t) is None and m("123", t) is None


def test_timeline_levels(lib):
    days = smart.timeline(lib.groups, "day")
    assert [s.key for s in days] == ["d2026-04-03", "d2026-04-02", "d2025-07-10", "d2024-12-25", "undated"]
    assert days[1].gids == [1, 0] and days[1].sub.startswith("东京")
    months = smart.timeline(lib.groups, "month")
    assert [s.key for s in months][:2] == ["m2026-04", "m2025-07"]
    years = smart.timeline(lib.groups, "year")
    assert [s.key for s in years] == ["y2026", "y2025", "y2024", "undated"]
    assert all(s.cover in s.gids for s in years)


def test_search_categories(lib):
    r = smart.search(lib, None, "拉面")
    assert r.gids == [0] and r.text_hits == {0: ["拉面"]}
    assert any(s.cat == "text" for s in r.suggestions)
    # 地点 + 文字同时满足
    assert smart.search(lib, None, "东京 东京塔").gids == [1]
    # 地点（中文）、日期 + 时段
    assert smart.search(lib, None, "东京").gids == [1, 0]
    assert smart.search(lib, None, "2026 晚上").gids == [1]
    # 一句话被拆成两行也能搜到；跨行的两行都高亮
    r = smart.search(lib, None, "发票合计")
    assert r.gids == [3]
    assert smart.matched_lines(lib.groups[3], r.text_hits[3]) == [0, 1]
    # 类型词、场景、相机、文件名
    assert smart.search(lib, None, "视频").gids == [4]
    assert smart.search(lib, None, "截图").gids == [5]
    assert 0 in smart.search(lib, None, "美食").gids
    assert len(smart.search(lib, None, "ILCE").gids) == 6
    assert smart.search(lib, None, "MOV_0001").gids == [4]
    assert smart.search(lib, None, "不存在的词").gids == []


def test_search_restrict(lib):
    # “东京”既是地点（0、1）又出现在照片文字里（1）：只按文字找
    r = smart.search(lib, None, "东京")
    cats = {s.cat for s in r.suggestions}
    assert {"place", "text"} <= cats
    r2 = smart.search(lib, None, "东京", restrict={0: "text"})
    assert r2.gids == [1]


def test_marks_and_albums(lib, tmp_path):
    marks = smart.Marks(lib.annotations)
    g0, g1, g2 = lib.groups[:3]
    assert marks.toggle_fav([g0, g1]) is True
    assert marks.is_fav(g0) and marks.is_fav(g1) and not marks.is_fav(g2)
    assert marks.toggle_fav([g0, g1]) is False and not marks.is_fav(g0)
    marks.set_fav([g2], True)
    aid = marks.create("旅行精选")
    assert marks.add([g0, g1], aid) == 2 and marks.add([g0], aid) == 0
    assert marks.album_gids(lib.groups, aid) == [1, 0]
    # 重新载入（另一个实例）数据还在
    m2 = smart.Marks(lib.annotations)
    assert m2.is_fav(g2) and m2.albums[aid].name == "旅行精选" and m2.in_album(g0, aid)
    assert smart.search(lib, m2, "旅行精选").gids == [1, 0]
    assert smart.search(lib, m2, "收藏").gids == [2]
    m2.remove([g0], aid)
    assert m2.album_gids(lib.groups, aid) == [1]
    m2.rename(aid, "改名了")
    m2.delete(aid)
    assert aid not in smart.Marks(lib.annotations).albums
    assert not smart.Marks(lib.annotations).in_album(g1, aid)


def test_shelves(lib):
    marks = smart.Marks(lib.annotations)
    marks.set_fav([lib.groups[2]], True)
    sh = {s.key: s for s in smart.shelves(lib, marks)}
    media = {a.key: a for a in sh["media"].albums}
    assert media["media:favorite"].gids == [2]
    assert media["media:video"].gids == [4]
    assert media["media:screenshot"].gids == [5]
    assert media["media:text"].gids == [1, 3, 0] or sorted(media["media:text"].gids) == [0, 1, 3]
    assert "media:document" not in media                     # 没有文档：不显示这个相簿
    places = {a.title: a.gids for a in sh["places"].albums}
    assert sorted(places["东京"]) == [0, 1] and sorted(places["长沙市"]) == [3, 5]
    assert [a.title for a in sh["scenes"].albums] == ["美食"]
    assert smart.album_by_key(lib, marks, "place:东京").gids == [1, 0]


# ---------------------------------------------------------------------------
# 基础编辑
# ---------------------------------------------------------------------------

def _img(h=60, w=90):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return np.dstack([xx / w, yy / h, 0.5 * np.ones_like(xx)]).astype(np.float32)


def test_quick_identity_and_preset_strength():
    img = _img()
    st = quick.QuickState()
    assert st.is_identity()
    assert np.array_equal(quick.apply(img, st), img)
    st.preset, st.amount = "黑白", 0
    assert adjust.is_identity(st.params())
    st.amount = 100
    p = st.params()
    assert p["saturation"] == adjust.PRESETS["黑白"]["saturation"]
    st.amount = 50
    assert st.params()["saturation"] == pytest.approx(adjust.PRESETS["黑白"]["saturation"] / 2)
    # 滑块叠加在滤镜上，并夹在范围内
    st.sliders["saturation"] = -100
    assert st.params()["saturation"] == -100


def test_quick_geometry():
    img = _img(60, 90)
    st = quick.QuickState(rot=1)
    out = quick.apply_geometry(img, st)
    assert out.shape[:2] == (90, 60)
    assert np.allclose(out[0, -1], img[0, 0])                 # 顺时针 90°：左上角到了右上角
    st = quick.QuickState(flip=True)
    assert np.allclose(quick.apply_geometry(img, st)[:, 0], img[:, -1])
    st = quick.QuickState(crop=(0.0, 0.0, 0.5, 0.5))
    assert quick.apply_geometry(img, st).shape[:2] == (30, 45)
    st = quick.QuickState(angle=10)
    out = quick.apply_geometry(img, st)
    assert out.shape == img.shape and quick.straighten_scale(90, 60, 10) > 1


def test_auto_params_brightens_dark_photo():
    dark = (_img(40, 40) * 0.15).astype(np.float32)
    p = quick.auto_params(dark)
    assert p.get("exposure", 0) > 0.3
    st = quick.QuickState(auto=p)
    assert float(quick.apply(dark, st).mean()) > float(dark.mean()) * 1.5


# ---------------------------------------------------------------------------
# 文字识别（需要模型）
# ---------------------------------------------------------------------------

def _ocr_models():
    d = os.environ.get("PHOTOCRATE_MODELS")
    if not d:
        return None
    det, rec = Path(d) / "ch_PP-OCRv4_det_infer.onnx", Path(d) / "ch_PP-OCRv4_rec_infer.onnx"
    return (det, rec) if det.exists() and rec.exists() else None


@pytest.mark.skipif(_ocr_models() is None, reason="没有文字识别模型（设置 PHOTOCRATE_MODELS）")
def test_ocr_engine_reads_text():
    from PIL import Image, ImageDraw, ImageFont
    from photocrate.ai.ocr import OcrEngine
    font = next((p for p in (r"C:\Windows\Fonts\msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
                 if os.path.exists(p)), None)
    if font is None:
        pytest.skip("没有中文字体")
    im = Image.new("RGB", (1200, 700), (200, 210, 190))
    d = ImageDraw.Draw(im)
    d.text((80, 100), "一兰拉面 ICHIRAN", fill=(20, 20, 20), font=ImageFont.truetype(font, 64))
    d.text((80, 400), "营业时间 9:00-23:00", fill=(250, 250, 250), font=ImageFont.truetype(font, 36))
    eng = OcrEngine(*_ocr_models())
    lines = eng.read(np.asarray(im)[..., ::-1].copy())
    text = "".join(l["t"] for l in lines)
    assert "拉面" in text and "ICHIRAN" in text and "23:00" in text, lines
    b = lines[0]["b"]
    assert len(b) == 8 and all(0 <= v <= 1 for v in b) and b[1] < 0.3
