"""Windows 路径与文件名安全处理。"""
from __future__ import annotations

import os
import re
import unicodedata

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
MAX_COMPONENT = 80


def sanitize_component(name: str) -> str:
    """把任意文字（镜头名、地名等）变成合法的文件夹名。"""
    s = unicodedata.normalize("NFC", str(name))
    s = s.replace("/", "-").replace("\\", "-").replace(":", "-")
    s = _ILLEGAL.sub("_", s)
    s = " ".join(s.split())
    s = s.strip(" .")
    if not s:
        s = "_"
    if s.split(".")[0].lower() in _RESERVED:
        s = "_" + s
    if len(s) > MAX_COMPONENT:
        s = s[:MAX_COMPONENT].rstrip(" .")
    return s


def long_path(p: str) -> str:
    """Windows 超过 260 字符的路径加 \\\\?\\ 前缀，其他系统原样返回。"""
    if os.name != "nt":
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\?\\") or len(p) < 240:
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def is_inside(child: str, parent: str) -> bool:
    c = os.path.normcase(os.path.abspath(child))
    p = os.path.normcase(os.path.abspath(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def existing_ancestor(p: str) -> str:
    p = os.path.abspath(p)
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def device_of(p: str) -> int | None:
    try:
        return os.stat(existing_ancestor(p)).st_dev
    except OSError:
        return None


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
