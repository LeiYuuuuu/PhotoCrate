"""生成一套测试照片（各种日期/GPS/色调/模糊/RAW 配对/截图/微信命名），用于自测与演示。

用法：python tests/make_samples.py <输出目录> [RAW样例目录]
"""
from __future__ import annotations

import os
import random
import shutil
import sys
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import piexif


def textured(w, h, base_rgb, accent_rgb, seed, sharp=True):
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, h)[:, None, None]
    base = np.array(base_rgb, dtype=np.float32)[None, None, :]
    acc = np.array(accent_rgb, dtype=np.float32)[None, None, :]
    img = base * (1 - y) + acc * y
    img = np.repeat(img, w, axis=1)
    img += rng.normal(0, 6, (h, w, 3))
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    r = random.Random(seed)
    for _ in range(60):
        x0, y0 = r.randrange(w), r.randrange(h)
        s = r.randrange(10, 120)
        c = tuple(int(np.clip(v + r.randrange(-60, 60), 0, 255)) for v in (accent_rgb if r.random() < .5 else base_rgb))
        if r.random() < .5:
            d.rectangle([x0, y0, x0 + s, y0 + s // 2], outline=c, width=3)
        else:
            d.line([x0, y0, x0 + s, y0 + r.randrange(-s, s)], fill=c, width=2)
    if not sharp:
        im = im.filter(ImageFilter.GaussianBlur(9))
    return im


def deg(v):
    v = abs(v)
    d = int(v)
    m = int((v - d) * 60)
    s = round(((v - d) * 60 - m) * 60 * 100)
    return ((d, 1), (m, 1), (s, 100))


def exif_bytes(dt=None, gps=None, make=None, model=None, lens=None, focal=None, iso=None, orientation=1, focal35=None):
    zeroth, exif, gpsd = {}, {}, {}
    if make:
        zeroth[piexif.ImageIFD.Make] = make.encode()
    if model:
        zeroth[piexif.ImageIFD.Model] = model.encode()
    zeroth[piexif.ImageIFD.Orientation] = orientation
    if dt:
        s = dt.strftime("%Y:%m:%d %H:%M:%S").encode()
        exif[piexif.ExifIFD.DateTimeOriginal] = s
        exif[piexif.ExifIFD.DateTimeDigitized] = s
    if lens:
        exif[piexif.ExifIFD.LensModel] = lens.encode()
    if focal:
        exif[piexif.ExifIFD.FocalLength] = (int(focal * 10), 10)
    if focal35:
        exif[piexif.ExifIFD.FocalLengthIn35mmFilm] = int(focal35)
    if iso:
        exif[piexif.ExifIFD.ISOSpeedRatings] = iso
    if gps:
        lat, lon = gps
        gpsd = {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: deg(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: deg(lon),
        }
    return piexif.dump({"0th": zeroth, "Exif": exif, "GPS": gpsd})


PLACES = {
    "shaoyang": (27.2418, 111.4674), "hangzhou": (30.2590, 120.1650), "beijing": (39.9042, 116.4074),
    "kunming": (25.0389, 102.7183), "tokyo": (35.6895, 139.6917), "boca": (26.3683, -80.1289),
    "paris": (48.8566, 2.3522), "hongkong": (22.3027, 114.1772),
}
COLORS = {
    "warm": ((250, 150, 40), (200, 60, 30)),
    "cool": ((40, 110, 220), (20, 60, 120)),
    "green": ((60, 160, 70), (30, 90, 40)),
    "mono": ((200, 200, 200), (60, 60, 60)),
    "dark": ((30, 25, 40), (10, 10, 15)),
    "bright": ((250, 245, 235), (225, 230, 240)),
    "purple": ((140, 80, 200), (90, 40, 140)),
}
CAMS = [
    ("Apple", "iPhone 15 Pro", "iPhone 15 Pro back triple camera 6.86mm f/1.78", 6.86, 24),
    ("Canon", "Canon EOS R6", "RF24-105mm F4 L IS USM", 50, 50),
    ("SONY", "ILCE-7M4", "FE 85mm F1.8", 85, 85),
    ("Xiaomi", "Xiaomi 14", None, 5.0, 23),
    ("NIKON CORPORATION", "NIKON Z 6_2", "NIKKOR Z 24-70mm f/4 S", 24, 24),
]


def main(out: str, raw_dir: str | None = None) -> None:
    random.seed(1)
    os.makedirs(out, exist_ok=True)
    sub_a = os.path.join(out, "手机导出")
    sub_b = os.path.join(out, "相机SD卡", "DCIM")
    sub_c = os.path.join(out, "杂图")
    for d in (sub_a, sub_b, sub_c):
        os.makedirs(d, exist_ok=True)

    specs = [
        # (目录, 文件名, 色调, 地点, 日期, 相机idx, 清晰, 竖幅)
        (sub_a, "IMG_0001.JPG", "warm", "shaoyang", datetime(2025, 2, 1, 17, 30), 0, True, False),
        (sub_a, "IMG_0002.JPG", "warm", "shaoyang", datetime(2025, 2, 1, 18, 5), 0, True, True),
        (sub_a, "IMG_0003.JPG", "green", "kunming", datetime(2025, 4, 12, 9, 15), 0, True, False),
        (sub_a, "IMG_0004.JPG", "cool", "kunming", datetime(2025, 4, 12, 10, 0), 0, False, False),
        (sub_a, "IMG_0005.JPG", "cool", "hangzhou", datetime(2025, 7, 20, 14, 0), 3, True, False),
        (sub_a, "IMG_0006.JPG", "bright", "hangzhou", datetime(2025, 7, 20, 15, 30), 3, True, True),
        (sub_a, "IMG_0007.JPG", "dark", "beijing", datetime(2024, 12, 31, 23, 50), 3, True, False),
        (sub_a, "IMG_0008.JPG", "mono", "beijing", datetime(2024, 12, 30, 11, 0), 0, True, False),
        (sub_a, "IMG_0009.JPG", "warm", "boca", datetime(2026, 9, 5, 19, 10), 0, True, False),
        (sub_a, "IMG_0010.JPG", "cool", "boca", datetime(2026, 9, 6, 8, 0), 0, True, False),
        (sub_a, "IMG_0011.JPG", "purple", "hongkong", datetime(2026, 1, 3, 20, 0), 3, True, False),
        (sub_b, "DSC00101.JPG", "cool", "tokyo", datetime(2025, 10, 2, 13, 0), 2, True, False),
        (sub_b, "DSC00102.JPG", "warm", "tokyo", datetime(2025, 10, 2, 16, 40), 2, False, False),
        (sub_b, "DSC00103.JPG", "green", None, datetime(2025, 10, 3, 10, 0), 2, True, True),
        (sub_b, "_MG_2001.JPG", "bright", "paris", datetime(2024, 6, 18, 12, 0), 1, True, False),
        (sub_b, "_MG_2002.JPG", "mono", "paris", datetime(2024, 6, 18, 12, 30), 1, True, False),
        (sub_b, "_MG_2003.JPG", "dark", None, datetime(2024, 6, 19, 22, 0), 1, False, False),
        (sub_b, "DSC_3001.JPG", "green", "kunming", datetime(2025, 4, 13, 7, 0), 4, True, False),
    ]
    for i, (d, name, color, place, dt, cam, sharp, portrait) in enumerate(specs):
        w, h = (1200, 1600) if portrait else (1600, 1200)
        im = textured(w, h, *COLORS[color], seed=i, sharp=sharp)
        mk, md, lens, focal, f35 = CAMS[cam]
        ex = exif_bytes(dt, PLACES.get(place) if place else None, mk, md, lens, focal, random.choice([100, 400, 1600, 6400]), 1, f35)
        im.save(os.path.join(d, name), "JPEG", quality=88, exif=ex)

    # EXIF 方向=6（像素横放，显示为竖幅）
    im = textured(1600, 1200, *COLORS["warm"], seed=99)
    im.save(os.path.join(sub_a, "IMG_0012.JPG"), "JPEG", quality=88,
            exif=exif_bytes(datetime(2025, 2, 2, 9, 0), PLACES["shaoyang"], "Apple", "iPhone 15 Pro", orientation=6))

    # 微信 / 截图 / 无元数据
    textured(1080, 1440, *COLORS["warm"], seed=21).save(os.path.join(sub_c, "微信图片_20230815103045.jpg"), quality=85)
    textured(1080, 1920, *COLORS["cool"], seed=22).save(os.path.join(sub_c, "mmexport1696123456789.jpg"), quality=85)
    textured(1170, 2532, *COLORS["bright"], seed=23).save(os.path.join(sub_c, "Screenshot_2024-02-03-10-20-30.png"))
    textured(1920, 1080, *COLORS["bright"], seed=24).save(os.path.join(sub_c, "屏幕截图 2025-05-06 101112.png"))
    p = os.path.join(sub_c, "随手存的图.jpg")
    textured(800, 600, *COLORS["green"], seed=25).save(p, quality=85)
    t = datetime(2022, 3, 4, 5, 6).timestamp()
    os.utime(p, (t, t))

    # HEIC + AAE + Live Photo
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        im = textured(1600, 1200, *COLORS["cool"], seed=31)
        im.save(os.path.join(sub_a, "IMG_1234.HEIC"), quality=80,
                exif=exif_bytes(datetime(2026, 9, 6, 9, 30), PLACES["boca"], "Apple", "iPhone 15 Pro"))
        with open(os.path.join(sub_a, "IMG_O1234.AAE"), "w") as f:
            f.write("<plist/>")
        with open(os.path.join(sub_a, "IMG_1234.MOV"), "wb") as f:
            f.write(b"\x00" * 2048)
    except Exception as e:
        print("HEIC 生成失败：", e)

    # RAW + JPG 配对 + XMP
    if raw_dir and os.path.isdir(raw_dir):
        for src in ("iss030e122639.NEF", "RAW_CANON_40D_SRAW_V103.CR2"):
            sp = os.path.join(raw_dir, src)
            if os.path.exists(sp):
                shutil.copy2(sp, os.path.join(sub_b, src))
        stem = "iss030e122639"
        textured(1600, 1060, *COLORS["dark"], seed=41).save(
            os.path.join(sub_b, stem + ".JPG"), quality=85,
            exif=exif_bytes(datetime(2012, 3, 4, 17, 20, 59), None, "NIKON CORPORATION", "NIKON D3S"))
        with open(os.path.join(sub_b, stem + ".NEF.xmp"), "w", encoding="utf-8") as f:
            f.write('<x:xmpmeta xmlns:x="adobe:ns:meta/"></x:xmpmeta>')
    print("样例已生成：", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
