"""色彩管理：校样（软打样）、色域警告、转换到另一个配置文件、导出 CMYK。用 Pillow 自带的 LittleCMS。

- 系统里能找到的 ICC 配置文件（Windows：System32\\spool\\drivers\\color，自带 RSWOP.icm = 美国卷筒纸胶印 CMYK）。
- 软打样：sRGB → 校样配置文件 → sRGB 的往返变换（LittleCMS 的 proof transform），显示“印出来大概是什么样”。
- 色域警告：往返之后颜色变化明显（ΔE 近似 > 阈值）的像素标成灰色。
- 转换配置文件：LittleCMS 只收 8 位，所以先用 8 位的 33³ 网格算一张三维查找表，再对 8 / 16 位的像素做三线性插值。
"""
from __future__ import annotations

import glob
import io
import os
import sys

import numpy as np

from ..i18n import t as _t

INTENTS = [(0, _t("可感知")), (1, _t("相对比色")), (2, _t("饱和度")), (3, _t("绝对比色"))]


def _cms():
    from PIL import ImageCms
    return ImageCms


def srgb():
    C = _cms()
    return C.ImageCmsProfile(C.createProfile("sRGB"))


def system_profiles() -> list[tuple[str, str, str]]:
    """[(路径, 描述, 色彩空间 RGB / CMYK / GRAY…)]"""
    dirs = []
    if sys.platform.startswith("win"):
        dirs.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "spool", "drivers", "color"))
    else:
        dirs += ["/usr/share/color/icc", "/usr/local/share/color/icc", os.path.expanduser("~/.local/share/icc"),
                 "/Library/ColorSync/Profiles", "/System/Library/ColorSync/Profiles"]
    out = []
    C = _cms()
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, "*.ic[cm]")) + glob.glob(os.path.join(d, "**", "*.ic[cm]"), recursive=True)):
            try:
                pr = C.getOpenProfile(p)
                out.append((p, C.getProfileDescription(pr).strip() or os.path.basename(p),
                            pr.profile.xcolor_space.strip()))
            except Exception:
                continue
    seen, uniq = set(), []
    for item in out:
        if item[0] not in seen:
            seen.add(item[0])
            uniq.append(item)
    return uniq


def open_profile(src):
    """路径 / ICC 字节 / None（= sRGB）→ ImageCmsProfile。"""
    C = _cms()
    if src is None:
        return srgb()
    if isinstance(src, (bytes, bytearray)):
        return C.ImageCmsProfile(io.BytesIO(bytes(src)))
    return C.ImageCmsProfile(src)


def profile_space(src) -> str:
    try:
        return open_profile(src).profile.xcolor_space.strip()
    except Exception:
        return "RGB"


def profile_bytes(src) -> bytes:
    return open_profile(src).tobytes()


def profile_name(src) -> str:
    try:
        return _cms().getProfileDescription(open_profile(src)).strip()
    except Exception:
        return "?"


class Proof:
    """显示用的校样变换（缓存起来，画布每帧调用）。"""

    def __init__(self, proof_src, intent: int = 1, src=None):
        C = _cms()
        self.space = profile_space(proof_src)
        rgb = open_profile(src)
        pr = open_profile(proof_src)
        self.tf = C.buildProofTransform(rgb, srgb(), pr, "RGB", "RGB", renderingIntent=0,
                                        proofRenderingIntent=int(intent), flags=C.Flags.SOFTPROOFING)
        mode = "CMYK" if self.space == "CMYK" else "RGB"
        self.to_dev = C.buildTransform(rgb, pr, "RGB", mode, renderingIntent=int(intent))
        self.from_dev = C.buildTransform(pr, srgb(), mode, "RGB", renderingIntent=1)

    def apply(self, rgba8: np.ndarray, gamut: bool = False) -> np.ndarray:
        from PIL import Image
        C = _cms()
        rgb = np.ascontiguousarray(rgba8[..., :3])
        im = Image.fromarray(rgb, "RGB")
        out = np.array(C.applyTransform(im, self.tf), dtype=np.uint8)
        if gamut:
            back = np.array(C.applyTransform(C.applyTransform(im, self.to_dev), self.from_dev), dtype=np.int16)
            diff = np.abs(back - rgb.astype(np.int16)).max(axis=-1)
            out[diff > 14] = (128, 128, 128)
        res = rgba8.copy()
        res[..., :3] = out
        return res


def _lut(src, dst, intent: int, n: int = 33) -> np.ndarray:
    """src → dst 的三维查找表 (n, n, n, 3)，float 0–1。"""
    from PIL import Image
    C = _cms()
    g = np.linspace(0, 255, n)
    r, gg, b = np.meshgrid(g, g, g, indexing="ij")
    grid = np.stack([r, gg, b], -1).reshape(1, -1, 3)
    im = Image.fromarray(np.round(grid).astype(np.uint8), "RGB")
    tf = C.buildTransform(open_profile(src), open_profile(dst), "RGB", "RGB", renderingIntent=int(intent))
    out = np.array(C.applyTransform(im, tf), dtype=np.float32).reshape(n, n, n, 3) / 255
    return out


def apply_lut(f: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """三线性插值：f 为 (..., 3) float 0–1。"""
    n = lut.shape[0] - 1
    x = np.clip(f, 0, 1) * n
    i0 = np.minimum(x.astype(np.int32), n - 1)
    t = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    tr, tg, tb = t[..., 0:1], t[..., 1:2], t[..., 2:3]
    out = 0
    for dr in (0, 1):
        wr = tr if dr else 1 - tr
        for dg in (0, 1):
            wg = tg if dg else 1 - tg
            for db in (0, 1):
                wb = tb if db else 1 - tb
                out = out + lut[r0 + dr, g0 + dg, b0 + db] * (wr * wg * wb)
    return out.astype(np.float32)


def convert_doc(doc, target, intent: int = 0) -> None:
    """编辑 → 转换为配置文件：所有像素图层的颜色从文档现在的配置文件换到 target，并把 target 记为文档的配置文件。"""
    from .document import from_float, to_float
    lut = _lut(doc.source.icc, target, intent)
    tb = profile_bytes(target)
    with doc.edit(_t('转换为配置文件「{0}」', profile_name(target))):
        for l in doc.layers:
            if l.pixels is None or l.canvas_sized:
                continue
            f = to_float(l.pixels)
            out = np.empty_like(l.pixels)
            for y0 in range(0, f.shape[0], 512):
                ff = f[y0:y0 + 512]
                res = ff.copy()
                res[..., :3] = apply_lut(ff[..., :3], lut)
                out[y0:y0 + 512] = from_float(res, l.pixels.dtype)
            l.pixels = out
            if l.kind in ("text", "shape", "smart"):
                l.kind = "pixel"
            l.touch()
        doc.source.icc = tb


def to_cmyk_image(rgb8: np.ndarray, src_icc, cmyk_profile, intent: int = 0):
    """导出 CMYK 用：返回 (PIL CMYK 图, CMYK 配置文件字节)。"""
    from PIL import Image
    C = _cms()
    im = Image.fromarray(np.ascontiguousarray(rgb8[..., :3]), "RGB")
    pr = open_profile(cmyk_profile)
    out = C.profileToProfile(im, open_profile(src_icc), pr, renderingIntent=int(intent), outputMode="CMYK")
    return out, pr.tobytes()
