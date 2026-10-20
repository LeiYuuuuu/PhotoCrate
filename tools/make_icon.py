"""生成程序图标 assets/app.png 与 assets/app.ico（原创图形：切角黄块 + 取景框角标 + 斜切光带）。"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

YELLOW = (255, 225, 0, 255)
INK = (21, 21, 21, 255)


def draw(size: int) -> Image.Image:
    S = 4 * size
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = S * 0.04
    s = S - 2 * pad
    c = s * 0.28
    d.polygon([(pad, pad), (pad + s - c, pad), (pad + s, pad + c), (pad + s, pad + s), (pad, pad + s)], fill=YELLOW)
    w = max(2, int(s / 13))
    m, L = s * 0.22, s * 0.2
    for (x, y, dx, dy) in ((m, m, 1, 1), (s - m, s - m, -1, -1), (m, s - m, 1, -1)):
        x, y = x + pad, y + pad
        d.line([(x, y), (x + dx * L, y)], fill=INK, width=w)
        d.line([(x, y), (x, y + dy * L)], fill=INK, width=w)
    d.polygon([(pad + s * .40, pad + s * .62), (pad + s * .62, pad + s * .40), (pad + s * .74, pad + s * .40),
               (pad + s * .52, pad + s * .62)], fill=INK)
    return im.resize((size, size), Image.Resampling.LANCZOS)


def main(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    draw(256).save(os.path.join(out_dir, "app.png"))
    big = draw(256)
    big.save(os.path.join(out_dir, "app.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon ok")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "assets"))
