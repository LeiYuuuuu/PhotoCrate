"""元数据读取。

主力：ExifTool（常驻进程批量读取，覆盖几乎所有相机 RAW / HEIC / JPG / PNG）。
备用：找不到 ExifTool 时用 Pillow + exifread，常见格式可用，部分 RAW（CR3/RAF 等）读不全。

所有后端输出统一的"归一化元数据"字典：
    dt_original, dt_create, dt_xmp      拍摄/创建时间原始字符串
    offset                              时区偏移字符串（如 +08:00）
    lat, lon, alt                       十进制坐标（WGS-84，南纬/西经为负）
    make, model, lens                   相机品牌、型号、镜头
    focal, focal35, iso, fnumber, exposure
    width, height, orientation
    software, user_comment, mime
"""
from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import app_dir, find_resource

log = logging.getLogger(__name__)

# 只读取需要的标签可显著提速；"#" 表示该标签输出数值而非可读文本
EXIFTOOL_TAGS = [
    "DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal", "CreateDate", "DateCreated",
    "GPSLatitude#", "GPSLongitude#", "GPSAltitude#",
    "Make", "Model", "LensModel", "LensID", "Lens",
    "FocalLength#", "FocalLengthIn35mmFormat#", "ISO#", "FNumber#", "ExposureTime#",
    "ImageWidth#", "ImageHeight#", "ImageSize#", "Orientation#",
    "Software", "UserComment", "MIMEType",
]


# ---------------------------------------------------------------------------
# ExifTool 查找
# ---------------------------------------------------------------------------

def find_exiftool(configured: str = "") -> str | None:
    cands: list[Path] = []
    if configured:
        cands.append(Path(configured))
    exe = "exiftool.exe" if os.name == "nt" else "exiftool"
    for rel in (("tools", "exiftool", exe), ("tools", exe), (exe,)):
        p = find_resource(*rel)
        if p:
            cands.append(p)
    cands.append(app_dir() / "tools" / "exiftool" / exe)
    for c in cands:
        if c and c.is_file():
            return str(c)
    which = shutil.which("exiftool")
    return which


def _hidden_startupinfo():
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return {"startupinfo": si, "creationflags": 0x08000000}  # CREATE_NO_WINDOW


def exiftool_version(path: str) -> str | None:
    try:
        out = subprocess.run([path, "-ver"], capture_output=True, timeout=30, **_hidden_startupinfo())
        return out.stdout.decode("utf-8", "replace").strip() or None
    except Exception as e:
        log.warning("ExifTool 无法运行：%s", e)
        return None


def exiftool_extract_preview(exiftool: str, path: str) -> bytes | None:
    """用 ExifTool 从 RAW 中提取内嵌预览图（rawpy 取不到时的回退）。"""
    for tag in ("-PreviewImage", "-JpgFromRaw", "-OtherImage", "-ThumbnailImage"):
        try:
            out = subprocess.run(
                [exiftool, "-b", tag, "-charset", "filename=utf8", path],
                capture_output=True, timeout=60, **_hidden_startupinfo(),
            )
            if out.returncode == 0 and len(out.stdout) > 1024:
                return out.stdout
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# 值的清洗
# ---------------------------------------------------------------------------

def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        s = str(v).strip()
        if not s:
            return None
        if "/" in s:
            try:
                a, b = s.split("/", 1)
                f = float(a) / float(b)
            except (ValueError, ZeroDivisionError):
                return None
        else:
            try:
                f = float(s.split()[0])
            except ValueError:
                return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _text(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).replace("\x00", "").strip()
    if not s or s.lower() in ("unknown", "n/a", "none", "----", "-", "0"):
        return None
    return s


def _clean_gps(lat: Any, lon: Any) -> tuple[float | None, float | None]:
    la, lo = _num(lat), _num(lon)
    if la is None or lo is None:
        return None, None
    if not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None, None
    if abs(la) < 1e-6 and abs(lo) < 1e-6:   # 0,0 通常是无效坐标
        return None, None
    return la, lo


def normalize_exiftool(d: dict[str, Any]) -> dict[str, Any]:
    def g(k: str) -> Any:
        return d.get(k)

    w = _num(g("ImageWidth"))
    h = _num(g("ImageHeight"))
    size = g("ImageSize")
    if size:
        parts = str(size).replace("x", " ").split()
        if len(parts) == 2:
            sw, sh = _num(parts[0]), _num(parts[1])
            if sw and sh and sw > 16 and sh > 16:
                w, h = sw, sh
    lat, lon = _clean_gps(g("GPSLatitude"), g("GPSLongitude"))
    lens = None
    for k in ("LensModel", "LensID", "Lens"):
        t = _text(g(k))
        if t and not t.replace(".", "").isdigit():
            lens = t
            break
    sub = _text(g("SubSecTimeOriginal"))
    return {
        "dt_original": _text(g("DateTimeOriginal")),
        "subsec": sub,
        "offset": _text(g("OffsetTimeOriginal")),
        "dt_create": _text(g("CreateDate")),
        "dt_xmp": _text(g("DateCreated")),
        "lat": lat, "lon": lon, "alt": _num(g("GPSAltitude")),
        "make": _text(g("Make")),
        "model": _text(g("Model")),
        "lens": lens,
        "focal": _num(g("FocalLength")),
        "focal35": _num(g("FocalLengthIn35mmFormat")),
        "iso": _num(g("ISO")),
        "fnumber": _num(g("FNumber")),
        "exposure": _num(g("ExposureTime")),
        "width": int(w) if w else None,
        "height": int(h) if h else None,
        "orientation": int(_num(g("Orientation")) or 1),
        "software": _text(g("Software")),
        "user_comment": _text(g("UserComment")),
        "mime": _text(g("MIMEType")),
    }


# ---------------------------------------------------------------------------
# 后端
# ---------------------------------------------------------------------------

class MetadataBackend:
    name = "base"

    def read_many(self, paths: list[str], progress: Callable[[int], None] | None = None,
                  cancel: threading.Event | None = None) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class ExifToolBackend(MetadataBackend):
    name = "exiftool"
    BATCH = 150

    def __init__(self, executable: str):
        import exiftool  # PyExifTool

        self.executable = executable
        self._et = exiftool.ExifToolHelper(
            executable=executable,
            encoding="utf-8",
            # -charset filename=utf8：Windows 下中文文件名必需
            # QuickTimeUTC：HEIC/MOV 的 QuickTime 时间按 UTC 换算为本地时间
            common_args=["-charset", "filename=utf8", "-api", "QuickTimeUTC=1", "-fast", "-m"],
            check_execute=False,
        )

    def _read_batch(self, batch: list[str]) -> list[dict[str, Any]]:
        return self._et.get_tags(batch, tags=EXIFTOOL_TAGS)

    def read_many(self, paths, progress=None, cancel=None):
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(paths), self.BATCH):
            if cancel is not None and cancel.is_set():
                break
            batch = paths[i:i + self.BATCH]
            try:
                results = self._read_batch(batch)
            except Exception as e:
                log.warning("ExifTool 批量读取失败，逐个重试：%s", e)
                results = []
                for p in batch:
                    try:
                        results.extend(self._read_batch([p]))
                    except Exception as e2:
                        log.warning("ExifTool 读取失败 %s: %s", p, e2)
            by_src = {}
            for r in results:
                src = r.get("SourceFile")
                if src:
                    by_src[os.path.normcase(os.path.normpath(src))] = r
            for p in batch:
                r = by_src.get(os.path.normcase(os.path.normpath(p)))
                if r is not None:
                    out[p] = normalize_exiftool(r)
            if progress:
                progress(len(batch))
        return out

    def close(self) -> None:
        try:
            self._et.terminate()
        except Exception:
            pass


class FallbackBackend(MetadataBackend):
    """没有 ExifTool 时：Pillow（JPG/HEIC/PNG/TIFF/WebP）+ exifread（TIFF 系 RAW）。"""
    name = "builtin"

    def __init__(self):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass

    def read_many(self, paths, progress=None, cancel=None):
        out = {}
        for p in paths:
            if cancel is not None and cancel.is_set():
                break
            try:
                out[p] = self.read_one(p)
            except Exception as e:
                log.debug("内置元数据读取失败 %s: %s", p, e)
            if progress:
                progress(1)
        return out

    def read_one(self, path: str) -> dict[str, Any]:
        from .formats import file_kind
        kind = file_kind(path)
        if kind == "raw":
            r = self._exifread(path)
            if r:
                return r
        return self._pillow(path) or self._exifread(path) or {}

    @staticmethod
    def _dms(v) -> float | None:
        try:
            vals = [float(x) for x in v]
            return vals[0] + vals[1] / 60 + vals[2] / 3600
        except Exception:
            return None

    def _pillow(self, path: str) -> dict[str, Any] | None:
        from PIL import Image
        with Image.open(path) as im:
            ex = im.getexif()
            w, h = im.size
        if not ex and not (w and h):
            return None
        sub = ex.get_ifd(0x8769) if ex else {}
        gps = ex.get_ifd(0x8825) if ex else {}
        lat = lon = None
        if gps and 2 in gps and 4 in gps:
            lat = self._dms(gps[2])
            lon = self._dms(gps[4])
            if lat is not None and str(gps.get(1, "N")).upper().startswith("S"):
                lat = -lat
            if lon is not None and str(gps.get(3, "E")).upper().startswith("W"):
                lon = -lon
        lat, lon = _clean_gps(lat, lon)
        return {
            "dt_original": _text(sub.get(36867)), "subsec": _text(sub.get(37521)),
            "offset": _text(sub.get(36881)), "dt_create": _text(sub.get(36868) or ex.get(306)),
            "dt_xmp": None, "lat": lat, "lon": lon, "alt": None,
            "make": _text(ex.get(271)), "model": _text(ex.get(272)),
            "lens": _text(sub.get(42036)),
            "focal": _num(sub.get(37386)), "focal35": _num(sub.get(41989)),
            "iso": _num(sub.get(34855)), "fnumber": _num(sub.get(33437)),
            "exposure": _num(sub.get(33434)),
            "width": w, "height": h, "orientation": int(_num(ex.get(274)) or 1),
            "software": _text(ex.get(305)), "user_comment": None, "mime": None,
        }

    def _exifread(self, path: str) -> dict[str, Any] | None:
        import exifread
        with open(path, "rb") as fh:
            t = exifread.process_file(fh, details=False)
        if not t:
            return None

        def tv(k):
            v = t.get(k)
            return None if v is None else v.printable

        def tn(k):
            v = t.get(k)
            if v is None:
                return None
            vals = v.values
            if isinstance(vals, list) and vals:
                x = vals[0]
                try:
                    return float(x.num) / float(x.den) if hasattr(x, "num") else float(x)
                except Exception:
                    return None
            return _num(vals)

        lat = lon = None
        if "GPS GPSLatitude" in t and "GPS GPSLongitude" in t:
            def dms(tag):
                try:
                    return sum(float(x.num) / float(x.den) / (60 ** i) for i, x in enumerate(t[tag].values))
                except Exception:
                    return None
            lat, lon = dms("GPS GPSLatitude"), dms("GPS GPSLongitude")
            if lat is not None and str(tv("GPS GPSLatitudeRef") or "N").upper().startswith("S"):
                lat = -lat
            if lon is not None and str(tv("GPS GPSLongitudeRef") or "E").upper().startswith("W"):
                lon = -lon
        lat, lon = _clean_gps(lat, lon)
        w = tn("EXIF ExifImageWidth") or tn("Image ImageWidth")
        h = tn("EXIF ExifImageLength") or tn("Image ImageLength")
        return {
            "dt_original": _text(tv("EXIF DateTimeOriginal")), "subsec": _text(tv("EXIF SubSecTimeOriginal")),
            "offset": _text(tv("EXIF OffsetTimeOriginal")),
            "dt_create": _text(tv("EXIF DateTimeDigitized") or tv("Image DateTime")),
            "dt_xmp": None, "lat": lat, "lon": lon, "alt": None,
            "make": _text(tv("Image Make")), "model": _text(tv("Image Model")),
            "lens": _text(tv("EXIF LensModel")),
            "focal": tn("EXIF FocalLength"), "focal35": tn("EXIF FocalLengthIn35mmFilm"),
            "iso": tn("EXIF ISOSpeedRatings"), "fnumber": tn("EXIF FNumber"),
            "exposure": tn("EXIF ExposureTime"),
            "width": int(w) if w else None, "height": int(h) if h else None,
            "orientation": int(tn("Image Orientation") or 1),
            "software": _text(tv("Image Software")), "user_comment": None, "mime": None,
        }


def create_backend(exiftool_path: str | None) -> MetadataBackend:
    if exiftool_path:
        try:
            return ExifToolBackend(exiftool_path)
        except Exception as e:
            log.warning("ExifTool 初始化失败，改用内置读取：%s", e)
    return FallbackBackend()


def merge_meta(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """合并一个文件组内多个文件的元数据：逐字段取第一个非空值（主图优先）。"""
    out: dict[str, Any] = {}
    for d in items:
        for k, v in d.items():
            if out.get(k) in (None, "") and v not in (None, ""):
                out[k] = v
    return out
