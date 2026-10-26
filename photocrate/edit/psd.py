"""PSD 读写（Photoshop 文档，RGB，8 / 16 位）。

写：每个像素 / 文字 / 形状 / 智能对象图层写成一个 PSD 像素图层（名称 luni、不透明度、混合模式、显示 / 隐藏、
剪贴蒙版、填充不透明度 iOpa、图层蒙版），图层组写成 Photoshop 的组（lsct 分隔层），再写一张合成图。
图层样式先合进图层像素；调整图层 PSD 里没法原样保留，写成一个隐藏的空图层占位（合成图里效果是对的）。
通道数据不压缩（Photoshop、GIMP、Affinity 都能读）。

读：图层记录 + 通道数据（不压缩 / RLE / ZIP / 带预测的 ZIP），组、蒙版、混合模式、不透明度、填充、剪贴都读进来；
文字、形状、调整图层读成它们在 PSD 里存的像素。没有图层信息的 PSD 读合成图。
"""
from __future__ import annotations

import struct
import zlib

import numpy as np

from ..i18n import t as _t

BLEND_KEYS = {"normal": b"norm", "multiply": b"mul ", "screen": b"scrn", "overlay": b"over", "soft_light": b"sLit",
              "hard_light": b"hLit", "darken": b"dark", "lighten": b"lite", "color_dodge": b"div ",
              "color_burn": b"idiv", "linear_dodge": b"lddg", "linear_burn": b"lbrn", "difference": b"diff",
              "exclusion": b"smud", "hue": b"hue ", "saturation": b"sat ", "color": b"colr", "luminosity": b"lum ",
              "pass_through": b"pass"}
BLEND_FROM = {v: k for k, v in BLEND_KEYS.items()}


class PSDError(Exception):
    pass


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------

def _pascal(name: str) -> bytes:
    b = name.encode("latin-1", "replace")[:255]
    s = bytes([len(b)]) + b
    return s + b"\0" * ((-len(s)) % 4)


def _block(key: bytes, data: bytes) -> bytes:
    if len(data) % 2:
        data += b"\0"
    return b"8BIM" + key + struct.pack(">I", len(data)) + data


def _luni(name: str) -> bytes:
    u = name.encode("utf-16-be")
    return struct.pack(">I", len(u) // 2) + u + b"\0\0"


def _chan(arr: np.ndarray, depth: int) -> bytes:
    a = arr.astype(">u2") if depth == 16 else arr.astype(np.uint8)
    return struct.pack(">H", 0) + np.ascontiguousarray(a).tobytes()


def write(doc, path: str) -> int:
    """把文档写成 PSD。返回文件大小。"""
    from .document import DTYPES, convert, crop_canvas
    depth = doc.depth
    dt = DTYPES[depth]
    records, chdata = [], []

    def add(name, top, left, bottom, right, chans, blend=b"norm", opacity=255, clip=0, visible=True, extra=b"",
            mask=None):
        """chans：[(id, ndarray)]；mask：(top, left, bottom, right, ndarray, default) 或 None。"""
        info = b"".join(struct.pack(">hI", cid, 2 + data.size * (2 if depth == 16 else 1)) for cid, data in chans)
        flags = 0 if visible else 2
        md = b""
        if mask is not None:
            mt, ml, mb, mr, _, dflt = mask
            md = struct.pack(">iiiiBB", mt, ml, mb, mr, dflt, 0) + b"\0\0"
        ex = struct.pack(">I", len(md)) + md + struct.pack(">I", 0) + _pascal(name) + _block(b"luni", _luni(name)) + extra
        rec = struct.pack(">iiiiH", top, left, bottom, right, len(chans)) + info + b"8BIM" + blend + \
            struct.pack(">BBBB", int(round(opacity)), clip, flags, 0) + struct.pack(">I", len(ex)) + ex
        records.append(rec)
        chdata.append(b"".join(_chan(data, depth) for _, data in chans))

    def emit(l, idx):
        name = l.name
        if l.kind == "adjust":
            add(_t('{0}（调整图层，PSD 里无法保留）', name), 0, 0, 0, 0, [(-1, np.zeros((0, 0), dt)), (0, np.zeros((0, 0), dt)),
                                                         (1, np.zeros((0, 0), dt)), (2, np.zeros((0, 0), dt))],
                visible=False)
            return
        if l.style:                                         # 样式合进像素
            from .styles import pad
            v = doc._view(l, opacity=1.0, blend="normal", visible=True, parent=None, clip=False, mask_enabled=False)
            p = pad(l.style)
            x, y, w, h = l.x - p, l.y - p, l.w + 2 * p, l.h + 2 * p
            px = doc.compose_rect([v], x, y, w, h, dt)
            mk = None if l.mask is None else crop_canvas(l.mask, -p, -p, w, h, 0)
        else:
            px = convert(l.pixels, dt).copy()
            if l.fill < 1:
                px[..., 3] = (px[..., 3].astype(np.float64) * l.fill).astype(dt)
            x, y, w, h = l.x, l.y, l.w, l.h
            mk = l.mask
        chans = [(-1, px[..., 3]), (0, px[..., 0]), (1, px[..., 1]), (2, px[..., 2])]
        minfo = None
        if mk is not None:
            minfo = (y, x, y + h, x + w, mk, 0)
            chans.append((-2, convert(mk, dt)))
        extra = b""
        add(name, y, x, y + h, x + w, chans, BLEND_KEYS.get(l.blend, b"norm"), l.opacity * 255,
            1 if l.clip else 0, l.visible, extra, minfo)

    # 组：在组的第一个后代前面插一个“组结束”分隔层（PSD 里从下往上：分隔层、子图层、组头）
    starts: dict[int, list] = {}
    for g in doc.layers:
        if g.kind == "group":
            a, b = doc.unit(g.id)
            starts.setdefault(a, []).append(g)
    for i, l in enumerate(doc.layers):
        for g in reversed(starts.get(i, [])):
            if doc.unit(g.id)[0] == i and doc.unit(g.id)[1] - 1 > i:
                z = np.zeros((0, 0), dt)
                add("</Layer group>", 0, 0, 0, 0, [(-1, z), (0, z), (1, z), (2, z)],
                    extra=_block(b"lsct", struct.pack(">I", 3)))
        if l.kind == "group":
            a, b = doc.unit(l.id)
            if b - 1 == a:                                   # 空组：也要有分隔层
                z = np.zeros((0, 0), dt)
                add("</Layer group>", 0, 0, 0, 0, [(-1, z), (0, z), (1, z), (2, z)],
                    extra=_block(b"lsct", struct.pack(">I", 3)))
            z = np.zeros((0, 0), dt)
            chans = [(-1, z), (0, z), (1, z), (2, z)]
            minfo = None
            if l.mask is not None:
                minfo = (0, 0, doc.height, doc.width, l.mask, 255)
                chans.append((-2, convert(l.mask, dt)))
            lsct = struct.pack(">I", 2 if l.collapsed else 1) + b"8BIM" + BLEND_KEYS.get(l.blend, b"pass")
            add(l.name, 0, 0, 0, 0, chans, BLEND_KEYS.get(l.blend, b"pass"), l.opacity * 255, 1 if l.clip else 0,
                l.visible, _block(b"lsct", lsct), minfo)
        else:
            emit(l, i)
    layer_info = struct.pack(">h", -len(records)) + b"".join(records) + b"".join(chdata)
    if len(layer_info) % 2:
        layer_info += b"\0"
    lm = struct.pack(">I", len(layer_info)) + layer_info + struct.pack(">I", 0)
    lm_section = struct.pack(">I", len(lm)) + lm
    res = b""
    if doc.source.icc:
        icc = doc.source.icc
        res += b"8BIM" + struct.pack(">H", 1039) + b"\0\0" + struct.pack(">I", len(icc)) + icc + (b"\0" if len(icc) % 2 else b"")
    comp = doc.render_full(dt)
    planes = b"".join(np.ascontiguousarray(comp[..., c].astype(">u2" if depth == 16 else np.uint8)).tobytes()
                      for c in range(4))
    hdr = b"8BPS" + struct.pack(">H", 1) + b"\0" * 6 + struct.pack(">HIIHH", 4, doc.height, doc.width, depth, 3)
    data = hdr + struct.pack(">I", 0) + struct.pack(">I", len(res)) + res + lm_section + struct.pack(">H", 0) + planes
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    import os
    os.replace(tmp, path)
    return len(data)


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------

class _R:
    def __init__(self, b: bytes):
        self.b, self.p = b, 0

    def read(self, n):
        v = self.b[self.p:self.p + n]
        if len(v) < n:
            raise PSDError(_t("PSD 文件不完整"))
        self.p += n
        return v

    def u8(self):
        return self.read(1)[0]

    def u16(self):
        return struct.unpack(">H", self.read(2))[0]

    def i16(self):
        return struct.unpack(">h", self.read(2))[0]

    def u32(self):
        return struct.unpack(">I", self.read(4))[0]

    def i32(self):
        return struct.unpack(">i", self.read(4))[0]


def _decode(raw: bytes, comp: int, h: int, w: int, depth: int, big: bool = False) -> np.ndarray:
    from PIL import Image
    bpp = 2 if depth == 16 else 1
    n = h * w * bpp
    if h == 0 or w == 0:
        return np.zeros((h, w), np.uint16 if depth == 16 else np.uint8)
    if comp == 0:
        data = raw[:n]
    elif comp == 1:
        cnt = 4 if big else 2
        body = raw[h * cnt:]
        data = Image.frombytes("L", (w * bpp, h), body, "packbits", "L").tobytes()
    elif comp in (2, 3):
        data = zlib.decompress(raw)
        if comp == 3:                                       # 带预测：每行按像素差分
            a = np.frombuffer(data, ">u2" if depth == 16 else np.uint8).reshape(h, w).copy()
            a = np.cumsum(a, axis=1, dtype=np.uint32 if depth == 16 else np.uint16)
            a = (a & (0xFFFF if depth == 16 else 0xFF)).astype(">u2" if depth == 16 else np.uint8)
            data = a.tobytes()
    else:
        raise PSDError(_t('不支持的压缩方式：{0}', comp))
    a = np.frombuffer(data, ">u2" if depth == 16 else np.uint8, count=h * w).reshape(h, w)
    return a.astype(np.uint16 if depth == 16 else np.uint8)


def read(path: str):
    """读 PSD → Document。"""
    from .document import Document, Layer, SourceInfo
    with open(path, "rb") as f:
        b = f.read()
    r = _R(b)
    if r.read(4) != b"8BPS":
        raise PSDError(_t("不是 PSD 文件"))
    ver = r.u16()
    big = ver == 2
    r.read(6)
    nch, H, W, depth, mode = r.u16(), r.u32(), r.u32(), r.u16(), r.u16()
    if mode not in (1, 3) or depth not in (8, 16):
        raise PSDError(_t("只支持 8 / 16 位的 RGB（或灰度）PSD"))
    if not (0 < W <= (300000 if big else 30000) and 0 < H <= (300000 if big else 30000)) or nch < 1:
        raise PSDError(_t("PSD 文件头损坏（尺寸 {0} × {1}）", W, H))        # 规范上限：PSD 3 万、PSB 30 万像素
    r.read(r.u32())                                         # 颜色模式数据
    icc = None
    end = r.u32() + r.p
    while r.p < end:
        if r.read(4) != b"8BIM":
            break
        rid = r.u16()
        nl = r.u8()
        r.read(nl + ((nl + 1) % 2))
        size = r.u32()
        data = r.read(size)
        if size % 2:
            r.read(1)
        if rid == 1039:
            icc = data
    r.p = end
    lm_len = r.u64() if big else r.u32()
    lm_end = r.p + lm_len
    doc = Document(W, H, 16 if depth == 16 else 8)
    dt = np.uint16 if depth == 16 else np.uint8
    layers = []
    if lm_len:
        li_len = r.u64() if big else r.u32()
        li_end = r.p + li_len
        if li_len:
            count = abs(r.i16())
            recs = []
            for _ in range(count):
                top, left, bottom, right = r.i32(), r.i32(), r.i32(), r.i32()
                nc = r.u16()
                chans = [(r.i16(), r.u64() if big else r.u32()) for _ in range(nc)]
                r.read(4)
                blend = r.read(4)
                opacity, clip, flags, _ = r.u8(), r.u8(), r.u8(), r.u8()
                ex_end = r.u32() + r.p
                mlen = r.u32()
                mask_rect = None
                if mlen:
                    mb = r.read(mlen)
                    mt, ml, mbt, mr = struct.unpack(">iiii", mb[:16])
                    mask_rect = (mt, ml, mbt, mr, mb[16], mb[17] if len(mb) > 17 else 0)
                r.read(r.u32())                              # 混合范围
                nl = r.u8()
                name = r.read(nl).decode("latin-1")
                r.read((-(nl + 1)) % 4)
                lsct, fill = None, 1.0
                while r.p + 12 <= ex_end:
                    sig = r.read(4)
                    if sig not in (b"8BIM", b"8B64"):
                        break
                    key = r.read(4)
                    ln = r.u32()
                    data = r.read(ln)
                    if ln % 2 and r.p < ex_end:
                        pass
                    if key == b"luni" and len(data) >= 4:
                        n = struct.unpack(">I", data[:4])[0]
                        name = data[4:4 + n * 2].decode("utf-16-be", "replace")
                    elif key in (b"lsct", b"lsdk"):
                        lsct = struct.unpack(">I", data[:4])[0]
                    elif key == b"iOpa":
                        fill = data[0] / 255
                r.p = ex_end
                recs.append(dict(rect=(top, left, bottom, right), chans=chans, blend=blend, opacity=opacity,
                                 clip=clip, flags=flags, mask=mask_rect, name=name, lsct=lsct, fill=fill))
            for rec in recs:                                  # 通道数据
                top, left, bottom, right = rec["rect"]
                h, w = max(0, bottom - top), max(0, right - left)
                data = {}
                for cid, ln in rec["chans"]:
                    raw = r.read(ln)
                    comp = struct.unpack(">H", raw[:2])[0]
                    if cid == -2 and rec["mask"] is not None:
                        mt, ml, mbt, mr, _, _ = rec["mask"]
                        data[cid] = _decode(raw[2:], comp, max(0, mbt - mt), max(0, mr - ml), depth, big)
                    elif cid in (-1, 0, 1, 2):
                        data[cid] = _decode(raw[2:], comp, h, w, depth, big)
                rec["data"] = data
            r.p = li_end
            layers = recs
    r.p = lm_end
    stack: list = []
    out = []
    for rec in layers:                                      # 自下而上
        if rec["lsct"] == 3:
            stack.append([])
            continue
        top, left, bottom, right = rec["rect"]
        h, w = max(0, bottom - top), max(0, right - left)
        if rec["lsct"] in (1, 2):
            g = Layer("group", rec["name"])
            g.collapsed = rec["lsct"] == 2
            kids = stack.pop() if stack else []
            for k in kids:
                if k.parent is None:
                    k.parent = g.id
            l = g
            items = kids + [g]
        else:
            if h == 0 or w == 0:
                continue
            d = rec["data"]
            z = np.zeros((h, w), dt)
            full = np.full((h, w), 65535 if depth == 16 else 255, dt)
            px = np.dstack([d.get(0, z), d.get(1, d.get(0, z)), d.get(2, d.get(0, z)), d.get(-1, full)])
            l = Layer("pixel", rec["name"], np.ascontiguousarray(px), left, top)
            items = [l]
        l.blend = BLEND_FROM.get(rec["blend"], "normal")
        if l.kind != "group" and l.blend == "pass_through":
            l.blend = "normal"
        l.opacity = rec["opacity"] / 255
        l.visible = not (rec["flags"] & 2)
        l.clip = bool(rec["clip"])
        l.fill = rec["fill"]
        if rec["mask"] is not None and -2 in rec["data"]:
            mt, ml, mbt, mr, dflt, _ = rec["mask"]
            m = rec["data"][-2]
            m8 = (m >> 8).astype(np.uint8) if m.dtype == np.uint16 else m
            if l.kind == "group":
                cm = np.full((H, W), dflt, np.uint8)
                from .document import crop_canvas
                cm = crop_canvas(m8, -ml, -mt, W, H, dflt)
                l.mask = cm
            else:
                from .document import crop_canvas
                l.mask = crop_canvas(m8, l.x - ml, l.y - mt, l.w, l.h, dflt)
        if stack:
            stack[-1].extend(items)
        else:
            out.extend(items)
    for l in out:
        doc.layers.append(doc._register(l))
    if not doc.layers:                                      # 没有图层：读合成图
        r.p = lm_end
        comp = r.u16()
        rest = b[r.p:]
        bpp = 2 if depth == 16 else 1
        planes = []
        if comp == 1:
            cnt = (4 if big else 2) * H * nch
            body = rest[cnt:]
            from PIL import Image
            data = Image.frombytes("L", (W * bpp, H * nch), body, "packbits", "L").tobytes()
        elif comp == 0:
            data = rest[:W * H * bpp * nch]
        else:
            data = zlib.decompress(rest)
        a = np.frombuffer(data, ">u2" if depth == 16 else np.uint8, count=W * H * nch).reshape(nch, H, W).astype(dt)
        ncolor = 1 if mode == 1 else 3                      # 灰度：1 个颜色通道（第 2 个是透明度）；RGB：3 个
        for c in range(3):
            planes.append(a[min(c, ncolor - 1, nch - 1)])
        planes.append(a[ncolor] if nch > ncolor else np.full((H, W), 65535 if depth == 16 else 255, dt))
        doc.layers.append(doc._register(Layer("pixel", _t("背景"), np.ascontiguousarray(np.dstack(planes)))))
    doc.active_id = doc.layers[-1].id
    doc.source = SourceInfo(path=path, icc=icc)
    import os
    doc.source.name = os.path.basename(path)
    return doc


def _u64(self):
    return struct.unpack(">Q", self.read(8))[0]


_R.u64 = _u64
