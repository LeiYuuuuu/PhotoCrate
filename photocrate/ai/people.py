"""人物分组：把所有照片里的人脸按特征相似度聚成一个个“人”。

- 已命名的人（用户起过名字）保存在 annotations.db 的 people 表：{名字, 特征中心}，以后的照片按相似度自动认出；
- 其余人脸用“贪心中心聚类 + 合并”分组：按脸从大到小依次加入最像的组（余弦相似度 ≥ 阈值），
  然后把中心足够像的组合并。只出现一次的脸归入“其他人”，不单独成组；
- 用户的更正（“这张不是他”“这不是人脸”“这是人脸”）存在 face_fix 表，键 = 主图指纹#第几张脸；
- 不是真人的脸（动物、玩偶、手办、动漫、雕像……人脸检测器也会框出来）由 AIService.screen_faces
  用 CLIP 判断后通过 excluded 传进来，不参与分组。
给一个组起的名字和已有的人相同 → 合并成同一个人。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .faces import decode_emb, encode_emb


@dataclass
class FaceRef:
    gid: int
    index: int
    key: str            # 主图指纹#序号
    emb: Any
    size: float         # 脸占预览图的面积比例
    crop: str


@dataclass
class Cluster:
    cid: str                       # 已命名：人物 id；未命名：c1、c2 …
    name: str
    named: bool
    faces: list[FaceRef] = field(default_factory=list)
    centroid: Any = None

    @property
    def gids(self) -> list[int]:
        seen, out = set(), []
        for f in self.faces:
            if f.gid not in seen:
                seen.add(f.gid)
                out.append(f.gid)
        return out

    @property
    def cover(self) -> FaceRef | None:
        return max(self.faces, key=lambda f: f.size) if self.faces else None


def collect_faces(groups) -> list[FaceRef]:
    out = []
    for g in groups:
        if not g.files:
            continue
        fd = g.feats.get("faces") or {}
        sig = g.primary_file.sig
        for i, f in enumerate(fd.get("faces") or []):
            try:
                emb = decode_emb(f["emb"])
            except Exception:
                continue
            b = f.get("box") or [0, 0, 0, 0]
            out.append(FaceRef(g.gid, i, f"{sig}#{i}", emb, float(b[2]) * float(b[3]), f.get("crop") or ""))
    return out


def _norm(v):
    import numpy as np
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def cluster_people(groups, persons: dict[str, dict], fixes: dict[str, dict], threshold: float = 0.42,
                   other_name: str = "其他人", excluded: set[str] | None = None) -> tuple[list[Cluster], list[FaceRef]]:
    """返回 (人物列表（已命名在前，其余按人数排序）, 落单的人脸)。同时写入 g.feats["people"]。

    excluded：不是真人的脸（键 = 主图指纹#序号），整个跳过。
    """
    import numpy as np

    excluded = excluded or set()
    faces = [f for f in collect_faces(groups) if f.key not in excluded]
    for g in groups:
        g.feats.pop("people", None)
    clusters: dict[str, Cluster] = {}
    named = []
    for pid, p in persons.items():
        try:
            c = _norm(decode_emb(p["centroid"]))
        except Exception:
            continue
        clusters[pid] = Cluster(pid, p.get("name") or "未命名", True, [], c)
        named.append(pid)
    rest: list[FaceRef] = []
    if named:
        C = np.stack([clusters[p].centroid for p in named])
    for f in faces:
        fix = fixes.get(f.key) or {}
        if fix.get("pid") in clusters:
            clusters[fix["pid"]].faces.append(f)
            continue
        target = None
        if named:
            s = C @ f.emb
            for j in np.argsort(-s):
                if s[int(j)] < threshold:
                    break
                pid = named[int(j)]
                if pid in (fix.get("not") or []):
                    continue
                target = pid
                break
        if target is not None:
            clusters[target].faces.append(f)
        else:
            rest.append(f)

    # 未命名：贪心中心聚类
    groups_: list[list] = []          # [sum_vec, members]
    for f in sorted(rest, key=lambda f: -f.size):
        best, bs = None, threshold
        for k, (sv, mem) in enumerate(groups_):
            sim = float(_norm(sv) @ f.emb)
            if sim >= bs:
                best, bs = k, sim
        if best is None:
            groups_.append([f.emb.copy(), [f]])
        else:
            groups_[best][0] += f.emb
            groups_[best][1].append(f)
    merged = True
    while merged and len(groups_) > 1:
        merged = False
        cs = np.stack([_norm(sv) for sv, _ in groups_])
        sim = cs @ cs.T
        np.fill_diagonal(sim, -1)
        i, j = np.unravel_index(int(np.argmax(sim)), sim.shape)
        if sim[i, j] >= threshold:
            a, b = (i, j) if len(groups_[i][1]) >= len(groups_[j][1]) else (j, i)
            groups_[a][0] += groups_[b][0]
            groups_[a][1] += groups_[b][1]
            del groups_[b]
            merged = True
    loners: list[FaceRef] = []
    unnamed = []
    for sv, mem in groups_:
        if len({f.gid for f in mem}) < 2:
            loners += mem
        else:
            unnamed.append((sv, mem))
    unnamed.sort(key=lambda t: -len(t[1]))
    for k, (sv, mem) in enumerate(unnamed, 1):
        cid = f"c{k}"
        clusters[cid] = Cluster(cid, f"人物 {k}", False, mem, _norm(sv))
    for cl in clusters.values():
        if cl.named and cl.faces:
            cl.faces.sort(key=lambda f: -f.size)

    # 写入每张照片
    owner: dict[str, Cluster] = {f.key: cl for cl in clusters.values() for f in cl.faces}
    by_gid: dict[int, list] = {}
    for f in faces:
        by_gid.setdefault(f.gid, []).append(f)
    for g in groups:
        fs = by_gid.get(g.gid)
        if not fs:
            continue
        fs.sort(key=lambda f: -f.size)
        ids, names = [], []
        for f in fs:
            cl = owner.get(f.key)
            ids.append(cl.cid if cl else None)
            nm = cl.name if cl and cl.named else None
            if nm and nm not in names:
                names.append(nm)
        g.feats["people"] = {"n": len(fs), "ids": ids, "names": names,
                             "main": next((owner[f.key].name for f in fs if f.key in owner and owner[f.key].named),
                                          None) or (other_name if fs else None)}
    order = [clusters[p] for p in named if clusters[p].faces] + \
            sorted((c for c in clusters.values() if not c.named), key=lambda c: -len(c.faces))
    return order, loners


def new_person(name: str, faces: list[FaceRef]) -> tuple[str, dict]:
    import numpy as np
    c = _norm(np.sum([f.emb for f in faces], axis=0))
    return "p" + uuid.uuid4().hex[:10], {"name": name, "centroid": encode_emb(c), "n": len(faces)}


def merge_into(person: dict, faces: list[FaceRef]) -> dict:
    import numpy as np
    old = decode_emb(person["centroid"]) * max(1, int(person.get("n") or 1))
    c = _norm(old + np.sum([f.emb for f in faces], axis=0))
    return {**person, "centroid": encode_emb(c), "n": int(person.get("n") or 1) + len(faces)}
