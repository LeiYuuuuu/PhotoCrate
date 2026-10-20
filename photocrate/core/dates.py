"""拍摄日期的解析与推断。

优先级：
1. EXIF DateTimeOriginal（相机记录的拍摄时间，本地时间）
2. EXIF/QuickTime CreateDate、XMP DateCreated
3. 文件名中的日期（微信、截图、手机相机命名规则，可在设置里编辑）
4. 文件修改时间（标记为"推测"）
注意：不使用 Windows 的"创建时间"——复制文件后它会变成复制当天。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

MIN_DATE = datetime(1990, 1, 1)

# 每条规则：name 名称、regex 正则（命名分组 y m d H M S，或 ts13 毫秒时间戳 / ts10 秒时间戳）
DEFAULT_FILENAME_PATTERNS: list[dict[str, str]] = [
    {"name": "微信 mmexport 毫秒时间戳", "regex": r"mmexport(?P<ts13>\d{13})"},
    {"name": "微信相机 wx_camera", "regex": r"wx_camera_(?P<ts13>\d{13})"},
    {"name": "紧凑日期时间 20231001_123045", "regex": r"(?<!\d)(?P<y>(?:19|20)\d{2})(?P<m>[01]\d)(?P<d>[0-3]\d)[_\-\sT]?(?P<H>[0-2]\d)(?P<M>[0-5]\d)(?P<S>[0-5]\d)"},
    {"name": "分隔日期时间 2023-10-01 12.30.45", "regex": r"(?<!\d)(?P<y>(?:19|20)\d{2})[-_.年](?P<m>[01]?\d)[-_.月](?P<d>[0-3]?\d)日?(?:[\s_\-T,]+(?:at\s*)?(?P<H>[0-2]?\d)[-_.:时 ]?(?P<M>[0-5]\d)[-_.:分 ]?(?P<S>[0-5]\d))?"},
    {"name": "13 位毫秒时间戳", "regex": r"(?<!\d)(?P<ts13>1[2-9]\d{11})(?!\d)"},
    {"name": "紧凑日期 20231001", "regex": r"(?<!\d)(?P<y>(?:19|20)\d{2})(?P<m>[01]\d)(?P<d>[0-3]\d)(?!\d)"},
    {"name": "10 位秒时间戳", "regex": r"(?<!\d)(?P<ts10>1[2-9]\d{8})(?!\d)"},
]

_EXIF_RE = re.compile(
    r"^\s*(\d{4})[:\-/](\d{1,2})[:\-/](\d{1,2})"
    r"(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?(?:\.\d+)?)?"
    r"\s*(Z|[+\-]\d{2}:?\d{2})?"
)


def _valid(dt: datetime | None) -> bool:
    if dt is None:
        return False
    now = datetime.now() + timedelta(days=2)
    return MIN_DATE <= dt <= now


def parse_exif_datetime(s: str | None) -> tuple[datetime | None, str | None]:
    """解析 '2023:10:01 12:30:45+08:00' 之类的字符串，返回（本地墙上时间, 时区字符串）。"""
    if not s:
        return None, None
    m = _EXIF_RE.match(str(s))
    if not m:
        return None, None
    y, mo, d, H, M, S, tz = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(H or 0), int(M or 0), int(S or 0))
    except ValueError:
        return None, None
    if not _valid(dt):
        return None, None
    return dt, tz


def compile_patterns(patterns: list[dict[str, str]] | None) -> list[tuple[str, re.Pattern]]:
    out = []
    for p in patterns or DEFAULT_FILENAME_PATTERNS:
        try:
            out.append((p.get("name", ""), re.compile(p["regex"], re.IGNORECASE)))
        except (re.error, KeyError):
            continue
    return out


def parse_filename_date(name: str, compiled: list[tuple[str, re.Pattern]]) -> tuple[datetime | None, str | None]:
    """从文件名推断日期，返回（日期, 命中的规则名）。"""
    for rule_name, rx in compiled:
        for m in rx.finditer(name):
            gd = m.groupdict()
            dt = None
            try:
                if gd.get("ts13"):
                    dt = datetime.fromtimestamp(int(gd["ts13"]) / 1000)
                elif gd.get("ts10"):
                    dt = datetime.fromtimestamp(int(gd["ts10"]))
                elif gd.get("y"):
                    dt = datetime(int(gd["y"]), int(gd["m"]), int(gd["d"]),
                                  int(gd.get("H") or 0), int(gd.get("M") or 0), int(gd.get("S") or 0))
            except (ValueError, OSError, OverflowError):
                dt = None
            if _valid(dt):
                return dt, rule_name
    return None, None


def resolve_date(meta: dict[str, Any], filename: str, mtime_ns: int | None,
                 compiled: list[tuple[str, re.Pattern]], use_filename: bool = True,
                 use_mtime: bool = True) -> dict[str, Any]:
    """按优先级确定拍摄日期。返回 {dt, source, detail, tz}。"""
    for key, source in (("dt_original", "exif"), ("dt_create", "exif_create"), ("dt_xmp", "xmp")):
        dt, tz = parse_exif_datetime(meta.get(key))
        if dt:
            return {"dt": dt.isoformat(), "source": source, "detail": key, "tz": tz or meta.get("offset")}
    if use_filename:
        dt, rule = parse_filename_date(filename, compiled)
        if dt:
            return {"dt": dt.isoformat(), "source": "filename", "detail": rule, "tz": None}
    if use_mtime and mtime_ns:
        try:
            dt = datetime.fromtimestamp(mtime_ns / 1e9)
            if _valid(dt):
                return {"dt": dt.isoformat(), "source": "mtime", "detail": "文件修改时间", "tz": None}
        except (OSError, ValueError, OverflowError):
            pass
    return {"dt": None, "source": None, "detail": None, "tz": None}


DATE_SOURCE_LABELS = {
    "exif": "EXIF 拍摄时间",
    "exif_create": "EXIF 创建时间",
    "xmp": "XMP 日期",
    "filename": "文件名推测",
    "mtime": "修改时间推测",
    None: "未知",
}
