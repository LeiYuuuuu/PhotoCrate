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

    def incomplete(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM batches WHERE status IN ('running','undoing') ORDER BY id")]

    def export_csv(self, batch_id: int, path: str) -> None:
        rows = self.ops(batch_id)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["序号", "源路径", "目标路径", "大小(字节)", "状态", "错误"])
            for r in rows:
                w.writerow([r["seq"] + 1, r["src"], r["dst"], r["size"], r["status"], r["error"] or ""])

    def close(self) -> None:
        with self._lock:
            try:
                self._db.commit()
                self._db.close()
            except Exception:
                pass


STATUS_LABELS = {
    "running": "进行中（未完成）", "done": "已完成", "cancelled": "已取消", "failed": "有失败",
    "undone": "已撤销", "partially_undone": "部分撤销", "undoing": "撤销中（未完成）",
    "pending": "等待", "skipped": "跳过", "undo_failed": "撤销失败",
}


def journal_path() -> Path:
    from ..config import user_data_dir
    return user_data_dir() / "journal.db"


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)
