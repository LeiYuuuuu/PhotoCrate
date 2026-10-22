"""“不是真人的脸”过滤：动物、玩偶、动漫等不参与人物分组；用户可以标记 / 恢复。

第二个用例需要真实的中文 CLIP 模型（环境变量 PHOTOCRATE_TEST_CLIP 指向放有
chinese_clip_vitb16_q8.onnx 与 chinese_clip_vocab.txt 的文件夹）和人脸模型（PHOTOCRATE_TEST_MODELS），
只用公开的测试图片（skimage 的宇航员 / 猫、OpenCV 的 lena / messi）。
"""
from __future__ import annotations

import base64
import io
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from photocrate.ai.faces import encode_emb  # noqa: E402
from photocrate.ai.service import HUMAN_MIN, AIService  # noqa: E402


class Store:
    def __init__(self):
        self.t = {}

    def all(self, table):
        return dict(self.t.get(table, {}))

    def set_many(self, items, table):
        self.t.setdefault(table, {}).update(items)

    def clear_many(self, keys, table):
        for k in keys:
            self.t.get(table, {}).pop(k, None)


class Cache:
    def __init__(self):
        self.d = {}

    def get_emb_many(self, keys, model):
        return {k: self.d[(k, model)] for k in keys if (k, model) in self.d}

    def put_emb_many(self, items, model):
        self.d.update({(k, model): np.asarray(v, dtype=np.float32) for k, v in items.items()})


class PF:
    def __init__(self, sig):
        self.sig = sig


class G:
    def __init__(self, gid, faces):
        self.gid, self.files, self.primary_file = gid, [1], PF(f"s{gid}")
        self.feats = {"faces": {"n": len(faces), "faces": faces}}


class Lib:
    def __init__(self, groups):
        self.groups = groups
        self.annotations, self.cache = Store(), Cache()
        self.settings = {"face_threshold": 0.42}

    def by_gid(self, gid):
        return next((g for g in self.groups if g.gid == gid), None)


def _face(vec, crop=""):
    return {"box": [0.3, 0.3, 0.2, 0.2], "score": 0.9, "emb": encode_emb(vec), "crop": crop}


def test_nonhuman_faces_are_excluded_and_can_be_restored():
    rng = np.random.default_rng(0)
    alice, deer = rng.normal(size=128), rng.normal(size=128)
    groups = [G(i, [_face(alice + rng.normal(size=128) * 0.1)]) for i in range(3)]
    groups += [G(10 + i, [_face(deer + rng.normal(size=128) * 0.1)]) for i in range(3)]
    svc = AIService(Lib(groups))
    svc.clip_ready = lambda: False
    svc.face_human = {f"s{i}#0": 0.97 for i in range(3)} | {f"s{10 + i}#0": 0.02 for i in range(3)}
    people = svc.recluster(groups)
    assert len(people) == 1 and sorted(people[0].gids) == [0, 1, 2]
    assert sorted(f.gid for f in svc.nonfaces) == [10, 11, 12]
    assert "people" not in groups[3].feats and svc.stats(groups)["nonfaces"] == 3
    # 用户：其中一张其实是人脸 → 恢复；另一组人标成“不是人脸”
    svc.mark_faces(["s10#0"], human=True)
    svc.mark_faces(["s0#0"], human=False)
    svc.recluster(groups)
    assert sorted(f.gid for f in svc.nonfaces) == [0, 11, 12]
    assert svc.lib.annotations.all("face_fix")["s10#0"] == {"human": True}


def _b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").resize((112, 112)).save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


CLIP_DIR = os.environ.get("PHOTOCRATE_TEST_CLIP")
FACE_DIR = os.environ.get("PHOTOCRATE_TEST_MODELS")


@pytest.mark.skipif(not (CLIP_DIR and FACE_DIR), reason="需要真实的 CLIP 与人脸模型")
def test_real_clip_tells_people_from_animals():
    import skimage.data as sd

    from photocrate.ai.clip import ClipEngine
    from photocrate.ai.faces import FaceEngine
    fe = FaceEngine(str(Path(FACE_DIR) / "face_detection_yunet_2023mar.onnx"),
                    str(Path(FACE_DIR) / "face_recognition_sface_2021dec.onnx"))
    people = [Image.fromarray(sd.astronaut())]
    for name in ("lena.jpg", "messi5.jpg"):
        if (Path(FACE_DIR) / name).exists():
            people.append(Image.open(Path(FACE_DIR) / name).convert("RGB"))
    groups = []
    for i, im in enumerate(people):
        fs = fe.detect(np.asarray(im.convert("RGB")), 20)
        assert fs, f"第 {i} 张公开测试图没检测到人脸"
        groups.append(G(i, fs[:1]))
    cat = Image.fromarray(sd.chelsea()).crop((80, 20, 330, 270))             # 模拟把猫脸误检成人脸
    groups.append(G(50, [_face(np.ones(128), _b64(cat))]))
    svc = AIService(Lib(groups))
    svc.clip_ready = lambda: True
    svc._clip = ClipEngine(Path(CLIP_DIR) / "chinese_clip_vitb16_q8.onnx", Path(CLIP_DIR) / "chinese_clip_vocab.txt")
    assert svc.screen_faces(groups) == len(groups)
    for g in groups[:-1]:
        assert svc.face_human[f"s{g.gid}#0"] > 0.8, svc.face_human
    assert svc.face_human["s50#0"] < HUMAN_MIN / 2, svc.face_human
    svc.recluster(groups)
    assert [f.gid for f in svc.nonfaces] == [50]
    assert svc.screen_faces(groups) == 0                                    # 结果有缓存
