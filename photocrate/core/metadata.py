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

import json
import logging
import math
import os
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import app_dir, find_resource

log = logging.getLogger(__name__)

# 只读取需要的标签可显著提速；"#" 表示该标签输出数值而非可读文本
EXIFTOOL_TAGS = [
    "DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal", "CreateDate", "DateCreated",
    "GPSLatitude#", "GPSLongitude#", "GPSAltitude#", "GPSDateTime",
    "Make", "Model", "LensModel", "LensID", "Lens",
    "FocalLength#", "FocalLengthIn35mmFormat#", "ISO#", "FNumber#", "ExposureTime#",
    "ImageWidth#", "ImageHeight#", "ImageSize#", "Orientation#",
    "Software", "UserComment", "MIMEType", "Rating#", "Label",
    # 视频：带时区的拍摄时间（苹果 CreationDate、索尼 XML 的 CreationDateValue）、时长、QuickTime GPS、索尼机型
    "CreationDate", "CreationDateValue", "Duration#", "GPSCoordinates#", "DeviceManufacturer", "DeviceModelName",
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
    if lat is None and g("GPSCoordinates"):
        parts = str(g("GPSCoordinates")).replace(",", " ").split()
        if len(parts) >= 2:
            lat, lon = _clean_gps(parts[0], parts[1])
    mime = _text(g("MIMEType")) or ""
    video = mime.startswith("video/")
    dt_orig = _text(g("DateTimeOriginal"))
    offset = _text(g("OffsetTimeOriginal"))
    if video and not dt_orig:
        # 视频优先用带时区的本地拍摄时间；没有时 CreateDate 是 UTC（ExifTool 已按电脑时区换算，之后再按同批照片校正）
        for k in ("CreationDate", "CreationDateValue"):
            t = _text(g(k))
            if t:
                dt_orig = t
                break
    lens = None
    for k in ("LensModel", "LensID", "Lens"):
        t = _text(g(k))
        if t and not t.replace(".", "").isdigit():
            lens = t
            break
    sub = _text(g("SubSecTimeOriginal"))
    return {
        "dt_original": dt_orig,
        "subsec": sub,
        "offset": offset,
        "dt_create": _text(g("CreateDate")),
        "dt_xmp": _text(g("DateCreated")),
        "lat": lat, "lon": lon, "alt": _num(g("GPSAltitude")),
        "gps_utc": _text(g("GPSDateTime")) if lat is not None else None,
        "make": _text(g("Make")) or _text(g("DeviceManufacturer")),
        "model": _text(g("Model")) or _text(g("DeviceModelName")),
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
        "rating": _rating(g("Rating")),
        "label": _text(g("Label")),
        "video": video,
        "duration": _num(g("Duration")) if video else None,
        "qt_utc": bool(video and not dt_orig),        # 时间来自 QuickTime CreateDate（UTC 换算到电脑时区）
    }


def _rating(v: Any) -> int | None:
    """XMP / EXIF 星级：-1 = 拒绝（Bridge），0 = 未评级，1–5 星。"""
    n = _num(v)
    if n is None:
        return None
    n = int(round(n))
    return n if -1 <= n <= 5 else None


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


class StayOpen:
    """ExifTool 常驻进程，只用来批量读元数据（写 XMP 一次只改一个文件，仍用 PyExifTool）。

    PyExifTool 每批先把 stdout 读完、再读 stderr。一批里有几十个读不了的文件（损坏 / 全是 0 的 RAW、
    读的时候被删掉）时，每个文件一行的错误信息会写满 stderr 管道（Windows 只有 4 KB）：ExifTool 卡在写 stderr，
    这边卡在等 stdout，扫描永远停住。这里由后台线程一直读 stderr；每批还有超时，超时就结束进程（下一批自动重启）。"""

    def __init__(self, executable: str, common_args: list[str]):
        if not (os.path.isfile(executable) or shutil.which(executable)):
            raise FileNotFoundError(executable)
        self._args = [executable, "-stay_open", "True", "-@", "-", "-common_args", *common_args]
        self._p: subprocess.Popen | None = None
        self._n = 0
        self._lock = threading.Lock()
        self.errors: deque[str] = deque(maxlen=20)         # 最近的错误信息（排查用）

    def _start(self) -> subprocess.Popen:
        if self._p is None or self._p.poll() is not None:
            self._p = subprocess.Popen(self._args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, **_hidden_startupinfo())
            threading.Thread(target=self._drain, args=(self._p.stderr,), daemon=True).start()
        return self._p

    def _drain(self, pipe) -> None:
        try:
            for line in pipe:
                self.errors.append(line.decode("utf-8", "replace").rstrip())
        except (OSError, ValueError):
            pass

    def execute_json(self, args: list[str], timeout: float) -> list[dict[str, Any]]:
        with self._lock:
            p = self._start()
            self._n += 1
            end = f"{{ready{self._n}}}".encode()
            buf = bytearray()
            timer = threading.Timer(timeout, p.kill)        # 卡住时结束进程，下面的读取随之返回
            timer.start()
            ok = False
            try:
                p.stdin.write(("\n".join([*args, f"-execute{self._n}"]) + "\n").encode("utf-8"))
                p.stdin.flush()
                while True:
                    tail = bytes(buf[-(len(end) + 2):])
                    if tail.endswith(b"\n") and tail.rstrip(b"\r\n").endswith(end):   # JSON 字符串里不会有真的换行
                        break
                    chunk = p.stdout.read1(1 << 16)
                    if not chunk:
                        raise IOError("ExifTool 没有响应（超时或已退出）")
                    buf += chunk
                ok = True
            finally:
                timer.cancel()
                if not ok:
                    self._stop(p)
        text = bytes(buf).rstrip()[:-len(end)].decode("utf-8", "replace").strip()
        return json.loads(text) if text else []

    def _stop(self, p: subprocess.Popen) -> None:
        try:
            p.kill()
            p.wait(5)
        except Exception:
            pass
        for f in (p.stdin, p.stdout):               # 进程没了，没写完的命令也不用再写（stderr 由读取线程读到结尾）
            try:
                f.close()
            except OSError:
                pass
        if self._p is p:
            self._p = None

    def close(self) -> None:
        p = self._p
        if p is None:
            return
        try:
            if p.poll() is None:
                p.stdin.write(b"-stay_open\nFalse\n")
                p.stdin.flush()
                p.wait(5)
        except Exception:
            pass
        if p.poll() is None:
            self._stop(p)
        self._p = None


class ExifToolBackend(MetadataBackend):
    name = "exiftool"
    BATCH = 150

    def __init__(self, executable: str):
        self.executable = executable
        self._et = StayOpen(
            executable,
            # -charset filename=utf8：Windows 下中文文件名必需
            # QuickTimeUTC：HEIC/MOV 的 QuickTime 时间按 UTC 换算为本地时间
            ["-charset", "filename=utf8", "-api", "QuickTimeUTC=1", "-fast", "-m"],
        )

    def _read_batch(self, batch: list[str]) -> list[dict[str, Any]]:
        # 超时按文件数放宽（网络盘上的大 RAW 也够用）；超时后整批改为逐个读取
        return self._et.execute_json(["-j", *[f"-{t}" for t in EXIFTOOL_TAGS], *batch], timeout=60 + 2 * len(batch))

    def read_many(self, paths, progress=None, cancel=None):
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(paths), self.BATCH):
            if cancel is not None and cancel.is_set():
                break
            # 常驻进程按“一行一个参数”读命令：文件名里的换行会变成额外的 ExifTool 参数（-if 能执行 Perl 代码），
            # 这类文件（只有 Linux / macOS 上可能出现）不交给 ExifTool
            chunk = paths[i:i + self.BATCH]
            batch = [p for p in chunk if not any(ord(c) < 32 for c in p)]
            if len(batch) < len(chunk):
                log.warning("跳过文件名含控制字符的文件（不交给 ExifTool）")
            try:
                results = self._read_batch(batch) if batch else []
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
                progress(len(chunk))
        return out

    def close(self) -> None:
        self._et.close()


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
        gps_utc = None
        try:
            if lat is not None and gps.get(29) and gps.get(7):
                h, mi, se = (float(x) for x in gps[7])
                gps_utc = f"{str(gps[29]).strip()} {int(h):02d}:{int(mi):02d}:{int(se):02d}Z"
        except Exception:
            gps_utc = None
        return {
            "dt_original": _text(sub.get(36867)), "subsec": _text(sub.get(37521)), "gps_utc": gps_utc,
            "offset": _text(sub.get(36881)), "dt_create": _text(sub.get(36868) or ex.get(306)),
            "dt_xmp": None, "lat": lat, "lon": lon, "alt": None,
            "make": _text(ex.get(271)), "model": _text(ex.get(272)),
            "lens": _text(sub.get(42036)),
            "focal": _num(sub.get(37386)), "focal35": _num(sub.get(41989)),
            "iso": _num(sub.get(34855)), "fnumber": _num(sub.get(33437)),
            "exposure": _num(sub.get(33434)),
            "width": w, "height": h, "orientation": int(_num(ex.get(274)) or 1),
            "software": _text(ex.get(305)), "user_comment": None, "mime": None,
            "rating": _rating(ex.get(18246)), "label": None,
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


def merge_group_meta(images: list[dict[str, Any]], sidecars: list[dict[str, Any]]) -> dict[str, Any]:
    """图片在前、.xmp 在后合并；但星级 / 色标以 .xmp 为准（Lightroom、Bridge 把评级写在附属文件里，
    相机写在 JPG/RAW 里的 Rating 通常只是 0）。"""
    out = merge_meta(list(images) + list(sidecars))
    for key in ("rating", "label"):
        for d in sidecars:
            if d.get(key) not in (None, ""):
                out[key] = d[key]
                break
    return out


def merge_meta(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """合并一个文件组内多个文件的元数据：逐字段取第一个非空值（主图优先）。"""
    out: dict[str, Any] = {}
    for d in items:
        for k, v in d.items():
            if out.get(k) in (None, "") and v not in (None, ""):
                out[k] = v
    return out
