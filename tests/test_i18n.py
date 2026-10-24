"""英文界面测试：翻译表本身 + 切到英文后文件夹名 / 模板 / 地名是否翻译。python -m pytest tests -q"""
from __future__ import annotations

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

from photocrate import i18n  # noqa: E402
from photocrate.bootstrap import bootstrap  # noqa: E402
from photocrate.config import Settings  # noqa: E402
from photocrate.locale_en import EN, PROVINCES  # noqa: E402

bootstrap(Settings(), load_user_plugins=False)

from photocrate.core.models import MemberFile, PhotoGroup  # noqa: E402
from photocrate.partition.base import DIMENSIONS, PartitionContext  # noqa: E402
from photocrate.partition.engine import render_template  # noqa: E402

_PH = re.compile(r"\{\d+(?::[^}]*)?\}")


@pytest.fixture
def en():
    old = i18n.LANG
    i18n.set_language("en")
    yield
    i18n.set_language(old)


def test_table_placeholders_match():
    bad = [(zh, en) for zh, en in EN.items() if sorted(_PH.findall(zh)) != sorted(_PH.findall(en))]
    assert not bad, bad[:5]
    assert all(v.strip() or not k.strip() for k, v in EN.items())


def test_provinces_cover_all_province_level_divisions():
    full = [k for k in PROVINCES if k.endswith(("省", "自治区", "特别行政区")) or k in ("北京市", "天津市", "上海市", "重庆市")]
    assert len(full) == 34


def test_t_formats_and_falls_back(en):
    assert i18n.t("湖南省") == "Hunan"
    assert i18n.t("日本") == "Japan"
    assert i18n.t("长沙市") == "长沙市"  # 中国的市没有英文数据，保持原样
    assert i18n.t("这句不在表里 {0}", 3) == "这句不在表里 3"
    assert i18n.is_unknown(i18n.t("未知地点"))
    i18n.set_language("zh")
    assert i18n.t("湖南省") == "湖南省"


def test_template_and_dimension_in_english(en):
    ctx = PartitionContext(Settings().snapshot())
    g = PhotoGroup(0, [MemberFile("/x/IMG_1.JPG", "jpeg", 1, 1)], "/x/IMG_1.JPG")
    g.feats["date"] = {"dt": "2026-10-19T10:00:00", "source": "exif"}
    g.feats["geo"] = {"has_gps": True, "country": "中国", "province": "湖南省", "city": None}
    # 英文别名和中文占位符都能用
    assert render_template("{year}/{year}-{month} {city|province|Unknown place}", g, ctx) == ["2026", "2026-10 Hunan"]
    assert render_template("{年}/{市|省|未知地点}", g, ctx) == ["2026", "Hunan"]
    g.feats["geo"] = {"has_gps": False}
    assert render_template("{year}/{city|province|Unknown place}", g, ctx) == ["2026", "Unknown place"]
    d = DIMENSIONS["rating"]
    g.feats["rating"] = {"rating": 4, "label": "Blue", "flag": None}
    assert d.segments(g, d.resolve_opts({}), ctx) == ["4 stars"]
