"""自动同步：监视照片文件夹，里面新增 / 删除 / 改动了照片时通知照片库。

- 快照 = 文件夹（含子文件夹）里所有照片 / 视频 / 附属文件的 {路径: 大小 + 修改时间}，与扫描同一套规则
  （跳过回收站、隐藏文件夹、.pctmp 临时文件）。照片库按快照增量同步（Library.sync）。
- Windows：每个根文件夹一个 ReadDirectoryChangesW（含子文件夹），一有变化就醒来比较快照，平时每分钟兜底比较一次；
  其他系统、或者文件夹不支持变化通知（部分网络盘）时定时轮询。
- 文件“稳定”才算数：相隔 1 秒的两次快照里大小和修改时间都没变，而且没有别的程序还开着它在写——
  正在复制的大文件不会被读成半张图；删除也要连续两次都不在。一大批照片拷进来时，拷完的先同步，其余的下一轮再来。
- 根文件夹本身不见了（拔掉移动硬盘、网络盘断开）不算删除：保留原来的记录，等它回来。

纯 Python，不依赖 Qt；回调在监视线程里调用。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable

from .formats import file_kind
from .models import PhotoGroup
from .scanner import TEMP_SUFFIXES, _iter_dir, group_directory

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Entry:
    path: str
    size: int
    mtime_ns: int
    born_ns: int = field(default=0, compare=False)     # 创建时间（只用来判断“刚拷进来”）


Snapshot = dict[str, Entry]          # 键：os.path.normcase(路径)


@dataclass
class Change:
    files: Snapshot                  # 稳定下来的全部文件（照片库按它同步）
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    unsettled: frozenset[str] = frozenset()   # 还在复制 / 变化中的文件（normcase 路径）：这次先不动，下一轮再同步
    initial: bool = False            # 开始监视时的第一份快照

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.modified)


def _norm_root(root: str) -> str:
    return os.path.abspath(root)


def snapshot(roots: list[str], recursive: bool = True, cancel: threading.Event | None = None,
             keep: Snapshot | None = None) -> Snapshot:
    """keep：上一份快照。不存在的根文件夹沿用其中的记录（离线不算删除）。"""
    out: Snapshot = {}
    for root in roots:
        root = _norm_root(root)
        if not os.path.isdir(root):
            if keep:
                pre = os.path.normcase(root).rstrip("\\/") + os.sep
                out.update((k, v) for k, v in keep.items() if k.startswith(pre))
            continue
        for _d, files in _iter_dir(root, recursive, cancel):
            for e in files:
                name = e.name
                if name.lower().endswith(TEMP_SUFFIXES) or name.startswith("~$") or file_kind(name) is None:
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                born = getattr(st, "st_birthtime_ns", None) or (st.st_ctime_ns if os.name == "nt" else 0)
                out[os.path.normcase(e.path)] = Entry(e.path, st.st_size, st.st_mtime_ns, born)
    return out


def diff(old: Snapshot, new: Snapshot) -> set[str]:
    return {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}


class _FileEntry:
    """给 scanner.group_directory 用的 os.DirEntry 替身（快照里已经有大小和修改时间）。"""
    __slots__ = ("path", "name", "_st")

    def __init__(self, e: Entry):
        self.path = e.path
        self.name = os.path.basename(e.path)
        self._st = SimpleNamespace(st_size=e.size, st_mtime_ns=e.mtime_ns)

    def stat(self, follow_symlinks: bool = True):
        return self._st


def build_groups(files: Snapshot, pair_live: bool = True, include_videos: bool = True) -> list[PhotoGroup]:
    """按快照建文件组，与 scanner.scan 同样的分组规则（gid 先按顺序编，照片库同步时再分配）。"""
    by_dir: dict[str, list[_FileEntry]] = defaultdict(list)
    for k in sorted(files):
        e = files[k]
        by_dir[os.path.dirname(k)].append(_FileEntry(e))
    groups: list[PhotoGroup] = []
    for d in sorted(by_dir):
        for members, primary in group_directory(by_dir[d], pair_live, include_videos):
            groups.append(PhotoGroup(gid=len(groups), files=members, primary=primary))
    return groups


# ---------------------------------------------------------------------------
# Windows：文件是否还被别的程序开着写；根文件夹的变化通知
# ---------------------------------------------------------------------------

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _k32.CreateFileW
    _CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                             wintypes.DWORD, wintypes.HANDLE]
    _CreateFileW.restype = wintypes.HANDLE
    _ReadDirectoryChangesW = _k32.ReadDirectoryChangesW
    _ReadDirectoryChangesW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID, wintypes.LPVOID]
    _ReadDirectoryChangesW.restype = wintypes.BOOL
    _CancelIoEx = _k32.CancelIoEx
    _CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    _CancelIoEx.restype = wintypes.BOOL
    _CloseHandle = _k32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL

    _INVALID = ctypes.c_void_p(-1).value
    _GENERIC_READ = 0x80000000
    _FILE_LIST_DIRECTORY = 0x0001
    _SHARE_READ, _SHARE_WRITE, _SHARE_DELETE = 0x1, 0x2, 0x4
    _OPEN_EXISTING = 3
    _FLAG_BACKUP_SEMANTICS = 0x02000000
    _ERROR_SHARING_VIOLATION = 32
    # 文件 / 文件夹的新建、删除、改名，大小与修改时间变化
    _NOTIFY = 0x1 | 0x2 | 0x8 | 0x10


def still_writing(path: str) -> bool:
    """别的程序还开着这个文件在写（复制没完成）。Windows 上以“不允许别人写”的方式打开试试；其他系统不判断。"""
    if os.name != "nt":
        return False
    h = _CreateFileW(path, _GENERIC_READ, _SHARE_READ | _SHARE_DELETE, None, _OPEN_EXISTING, 0, None)
    if not h or h == _INVALID:
        return ctypes.get_last_error() == _ERROR_SHARING_VIOLATION
    _CloseHandle(h)
    return False


def _ready(e: Entry) -> bool:
    """快照里的记录可以用了：文件的实际状态与目录列表一致（NTFS 的目录列表在文件写完前可能是旧的），且没人在写。"""
    try:
        st = os.stat(e.path)
    except OSError:
        return False
    return st.st_size == e.size and st.st_mtime_ns == e.mtime_ns and not still_writing(e.path)


class _Notifier:
    """Windows：一个根文件夹（可含子文件夹）的变化通知，只用来叫醒监视线程，具体变了什么靠快照比较。"""

    def __init__(self, root: str, recursive: bool, wake: Callable[[], None]):
        h = _CreateFileW(root, _FILE_LIST_DIRECTORY, _SHARE_READ | _SHARE_WRITE | _SHARE_DELETE, None,
                         _OPEN_EXISTING, _FLAG_BACKUP_SEMANTICS, None)
        if not h or h == _INVALID:
            raise ctypes.WinError(ctypes.get_last_error())
        self.root = root
        self.alive = True
        self._h = h
        self._open = True
        self._closed = False
        self._lock = threading.Lock()
        self._wake = wake
        self._recursive = recursive
        threading.Thread(target=self._run, name="PhotoCrate-notify", daemon=True).start()

    def _run(self) -> None:
        buf = ctypes.create_string_buffer(32 * 1024)
        n = wintypes.DWORD()
        try:
            while not self._closed:
                ok = _ReadDirectoryChangesW(self._h, buf, len(buf), self._recursive, _NOTIFY, ctypes.byref(n), None, None)
                if self._closed:
                    break
                if not ok:
                    log.info("自动同步：%s 收不到变化通知了（错误 %s），改为定时比较", self.root, ctypes.get_last_error())
                    break
                self._wake()          # n == 0 表示变化太多、缓冲区装不下，同样只是叫醒
        finally:
            self.alive = False
            with self._lock:
                _CloseHandle(self._h)
                self._open = False
            if not self._closed:
                self._wake()          # 通知断了（文件夹被删 / 网络断开）：马上比较一次

    def close(self) -> None:
        self._closed = True
        with self._lock:
            if self._open:
                _CancelIoEx(self._h, None)


class FolderWatcher:
    """在后台线程里监视几个文件夹；稳定下来的变化通过 on_change(Change) 回调（在监视线程里调用）。"""

    SETTLE = 1.0          # 有变化时，两次快照之间隔多久
    SETTLE_MAX = 30.0     # 文件一直被占用（还在写）时，最多隔多久再看
    POLL = 3.0            # 不能用变化通知时的轮询间隔（快照本身很慢时自动放长）
    SAFETY = 60.0         # 变化通知正常时，也隔这么久兜底比较一次（网络盘的通知不一定可靠）
    RECENT = 120.0        # 开始监视时，这么多秒内新建 / 改过的文件先当作“可能还在复制”，确认稳定后再同步

    def __init__(self, roots: list[str], recursive: bool = True,
                 on_change: Callable[[Change], None] | None = None):
        seen, self.roots = set(), []
        for r in roots:
            r = _norm_root(r)
            if os.path.normcase(r) not in seen:
                seen.add(os.path.normcase(r))
                self.roots.append(r)
        self.recursive = recursive
        self.on_change = on_change
        self.committed: Snapshot = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._full = False
        self._notifiers: dict[str, _Notifier] = {}
        self._warned: set[str] = set()
        self._cost = 0.0
        self._emitted = 0
        self._thread: threading.Thread | None = None

    # ---- 控制 ------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="PhotoCrate-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """不等线程结束（快照做到一半也会尽快退出，退出时关掉变化通知）；之后不会再回调。"""
        self._stop.set()
        self._wake.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def poke(self, full: bool = False) -> None:
        """马上比较一次。full=True：即使没有变化也回调一次当前的完整快照（照片库据此补同步）。"""
        if full:
            self._full = True
        self._wake.set()

    @property
    def notifying(self) -> bool:
        """所有在线的根文件夹都能收到系统的变化通知（否则在定时比较）。"""
        live = [r for r in self.roots if os.path.isdir(r)]
        return bool(live) and all((n := self._notifiers.get(r)) is not None and n.alive for r in live)

    # ---- 线程 ------------------------------------------------------------
    def _emit(self, ch: Change) -> None:
        if self._stop.is_set() or self.on_change is None:
            return
        try:
            self.on_change(ch)
        except Exception:
            log.exception("自动同步：回调出错")

    def _snap(self) -> Snapshot:
        t = time.monotonic()
        s = snapshot(self.roots, self.recursive, self._stop, keep=self.committed)
        self._cost = time.monotonic() - t
        return s

    def _ensure_notifiers(self) -> None:
        if os.name != "nt":
            return
        for r in self.roots:
            n = self._notifiers.get(r)
            if (n is not None and n.alive) or not os.path.isdir(r) or self._stop.is_set():
                continue
            try:
                self._notifiers[r] = _Notifier(r, self.recursive, self._wake.set)
                self._warned.discard(r)
            except OSError as e:
                self._notifiers.pop(r, None)
                if r not in self._warned:
                    self._warned.add(r)
                    log.info("自动同步：%s 不支持变化通知（%s），改为定时比较", r, e)

    def _interval(self) -> float:
        return self.SAFETY if self.notifying else max(self.POLL, self._cost * 5)

    def _run(self) -> None:
        try:
            self._ensure_notifiers()
            first = self._snap()
            if self._stop.is_set():
                return
            # 刚拷进来的文件可能还没写完：先不算，走下面“确认稳定”的流程
            now = time.time_ns()
            recent = int(self.RECENT * 1e9)
            self.committed = {k: e for k, e in first.items() if now - max(e.mtime_ns, e.born_ns) > recent}
            self._full = False
            self._emit(Change(files=dict(self.committed), unsettled=frozenset(diff(self.committed, first)), initial=True))
            prev = self._settle(dict(self.committed), first)
            while not self._stop.is_set():
                self._wake.wait(self._interval())
                if self._stop.is_set():
                    break
                self._wake.clear()                 # 先清再拍快照：拍的过程中又有变化会再醒一次，不会漏
                self._ensure_notifiers()
                full, self._full = self._full, False
                cur = self._snap()
                if self._stop.is_set():
                    break
                n = self._emitted
                prev = self._settle(prev, cur)
                if full and self._emitted == n:
                    self._emit(Change(files=dict(self.committed)))
        except Exception:
            log.exception("自动同步：监视线程出错")
        finally:
            for n in list(self._notifiers.values()):
                n.close()

    def _settle(self, prev: Snapshot, cur: Snapshot) -> Snapshot:
        """有变化时每隔一会儿再拍一次快照，稳定下来的变化交给回调；返回最后一份快照。"""
        wait = self.SETTLE
        while True:
            changed = diff(self.committed, cur)
            if not changed:
                return cur
            added, removed, modified = [], [], []
            pending = set()
            for k in changed:
                old, new = self.committed.get(k), cur.get(k)
                if prev.get(k) != new:              # 两次快照不一样：还在变
                    pending.add(k)
                elif new is None:
                    removed.append(old.path)
                    del self.committed[k]
                elif _ready(new):
                    (modified if old is not None else added).append(new.path)
                    self.committed[k] = new
                else:
                    pending.add(k)
            done = len(added) + len(removed) + len(modified)
            if done:
                self._emitted += 1
                self._emit(Change(files=dict(self.committed), added=sorted(added), removed=sorted(removed),
                                  modified=sorted(modified), unsettled=frozenset(pending)))
                if not pending:
                    return cur
            # 只剩被占用的文件、快照也没再变：慢慢放长间隔，别一直狂扫
            wait = self.SETTLE if (done or prev != cur) else min(wait * 2, self.SETTLE_MAX)
            if self._stop.wait(wait):
                return cur
            self._wake.clear()
            prev, cur = cur, self._snap()
            if self._stop.is_set():
                return cur
