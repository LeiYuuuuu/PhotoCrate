"""回收站：删除文件 + 从回收站找回（用于“清理 RAW”等删除操作的撤销）。

删除一律用 send2trash 放进系统回收站。撤销时到回收站里按“原路径 + 删除时间”找到那个文件，改名放回原处：
- Windows：每个磁盘的 X:\\$Recycle.Bin\\<用户 SID>\\ 下，$I 文件记录原路径与删除时间，$R 文件是文件本身
  （格式：Win10+ 为 版本 2：8 字节版本号、8 字节大小、8 字节 FILETIME、4 字节路径长度、UTF-16 路径；
   Vista/7 为 版本 1：路径固定 520 字节）。
- Linux（freedesktop 规范，send2trash 使用的方式）：Trash/info/*.trashinfo 记录 Path 与 DeletionDate，
  文件在 Trash/files/ 下；家目录回收站或各挂载点的 .Trash-<uid>。
- 其他系统：不支持找回（撤销时会提示到回收站手动还原）。
如果回收站已经被清空，文件就无法找回——撤销时会明确列出这些文件。
"""
from __future__ import annotations

import logging
import os
import struct
import sys
import time
from datetime import datetime
from urllib.parse import unquote
from ..i18n import t as _t

log = logging.getLogger(__name__)


def send_to_trash(path: str) -> float:
    """放入回收站，返回删除时间（秒）。回收站不可用时抛异常——绝不改为永久删除。"""
    from send2trash import send2trash
    t = time.time()
    send2trash(path)
    if os.path.lexists(path):
        raise IOError(_t("放入回收站失败"))
    return t


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

_EPOCH_DIFF = 116444736000000000   # 1601-01-01 → 1970-01-01（100 纳秒）


def parse_windows_i_file(data: bytes) -> tuple[str, float, int] | None:
    """解析 $I 文件 → (原路径, 删除时间, 大小)。"""
    if len(data) < 24:
        return None
    ver, size, ft = struct.unpack_from("<qqq", data, 0)
    try:
        if ver == 2 and len(data) >= 28:
            n = struct.unpack_from("<i", data, 24)[0]
            path = data[28:28 + n * 2].decode("utf-16-le").rstrip("\x00")
        elif ver == 1:
            path = data[24:24 + 520].decode("utf-16-le").split("\x00", 1)[0]
        else:
            return None
    except (UnicodeDecodeError, struct.error):
        return None
    return path, (ft - _EPOCH_DIFF) / 1e7, size


def _windows_candidates(path: str):
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    root = drive + "\\$Recycle.Bin"
    try:
        sids = os.listdir(root)
    except OSError:
        return
    want = os.path.normcase(os.path.abspath(path))
    for sid in sids:
        d = os.path.join(root, sid)
        try:
            names = os.listdir(d)
        except OSError:
            continue                  # 其他用户的回收站没有权限，跳过
        for n in names:
            if not n.startswith("$I"):
                continue
            ip = os.path.join(d, n)
            try:
                with open(ip, "rb") as fh:
                    info = parse_windows_i_file(fh.read(2048))
            except OSError:
                continue
            if not info or os.path.normcase(os.path.abspath(info[0])) != want:
                continue
            rp = os.path.join(d, "$R" + n[2:])
            if os.path.lexists(rp):
                yield info[1], rp, ip


# ---------------------------------------------------------------------------
# Linux / freedesktop
# ---------------------------------------------------------------------------

def _trash_dirs(path: str) -> list[str]:
    dirs = []
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    dirs.append(os.path.join(xdg, "Trash"))
    # 文件所在挂载点的 .Trash-<uid> / .Trash/<uid>
    p = os.path.abspath(path)
    while True:
        parent = os.path.dirname(p)
        if parent == p or os.path.ismount(p):
            break
        p = parent
    uid = str(os.getuid()) if hasattr(os, "getuid") else ""
    dirs += [os.path.join(p, f".Trash-{uid}"), os.path.join(p, ".Trash", uid)]
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(os.path.join(d, "info"))]


def _freedesktop_candidates(path: str):
    want = os.path.abspath(path)
    for td in _trash_dirs(path):
        top = os.path.dirname(td) if os.path.basename(td).startswith(".Trash") else None
        info_dir = os.path.join(td, "info")
        for n in os.listdir(info_dir):
            if not n.endswith(".trashinfo"):
                continue
            ip = os.path.join(info_dir, n)
            try:
                with open(ip, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            orig = when = None
            for line in text.splitlines():
                if line.startswith("Path="):
                    orig = unquote(line[5:].strip())
                elif line.startswith("DeletionDate="):
                    try:
                        when = datetime.fromisoformat(line[13:].strip()).timestamp()
                    except ValueError:
                        when = 0.0
            if not orig:
                continue
            if not os.path.isabs(orig) and top:
                orig = os.path.join(top, orig)
            if os.path.abspath(orig) != want:
                continue
            fp = os.path.join(td, "files", n[: -len(".trashinfo")])
            if os.path.lexists(fp):
                yield (when or 0.0), fp, ip


# ---------------------------------------------------------------------------
# 找回
# ---------------------------------------------------------------------------

def find_in_trash(path: str, deleted_after: float | None = None) -> tuple[str, str] | None:
    """返回 (回收站里的文件, 记录文件)。有多条时取删除时间最接近、且不早于 deleted_after 的那条。"""
    if sys.platform.startswith("win"):
        cands = list(_windows_candidates(path))
    elif sys.platform == "darwin":
        return None
    else:
        cands = list(_freedesktop_candidates(path))
    if deleted_after is not None:
        # 时间记录精度：Windows 为 100ns，freedesktop 为秒 → 留 2 秒余量
        cands = [c for c in cands if c[0] >= deleted_after - 2]
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])
    _, fp, ip = cands[0]
    return fp, ip


def restore_from_trash(path: str, deleted_after: float | None = None) -> None:
    """把回收站里的文件放回原处。找不到或原处已被占用时抛异常。"""
    hit = find_in_trash(path, deleted_after)
    if hit is None:
        raise FileNotFoundError(_t("回收站里找不到（可能已清空回收站或手动还原过）"))
    fp, ip = hit
    if os.path.lexists(path):
        raise FileExistsError(_t("原位置已有同名文件，未覆盖"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.rename(fp, path)
    try:
        os.remove(ip)
    except OSError:
        pass
