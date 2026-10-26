"""重复照片：找出内容完全相同的文件（比如同一批照片复制了好几份），每组保留一份，其余放进回收站（可撤销）。

判断方法（只比较照片 / 视频本身，不比较 .xmp 等附属文件）：
1. 先按文件大小分桶——大小不同一定不重复，绝大多数文件这一步就排除了，不用读内容；
2. 大小相同的再算内容哈希（BLAKE2b；先比头尾各 1 MB，相同再算全文件），完全一致才算重复。
以“照片组”为单位决定去留：如果一组照片的每个文件，在另一组里都有内容相同的文件，这一组就是多余的副本
（例如别处单独复制出来的 JPG，与原处的 RAW+JPG 中的 JPG 相同，删掉那份单独的 JPG）。
副本带有原件没有的修图记录（.xmp 等）时不删，并提示。
"""
from __future__ import annotations

import hashlib
import os
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from ..core.formats import MEDIA_ROLES
from .cleanup import TRASH_DST
from .planner import Plan, PlanItem, PlanOp
from ..i18n import t as _t

KEEP_RULES = {
    "shortest": _t("保留排在前面的文件夹里的（按「资源接入」的文件夹顺序，主照片库优先）"),
    "oldest": _t("保留最早的文件（修改时间最早）"),
    "in_target": _t("优先保留“目标文件夹”里的"),
}

CHUNK = 1 << 20


def _hash_quick(path: str, size: int) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        h.update(f.read(CHUNK))
        if size > 2 * CHUNK:
            f.seek(size - CHUNK)
            h.update(f.read(CHUNK))
    return h.hexdigest()


def _hash_full(path: str) -> str:
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        while True:
            b = f.read(4 * CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _identity(path: str) -> tuple[int, int] | None:
    """磁盘上的“哪一个文件”（设备号 + 文件号）；取不到时返回 None。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


@dataclass
class DupSet:
    keep: int                      # 保留的组 gid
    remove: list[int]              # 多余的组 gid
    bytes: int = 0                 # 删除后释放的空间
    files: int = 0


@dataclass
class DupResult:
    sets: list[DupSet] = field(default_factory=list)
    skipped_sidecar: list[int] = field(default_factory=list)   # 副本有原件没有的修图记录，未删除
    hashed_bytes: int = 0
    cancelled: bool = False

    @property
    def total_bytes(self) -> int:
        return sum(s.bytes for s in self.sets)

    @property
    def total_groups(self) -> int:
        return sum(len(s.remove) for s in self.sets)


def find_duplicates(groups, keep_rule: str = "shortest", target_dir: str = "",
                    progress: Callable[[int, int, str], None] | None = None,
                    cancel: threading.Event | None = None) -> DupResult:
    res = DupResult()
    gs = {g.gid: g for g in groups if g.files}
    media = [(g.gid, f) for g in gs.values() for f in g.files if f.role in MEDIA_ROLES and f.size > 0]
    by_size: dict[int, list] = defaultdict(list)
    for gid, f in media:
        by_size[f.size].append((gid, f))
    cands = [lst for lst in by_size.values() if len({os.path.normcase(f.path) for _, f in lst}) > 1]
    todo = sum(len(lst) for lst in cands)
    done = 0
    fhash: dict[str, str] = {}             # 文件路径 → 内容哈希
    for lst in cands:
        # 同一个文件经由两条路径出现（目录联接、符号链接、硬链接、同一文件夹用不同写法添加了两次）：
        # 删掉其中“一份”其实就是删掉唯一的那份 → 这样的文件不参与比较
        same_file: dict[tuple[int, int], int] = defaultdict(int)
        ident: dict[str, tuple[int, int] | None] = {}
        for _, f in lst:
            ident[f.path] = _identity(f.path)
            if ident[f.path] is not None:
                same_file[ident[f.path]] += 1
        lst = [(gid, f) for gid, f in lst if ident[f.path] is None or same_file[ident[f.path]] == 1]
        quick: dict[str, list] = defaultdict(list)
        for gid, f in lst:
            if cancel is not None and cancel.is_set():
                res.cancelled = True
                return res
            try:
                quick[_hash_quick(f.path, f.size)].append((gid, f))
                res.hashed_bytes += min(f.size, 2 * CHUNK)
            except OSError:
                pass
            done += 1
            if progress and done % 10 == 0:
                progress(done, todo, f.name)
        for same in quick.values():
            if len(same) < 2:
                continue
            for gid, f in same:
                try:
                    fhash[f.path] = _hash_full(f.path)
                    res.hashed_bytes += f.size
                except OSError:
                    pass
    if progress:
        progress(todo, todo, _t("比较完成"))
    # 每组的“内容集合”
    content: dict[int, set[str]] = {}
    for gid, g in gs.items():
        # 没算哈希的文件（大小独一无二）不可能与别的重复，用一个只属于它的标记代替
        hs = {fhash.get(f.path) or f"unique:{f.path}" for f in g.files if f.role in MEDIA_ROLES}
        if any(not h.startswith("unique:") for h in hs):
            content[gid] = hs
    # 按共享的哈希把组连成簇
    owners: dict[str, list[int]] = defaultdict(list)
    for gid, hs in content.items():
        for h in hs:
            owners[h].append(gid)
    parent = {gid: gid for gid in content}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for lst in owners.values():
        for other in lst[1:]:
            a, b = find(lst[0]), find(other)
            if a != b:
                parent[b] = a
    clusters: dict[int, list[int]] = defaultdict(list)
    for gid in content:
        clusters[find(gid)].append(gid)
    tgt = os.path.normcase(os.path.abspath(target_dir)) if target_dir else ""

    def rank(gid):
        g = gs[gid]
        depth = g.primary.replace("\\", "/").count("/")
        in_t = 0 if tgt and os.path.normcase(os.path.abspath(g.primary)).startswith(tgt + os.sep) else 1
        oldest = min(f.mtime_ns for f in g.files if f.role in MEDIA_ROLES)
        base = (-len(content[gid]),)             # 文件更全的（RAW+JPG）优先保留
        if keep_rule == "oldest":
            return base + (oldest, depth, gid)
        if keep_rule == "in_target":
            return base + (in_t, depth, oldest, gid)
        # 按扫描顺序：「资源接入」里排在前面的文件夹（主照片库）优先保留；同一文件夹树里按遍历顺序
        return base + (gid,)

    for members in clusters.values():
        if len(members) < 2:
            continue
        members.sort(key=rank)
        keep = members[0]
        kept_hashes = content[keep]
        kg = gs[keep]
        kept_side = {f.name.lower()[len(kg.stem):] for f in kg.files if f.role not in MEDIA_ROLES}
        ds = DupSet(keep, [])
        for gid in members[1:]:
            if not content[gid] <= kept_hashes:
                continue
            g = gs[gid]
            side = [f for f in g.files if f.role not in MEDIA_ROLES]
            # 副本自己的修图记录（原件没有同类附属文件）→ 不删，以免丢掉修图
            if any(f.name.lower()[len(g.stem):] not in kept_side for f in side if f.role == "sidecar"):
                res.skipped_sidecar.append(gid)
                continue
            ds.remove.append(gid)
            ds.bytes += sum(f.size for f in g.files)
            ds.files += len(g.files)
        if ds.remove:
            res.sets.append(ds)
    res.sets.sort(key=lambda s: -s.bytes)
    return res


def build_dedupe_plan(groups, result: DupResult) -> Plan:
    gs = {g.gid: g for g in groups}
    plan = Plan(spec_text=_t("删除重复照片（每组保留一份）"), target_root="", operation="delete")
    dirs = set()
    for s in result.sets:
        for gid in s.remove:
            g = gs[gid]
            ops = [PlanOp(f.path, TRASH_DST, f.size, f.mtime_ns, f.role) for f in g.files]
            plan.items.append(PlanItem(gid, ops, os.path.dirname(g.primary)))
            plan.total_bytes += sum(f.size for f in g.files)
            dirs.add(os.path.dirname(g.primary))
    if dirs:
        try:
            plan.target_root = os.path.commonpath(sorted(dirs))
        except ValueError:
            plan.target_root = sorted(dirs)[0]
    if result.skipped_sidecar:
        plan.warnings.append(_t('{0} 组副本带有原件没有的修图记录（.xmp 等），没有删除。', len(result.skipped_sidecar)))
    if not plan.items:
        plan.warnings.append(_t("没有找到重复的照片。"))
    return plan
