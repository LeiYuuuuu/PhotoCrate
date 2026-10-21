"""AI 服务：在照片库上建立 CLIP 向量索引、做场景识别、文字 / 以图搜索，以及人物聚类。

- 图片向量用缩略图（长边 360）计算，按主图指纹缓存在 cache.db 的 emb 表，移动 / 改名后依然命中；
- 场景识别和人物聚类都只用缓存好的向量，改设置后重算几乎是瞬时的；
- 已命名的人、人脸更正存在 annotations.db（people / face_fix），不随缓存清空。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from . import models

log = logging.getLogger(__name__)

CLIP_KEY = "cnclip-vitb16-q8"


class AIService:
    def __init__(self, library):
        self.lib = library
        self._clip = None
        self._clip_error: str | None = None
        self._labels = None
        self._lock = threading.RLock()
        self.emb: dict[int, Any] = {}           # gid → 向量
        self.people: list = []                  # ai.people.Cluster
        self.loners: list = []

    # ---- 模型 ------------------------------------------------------------
    def clip_ready(self) -> bool:
        return models.ready("clip") and models.onnxruntime_ok()

    def clip(self):
        with self._lock:
            if self._clip is None:
                from .clip import ClipEngine
                path, vocab = models.file_path("chinese_clip_vitb16_q8.onnx"), models.vocab_path()
                if path is None or vocab is None:
                    raise RuntimeError("中文 CLIP 模型尚未下载")
                try:
                    self._clip = ClipEngine(path, vocab)
                    self._clip_error = None
                except Exception as e:
                    self._clip_error = str(e)
                    raise
            return self._clip

    def unload(self) -> None:
        with self._lock:
            self._clip = None
            self._labels = None

    # ---- 向量索引 --------------------------------------------------------
    def load_cached(self, groups) -> int:
        sigs = {g.gid: g.primary_file.sig for g in groups if g.files}
        cached = self.lib.cache.get_emb_many(list(sigs.values()), CLIP_KEY)
        self.emb = {gid: cached[s] for gid, s in sigs.items() if s in cached}
        return len(self.emb)

    def index(self, groups, progress: Callable[[int, int], None] | None = None,
              cancel: threading.Event | None = None, batch: int = 16) -> int:
        """为还没有向量的照片计算 CLIP 向量。返回新算的数量。"""
        from PIL import Image

        self.load_cached(groups)
        todo = [g for g in groups if g.files and g.gid not in self.emb]
        if not todo:
            return 0
        eng = self.clip()
        done = 0
        for i in range(0, len(todo), batch):
            if cancel is not None and cancel.is_set():
                break
            chunk = todo[i:i + batch]
            imgs, ok = [], []
            for g in chunk:
                try:
                    if g.thumb:
                        with Image.open(g.thumb) as im:
                            imgs.append(im.convert("RGB"))
                    else:
                        from ..core.imaging import load_preview
                        imgs.append(load_preview(g.primary, g.primary_file.role, 360, self.lib.exiftool_path)[0])
                    ok.append(g)
                except Exception as e:
                    log.warning("AI：读取 %s 失败：%s", g.primary, e)
            if imgs:
                vecs = eng.encode_images(imgs)
                new = {}
                for g, v in zip(ok, vecs):
                    self.emb[g.gid] = v
                    new[g.primary_file.sig] = v
                self.lib.cache.put_emb_many(new, CLIP_KEY)
            done += len(chunk)
            if progress:
                progress(done, len(todo))
        return done

    # ---- 场景 ------------------------------------------------------------
    def classify(self, groups) -> int:
        import numpy as np

        from .scenes import classify, label_matrix
        if not self.emb:
            for g in groups:
                g.feats.pop("scene", None)
            return 0
        if self._labels is None:
            self._labels = label_matrix(self.clip())
        have = [g for g in groups if g.gid in self.emb]
        res = classify(np.stack([self.emb[g.gid] for g in have]), self._labels)
        for g, r in zip(have, res):
            g.feats["scene"] = r
        for g in groups:
            if g.gid not in self.emb:
                g.feats.pop("scene", None)
        return len(have)

    # ---- 搜索 ------------------------------------------------------------
    def _rank(self, q, gids=None, limit: int = 300) -> list[tuple[int, float]]:
        import numpy as np
        items = [(gid, v) for gid, v in self.emb.items() if gids is None or gid in gids]
        if not items:
            return []
        M = np.stack([v for _, v in items])
        s = M @ q
        order = np.argsort(-s)[:limit]
        return [(items[int(i)][0], float(s[int(i)])) for i in order]

    def search_text(self, text: str, gids=None, limit: int = 300) -> list[tuple[int, float]]:
        import numpy as np
        text = (text or "").strip()
        if not text:
            return []
        variants = [text] if len(text) > 12 else [text, f"一张{text}的照片"]
        q = self.clip().encode_texts(variants).mean(axis=0)
        q /= max(1e-8, float(np.linalg.norm(q)))
        return self._rank(q, gids, limit)

    def search_similar(self, gid: int, gids=None, limit: int = 300) -> list[tuple[int, float]]:
        v = self.emb.get(gid)
        if v is None:
            return []
        return [(g, s) for g, s in self._rank(v, gids, limit + 1) if g != gid][:limit]

    # ---- 人物 ------------------------------------------------------------
    def recluster(self, groups) -> list:
        from .people import cluster_people
        s = self.lib.settings
        ann = self.lib.annotations
        self.people, self.loners = cluster_people(groups, ann.all("people"), ann.all("face_fix"),
                                                  float(s.get("face_threshold", 0.42)),
                                                  s.get("people_other_name") or "其他人")
        return self.people

    def cluster(self, cid: str):
        return next((c for c in self.people if c.cid == cid), None)

    def name_cluster(self, cid: str, name: str) -> str:
        """给一组人脸命名。已有同名的人 → 合并进去。返回人物 id。"""
        from .people import merge_into, new_person
        ann = self.lib.annotations
        cl = self.cluster(cid)
        name = name.strip()
        if cl is None or not name:
            raise ValueError("没有这个人物或名字为空")
        persons = ann.all("people")
        same = next((pid for pid, p in persons.items() if p.get("name") == name), None)
        if cl.named and cl.cid in persons:
            if same and same != cl.cid:            # 改成别人的名字 = 合并两个人
                ann.set_many({same: merge_into(persons[same], cl.faces)}, "people")
                ann.clear_many([cl.cid], "people")
                self._retarget_fixes(cl.cid, same)
                return same
            ann.set_many({cl.cid: {**persons[cl.cid], "name": name}}, "people")
            return cl.cid
        if same:
            ann.set_many({same: merge_into(persons[same], cl.faces)}, "people")
            return same
        pid, rec = new_person(name, cl.faces)
        ann.set_many({pid: rec}, "people")
        return pid

    def _retarget_fixes(self, old: str, new: str) -> None:
        ann = self.lib.annotations
        fixes = ann.all("face_fix")
        upd = {k: {**v, "pid": new} for k, v in fixes.items() if v.get("pid") == old}
        if upd:
            ann.set_many(upd, "face_fix")

    def forget_person(self, pid: str) -> None:
        ann = self.lib.annotations
        ann.clear_many([pid], "people")
        fixes = ann.all("face_fix")
        stale = [k for k, v in fixes.items() if v.get("pid") == pid]
        if stale:
            ann.clear_many(stale, "face_fix")

    def remove_faces(self, cid: str, keys: list[str]) -> None:
        """“这几张不是他”：记住更正，以后不再自动归给这个人。"""
        ann = self.lib.annotations
        fixes = ann.all("face_fix")
        upd = {}
        for k in keys:
            f = dict(fixes.get(k) or {})
            if f.get("pid") == cid:
                f.pop("pid", None)
            f["not"] = sorted(set(f.get("not") or []) | {cid})
            upd[k] = f
        if upd:
            ann.set_many(upd, "face_fix")

    def assign_faces(self, keys: list[str], pid: str) -> None:
        ann = self.lib.annotations
        fixes = ann.all("face_fix")
        ann.set_many({k: {**(fixes.get(k) or {}), "pid": pid,
                          "not": [x for x in (fixes.get(k) or {}).get("not", []) if x != pid]} for k in keys},
                     "face_fix")

    def clear_people(self) -> int:
        ann = self.lib.annotations
        n = len(ann.all("people"))
        ann.clear_all("people")
        ann.clear_all("face_fix")
        return n

    # ---- 状态 ------------------------------------------------------------
    def stats(self, groups) -> dict[str, Any]:
        gs = [g for g in groups if g.files]
        return {
            "photos": len(gs),
            "indexed": sum(1 for g in gs if g.gid in self.emb),
            "faces_done": sum(1 for g in gs if "faces" in g.feats),
            "faces": sum(int((g.feats.get("faces") or {}).get("n") or 0) for g in gs),
            "people": len(self.people),
            "named": sum(1 for c in self.people if c.named),
        }
