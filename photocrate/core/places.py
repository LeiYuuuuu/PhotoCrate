"""用户标注（手动地点、时间校正）的存储 + 地名解析。

- 标注是用户自己输入的数据，存在用户数据目录的 annotations.db（不是缓存，清空缓存不会丢）。
- 键是照片组里每个图片文件的“文件指纹”（文件名+大小+修改时间），与路径无关：
  照片被移动后标注依然有效；RAW 与 JPG 分开放、配对关系变化后也不会丢。
- 每类标注一张表：places（手动地点）、timeshift（时间校正 {seconds, tz}）、rating（星级 / 色标 / 留弃 {rating, label, flag}）。
- 地名解析：用户输入“日本/东京”“京都”“湖南/邵阳”等，尽量补全成 国家/省/市/区县 的标准写法；
  补全候选来自 GeoToolCN（国内行政区划）与 data/place_names_zh.json + GeoNames（海外）。
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import find_resource, user_data_dir

LEVELS = ("country", "province", "city", "district")


# ---------------------------------------------------------------------------
# 标注存储
# ---------------------------------------------------------------------------

class AnnotationStore:
    KINDS = ("places", "timeshift", "rating", "people", "face_fix")
    # people：已命名的人（键 = 人物 id）；face_fix：人脸更正（键 = 主图指纹#序号）

    def __init__(self, path: Path | None = None):
        self.path = path or (user_data_dir() / "annotations.db")
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        for t in self.KINDS:
            self._db.execute(f"CREATE TABLE IF NOT EXISTS {t}(sig TEXT PRIMARY KEY, data TEXT, updated TEXT)")
        self._db.commit()

    @classmethod
    def _table(cls, kind: str) -> str:
        if kind not in cls.KINDS:
            raise ValueError(f"未知的标注类型：{kind}")
        return kind

    def get_many(self, sigs: list[str], kind: str = "places") -> dict[str, dict[str, Any]]:
        t = self._table(kind)
        out: dict[str, dict[str, Any]] = {}
        sigs = list(set(sigs))
        with self._lock:
            for i in range(0, len(sigs), 500):
                ch = sigs[i:i + 500]
                q = f"SELECT sig, data FROM {t} WHERE sig IN ({','.join('?' * len(ch))})"
                for sig, data in self._db.execute(q, ch):
                    try:
                        out[sig] = json.loads(data)
                    except ValueError:
                        pass
        return out

    def set_many(self, items: dict[str, dict[str, Any]], kind: str = "places") -> None:
        t = self._table(kind)
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._db:
            self._db.executemany(
                f"INSERT OR REPLACE INTO {t}(sig, data, updated) VALUES(?,?,?)",
                [(s, json.dumps(d, ensure_ascii=False), now) for s, d in items.items()])

    def clear_many(self, sigs: list[str], kind: str = "places") -> None:
        t = self._table(kind)
        with self._lock, self._db:
            self._db.executemany(f"DELETE FROM {t} WHERE sig=?", [(s,) for s in sigs])

    def copy_keys(self, mapping: dict[str, str]) -> int:
        """文件改名后指纹会变：把旧指纹下的所有标注移到新指纹（撤销改名时同样会移回来）。"""
        n = 0
        olds = list(mapping)
        with self._lock, self._db:
            for t in self.KINDS:
                for i in range(0, len(olds), 500):
                    ch = olds[i:i + 500]
                    rows = self._db.execute(
                        f"SELECT sig, data, updated FROM {t} WHERE sig IN ({','.join('?' * len(ch))})", ch).fetchall()
                    for sig, data, upd in rows:
                        self._db.execute(f"INSERT OR REPLACE INTO {t}(sig, data, updated) VALUES(?,?,?)",
                                         (mapping[sig], data, upd))
                        if mapping[sig] != sig:
                            self._db.execute(f"DELETE FROM {t} WHERE sig=?", (sig,))
                        n += 1
        return n

    def all(self, kind: str) -> dict[str, dict[str, Any]]:
        t = self._table(kind)
        out = {}
        with self._lock:
            for sig, data in self._db.execute(f"SELECT sig, data FROM {t}"):
                try:
                    out[sig] = json.loads(data)
                except ValueError:
                    pass
        return out

    def clear_all(self, kind: str) -> int:
        t = self._table(kind)
        with self._lock, self._db:
            return self._db.execute(f"DELETE FROM {t}").rowcount

    def count(self, kind: str = "places") -> int:
        t = self._table(kind)
        with self._lock:
            return int(self._db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 地名索引与解析
# ---------------------------------------------------------------------------

_SPLIT = re.compile(r"\s*[/／\\|,，、>＞]\s*")
_SUFFIXES = ("特别行政区", "自治区", "自治州", "自治县", "地区", "都", "道", "府", "县", "省", "市", "区", "州")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def _core(s: str) -> str:
    """去掉行政后缀便于匹配：东京都→东京，湖南省→湖南，京都府→京都。"""
    s = _norm(s)
    for suf in _SUFFIXES:
        if len(s) > len(suf) + 1 and s.endswith(suf):
            return s[: -len(suf)]
    return s


class PlaceIndex:
    """所有可补全的标准地点路径：(国家, 省, 市, 区县)。"""

    _inst: "PlaceIndex | None" = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "PlaceIndex":
        with cls._lock:
            if cls._inst is None:
                cls._inst = cls()
            return cls._inst

    def __init__(self):
        self.entries: list[dict[str, Any]] = []
        self._seen: set[tuple] = set()
        self._load_world()
        self._load_china()

    def _add(self, country, province=None, city=None, district=None, cc=None, lat=None, lon=None):
        key = (country, province, city, district)
        if key in self._seen or not country:
            return
        self._seen.add(key)
        self.entries.append({"country": country, "province": province, "city": city, "district": district,
                             "cc": cc, "lat": lat, "lon": lon})

    def _load_world(self) -> None:
        p = find_resource("data", "place_names_zh.json")
        if not p:
            return
        zh = json.loads(p.read_text("utf-8"))
        countries, admin1, cities = zh.get("countries", {}), zh.get("admin1", {}), zh.get("cities", {})
        for cc, name in countries.items():
            if cc in ("HK", "MO", "TW"):
                continue
            self._add(name, cc=cc)
        for key, name in admin1.items():
            cc = key.split("/", 1)[0]
            if cc in countries:
                self._add(countries[cc], name, cc=cc)
        # 城市：用 GeoNames 找到所属一级行政区与坐标
        want = {k: v for k, v in cities.items()}
        found: dict[str, tuple] = {}
        gp = find_resource("data", "geonames_cities1000.csv.gz")
        if gp and want:
            with gzip.open(gp, "rt", encoding="utf-8", newline="") as f:
                r = csv.reader(f)
                next(r, None)
                from .geo import normalize_city_name
                for row in r:
                    try:
                        keys = (f"{row[5]}/{row[2]}", f"{row[5]}/{normalize_city_name(row[5], row[2])}")
                    except IndexError:
                        continue
                    for key in keys:
                        if key in want and key not in found:
                            found[key] = (row[3], float(row[0]), float(row[1]))
        for key, name in want.items():
            cc = key.split("/", 1)[0]
            a1, lat, lon = found.get(key, (None, None, None))
            prov = admin1.get(f"{cc}/{a1}", a1) if a1 else None
            if cc in countries:
                self._add(countries[cc], prov or name, name, cc=cc, lat=lat, lon=lon)

    def _load_china(self) -> None:
        try:
            import GeoToolCN
            tree = GeoToolCN.get_administrative_tree()
        except Exception:
            return
        self._add("中国", cc="CN")

        def walk(nodes, path):
            for n in nodes or []:
                # GeoToolCN 的节点格式：{"value": 行政区划代码, "label": 名称, "children": [...]}
                if isinstance(n, dict):
                    name = n.get("label") or n.get("name")
                    kids = n.get("children")
                else:
                    name = getattr(n, "label", None) or getattr(n, "name", None)
                    kids = getattr(n, "children", None)
                if not name:
                    continue
                p = path + [name]
                if len(p) <= 3:
                    prov = p[0]
                    city = p[1] if len(p) > 1 else None
                    dist = p[2] if len(p) > 2 else None
                    self._add("中国", prov, city, dist, cc="CN")
                walk(kids, p)

        if isinstance(tree, dict) and "children" in tree:
            walk(tree["children"], [])
        elif isinstance(tree, list):
            walk(tree, [])

    # ---- 查询 ------------------------------------------------------------
    @staticmethod
    def label(e: dict[str, Any]) -> str:
        parts = []
        for k in LEVELS:
            v = e.get(k)
            if v and (not parts or parts[-1] != v):
                parts.append(v)
        return "/".join(parts)

    def suggestions(self) -> list[str]:
        return sorted({self.label(e) for e in self.entries}, key=lambda s: (s.count("/"), s))

    def resolve(self, text: str) -> dict[str, Any] | None:
        """把用户输入解析成标准地点；无法匹配时按字面层级保存。"""
        toks = [t for t in _SPLIT.split((text or "").strip()) if t]
        if not toks:
            return None
        best = None
        for e in self.entries:
            comps = [e.get(k) for k in LEVELS]
            score = self._match(toks, comps)
            if score is None:
                continue
            depth = sum(1 for c in comps if c)
            cand = (score, -depth if score >= 100 else depth, e)
            if best is None or cand[:2] > best[:2]:
                best = cand
        if best is not None:
            e = dict(best[2])
            e["label"] = self.label(e)
            return e
        # 字面层级：国家/省/市/区县；只有两级时把第二级同时当作省和市（如“日本/箱根”）
        lv = list(toks[:4])
        if len(lv) == 1:
            # 只有一个不认识的地名：无法确定国家，交给界面提示用户写成“国家/地名”
            return {"country": None, "province": None, "city": lv[0], "district": None, "cc": None,
                    "lat": None, "lon": None, "label": lv[0], "needs_country": True}
        place = {"country": lv[0], "province": lv[1] if len(lv) > 1 else None,
                 "city": lv[2] if len(lv) > 2 else (lv[1] if len(lv) == 2 else None),
                 "district": lv[3] if len(lv) > 3 else None, "cc": None, "lat": None, "lon": None}
        place["label"] = self.label(place)
        return place

    @staticmethod
    def _match(toks: list[str], comps: list[str | None]) -> int | None:
        """所有输入词都要按顺序命中某一层级；最后一个词命中的层级 = 用户想要的精度。"""
        j = 0
        exact = 0
        last_level = -1
        for t in toks:
            nt, ct = _norm(t), _core(t)
            hit = next((k for k in range(j, 4) if comps[k] and _norm(comps[k]) == nt), None)
            if hit is not None:
                exact += 1
            else:
                hit = next((k for k in range(j, 4) if comps[k] and _core(comps[k]) == ct), None)
            if hit is None:
                return None
            j = hit + 1
            last_level = hit
        # 最后一个词之后不应再有更细的层级（“日本/东京” 不应匹配到某个区）
        deeper = any(comps[k] for k in range(last_level + 1, 4)
                     if comps[k] and comps[k] != comps[last_level])
        if deeper:
            return None
        return 100 + exact * 10 + len(toks)
