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

from .document import Document, Layer, SourceInfo, convert, to8
from ..i18n import t as _t

PROJECT_EXT = ".pcedit"
EXPORT_FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "tif": "TIFF", "tiff": "TIFF", "webp": "WEBP"}
OPEN_FILTER = _t("图片 (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp *.heic *.heif *.gif *.psd *.dng *.arw *.cr2 *.cr3 *.nef *.raf *.orf *.rw2 *.pef *.srw);;修图工程 (*.pcedit);;所有文件 (*.*)")


def _rgba(im) -> np.ndarray:
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    return np.array(im, dtype=np.uint8)          # 可写的副本（asarray 得到的是只读视图）


def _new_doc(rgba: np.ndarray, name: str = _t("背景")) -> Document:
    h, w = rgba.shape[:2]
    doc = Document(w, h, 16 if rgba.dtype == np.uint16 else 8)
    bg = doc._register(Layer("pixel", name, rgba))
    doc.layers.append(bg)
    doc.active_id = bg.id
    return doc


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def decode16(data: bytes) -> np.ndarray | None:
    """用 OpenCV 读 16 位的 PNG / TIFF，返回 uint16 RGBA；不是 16 位（或读不了）返回 None。"""
    import cv2
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
    if arr is None or arr.dtype != np.uint16:
        return None
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGBA)
    elif arr.shape[2] == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGBA)
    elif arr.shape[2] == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGBA)
    else:
        return None
    return np.ascontiguousarray(arr)


def _orient(arr: np.ndarray, o: int) -> np.ndarray:
    """按 EXIF 方向（1–8）转正。"""
    ops = {2: lambda a: a[:, ::-1], 3: lambda a: a[::-1, ::-1], 4: lambda a: a[::-1],
           5: lambda a: np.rot90(a[:, ::-1], 1), 6: lambda a: np.rot90(a, -1), 7: lambda a: np.rot90(a[:, ::-1], -1),
           8: lambda a: np.rot90(a, 1)}
    f = ops.get(int(o or 1))
    return np.ascontiguousarray(f(arr)) if f else arr


def open_raw(path: str, opts=None, exif: bytes | None = None, name: str | None = None, gid=None) -> Document:
    """RAW 显影成 16 位文档。"""
    from . import raw
    rgb = raw.develop(path, opts)
    rgba = np.dstack([rgb, np.full(rgb.shape[:2], 65535, np.uint16)])
    doc = _new_doc(np.ascontiguousarray(rgba))
    doc.source = SourceInfo(path=path, name=name or os.path.basename(path),
                            exif=exif if exif is not None else raw.embedded_exif(path), gid=gid)
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
    if f is not None and f.role == "raw":                 # 只有 RAW：真正显影（16 位），不用内嵌的 JPEG 预览
        try:
            return open_raw(f.path, name=g.name, gid=g.gid, exif=_group_exif(g))
        except Exception:
            pass
    im, exif, icc = load_full(g, exiftool)
    doc = _new_doc(_rgba(im))
    doc.source = SourceInfo(path=f.path if f else "", name=g.name, exif=_exif_bytes(exif), icc=icc, gid=g.gid)
    return doc


def _group_exif(g) -> bytes | None:
    try:
        from PIL import Image

        from ..ops.exporter import _fill_exif_from_meta
        ex = Image.Exif()
        _fill_exif_from_meta(ex, g)
        return ex.tobytes() if len(ex) else None
    except Exception:
        return None


def open_file(path: str, exiftool: str | None = None, raw_opts=None) -> Document:
    from PIL import Image, ImageOps

    from ..core.formats import file_kind
    if path.lower().endswith(PROJECT_EXT):
        return load_project(path)
    if path.lower().endswith((".psd", ".psb")):
        from .psd import read
        return read(path)
    kind = file_kind(path)
    if kind == "raw":
        try:
            return open_raw(path, raw_opts)
        except Exception:                                   # LibRaw 不认识这种 RAW：退回内嵌预览
            from ..core.imaging import load_raw_preview
            im, _ = load_raw_preview(path, 100000, exiftool)
            doc = _new_doc(_rgba(im))
            doc.source = SourceInfo(path=path, name=os.path.basename(path))
            return doc
    if kind == "heif":
        from ..core.imaging import _ensure_heif
        _ensure_heif()
    deep = None
    if path.lower().endswith((".png", ".tif", ".tiff")):
        deep = decode16(_read_bytes(path))
    if deep is not None:                                    # 16 位：Pillow 读不了 48 位 RGB，只用它取 EXIF / ICC
        exif = icc = None
        try:
            with Image.open(path) as im:
                exif = im.getexif()
                icc = im.info.get("icc_profile")
        except Exception:
            pass
        rgba = _orient(deep, exif.get(0x0112, 1) if exif else 1)
    else:
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


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    import struct
    import zlib
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png16(arr: np.ndarray, icc: bytes | None = None, exif: bytes | None = None) -> bytes:
    """16 位 PNG（RGB 或 RGBA），带 ICC（iCCP）和 EXIF（eXIf）。"""
    import cv2
    import zlib
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA if arr.shape[2] == 4 else cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", bgr, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not ok:
        raise ValueError("PNG encode failed")
    data = buf.tobytes()
    extra = b""
    if icc:
        extra += _png_chunk(b"iCCP", b"ICC Profile\0\0" + zlib.compress(icc))
    if exif:
        extra += _png_chunk(b"eXIf", exif[6:] if exif.startswith(b"Exif\0\0") else exif)
    i = 8 + 8 + 13 + 4                                    # 签名 + IHDR 块之后
    return data[:i] + extra + data[i:]


def tiff16(arr: np.ndarray, icc: bytes | None = None, tags: dict | None = None) -> bytes:
    """16 位 TIFF（RGB 或 RGBA，Deflate + 水平差分预测），带 ICC 和几个基本文字标签（相机、时间）。"""
    import struct
    import zlib
    h, w, spp = arr.shape
    rows = max(1, min(h, 262144 // max(1, w * spp * 2)))                   # 每条约 256 KB
    strips = []
    for y0 in range(0, h, rows):
        a = arr[y0:y0 + rows].astype("<u2")
        d = a.copy()
        d[:, 1:] = a[:, 1:] - a[:, :-1]                    # 预测器 2：按像素做差（uint16 自然回绕）
        strips.append(zlib.compress(d.tobytes(), 6))
    text = {k: v for k, v in (tags or {}).items() if v}
    entries = [(256, 4, [w]), (257, 4, [h]), (258, 3, [16] * spp), (259, 3, [8]), (262, 3, [2]),
               (273, 4, [0] * len(strips)), (277, 3, [spp]), (278, 4, [rows]), (279, 4, [len(s) for s in strips]),
               (284, 3, [1]), (317, 3, [2]), (339, 3, [1] * spp)]
    if spp == 4:
        entries.append((338, 3, [2]))                     # 第 4 个通道是（非预乘的）透明度
    for tag, v in text.items():
        entries.append((tag, 2, str(v)))
    if icc:
        entries.append((34675, 7, icc))
    entries.sort(key=lambda e: e[0])
    fmt = {3: "H", 4: "I"}

    def build(strip0: int, data0: int):
        ifd = struct.pack("<H", len(entries))
        extra = b""
        for tag, typ, vals in entries:
            if tag == 273:
                vals = [strip0 + sum(len(s) for s in strips[:k]) for k in range(len(strips))]
            if typ == 2:
                raw = vals.encode("utf-8", "replace") + b"\0"
            elif typ == 7:
                raw = bytes(vals)
            else:
                raw = struct.pack("<" + fmt[typ] * len(vals), *vals)
            count = len(raw) if typ in (2, 7) else len(vals)
            if len(raw) <= 4:
                ifd += struct.pack("<HHI", tag, typ, count) + raw.ljust(4, b"\0")
            else:
                ifd += struct.pack("<HHII", tag, typ, count, data0 + len(extra))
                extra += raw + (b"\0" if len(raw) % 2 else b"")
        return ifd + struct.pack("<I", 0), extra

    ifd_len = 2 + 12 * len(entries) + 4
    _, extra = build(0, 8 + ifd_len)
    strip0 = 8 + ifd_len + len(extra)
    ifd, extra = build(strip0, 8 + ifd_len)
    return b"II*\0" + struct.pack("<I", 8) + ifd + extra + b"".join(strips)


def _exif_out(doc: Document, w: int, h: int, strip_gps: bool) -> bytes | None:
    from PIL import Image
    try:
        ex = Image.Exif()
        ex.load(doc.source.exif)
        ex[0x0112] = 1                                   # 方向：已经转正
        for tag in (0x0100, 0x0101, 0x0201, 0x0202):     # 旧的宽高、内嵌缩略图
            ex.pop(tag, None)
        ex[0x0131] = "PhotoCrate"
        sub = ex.get_ifd(0x8769)
        if sub:
            sub[0xA002], sub[0xA003] = w, h
        if strip_gps:
            ex.pop(0x8825, None)
        return ex.tobytes()
    except Exception:
        return None


def _tiff_tags(doc: Document) -> dict:
    tags = {305: "PhotoCrate"}
    if not doc.source.exif:
        return tags
    try:
        from PIL import Image
        ex = Image.Exif()
        ex.load(doc.source.exif)
        sub = ex.get_ifd(0x8769)
        tags.update({271: ex.get(271), 272: ex.get(272), 306: sub.get(0x9003) or ex.get(306)})
    except Exception:
        pass
    return tags


def export_image(doc: Document, path: str, quality: int = 92, keep_exif: bool = True, strip_gps: bool = False,
                 rgba: np.ndarray | None = None, deep: bool | None = None, cmyk=None) -> int:
    """把合成结果存成一张图（格式看扩展名）。返回文件大小。
    deep：存 16 位（只有 PNG / TIFF 可以；None = 文档是 16 位时就存 16 位）。"""
    from PIL import Image

    if _same(path, doc.source.path):
        raise ValueError(_t("不能覆盖原片：请另存为一个新文件。"))
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    fmt = EXPORT_FORMATS.get(ext)
    if fmt is None:
        raise ValueError(_t('不支持的格式：.{0}（可以用 .jpg / .png / .tif / .webp）', ext))
    if deep is None:
        deep = doc.depth == 16
    if deep and fmt in ("PNG", "TIFF"):
        return _export16(doc, path, fmt, keep_exif, strip_gps, rgba)
    rgba = doc.render_full(np.uint8) if rgba is None else to8(rgba)
    transparent = int(rgba[..., 3].min()) < 255
    cmyk_icc = None
    if cmyk and fmt in ("JPEG", "TIFF"):                     # 印刷用：按配置文件转成 CMYK
        from .color import to_cmyk_image
        a = rgba[..., 3:4].astype(np.float32) / 255.0
        rgb = np.clip(rgba[..., :3].astype(np.float32) * a + 255.0 * (1 - a) + 0.5, 0, 255).astype(np.uint8)
        im, cmyk_icc = to_cmyk_image(rgb, doc.source.icc, cmyk)
    elif fmt == "JPEG" or not transparent:
        a = rgba[..., 3:4].astype(np.float32) / 255.0
        rgb = rgba[..., :3].astype(np.float32) * a + 255.0 * (1 - a)
        im = Image.fromarray(np.clip(rgb + 0.5, 0, 255).astype(np.uint8), "RGB")
    else:
        im = Image.fromarray(rgba, "RGBA")
    kw: dict = {}
    if keep_exif and doc.source.exif:
        ex = _exif_out(doc, im.width, im.height, strip_gps)
        if ex:
            kw["exif"] = ex
    if cmyk_icc:
        kw["icc_profile"] = cmyk_icc
    elif doc.source.icc and fmt in ("JPEG", "PNG", "TIFF", "WEBP"):
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


def _export16(doc: Document, path: str, fmt: str, keep_exif: bool, strip_gps: bool, rgba) -> int:
    rgba = doc.render_full(np.uint16) if rgba is None else convert(rgba, np.uint16)
    if int(rgba[..., 3].min()) >= 65535:
        arr = np.ascontiguousarray(rgba[..., :3])
    else:
        arr = np.ascontiguousarray(rgba)
    h, w = arr.shape[:2]
    if fmt == "PNG":
        exif = _exif_out(doc, w, h, strip_gps) if keep_exif and doc.source.exif else None
        data = png16(arr, doc.source.icc, exif)
    else:
        data = tiff16(arr, doc.source.icc, _tiff_tags(doc) if keep_exif else {305: "PhotoCrate"})
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return os.path.getsize(path)


# ---------------------------------------------------------------------------
# 工程文件
# ---------------------------------------------------------------------------

def _png(arr: np.ndarray) -> bytes:
    from PIL import Image
    if arr.dtype == np.uint16:
        return png16(arr)
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
             "blend": l.blend, "mask_enabled": l.mask_enabled, "params": l.params, "pixels": None, "mask": None,
             "clip": l.clip, "parent": doc.index_of(l.parent) if l.parent is not None else None,
             "collapsed": l.collapsed, "fill": l.fill, "style": l.style,
             "locks": [l.lock_alpha, l.lock_pixels, l.lock_pos, l.lock_all]}
        if l.pixels is not None:
            d["pixels"] = f"layers/{i:03d}.png"
            d["depth"] = 16 if l.pixels.dtype == np.uint16 else 8
            files[d["pixels"]] = _png(l.pixels)
        if l.mask is not None:
            d["mask"] = f"masks/{i:03d}.png"
            files[d["mask"]] = _png(l.mask)
        if l.source is not None:                              # 智能对象的原始像素
            d["source"] = f"smart/{i:03d}.png"
            d["source_depth"] = 16 if l.source.dtype == np.uint16 else 8
            files[d["source"]] = _png(l.source)
        layers.append(d)
    chans = []
    for i, (name, arr) in enumerate(doc.channels.items()):
        f = f"channels/{i:03d}.png"
        files[f] = _png(arr)
        chans.append({"name": name, "file": f})
    s = doc.source
    meta = {"channels": chans, "guides": [list(g) for g in doc.guides], "regions": doc.regions,
            "frames": [{"show": [doc.index_of(i) for i in f.get("show", [])], "delay": f.get("delay", 100)}
                       for f in doc.frames], "format": "photocrate-edit", "version": 2, "width": doc.width, "height": doc.height, "depth": doc.depth,
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
        doc = Document(int(meta["width"]), int(meta["height"]), int(meta.get("depth", 8)))
        parents: list = []
        for d in meta["layers"]:
            l = Layer(d["kind"], d.get("name") or _t("图层"), x=int(d.get("x", 0)), y=int(d.get("y", 0)),
                      params=d.get("params") or {})
            if d.get("pixels") in names:
                data = z.read(d["pixels"])
                l.pixels = (decode16(data) if d.get("depth") == 16 else None)
                if l.pixels is None:
                    l.pixels = _unpng(data, "RGBA")
            if d.get("mask") in names:
                l.mask = _unpng(z.read(d["mask"]), "L")
            l.visible = bool(d.get("visible", True))
            l.opacity = float(d.get("opacity", 1.0))
            l.blend = d.get("blend") or "normal"
            l.mask_enabled = bool(d.get("mask_enabled", True))
            l.clip, l.collapsed = bool(d.get("clip", False)), bool(d.get("collapsed", False))
            l.fill, l.style = float(d.get("fill", 1.0)), dict(d.get("style") or {})
            l.lock_alpha, l.lock_pixels, l.lock_pos, l.lock_all = (list(d.get("locks") or []) + [False] * 4)[:4]
            if l.kind == "adjust":
                from .adjust import normalize
                l.params = normalize(l.params)
            elif l.kind == "text" and l.pixels is None:
                l.render_text()
            elif l.kind in ("shape", "smart"):
                if d.get("source") in names:
                    data = z.read(d["source"])
                    l.source = decode16(data) if d.get("source_depth") == 16 else None
                    if l.source is None:
                        l.source = _unpng(data, "RGBA")
                if l.kind == "smart" and l.source is None:
                    l.kind = "pixel"
                elif l.pixels is None:
                    l.render()
            elif l.pixels is None and l.kind != "group":
                parents.append(None)
                continue
            parents.append((l, d.get("parent")))
            doc.layers.append(doc._register(l))
        allp = [p for p in parents]
        for item in allp:                                   # parent 存的是父组在列表里的序号
            if item is None:
                continue
            l, pi = item
            if isinstance(pi, int) and 0 <= pi < len(allp) and allp[pi] is not None and allp[pi][0].kind == "group":
                l.parent = allp[pi][0].id
        if not doc.layers:
            raise ValueError(_t("工程文件里没有图层"))
        for c in meta.get("channels") or []:
            if c.get("file") in names:
                arr = _unpng(z.read(c["file"]), "L")
                if arr.shape == (doc.height, doc.width):
                    doc.channels[str(c.get("name") or c["file"])] = arr
        doc.guides = [(str(o), float(v)) for o, v in (meta.get("guides") or []) if o in ("h", "v")]
        doc.regions = [dict(r) for r in meta.get("regions") or [] if isinstance(r, dict)]
        doc.frames = [{"show": [doc.layers[i].id for i in f.get("show", []) if 0 <= i < len(doc.layers)],
                       "delay": int(f.get("delay", 100))} for f in meta.get("frames") or []]
        a = int(meta.get("active", -1))
        doc.active_id = doc.layers[a if 0 <= a < len(doc.layers) else -1].id
        src = meta.get("source") or {}
        doc.source = SourceInfo(path=src.get("path") or "", name=src.get("name") or os.path.basename(path),
                                exif=z.read("exif.bin") if "exif.bin" in names else None,
                                icc=z.read("icc.icc") if "icc.icc" in names else None,
                                gid=src.get("gid"), project=path, suffix=src.get("suffix") or "")
    return doc
