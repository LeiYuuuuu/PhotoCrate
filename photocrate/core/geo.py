"""离线逆地理编码：GPS 坐标 → 国家 / 省 / 市 / 区县。

- 中国境内（含港澳台）：GeoToolCN，按真实行政边界判定，返回中文名。
- 中国境外：GeoNames 人口 >1000 城市点（data/geonames_cities1000.csv.gz，CC BY 4.0），
  取最近的城市；国家名和常见地名用 data/place_names_zh.json 翻成中文，其余保留英文。
- 坐标系：EXIF 默认是 WGS-84。若某批照片由在大陆写入 GCJ-02 的 App 生成，
  可在设置中开启"坐标为 GCJ-02"，查询前先转换回 WGS-84。
"""
from __future__ import annotations

import csv
import gzip
import json
import logging
import math
import threading
from pathlib import Path
from typing import Any

from ..config import find_resource

log = logging.getLogger(__name__)

CHINA_CODES = {"CN", "HK", "MO", "TW"}
_JP_SUFFIXES = ("-shi", " Shi", "-cho", " Cho", " Town")


def normalize_city_name(cc: str, name: str | None) -> str | None:
    """GeoNames 的日本地名常带“-shi / -cho”后缀（Osaka-shi、Nara-shi），去掉后才能和中文对照表对上。"""
    if not name or cc != "JP":
        return name
    for suf in _JP_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf) + 1:
            return name[: -len(suf)]
    return name


class _WorldIndex:
    def __init__(self, path: Path):
        import numpy as np

        lats, lons, self.names, self.admin1, self.admin2, self.cc = [], [], [], [], [], []
        pops = []
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                try:
                    lats.append(float(row[0]))
                    lons.append(float(row[1]))
                except (ValueError, IndexError):
                    continue
                self.names.append(row[2])
                self.admin1.append(row[3])
                self.admin2.append(row[4])
                self.cc.append(row[5])
                try:
                    pops.append(int(row[6]))
                except (ValueError, IndexError):
                    pops.append(0)
        la = np.radians(np.asarray(lats, dtype=np.float64))
        lo = np.radians(np.asarray(lons, dtype=np.float64))
        self.xyz = np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=1).astype(np.float32)
        self.logpop = np.log10(np.maximum(np.asarray(pops, dtype=np.float32), 1000.0))
        self._np = np

    def nearest(self, lat: float, lon: float) -> tuple[int, float]:
        """最近的城市点——但附近有明显更大的城市时取大城市。

        GeoNames 只有城市中心点，大城市的市区里常常离某个卫星城的中心点更近（东京浅草 → 草加 / 浦安，
        伏见稻荷 → 向日）。所以在“最近距离 + 12 km、且不超过 30 km”的同国候选里，按
        距离 − 4 × log10(人口) 取最小（人口差 10 倍 ≈ 抵消 4 km 距离差）。"""
        np = self._np
        la, lo = math.radians(lat), math.radians(lon)
        q = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)], dtype=np.float32)
        dots = self.xyz @ q
        k = min(64, len(dots))
        idx = np.argpartition(-dots, k - 1)[:k]
        dist = np.degrees(np.arccos(np.clip(dots[idx].astype(np.float64), -1.0, 1.0))) * 111.195
        j0 = int(np.argmin(dist))
        i0, d0 = int(idx[j0]), float(dist[j0])
        best, best_score = i0, d0 - 4.0 * float(self.logpop[i0])
        for j in range(k):
            i, d = int(idx[j]), float(dist[j])
            if i == i0 or d > d0 + 12 or d > 30 or self.cc[i] != self.cc[i0]:
                continue
            sc = d - 4.0 * float(self.logpop[i])
            if sc < best_score:
                best, best_score = i, sc
        return best, float(dist[list(idx).index(best)])


class GeoService:
    def __init__(self, assume_gcj02: bool = False):
        self.assume_gcj02 = assume_gcj02
        self._lock = threading.Lock()
        self._world: _WorldIndex | None = None
        self._world_failed = False
        self._cn = None
        self._cn_failed = False
        self._cache: dict[tuple[float, float], dict[str, Any]] = {}
        self._zh = self._load_zh()

    @staticmethod
    def _load_zh() -> dict[str, dict[str, str]]:
        p = find_resource("data", "place_names_zh.json")
        if not p:
            return {"countries": {}, "admin1": {}, "cities": {}}
        try:
            d = json.loads(p.read_text("utf-8"))
            return {k: d.get(k, {}) for k in ("countries", "admin1", "cities")}
        except Exception as e:
            log.warning("读取地名对照表失败：%s", e)
            return {"countries": {}, "admin1": {}, "cities": {}}

    # ---- 懒加载 ----------------------------------------------------------
    def _china(self):
        if self._cn is None and not self._cn_failed:
            try:
                import GeoToolCN
                self._cn = GeoToolCN
            except Exception as e:
                log.warning("GeoToolCN 不可用，国内地点只能精确到国家：%s", e)
                self._cn_failed = True
        return self._cn

    def _world_index(self) -> _WorldIndex | None:
        if self._world is None and not self._world_failed:
            p = find_resource("data", "geonames_cities1000.csv.gz")
            if p is None:
                self._world_failed = True
                log.warning("缺少 data/geonames_cities1000.csv.gz，海外地点无法识别")
                return None
            try:
                self._world = _WorldIndex(p)
            except Exception as e:
                self._world_failed = True
                log.warning("加载 GeoNames 数据失败：%s", e)
        return self._world

    # ---- 查询 ------------------------------------------------------------
    def lookup(self, lat: float | None, lon: float | None) -> dict[str, Any] | None:
        if lat is None or lon is None:
            return None
        key = (round(lat, 4), round(lon, 4))
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                return hit
            res = self._lookup(lat, lon)
            self._cache[key] = res
            return res

    def _city_zh(self, cc: str, name: str | None) -> str | None:
        if not name:
            return None
        cities = self._zh["cities"]
        norm = normalize_city_name(cc, name)
        return cities.get(f"{cc}/{name}") or cities.get(f"{cc}/{norm}") or norm

    def _lookup(self, lat: float, lon: float) -> dict[str, Any]:
        cn = self._china()
        if cn is not None:
            qlat, qlon = lat, lon
            if self.assume_gcj02:
                # GeoToolCN 的坐标转换函数参数顺序是 (lng, lat)
                qlon, qlat = cn.gcj02_to_wgs84(lon, lat)
            try:
                r = cn.reverse(qlat, qlon)
            except Exception:
                r = None
            if r is not None and r.province is not None:
                prov = r.province.name
                city = r.city.name if r.city else None
                dist = r.district.name if r.district else None
                return {
                    "cc": "CN", "country": "中国", "province": prov, "city": city,
                    "district": dist, "adcode": (r.district or r.city or r.province).code,
                    "in_china": True,
                }
        w = self._world_index()
        if w is None:
            return {"cc": None, "country": None, "province": None, "city": None, "district": None, "in_china": False}
        i, dist_km = w.nearest(lat, lon)
        cc = w.cc[i]
        if cc in CHINA_CODES and cn is not None:
            # 落在中国边界外但最近城市在国内（如近海），只给到国家
            return {"cc": "CN", "country": "中国", "province": None, "city": None, "district": None,
                    "in_china": True, "far_km": round(dist_km, 1)}
        countries = self._zh["countries"]
        admin1 = w.admin1[i] or None
        name = w.names[i] or None
        district = None
        admin2 = w.admin2[i] or ""
        if cc == "JP" and name and name.endswith("-ku") and admin2.endswith(("-shi", " Shi")):
            # 政令指定都市的区（Kamigyo-ku，所属 Kyoto-shi）：市取所属城市，区作为区县
            district, name = name, admin2
        res = {
            "cc": cc,
            "country": countries.get(cc, cc),
            "province": self._zh["admin1"].get(f"{cc}/{admin1}", admin1) if admin1 else None,
            "city": self._city_zh(cc, name),
            "district": self._city_zh(cc, district),
            "in_china": False,
            "far_km": round(dist_km, 1),
        }
        # 离最近城市超过 80 km（海上、荒野），城市级别不可靠
        if dist_km > 80:
            res["city"] = None
        return res
