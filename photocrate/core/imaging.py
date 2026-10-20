"""取像素：为每张照片生成一张长边 ≤1024 的预览图（RGB），供缩略图和各像素分析器复用。

一次解码、多处复用：
- JPG：Pillow draft 模式在解码时直接缩小，速度快很多。
- HEIC：pillow-heif。
- RAW：不做完整解码，只取相机内嵌的 JPEG 预览（rawpy.extract_thumb，几毫秒）；
  取不到时依次回退到 ExifTool 提取预览、rawpy 半尺寸解码。
"""
from __future__ import annotations

import io
import logging
from typing import Any

log = logging.getLogger(__name__)

_heif_registered = False


def _ensure_heif() -> None:
    global _heif_registered
    if not _heif_registered:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener(thumbnails=False)
        except Exception as e:  # pragma: no cover
            log.debug("pillow-heif 不可用：%s", e)
        _heif_registered = True


def _finish(im, max_side: int):
    from PIL import Image

    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (128, 128, 128, 255))
        bg.alpha_composite(im)
        im = bg
    if im.mode != "RGB":
        im = im.convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS if max(im.size) < 4 * max_side else Image.Resampling.BILINEAR)
    return im


def _draft_size(size: tuple[int, int], max_side: int) -> tuple[int, int]:
    """JPEG 草稿解码的目标尺寸：只保证长边 ≥ max_side（按短边算会多解码 4 倍像素）。"""
    w, h = size
    s = max_side / max(w, h, 1)
    return max(1, int(w * s)), max(1, int(h * s))


def _open_jpeg_bytes(data: bytes, max_side: int, flip: int = 0):
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(data))
    im.draft("RGB", _draft_size(im.size, max_side))
    orient = 1
    try:
        orient = im.getexif().get(274, 1)
    except Exception:
        pass
    if orient and orient != 1:
        im = ImageOps.exif_transpose(im)
    elif flip:
        im = _apply_libraw_flip(im, flip)
    im.load()
    return im


def _apply_libraw_flip(im, flip: int):
    from PIL import Image

    # LibRaw: 3 = 旋转 180°，5 = 逆时针 90°，6 = 顺时针 90°
    if flip == 3:
        return im.transpose(Image.Transpose.ROTATE_180)
    if flip == 5:
        return im.transpose(Image.Transpose.ROTATE_90)
    if flip == 6:
        return im.transpose(Image.Transpose.ROTATE_270)
    return im


def load_raw_preview(path: str, max_side: int, exiftool: str | None) -> tuple[Any, str]:
    from PIL import Image
    import rawpy

    flip = 0
    try:
        with rawpy.imread(path) as raw:
            try:
                flip = int(raw.sizes.flip)
            except Exception:
                flip = 0
            try:
                th = raw.extract_thumb()
                if th.format == rawpy.ThumbFormat.JPEG:
                    im = _open_jpeg_bytes(th.data, max_side, flip)
                    if max(im.size) >= 320:
                        return im, "raw-embedded"
                elif th.format == rawpy.ThumbFormat.BITMAP:
                    im = _apply_libraw_flip(Image.fromarray(th.data), flip)
                    if max(im.size) >= 320:
                        return im, "raw-embedded"
            except Exception as e:
                log.debug("内嵌预览不可用 %s: %s", path, e)
    except Exception as e:
        log.debug("rawpy 无法打开 %s: %s", path, e)

    if exiftool:
        from .metadata import exiftool_extract_preview
        data = exiftool_extract_preview(exiftool, path)
        if data:
            try:
                return _open_jpeg_bytes(data, max_side, flip), "exiftool-preview"
            except Exception:
                pass

    with rawpy.imread(path) as raw:
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=False, output_bps=8)
    return Image.fromarray(rgb), "raw-decoded"


def load_preview(path: str, kind: str, max_side: int = 1024, exiftool: str | None = None):
    """返回 (PIL.Image RGB, 来源说明)；失败抛异常。"""
    from PIL import Image, ImageOps

    if kind == "raw":
        im, src = load_raw_preview(path, max_side, exiftool)
        return _finish(im, max_side), src
    if kind == "heif":
        _ensure_heif()
    with open(path, "rb") as fh:
        im = Image.open(fh)
        if kind == "jpeg":
            im.draft("RGB", _draft_size(im.size, max_side))
        if getattr(im, "is_animated", False):
            im.seek(0)
        im = ImageOps.exif_transpose(im)
        im.load()
    return _finish(im, max_side), "decoded"
