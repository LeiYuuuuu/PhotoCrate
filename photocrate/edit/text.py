"""文字图层：用 Pillow 把文字画成一块 RGBA。参数改了就重画，所以文字一直可以再编辑。

字体从系统字体文件夹里按文件名找（Windows 自带的中文字体都在），找不到时退回导出水印用的字体。
"""
from __future__ import annotations

import os
import sys

import numpy as np
from ..i18n import t as _t

# (显示名, 候选文件名)
FONT_FILES: list[tuple[str, list[str]]] = [
    ("微软雅黑", ["msyh.ttc", "msyh.ttf"]),
    ("微软雅黑 粗", ["msyhbd.ttc", "msyhbd.ttf"]),
    ("黑体", ["simhei.ttf"]),
    ("等线", ["Deng.ttf"]),
    ("等线 粗", ["Dengb.ttf"]),
    ("宋体", ["simsun.ttc"]),
    ("仿宋", ["simfang.ttf"]),
    ("楷体", ["simkai.ttf"]),
    ("Bahnschrift", ["bahnschrift.ttf"]),
    ("Arial", ["arial.ttf"]),
    ("Arial 粗", ["arialbd.ttf"]),
    ("Segoe UI", ["segoeui.ttf"]),
    ("Segoe UI 粗", ["segoeuib.ttf"]),
    ("Georgia", ["georgia.ttf"]),
    ("Times New Roman", ["times.ttf"]),
    ("Impact", ["impact.ttf"]),
    ("Consolas", ["consola.ttf"]),
    ("Noto Sans CJK", ["NotoSansCJK-Regular.ttc", "NotoSansCJKsc-Regular.otf"]),
    ("PingFang", ["PingFang.ttc"]),
]


def _font_dirs() -> list[str]:
    dirs = []
    if sys.platform.startswith("win"):
        dirs.append(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
        la = os.environ.get("LOCALAPPDATA")
        if la:
            dirs.append(os.path.join(la, "Microsoft", "Windows", "Fonts"))
    dirs += ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts"),
             "/usr/share/fonts/opentype/noto", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/wqy"]
    return [d for d in dirs if os.path.isdir(d)]


_cache: dict[str, str | None] = {}


def font_path(name: str) -> str | None:
    if name in _cache:
        return _cache[name]
    files = dict(FONT_FILES).get(name, [])
    found = None
    for d in _font_dirs():
        for f in files:
            p = os.path.join(d, f)
            if os.path.exists(p):
                found = p
                break
        if found:
            break
    _cache[name] = found
    return found


def available_fonts() -> list[str]:
    names = [n for n, _ in FONT_FILES if font_path(n)]
    return names or [_t("默认")]


def default_params() -> dict:
    fonts = available_fonts()
    return {"text": _t("输入文字"), "font": fonts[0], "size": 96, "color": "#FFFFFF", "align": "left",
            "spacing": 1.2, "stroke": 0, "stroke_color": "#000000", "shadow": False}


def _load_font(name: str, size: int):
    from PIL import ImageFont
    p = font_path(name)
    if p:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    from ..ops.exporter import _font
    return _font(size)


def _rgb(hexstr: str) -> tuple[int, int, int]:
    s = (hexstr or "#FFFFFF").lstrip("#")
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except (ValueError, IndexError):
        return 255, 255, 255


def render_text(params: dict) -> np.ndarray:
    """返回 HxWx4 uint8（左上角就是文字图层的位置）。"""
    from PIL import Image, ImageDraw, ImageFilter

    text = str(params.get("text") or " ")
    size = max(4, int(round(float(params.get("size") or 48))))
    font = _load_font(str(params.get("font") or ""), size)
    stroke = max(0, int(round(float(params.get("stroke") or 0))))
    spacing = max(0, int(round(size * (float(params.get("spacing") or 1.2) - 1))))
    align = params.get("align") if params.get("align") in ("left", "center", "right") else "left"
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    l, t, r, b = probe.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align=align, stroke_width=stroke)
    shadow = bool(params.get("shadow"))
    sh = max(2, size // 16) if shadow else 0
    pad = 2 + stroke + (sh * 3 if shadow else 0)
    w, h = int(r - l) + pad * 2 + sh, int(b - t) + pad * 2 + sh
    im = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    org = (pad - l, pad - t)
    if shadow:
        layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).multiline_text((org[0] + sh, org[1] + sh), text, font=font, fill=(0, 0, 0, 150),
                                             spacing=spacing, align=align, stroke_width=stroke,
                                             stroke_fill=(0, 0, 0, 150))
        im = Image.alpha_composite(im, layer.filter(ImageFilter.GaussianBlur(sh)))
    dr = ImageDraw.Draw(im)
    dr.multiline_text(org, text, font=font, fill=_rgb(params.get("color")) + (255,), spacing=spacing, align=align,
                      stroke_width=stroke, stroke_fill=_rgb(params.get("stroke_color")) + (255,))
    return np.array(im, dtype=np.uint8)          # 可写的副本（asarray 得到的是只读视图）
