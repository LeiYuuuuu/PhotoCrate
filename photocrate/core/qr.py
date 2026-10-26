"""二维码编码（QR Code Model 2，字节模式，版本 1–10，纠错等级 L / M / Q / H）。不依赖第三方库。

给「手机互传」显示网址用（网址几十个字符，版本 3–5 就够）。算法照 ISO/IEC 18004：
数据位流 → 补齐 → 分块做 Reed-Solomon 纠错码 → 交错 → 按之字形放进矩阵 → 试 8 种掩模、取扣分最少的 → 写格式信息。
encode(text) 返回 list[list[bool]]（True = 黑）。测试里用 OpenCV 的 QRCodeDetector 解码核对。
"""
from __future__ import annotations

ECL = {"L": 1, "M": 0, "Q": 3, "H": 2}             # 格式信息里的两位
_ECL_INDEX = {"L": 0, "M": 1, "Q": 2, "H": 3}
# 每块的纠错码字数、块数（版本 1–10）
ECC_PER_BLOCK = [
    [7, 10, 15, 20, 26, 18, 20, 24, 30, 18],
    [10, 16, 26, 18, 24, 16, 18, 22, 22, 26],
    [13, 22, 18, 26, 18, 24, 18, 22, 20, 24],
    [17, 28, 22, 16, 22, 28, 26, 26, 24, 28],
]
NUM_BLOCKS = [
    [1, 1, 1, 1, 1, 2, 2, 2, 2, 4],
    [1, 1, 1, 2, 2, 4, 4, 4, 5, 5],
    [1, 1, 2, 2, 4, 4, 6, 6, 8, 8],
    [1, 1, 2, 4, 4, 4, 5, 6, 8, 8],
]
MAX_VERSION = 10


def _raw_modules(ver: int) -> int:
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        k = ver // 7 + 2
        n -= (25 * k - 10) * k - 55
        if ver >= 7:
            n -= 36
    return n


def data_capacity(ver: int, ecl: str) -> int:
    """能放多少个数据码字。"""
    e = _ECL_INDEX[ecl]
    return _raw_modules(ver) // 8 - ECC_PER_BLOCK[e][ver - 1] * NUM_BLOCKS[e][ver - 1]


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    res = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            res[j] = _gf_mul(res[j], root)
            if j + 1 < degree:
                res[j] ^= res[j + 1]
        root = _gf_mul(root, 0x02)
    return res


def _rs_remainder(data: list[int], div: list[int]) -> list[int]:
    res = [0] * len(div)
    for b in data:
        factor = b ^ res.pop(0)
        res.append(0)
        for i, c in enumerate(div):
            res[i] ^= _gf_mul(c, factor)
    return res


def _codewords(data: bytes, ver: int, ecl: str) -> list[int]:
    bits: list[int] = []

    def put(v: int, n: int) -> None:
        bits.extend((v >> i) & 1 for i in range(n - 1, -1, -1))
    put(0b0100, 4)                                   # 字节模式
    put(len(data), 8 if ver <= 9 else 16)
    for b in data:
        put(b, 8)
    cap = data_capacity(ver, ecl) * 8
    put(0, min(4, cap - len(bits)))                  # 结束符
    put(0, (-len(bits)) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < cap // 8:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    e = _ECL_INDEX[ecl]
    nblocks, ecclen = NUM_BLOCKS[e][ver - 1], ECC_PER_BLOCK[e][ver - 1]
    raw = _raw_modules(ver) // 8
    nshort = nblocks - raw % nblocks
    short = raw // nblocks
    div = _rs_divisor(ecclen)
    blocks, k = [], 0
    for i in range(nblocks):
        n = short - ecclen + (0 if i < nshort else 1)
        dat = words[k:k + n]
        k += n
        ecc = _rs_remainder(dat, div)
        if i < nshort:
            dat = dat + [0]
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short - ecclen or j >= nshort:
                out.append(blk[i])
    return out


class _Matrix:
    def __init__(self, ver: int):
        self.ver = ver
        self.size = ver * 4 + 17
        self.m = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]

    def set_fn(self, x: int, y: int, dark: bool) -> None:
        self.m[y][x] = dark
        self.fn[y][x] = True

    def draw_function_patterns(self) -> None:
        s = self.size
        for i in range(s):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (s - 4, 3), (3, s - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < s and 0 <= y < s:
                        self.set_fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = self.alignment_positions()
        n = len(pos)
        for i in range(n):
            for j in range(n):
                if (i == 0 and j == 0) or (i == 0 and j == n - 1) or (i == n - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(pos[i] + dx, pos[j] + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0, "M")                     # 先占位，掩模选好后重写
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = s - 11 + i % 3, i // 3
                self.set_fn(a, b, bit)
                self.set_fn(b, a, bit)

    def alignment_positions(self) -> list[int]:
        if self.ver == 1:
            return []
        n = self.ver // 7 + 2
        step = (self.ver * 8 + n * 3 + 5) // (n * 4 - 4) * 2
        return [6] + [self.size - 7 - i * step for i in range(n - 1)][::-1]

    def draw_format(self, mask: int, ecl: str) -> None:
        data = ECL[ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        g = lambda i: (bits >> i) & 1 == 1           # noqa: E731
        s = self.size
        for i in range(6):
            self.set_fn(8, i, g(i))
        self.set_fn(8, 7, g(6))
        self.set_fn(8, 8, g(7))
        self.set_fn(7, 8, g(8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, g(i))
        for i in range(8):
            self.set_fn(s - 1 - i, 8, g(i))
        for i in range(8, 15):
            self.set_fn(8, s - 15 + i, g(i))
        self.set_fn(8, s - 8, True)

    def draw_codewords(self, words: list[int]) -> None:
        s = self.size
        i = 0
        total = len(words) * 8
        right = s - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(s):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = s - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < total:
                        self.m[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        f = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0][mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fn[y][x] and f(x, y):
                    self.m[y][x] = not self.m[y][x]


def penalty(m: list[list[bool]]) -> int:
    """ISO 18004 的四条扣分规则（连成一串、2×2 色块、像定位图形的图案、黑白比例）。"""
    s = len(m)
    score = 0
    lines = [row for row in m] + [[m[y][x] for y in range(s)] for x in range(s)]
    finder_a = [True, False, True, True, True, False, True, False, False, False, False]
    finder_b = finder_a[::-1]
    for ln in lines:
        run, prev = 0, None
        for v in ln:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run, prev = 1, v
        if run >= 5:
            score += run - 2
        for i in range(s - 10):
            seg = ln[i:i + 11]
            if seg == finder_a or seg == finder_b:
                score += 40
    for y in range(s - 1):
        for x in range(s - 1):
            c = m[y][x]
            if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(v for row in m for v in row)
    k = abs(dark * 20 - s * s * 10) // (s * s)
    return score + k * 10


def encode(text: str, ecl: str = "M") -> list[list[bool]]:
    data = text.encode("utf-8")
    ver = next((v for v in range(1, MAX_VERSION + 1) if data_capacity(v, ecl) * 8 >= 4 + (8 if v <= 9 else 16) + 8 * len(data)),
               None)
    if ver is None:
        raise ValueError("text too long for a version 1-10 QR code")
    words = _codewords(data, ver, ecl)
    best = None
    for mask in range(8):
        mx = _Matrix(ver)
        mx.draw_function_patterns()
        mx.draw_codewords(words)
        mx.apply_mask(mask)
        mx.draw_format(mask, ecl)
        p = penalty(mx.m)
        if best is None or p < best[0]:
            best = (p, mx.m)
    return best[1]


def to_image(m: list[list[bool]], scale: int = 8, border: int = 4):
    """矩阵 → uint8 灰度 numpy 图（白底黑码，带静区）。"""
    import numpy as np
    a = np.array(m, dtype=bool)
    img = np.where(a, 0, 255).astype(np.uint8)
    img = np.pad(img, border, constant_values=255)
    return np.kron(img, np.ones((scale, scale), np.uint8))
