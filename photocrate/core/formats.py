"""文件格式识别。"""
from __future__ import annotations

import os
from ..i18n import t as _t

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
# 附属文件：修图记录 / 编辑记录 / 导出元数据；.xml / .thm 是相机给视频写的附属文件（索尼 C0001M01.XML、佳能 .THM）；
# .pcedit 是本软件的修图工程（与原片同名时跟着照片移动 / 改名）
SIDECAR_EXTS = {".xmp", ".aae", ".json", ".pp3", ".dop", ".on1", ".xml", ".thm", ".pcedit"}
# .mov / .mp4 与同名照片在一起时是实况照片（Live Photo）的视频部分，否则是独立视频
LIVE_EXTS = {".mov", ".mp4"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".mts", ".m2ts", ".avi", ".mkv", ".3gp", ".wmv", ".mpg", ".mpeg", ".webm"}
MEDIA_ROLES = ("raw", "jpeg", "heif", "png", "image", "video")

IMAGE_EXTS = RAW_EXTS | JPEG_EXTS | HEIF_EXTS | PNG_EXTS | OTHER_IMAGE_EXTS

KIND_LABELS = {
    "raw": "RAW",
    "jpeg": "JPG",
    "heif": "HEIC",
    "png": "PNG",
    "image": _t("其他图片"),
    "video": _t("视频"),
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
    if e in VIDEO_EXTS:
        return "video"
    return None


def is_image(path: str) -> bool:
    return ext_of(path) in IMAGE_EXTS


def is_video(path: str) -> bool:
    return ext_of(path) in VIDEO_EXTS
