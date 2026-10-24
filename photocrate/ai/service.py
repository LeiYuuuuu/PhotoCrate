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
from ..i18n import t as _t

log = logging.getLogger(__name__)

CLIP_KEY = "cnclip-vitb16-q8"
SEARCH_MARGIN = 0.03     # 照片像某个无关主体比像搜索词还高出这么多，就不算搜索结果
SIMILAR_MIN = 0.30       # 找相似：综合相似度低于这个值不显示
FACE_KIND_KEY = "facekind-v1"
HUMAN_MIN = 0.5          # 人脸小图被 CLIP 判为“真人的脸”的概率低于这个值 → 不是人脸（动物、玩偶、动漫…）
HUMAN_PROMPTS = ["一个人的脸", "一张真人的面部照片", "一个男人的脸", "一个女人的脸", "一个孩子的脸"]
OTHER_PROMPTS = ["一只动物的脸", "一只鹿", "一只猫", "一只狗", "一个动漫人物", "一个卡通人物", "一个玩偶",
                 "一个手办", "一座雕像", "一个面具"]


class AIService:
    def __init__(self, library):
        self.lib = library
        self._clip = None
        self._clip_error: str | None = None
        self._labels = None
        self._lock = threading.RLock()
        self.emb: dict[int, Any] = {}           # gid → 向量
        self.vis: dict[int, Any] = {}           # gid → 画面指纹（颜色布局，ai/visual.py）
        self.people: list = []                  # ai.people.Cluster
        self.loners: list = []
        self.nonfaces: list = []                # 不是真人的脸（FaceRef），不参与分组
        self.face_human: dict[str, float] = {}  # 人脸键 → 是真人的概率
        self._face_prompts = None

    # ---- 模型 ------------------------------------------------------------
    def clip_ready(self) -> bool:
        return models.ready("clip") and models.onnxruntime_ok()

    def clip(self):
        with self._lock:
            if self._clip is None:
                from .clip import ClipEngine
                path, vocab = models.file_path("chinese_clip_vitb16_q8.onnx"), models.vocab_path()
                if path is None or vocab is None:
                    raise RuntimeError(_t("中文 CLIP 模型尚未下载"))
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
            self._face_prompts = None

    # ---- 向量索引 --------------------------------------------------------
    def load_cached(self, groups) -> int:
        from .visual import VIS_KEY
        sigs = {g.gid: g.primary_file.sig for g in groups if g.files}
        cached = self.lib.cache.get_emb_many(list(sigs.values()), CLIP_KEY)
        self.emb = {gid: cached[s] for gid, s in sigs.items() if s in cached}
        vis = self.lib.cache.get_emb_many(list(sigs.values()), VIS_KEY)
        self.vis = {gid: vis[s] for gid, s in sigs.items() if s in vis}
        return len(self.emb)

    def _open_thumb(self, g):
        from PIL import Image
        if g.thumb:
            with Image.open(g.thumb) as im:
                return im.convert("RGB")
        from ..core.imaging import load_preview
        return load_preview(g.primary, g.primary_file.role, 360, self.lib.exiftool_path)[0]

    def ensure_visual(self, groups, cancel: threading.Event | None = None) -> int:
        """补算画面指纹（只读缩略图，每张约 1 毫秒）。返回新算的数量。"""
        from .visual import VIS_KEY, descriptor
        todo = [g for g in groups if g.files and g.gid not in self.vis]
        new = {}
        for g in todo:
            if cancel is not None and cancel.is_set():
                break
            try:
                v = descriptor(self._open_thumb(g))
            except Exception as e:
                log.debug("画面指纹：读取 %s 失败：%s", g.primary, e)
                continue
            self.vis[g.gid] = v
            new[g.primary_file.sig] = v
        if new:
            self.lib.cache.put_emb_many(new, VIS_KEY)
        return len(new)

    def index(self, groups, progress: Callable[[int, int], None] | None = None,
              cancel: threading.Event | None = None, batch: int = 16) -> int:
        """为还没有向量的照片计算 CLIP 向量（顺带算画面指纹）。返回新算的数量。"""
        from .visual import VIS_KEY, descriptor

        self.load_cached(groups)
        todo = [g for g in groups if g.files and g.gid not in self.emb]
        self.ensure_visual([g for g in groups if g.gid in self.emb], cancel)    # 以前分析过的照片补上画面指纹
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
                    imgs.append(self._open_thumb(g))
                    ok.append(g)
                except Exception as e:
                    log.warning("AI：读取 %s 失败：%s", g.primary, e)
            if imgs:
                vecs = eng.encode_images(imgs)
                new, newvis = {}, {}
                for g, v, im in zip(ok, vecs, imgs):
                    self.emb[g.gid] = v
                    new[g.primary_file.sig] = v
                    try:
                        self.vis[g.gid] = newvis[g.primary_file.sig] = descriptor(im)
                    except Exception:
                        pass
                self.lib.cache.put_emb_many(new, CLIP_KEY)
                self.lib.cache.put_emb_many(newvis, VIS_KEY)
            done += len(chunk)
            if progress:
                progress(done, len(todo))
        return done

    # ---- 场景 ------------------------------------------------------------
    def classify(self, groups) -> int:
        import numpy as np

        from .scenes import classify
        if not self.emb:
            for g in groups:
                g.feats.pop("scene", None)
            return 0
        have = [g for g in groups if g.gid in self.emb]
        res = classify(np.stack([self.emb[g.gid] for g in have]), self._scene_labels())
        for g, r in zip(have, res):
            g.feats["scene"] = r
        for g in groups:
            if g.gid not in self.emb:
                g.feats.pop("scene", None)
        return len(have)

    # ---- 搜索 ------------------------------------------------------------
    def _candidates(self, gids=None):
        import numpy as np
        items = [(gid, v) for gid, v in self.emb.items() if gids is None or gid in gids]
        if not items:
            return [], None
        return [gid for gid, _ in items], np.stack([v for _, v in items])

    def _rank(self, q, gids=None, limit: int = 300) -> list[tuple[int, float]]:
        import numpy as np
        ids, M = self._candidates(gids)
        if not ids:
            return []
        s = M @ q
        order = np.argsort(-s)[:limit]
        return [(ids[int(i)], float(s[int(i)])) for i in order]

    def _scene_labels(self):
        if self._labels is None:
            from .scenes import label_matrix
            self._labels = label_matrix(self.clip())
        return self._labels

    def search_text(self, text: str, gids=None, limit: int = 300) -> list[tuple[int, float]]:
        """文字搜图。返回 [(gid, 分数)]，分数越高越符合，只返回“像样”的结果（可能少于 limit）。

        CLIP 的文字-图片分数挤在很窄的区间里（相关 0.40 左右、无关 0.37 左右），只按分数排序时，
        一张特写人像也可能混进“街道”的前几十名。所以再做一次“对比”：拿照片和几类互相排斥的
        主体（人像 / 自拍 / 合影、美食、饮品、文档截图、室内、猫狗）比——这些主体里和搜索词无关的那几类，
        如果照片明显更像其中之一（比像搜索词还高出 SEARCH_MARGIN），就说明它拍的是别的东西，去掉；
        略高一点的降低排名。夜景、雪景、日落这类“时间 / 天气”场景能和任何主体同时出现，不参与比较。
        """
        import numpy as np
        from .scenes import SCENES, SUBJECT_SCENES
        text = (text or "").strip()
        if not text:
            return []
        ids, M = self._candidates(gids)
        if not ids:
            return []
        variants = [text] if len(text) > 12 else [text, f"一张{text}的照片"]    # 给中文 CLIP 的提示词，不翻译
        q = self.clip().encode_texts(variants).mean(axis=0)
        q /= max(1e-8, float(np.linalg.norm(q)))
        s = M @ q
        final = s.copy()
        keep = np.ones(len(ids), dtype=bool)
        L = self._scene_labels()
        tc = L @ q
        rivals = [k for k, (name, _, _) in enumerate(SCENES) if name in SUBJECT_SCENES and tc[k] < np.median(tc)]
        if rivals:
            margin = (M @ L[rivals].T).max(axis=1) - s          # 比像搜索词还像别的主体多少
            final = s - 0.5 * np.clip(margin, 0.0, None)
            keep &= margin <= SEARCH_MARGIN
        if len(ids) >= 20:                                     # 连全库一半照片都不如的，不算相关
            keep &= s >= float(np.median(s))
        order = [int(i) for i in np.argsort(-final) if keep[int(i)]][:limit]
        return [(ids[i], float(final[i])) for i in order]

    def search_similar(self, gid: int, gids=None, limit: int = 300) -> list[tuple[int, float]]:
        """以图搜图。返回 [(gid, 相似度 0–1)]，按相似度从高到低，只包含明显相似的照片。

        相似度 = 内容（CLIP，55%）+ 画面（颜色布局与颜色分布，30%）+ 结构（pHash，15%）。
        每一项先减去“随便两张照片”的水平（全库中位数）再缩放到 0–1，所以分数表示“比一般照片像多少”，
        不同照片之间可以比较。低于 SIMILAR_MIN 的不显示。
        """
        import numpy as np
        from .visual import hash_similarity, similarity
        v = self.emb.get(gid)
        if v is None:
            return []
        ids, M = self._candidates(gids)
        if gid not in ids:
            ids, M = ids + [gid], (np.vstack([M, v[None, :]]) if ids else v[None, :])
        groups = [self.lib.by_gid(i) for i in ids]

        def rel(x):
            """减去“随便两张”的水平（中位数），缩放到 0–1。"""
            base = float(np.median(x)) if len(x) >= 5 else 0.5
            return np.clip((x - base) / max(1e-6, 1.0 - base), 0.0, 1.0)

        parts = [(0.55, rel(M @ v), np.ones(len(ids), dtype=bool))]          # 内容
        missing = [g for g in groups if g is not None and g.gid not in self.vis]
        if missing:
            self.ensure_visual(missing)
        if gid in self.vis:                                                   # 颜色布局 / 颜色分布
            have = np.array([i in self.vis for i in ids])
            vs = np.zeros(len(ids), dtype=np.float32)
            vs[have] = rel(similarity(self.vis[gid], np.stack([self.vis[i] for i in ids if i in self.vis])))
            parts.append((0.30, vs, have))
        h0 = groups[ids.index(gid)].feat("phash", "phash") if groups[ids.index(gid)] is not None else None
        if h0:                                                                # 结构（pHash）
            hs = [hash_similarity(h0, g.feat("phash", "phash")) if g is not None else None for g in groups]
            have = np.array([x is not None for x in hs])
            hv = np.array([(x - 0.5) / 0.5 if x is not None else 0.0 for x in hs], dtype=np.float32)
            parts.append((0.15, np.clip(hv, 0.0, 1.0), have))
        score = sum(w * s * h for w, s, h in parts)
        wsum = sum(w * h.astype(np.float32) for w, _, h in parts)              # 缺某一项时按剩下的几项算
        score = score / np.maximum(wsum, 1e-6)
        order = [int(i) for i in np.argsort(-score) if ids[int(i)] != gid and score[int(i)] >= SIMILAR_MIN]
        return [(ids[i], float(score[i])) for i in order[:limit]]

    # ---- 人物 ------------------------------------------------------------
    def screen_faces(self, groups) -> int:
        """判断每张检测到的脸是不是真人（人脸检测器会把鹿、猫、玩偶、手办、动漫人物、雕像也框出来）。

        用 CLIP 比较人脸小图和两组描述（真人的脸 / 动物、动漫、玩偶、雕像……），得到“是真人”的概率；
        结果按人脸键缓存在 cache.db 的 emb 表。没有 CLIP 模型时不判断（全部当作真人）。返回新判断的数量。
        """
        import base64
        import io

        import numpy as np
        from PIL import Image

        from .people import collect_faces
        faces = collect_faces(groups)
        if not faces:
            return 0
        keys = [f.key for f in faces]
        cached = self.lib.cache.get_emb_many(keys, FACE_KIND_KEY)
        self.face_human.update({k: float(v[0]) for k, v in cached.items()})
        todo = [f for f in faces if f.key not in self.face_human and f.crop]
        if not todo or not self.clip_ready():
            return 0
        eng = self.clip()
        if self._face_prompts is None:
            self._face_prompts = eng.encode_texts(HUMAN_PROMPTS + OTHER_PROMPTS)
        T = self._face_prompts
        sims = T @ T.T
        if float(sims.max() - sims[~np.eye(len(T), dtype=bool)].min()) < 0.02:     # 描述分不开（模型异常）：不判断
            return 0
        new = {}
        for i in range(0, len(todo), 32):
            chunk, imgs = [], []
            for f in todo[i:i + 32]:
                try:
                    imgs.append(Image.open(io.BytesIO(base64.b64decode(f.crop))).convert("RGB"))
                    chunk.append(f)
                except Exception:
                    continue
            if not imgs:
                continue
            logits = 100.0 * (eng.encode_images(imgs) @ T.T)
            logits -= logits.max(axis=1, keepdims=True)
            p = np.exp(logits)
            p /= p.sum(axis=1, keepdims=True)
            for f, ph in zip(chunk, p[:, :len(HUMAN_PROMPTS)].sum(axis=1)):
                self.face_human[f.key] = float(ph)
                new[f.key] = np.array([ph], dtype=np.float32)
        if new:
            self.lib.cache.put_emb_many(new, FACE_KIND_KEY)
        return len(new)

    def recluster(self, groups) -> list:
        from .people import cluster_people, collect_faces
        s = self.lib.settings
        ann = self.lib.annotations
        fixes = ann.all("face_fix")
        try:
            self.screen_faces(groups)
        except Exception as e:
            log.warning("判断是否真人的脸失败：%s", e)
        excluded = set()
        for f in collect_faces(groups):
            fix = fixes.get(f.key) or {}
            if fix.get("notface") or (not fix.get("human") and self.face_human.get(f.key, 1.0) < HUMAN_MIN):
                excluded.add(f.key)
        self.nonfaces = [f for f in collect_faces(groups) if f.key in excluded]
        self.people, self.loners = cluster_people(groups, ann.all("people"), fixes,
                                                  float(s.get("face_threshold", 0.42)),
                                                  _t(s.get("people_other_name") or "其他人"), excluded)
        return self.people

    def mark_faces(self, keys: list[str], human: bool) -> None:
        """用户更正：这几张“不是人脸”（human=False）或“是人脸”（human=True，覆盖 AI 的判断）。"""
        ann = self.lib.annotations
        fixes = ann.all("face_fix")
        upd = {}
        for k in keys:
            f = dict(fixes.get(k) or {})
            f.pop("notface", None)
            f.pop("human", None)
            if human:
                f["human"] = True
            else:
                f["notface"] = True
                f.pop("pid", None)
            upd[k] = f
        if upd:
            ann.set_many(upd, "face_fix")

    def cluster(self, cid: str):
        return next((c for c in self.people if c.cid == cid), None)

    def name_cluster(self, cid: str, name: str) -> str:
        """给一组人脸命名。已有同名的人 → 合并进去。返回人物 id。"""
        from .people import merge_into, new_person
        ann = self.lib.annotations
        cl = self.cluster(cid)
        name = name.strip()
        if cl is None or not name:
            raise ValueError(_t("没有这个人物或名字为空"))
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
            "nonfaces": len(self.nonfaces),
            "people": len(self.people),
            "named": sum(1 for c in self.people if c.named),
        }
