"""扫描文件夹并建立"文件组"。

分组规则（同一目录内）：
1. 同主文件名的 RAW 与 JPG/HEIC 等视为同一张照片（如 DSC01234.ARW + DSC01234.JPG）。
2. 附属文件跟随对应照片：
   - .xmp / .pp3 / .dop：IMG_1.xmp 或 IMG_1.CR3.xmp
   - .aae：IMG_1234.AAE 或 IMG_O1234.AAE（iOS 编辑记录）
   - .json：Google 相册导出的 IMG_1.JPG.json / IMG_1.JPG.supplemental-metadata.json
   - .mov/.mp4：同名 Live Photo 视频（可在设置中关闭）
3. 找不到主人的附属文件不处理（留在原处）。
"""
from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict
from typing import Callable, Iterable

from .formats import file_kind
from .models import MemberFile, PhotoGroup

log = logging.getLogger(__name__)

SKIP_DIR_NAMES = {"$recycle.bin", "system volume information", ".photocrate", "@eadir", ".thumbnails", "__macosx"}
TEMP_SUFFIXES = (".pctmp",)

ProgressFn = Callable[[int, str], None]


def _iter_dir(root: str, recursive: bool, cancel: threading.Event | None) -> Iterable[tuple[str, list[os.DirEntry]]]:
    stack = [root]
    while stack:
        if cancel is not None and cancel.is_set():
            return
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError as e:
            log.warning("无法读取目录 %s: %s", d, e)
            continue
        files = []
        subdirs = []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if recursive and e.name.lower() not in SKIP_DIR_NAMES and not e.name.startswith("."):
                        subdirs.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    files.append(e)
            except OSError:
                continue
        yield d, files
        stack.extend(sorted(subdirs, reverse=True))


def _member(entry: os.DirEntry, role: str) -> MemberFile | None:
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return None
    return MemberFile(path=entry.path, role=role, size=st.st_size, mtime_ns=st.st_mtime_ns)


def group_directory(files: list[os.DirEntry], pair_live: bool = True) -> list[tuple[list[MemberFile], str]]:
    """把一个目录里的文件分成若干文件组，返回 [(成员列表, 主图路径)]。"""
    images_by_stem: dict[str, list[MemberFile]] = defaultdict(list)
    sidecars: list[os.DirEntry] = []
    lives: list[os.DirEntry] = []

    for e in files:
        name = e.name
        if name.lower().endswith(TEMP_SUFFIXES) or name.startswith("~$"):
            continue
        kind = file_kind(name)
        if kind is None:
            continue
        if kind == "sidecar":
            sidecars.append(e)
        elif kind == "live":
            lives.append(e)
        else:
            m = _member(e, kind)
            if m:
                stem = os.path.splitext(name)[0].lower()
                images_by_stem[stem].append(m)

    # 组装：含 RAW 的同名文件合为一组；其余每个图片单独一组
    groups: list[list[MemberFile]] = []
    by_stem_group: dict[str, list[MemberFile]] = {}
    by_fullname: dict[str, list[MemberFile]] = {}
    for stem, members in images_by_stem.items():
        if len(members) > 1 and any(m.role == "raw" for m in members):
            g = sorted(members, key=lambda m: m.path.lower())
            groups.append(g)
            by_stem_group[stem] = g
            for m in g:
                by_fullname[m.name.lower()] = g
        else:
            for m in members:
                g = [m]
                groups.append(g)
                by_stem_group.setdefault(stem, g)
                by_fullname[m.name.lower()] = g

    def attach(entry: os.DirEntry, g: list[MemberFile], role: str) -> None:
        m = _member(entry, role)
        if m:
            g.append(m)

    for e in sidecars:
        lname = e.name.lower()
        base, ext = os.path.splitext(lname)
        target = None
        if ext == ".json":
            # IMG_1.JPG.json / IMG_1.JPG.supplemental-metadata.json / IMG_1.JPG.suppl.json
            for full, g in by_fullname.items():
                if lname.startswith(full + "."):
                    target = g
                    break
            if target is None:
                target = by_stem_group.get(base)
        else:
            # IMG_1.CR3.xmp → 先按完整文件名，再按主文件名
            target = by_fullname.get(base) or by_stem_group.get(base)
            if target is None and ext == ".aae":
                # IMG_O1234.AAE → IMG_1234
                for prefix in ("img_o", "img_e"):
                    if base.startswith(prefix):
                        target = by_stem_group.get("img_" + base[len(prefix):])
                        if target:
                            break
        if target is not None:
            attach(e, target, "sidecar")

    if pair_live:
        for e in lives:
            base = os.path.splitext(e.name.lower())[0]
            g = by_stem_group.get(base)
            if g is not None and any(m.role in ("heif", "jpeg") for m in g):
                attach(e, g, "live")

    out = []
    for g in groups:
        primary = _pick_primary(g)
        out.append((g, primary))
    return out


def _pick_primary(members: list[MemberFile]) -> str:
    """显示与像素分析用哪个文件：优先 JPG（快），其次 HEIC/PNG/其他，最后 RAW。"""
    order = {"jpeg": 0, "heif": 1, "png": 2, "image": 3, "raw": 4}
    imgs = [m for m in members if m.role in order]
    imgs.sort(key=lambda m: (order[m.role], m.path.lower()))
    return imgs[0].path


def scan(
    roots: list[str],
    recursive: bool = True,
    pair_live: bool = True,
    cancel: threading.Event | None = None,
    progress: ProgressFn | None = None,
) -> list[PhotoGroup]:
    groups: list[PhotoGroup] = []
    seen_paths: set[str] = set()
    count = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for d, files in _iter_dir(root, recursive, cancel):
            for members, primary in group_directory(files, pair_live):
                key = os.path.normcase(os.path.abspath(primary))
                if key in seen_paths:   # 多个源文件夹相互包含时去重
                    continue
                seen_paths.add(key)
                groups.append(PhotoGroup(gid=len(groups), files=members, primary=primary))
            count += len(files)
            if progress:
                progress(len(groups), d)
            if cancel is not None and cancel.is_set():
                return groups
    return groups
