"""测试用的 RAW：自己写一个最小的 DNG（未压缩 Bayer RGGB、12 位），LibRaw 能像真相机的 RAW 一样显影它。

传感器响应 = 线性 sRGB × 通道增益（gains），所以 DNG 里记录的「拍摄时白平衡」(AsShotNeutral) 就是 gains，
颜色矩阵 = diag(gains) · XYZ→线性 sRGB。显影后应该得到原来的颜色。
"""
from __future__ import annotations

import struct

import numpy as np

XYZ_TO_SRGB = np.array([[3.2404542, -1.5371385, -0.4985314],
                        [-0.9692660, 1.8760108, 0.0415560],
                        [0.0556434, -0.2040259, 1.0572252]])


def _ifd_entries(entries, data_start):
    """entries: [(tag, type, values)]，返回 (IFD 字节, 附加数据字节)。"""
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 10: 8}
    fmt = {1: "B", 2: "B", 3: "H", 4: "I"}
    out = struct.pack("<H", len(entries))
    extra = b""
    for tag, typ, vals in sorted(entries, key=lambda e: e[0]):
        if typ == 2:
            raw = vals.encode("ascii") + b"\0"
            count = len(raw)
        elif typ in (5, 10):
            count = len(vals)
            raw = b"".join(struct.pack("<ii" if typ == 10 else "<II", *v) for v in vals)
        else:
            count = len(vals)
            raw = struct.pack("<" + fmt[typ] * count, *vals)
        if len(raw) <= 4:
            out += struct.pack("<HHI", tag, typ, count) + raw.ljust(4, b"\0")
        else:
            out += struct.pack("<HHII", tag, typ, count, data_start + len(extra))
            extra += raw + (b"\0" if len(raw) % 2 else b"")
    out += struct.pack("<I", 0)
    return out, extra


def _rat(x: float, den: int = 10000) -> tuple[int, int]:
    return int(round(x * den)), den


def make_dng(path: str, linear_rgb: np.ndarray, gains=(0.55, 1.0, 0.72), white: int = 4095) -> str:
    """linear_rgb：H×W×3、0–1 的线性 sRGB（H、W 取偶数）。"""
    h, w = linear_rgb.shape[:2]
    h, w = h // 2 * 2, w // 2 * 2
    cam = np.clip(linear_rgb[:h, :w] * np.asarray(gains), 0, 1)
    mos = np.empty((h, w), np.float64)
    mos[0::2, 0::2] = cam[0::2, 0::2, 0]
    mos[0::2, 1::2] = cam[0::2, 1::2, 1]
    mos[1::2, 0::2] = cam[1::2, 0::2, 1]
    mos[1::2, 1::2] = cam[1::2, 1::2, 2]
    data = np.round(mos * white).astype("<u2").tobytes()
    cm = np.diag(gains) @ XYZ_TO_SRGB
    head = 8
    entries = [
        (254, 4, [0]), (256, 4, [w]), (257, 4, [h]), (258, 3, [16]), (259, 3, [1]), (262, 3, [32803]),
        (271, 2, "PhotoCrate"), (272, 2, "Test Sensor"), (273, 4, [0]), (274, 3, [1]), (277, 3, [1]),
        (278, 4, [h]), (279, 4, [len(data)]), (284, 3, [1]), (33421, 3, [2, 2]), (33422, 1, [0, 1, 1, 2]),
        (50706, 1, [1, 4, 0, 0]), (50707, 1, [1, 1, 0, 0]), (50708, 2, "PhotoCrate Test Sensor"),
        (50714, 4, [0]), (50717, 4, [white]),
        (50721, 10, [_rat(v) for v in cm.ravel()]),
        (50728, 5, [_rat(g) for g in gains]), (50778, 3, [21]),
    ]
    n = len(entries)
    ifd_size = 2 + 12 * n + 4
    ifd, extra = _ifd_entries(entries, head + ifd_size)
    strip_off = head + ifd_size + len(extra)
    entries = [(t, ty, [strip_off] if t == 273 else v) for t, ty, v in entries]
    ifd, extra = _ifd_entries(entries, head + ifd_size)
    with open(path, "wb") as f:
        f.write(b"II*\0" + struct.pack("<I", head) + ifd + extra + data)
    return path


def scene(h: int = 400, w: int = 600, seed: int = 5) -> np.ndarray:
    """线性 sRGB 场景：灰阶渐变、几块纯色、很亮（会过曝）的一条和细纹理。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    base = np.dstack([0.05 + 0.6 * xx / w] * 3)
    base[: h // 4, :, :] = np.linspace(0, 1, w)[None, :, None] ** 2.2     # 顶部：灰阶
    base[h // 3:h // 3 + 60, 40:140] = (0.6, 0.08, 0.06)                  # 红
    base[h // 3:h // 3 + 60, 180:280] = (0.07, 0.4, 0.1)                  # 绿
    base[h // 3:h // 3 + 60, 320:420] = (0.05, 0.1, 0.55)                 # 蓝
    base[h // 3:h // 3 + 60, 460:560] = (0.18, 0.18, 0.18)                # 中性灰（吸管白平衡）
    base[-60:, :, :] *= 1 + 0.15 * np.sin(xx[-60:] / 2.5)[..., None]       # 细纹理
    base += rng.normal(0, 0.003, base.shape)
    return np.clip(base, 0, 1)
