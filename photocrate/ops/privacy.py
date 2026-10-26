"""分享前隐私体检：导出前找出照片里可能泄露隐私的地方，确认后只在导出的副本上打码（原片不动）。

- 照片里的文字（用已有的文字识别结果；没识别过、模型又在的，当场识别并缓存）：手机号 / 座机、身份证号（核对出生日期，
  校验位对得上的标“身份证号”，对不上的标“疑似”）、银行卡号（Luhn 校验）、车牌、快递面单（单号、收件人 / 寄件人、取件码）、
  门牌地址、邮箱。命中的整行文字打码（宁可多遮一点）；
- 二维码 / 条形码（OpenCV 自带的检测器）：收款码、健康码、快递单条码…；
- 人脸：不认识、又不是画面主角的脸算“路人”，默认打码；主要人物的脸也列出来，默认不打。
区域一律是转正之后的相对坐标（0–1），导出时读到的原图同样是转正的，所以对得上。纯计算，不碰 Qt。
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Callable

from ..i18n import t as _t

KINDS = {
    "phone": "手机号 / 电话", "idcard": "身份证号", "bank": "银行卡号", "plate": "车牌", "express": "快递面单",
    "address": "门牌地址", "email": "邮箱", "qr": "二维码", "barcode": "条形码", "stranger": "路人的脸", "face": "人脸",
}
STYLES = [("mosaic", _t("马赛克")), ("blur", _t("模糊")), ("block", _t("色块"))]
SCAN_SIDE = 1600
KNOWN_MIN_PHOTOS = 5          # 没起名字、但在这么多张照片里出现过的人，也当作“认识的人”
MAIN_FACE = 0.4               # 面积不小于画面里最大那张脸的 40%：算主要人物


@dataclass
class Finding:
    kind: str
    label: str                  # 显示用（号码中间打了星号）
    box: tuple                  # (x0, y0, x1, y1)，0–1
    on: bool = True             # 默认要不要打码

    @property
    def kind_name(self) -> str:
        return _t(KINDS.get(self.kind, self.kind))


# ---------------------------------------------------------------------------
# 文字
# ---------------------------------------------------------------------------

_PROV = "京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼"
RE_MOBILE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d(?:[-\s]?(?:\d|\*){4}){2}(?!\d)")
RE_LANDLINE = re.compile(r"(?<!\d)0\d{2,3}[-－—]\d{7,8}(?!\d)")
RE_ID = re.compile(r"(?<![0-9A-Za-z])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9A-Za-z])")
RE_CARD = re.compile(r"(?<!\d)(?:62|4\d|5[1-5])\d{14,17}(?!\d)")
RE_PLATE = re.compile(rf"[{_PROV}][A-HJ-NP-Z][·•.\s-]?[A-HJ-NP-Z0-9]{{4,5}}[A-HJ-NP-Z0-9挂学警港澳]")
RE_EXPRESS_WORD = re.compile(r"运单号|快递单号|快件单号|单号[:：]|收件人|寄件人|收货人|发件人|取件码|提货码|取货码")
RE_TRACKING = re.compile(r"(?<![A-Z0-9])(?:SF\d{12,15}|JT\d{13}|YT\d{13,15}|JD[A-Z0-9]{10,15}|E[A-Z]\d{9}CN|7[358]\d{12,13})(?![A-Z0-9])")
RE_ADDRESS = re.compile(r"(?:省|市|区|县|镇|乡|街道|大道|路|街|巷|弄|村|小区|花园|大厦|公寓|社区)[^，。,；;\s]{0,16}?\d+\s*(?:号|栋|幢|座|单元|室|楼|层)"
                        r"|\d+\s*(?:栋|幢|号楼)\s*\d*\s*(?:单元)?\s*\d+\s*(?:室|号)")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_ID_W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]


def id_checksum_ok(s: str) -> bool:
    s = s.upper()
    if len(s) != 18 or not s[:17].isdigit():
        return False
    return "10X98765432"[sum(int(c) * w for c, w in zip(s[:17], _ID_W)) % 11] == s[17]


def luhn_ok(s: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(s)):
        d = int(c)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mask(s: str, keep: int = 3, tail: int = 4) -> str:
    s = s.strip()
    if len(s) <= keep + tail:
        return s[:1] + "*" * max(1, len(s) - 1)
    return s[:keep] + "*" * (len(s) - keep - tail) + s[-tail:]


def text_hits(text: str) -> list[tuple[str, str]]:
    """一行文字 → [(类型, 打码后的显示文字)]。数字中间的空格先去掉再找（OCR 常把号码拆开）。"""
    out: list[tuple[str, str]] = []
    compact = re.sub(r"(?<=\d)[\s-](?=\d)", "", text)
    for m in RE_ID.finditer(compact):
        v = m.group(0)
        out.append(("idcard", mask(v, 4, 4) + ("" if id_checksum_ok(v) else _t("（疑似）"))))
    for m in RE_CARD.finditer(compact):
        v = m.group(0)
        if luhn_ok(v) and not RE_ID.fullmatch(v):
            out.append(("bank", mask(v, 4, 4)))
    for m in RE_MOBILE.finditer(text):
        out.append(("phone", mask(re.sub(r"[\s-]", "", m.group(0)))))
    for m in RE_LANDLINE.finditer(text):
        out.append(("phone", mask(m.group(0), 4, 2)))
    for m in RE_PLATE.finditer(text.upper()):
        out.append(("plate", mask(m.group(0), 2, 1)))
    if RE_EXPRESS_WORD.search(text) or RE_TRACKING.search(compact.upper()):
        out.append(("express", mask(text, 4, 2)))
    for m in RE_ADDRESS.finditer(text):
        out.append(("address", mask(m.group(0), 3, 1)))
    for m in RE_EMAIL.finditer(text):
        v = m.group(0)
        out.append(("email", v[:2] + "***" + v[v.index("@"):]))
    seen, uniq = set(), []
    for k, lab in out:                        # 同一行只按类型各报一次
        if k not in seen:
            seen.add(k)
            uniq.append((k, lab))
    return uniq


def _quad_box(b) -> tuple:
    xs, ys = b[0::2], b[1::2]
    return (max(0.0, min(xs)), max(0.0, min(ys)), min(1.0, max(xs)), min(1.0, max(ys)))


def scan_text(lines: list[dict]) -> list[Finding]:
    out = []
    for ln in lines or []:
        text = ln.get("t") or ""
        b = ln.get("b")
        if not text or not b:
            continue
        for kind, label in text_hits(text):
            out.append(Finding(kind, label, _quad_box(b)))
    return out


# ---------------------------------------------------------------------------
# 二维码 / 条形码
# ---------------------------------------------------------------------------

def _pts_box(pts, w: int, h: int, pad: float = 0.08) -> tuple:
    import numpy as np
    p = np.asarray(pts, np.float32).reshape(-1, 2)
    x0, y0 = p.min(axis=0)
    x1, y1 = p.max(axis=0)
    dx, dy = (x1 - x0) * pad, (y1 - y0) * pad
    return (max(0.0, (x0 - dx) / w), max(0.0, (y0 - dy) / h), min(1.0, (x1 + dx) / w), min(1.0, (y1 + dy) / h))


def scan_codes(rgb) -> list[Finding]:
    import cv2
    import numpy as np
    gray = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2GRAY)
    h, w = gray.shape[:2]
    out = []
    try:
        ok, pts = cv2.QRCodeDetector().detectMulti(gray)
        if ok and pts is not None:
            for p in pts:
                out.append(Finding("qr", _t("二维码"), _pts_box(p, w, h)))
    except cv2.error:
        pass
    make = getattr(getattr(cv2, "barcode", None), "BarcodeDetector", None) or getattr(cv2, "barcode_BarcodeDetector", None)
    if make is not None:
        try:
            res = make().detect(gray)
            ok, pts = res[0], res[1]
            if ok and pts is not None:
                for p in pts:
                    box = _pts_box(p, w, h, 0.12)
                    if not any(_overlap(box, f.box) > 0.5 for f in out):
                        out.append(Finding("barcode", _t("条形码"), box))
        except cv2.error:
            pass
    return out


def _overlap(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / area if area > 0 else 0.0


# ---------------------------------------------------------------------------
# 人脸
# ---------------------------------------------------------------------------

def face_owners(lib) -> tuple[dict[str, object], set[str]]:
    """人脸键（主图指纹#序号）→ 所属的人；以及“不是真人的脸”的键。"""
    ai = getattr(lib, "ai", None)
    owner = {}
    for c in getattr(ai, "people", None) or []:
        for f in c.faces:
            owner[f.key] = c
    nonface = {f.key for f in getattr(ai, "nonfaces", None) or []}
    return owner, nonface


def scan_faces(g, faces: list[dict], owner: dict, nonface: set[str]) -> list[Finding]:
    out = []
    sig = g.primary_file.sig
    areas = [float(f["box"][2]) * float(f["box"][3]) for f in faces]
    biggest = max(areas, default=0.0)
    for i, f in enumerate(faces):
        key = f"{sig}#{i}"
        if key in nonface:
            continue
        x, y, w, h = (float(v) for v in f["box"])
        c = owner.get(key)
        known = c is not None and (c.named or len(c.gids) >= KNOWN_MIN_PHOTOS)
        main = biggest > 0 and areas[i] >= MAIN_FACE * biggest
        pad_x, pad_y = w * 0.15, h * 0.2
        box = (max(0.0, x - pad_x), max(0.0, y - pad_y), min(1.0, x + w + pad_x), min(1.0, y + h + pad_y))
        if known or main:
            label = c.name if c is not None and c.named else _t("主要人物")
            out.append(Finding("face", label, box, on=False))
        else:
            out.append(Finding("stranger", _t("路人"), box, on=True))
    return out


# ---------------------------------------------------------------------------
# 一张照片 / 一批
# ---------------------------------------------------------------------------

def scan_group(g, lib, owner=None, nonface=None) -> list[Finding]:
    """一张照片的全部发现。需要时当场做文字识别（结果写进 feats 和缓存，下次搜索也能用）。"""
    import numpy as np
    from ..core.imaging import load_preview
    if owner is None or nonface is None:
        owner, nonface = face_owners(lib)
    im = load_preview(g.primary, g.primary_file.role, SCAN_SIDE, lib.exiftool_path)[0]
    rgb = np.asarray(im.convert("RGB"))
    out: list[Finding] = []
    lines = g.feats.get("ocr")
    ocr = getattr(lib, "ocr", None)
    if lines is None and ocr is not None and ocr.ready():
        try:
            from ..ai.ocrindex import OCR_KEY
            lines = ocr.engine().read(np.ascontiguousarray(rgb[..., ::-1]))
            g.feats["ocr"] = lines
            lib.cache.put_ocr_many({g.primary_file.sig: lines}, OCR_KEY)
        except Exception:
            lines = None
    out += scan_text(lines or [])
    out += scan_codes(rgb)
    fd = g.feats.get("faces")
    faces = (fd or {}).get("faces")
    if faces is None:
        from ..ai import faces as aif
        eng = aif.engine()
        if eng is not None:
            small = np.asarray(im.convert("RGB").resize((max(1, im.width * 1024 // max(im.size)),
                                                         max(1, im.height * 1024 // max(im.size)))))
            faces = eng.detect(small, 40)
    out += scan_faces(g, faces or [], owner, nonface)
    return out


def scan(groups, lib, progress: Callable[[int, int, str], None] | None = None,
         cancel: threading.Event | None = None) -> dict[int, list[Finding]]:
    """一批照片 → {gid: 发现}（没有发现的不列）。"""
    owner, nonface = face_owners(lib)
    todo = [g for g in groups if g.files and not g.is_video]
    out: dict[int, list[Finding]] = {}
    for i, g in enumerate(todo):
        if cancel is not None and cancel.is_set():
            break
        if progress:
            progress(i, len(todo), g.name)
        try:
            found = scan_group(g, lib, owner, nonface)
        except Exception:
            found = []
        if found:
            out[g.gid] = found
    if progress:
        progress(len(todo), len(todo), "")
    return out


def needs_text_model(lib) -> bool:
    ocr = getattr(lib, "ocr", None)
    return ocr is None or not ocr.ready()


# ---------------------------------------------------------------------------
# 打码
# ---------------------------------------------------------------------------

def redact(im, boxes, style: str = "mosaic"):
    """PIL 图（RGB）上把这些区域打码，返回新图。boxes：[(x0, y0, x1, y1)]，0–1。"""
    from PIL import Image, ImageDraw, ImageFilter
    out = im.copy()
    W, H = out.size
    for x0, y0, x1, y1 in boxes:
        pad = 0.02 * max(x1 - x0, y1 - y0)
        l, t = max(0, int((x0 - pad) * W) - 2), max(0, int((y0 - pad) * H) - 2)
        r, b = min(W, int((x1 + pad) * W) + 2), min(H, int((y1 + pad) * H) + 2)
        if r - l < 2 or b - t < 2:
            continue
        region = out.crop((l, t, r, b))
        if style == "block":
            ImageDraw.Draw(out).rectangle((l, t, r - 1, b - 1), fill=(24, 24, 24))
            continue
        if style == "blur":
            rad = max(6, min(r - l, b - t) // 3)
            region = region.filter(ImageFilter.GaussianBlur(rad)).filter(ImageFilter.GaussianBlur(rad))
        else:
            cell = max(6, min(r - l, b - t) // 6, max(W, H) // 160)
            small = region.resize((max(1, (r - l) // cell), max(1, (b - t) // cell)), Image.Resampling.BILINEAR)
            region = small.resize(region.size, Image.Resampling.NEAREST)
        out.paste(region, (l, t))
    return out
