"""特征缓存（SQLite，WAL 模式）。

键是"文件指纹"（文件名+大小+修改时间），与路径无关：文件被本软件移动后仍然命中缓存。
- meta：每个文件的归一化元数据
- pix：每个主图 × 每个像素分析器的结果（带版本号，算法升级后自动失效）
- preview：缩略图路径与预览尺寸
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

META_VERSION = 5     # v5：增加星级 / 色标（rating, label）


class FeatureCache:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta(sig TEXT PRIMARY KEY, ver INTEGER, data TEXT);
            CREATE TABLE IF NOT EXISTS pix(sig TEXT, analyzer TEXT, ver INTEGER, data TEXT,
                                           PRIMARY KEY(sig, analyzer));
            CREATE TABLE IF NOT EXISTS preview(sig TEXT PRIMARY KEY, thumb TEXT, w INTEGER, h INTEGER, src TEXT);
            """
        )
        self._db.commit()

    @staticmethod
    def _chunks(items: list[str], n: int = 500) -> Iterable[list[str]]:
        for i in range(0, len(items), n):
            yield items[i:i + n]

    # ---- 元数据 ----------------------------------------------------------
    def get_meta_many(self, sigs: list[str]) -> dict[str, dict[str, Any]]:
        out = {}
        with self._lock:
            for ch in self._chunks(list(set(sigs))):
                q = f"SELECT sig, data FROM meta WHERE ver=? AND sig IN ({','.join('?' * len(ch))})"
                for sig, data in self._db.execute(q, [META_VERSION, *ch]):
                    out[sig] = json.loads(data)
        return out

    def put_meta_many(self, items: dict[str, dict[str, Any]]) -> None:
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO meta(sig, ver, data) VALUES(?,?,?)",
                [(s, META_VERSION, json.dumps(d, ensure_ascii=False)) for s, d in items.items()],
            )
            self._db.commit()

    # ---- 像素特征 --------------------------------------------------------
    def get_pix_many(self, sigs: list[str]) -> dict[str, dict[str, tuple[int, Any]]]:
        out: dict[str, dict[str, tuple[int, Any]]] = {}
        with self._lock:
            for ch in self._chunks(list(set(sigs))):
                q = f"SELECT sig, analyzer, ver, data FROM pix WHERE sig IN ({','.join('?' * len(ch))})"
                for sig, an, ver, data in self._db.execute(q, ch):
                    out.setdefault(sig, {})[an] = (ver, json.loads(data))
        return out

    def get_preview_many(self, sigs: list[str]) -> dict[str, dict[str, Any]]:
        out = {}
        with self._lock:
            for ch in self._chunks(list(set(sigs))):
                q = f"SELECT sig, thumb, w, h, src FROM preview WHERE sig IN ({','.join('?' * len(ch))})"
                for sig, thumb, w, h, src in self._db.execute(q, ch):
                    out[sig] = {"thumb": thumb, "w": w, "h": h, "src": src}
        return out

    def put_pixel_result(self, res: dict[str, Any]) -> None:
        sig = res["sig"]
        with self._lock:
            for an, item in (res.get("feats") or {}).items():
                self._db.execute(
                    "INSERT OR REPLACE INTO pix(sig, analyzer, ver, data) VALUES(?,?,?,?)",
                    (sig, an, item["v"], json.dumps(item["data"], ensure_ascii=False)),
                )
            pv = res.get("preview")
            if pv:
                self._db.execute(
                    "INSERT OR REPLACE INTO preview(sig, thumb, w, h, src) VALUES(?,?,?,?,?)",
                    (sig, res.get("thumb"), pv.get("w"), pv.get("h"), pv.get("src")),
                )

    def copy_keys(self, mapping: dict[str, str]) -> None:
        """文件改名后（大小、修改时间不变）把旧指纹的缓存复制到新指纹，避免重新分析。"""
        if not mapping:
            return
        with self._lock:
            for old, new in mapping.items():
                self._db.execute("INSERT OR REPLACE INTO meta(sig, ver, data) SELECT ?, ver, data FROM meta WHERE sig=?",
                                 (new, old))
                self._db.execute("INSERT OR REPLACE INTO pix(sig, analyzer, ver, data) "
                                 "SELECT ?, analyzer, ver, data FROM pix WHERE sig=?", (new, old))
                self._db.execute("INSERT OR REPLACE INTO preview(sig, thumb, w, h, src) "
                                 "SELECT ?, thumb, w, h, src FROM preview WHERE sig=?", (new, old))
            self._db.commit()

    def commit(self) -> None:
        with self._lock:
            self._db.commit()

    # ---- 维护 ------------------------------------------------------------
    def clear(self) -> None:
        with self._lock:
            self._db.executescript("DELETE FROM meta; DELETE FROM pix; DELETE FROM preview;")
            self._db.commit()
            self._db.execute("VACUUM")

    def stats(self) -> dict[str, int]:
        with self._lock:
            m = self._db.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
            p = self._db.execute("SELECT COUNT(*) FROM preview").fetchone()[0]
        size = 0
        for suffix in ("", "-wal"):
            try:
                size += os.path.getsize(str(self.path) + suffix)
            except OSError:
                pass
        return {"meta": m, "preview": p, "bytes": size}

    def close(self) -> None:
        with self._lock:
            try:
                self._db.commit()
                self._db.close()
            except Exception:
                pass
