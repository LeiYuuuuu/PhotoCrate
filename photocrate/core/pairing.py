"""跨文件夹配对：RAW 与 JPG 被分别放在不同文件夹（如 ARW/ 与 JPG/）时，重新认成同一张照片。

同一文件夹内的配对在 scanner 里按文件名完成；这里处理分处两个文件夹的情况，条件更严格：
1. 主文件名相同（不区分大小写），一边只有 RAW、另一边只有 JPG/HEIC；
2. 两边都有 EXIF 拍摄时间，且相差不超过 2 秒；
3. 两边都有相机型号时，型号必须一致；
4. 一一对应：同名候选不止一个（例如相机文件编号循环）且无法区分时，不配对。
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime

from .dates import parse_exif_datetime
from .metadata import merge_meta
from .models import PhotoGroup

MAX_DIFF_S = 2.0


def _shot_time(g: PhotoGroup) -> datetime | None:
    dt, _ = parse_exif_datetime(g.meta.get("dt_original"))
    return dt


def _model(g: PhotoGroup) -> str | None:
    m = g.meta.get("model")
    return " ".join(str(m).lower().split()) if m else None


def same_shot(a: PhotoGroup, b: PhotoGroup) -> bool:
    ta, tb = _shot_time(a), _shot_time(b)
    if ta is None or tb is None or abs((ta - tb).total_seconds()) > MAX_DIFF_S:
        return False
    ma, mb = _model(a), _model(b)
    if ma and mb and ma != mb:
        return False
    return os.path.normcase(a.folder) != os.path.normcase(b.folder)


def _single(g: PhotoGroup, roles: set[str]) -> bool:
    imgs = g.image_files
    return len(imgs) == 1 and imgs[0].role in roles


def pair_across_folders(groups: list[PhotoGroup]) -> int:
    """就地合并配对成功的组（RAW 组的文件并入 JPG 组，RAW 组清空）。返回配对数量。"""
    by_stem: dict[str, dict[str, list[PhotoGroup]]] = defaultdict(lambda: {"raw": [], "img": []})
    for g in groups:
        if not g.files:
            continue
        if _single(g, {"raw"}):
            by_stem[os.path.splitext(g.image_files[0].name)[0].lower()]["raw"].append(g)
        elif _single(g, {"jpeg", "heif"}):
            by_stem[g.stem.lower()]["img"].append(g)
    n = 0
    for d in by_stem.values():
        raws, imgs = d["raw"], d["img"]
        if not raws or not imgs:
            continue
        used: set[int] = set()
        for rg in raws:
            cands = [ig for ig in imgs if id(ig) not in used and same_shot(rg, ig)]
            if len(cands) != 1:
                continue
            ig = cands[0]
            if sum(1 for r in raws if same_shot(r, ig)) != 1:
                continue
            used.add(id(ig))
            ig.files = ig.files + rg.files
            ig.meta = merge_meta([ig.meta, rg.meta])
            rg.files = []
            n += 1
    return n


def renumber(groups: list[PhotoGroup]) -> list[PhotoGroup]:
    """去掉被合并掉的空组，重新编号（gid = 下标）。"""
    out = [g for g in groups if g.files]
    for i, g in enumerate(out):
        g.gid = i
    return out
