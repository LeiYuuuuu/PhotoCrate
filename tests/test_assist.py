"""小匣助手（photocrate/assist）的测试：知识库里写的界面文字都真实存在、参数都合法，常见问法答对。

python -m pytest tests/test_assist.py -q
修图菜单路径、工具、页面 id 在真实窗口里的核对见 tests/ui_assist.py。
"""
from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from photocrate.assist import effects  # noqa: E402
from photocrate.assist.engine import Assistant, compose, diagnose  # noqa: E402
from photocrate.assist.features import BY_ID, DESCRIPTIVE, FEATURES, PHOTOS, STUDIO  # noqa: E402
from photocrate.assist.photo import analyze  # noqa: E402
from photocrate.assist.text import normalize  # noqa: E402
from photocrate.edit import adjust  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "photocrate"
A = Assistant()


def _source() -> str:
    """除了助手自己以外的全部源码（知识库里的文字必须来自真实界面，不能只在知识库里出现）。"""
    parts = []
    for p in ROOT.rglob("*.py"):
        if "assist" in p.parts or p.name == "assistant.py" or p.name.startswith("locale_en"):
            continue
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


SRC = _source()


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------

def test_feature_ids_unique_and_refs_valid():
    assert len(BY_ID) == len(FEATURES)
    for f in FEATURES:
        assert f.path[0] in (PHOTOS, STUDIO), f.id
        assert f.keys, f.id
        for s in f.see:
            assert s in BY_ID, (f.id, s)
    for r in effects.RECIPES:
        for s in r.features:
            assert s in BY_ID, (r.id, s)


@pytest.mark.parametrize("f", FEATURES, ids=lambda f: f.id)
def test_feature_path_labels_exist_in_ui(f):
    for i, s in enumerate(f.path[1:], 1):
        if s in DESCRIPTIVE:
            continue
        if f.path[i - 1] == "工具栏":                  # 工具：tooltip 以“名字（快捷键）”开头
            assert s.rstrip("）") in SRC, (f.id, s)
            continue
        assert s in SRC or s.rstrip("…") in SRC, (f.id, s)


def test_shortcuts_mentioned_exist():
    for f in FEATURES:
        for sc in f.shortcut.replace(" / ", "/").split("/"):
            sc = sc.strip()
            if len(sc) > 1 and "+" in sc:
                assert sc in SRC, (f.id, sc)


def test_effect_params_valid():
    bands = {b for b, _, _ in adjust.HSL_BANDS}

    def check(d):
        for k, v in d.items():
            if k.startswith("hsl:"):
                assert k[4:] in bands and len(v) == 3, k
            else:
                assert k in adjust.SLIDERS, k
    for a in effects.AXES:
        check(a.up)
        check(a.down)
    for r in effects.RECIPES:
        check(r.params)
    for s in effects.STYLES:
        assert set(adjust.SLIDERS) <= set(s.params), s.id
        if s.preset:
            assert s.preset in adjust.PRESETS


def test_vocab_words_not_shadowed():
    """每个词都能被找到（没有被另一个更长的同义词表项完全吞掉而失效的空词）。"""
    for w in effects.VOCAB:
        assert w == normalize(w), w


# ---------------------------------------------------------------------------
# 问答
# ---------------------------------------------------------------------------

FIND = [
    ("曲线在哪", "curves"), ("清晰度在哪里", "detail"), ("怎么去掉照片里的路人", "content_fill"),
    ("批量调色怎么弄", "copy_edits"), ("怎么把照片传到手机", "phone"), ("拍摄时间不对怎么改", "time_fix"),
    ("找出拍糊的照片", "blur_check"), ("闭眼", "eyes"), ("深色模式", "theme"), ("怎么导出图片", "export"),
    ("图层蒙版怎么加", "mask"), ("怎么加水印", "text"), ("全景拼接", "panorama"), ("怎么裁剪", "crop"),
    ("地平线歪了在哪拉直", "crop"), ("去痘痘", "heal"), ("液化在哪", "liquify"), ("撤销移动", "history"),
    ("重复照片", "dedupe"), ("怎么按日期整理文件夹", "partition"), ("以图搜图", "ai_search"),
    ("照片里的文字怎么复制", "ocr_copy"), ("隐私打码", "privacy"), ("快捷键", "shortcuts"),
    ("psd 怎么打开", "psd"), ("图像大小在哪", "image_size"), ("隐藏照片", "hidden"), ("加地点", "gps"),
    ("天空替换在哪", "sky"), ("滤镜预设在哪里", "presets"), ("色彩范围", "color_range"), ("怎么改分辨率", "image_size"),
    ("混合模式", "blend_mode"), ("导入存储卡", "import_card"), ("合并曝光", "merge_hdr"),
]


@pytest.mark.parametrize("q,fid", FIND)
def test_find_feature(q, fid):
    a = A.ask(q)
    ids = [f.id for f in a.features]
    assert fid in ids[:2], (q, a.kind, ids)


LOOK = [
    # (问题, 期望的 (参数, 符号) 列表)
    ("亮一点", [("exposure", 1)]),
    ("太亮了", [("exposure", -1)]),
    ("暗一些", [("exposure", -1)]),
    ("高光太亮", [("highlights", -1)]),
    ("暗部太暗", [("shadows", 1)]),
    ("更暖", [("temp", 1)]),
    ("不要那么黄", [("temp", -1)]),
    ("照片偏蓝", [("temp", 1)]),
    ("偏绿", [("tint", 1)]),
    ("颜色太艳了", [("saturation", -1)]),
    ("颜色寡淡", [("vibrance", 1)]),
    ("太灰了想通透一些", [("clarity", 1), ("blacks", -1)]),
    ("要朦胧感", [("fade", 1)]),
    ("噪点太多", [("denoise", 1)]),
    ("加点颗粒", [("grain", 1)]),
    ("去暗角", [("vignette", 1)]),
    ("暗角重一点", [("vignette", -1)]),
    ("天空更蓝一点", [("hsl:blue", 1)]),
    ("皮肤白一点", [("hsl:orange", 1)]),
    ("对比强一点", [("contrast", 1)]),
    ("不要那么锐利", [("clarity", -1)]),
    ("降低一下高光", [("highlights", -1)]),
    ("提高高光", [("highlights", 1)]),
    ("曝光+0.5 色温-20", [("exposure", 1), ("temp", -1)]),
]


def _sign(plan, key):
    if key.startswith("hsl:"):
        v = plan.params["hsl"][key[4:]]
        x = v[1] if key == "hsl:blue" else v[2]
        return (x > 0) - (x < 0)
    d = adjust.default_params()[key]
    return (plan.params[key] > d) - (plan.params[key] < d)


@pytest.mark.parametrize("q,expect", LOOK)
def test_look_directions(q, expect):
    a = A.ask(q)
    assert a.kind == "effect" and a.plan is not None, (q, a.kind)
    for key, s in expect:
        assert _sign(a.plan, key) == s, (q, key, a.plan.lines)


def test_styles_use_presets():
    a = A.ask("想要日系的感觉")
    assert a.plan.preset == "日系"
    assert a.plan.params["fade"] == adjust.PRESETS["日系"]["fade"]
    a = A.ask("电影感，暗角重一点")
    assert a.plan.preset == "电影感" and a.plan.params["vignette"] < adjust.PRESETS["电影感"]["vignette"]


def test_intensity_words():
    mild, plain, strong = (A.ask(q).plan.params["exposure"] for q in ("稍微亮一点", "更亮", "亮很多"))
    assert 0 < mild < plain < strong


def test_explicit_values():
    p = A.ask("曝光+0.5 色温-20").plan.params
    assert p["exposure"] == 0.5 and p["temp"] == -20
    p = A.ask("对比度调到30").plan.params
    assert p["contrast"] == 30
    p = A.ask("曝光加 0.3").plan.params
    assert p["exposure"] == pytest.approx(0.3)


def test_recipes():
    a = A.ask("背景虚化")
    assert [r.id for r in a.recipes] == ["bg_blur"] and "lensblur" in [f.id for f in a.features]
    a = A.ask("皮肤白一点、背景虚化")
    assert a.plan is not None and a.recipes and a.recipes[0].id == "bg_blur"
    assert A.ask("怎么瘦脸").recipes[0].id == "slim"


def test_fallbacks():
    assert A.ask("").kind == "greet"
    assert A.ask("你好").kind == "greet"
    assert A.ask("今天天气怎么样").kind == "unknown"
    a = A.ask("漂亮一点")
    assert a.kind == "unknown" and a.suggestions
    assert A.ask("这张照片怎么修").kind == "diagnose"


def test_every_combination_stays_in_range():
    """随便组合各种说法，结果都在滑块范围内。"""
    words = ["亮很多", "太暗", "非常暖", "冷", "鲜艳", "太艳", "通透", "朦胧", "日系", "胶片", "天空更蓝", "皮肤白一点",
             "加颗粒", "降噪", "暗角重一点", "高光太亮", "暗部太暗", "曝光+9", "色温-500"]
    for a, b, c in itertools.combinations(words, 3):
        ans = A.ask(f"{a}，{b}，{c}")
        assert ans.plan is not None
        p = ans.plan.params
        for k, (_, lo, hi, _) in adjust.SLIDERS.items():
            assert lo <= p[k] <= hi, (a, b, c, k, p[k])
        for v in p["hsl"].values():
            assert all(-100 <= x <= 100 for x in v)
        adjust.develop(np.full((4, 4, 3), 0.5, np.float32), p)     # 参数能直接拿去渲染


# ---------------------------------------------------------------------------
# 看照片
# ---------------------------------------------------------------------------

def _img(level: float, cast=(1.0, 1.0, 1.0), noise=0.15, sat=0.0) -> np.ndarray:
    rng = np.random.default_rng(1)
    base = np.clip(level + rng.normal(0, noise, (200, 300, 1)), 0, 1)
    img = np.repeat(base, 3, axis=2) * np.array(cast, np.float32)
    if sat:
        img[..., 0] = np.clip(img[..., 0] + sat, 0, 1)
    return (np.clip(img, 0, 1) * 255).astype(np.uint8)


def test_analyze_and_adapt():
    bright = analyze(_img(0.75))
    dark = analyze(_img(0.15, noise=0.05))
    assert bright.mean > 0.6 and dark.mean < 0.3
    p_bright = A.ask("亮一点", bright).plan.params["exposure"]
    p_none = A.ask("亮一点").plan.params["exposure"]
    assert 0 < p_bright < p_none
    assert any("偏亮" in n for n in A.ask("亮一点", bright).plan.notes)


def test_white_balance_estimate():
    warm = analyze(_img(0.5, cast=(1.0, 0.93, 0.78), noise=0.08))
    cool = analyze(_img(0.5, cast=(0.8, 0.92, 1.0), noise=0.08))
    assert warm.temp_fix < -10 < 10 < cool.temp_fix
    # 按估计值校正后，中性灰应该回到接近中性
    p = adjust.default_params()
    p["temp"], p["tint"] = warm.temp_fix, warm.tint_fix
    out = adjust.develop(np.full((2, 2, 3), (0.5, 0.465, 0.39), np.float32), p)[0, 0]
    assert abs(out[0] - out[2]) < 0.03 and abs(out[1] - (out[0] + out[2]) / 2) < 0.03


def test_diagnose():
    plan = diagnose(analyze(_img(0.12, noise=0.04)))
    assert plan is not None and plan.params["exposure"] > 0
    flat = analyze(_img(0.5, noise=0.05))
    plan = diagnose(flat)
    assert plan is not None and plan.params["lv_lo"] > 0 and plan.params["lv_hi"] < 255
    ok = analyze((np.random.default_rng(3).random((200, 300, 3)) ** 1.1 * 255).astype(np.uint8))
    a = A.ask("这张照片怎么修", ok)
    assert a.kind == "diagnose"
    gray = analyze(_img(0.12, noise=0.04))
    assert compose([], [], gray) is None
