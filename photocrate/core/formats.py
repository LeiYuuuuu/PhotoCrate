"""文件格式识别。"""
from __future__ import annotations

import os

# 各厂商相机原图（RAW）扩展名
RAW_EXTS = {
    ".3fr", ".ari", ".arw", ".bay", ".cap", ".cr2", ".cr3", ".crw", ".dcr", ".dcs",
    ".dng", ".drf", ".eip", ".erf", ".fff", ".gpr", ".iiq", ".k25", ".kdc", ".mdc",
    ".mef", ".mos", ".mrw", ".nef", ".nrw", ".orf", ".ori", ".pef", ".ptx", ".pxn",
    ".raf", ".raw", ".rw2", ".rwl", ".rwz", ".sr2", ".srf", ".srw", ".x3f",
}
JPEG_EXTS = {".jpg", ".jpeg", ".jpe", ".jfif"}
HEIF_EXTS = {".heic", ".heif", ".hif"}
PNG_EXTS = {".png"}
OTHER_IMAGE_EXTS = {".tif", ".tiff", ".webp", ".bmp", ".gif", ".avif"}
# 附属文件：修图记录 / 编辑记录 / 导出元数据
SIDECAR_EXTS = {".xmp", ".aae", ".json", ".pp3", ".dop", ".on1"}
LIVE_EXTS = {".mov", ".mp4"}

IMAGE_EXTS = RAW_EXTS | JPEG_EXTS | HEIF_EXTS | PNG_EXTS | OTHER_IMAGE_EXTS

KIND_LABELS = {
    "raw": "RAW",
    "jpeg": "JPG",
    "heif": "HEIC",
    "png": "PNG",
    "image": "其他图片",
}


def ext_of(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def file_kind(path: str) -> str | None:
    """返回 raw / jpeg / heif / png / image / sidecar / live / None。"""
    e = ext_of(path)
    if e in RAW_EXTS:
        return "raw"
    if e in JPEG_EXTS:
        return "jpeg"
    if e in HEIF_EXTS:
        return "heif"
    if e in PNG_EXTS:
        return "png"
    if e in OTHER_IMAGE_EXTS:
        return "image"
    if e in SIDECAR_EXTS:
        return "sidecar"
    if e in LIVE_EXTS:
        return "live"
    return None


def is_image(path: str) -> bool:
    return ext_of(path) in IMAGE_EXTS
