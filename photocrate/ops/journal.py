"""操作日志（SQLite）—— 撤销与崩溃恢复的基础。

规则：先写日志，再动文件。每个批次开始前，把全部"源→目标"写入日志（状态 pending），
每完成一组就提交一次状态。程序意外退出后，下次启动能发现未完成的批次并提示继续或回滚。
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from ..i18n import t as _t


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS batches(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT, finished TEXT, operation TEXT, target_root TEXT,
                spec TEXT, status TEXT, total INTEGER, done INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0, groups INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0, note TEXT);
            CREATE TABLE IF NOT EXISTS ops(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER, seq INTEGER, item INTEGER, grp INTEGER,
                src TEXT, dst TEXT, size INTEGER, mtime_ns INTEGER, role TEXT,
                status TEXT, error TEXT);
            CREATE INDEX IF NOT EXISTS ops_batch ON ops(batch_id, seq);
            CREATE TABLE IF NOT EXISTS dirs(batch_id INTEGER, path TEXT);
            """
        )
        self._db.commit()

    # ---- 写入 ------------------------------------------------------------
    def create_batch(self, plan, extra: dict[str, Any] | None = None) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO batches(created, operation, target_root, spec, status, total, groups, bytes, note)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (now, plan.operation, plan.target_root, plan.spec_text, "running", plan.file_count,
                 len(plan.items), plan.total_bytes, json.dumps(extra or {}, ensure_ascii=False)),
            )
            bid = cur.lastrowid
            rows = []
            seq = 0
            for idx, item in enumerate(plan.items):
                for op in item.ops:
                    rows.append((bid, seq, idx, item.gid, op.src, op.dst, op.size, op.mtime_ns, op.role, "pending"))
                    seq += 1
            self._db.executemany(
                "INSERT INTO ops(batch_id, seq, item, grp, src, dst, size, mtime_ns, role, status)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        return int(bid)

    def set_op(self, op_id: int, status: str, error: str | None = None, dst: str | None = None,
               src: str | None = None, commit: bool = False) -> None:
        with self._lock:
            sets, args = ["status=?", "error=?"], [status, error]
            if dst is not None:
                sets.append("dst=?")
                args.append(dst)
            if src is not None:
                sets.append("src=?")
                args.append(src)
            self._db.execute(f"UPDATE ops SET {', '.join(sets)} WHERE id=?", (*args, op_id))
            if commit:
                self._db.commit()

    def add_dir(self, batch_id: int, path: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO dirs(batch_id, path) VALUES(?,?)", (batch_id, path))

    def commit(self) -> None:
        with self._lock:
            self._db.commit()

    def finish_batch(self, batch_id: int, status: str) -> None:
        with self._lock, self._db:
            done = self._db.execute("SELECT COUNT(*) FROM ops WHERE batch_id=? AND status='done'", (batch_id,)).fetchone()[0]
            failed = self._db.execute("SELECT COUNT(*) FROM ops WHERE batch_id=? AND status='failed'", (batch_id,)).fetchone()[0]
            self._db.execute(
                "UPDATE batches SET status=?, finished=?, done=?, failed=? WHERE id=?",
                (status, datetime.now().isoformat(timespec="seconds"), done, failed, batch_id))

    def finish_undo(self, batch_id: int, status: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE batches SET status=?, finished=? WHERE id=?",
                             (status, datetime.now().isoformat(timespec="seconds"), batch_id))

    def set_batch_status(self, batch_id: int, status: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE batches SET status=? WHERE id=?", (status, batch_id))

    # ---- 读取 ------------------------------------------------------------
    def batches(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM batches ORDER BY id DESC LIMIT ?", (limit,))]

    def batch(self, batch_id: int) -> dict[str, Any] | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            return dict(r) if r else None

    def ops(self, batch_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM ops WHERE batch_id=? ORDER BY seq", (batch_id,))]

    def dirs(self, batch_id: int) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT path FROM dirs WHERE batch_id=?", (batch_id,))]

    def find_moved(self, path: str, after_batch: int) -> str | None:
        """path 在 after_batch 之后的“移动”批次里被移到了哪里（逐批跟踪，返回最新位置；没被移动过返回 None）。"""
        cur, bid, moved = path, after_batch, False
        with self._lock:
            for _ in range(50):
                r = self._db.execute(
                    "SELECT o.dst, o.batch_id FROM ops o JOIN batches b ON b.id = o.batch_id "
                    "WHERE b.operation IN ('move', 'rename') AND o.batch_id > ? AND o.status = 'done' AND lower(o.src) = lower(?) "
                    "ORDER BY o.batch_id LIMIT 1", (bid, cur)).fetchone()
                if not r:
                    break
                cur, bid, moved = r[0], r[1], True
        return cur if moved else None

    def later_dependents(self, batch_id: int) -> list[int]:
        """之后的“移动 / 重命名”批次里，接着移动了本批次结果的那些批次（连带的也算），按编号从小到大。
        撤销本批次前要先（从新到旧）撤销它们，否则文件已不在本批次记录的位置。"""
        out: set[int] = set()
        frontier = [batch_id]
        with self._lock:
            while frontier:
                b = frontier.pop()
                dsts = [r[0].lower() for r in self._db.execute(
                    "SELECT dst FROM ops WHERE batch_id=? AND status='done'", (b,))]
                if not dsts:
                    continue
                for i in range(0, len(dsts), 500):
                    ch = dsts[i:i + 500]
                    q = ("SELECT DISTINCT o.batch_id FROM ops o JOIN batches bb ON bb.id = o.batch_id "
                         "WHERE bb.operation IN ('move', 'rename') AND o.batch_id > ? AND o.status = 'done' "
                         f"AND lower(o.src) IN ({','.join('?' * len(ch))})")
                    for (nb,) in self._db.execute(q, (b, *ch)):
                        if nb not in out:
                            out.add(nb)
                            frontier.append(nb)
        return sorted(out)

    def removed_by_xmp_undo(self, path: str) -> bool:
        """path 是否是某个“写入 XMP”批次新建、之后又被撤销（放入回收站）的文件——沿移动记录往前追溯到它最初的位置。"""
        cur = path
        with self._lock:
            for _ in range(50):
                if self._db.execute(
                        "SELECT 1 FROM ops o JOIN batches b ON b.id = o.batch_id WHERE b.operation = 'xmp' "
                        "AND o.status = 'undone' AND lower(o.dst) = lower(?) LIMIT 1", (cur,)).fetchone():
                    return True
                r = self._db.execute(
                    "SELECT o.src FROM ops o JOIN batches b ON b.id = o.batch_id WHERE b.operation IN ('move', 'rename') "
                    "AND o.status IN ('done', 'undone') AND lower(o.dst) = lower(?) ORDER BY o.batch_id DESC LIMIT 1",
                    (cur,)).fetchone()
                if not r or r[0] == cur:
                    return False
                cur = r[0]
        return False

    def incomplete(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM batches WHERE status IN ('running','undoing') ORDER BY id")]

    def export_csv(self, batch_id: int, path: str) -> None:
        rows = self.ops(batch_id)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([_t("序号"), _t("源路径"), _t("目标路径"), _t("大小(字节)"), _t("状态"), _t("错误")])
            for r in rows:
                w.writerow([r["seq"] + 1, r["src"], r["dst"], r["size"], r["status"], r["error"] or ""])

    def close(self) -> None:
        with self._lock:
            try:
                self._db.commit()
                self._db.close()
            except Exception:
                pass


OPERATION_LABELS = {"move": _t("移动"), "copy": _t("复制"), "xmp": _t("写入 XMP"), "delete": _t("删除到回收站"), "rename": _t("重命名")}

STATUS_LABELS = {
    "running": _t("进行中（未完成）"), "done": _t("已完成"), "cancelled": _t("已取消"), "failed": _t("有失败"),
    "undone": _t("已撤销"), "partially_undone": _t("部分撤销"), "undoing": _t("撤销中（未完成）"),
    "pending": _t("等待"), "skipped": _t("跳过"), "undo_failed": _t("撤销失败"),
}


def journal_path() -> Path:
    from ..config import user_data_dir
    return user_data_dir() / "journal.db"


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)
