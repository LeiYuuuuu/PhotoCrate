"""打开与保存。原片只读；导出图片和工程文件都写新文件（先写 .part 再改名），绝不覆盖原片。

- 从照片库打开：与「导出分享」同一套读图（有 JPG/HEIC/PNG 用它，只有 RAW 用相机内嵌的全尺寸预览），
  记下原片的 EXIF 与色彩配置文件，导出时原样带上（方向改成“正常”、宽高改成新尺寸，可选去掉 GPS）。
- 工程文件 .pcedit：一个 zip，document.json（图层结构与参数）+ 每个图层一张 PNG（蒙版另存一张灰度 PNG）。
  放在原片旁边、与原片同名时，扫描会把它当作附属文件，移动 / 改名时跟着照片走。
"""
from __future__ import annotations

import io
import json
import os
import zipfile

import numpy as np

from .document import Document, Layer, SourceInfo
from ..i18n import t as _t

PROJECT_EXT = ".pcedit"
EXPORT_FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "tif": "TIFF", "tiff": "TIFF", "webp": "WEBP"}
OPEN_FILTER = _t("图片 (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp *.heic *.heif *.gif);;修图工程 (*.pcedit);;所有文件 (*.*)")


def _rgba(im) -> np.ndarray:
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    return np.array(im, dtype=np.uint8)          # 可写的副本（asarray 得到的是只读视图）


def _new_doc(rgba: np.ndarray, name: str = _t("背景")) -> Document:
    h, w = rgba.shape[:2]
    doc = Document(w, h)
    bg = doc._register(Layer("pixel", name, rgba))
    doc.layers.append(bg)
    doc.active_id = bg.id
    return doc


def _exif_bytes(exif) -> bytes | None:
    if exif is None:
        return None
    try:
        return exif.tobytes()
    except Exception:
        return None


def open_group(g, exiftool: str | None = None) -> Document:
    """从照片库里的一张照片打开。"""
    from ..ops.exporter import load_full, source_file
    f = source_file(g)
    im, exif, icc = load_full(g, exiftool)
    doc = _new_doc(_rgba(im))
    doc.source = SourceInfo(path=f.path if f else "", name=g.name, exif=_exif_bytes(exif), icc=icc, gid=g.gid)
    return doc


def open_file(path: str, exiftool: str | None = None) -> Document:
    from PIL import Image, ImageOps

    from ..core.formats import file_kind
    if path.lower().endswith(PROJECT_EXT):
        return load_project(path)
    kind = file_kind(path)
    if kind == "raw":
        from ..core.imaging import load_raw_preview
        im, _ = load_raw_preview(path, 100000, exiftool)
        doc = _new_doc(_rgba(im))
        doc.source = SourceInfo(path=path, name=os.path.basename(path))
        return doc
    if kind == "heif":
        from ..core.imaging import _ensure_heif
        _ensure_heif()
    with Image.open(path) as im:
        exif = im.getexif()
        icc = im.info.get("icc_profile")
        if getattr(im, "is_animated", False):
            im.seek(0)
        im = ImageOps.exif_transpose(im)
        rgba = _rgba(im)
    doc = _new_doc(rgba)
    doc.source = SourceInfo(path=path, name=os.path.basename(path), exif=_exif_bytes(exif), icc=icc)
    return doc


def project_path_for(source_path: str) -> str:
    return os.path.splitext(source_path)[0] + PROJECT_EXT


def default_project_path(doc: Document) -> str:
    """没保存过的文档默认存到哪：原片旁边同名（合并曝光的结果加「_HDR」，不和参考帧那张自己的工程混在一起）。"""
    if doc.source.project:
        return doc.source.project
    if not doc.source.path:
        return ""
    return os.path.splitext(doc.source.path)[0] + doc.source.suffix + PROJECT_EXT


def suggest_export_path(doc: Document, ext: str = ".jpg") -> str:
    """原片旁边的「原名_修图.jpg」，重名加编号。"""
    src = doc.source.path or doc.source.project
    folder = os.path.dirname(src) if src else os.path.expanduser("~")
    stem = os.path.splitext(os.path.basename(src))[0] if src else _t("未命名")
    n = 0
    while True:
        p = os.path.join(folder, f"{stem}{doc.source.suffix or _t('_修图')}{'' if n == 0 else f' ({n})'}{ext}")
        if not os.path.exists(p):
            return p
        n += 1


def _same(a: str, b: str) -> bool:
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def export_image(doc: Document, path: str, quality: int = 92, keep_exif: bool = True, strip_gps: bool = False,
                 rgba: np.ndarray | None = None) -> int:
    """把合成结果存成一张图（格式看扩展名）。返回文件大小。"""
    from PIL import Image

    if _same(path, doc.source.path):
        raise ValueError(_t("不能覆盖原片：请另存为一个新文件。"))
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    fmt = EXPORT_FORMATS.get(ext)
    if fmt is None:
        raise ValueError(_t('不支持的格式：.{0}（可以用 .jpg / .png / .tif / .webp）', ext))
    rgba = doc.render_full() if rgba is None else rgba
    transparent = int(rgba[..., 3].min()) < 255
    if fmt == "JPEG" or not transparent:
        a = rgba[..., 3:4].astype(np.float32) / 255.0
        rgb = rgba[..., :3].astype(np.float32) * a + 255.0 * (1 - a)
        im = Image.fromarray(np.clip(rgb + 0.5, 0, 255).astype(np.uint8), "RGB")
    else:
        im = Image.fromarray(rgba, "RGBA")
    kw: dict = {}
    if keep_exif and doc.source.exif:
        try:
            ex = Image.Exif()
            ex.load(doc.source.exif)
            ex[0x0112] = 1                                   # 方向：已经转正
            for tag in (0x0100, 0x0101, 0x0201, 0x0202):     # 旧的宽高、内嵌缩略图
                ex.pop(tag, None)
            ex[0x0131] = "PhotoCrate"
            sub = ex.get_ifd(0x8769)
            if sub:
                sub[0xA002], sub[0xA003] = im.width, im.height
            if strip_gps:
                ex.pop(0x8825, None)
            kw["exif"] = ex.tobytes()
        except Exception:
            pass
    if doc.source.icc and fmt in ("JPEG", "PNG", "TIFF", "WEBP"):
        kw["icc_profile"] = doc.source.icc
    if fmt == "JPEG":
        q = int(quality)
        kw.update(quality=q, optimize=True, progressive=True, subsampling="4:4:4" if q >= 92 else "4:2:0")
    elif fmt == "PNG":
        kw.update(compress_level=6)
    elif fmt == "TIFF":
        kw.update(compression="tiff_lzw")
    elif fmt == "WEBP":
        kw.update(quality=int(quality), lossless=int(quality) >= 100, method=4)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".part"
    try:
        im.save(tmp, fmt, **kw)
    except (TypeError, ValueError, OSError):
        kw.pop("exif", None)                          # 个别格式 / 旧版 Pillow 不收 EXIF：不带也要存下来
        im.save(tmp, fmt, **kw)
    os.replace(tmp, path)
    return os.path.getsize(path)


# ---------------------------------------------------------------------------
# 工程文件
# ---------------------------------------------------------------------------

def _png(arr: np.ndarray) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA" if arr.ndim == 3 else "L").save(buf, "PNG", compress_level=1)
    return buf.getvalue()


def _unpng(data: bytes, mode: str) -> np.ndarray:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return np.array(im.convert(mode), dtype=np.uint8)


def save_project(doc: Document, path: str) -> int:
    if _same(path, doc.source.path):
        raise ValueError(_t("不能覆盖原片。"))
    layers = []
    files: dict[str, bytes] = {}
    for i, l in enumerate(doc.layers):
        d = {"kind": l.kind, "name": l.name, "x": l.x, "y": l.y, "visible": l.visible, "opacity": l.opacity,
             "blend": l.blend, "mask_enabled": l.mask_enabled, "params": l.params, "pixels": None, "mask": None}
        if l.pixels is not None:
            d["pixels"] = f"layers/{i:03d}.png"
            files[d["pixels"]] = _png(l.pixels)
        if l.mask is not None:
            d["mask"] = f"masks/{i:03d}.png"
            files[d["mask"]] = _png(l.mask)
        layers.append(d)
    s = doc.source
    meta = {"format": "photocrate-edit", "version": 1, "width": doc.width, "height": doc.height,
            "active": doc.index_of(doc.active_id), "layers": layers,
            "source": {"path": s.path, "name": s.name, "gid": s.gid, "suffix": s.suffix}}
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        z.writestr("document.json", json.dumps(meta, ensure_ascii=False, indent=1))
        if s.exif:
            z.writestr("exif.bin", s.exif)
        if s.icc:
            z.writestr("icc.icc", s.icc)
        for name, data in files.items():
            z.writestr(name, data)
    os.replace(tmp, path)
    doc.source.project = path
    doc.modified = False
    return os.path.getsize(path)


def load_project(path: str) -> Document:
    with zipfile.ZipFile(path) as z:
        meta = json.loads(z.read("document.json").decode("utf-8"))
        if meta.get("format") != "photocrate-edit":
            raise ValueError(_t("不是影匣的修图工程文件"))
        names = set(z.namelist())
        doc = Document(int(meta["width"]), int(meta["height"]))
        for d in meta["layers"]:
            l = Layer(d["kind"], d.get("name") or _t("图层"), x=int(d.get("x", 0)), y=int(d.get("y", 0)),
                      params=d.get("params") or {})
            if d.get("pixels") in names:
                l.pixels = _unpng(z.read(d["pixels"]), "RGBA")
            if d.get("mask") in names:
                l.mask = _unpng(z.read(d["mask"]), "L")
            l.visible = bool(d.get("visible", True))
            l.opacity = float(d.get("opacity", 1.0))
            l.blend = d.get("blend") or "normal"
            l.mask_enabled = bool(d.get("mask_enabled", True))
            if l.kind == "adjust":
                from .adjust import normalize
                l.params = normalize(l.params)
            elif l.kind == "text" and l.pixels is None:
                l.render_text()
            elif l.pixels is None:
                continue
            doc.layers.append(doc._register(l))
        if not doc.layers:
            raise ValueError(_t("工程文件里没有图层"))
        a = int(meta.get("active", -1))
        doc.active_id = doc.layers[a if 0 <= a < len(doc.layers) else -1].id
        src = meta.get("source") or {}
        doc.source = SourceInfo(path=src.get("path") or "", name=src.get("name") or os.path.basename(path),
                                exif=z.read("exif.bin") if "exif.bin" in names else None,
                                icc=z.read("icc.icc") if "icc.icc" in names else None,
                                gid=src.get("gid"), project=path, suffix=src.get("suffix") or "")
    return doc
