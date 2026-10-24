"""安全移动 / 复制 / 撤销 / 崩溃恢复。

- 同一磁盘：os.rename，瞬间完成，不复制数据。
- 跨磁盘：复制到 目标.pctmp → 校验大小（可选校验 SHA-256）→ 改名为最终文件名 → 删除源文件。
  不使用 shutil.move 的隐式回退，因为它不是原子的，也不做校验。
- 一个文件组（RAW+JPG+xmp…）要么全部移动成功，要么整组退回原处。
- 绝不覆盖已存在的文件；删除一律进回收站。
"""
from __future__ import annotations

import errno
import hashlib
import logging
import os
import shutil
import stat
import threading
from dataclasses import dataclass, field
from typing import Callable

from .journal import Journal
from .pathsafe import long_path
from ..i18n import t as _t

log = logging.getLogger(__name__)
TMP_SUFFIX = ".pctmp"

ProgressFn = Callable[[int, int, str], None]


@dataclass
class ExecResult:
    batch_id: int
    done_files: int = 0
    failed_groups: int = 0
    done_groups: int = 0
    cancelled: bool = False
    mapping: dict[str, str] = field(default_factory=dict)   # 旧路径(normcase) → 新路径
    pruned_dirs: list[str] = field(default_factory=list)     # 移动后清理掉的空文件夹
    errors: list[str] = field(default_factory=list)
    copied: list[tuple[str, str]] = field(default_factory=list)   # 复制批次：(源, 目标)


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _is_cross_device(e: OSError) -> bool:
    return e.errno == errno.EXDEV or getattr(e, "winerror", None) == 17


def _sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(long_path(p), "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _remove(p: str) -> None:
    lp = long_path(p)
    try:
        os.remove(lp)
    except PermissionError:
        os.chmod(lp, stat.S_IWRITE)   # 只读文件
        os.remove(lp)


def trash(p: str) -> None:
    """删除到回收站（不可用时才永久删除）。"""
    try:
        from send2trash import send2trash
        send2trash(p)
    except Exception as e:
        log.warning("回收站不可用，直接删除 %s：%s", p, e)
        _remove(p)


def _rename_no_overwrite(a: str, b: str) -> None:
    if os.path.lexists(long_path(b)):
        raise FileExistsError(_t('目标已存在：{0}', b))
    os.rename(long_path(a), long_path(b))   # Windows 下 rename 遇到已存在目标会报错，不会覆盖


def transfer(src: str, dst: str, copy_only: bool = False, verify_hash: bool = False) -> str:
    """把 src 移动（或复制）到 dst。dst 必须不存在。返回 'renamed' 或 'copied'。"""
    if os.path.lexists(long_path(dst)):
        raise FileExistsError(_t('目标已存在：{0}', dst))
    if not os.path.exists(long_path(src)):
        raise FileNotFoundError(_t('源文件不存在：{0}', src))
    if not copy_only:
        try:
            _rename_no_overwrite(src, dst)
            return "renamed"
        except OSError as e:
            if not _is_cross_device(e):
                raise
    tmp = dst + TMP_SUFFIX
    if os.path.exists(long_path(tmp)):
        _remove(tmp)
    try:
        shutil.copy2(long_path(src), long_path(tmp))     # copy2 保留修改时间
        if os.path.getsize(long_path(tmp)) != os.path.getsize(long_path(src)):
            raise IOError(_t("复制后文件大小不一致"))
        if verify_hash and _sha256(tmp) != _sha256(src):
            raise IOError(_t("复制后校验值不一致"))
        _rename_no_overwrite(tmp, dst)
    except BaseException:
        try:
            if os.path.exists(long_path(tmp)):
                _remove(tmp)
        except OSError:
            pass
        raise
    if not copy_only:
        try:
            _remove(src)
        except OSError as e:
            # 源文件被占用删不掉：撤掉刚复制的副本，保持“原样未动”，由上层整组回退
            try:
                _remove(dst)
            except OSError:
                pass
            raise IOError(_t('源文件被占用，无法移动（已撤回复制）：{0}', e)) from e
    return "copied"


def _unique_name(path: str) -> str:
    if not os.path.lexists(long_path(path)):
        return path
    d, name = os.path.split(path)
    s, e = os.path.splitext(name)
    i = 1
    while True:
        cand = os.path.join(d, f"{s} (恢复{i}){e}")
        if not os.path.lexists(long_path(cand)):
            return cand
        i += 1


class Mover:
    def __init__(self, journal: Journal, verify_hash: bool = False):
        self.journal = journal
        self.verify_hash = verify_hash

    # ---- 执行 ------------------------------------------------------------
    def execute(self, plan, progress: ProgressFn | None = None, cancel: threading.Event | None = None,
                extra: dict | None = None, keep_roots: list[str] | None = None) -> ExecResult:
        """keep_roots：数据源 / 目标文件夹。移动后被搬空的子文件夹（只限这些文件夹里面、不含它们本身）会被删掉，
        这样按新规则重新分区不会留下一堆空的旧文件夹；撤销时会重新建出来。"""
        bid = self.journal.create_batch(plan, extra)          # 先写日志
        res = self._run_batch(bid, plan.operation, progress, cancel)
        if keep_roots and plan.operation in ("move", "rename"):
            res.pruned_dirs = self.prune_emptied(bid, keep_roots)
        return res

    def prune_emptied(self, bid: int, keep_roots: list[str]) -> list[str]:
        """删除本批次搬空的源文件夹（逐级向上，到 keep_roots 为止；只删真正空的文件夹）。"""
        from .planner import _inside
        roots = [os.path.normpath(r) for r in keep_roots if r]
        dirs = {os.path.dirname(o["src"]) for o in self.journal.ops(bid) if o["status"] == "done"}
        removed = []
        for d in sorted(dirs, key=lambda p: -len(p)):
            cur = os.path.normpath(d)
            while True:
                if not any(_inside(cur, r) and os.path.normcase(cur) != os.path.normcase(os.path.abspath(r))
                           for r in roots):
                    break
                try:
                    if not os.path.isdir(long_path(cur)) or os.listdir(long_path(cur)):
                        break
                    os.rmdir(long_path(cur))
                    removed.append(cur)
                except OSError:
                    break
                parent = os.path.dirname(cur)
                if parent == cur:
                    break
                cur = parent
        return removed

    def _run_batch(self, bid: int, operation: str, progress: ProgressFn | None,
                   cancel: threading.Event | None) -> ExecResult:
        if operation not in ("move", "copy", "rename"):
            raise ValueError(_t('不能按移动方式执行“{0}”批次', operation))
        res = ExecResult(bid)
        ops = [o for o in self.journal.ops(bid) if o["status"] == "pending"]
        total = len(ops)
        items: dict[int, list[dict]] = {}
        for o in ops:
            items.setdefault(o["item"], []).append(o)
        created_dirs: set[str] = set()
        copy_only = operation == "copy"
        n = 0
        for item_ops in items.values():
            if cancel is not None and cancel.is_set():
                res.cancelled = True
                break
            done_here: list[dict] = []
            try:
                for o in item_ops:
                    if os.path.exists(long_path(o["src"])):
                        st = os.stat(long_path(o["src"]))
                        if st.st_size != o["size"]:
                            raise IOError(_t("源文件在预览后发生了变化，请重新扫描"))
                    d = os.path.dirname(o["dst"])
                    self._makedirs(bid, d, created_dirs)
                    transfer(o["src"], o["dst"], copy_only, self.verify_hash)
                    self.journal.set_op(o["id"], "done")
                    done_here.append(o)
                    n += 1
                    if progress:
                        progress(n, total, os.path.basename(o["src"]))
                self.journal.commit()
                res.done_groups += 1
                for o in done_here:
                    if not copy_only:
                        res.mapping[_norm(o["src"])] = o["dst"]
                    else:
                        res.copied.append((o["src"], o["dst"]))
                    res.done_files += 1
            except Exception as e:
                log.warning("移动失败：%s", e)
                res.errors.append(_t('{0}：{1}', os.path.basename(item_ops[0]['src']), e))
                res.failed_groups += 1
                # 整组退回
                for o in reversed(done_here):
                    try:
                        if copy_only:
                            _remove(o["dst"])
                        else:
                            transfer(o["dst"], o["src"])
                        self.journal.set_op(o["id"], "failed", _t("已回退（同组文件失败）"))
                    except Exception as e2:
                        self.journal.set_op(o["id"], "done", _t('回退失败：{0}', e2))
                        res.errors.append(_t('回退失败 {0}：{1}', o['dst'], e2))
                for o in item_ops:
                    if o not in done_here:
                        self.journal.set_op(o["id"], "failed", str(e))
                self.journal.commit()
        if res.cancelled:
            status = "cancelled"
        else:
            status = "failed" if res.failed_groups else "done"
        # 取消时：未执行的保持 pending → 标记为 skipped
        for o in self.journal.ops(bid):
            if o["status"] == "pending":
                self.journal.set_op(o["id"], "skipped")
        self.journal.commit()
        self.journal.finish_batch(bid, status)
        return res

    # ---- 删除（放入回收站） ------------------------------------------------
    def delete(self, plan, progress: ProgressFn | None = None, cancel: threading.Event | None = None):
        """按计划把文件逐组放入回收站。一组里任何一个失败，已删除的同组文件从回收站放回。"""
        import json
        import time
        from .cleanup import DeleteResult
        from .trashbin import restore_from_trash, send_to_trash

        t0 = time.time()
        bid = self.journal.create_batch(plan, {"kind": "delete", "t0": t0})
        res = DeleteResult(bid)
        ops = self.journal.ops(bid)
        items: dict[int, list[dict]] = {}
        for o in ops:
            items.setdefault(o["item"], []).append(o)
        total, n = len(ops), 0
        for item_ops in items.values():
            if cancel is not None and cancel.is_set():
                res.cancelled = True
                break
            done_here: list[dict] = []
            try:
                for o in item_ops:
                    if not os.path.exists(long_path(o["src"])):
                        raise FileNotFoundError(_t("文件已不在原位置，请重新扫描"))
                    if os.path.getsize(long_path(o["src"])) != o["size"]:
                        raise IOError(_t("文件在预览后发生了变化，请重新扫描"))
                    send_to_trash(o["src"])
                    self.journal.set_op(o["id"], "done")
                    done_here.append(o)
                    n += 1
                    if progress:
                        progress(n, total, os.path.basename(o["src"]))
                self.journal.commit()
                res.done_groups += 1
                for o in done_here:
                    res.deleted.append(o["src"])
                    res.freed += o["size"] or 0
            except Exception as e:
                res.failed_groups += 1
                res.errors.append(_t('{0}：{1}', os.path.basename(item_ops[0]['src']), e))
                for o in reversed(done_here):
                    try:
                        restore_from_trash(o["src"], t0)
                        self.journal.set_op(o["id"], "failed", _t("已从回收站放回（同组文件失败）"))
                    except Exception as e2:
                        self.journal.set_op(o["id"], "done", _t('放回失败：{0}', e2))
                        res.deleted.append(o["src"])
                        res.errors.append(_t('放回失败 {0}：{1}', o['src'], e2))
                for o in item_ops:
                    if o not in done_here:
                        self.journal.set_op(o["id"], "failed", str(e))
                self.journal.commit()
        for o in self.journal.ops(bid):
            if o["status"] == "pending":
                self.journal.set_op(o["id"], "skipped")
        self.journal.commit()
        self.journal.finish_batch(bid, "cancelled" if res.cancelled else ("failed" if res.failed_groups else "done"))
        return res

    def _makedirs(self, bid: int, d: str, created: set[str]) -> None:
        if _norm(d) in created or os.path.isdir(long_path(d)):
            return
        # 记录本批次新建的每一级目录，撤销时据此清理空文件夹
        missing = []
        p = os.path.abspath(d)
        while not os.path.isdir(long_path(p)):
            missing.append(p)
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        os.makedirs(long_path(d), exist_ok=True)
        for m in missing:
            created.add(_norm(m))
            self.journal.add_dir(bid, m)

    # ---- 撤销 ------------------------------------------------------------
    def undo(self, bid: int, progress: ProgressFn | None = None,
             cancel: threading.Event | None = None) -> ExecResult:
        batch = self.journal.batch(bid)
        res = ExecResult(bid)
        if not batch:
            res.errors.append(_t("找不到该批次"))
            return res
        # “复制”与“写入 XMP”的撤销都是：把本批次新建的文件放入回收站
        copy_only = batch["operation"] in ("copy", "xmp")
        is_delete = batch["operation"] == "delete"
        t0 = None
        if is_delete:
            import json
            try:
                t0 = float(json.loads(batch["note"] or "{}").get("t0"))
            except (TypeError, ValueError):
                t0 = None
        self.journal.set_batch_status(bid, "undoing")
        ops = [o for o in self.journal.ops(bid) if o["status"] == "done"]
        total = len(ops)
        failed = 0
        for i, o in enumerate(reversed(ops), 1):
            if cancel is not None and cancel.is_set():
                res.cancelled = True
                break
            try:
                if is_delete:
                    from .trashbin import restore_from_trash
                    if os.path.exists(long_path(o["src"])) and os.path.getsize(long_path(o["src"])) == o["size"]:
                        self.journal.set_op(o["id"], "undone")       # 已经还原过（手动或上次撤销）
                    else:
                        restore_from_trash(o["src"], t0)
                        self.journal.set_op(o["id"], "undone")
                    res.mapping[_norm(o["src"])] = o["src"]
                elif o["role"] == "xmp_edit":
                    # 写入 XMP 时修改过的已有 .xmp：用备份换回原样
                    target = o["dst"]
                    if not os.path.exists(long_path(target)):
                        target = self.journal.find_moved(o["dst"], bid) or target
                    if not os.path.exists(long_path(o["src"])):
                        raise FileNotFoundError(_t("找不到当时的备份，无法恢复"))
                    if os.path.exists(long_path(target)):
                        trash(target)
                    os.makedirs(long_path(os.path.dirname(target)), exist_ok=True)
                    shutil.copy2(long_path(o["src"]), long_path(target))
                    self.journal.set_op(o["id"], "undone")
                elif copy_only:
                    target = o["dst"]
                    if not os.path.exists(long_path(target)):
                        # 新建的文件之后又被本软件移动过（例如 .xmp 跟着照片进了分区文件夹）：顺着日志找到现在的位置
                        target = self.journal.find_moved(o["dst"], bid) or target
                    if os.path.exists(long_path(target)):
                        trash(target)
                    self.journal.set_op(o["id"], "undone")
                else:
                    if not os.path.exists(long_path(o["dst"])):
                        if os.path.exists(long_path(o["src"])) and os.path.getsize(long_path(o["src"])) == o["size"]:
                            # 上次撤销中途退出时已经移回
                            self.journal.set_op(o["id"], "undone")
                            res.done_files += 1
                            continue
                        if o["role"] == "sidecar" and self.journal.removed_by_xmp_undo(o["src"]):
                            # 这个 .xmp 是本软件写入的，已经随“写入 XMP”批次撤销而放入回收站
                            self.journal.set_op(o["id"], "undone", _t("已随“写入 XMP”撤销放入回收站"))
                            res.done_files += 1
                            continue
                        raise FileNotFoundError(_t("文件已不在目标位置（可能被手动移动或删除）"))
                    back = o["src"]
                    os.makedirs(long_path(os.path.dirname(back)), exist_ok=True)
                    if os.path.lexists(long_path(back)):
                        back = _unique_name(back)
                        res.errors.append(_t('原位置已有同名文件，已恢复为：{0}', os.path.basename(back)))
                    transfer(o["dst"], back)
                    res.mapping[_norm(o["dst"])] = back
                    self.journal.set_op(o["id"], "undone", src=back if back != o["src"] else None)
                res.done_files += 1
            except Exception as e:
                failed += 1
                res.errors.append(_t('{0}：{1}', os.path.basename(o['src'] if is_delete else o['dst']), e))
                self.journal.set_op(o["id"], "undo_failed", str(e))
            if i % 20 == 0:
                self.journal.commit()
            if progress:
                progress(i, total, os.path.basename(o["dst"]))
        self.journal.commit()
        self._cleanup_dirs(bid)
        status = "undone" if not failed and not res.cancelled else "partially_undone"
        self.journal.finish_undo(bid, status)
        res.failed_groups = failed
        return res

    def _cleanup_dirs(self, bid: int) -> None:
        dirs = sorted(set(self.journal.dirs(bid)), key=lambda p: -len(p))
        for d in dirs:
            try:
                if os.path.isdir(long_path(d)) and not os.listdir(long_path(d)):
                    os.rmdir(long_path(d))
            except OSError:
                pass

    # ---- 崩溃恢复 --------------------------------------------------------
    def reconcile(self, bid: int) -> dict[str, int]:
        """程序在执行中途退出后，根据磁盘实际状态修正日志。"""
        batch = self.journal.batch(bid)
        copy_only = bool(batch and batch["operation"] == "copy")
        fixed = {"moved": 0, "tmp_removed": 0, "src_removed": 0, "pending": 0}
        if batch and batch["operation"] == "delete":
            # 删除：原位置已经没有的文件记为完成（在回收站里，撤销时放回）
            for o in self.journal.ops(bid):
                if o["status"] == "pending":
                    if not os.path.exists(long_path(o["src"])):
                        self.journal.set_op(o["id"], "done")
                        fixed["moved"] += 1
                    else:
                        fixed["pending"] += 1
            self.journal.commit()
            return fixed
        if batch and batch["operation"] == "xmp":
            # 写入 XMP：已生成的文件记为完成（撤销时会放入回收站），没生成的保持未完成
            for o in self.journal.ops(bid):
                if o["status"] == "pending":
                    if o["role"] == "xmp_edit":
                        # 已经做了备份 = 可能已经改过；记为完成，撤销时会换回备份（备份就是原样）
                        if os.path.exists(long_path(o["src"])):
                            self.journal.set_op(o["id"], "done")
                            fixed["moved"] += 1
                        else:
                            fixed["pending"] += 1
                    elif os.path.exists(long_path(o["dst"])):
                        self.journal.set_op(o["id"], "done")
                        fixed["moved"] += 1
                    else:
                        fixed["pending"] += 1
            self.journal.commit()
            return fixed
        for o in self.journal.ops(bid):
            if o["status"] != "pending":
                continue
            tmp = o["dst"] + TMP_SUFFIX
            if os.path.exists(long_path(tmp)):
                try:
                    _remove(tmp)
                    fixed["tmp_removed"] += 1
                except OSError:
                    pass
            src_ok = os.path.exists(long_path(o["src"]))
            dst_ok = os.path.exists(long_path(o["dst"]))
            if dst_ok and not src_ok:
                self.journal.set_op(o["id"], "done")
                fixed["moved"] += 1
            elif dst_ok and src_ok:
                if copy_only:
                    self.journal.set_op(o["id"], "done")
                elif os.path.getsize(long_path(o["dst"])) == os.path.getsize(long_path(o["src"])):
                    # 复制已完成但源文件未删除
                    _remove(o["src"])
                    self.journal.set_op(o["id"], "done")
                    fixed["src_removed"] += 1
            else:
                fixed["pending"] += 1
        self.journal.commit()
        return fixed

    def resume(self, bid: int, progress: ProgressFn | None = None,
               cancel: threading.Event | None = None) -> ExecResult:
        self.reconcile(bid)
        batch = self.journal.batch(bid)
        if batch and batch["operation"] in ("xmp", "delete"):
            # 写入 XMP / 删除：不自动“继续”，保留已完成的部分，其余标为跳过（可重新生成计划）
            res = ExecResult(bid)
            for o in self.journal.ops(bid):
                if o["status"] == "pending":
                    self.journal.set_op(o["id"], "skipped")
                elif o["status"] == "done":
                    res.done_files += 1
            self.journal.commit()
            self.journal.finish_batch(bid, "cancelled")
            return res
        self.journal.set_batch_status(bid, "running")
        return self._run_batch(bid, batch["operation"] if batch else "move", progress, cancel)

    def rollback(self, bid: int, progress: ProgressFn | None = None,
                 cancel: threading.Event | None = None) -> ExecResult:
        self.reconcile(bid)
        for o in self.journal.ops(bid):
            if o["status"] == "pending":
                self.journal.set_op(o["id"], "skipped")
        self.journal.commit()
        return self.undo(bid, progress, cancel)
