"""特征缓存（SQLite，WAL 模式）。

键是"文件指纹"（文件名+大小+修改时间），与路径无关：文件被本软件移动后仍然命中缓存。
- meta：每个文件的归一化元数据
- pix：每个主图 × 每个像素分析器的结果（带版本号，算法升级后自动失效）
- preview：缩略图路径与预览尺寸
- emb：AI 向量（CLIP 图片向量，float16），按 模型名 区分
- ocr：照片里识别出的文字（每行文字 + 分数 + 位置，JSON），按 识别版本 区分
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

META_VERSION = 6     # v5：增加星级 / 色标（rating, label）；v6：视频（带时区的拍摄时间、时长、QuickTime GPS）


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
            CREATE TABLE IF NOT EXISTS emb(sig TEXT, model TEXT, data BLOB, PRIMARY KEY(sig, model));
            CREATE TABLE IF NOT EXISTS ocr(sig TEXT, ver TEXT, data TEXT, PRIMARY KEY(sig, ver));
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

    # ---- AI 向量 --------------------------------------------------------
    def get_emb_many(self, sigs: list[str], model: str) -> dict[str, "Any"]:
        import numpy as np
        out = {}
        with self._lock:
            for ch in self._chunks(list(set(sigs))):
                q = f"SELECT sig, data FROM emb WHERE model=? AND sig IN ({','.join('?' * len(ch))})"
                for sig, data in self._db.execute(q, [model, *ch]):
                    out[sig] = np.frombuffer(data, dtype=np.float16).astype(np.float32)
        return out

    def put_emb_many(self, items: dict[str, "Any"], model: str) -> None:
        import numpy as np
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO emb(sig, model, data) VALUES(?,?,?)",
                                 [(s, model, np.asarray(v, dtype=np.float16).tobytes()) for s, v in items.items()])
            self._db.commit()

    def count_emb(self, model: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM emb WHERE model=?", (model,)).fetchone()[0]

    # ---- 照片里的文字 ----------------------------------------------------
    def get_ocr_many(self, sigs: list[str], ver: str) -> dict[str, list]:
        out = {}
        with self._lock:
            for ch in self._chunks(list(set(sigs))):
                q = f"SELECT sig, data FROM ocr WHERE ver=? AND sig IN ({','.join('?' * len(ch))})"
                for sig, data in self._db.execute(q, [ver, *ch]):
                    try:
                        out[sig] = json.loads(data)
                    except ValueError:
                        pass
        return out

    def put_ocr_many(self, items: dict[str, list], ver: str) -> None:
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO ocr(sig, ver, data) VALUES(?,?,?)",
                                 [(s, ver, json.dumps(v, ensure_ascii=False, separators=(",", ":")))
                                  for s, v in items.items()])
            self._db.commit()

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
                self._db.execute("INSERT OR REPLACE INTO emb(sig, model, data) "
                                 "SELECT ?, model, data FROM emb WHERE sig=?", (new, old))
                self._db.execute("INSERT OR REPLACE INTO ocr(sig, ver, data) "
                                 "SELECT ?, ver, data FROM ocr WHERE sig=?", (new, old))
            self._db.commit()

    def commit(self) -> None:
        with self._lock:
            self._db.commit()

    # ---- 维护 ------------------------------------------------------------
    def clear(self) -> None:
        with self._lock:
            self._db.executescript("DELETE FROM meta; DELETE FROM pix; DELETE FROM preview; DELETE FROM emb; DELETE FROM ocr;")
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
