"""核心数据结构。

一张"照片"在本软件里是一个 PhotoGroup（文件组）：
主图 + 同名 RAW/JPG 配对 + .xmp/.aae/.json 附属文件 + Live Photo 视频。
移动时整个组一起移动、一起撤销，保证修图记录不和照片分家。
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .formats import KIND_LABELS, ext_of


def file_signature(path: str, size: int, mtime_ns: int) -> str:
    """与路径无关的文件指纹：文件名 + 大小 + 修改时间。

    移动文件不会改变它，所以移动后缓存依然命中，无需重新分析。
    """
    name = os.path.basename(path).lower()
    raw = f"{name}|{size}|{mtime_ns}".encode("utf-8", "surrogatepass")
    return hashlib.sha1(raw).hexdigest()[:24]


@dataclass
class MemberFile:
    path: str
    role: str          # raw / jpeg / heif / png / image / sidecar / live
    size: int
    mtime_ns: int

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def sig(self) -> str:
        return file_signature(self.path, self.size, self.mtime_ns)


@dataclass
class PhotoGroup:
    gid: int
    files: list[MemberFile]
    primary: str                       # 用于显示 / 像素分析的文件
    meta: dict[str, Any] = field(default_factory=dict)    # 归一化元数据
    feats: dict[str, Any] = field(default_factory=dict)   # 各分析器输出 {analyzer_id: {...}}
    thumb: str | None = None           # 缩略图缓存路径

    # ---- 基础属性 -------------------------------------------------------
    @property
    def primary_file(self) -> MemberFile:
        for f in self.files:
            if f.path == self.primary:
                return f
        return self.files[0]

    @property
    def folder(self) -> str:
        return os.path.dirname(self.primary)

    @property
    def name(self) -> str:
        return os.path.basename(self.primary)

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0]

    @property
    def image_files(self) -> list[MemberFile]:
        return [f for f in self.files if f.role in ("raw", "jpeg", "heif", "png", "image")]

    @property
    def has_raw(self) -> bool:
        return any(f.role == "raw" for f in self.files)

    @property
    def kind(self) -> str:
        """组的类型：raw / jpeg / heif / png / image / raw+jpeg / raw+heif。"""
        roles = {f.role for f in self.image_files}
        if "raw" in roles and len(roles) > 1:
            other = "jpeg" if "jpeg" in roles else ("heif" if "heif" in roles else sorted(roles - {"raw"})[0])
            return f"raw+{other}"
        return self.primary_file.role

    @property
    def kind_label(self) -> str:
        k = self.kind
        if k.startswith("raw+"):
            return "RAW+" + KIND_LABELS.get(k[4:], k[4:].upper())
        return KIND_LABELS.get(k, k.upper())

    @property
    def ext_label(self) -> str:
        exts = sorted({ext_of(f.path).lstrip(".").upper() for f in self.image_files})
        return "+".join(exts) if exts else ext_of(self.primary).lstrip(".").upper()

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    # ---- 常用分析结果的便捷访问 ------------------------------------------
    def feat(self, analyzer: str, key: str, default: Any = None) -> Any:
        d = self.feats.get(analyzer)
        if not isinstance(d, dict):
            return default
        return d.get(key, default)

    @property
    def date(self) -> datetime | None:
        s = self.feat("date", "dt")
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    @property
    def date_source(self) -> str | None:
        return self.feat("date", "source")

    @property
    def date_is_guess(self) -> bool:
        return self.date_source in ("filename", "mtime")
