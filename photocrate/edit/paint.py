"""画笔类工具与选区工具（纯 numpy / OpenCV）。

一笔（Stroke）：开始时拍下图层快照；每个笔触点把圆形笔刷“盖章”进覆盖度图（同一笔里取最大值，
所以来回涂不会越涂越深，和 Photoshop 一样由“不透明度”封顶）；图层 = 快照 与 目标 按覆盖度混合。
结束时只把改动的矩形区域记进历史（改前 / 改后两块），撤销不用存整张图。

- paint   画笔：目标 = 前景色
- erase   橡皮擦：降低不透明度（画在蒙版上时 = 涂黑）
- clone   仿制图章：目标 = 快照里偏移 (dx, dy) 处的像素
- heal    污点修复：先只记录涂抹范围，松开时用 OpenCV 修复（inpaint）填补并匹配周围的噪点
- mask    在图层蒙版上画：目标 = 灰度值（白显示、黑隐藏）
- dodge / burn / sponge   减淡 / 加深 / 海绵：目标 = 快照提亮 / 压暗 / 加减饱和度（按“范围”只作用于暗部 / 中间调 / 亮部）
- blur / sharpen          模糊 / 锐化：目标 = 快照模糊 / USM 锐化后的样子（按笔触所在的一小块现算）
- smudge                  涂抹：手指“沾”上起点的颜色往前推，每个笔触点都在当前像素上混合（不是按覆盖度）
"""
from __future__ import annotations

import math

import numpy as np

from .document import Document, Layer, crop_canvas, from_float, mask_bbox, maxval, over, to8, to_float
from ..i18n import is_en, t as _t

TONE_MODES = ("dodge", "burn", "sponge", "blur", "sharpen")
BLUR = "Blur" if is_en() else "模糊"                  # 英文表里的「模糊」是挑片的 Blurry，这里是工具名
MODE_NAMES = {"paint": _t("画笔"), "erase": _t("橡皮擦"), "clone": _t("仿制图章"), "heal": _t("污点修复"),
              "dodge": _t("减淡"), "burn": _t("加深"), "sponge": _t("海绵"), "blur": BLUR, "sharpen": _t("锐化"),
              "smudge": _t("涂抹")}


def _lum(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def tone_weight(L: np.ndarray, rng: str) -> np.ndarray:
    """减淡 / 加深只作用在哪个亮度范围：shadows 暗部、midtones 中间调、highlights 亮部（平滑过渡）。"""
    if rng == "shadows":
        return (1 - L) ** 2
    if rng == "highlights":
        return L ** 2
    return np.clip(4 * L * (1 - L), 0, 1)


def tone_target(rgb: np.ndarray, mode: str, strength: float, rng: str = "midtones", sat_up: bool = False):
    """减淡 / 加深 / 海绵的目标颜色（float 0–1）。"""
    L = _lum(rgb)
    if mode == "dodge":
        w = tone_weight(L, rng)[..., None] * strength
        return rgb + (1 - rgb) * w
    if mode == "burn":
        w = tone_weight(L, rng)[..., None] * strength
        return rgb - rgb * w
    k = 1 + strength if sat_up else 1 - strength                     # 海绵：加色 / 去色
    return np.clip(L[..., None] + (rgb - L[..., None]) * k, 0, 1)


def dab(radius: float, hardness: float) -> np.ndarray:
    """圆形笔刷（float32 0–1）：硬度以内全满，往外平滑衰减到 0。"""
    r = max(0.5, float(radius))
    n = int(math.ceil(r)) * 2 + 1
    c = n // 2
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    d = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / r
    hd = min(0.99, max(0.0, float(hardness)))
    t = np.clip((d - hd) / max(1e-3, 1 - hd), 0, 1)
    m = 1 - t * t * (3 - 2 * t)
    m[d > 1] = 0
    if r < 1.5:                                  # 很小的笔刷：至少盖住中心像素
        m[c, c] = 1
    return m


class Stroke:
    def __init__(self, doc: Document, layer: Layer, mode: str, *, color=(0, 0, 0), size: float = 30,
                 hardness: float = 0.8, opacity: float = 1.0, clone_offset: tuple[int, int] = (0, 0),
                 to_mask: bool = False, name: str = "", strength: float = 0.5, tone_range: str = "midtones",
                 sat_up: bool = False, record: bool = True):
        self.doc, self.layer, self.mode = doc, layer, mode
        self.to_mask = to_mask and layer.mask is not None
        if self.to_mask and mode not in ("paint", "erase"):      # 修饰类工具只改像素；在蒙版上没有意义
            self.to_mask = False
        self.radius = max(0.5, size / 2)
        self.kernel = dab(self.radius, hardness)
        self.opacity = float(opacity)
        self.color = np.array(color[:3], np.float32) / 255.0
        self.gray = float(np.dot(color[:3], (0.299, 0.587, 0.114)))
        self.offset = (int(clone_offset[0]), int(clone_offset[1]))
        self.strength = float(np.clip(strength, 0, 1))
        self.tone_range = tone_range
        self.sat_up = bool(sat_up)
        self.record = record                                       # False：不记历史（快速蒙版由调用方记）
        self.pickup = None                                         # 涂抹：手指上沾着的颜色（笔刷大小的 float RGBA）
        self.name = name or MODE_NAMES.get(mode, _t("画笔"))
        self.state_before = None
        if not self.to_mask and layer.kind in ("text", "shape", "smart"):     # 在文字上画 = 先栅格化（换新数组，旧状态里的像素不受影响）
            self.state_before = doc.state()
            layer.kind = "pixel"
            layer.pixels = layer.pixels.copy()
            layer.touch()
        if not self.to_mask and not layer.canvas_sized:
            self._cover_canvas()
        arr = layer.mask if self.to_mask else layer.pixels
        self.arr = arr
        self.before = arr.copy()
        self.cov = np.zeros(arr.shape[:2], np.float32)
        self.bbox: list[int] | None = None          # x0, y0, x1, y1（图层坐标）
        self.last: tuple[float, float] | None = None
        self.points: list[tuple[float, float]] = []
        self.sel = None
        if doc.selection is not None:
            x, y, w, h = layer.bounds(doc)
            self.sel = crop_canvas(doc.selection, x, y, w, h, 0).astype(np.float32) / 255.0

    def _cover_canvas(self) -> None:
        """图层没盖住整个画布（置入的小图）时先扩到画布大小，否则画到外面的笔触会丢。"""
        l, d = self.layer, self.doc
        x0, y0 = min(l.x, 0), min(l.y, 0)
        x1, y1 = max(l.x + l.w, d.width), max(l.y + l.h, d.height)
        if (x0, y0, x1, y1) == (l.x, l.y, l.x + l.w, l.y + l.h):
            return
        if self.state_before is None:
            self.state_before = d.state()
        l.pixels = crop_canvas(l.pixels, x0 - l.x, y0 - l.y, x1 - x0, y1 - y0, 0)
        if l.mask is not None:
            l.mask = crop_canvas(l.mask, x0 - l.x, y0 - l.y, x1 - x0, y1 - y0, 255)
            l.touch_mask()
        l.x, l.y = x0, y0
        l.touch()

    # ---- 坐标 ----
    def to_local(self, cx: float, cy: float) -> tuple[float, float]:
        if self.layer.canvas_sized:
            return cx, cy
        return cx - self.layer.x, cy - self.layer.y

    def add(self, cx: float, cy: float) -> tuple[int, int, int, int] | None:
        """加一个笔触点（画布坐标）。返回这次改动的画布矩形，没改动返回 None。"""
        x, y = self.to_local(cx, cy)
        pts = [(x, y)]
        if self.last is not None:
            lx, ly = self.last
            dist = math.hypot(x - lx, y - ly)
            step = max(1.0, self.radius * 0.25)
            n = int(dist // step)
            if n == 0:
                return None
            pts = [(lx + (x - lx) * (i / n), ly + (y - ly) * (i / n)) for i in range(1, n + 1)]
        self.last = pts[-1]
        self.points.extend(pts)
        h, w = self.cov.shape
        k = self.kernel
        r = k.shape[0] // 2
        rx0 = ry0 = 10 ** 9
        rx1 = ry1 = -10 ** 9
        smudge = self.mode == "smudge" and not self.to_mask
        for px, py in pts:
            ix, iy = int(round(px)), int(round(py))
            x0, y0, x1, y1 = ix - r, iy - r, ix + r + 1, iy + r + 1
            cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
            if cx0 >= cx1 or cy0 >= cy1:
                continue
            sub = self.cov[cy0:cy1, cx0:cx1]
            np.maximum(sub, k[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0], out=sub)
            if smudge:
                self._smudge(x0, y0, cx0, cy0, cx1, cy1)
            rx0, ry0, rx1, ry1 = min(rx0, cx0), min(ry0, cy0), max(rx1, cx1), max(ry1, cy1)
        if rx0 >= rx1:
            return None
        if not smudge:
            self._render(rx0, ry0, rx1, ry1)
        if self.bbox is None:
            self.bbox = [rx0, ry0, rx1, ry1]
        else:
            b = self.bbox
            self.bbox = [min(b[0], rx0), min(b[1], ry0), max(b[2], rx1), max(b[3], ry1)]
        if not self.to_mask and self.layer.locked_alpha and self.mode != "heal":    # 锁定透明像素：只改颜色
            self.arr[ry0:ry1, rx0:rx1, 3] = self.before[ry0:ry1, rx0:rx1, 3]
        rect = (rx0, ry0, rx1 - rx0, ry1 - ry0)
        if self.to_mask:
            self.layer.touch_mask(rect)
        else:
            self.layer.touch(rect)
        ox, oy = (0, 0) if self.layer.canvas_sized else (self.layer.x, self.layer.y)
        return rx0 + ox, ry0 + oy, rx1 - rx0, ry1 - ry0

    def _render(self, x0: int, y0: int, x1: int, y1: int) -> None:
        a = self.cov[y0:y1, x0:x1] * self.opacity
        if self.sel is not None:
            a = a * self.sel[y0:y1, x0:x1]
        before = self.before[y0:y1, x0:x1]
        if self.mode == "heal":                     # 先不改像素，松开时一起修复（界面上画半透明提示）
            return
        if self.to_mask:
            target = 0.0 if self.mode == "erase" else self.gray
            out = before.astype(np.float32) + (target - before.astype(np.float32)) * a
            self.arr[y0:y1, x0:x1] = np.clip(out + 0.5, 0, 255).astype(np.uint8)
            return
        M = maxval(before)
        if self.mode == "erase":
            px = before.copy()
            px[..., 3] = np.clip(before[..., 3].astype(np.float32) * (1 - a) + 0.5, 0, M).astype(before.dtype)
            self.arr[y0:y1, x0:x1] = px
        elif self.mode == "clone":
            dx, dy = self.offset
            src = to_float(crop_canvas(self.before, x0 + dx, y0 + dy, x1 - x0, y1 - y0, 0))
            dst = to_float(before)
            sa = src[..., 3] * a
            oa = sa + dst[..., 3] * (1 - sa)
            oc = (src[..., :3] * sa[..., None] + dst[..., :3] * (dst[..., 3] * (1 - sa))[..., None]) \
                / np.maximum(oa, 1e-6)[..., None]
            self.arr[y0:y1, x0:x1] = from_float(np.dstack([oc, oa]), before.dtype)
        elif self.mode in TONE_MODES:
            src = to_float(before)
            tgt = self._tone_region(x0, y0, x1, y1, src)
            out = src.copy()
            out[..., :3] = src[..., :3] + (tgt - src[..., :3]) * a[..., None]
            self.arr[y0:y1, x0:x1] = from_float(out, before.dtype)
        else:
            self.arr[y0:y1, x0:x1] = over(before, self.color, a)

    def _tone_region(self, x0, y0, x1, y1, src):
        """修饰类工具在这块的目标颜色（float RGB）。模糊 / 锐化要看周围，所以从快照里多取一圈。"""
        import cv2
        if self.mode in ("dodge", "burn", "sponge"):
            return tone_target(src[..., :3], self.mode, self.strength, self.tone_range, self.sat_up)
        sigma = 0.6 + 4.0 * self.strength if self.mode == "blur" else 1.0
        m = int(math.ceil(sigma * 3)) + 2
        big = to_float(crop_canvas(self.before, x0 - m, y0 - m, x1 - x0 + 2 * m, y1 - y0 + 2 * m, 0))[..., :3]
        H, W = self.before.shape[:2]                          # 图层边缘外面用复制边缘，免得边上被模糊成黑色
        ix0, iy0 = max(0, m - x0), max(0, m - y0)
        ix1, iy1 = big.shape[1] - max(0, x1 + m - W), big.shape[0] - max(0, y1 + m - H)
        inner = big[iy0:iy1, ix0:ix1]
        big = cv2.copyMakeBorder(inner, iy0, big.shape[0] - iy1, ix0, big.shape[1] - ix1, cv2.BORDER_REPLICATE)
        bl = cv2.GaussianBlur(big, (0, 0), sigma)
        if self.mode == "blur":
            res = bl
        else:
            res = np.clip(big + (big - bl) * (0.4 + 2.6 * self.strength), 0, 1)
        return res[m:m + (y1 - y0), m:m + (x1 - x0)]

    def _smudge(self, x0, y0, cx0, cy0, cx1, cy1) -> None:
        """涂抹一个笔触点：当前像素向“手指上的颜色”混合，手指再沾上混合后的颜色。"""
        k = self.kernel
        n = k.shape[0]
        cur = to_float(self.arr[cy0:cy1, cx0:cx1])
        if self.pickup is None:
            self.pickup = np.zeros((n, n, 4), np.float32)
            self.pickup[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] = cur
            self.pick_ok = np.zeros((n, n), bool)
            self.pick_ok[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] = True
            return
        sl = (slice(cy0 - y0, cy1 - y0), slice(cx0 - x0, cx1 - x0))
        w = k[sl] * self.strength * self.pick_ok[sl]
        if self.sel is not None:
            w = w * self.sel[cy0:cy1, cx0:cx1]
        new = cur + (self.pickup[sl] - cur) * w[..., None]
        self.arr[cy0:cy1, cx0:cx1] = from_float(new, self.arr.dtype)
        self.pickup[sl] = new
        self.pick_ok[sl] = True

    def finish(self) -> tuple[int, int, int, int] | None:
        """结束这一笔：污点修复在这里真正计算；把改动记进历史。返回改动的画布矩形。"""
        if self.bbox is None:
            if self.state_before is not None:
                self.doc.restore(self.state_before)
            return None
        x0, y0, x1, y1 = self.bbox
        if self.mode == "heal":
            m = int(self.radius * 2) + 8
            hx0, hy0 = max(0, x0 - m), max(0, y0 - m)
            hx1, hy1 = min(self.arr.shape[1], x1 + m), min(self.arr.shape[0], y1 + m)
            cov = self.cov[hy0:hy1, hx0:hx1]
            if self.sel is not None:
                cov = cov * self.sel[hy0:hy1, hx0:hx1]
            region = self.before[hy0:hy1, hx0:hx1]
            mask = (cov > 0.05).astype(np.uint8) * 255
            if mask.any():
                fixed = inpaint(region, mask, feather=cov)
                self.arr[hy0:hy1, hx0:hx1] = fixed
            x0, y0, x1, y1 = hx0, hy0, hx1, hy1
            self.layer.touch((x0, y0, x1 - x0, y1 - y0))
        rect = (x0, y0, x1 - x0, y1 - y0)
        if not self.record:
            pass
        elif self.state_before is not None:
            self.doc.history.push_state(self.name, self.state_before)
        else:
            self.doc.history.push_patch(self.name, self.layer.id, "mask" if self.to_mask else "pixels", rect,
                                        self.before[y0:y1, x0:x1].copy(), self.arr[y0:y1, x0:x1].copy())
        self.before = None
        self.doc.version += 1
        self.doc.emit("history")
        ox, oy = (0, 0) if self.layer.canvas_sized else (self.layer.x, self.layer.y)
        return x0 + ox, y0 + oy, x1 - x0, y1 - y0

    def cancel(self) -> None:
        if self.bbox is not None:
            x0, y0, x1, y1 = self.bbox
            self.arr[y0:y1, x0:x1] = self.before[y0:y1, x0:x1]
            (self.layer.touch_mask if self.to_mask else self.layer.touch)((x0, y0, x1 - x0, y1 - y0))
        if self.state_before is not None:
            self.doc.restore(self.state_before)


# ---------------------------------------------------------------------------
# 修复（污点修复画笔 / 内容识别填充）
# ---------------------------------------------------------------------------

def inpaint(rgba: np.ndarray, mask: np.ndarray, feather: np.ndarray | None = None) -> np.ndarray:
    """用周围的画面补上 mask（uint8，>0 = 要补）的区域，返回新的 uint8 RGBA。

    OpenCV 的 Telea 修复在大块区域上会变得很“糊”，所以大区域先在缩小图上补、放大回来，再把周围的噪点
    （高频细节的标准差）加回去，看起来不那么像涂了一层蜡。

    16 位图层：OpenCV 的修复只收 8 位彩色图，所以在 8 位上补好，再只把“补上的差值”加回 16 位原图——
    没补的地方一个值都不变。"""
    import cv2

    if rgba.dtype != np.uint8:
        r8 = to8(rgba)
        fixed8 = inpaint(r8, mask, feather)
        d = fixed8[..., :3].astype(np.int32) - r8[..., :3].astype(np.int32)
        if not d.any():
            return rgba.copy()
        out = rgba.copy()
        out[..., :3] = np.clip(rgba[..., :3].astype(np.int32) + d * 257, 0, 65535).astype(np.uint16)
        return out
    rgb = np.ascontiguousarray(rgba[..., :3])
    m = (mask > 0).astype(np.uint8) * 255
    bb = mask_bbox(m)
    if bb is None:
        return rgba.copy()
    big = max(bb[2], bb[3])
    if big > 160:
        s = 160 / big
        small = cv2.resize(rgb, (max(1, int(rgb.shape[1] * s)), max(1, int(rgb.shape[0] * s))),
                           interpolation=cv2.INTER_AREA)
        sm = cv2.resize(m, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
        sm = cv2.dilate(sm, np.ones((3, 3), np.uint8))
        filled = cv2.inpaint(small, sm, 5, cv2.INPAINT_TELEA)
        up = cv2.resize(filled, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_CUBIC)
        base = np.where(m[..., None] > 0, up, rgb)
        res = cv2.inpaint(base, cv2.erode(m, np.ones((3, 3), np.uint8)) if big > 400 else m, 3, cv2.INPAINT_TELEA)
        res = np.where(m[..., None] > 0, (0.5 * res.astype(np.float32) + 0.5 * up.astype(np.float32)), rgb)
    else:
        res = cv2.inpaint(rgb, m, max(3, int(big * 0.15)), cv2.INPAINT_TELEA).astype(np.float32)
    # 匹配噪点：周围一圈的高频细节强度
    ring = cv2.dilate(m, np.ones((15, 15), np.uint8)) & ~m
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    hf = gray - cv2.GaussianBlur(gray, (0, 0), 1.2)
    sd = float(hf[ring > 0].std()) if (ring > 0).any() else 0.0
    if sd > 0.5:
        rng = np.random.default_rng(99)
        noise = cv2.GaussianBlur(rng.standard_normal(m.shape).astype(np.float32), (0, 0), 0.6)
        noise *= sd / max(1e-3, float(noise.std()))
        res = res + (noise * (m > 0))[..., None]
    a = (m.astype(np.float32) / 255.0) if feather is None else np.clip(feather * 1.6, 0, 1)
    a = cv2.GaussianBlur(a, (0, 0), 1.0)
    out = rgba.copy()
    out[..., :3] = np.clip(rgb.astype(np.float32) + (res - rgb.astype(np.float32)) * a[..., None] + 0.5,
                           0, 255).astype(np.uint8)
    return out


def patch(rgba: np.ndarray, mask: np.ndarray, dx: int, dy: int) -> np.ndarray | None:
    """修补工具：选区里的内容换成 (dx, dy) 处的画面，用泊松融合（cv2.seamlessClone）让边缘的颜色和亮度
    自然过渡。mask：uint8、与 rgba 同尺寸。16 位图层在 8 位上算好，只把差值加回去。"""
    import cv2
    if rgba.dtype != np.uint8:
        r8 = to8(rgba)
        out8 = patch(r8, mask, dx, dy)
        if out8 is None:
            return None
        d = out8[..., :3].astype(np.int32) - r8[..., :3].astype(np.int32)
        out = rgba.copy()
        out[..., :3] = np.clip(rgba[..., :3].astype(np.int32) + d * 257, 0, 65535).astype(np.uint16)
        return out
    h, w = mask.shape
    hard = (mask > 127).astype(np.uint8) * 255
    bb = mask_bbox(hard)
    if bb is None:
        return None
    x, y, bw, bh = bb
    if x + dx < 1 or y + dy < 1 or x + dx + bw > w - 1 or y + dy + bh > h - 1 or x < 1 or y < 1 \
            or x + bw > w - 1 or y + bh > h - 1:
        return None                                       # 源或目标碰到图层边缘：seamlessClone 不收
    src = np.ascontiguousarray(rgba[y + dy:y + dy + bh, x + dx:x + dx + bw, :3])
    m = np.ascontiguousarray(hard[y:y + bh, x:x + bw])
    m[0, :] = m[-1, :] = 0
    m[:, 0] = m[:, -1] = 0
    dst = np.ascontiguousarray(rgba[..., :3])
    center = (int(x + bw // 2), int(y + bh // 2))
    try:
        res = cv2.seamlessClone(src, dst, m, center, cv2.NORMAL_CLONE)
    except cv2.error:
        return None
    a = cv2.GaussianBlur(mask.astype(np.float32) / 255, (0, 0), 1.0)[..., None]
    out = rgba.copy()
    out[..., :3] = np.clip(dst + (res.astype(np.float32) - dst) * a + 0.5, 0, 255).astype(np.uint8)
    return out


def red_eye(rgba: np.ndarray, cx: float, cy: float, radius: float, darken: float = 0.5) -> np.ndarray | None:
    """红眼：在 (cx, cy) 半径 radius 的圆里，把明显偏红的像素换成暗的中性色（瞳孔）。"""
    h, w = rgba.shape[:2]
    r = max(2.0, float(radius))
    x0, y0, x1, y1 = max(0, int(cx - r)), max(0, int(cy - r)), min(w, int(cx + r) + 1), min(h, int(cy + r) + 1)
    if x0 >= x1 or y0 >= y1:
        return None
    f = to_float(rgba[y0:y1, x0:x1])
    R, G, B = f[..., 0], f[..., 1], f[..., 2]
    red = np.clip((R - np.maximum(G, B)) / np.maximum(R, 1e-3) - 0.25, 0, None) / 0.35
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    circ = np.clip((r - np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)) / max(1.0, r * 0.25), 0, 1)
    k = np.clip(red, 0, 1) * circ
    if not (k > 0.05).any():
        return None
    import cv2
    k = cv2.GaussianBlur(k.astype(np.float32), (0, 0), max(0.6, r * 0.04))
    gray = (G + B) / 2 * (1 - float(darken) * 0.8)
    out_f = f.copy()
    for i in range(3):
        target = gray if i == 0 else f[..., i] * (1 - float(darken) * 0.5)
        out_f[..., i] = f[..., i] + (target - f[..., i]) * k
    out = rgba.copy()
    out[y0:y1, x0:x1] = from_float(out_f, rgba.dtype)
    return out


# ---------------------------------------------------------------------------
# 选区工具
# ---------------------------------------------------------------------------

def combine(old: np.ndarray | None, new: np.ndarray, mode: str) -> np.ndarray:
    """mode: new 新选区 / add 添加 / sub 减去 / and 交叉。"""
    if mode == "new":
        return new
    if old is None:
        return new if mode == "add" else np.zeros_like(new)
    if mode == "add":
        return np.maximum(old, new)
    if mode == "sub":
        return np.minimum(old, 255 - new)
    if mode == "and":
        return np.minimum(old, new)
    return new


def rect_mask(w: int, h: int, x0: float, y0: float, x1: float, y1: float, ellipse: bool = False,
              feather: float = 0.0) -> np.ndarray:
    import cv2
    m = np.zeros((h, w), np.uint8)
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    if ellipse:
        c = (int(round((xa + xb) * 8)), int(round((ya + yb) * 8)))
        ax = (int(round((xb - xa) * 8)), int(round((yb - ya) * 8)))
        cv2.ellipse(m, c, ax, 0, 0, 360, 255, -1, cv2.LINE_AA, 4)
    else:
        m[max(0, int(round(ya))):max(0, int(round(yb))), max(0, int(round(xa))):max(0, int(round(xb)))] = 255
    if feather > 0:
        m = cv2.GaussianBlur(m, (0, 0), feather / 2)
    return m


def polygon_mask(w: int, h: int, points, feather: float = 0.0) -> np.ndarray:
    import cv2
    m = np.zeros((h, w), np.uint8)
    if len(points) >= 3:
        pts = np.round(np.asarray(points, np.float64) * 16).astype(np.int32)
        cv2.fillPoly(m, [pts], 255, cv2.LINE_AA, 4)
    if feather > 0:
        m = cv2.GaussianBlur(m, (0, 0), feather / 2)
    return m


def wand(rgba: np.ndarray, x: int, y: int, tolerance: float = 32, contiguous: bool = True,
         antialias: bool = True) -> np.ndarray:
    """魔棒：与点击处颜色相差不超过容差（0–255，按通道最大差）的像素。"""
    import cv2
    h, w = rgba.shape[:2]
    x, y = int(np.clip(x, 0, w - 1)), int(np.clip(y, 0, h - 1))
    img = np.ascontiguousarray(rgba[..., :3])
    tol = float(tolerance)
    if contiguous:
        mm = np.zeros((h + 2, w + 2), np.uint8)
        flags = 8 | cv2.FLOODFILL_FIXED_RANGE | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
        cv2.floodFill(img, mm, (x, y), (0, 0, 0), (tol,) * 3, (tol,) * 3, flags)
        m = mm[1:-1, 1:-1]
    else:
        seed = img[y, x].astype(np.int16)
        m = (np.abs(img.astype(np.int16) - seed).max(axis=2) <= tol).astype(np.uint8) * 255
    if rgba.shape[2] == 4:                        # 透明的地方只和透明的连在一起
        sa = rgba[y, x, 3]
        m = np.where((rgba[..., 3] > 0) == (sa > 0), m, 0).astype(np.uint8)
    if antialias:
        m = cv2.GaussianBlur(m, (0, 0), 0.7)
    return m


def object_select(rgba: np.ndarray, rect, iterations: int = 5) -> np.ndarray:
    """对象选择：在框里用 GrabCut 找出主体（在长边约 900 px 的缩小图上算，再放大、平滑边缘）。"""
    import cv2
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = rect
    x0, x1 = sorted((int(round(x0)), int(round(x1))))
    y0, y1 = sorted((int(round(y0)), int(round(y1))))
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return np.zeros((h, w), np.uint8)
    s = min(1.0, 900 / max(w, h))
    sw, sh = max(1, int(w * s)), max(1, int(h * s))
    img = cv2.resize(np.ascontiguousarray(rgba[..., :3]), (sw, sh), interpolation=cv2.INTER_AREA)
    img = cv2.bilateralFilter(img, 9, 40, 5)          # 保边平滑：去掉噪点 / 颗粒，免得 GrabCut 把颗粒当成物体
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    r = (int(x0 * s), int(y0 * s), max(2, int((x1 - x0) * s)), max(2, int((y1 - y0) * s)))
    gm = np.zeros((sh, sw), np.uint8)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(img, gm, r, bgd, fgd, iterations, cv2.GC_INIT_WITH_RECT)
    fg = np.where((gm == cv2.GC_FGD) | (gm == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats((fg > 0).astype(np.uint8), 8)
    if n > 2:                                      # 只留大块，去掉零碎的小斑点
        keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 0.04 * stats[1:, cv2.CC_STAT_AREA].max()]
        fg = np.isin(lab, keep).astype(np.uint8) * 255
    big = cv2.resize(fg, (w, h), interpolation=cv2.INTER_LINEAR)
    big = cv2.GaussianBlur(big, (0, 0), max(0.8, 0.6 / s))
    out = np.zeros((h, w), np.uint8)
    out[y0:y1, x0:x1] = big[y0:y1, x0:x1]
    return out


def object_select_doc(doc: Document, rect) -> np.ndarray:
    """在文档上做对象选择：用缩小的合成图算，结果放大回画布尺寸。"""
    small, f = doc.flat_level(1600)
    x0, y0, x1, y1 = rect
    m = object_select(small, (x0 / f, y0 / f, x1 / f, y1 / f))
    return _to_canvas(doc, m, f)


def _to_canvas(doc: Document, m: np.ndarray, f: int) -> np.ndarray:
    """在缩小 f 倍的图上算出的选区放大回画布尺寸。"""
    import cv2
    if f > 1:
        m = cv2.resize(m, (m.shape[1] * f, m.shape[0] * f), interpolation=cv2.INTER_LINEAR)
        m = np.ascontiguousarray(m[:doc.height, :doc.width])
    return m


# ---- 用 AI 模型（ai/matting.py；没下载时调用方退回上面的 GrabCut / 不提供） ----

def ai_object_select_doc(doc: Document, points=(), labels=(), box=None) -> np.ndarray:
    """对象选择（SAM 2.1）：points / box 用画布坐标，labels 1 = 要、0 = 不要。
    在缩小的合成图上算（编码结果按画面内容缓存，同一张图接着点很快），边缘按照片本身精修，再放大回画布尺寸。"""
    import hashlib
    from ..ai import matting
    from .select import refine
    small, f = doc.flat_level(1600)
    h, w = small.shape[:2]
    key = (id(doc), small.shape, hashlib.blake2b(small.tobytes(), digest_size=16).digest())
    emb = matting.sam_encode(small, key)
    m = matting.sam_predict(emb, w, h, [(x / f, y / f) for x, y in points], labels,
                            None if box is None else tuple(v / f for v in box))
    if not m.any():
        return np.zeros((doc.height, doc.width), np.uint8)
    m = refine(small, (m * 255).astype(np.uint8), radius=max(2, max(w, h) / 250))
    return _to_canvas(doc, m, f)


def ai_matte_doc(doc: Document, kind: str = "subject") -> np.ndarray:
    """选择主体（BiRefNet）/ 选择人像（MODNet，kind="portrait"）：画布尺寸的 uint8 软选区，头发丝是半透明的。"""
    from ..ai import matting
    from .select import refine
    small, f = doc.flat_level(1600)
    a = (matting.portrait if kind == "portrait" else matting.subject)(small)
    m = np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8)
    if f > 1 or max(small.shape[:2]) > 1024:          # 模型在 512 / 1024 上算的，放大后边缘按照片精修一下
        m = refine(small, m, radius=max(2, max(small.shape[:2]) / 400))
    return _to_canvas(doc, m, f)


def matte_found(m: np.ndarray, min_frac: float = 0.02) -> bool:
    """抠图结果里有没有像样的一块（最大的连通块至少占画面 min_frac）。MODNet 在没有人的照片上
    也会给出零星几块（船上的绳子、远处的花），有人的照片最大块在 4% 以上（在 18 张照片上量的）。"""
    import cv2
    hard = (m > 127).astype(np.uint8)
    s = max(1, max(hard.shape) // 800)
    if s > 1:
        hard = np.ascontiguousarray(hard[::s, ::s])
    n, _, st, _ = cv2.connectedComponentsWithStats(hard, 8)
    return n > 1 and st[1:, cv2.CC_STAT_AREA].max() >= hard.size * min_frac


def portrait_blur(px: np.ndarray, alpha: np.ndarray, radius: float) -> np.ndarray:
    """人像虚化：alpha（uint8，255 = 人，和 px 同尺寸）以外做圆形光圈模糊，返回新的像素（原数组不动）。
    模糊前先把人从背景里挖掉、按剩下的权重归一化，人的颜色不会晕到背景上；背景本来就糊，所以在缩小的图上模糊再放大。"""
    import cv2
    h, w = px.shape[:2]
    top = 65535.0 if px.dtype == np.uint16 else 255.0
    s = min(1.0, 12.0 / max(1.0, float(radius)))
    sw, sh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    img = cv2.resize(np.ascontiguousarray(px[..., :3]), (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32) / top
    bg = 1 - cv2.resize(alpha, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    bg = bg * bg                                       # 半透明的头发边也少算一点，免得发色晕开
    r = max(1, int(round(radius * s)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)).astype(np.float32)
    k /= k.sum()
    num = cv2.filter2D(img * bg[..., None], -1, k, borderType=cv2.BORDER_REFLECT)
    den = cv2.filter2D(bg, -1, k, borderType=cv2.BORDER_REFLECT)
    plain = cv2.filter2D(img, -1, k, borderType=cv2.BORDER_REFLECT)
    wgt = np.clip(den / 0.05, 0, 1)[..., None]         # 人身体正中（周围全是人）没有背景可取：退回普通模糊
    blur = num / np.maximum(den, 1e-4)[..., None] * wgt + plain * (1 - wgt)
    out = px.copy()
    mx = (np.arange(w, dtype=np.float32) + 0.5) * (sw / w) - 0.5
    for y0 in range(0, h, 1024):                       # 分条放大、合成：整张大图转浮点太占内存
        y1 = min(h, y0 + 1024)
        gx, gy = np.meshgrid(mx, (np.arange(y0, y1, dtype=np.float32) + 0.5) * (sh / h) - 0.5)
        b = cv2.remap(blur, gx, gy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        a = (alpha[y0:y1].astype(np.float32) / 255)[..., None]
        rgb = px[y0:y1, :, :3].astype(np.float32) / top
        out[y0:y1, :, :3] = np.clip((rgb * a + b * (1 - a)) * top + 0.5, 0, top).astype(px.dtype)
    return out


# ---------------------------------------------------------------------------
# 油漆桶 / 渐变
# ---------------------------------------------------------------------------

def bucket_mask(rgba: np.ndarray, x: int, y: int, tolerance: float = 32, contiguous: bool = True) -> np.ndarray:
    return wand(rgba, x, y, tolerance, contiguous, antialias=False)


def gradient(w: int, h: int, p0, p1, c0, c1, radial: bool = False, ox: int = 0, oy: int = 0) -> np.ndarray:
    """float32 RGBA 渐变（0–1）。c0 / c1 是 0–255 的 RGBA；(ox, oy) = 这块数组左上角的画布坐标。"""
    xs = np.arange(w, dtype=np.float32) + ox + 0.5
    ys = np.arange(h, dtype=np.float32) + oy + 0.5
    X, Y = np.meshgrid(xs, ys)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(dx, dy) or 1.0
    if radial:
        t = np.sqrt((X - p0[0]) ** 2 + (Y - p0[1]) ** 2) / L
    else:
        t = ((X - p0[0]) * dx + (Y - p0[1]) * dy) / (L * L)
    t = np.clip(t, 0, 1)[..., None]
    a = np.asarray(c0, np.float32) / 255.0
    b = np.asarray(c1, np.float32) / 255.0
    return a + (b - a) * t


def apply_gradient(doc: Document, layer: Layer, p0, p1, c0, c1, radial: bool, opacity: float) -> None:
    if not layer.has_pixels:
        return
    g = gradient(layer.w, layer.h, p0, p1, c0, c1, radial, layer.x, layer.y)
    a = g[..., 3] * opacity
    sel = doc.sel_local(layer)
    if sel is not None:
        a = a * sel
    new = over(layer.pixels, g[..., :3], a)
    doc.replace_pixels(layer.id, new, _t("渐变"))


def apply_bucket(doc: Document, layer: Layer, cx: int, cy: int, color, tolerance: float, contiguous: bool,
                 opacity: float, sample_all: bool = False) -> bool:
    if not layer.has_pixels:
        return False
    if sample_all:
        src = crop_canvas(doc.flat_rgba(), layer.x, layer.y, layer.w, layer.h, 0)
    else:
        src = to8(layer.pixels)
    lx, ly = int(cx - layer.x), int(cy - layer.y)
    if not (0 <= lx < layer.w and 0 <= ly < layer.h):
        return False
    m = bucket_mask(src, lx, ly, tolerance, contiguous).astype(np.float32) / 255.0
    sel = doc.sel_local(layer)
    if sel is not None:
        m *= sel
    if not m.any():
        return False
    new = over(layer.pixels, np.array(color[:3], np.float32) / 255.0, m * opacity)
    doc.replace_pixels(layer.id, new, _t("油漆桶"))
    return True
