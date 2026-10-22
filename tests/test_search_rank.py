"""文字搜图的“对比”过滤和找相似的综合打分（用人工构造的向量，不需要模型）。"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from photocrate.ai.scenes import SCENES  # noqa: E402
from photocrate.ai.service import AIService  # noqa: E402
from photocrate.ai.visual import descriptor, hash_similarity, similarity  # noqa: E402

D = 48
SC = {name: i for i, (name, _, _) in enumerate(SCENES)}


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def e(i):
    v = np.zeros(D, dtype=np.float32)
    v[i] = 1
    return v


class G:
    def __init__(self, gid, phash=None):
        self.gid, self.files, self._ph = gid, [1], phash

    def feat(self, a, k, default=None):
        return self._ph if (a, k) == ("phash", "phash") else default


class Lib:
    def __init__(self, groups):
        self.g = {g.gid: g for g in groups}
        self.cache = type("C", (), {"put_emb_many": lambda *a: None})()

    def by_gid(self, gid):
        return self.g.get(gid)


class FakeClip:
    def __init__(self, table):
        self.table = table

    def encode_texts(self, texts):
        return np.stack([self.table.get(t.replace("一张", "").replace("的照片", ""), np.zeros(D)) for t in texts])


def _svc(groups):
    s = AIService(Lib(groups))
    s._labels = np.stack([e(i) for i in range(len(SCENES))])     # 每个场景一个方向
    return s


def test_text_search_drops_photos_of_another_subject():
    street, portrait, food = SC["城市街景"], SC["人像"], SC["美食"]
    others = [i for n, i in SC.items() if n not in ("人像", "自拍", "合影", "美食", "饮品甜点", "文档截图", "室内", "猫狗宠物")]
    q = unit(e(street) + 0.08 * sum(e(i) for i in others))       # “街道”：和主体类无关
    rng = np.random.default_rng(1)
    gs = [G(i) for i in range(30)]
    svc = _svc(gs)
    svc._clip = FakeClip({"街道": q})
    for g in gs:                                                 # 随机填充，构成“全库”
        svc.emb[g.gid] = unit(rng.normal(size=D) * 0.3 + e(SC["山景"]))
    svc.emb[0] = unit(e(street) + 0.3 * rng.normal(size=D) / np.sqrt(D))   # 街景
    svc.emb[1] = unit(e(street) * 0.8 + e(SC["夜景"]) * 0.6)     # 夜晚的街道：夜景不算竞争者，保留
    svc.emb[2] = unit(e(street) * 0.5 + e(portrait))             # 特写人像，背景有点街道
    svc.emb[3] = unit(e(street) * 0.75 + e(portrait) * 0.35)     # 街上有人，但主体还是街道
    svc.emb[4] = unit(e(street) * 0.4 + e(food))                 # 一碗面
    res = svc.search_text("街道")
    ids = [g for g, _ in res]
    assert set(ids[:3]) == {0, 1, 3}, res
    assert ids[0] == 0 and 2 not in ids and 4 not in ids, res
    assert all(a[1] >= b[1] for a, b in zip(res, res[1:]))


def test_text_search_for_the_subject_itself_keeps_it():
    portrait = SC["人像"]
    q = unit(e(portrait) + 0.05 * e(SC["合影"]))
    gs = [G(i) for i in range(25)]
    svc = _svc(gs)
    svc._clip = FakeClip({"人像": q})
    rng = np.random.default_rng(2)
    for g in gs:
        svc.emb[g.gid] = unit(rng.normal(size=D) * 0.3 + e(SC["海边"]))
    svc.emb[0] = unit(e(portrait) + 0.1 * rng.normal(size=D))
    assert svc.search_text("人像")[0][0] == 0


def _img(color, shift=0, dark=1.0):
    a = np.zeros((120, 160, 3), dtype=np.float32)
    a[:60] = (120, 170, 230)                                     # 天
    a[60:] = color                                               # 地
    a[30 + shift:100 + shift, 60:90] = (90, 80, 70)              # 一棵树
    return Image.fromarray(np.clip(a * dark, 0, 255).astype(np.uint8))


def test_similar_prefers_photos_that_also_look_alike():
    rng = np.random.default_rng(3)
    base = unit(rng.normal(size=D))
    gs = [G(i, phash=f"{rng.integers(0, 2**63):016x}") for i in range(40)]
    gs[0]._ph = gs[1]._ph = "f0f0f0f0f0f0f0f0"
    gs[2]._ph = "f0f0f0f0f0f0f0f1"
    svc = _svc(gs)
    for g in gs:
        svc.emb[g.gid] = unit(rng.normal(size=D))
        svc.vis[g.gid] = descriptor(Image.fromarray(rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)))
    svc.emb[0] = base                                            # 目标
    svc.vis[0] = descriptor(_img((90, 140, 60)))
    svc.emb[1] = unit(base + 0.35 * rng.normal(size=D) / np.sqrt(D))   # 同一处连拍：内容很像、画面也像
    svc.vis[1] = descriptor(_img((95, 140, 60), shift=4))
    svc.emb[2] = unit(base + 0.25 * rng.normal(size=D) / np.sqrt(D))   # 内容更像一点，但是夜里拍的
    svc.vis[2] = descriptor(_img((90, 140, 60), dark=0.15))
    svc.emb[3] = unit(base + 1.2 * rng.normal(size=D) / np.sqrt(D))    # 只是有点像
    res = svc.search_similar(0)
    ids = [g for g, _ in res]
    assert ids[0] == 1, res                                      # 看起来也像的排第一
    assert 0 not in ids
    assert all(a[1] >= b[1] for a, b in zip(res, res[1:]))
    assert len(ids) <= 4, res                                    # 随机的照片不出现
    assert all(s >= 0.3 for _, s in res)


def test_visual_helpers():
    a, b = descriptor(_img((90, 140, 60))), descriptor(_img((90, 140, 60), dark=0.2))
    near = descriptor(_img((92, 138, 62), shift=3))
    sims = similarity(a, np.stack([a, near, b]))
    assert sims[0] > 0.99 and sims[1] > 0.8 and sims[2] < 0.5, sims
    assert hash_similarity("ffff0000ffff0000", "ffff0000ffff0000") == 1.0
    assert hash_similarity(None, "ff") is None
