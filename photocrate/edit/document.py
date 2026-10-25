"""修图文档：图层栈 + 选区 + 历史记录。

坐标一律是画布像素，左上角 (0, 0)。图层三种：
- pixel：一块 RGBA（pixels，uint8 或 uint16），左上角在画布的 (x, y)；可以比画布大或小（置入的图片）。
- text：params 是文字参数，pixels 是按参数画出来的结果，改参数就重画，所以一直可以再编辑。
- adjust：调整图层，params 是 adjust.py 的参数，作用在下面所有图层合成后的结果上。
蒙版 mask 是 uint8（255 = 显示）：像素 / 文字图层的蒙版与 pixels 同尺寸、跟着图层移动；调整图层的蒙版与画布同尺寸。

渲染：render(level, rect) 在 2^-level 的缩放级别上合成画布的一块。每个图层按级别缓存缩小的代理图（盒式平均；
宽高先补齐到 2^level 的整数倍，所以只更新一小块时结果与整张重算完全一致）。画笔只改一小块，界面只重算这一块；
拖动调整图层的滑块时，“这一层下面所有图层的合成结果”有缓存（hot）。

位深：文档是 8 位或 16 位（depth），新建 / 合并出来的像素用文档的位深；每个图层按自己数组的类型换算成 0–1，
所以两种位深的图层可以放在同一个文档里（文字图层总是 8 位渲染）。蒙版和选区一直是 uint8。

历史记录有两种步骤：
- 局部步骤（画笔的一笔）：记下改动区域改之前 / 之后的像素；
- 状态步骤（其他一切）：记下改之前 / 之后整个文档的状态——图层列表、属性、像素数组的引用。
  约定：除画笔外，任何改动都换一个新数组而不是原地改，所以记引用就够了，不用复制像素。
"""
from __future__ import annotations

import copy
import itertools
import math
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from . import adjust
from .blend import composite
from ..i18n import t as _t

_ids = itertools.count(1)

KIND_NAMES = {"pixel": _t("图层"), "text": _t("文字"), "adjust": _t("调整"), "group": _t("图层组")}


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


# ---------------------------------------------------------------------------
# 位深
# ---------------------------------------------------------------------------

DTYPES = {8: np.uint8, 16: np.uint16}


def maxval(a) -> float:
    """数组（或 dtype）的满值：uint8 → 255，uint16 → 65535。"""
    dt = a.dtype if isinstance(a, np.ndarray) else np.dtype(a)
    return 65535.0 if dt == np.uint16 else 255.0


def to_float(a: np.ndarray) -> np.ndarray:
    return np.multiply(a, np.float32(1 / maxval(a)), dtype=np.float32)


def from_float(f: np.ndarray, dtype=np.uint8) -> np.ndarray:
    m = maxval(dtype)
    return np.clip(f * np.float32(m) + np.float32(0.5), 0, m).astype(dtype)


def convert(a: np.ndarray, dtype) -> np.ndarray:
    """换位深（8 ↔ 16 位）；类型已经对了就原样返回。"""
    dtype = np.dtype(dtype)
    if a.dtype == dtype:
        return a
    if dtype == np.uint16:
        return a.astype(np.uint16) * np.uint16(257)
    return ((a.astype(np.uint32) + 128) // 257).astype(np.uint8)


def to8(a: np.ndarray) -> np.ndarray:
    return convert(a, np.uint8)


# ---------------------------------------------------------------------------
# 代理图（按级别缩小）
# ---------------------------------------------------------------------------

def _downscale(arr: np.ndarray, level: int) -> np.ndarray:
    """宽高补齐到 2^level 的倍数（复制边缘）后按整数倍盒式平均。RGBA 有透明时先预乘，避免边缘发黑。"""
    import cv2
    if level <= 0:
        return arr
    f = 1 << level
    h, w = arr.shape[:2]
    H, W = _ceil_div(h, f) * f, _ceil_div(w, f) * f
    rgba = arr.ndim == 3 and arr.shape[2] == 4
    M = maxval(arr)
    premul = rgba and int(arr[..., 3].min()) < M
    src = arr
    if premul:
        src = arr.astype(np.float32)
        src[..., :3] *= src[..., 3:4] / M
    if (H, W) != (h, w):
        src = cv2.copyMakeBorder(src, 0, H - h, 0, W - w, cv2.BORDER_REPLICATE)
    out = cv2.resize(src, (W // f, H // f), interpolation=cv2.INTER_AREA)
    if premul:
        a = out[..., 3:4]
        out[..., :3] = np.where(a > 0.5 * M / 255, out[..., :3] * M / np.maximum(a, 1e-3), 0)
        out = np.clip(out + 0.5, 0, M).astype(arr.dtype)
    return out


def _update_proxy(proxy: np.ndarray, src: np.ndarray, level: int, rect) -> None:
    f = 1 << level
    x, y, w, h = rect
    H, W = src.shape[:2]
    x0, y0 = max(0, (x // f) * f), max(0, (y // f) * f)
    x1, y1 = min(W, _ceil_div(x + w, f) * f), min(H, _ceil_div(y + h, f) * f)
    if x0 >= x1 or y0 >= y1:
        return
    d = _downscale(src[y0:y1, x0:x1], level)
    py, px = y0 // f, x0 // f
    d = d[: proxy.shape[0] - py, : proxy.shape[1] - px]
    proxy[py:py + d.shape[0], px:px + d.shape[1]] = d


# ---------------------------------------------------------------------------
# 图层
# ---------------------------------------------------------------------------

class Layer:
    def __init__(self, kind: str, name: str, pixels: np.ndarray | None = None, x: int = 0, y: int = 0,
                 params: dict | None = None, lid: int | None = None):
        self.id = lid if lid is not None else next(_ids)
        self.kind = kind
        self.name = name
        self.pixels = pixels
        self.x, self.y = int(x), int(y)
        self.visible = True
        self.opacity = 1.0
        self.blend = "normal"
        self.mask: np.ndarray | None = None
        self.mask_enabled = True
        self.params: dict = params if params is not None else {}
        self.clip = False                          # 剪贴蒙版：只在下面那个“基底”图层有像素的地方显示 / 生效
        self.fill = 1.0                            # 填充不透明度：只影响图层自己的像素，不影响图层样式
        self.style: dict = {}                      # 图层样式（styles.py）：投影、描边、发光…
        self.lock_alpha = self.lock_pixels = self.lock_pos = self.lock_all = False
        self.parent: int | None = None             # 所在图层组的 id（None = 不在组里）
        self.collapsed = False                     # 图层组在面板里是否折叠
        self.version = 0
        self.mask_version = 0
        self.xform = None      # 自由变换预览（图层像素 → 画布）：2×3 / 3×3 矩阵或变形网格，提交前只影响显示
        self._px: dict[int, np.ndarray] = {}
        self._mk: dict[int, np.ndarray] = {}

    # ---- 尺寸 ----
    @property
    def w(self) -> int:
        return 0 if self.pixels is None else self.pixels.shape[1]

    @property
    def h(self) -> int:
        return 0 if self.pixels is None else self.pixels.shape[0]

    @property
    def has_pixels(self) -> bool:
        return self.kind in ("pixel", "text") and self.pixels is not None

    @property
    def canvas_sized(self) -> bool:
        """调整图层和图层组没有自己的像素，蒙版和画布一样大、不跟着移动。"""
        return self.kind in ("adjust", "group")

    def bounds(self, doc: "Document") -> tuple[int, int, int, int]:
        if self.canvas_sized:
            return 0, 0, doc.width, doc.height
        return self.x, self.y, self.w, self.h

    # ---- 代理图 ----
    def proxy(self, level: int) -> tuple[np.ndarray | None, np.ndarray | None]:
        px = mk = None
        if self.pixels is not None:
            if level <= 0:
                px = self.pixels
            else:
                px = self._px.get(level)
                if px is None:
                    px = self._px[level] = _downscale(self.pixels, level)
        if self.mask is not None and self.mask_enabled:
            if level <= 0:
                mk = self.mask
            else:
                mk = self._mk.get(level)
                if mk is None:
                    mk = self._mk[level] = _downscale(self.mask, level)
        return px, mk

    def touch(self, rect=None) -> None:
        """像素变了：rect（图层内坐标 x, y, w, h）为 None 表示整个图层。"""
        self.version += 1
        if rect is None or self.pixels is None:
            self._px.clear()
            return
        for lv, prox in self._px.items():
            _update_proxy(prox, self.pixels, lv, rect)

    def touch_mask(self, rect=None) -> None:
        self.mask_version += 1
        if rect is None or self.mask is None:
            self._mk.clear()
            return
        for lv, prox in self._mk.items():
            _update_proxy(prox, self.mask, lv, rect)

    def render_text(self) -> None:
        from .text import render_text
        self.pixels = render_text(self.params)
        self.touch()

    # ---- 历史记录用 ----
    def snapshot(self) -> dict:
        return {"id": self.id, "kind": self.kind, "name": self.name, "pixels": self.pixels, "x": self.x, "y": self.y,
                "visible": self.visible, "opacity": self.opacity, "blend": self.blend, "mask": self.mask,
                "mask_enabled": self.mask_enabled, "params": copy.deepcopy(self.params), "clip": self.clip,
                "parent": self.parent, "collapsed": self.collapsed, "fill": self.fill,
                "style": copy.deepcopy(self.style), "locks": (self.lock_alpha, self.lock_pixels, self.lock_pos,
                                                             self.lock_all)}

    @property
    def locked_pixels(self) -> bool:
        return self.lock_pixels or self.lock_all

    @property
    def locked_pos(self) -> bool:
        return self.lock_pos or self.lock_all

    @property
    def locked_alpha(self) -> bool:
        return self.lock_alpha or self.lock_all


@dataclass
class SourceInfo:
    path: str = ""            # 原片 / 打开的文件
    name: str = ""
    exif: bytes | None = None
    icc: bytes | None = None
    gid: int | None = None
    project: str = ""         # 工程文件（保存过或从工程打开）
    suffix: str = ""          # 导出 / 工程文件名在原名后加的后缀；空 = 「_修图」（合并曝光是「_HDR」）


# ---------------------------------------------------------------------------
# 历史记录
# ---------------------------------------------------------------------------

@dataclass
class Step:
    name: str
    kind: str                       # state / patch
    before: Any = None
    after: Any = None
    key: str | None = None
    layer_id: int | None = None
    target: str = "pixels"
    rect: tuple | None = None
    nbytes: int = 0


def _arrays(state: dict) -> dict[int, np.ndarray]:
    out = {}
    for s in state["layers"]:
        for k in ("pixels", "mask"):
            a = s.get(k)
            if a is not None:
                out[id(a)] = a
    if state.get("sel") is not None:
        out[id(state["sel"])] = state["sel"]
    return out


class History:
    MAX_STEPS = 60
    MAX_BYTES = 1_500_000_000

    def __init__(self, doc: "Document"):
        self.doc = doc
        self.steps: list[Step] = []
        self.index = 0                  # 已经应用了几步（undo 后变小）

    @property
    def can_undo(self) -> bool:
        return self.index > 0

    @property
    def can_redo(self) -> bool:
        return self.index < len(self.steps)

    def names(self) -> list[str]:
        return [s.name for s in self.steps]

    def push_state(self, name: str, before: dict, key: str | None = None) -> None:
        after = self.doc.state()
        if key and self.index == len(self.steps) and self.steps and self.steps[-1].key == key \
                and self.steps[-1].kind == "state":
            self.steps[-1].after = after             # 连续拖同一个滑块只算一步
            self.doc.modified = True
            return
        freed = _arrays(before)
        for k in _arrays(after):
            freed.pop(k, None)
        self._push(Step(name, "state", before, after, key, nbytes=sum(a.nbytes for a in freed.values())))

    def push_patch(self, name: str, layer_id: int, target: str, rect, before: np.ndarray, after: np.ndarray) -> None:
        self._push(Step(name, "patch", before, after, None, layer_id, target, tuple(rect),
                        before.nbytes + after.nbytes))

    def _push(self, step: Step) -> None:
        del self.steps[self.index:]
        self.steps.append(step)
        self.index = len(self.steps)
        self.doc.modified = True
        total = sum(s.nbytes for s in self.steps)
        while len(self.steps) > 1 and (len(self.steps) > self.MAX_STEPS or total > self.MAX_BYTES):
            total -= self.steps[0].nbytes
            self.steps.pop(0)
            self.index -= 1

    def _apply(self, step: Step, undo: bool) -> None:
        if step.kind == "state":
            self.doc.restore(step.before if undo else step.after)
            return
        layer = self.doc.layer(step.layer_id)
        if layer is None:
            return
        arr = layer.pixels if step.target == "pixels" else layer.mask
        x, y, w, h = step.rect
        arr[y:y + h, x:x + w] = step.before if undo else step.after
        if step.target == "pixels":
            layer.touch(step.rect)
        else:
            layer.touch_mask(step.rect)
        self.doc.version += 1
        self.doc.emit("pixels", (layer.x + x, layer.y + y, w, h) if not layer.canvas_sized else (x, y, w, h))

    def undo(self) -> str | None:
        if not self.can_undo:
            return None
        step = self.steps[self.index - 1]
        self._apply(step, True)
        self.index -= 1
        self.doc.modified = True
        self.doc.emit("history")
        return step.name

    def redo(self) -> str | None:
        if not self.can_redo:
            return None
        step = self.steps[self.index]
        self._apply(step, False)
        self.index += 1
        self.doc.modified = True
        self.doc.emit("history")
        return step.name

    def goto(self, i: int) -> None:
        while self.index > i and self.can_undo:
            self.undo()
        while self.index < i and self.can_redo:
            self.redo()


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def crop_canvas(arr: np.ndarray, x: int, y: int, w: int, h: int, fill: int = 0) -> np.ndarray:
    """从数组里取出 (x, y, w, h) 这一块；超出数组的部分填 fill。"""
    out = np.full((h, w) + arr.shape[2:], fill, arr.dtype)
    ix0, iy0 = max(0, x), max(0, y)
    ix1, iy1 = min(arr.shape[1], x + w), min(arr.shape[0], y + h)
    if ix0 < ix1 and iy0 < iy1:
        out[iy0 - y:iy1 - y, ix0 - x:ix1 - x] = arr[iy0:iy1, ix0:ix1]
    return out


def mask_bbox(m: np.ndarray | None) -> tuple[int, int, int, int] | None:
    if m is None:
        return None
    rows = np.flatnonzero(m.any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(m.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1] - cols[0] + 1), int(rows[-1] - rows[0] + 1)


def over(dst: np.ndarray, src_rgb, src_a: np.ndarray) -> np.ndarray:
    """把颜色 src_rgb（0–1，HxWx3 或 3 个数）按不透明度 src_a（HxW，0–1）画到 RGBA 的 dst 上，返回同类型的新数组。"""
    d = to_float(dst)
    da = d[..., 3]
    oa = src_a + da * (1 - src_a)
    src_rgb = np.asarray(src_rgb, np.float32)
    oc = (src_rgb * src_a[..., None] + d[..., :3] * (da * (1 - src_a))[..., None]) / np.maximum(oa, 1e-6)[..., None]
    return from_float(np.dstack([oc, oa]), dst.dtype)


def to_uint8(rgb: np.ndarray, a: np.ndarray) -> np.ndarray:
    return np.clip(np.dstack([rgb, a]) * 255.0 + 0.5, 0, 255).astype(np.uint8)


def to_dtype(rgb: np.ndarray, a: np.ndarray, dtype=np.uint8) -> np.ndarray:
    return to_uint8(rgb, a) if np.dtype(dtype) == np.uint8 else from_float(np.dstack([rgb, a]), dtype)


def _resize(arr: np.ndarray, w: int, h: int) -> np.ndarray:
    import cv2
    shrink = w < arr.shape[1] or h < arr.shape[0]
    interp = cv2.INTER_AREA if shrink else cv2.INTER_CUBIC
    rgba = arr.ndim == 3 and arr.shape[2] == 4
    M = maxval(arr)
    if rgba and int(arr[..., 3].min()) < M:
        f = arr.astype(np.float32)
        f[..., :3] *= f[..., 3:4] / M
        f = cv2.resize(f, (w, h), interpolation=interp)
        a = np.clip(f[..., 3:4], 0, M)
        f[..., :3] = np.where(a > 0.5 * M / 255, f[..., :3] * M / np.maximum(a, 1e-3), 0)
        f[..., 3:4] = a
        return np.clip(f + 0.5, 0, M).astype(arr.dtype)
    return cv2.resize(arr, (w, h), interpolation=interp)


def _warp(arr: np.ndarray, M: np.ndarray, w: int, h: int, inverse: bool = False, fill: int = 0) -> np.ndarray:
    import cv2
    flags = cv2.INTER_LINEAR | (cv2.WARP_INVERSE_MAP if inverse else 0)
    border = (fill,) * (arr.shape[2] if arr.ndim == 3 else 1)
    return cv2.warpAffine(arr, M.astype(np.float64), (int(w), int(h)), flags=flags,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=border)


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------

class Document:
    def __init__(self, width: int, height: int, depth: int = 8):
        self.width, self.height = int(width), int(height)
        self.depth = 16 if depth == 16 else 8   # 新建 / 合并出来的像素用这个位深
        self.layers: list[Layer] = []           # 自下而上
        self.active_id: int | None = None
        self.selection: np.ndarray | None = None
        self.channels: dict[str, np.ndarray] = {}    # 存储的选区（Alpha 通道）：名称 → 与画布同尺寸的 uint8
        self.quick_mask = False                       # 快速蒙版模式（界面状态，不进历史）
        self.mask_edit = False                  # True = 画笔画在当前图层的蒙版上
        self.history = History(self)
        self.source = SourceInfo()
        self.version = 0
        self.modified = False
        self.previews: dict[int, tuple[Any, Callable]] = {}   # 滤镜预览：图层 id → (键, fn(rgba, scale))
        self._preview_cache: dict = {}
        self._sel_proxy: dict = {}
        self._registry: dict[int, Layer] = {}
        self._below: tuple | None = None
        self._flat: tuple | None = None
        self.listeners: list[Callable[[str, Any], None]] = []

    # ---- 通知界面 ----
    def emit(self, kind: str, data: Any = None) -> None:
        for fn in list(self.listeners):
            fn(kind, data)

    def changed(self, kind: str = "layers", data: Any = None) -> None:
        self.version += 1
        self.emit(kind, data)

    # ---- 图层查找 ----
    def layer(self, lid: int | None) -> Layer | None:
        return next((l for l in self.layers if l.id == lid), None)

    def index_of(self, lid: int | None) -> int:
        return next((i for i, l in enumerate(self.layers) if l.id == lid), -1)

    @property
    def active(self) -> Layer | None:
        return self.layer(self.active_id)

    def set_active(self, lid: int) -> None:
        if self.layer(lid) is None:
            return
        self.active_id = lid
        lay = self.layer(lid)
        if lay.mask is None:
            self.mask_edit = False
        self.emit("active")

    @property
    def dtype(self):
        return DTYPES[self.depth]

    # ---- 状态 / 历史 ----
    def state(self) -> dict:
        return {"w": self.width, "h": self.height, "active": self.active_id, "sel": self.selection,
                "depth": self.depth, "channels": dict(self.channels), "layers": [l.snapshot() for l in self.layers]}

    def restore(self, st: dict) -> None:
        size_changed = (self.width, self.height) != (st["w"], st["h"])
        self.width, self.height = st["w"], st["h"]
        depth_changed = self.depth != st.get("depth", 8)
        self.depth = st.get("depth", 8)
        layers = []
        for s in st["layers"]:
            l = self._registry.get(s["id"])
            if l is None:
                l = Layer(s["kind"], s["name"], lid=s["id"])
                self._registry[l.id] = l
            if l.pixels is not s["pixels"]:
                l._px.clear()
            if l.mask is not s["mask"]:
                l._mk.clear()
            for k in ("kind", "name", "pixels", "x", "y", "visible", "opacity", "blend", "mask", "mask_enabled"):
                setattr(l, k, s[k])
            l.clip, l.parent, l.collapsed = s.get("clip", False), s.get("parent"), s.get("collapsed", False)
            l.fill, l.style = s.get("fill", 1.0), copy.deepcopy(s.get("style") or {})
            l.lock_alpha, l.lock_pixels, l.lock_pos, l.lock_all = s.get("locks", (False,) * 4)
            l.params = copy.deepcopy(s["params"])
            l.xform = None
            l.version += 1
            l.mask_version += 1
            layers.append(l)
        self.layers = layers
        self.selection = st["sel"]
        self.channels = dict(st.get("channels") or {})
        self._sel_proxy.clear()
        self.active_id = st["active"] if self.layer(st["active"]) else (layers[-1].id if layers else None)
        if self.active is None or self.active.mask is None:
            self.mask_edit = False
        self.version += 1
        if size_changed:
            self.emit("canvas")
        if depth_changed:
            self.emit("depth")
        self.emit("layers")
        self.emit("selection")

    @contextmanager
    def edit(self, name: str, key: str | None = None, kind: str = "layers"):
        before = self.state()
        yield
        self.history.push_state(name, before, key)
        self._sel_proxy.clear()
        self.changed(kind)
        if kind != "history":
            self.emit("history")

    def _register(self, layer: Layer) -> Layer:
        self._registry[layer.id] = layer
        return layer

    # ---- 图层组 ----
    # 组和普通图层放在同一个列表里：组的子图层（含更深的组）连续排在组的正下方，组排在它们上面
    # （和面板里看到的一样）。“单元” = 一个图层，或一个组连同它的全部子图层。
    def ancestors(self, l: Layer) -> list[Layer]:
        out, seen = [], set()
        p = self.layer(l.parent)
        while p is not None and p.id not in seen:
            out.append(p)
            seen.add(p.id)
            p = self.layer(p.parent)
        return out

    def in_group(self, l: Layer, gid: int) -> bool:
        return any(a.id == gid for a in self.ancestors(l))

    def shown(self, l: Layer) -> bool:
        """图层自己和所在的组都是显示的。"""
        return l.visible and all(a.visible for a in self.ancestors(l))

    def unit(self, lid: int) -> tuple[int, int]:
        """图层所在单元在列表里的范围 [a, b)：普通图层就是它自己，组还包括它下面的全部子图层。"""
        i = self.index_of(lid)
        if i < 0:
            return 0, 0
        a = i
        if self.layers[i].kind == "group":
            while a > 0 and self.in_group(self.layers[a - 1], lid):
                a -= 1
        return a, i + 1

    def children(self, gid: int | None) -> list[Layer]:
        return [l for l in self.layers if l.parent == gid]

    def clip_base(self, l: Layer) -> Layer | None:
        """剪贴蒙版的基底：同一层级里、下面最近的那个不是剪贴的图层。"""
        if not l.clip:
            return None
        sib = [s for s in self.layers if s.parent == l.parent]
        i = next((k for k, s in enumerate(sib) if s.id == l.id), 0)
        for s in reversed(sib[:i]):
            if not s.clip:
                return s
        return None

    # ---- 新建 / 删除 / 排序 ----
    def add_layer(self, layer: Layer, index: int | None = None, name: str | None = None) -> Layer:
        """index 为空：放在当前图层上面、同一个组里（当前选中的是组时放进组里最上面）。"""
        with self.edit(name or _t('新建{0}', KIND_NAMES.get(layer.kind, _t('图层')))):
            if index is None:
                i = self.index_of(self.active_id)
                cur = self.active
                if cur is not None and cur.kind == "group" and not cur.collapsed:
                    index, layer.parent = i, cur.id
                else:
                    index = i + 1 if i >= 0 else len(self.layers)
                    layer.parent = cur.parent if cur is not None else None
                    nxt = self.layers[index] if index < len(self.layers) else None
                    if nxt is not None and nxt.clip and nxt.parent == layer.parent and layer.kind != "group":
                        layer.clip = True                     # 插在剪贴图层中间：也跟着剪贴
            self.layers.insert(index, self._register(layer))
            self.active_id = layer.id
            self.mask_edit = False
        return layer

    def new_pixel_layer(self, name: str | None = None) -> Layer:
        n = sum(1 for l in self.layers if l.kind == "pixel")
        pix = np.zeros((self.height, self.width, 4), self.dtype)
        return self.add_layer(Layer("pixel", name or _t('图层 {0}', n), pix))

    def new_group(self) -> Layer:
        n = sum(1 for l in self.layers if l.kind == "group") + 1
        g = Layer("group", _t('组 {0}', n))
        g.blend = "pass_through"
        return self.add_layer(g, name=_t("新建图层组"))

    def group_layers(self, lid: int | None = None) -> Layer | None:
        """把一个图层（或组）放进新建的组里（Ctrl+G）。"""
        l = self.layer(self.active_id if lid is None else lid)
        if l is None:
            return None
        a, b = self.unit(l.id)
        n = sum(1 for x in self.layers if x.kind == "group") + 1
        g = self._register(Layer("group", _t('组 {0}', n)))
        g.blend = "pass_through"
        with self.edit(_t("图层编组")):
            g.parent = l.parent
            l.parent = g.id
            if l.clip:
                l.clip = False
            self.layers.insert(b, g)
            self.active_id = g.id
            self.mask_edit = False
        return g

    def ungroup(self, gid: int | None = None) -> None:
        """取消编组（Ctrl+Shift+G）：子图层留在原位，组本身去掉。"""
        g = self.layer(self.active_id if gid is None else gid)
        if g is None or g.kind != "group":
            return
        with self.edit(_t("取消编组")):
            kids = self.children(g.id)
            for c in kids:
                c.parent = g.parent
                c.version += 1
            self.layers.remove(g)
            self.active_id = kids[-1].id if kids else (self.layers[-1].id if self.layers else None)
            self.mask_edit = False

    def toggle_clip(self, lid: int | None = None) -> None:
        """创建 / 释放剪贴蒙版（Ctrl+Alt+G）：只在下面那一层有像素的地方显示（调整图层只调下面那一层）。"""
        l = self.layer(self.active_id if lid is None else lid)
        if l is None:
            return
        if not l.clip:
            a, _ = self.unit(l.id)
            if not any(s.parent == l.parent for s in self.layers[:a]):       # 下面没有同一层级的图层可当基底
                return
        with self.edit(_t("释放剪贴蒙版") if l.clip else _t("创建剪贴蒙版")):
            l.clip = not l.clip
            l.version += 1

    def new_adjust_layer(self, params: dict | None = None, name: str = _t("调整")) -> Layer:
        l = Layer("adjust", name, params=adjust.normalize(params))
        if self.selection is not None:
            l.mask = self.selection.copy()
        return self.add_layer(l, name=_t('新建调整图层「{0}」', name))

    def new_text_layer(self, params: dict, x: int, y: int) -> Layer:
        l = Layer("text", (params.get("text") or _t("文字")).splitlines()[0][:16] or _t("文字"), x=x, y=y,
                  params=dict(params))
        l.render_text()
        return self.add_layer(l, name=_t("新建文字图层"))

    def place_image(self, rgba: np.ndarray, name: str = _t("置入的图片")) -> Layer:
        h, w = rgba.shape[:2]
        s = min(1.0, 0.9 * self.width / w, 0.9 * self.height / h)
        if s < 1:
            rgba = _resize(rgba, max(1, int(w * s)), max(1, int(h * s)))
            h, w = rgba.shape[:2]
        l = Layer("pixel", name, np.ascontiguousarray(convert(rgba, self.dtype)), (self.width - w) // 2,
                  (self.height - h) // 2)
        return self.add_layer(l, name=_t('置入「{0}」', name))

    @staticmethod
    def _copy_layer(src: Layer, name: str | None = None) -> Layer:
        l = Layer(src.kind, src.name if name is None else name, None if src.pixels is None else src.pixels.copy(),
                  src.x, src.y, copy.deepcopy(src.params))
        l.visible, l.opacity, l.blend, l.mask_enabled = src.visible, src.opacity, src.blend, src.mask_enabled
        l.clip, l.parent, l.collapsed = src.clip, src.parent, src.collapsed
        l.fill, l.style = src.fill, copy.deepcopy(src.style)
        l.lock_alpha, l.lock_pixels, l.lock_pos, l.lock_all = src.lock_alpha, src.lock_pixels, src.lock_pos, src.lock_all
        l.mask = None if src.mask is None else src.mask.copy()
        return l

    def duplicate_layer(self, lid: int) -> Layer | None:
        src = self.layer(lid)
        if src is None:
            return None
        a, b = self.unit(lid)
        if b - a == 1:
            return self.add_layer(self._copy_layer(src, src.name + _t(" 拷贝")), b, _t("复制图层"))
        ids: dict[int, int] = {}                          # 复制整个组：子图层的 parent 换成新组
        copies = []
        for s in self.layers[a:b]:
            c = self._copy_layer(s, s.name + _t(" 拷贝") if s.id == lid else None)
            ids[s.id] = c.id
            copies.append(c)
        for c in copies:
            c.parent = ids.get(c.parent, c.parent)
        with self.edit(_t("复制图层组")):
            self.layers[b:b] = [self._register(c) for c in copies]
            self.active_id = copies[-1].id
            self.mask_edit = False
        return copies[-1]

    def delete_layer(self, lid: int) -> None:
        a, b = self.unit(lid)
        if b <= a or b - a >= len(self.layers):
            return
        with self.edit(_t("删除图层组") if b - a > 1 else _t("删除图层")):
            del self.layers[a:b]
            self.active_id = self.layers[max(0, min(a, len(self.layers)) - 1)].id
            self.mask_edit = False

    def move_layer(self, lid: int, delta: int) -> None:
        """上移 / 下移一层（Ctrl+] / Ctrl+[）。和 Photoshop 一样：碰到展开的组就移进去（折叠的组整个跳过），
        已经在组的最上 / 最下面时移出组。组连同子图层一起移动。"""
        l = self.layer(lid)
        if l is None:
            return
        a, b = self.unit(lid)
        p = l.parent
        L = self.layers
        moved = L[a:b]
        if delta > 0:
            if b >= len(L):
                return
            up = L[b]
            if up.id == p:                                     # 组里最上面：移到组的上面
                new_parent, target = self.layer(p).parent, b + 1
            elif up.kind == "group" and not up.collapsed:      # 上面是展开的组：移进它的最下面
                ua, _ = self.unit(up.id)
                new_parent, target = up.id, ua
            else:
                _, ub = self.unit(up.id)
                new_parent, target = p, ub
        else:
            below = L[a - 1] if a > 0 else None
            if below is None or below.parent != p:
                if p is None:
                    return
                new_parent, target = self.layer(p).parent, a       # 组里最下面：移出组（位置不变）
            elif below.kind == "group" and not below.collapsed:  # 下面是展开的组：移进它的最上面
                new_parent, target = below.id, a - 1
            else:
                ba, _ = self.unit(below.id)
                new_parent, target = p, ba
        with self.edit(_t("调整图层顺序")):
            rest = L[:a] + L[b:]
            if target > a:
                target -= len(moved)
            l.parent = new_parent
            self.layers = rest[:target] + moved + rest[target:]
            l.version += 1

    def descendants(self, gid: int) -> list[Layer]:
        a, b = self.unit(gid)
        return self.layers[a:b - 1]

    def set_props(self, lid: int, key: str | None = None, name: str = _t("图层属性"), **kw) -> None:
        l = self.layer(lid)
        if l is None:
            return
        with self.edit(name, key):
            for k, v in kw.items():
                setattr(l, k, v)
            l.version += 1

    def set_params(self, lid: int, params: dict, key: str | None = None, name: str = _t("调整")) -> None:
        l = self.layer(lid)
        if l is None:
            return
        with self.edit(name, key):
            l.params = copy.deepcopy(params)
            if l.kind == "text":
                l.render_text()
            l.version += 1

    def translate(self, lid: int, dx: int, dy: int, before: dict | None = None) -> None:
        """移动图层。before 不为空时说明界面已经边拖边改好了位置，这里只记一步历史。"""
        l = self.layer(lid)
        if l is None or l.kind == "adjust" or l.locked_pos:
            return
        st = before or self.state()
        if before is None:
            for m in self.movable(l):
                m.x += int(dx)
                m.y += int(dy)
                m.version += 1
        l.version += 1
        self.history.push_state(_t("移动"), st)
        self.changed("layers")
        self.emit("history")

    def movable(self, l: Layer) -> list[Layer]:
        """移动这个图层时一起动的图层：组 = 组里所有像素 / 文字图层。"""
        if l.kind == "group":
            return [d for d in self.descendants(l.id) if not d.canvas_sized]
        return [] if l.canvas_sized else [l]

    # ---- 蒙版 ----
    def add_mask(self, lid: int, mode: str = "reveal") -> None:
        """mode: reveal（全白）/ hide（全黑）/ selection（按当前选区）。"""
        l = self.layer(lid)
        if l is None or l.kind == "text" or l.mask is not None:
            return
        with self.edit(_t("添加图层蒙版")):
            x, y, w, h = l.bounds(self)
            if mode == "selection" and self.selection is not None:
                l.mask = crop_canvas(self.selection, x, y, w, h, 0)
            else:
                l.mask = np.full((h, w), 0 if mode == "hide" else 255, np.uint8)
            l.mask_enabled = True
            l.touch_mask()
            self.mask_edit = True

    def delete_mask(self, lid: int) -> None:
        l = self.layer(lid)
        if l is None or l.mask is None:
            return
        with self.edit(_t("删除图层蒙版")):
            l.mask = None
            l.touch_mask()
            self.mask_edit = False

    def apply_mask(self, lid: int) -> None:
        l = self.layer(lid)
        if l is None or l.mask is None or not l.has_pixels:
            return
        with self.edit(_t("应用图层蒙版")):
            px = l.pixels.copy()
            px[..., 3] = (px[..., 3].astype(np.uint32) * l.mask // 255).astype(px.dtype)
            l.pixels, l.mask = px, None
            l.kind = "pixel"
            l.touch()
            l.touch_mask()
            self.mask_edit = False

    def invert_mask(self, lid: int) -> None:
        l = self.layer(lid)
        if l is None or l.mask is None:
            return
        with self.edit(_t("反相蒙版")):
            l.mask = 255 - l.mask
            l.touch_mask()

    # ---- 选区 ----
    def set_selection(self, mask: np.ndarray | None, name: str = _t("选区")) -> None:
        if mask is not None and not mask.any():
            mask = None
        if mask is None and self.selection is None:
            return
        with self.edit(name, kind="selection"):
            self.selection = mask

    def select_all(self) -> None:
        self.set_selection(np.full((self.height, self.width), 255, np.uint8), _t("全选"))

    def deselect(self) -> None:
        self.set_selection(None, _t("取消选择"))

    def invert_selection(self) -> None:
        sel = self.selection
        self.set_selection(np.full((self.height, self.width), 255, np.uint8) if sel is None else 255 - sel, _t("反选"))

    def feather_selection(self, radius: float) -> None:
        import cv2
        if self.selection is None or radius <= 0:
            return
        self.set_selection(cv2.GaussianBlur(self.selection, (0, 0), radius / 2), _t("羽化"))

    def grow_selection(self, px: int) -> None:
        import cv2
        if self.selection is None or not px:
            return
        hard = (self.selection > 127).astype(np.uint8)
        if px > 0:
            dist = cv2.distanceTransform(1 - hard, cv2.DIST_L2, 5)
            out = (dist <= px).astype(np.uint8) * 255
        else:
            dist = cv2.distanceTransform(hard, cv2.DIST_L2, 5)
            out = (dist > -px).astype(np.uint8) * 255
        self.set_selection(out, _t("扩展选区") if px > 0 else _t("收缩选区"))

    def save_selection(self, name: str) -> str:
        """选择 → 存储选区：存成一个 Alpha 通道（同名覆盖）。返回实际用的名称。"""
        if self.selection is None:
            return ""
        name = (name or "").strip() or _t('Alpha {0}', len(self.channels) + 1)
        with self.edit(_t('存储选区「{0}」', name), kind="selection"):
            self.channels = dict(self.channels)
            self.channels[name] = self.selection.copy()
        return name

    def load_selection(self, name: str, mode: str = "new", invert: bool = False) -> None:
        """选择 → 载入选区：mode = new / add / sub / and。"""
        ch = self.channels.get(name)
        if ch is None:
            return
        m = 255 - ch if invert else ch
        old = self.selection
        if mode == "add" and old is not None:
            m = np.maximum(old, m)
        elif mode == "sub":
            m = np.minimum(old, 255 - m) if old is not None else np.zeros_like(m)
        elif mode == "and":
            m = np.minimum(old, m) if old is not None else np.zeros_like(m)
        self.set_selection(m.copy(), _t('载入选区「{0}」', name))

    def delete_channel(self, name: str) -> None:
        if name not in self.channels:
            return
        with self.edit(_t('删除通道「{0}」', name), kind="selection"):
            self.channels = {k: v for k, v in self.channels.items() if k != name}

    def selection_bbox(self):
        return mask_bbox(self.selection)

    def sel_local(self, layer: Layer) -> np.ndarray | None:
        """选区换算到图层坐标（float32 0–1）；没有选区返回 None。"""
        if self.selection is None:
            return None
        x, y, w, h = layer.bounds(self)
        return crop_canvas(self.selection, x, y, w, h, 0).astype(np.float32) / 255.0

    # ---- 像素操作（都在选区内，没有选区 = 整个图层）----
    def _editable(self, lid: int) -> Layer | None:
        l = self.layer(lid)
        if l is None or l.canvas_sized or l.locked_pixels:
            return None
        return l

    def rasterize(self, lid: int, record: bool = True) -> None:
        l = self.layer(lid)
        if l is None or l.kind != "text":
            return
        if record:
            with self.edit(_t("栅格化文字")):
                l.kind = "pixel"
                l.version += 1
        else:
            l.kind = "pixel"
            l.version += 1

    def clear_selected(self, lid: int) -> None:
        l = self._editable(lid)
        if l is None:
            return
        sel = self.sel_local(l)
        with self.edit(_t("清除")):
            l.kind = "pixel"
            px = l.pixels.copy()
            if sel is None:
                px[...] = 0
            else:
                px[..., 3] = np.clip(px[..., 3] * (1 - sel) + 0.5, 0, maxval(px)).astype(px.dtype)
            l.pixels = px
            l.touch()

    def fill(self, lid: int, rgba: tuple[int, int, int, int], opacity: float = 1.0) -> None:
        l = self._editable(lid)
        if l is None:
            return
        sel = self.sel_local(l)
        a = np.full((l.h, l.w), opacity * rgba[3] / 255.0, np.float32) if sel is None else sel * opacity * rgba[3] / 255
        with self.edit(_t("填充")):
            l.kind = "pixel"
            new = over(l.pixels, np.array(rgba[:3], np.float32) / 255.0, a)
            if l.locked_alpha:                                  # 锁定透明像素：只在已有的像素上填色
                new[..., 3] = l.pixels[..., 3]
            l.pixels = new
            l.touch()

    def layer_via_copy(self, cut: bool = False) -> Layer | None:
        l = self.active
        if l is None:
            return None
        if l.canvas_sized or self.selection is None:
            return self.duplicate_layer(l.id)
        sel = self.sel_local(l)
        bb = mask_bbox((l.pixels[..., 3] > 0) & (sel > 0))
        if bb is None:
            return None
        x, y, w, h = bb
        M = maxval(l.pixels)
        part = l.pixels[y:y + h, x:x + w].copy()
        part[..., 3] = np.clip(part[..., 3] * sel[y:y + h, x:x + w] + 0.5, 0, M).astype(part.dtype)
        nl = Layer("pixel", l.name + (_t(" 剪切") if cut else _t(" 拷贝")), part, l.x + x, l.y + y)
        nl.parent = l.parent
        with self.edit(_t("通过剪切的图层") if cut else _t("通过拷贝的图层")):
            if cut:
                px = l.pixels.copy()
                px[..., 3] = np.clip(px[..., 3] * (1 - sel) + 0.5, 0, M).astype(px.dtype)
                l.pixels = px
                l.kind = "pixel"
                l.touch()
            self.layers.insert(self.index_of(l.id) + 1, self._register(nl))
            self.active_id = nl.id
            self.mask_edit = False
        return nl

    def replace_pixels(self, lid: int, new: np.ndarray, name: str, x: int | None = None, y: int | None = None) -> None:
        """整块换掉一个图层的像素（滤镜、修复、变换都走这里）。有选区时由调用方先按选区混合好。"""
        l = self.layer(lid)
        if l is None:
            return
        if l.locked_alpha and l.pixels is not None and new.shape == l.pixels.shape:   # 锁定透明像素：alpha 不变
            new = new.copy()
            new[..., 3] = convert(l.pixels, new.dtype)[..., 3]
        with self.edit(name):
            if l.mask is not None and new.shape[:2] != l.pixels.shape[:2] and x is not None:
                l.mask = crop_canvas(l.mask, int(x) - l.x, int(y) - l.y, new.shape[1], new.shape[0], 255)
                l.touch_mask()
            l.kind = "pixel"
            l.pixels = np.ascontiguousarray(new)
            if x is not None:
                l.x, l.y = int(x), int(y)
            l.touch()

    def apply_filter(self, lid: int, fid: str, params: dict | None = None) -> None:
        from .filters import FILTERS
        l = self._editable(lid)
        if l is None:
            return
        out = self._filtered_full(l, fid, params or {})
        self.replace_pixels(lid, out, FILTERS[fid].name)

    def _filtered_full(self, l: Layer, fid: str, params: dict) -> np.ndarray:
        """原尺寸应用滤镜：分条处理（每条上下多算一圈，避免整张转成浮点占太多内存）。"""
        from .filters import run
        src = l.pixels
        H, W = src.shape[:2]
        sel = self.sel_local(l)
        p = dict(params)
        if fid == "auto_tone":                    # 需要全图统计：先在缩小图上算好每个通道的拉伸范围
            small = _downscale(src, max(0, int(math.log2(max(1, max(H, W) / 1500)))))
            lohi = []
            ref = to_float(small)
            vis = ref[..., 3] > 0.5
            for i in range(3):
                v = ref[..., i][vis] if vis.any() else ref[..., i].ravel()
                lohi.append(np.percentile(v, (0.3, 99.7)) if v.size else (0.0, 1.0))

            def fn(rgba, _scale):
                out = rgba.copy()
                for i, (lo, hi) in enumerate(lohi):
                    if hi - lo > 1e-3:
                        out[..., i] = np.clip((rgba[..., i] - lo) / (hi - lo), 0, 1)
                return out
        else:
            fn = lambda rgba, scale: run(fid, rgba, scale, p)          # noqa: E731
        from .filters import FILTERS
        spec = FILTERS.get(fid)
        radius = max([float(p.get(k, 0) or 0) for k in ("radius", "distance")] + [0])
        m = int(math.ceil(radius * 2)) + 12 if fid in ("gaussian", "motion", "unsharp", "highpass", "denoise") else 0
        if spec is not None and spec.margin is not None:
            m = int(math.ceil(spec.margin(p)))
        step = H if spec is not None and spec.whole else 768
        if fid == "mosaic":
            c = max(1, int(round(float(p.get("cell", 16)))))
            step = max(c, (768 // c) * c)
        out = np.empty_like(src)
        for y0 in range(0, H, step):
            y1 = min(H, y0 + step)
            a0, a1 = max(0, y0 - m), min(H, y1 + m)
            chunk = to_float(src[a0:a1])
            res = fn(chunk, 1.0)[y0 - a0:y0 - a0 + (y1 - y0)]
            if sel is not None:
                s = sel[y0:y1][..., None]
                orig = chunk[y0 - a0:y0 - a0 + (y1 - y0)]
                res = orig + (res - orig) * s
            out[y0:y1] = from_float(res, src.dtype)
        return out

    def set_filter_preview(self, lid: int | None, fid: str | None = None, params: dict | None = None) -> None:
        self.previews.clear()
        self._preview_cache.clear()
        if lid is not None and fid:
            from .filters import run
            p = dict(params or {})
            key = (fid, tuple(sorted((k, v if isinstance(v, str) else float(v)) for k, v in p.items())))
            self.previews[lid] = (key, lambda rgba, scale: run(fid, rgba, scale, p))
        self.version += 1
        self.emit("pixels", None)

    def content_fill(self, lid: int) -> bool:
        """内容识别填充：用周围的画面补上选区（OpenCV 修复 + 匹配噪点）。"""
        from .paint import inpaint
        l = self._editable(lid)
        if l is None or self.selection is None:
            return False
        sel = crop_canvas(self.selection, l.x, l.y, l.w, l.h, 0)
        if not sel.any():
            return False
        out = inpaint(l.pixels, sel)
        self.replace_pixels(lid, out, _t("内容识别填充"))
        return True

    # ---- 合并 ----
    def _view(self, l: Layer, **kw) -> Layer:
        v = copy.copy(l)
        for k, val in kw.items():
            setattr(v, k, val)
        return v

    def compose_rect(self, layers: list[Layer], x: int, y: int, w: int, h: int, dtype=None) -> np.ndarray:
        """原尺寸合成任意一块（可以超出画布），返回文档位深（或 dtype）的 RGBA。"""
        dtype = self.dtype if dtype is None else dtype
        out = np.empty((h, w, 4), dtype)
        for y0 in range(0, h, 1024):
            hh = min(1024, h - y0)
            rgb, a = self._compose(layers, 0, (x, y + y0, w, hh), clip=False)
            out[y0:y0 + hh] = to_dtype(rgb, a, dtype)
        return out

    def _pixel_bounds(self, layers: list[Layer]):
        from .styles import pad
        boxes = []
        for l in layers:
            if not l.canvas_sized and l.pixels is not None:
                x, y, w, h = l.bounds(self)
                p = pad(l.style) if l.style else 0
                boxes.append((x - p, y - p, w + 2 * p, h + 2 * p))
        if not boxes:
            return 0, 0, self.width, self.height
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[0] + b[2] for b in boxes),
                max(b[1] + b[3] for b in boxes))

    def merge_down(self, lid: int) -> None:
        up = self.layer(lid)
        if up is not None and up.kind == "group":
            self.merge_group(lid)
            return
        i = self.index_of(lid)
        if i <= 0:
            return
        lo = self.layers[i - 1]
        if lo.canvas_sized or lo.parent != up.parent:
            return
        x0, y0, x1, y1 = self._pixel_bounds([lo] + ([up] if up.visible else []))
        base = self._view(lo, opacity=1.0, blend="normal", visible=True, mask_enabled=True, clip=False, parent=None)
        top = self._view(up, clip=up.clip, parent=None)
        pix = self.compose_rect([base, top], x0, y0, x1 - x0, y1 - y0)
        with self.edit(_t("向下合并")):
            lo.kind, lo.pixels, lo.x, lo.y, lo.mask = "pixel", pix, x0, y0, None
            lo.style, lo.fill = {}, 1.0                     # 样式已经合进像素
            lo.touch()
            lo.touch_mask()
            self.layers.pop(i)
            self.active_id = lo.id
            self.mask_edit = False

    def merge_group(self, gid: int) -> None:
        """合并组：组里的全部图层合成一个像素图层（组的不透明度、混合模式留在新图层上，蒙版合进像素）。"""
        g = self.layer(gid)
        if g is None or g.kind != "group":
            return
        a, b = self.unit(gid)
        members = self.layers[a:b]
        iso = self._view(g, opacity=1.0, blend="normal", visible=True, parent=None, clip=False)
        views = [self._view(m) for m in members[:-1]] + [iso]
        x0, y0, x1, y1 = self._pixel_bounds(members[:-1])
        pix = self.compose_rect(views, x0, y0, x1 - x0, y1 - y0)
        with self.edit(_t("合并组")):
            nl = self._register(Layer("pixel", g.name, pix, x0, y0))
            nl.opacity, nl.visible, nl.parent, nl.clip = g.opacity, g.visible, g.parent, g.clip
            nl.blend = "normal" if g.blend == "pass_through" else g.blend
            self.layers[a:b] = [nl]
            self.active_id = nl.id
            self.mask_edit = False

    def flatten(self) -> None:
        pix = self.render_full()
        with self.edit(_t("拼合图像")):
            l = self._register(Layer("pixel", _t("背景"), pix))
            self.layers = [l]
            self.active_id = l.id
            self.mask_edit = False

    def stamp_visible(self) -> Layer:
        return self.add_layer(Layer("pixel", _t("盖印图层"), self.render_full()), len(self.layers), _t("盖印可见图层"))

    def convert_depth(self, depth: int) -> None:
        """图像 → 模式 → 8 / 16 位：所有像素图层换位深（文字图层随文档）。"""
        depth = 16 if depth == 16 else 8
        if depth == self.depth:
            return
        dt = DTYPES[depth]
        with self.edit(_t("转换为 16 位/通道") if depth == 16 else _t("转换为 8 位/通道")):
            self.depth = depth
            for l in self.layers:
                if l.pixels is not None and l.pixels.dtype != dt:
                    l.pixels = convert(l.pixels, dt)
                    l.touch()
        self.emit("depth")

    # ---- 画布：裁剪 / 旋转 / 翻转 / 图像大小 ----
    def crop(self, x: float, y: float, w: float, h: float, angle: float = 0.0) -> None:
        """裁剪到 (x, y, w, h)；angle ≠ 0 时裁剪框绕自己的中心旋转 angle 度（顺时针为正），结果转正。"""
        w, h = max(1, int(round(w))), max(1, int(round(h)))
        if abs(angle) < 1e-3:
            self._crop_straight(int(round(x)), int(round(y)), w, h)
            return
        a = math.radians(angle)
        c, s = math.cos(a), math.sin(a)
        cx, cy = x + w / 2, y + h / 2
        # 新画布 (u, v) → 旧画布：old = 中心 + R · (u − w/2, v − h/2)
        M = np.array([[c, -s, cx - c * w / 2 + s * h / 2], [s, c, cy - s * w / 2 - c * h / 2]], np.float64)
        with self.edit(_t("裁剪")):
            for l in self.layers:
                if l.kind == "text":
                    l.kind = "pixel"
                if l.canvas_sized:
                    if l.mask is not None:
                        l.mask = _warp(l.mask, M, w, h, inverse=True, fill=255)
                        l.touch_mask()
                    continue
                Ml = M.copy()
                Ml[:, 2] -= (l.x, l.y)
                l.pixels = _warp(l.pixels, Ml, w, h, inverse=True)
                if l.mask is not None:
                    l.mask = _warp(l.mask, Ml, w, h, inverse=True, fill=255)
                    l.touch_mask()
                l.x = l.y = 0
                l.touch()
            self.width, self.height = w, h
            self.selection = None
            self.channels = {k: _warp(v, M, w, h, inverse=True, fill=0) for k, v in self.channels.items()}
        self.emit("canvas")

    def _crop_straight(self, x: int, y: int, w: int, h: int) -> None:
        with self.edit(_t("裁剪")):
            for l in self.layers:
                if l.canvas_sized:
                    if l.mask is not None:
                        l.mask = crop_canvas(l.mask, x, y, w, h, 255)
                        l.touch_mask()
                    continue
                l.x -= x
                l.y -= y
                if l.kind == "pixel":            # 删掉画布外的像素（文字图层保留，方便再编辑）
                    ix0, iy0 = max(0, -l.x), max(0, -l.y)
                    ix1, iy1 = min(l.w, w - l.x), min(l.h, h - l.y)
                    if ix0 >= ix1 or iy0 >= iy1:
                        ix0, iy0, ix1, iy1 = 0, 0, 1, 1
                    if (ix0, iy0, ix1, iy1) != (0, 0, l.w, l.h):
                        l.pixels = l.pixels[iy0:iy1, ix0:ix1].copy()
                        if l.mask is not None:
                            l.mask = l.mask[iy0:iy1, ix0:ix1].copy()
                            l.touch_mask()
                        l.x += ix0
                        l.y += iy0
                l.touch()
            self.width, self.height = w, h
            self.selection = None if self.selection is None else crop_canvas(self.selection, x, y, w, h, 0)
            self.channels = {k: crop_canvas(v, x, y, w, h, 0) for k, v in self.channels.items()}
        self.emit("canvas")

    def rotate(self, k: int) -> None:
        """k = 1 顺时针 90°，2 = 180°，3 = 逆时针 90°。"""
        k %= 4
        if not k:
            return
        W, H = self.width, self.height
        with self.edit({1: _t("顺时针旋转 90°"), 2: _t("旋转 180°"), 3: _t("逆时针旋转 90°")}[k]):
            for l in self.layers:
                if l.kind == "text":
                    l.kind = "pixel"
                if l.canvas_sized:
                    if l.mask is not None:
                        l.mask = np.ascontiguousarray(np.rot90(l.mask, -k))
                        l.touch_mask()
                    continue
                lx, ly, lw, lh = l.x, l.y, l.w, l.h
                if k == 1:
                    l.x, l.y = H - (ly + lh), lx
                elif k == 2:
                    l.x, l.y = W - (lx + lw), H - (ly + lh)
                else:
                    l.x, l.y = ly, W - (lx + lw)
                l.pixels = np.ascontiguousarray(np.rot90(l.pixels, -k))
                if l.mask is not None:
                    l.mask = np.ascontiguousarray(np.rot90(l.mask, -k))
                    l.touch_mask()
                l.touch()
            if k % 2:
                self.width, self.height = H, W
            if self.selection is not None:
                self.selection = np.ascontiguousarray(np.rot90(self.selection, -k))
            self.channels = {n: np.ascontiguousarray(np.rot90(v, -k)) for n, v in self.channels.items()}
        self.emit("canvas")

    def flip(self, horizontal: bool = True) -> None:
        W, H = self.width, self.height
        with self.edit(_t("水平翻转") if horizontal else _t("垂直翻转")):
            ax = 1 if horizontal else 0
            for l in self.layers:
                if l.kind == "text":
                    l.kind = "pixel"
                if l.canvas_sized:
                    if l.mask is not None:
                        l.mask = np.ascontiguousarray(np.flip(l.mask, ax))
                        l.touch_mask()
                    continue
                if horizontal:
                    l.x = W - (l.x + l.w)
                else:
                    l.y = H - (l.y + l.h)
                l.pixels = np.ascontiguousarray(np.flip(l.pixels, ax))
                if l.mask is not None:
                    l.mask = np.ascontiguousarray(np.flip(l.mask, ax))
                    l.touch_mask()
                l.touch()
            if self.selection is not None:
                self.selection = np.ascontiguousarray(np.flip(self.selection, ax))
            self.channels = {n: np.ascontiguousarray(np.flip(v, ax)) for n, v in self.channels.items()}

    def resize_image(self, w: int, h: int) -> None:
        w, h = max(1, int(w)), max(1, int(h))
        sx, sy = w / self.width, h / self.height
        with self.edit(_t("图像大小")):
            for l in self.layers:
                if l.canvas_sized:
                    if l.mask is not None:
                        l.mask = _resize(l.mask, w, h)
                        l.touch_mask()
                    continue
                l.x, l.y = int(round(l.x * sx)), int(round(l.y * sy))
                if l.kind == "text":
                    l.params["size"] = max(4, float(l.params.get("size", 48)) * math.sqrt(sx * sy))
                    l.params["stroke"] = float(l.params.get("stroke", 0)) * math.sqrt(sx * sy)
                    l.render_text()
                    continue
                nw, nh = max(1, int(round(l.w * sx))), max(1, int(round(l.h * sy)))
                l.pixels = _resize(l.pixels, nw, nh)
                if l.mask is not None:
                    l.mask = _resize(l.mask, nw, nh)
                    l.touch_mask()
                l.touch()
            self.width, self.height = w, h
            if self.selection is not None:
                self.selection = _resize(self.selection, w, h)
            self.channels = {n: _resize(v, w, h) for n, v in self.channels.items()}
        self.emit("canvas")

    def transform_layer(self, lid: int, M) -> None:
        """提交自由变换：M 把图层像素坐标映射到画布坐标——2×3 仿射、3×3 透视，或 {"mesh": 4×4 控制点} 变形。"""
        import cv2
        from . import warp
        l = self.layer(lid)
        if l is None or not l.has_pixels:
            return
        if isinstance(M, dict) or not warp.is_affine(M):
            self._transform_general(l, M)
            return
        M = warp.to3(M)[:2]
        w, h = l.w, l.h
        corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float64) @ M.T
        x0, y0 = np.floor(corners.min(axis=0)).astype(int)
        x1, y1 = np.ceil(corners.max(axis=0)).astype(int)
        nw, nh = max(1, x1 - x0), max(1, y1 - y0)
        src, msk = l.pixels, l.mask
        A = M[:, :2].copy()
        s = math.sqrt(abs(np.linalg.det(A)))
        if s < 0.7:                                      # 缩小很多时先盒式缩小，避免锯齿
            pw, ph = max(1, int(round(w * s))), max(1, int(round(h * s)))
            src = _resize(src, pw, ph)
            if msk is not None:
                msk = _resize(msk, pw, ph)
            A = A @ np.diag([w / pw, h / ph])
        Mt = np.hstack([A, (M[:, 2] - (x0, y0))[:, None]])
        px = cv2.warpAffine(src, Mt, (nw, nh), flags=cv2.INTER_CUBIC if s > 1.2 else cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        mk = None if msk is None else cv2.warpAffine(msk, Mt, (nw, nh), flags=cv2.INTER_LINEAR,
                                                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        with self.edit(_t("自由变换")):
            l.kind = "pixel"
            l.pixels, l.x, l.y = px, int(x0), int(y0)
            l.xform = None
            l.touch()
            if l.mask is not None:
                l.mask = mk
                l.touch_mask()

    def _transform_general(self, l: Layer, xf) -> None:
        """透视 / 扭曲 / 变形：按外接框分条渲染（变形网格每条单独算三角形编号图，内存不随图大小翻倍）。"""
        import cv2
        from . import warp
        w, h = l.w, l.h
        bx0, by0, bx1, by1 = warp.xform_bounds(xf, w, h)
        pad = 2
        x0, y0 = int(math.floor(bx0)) - pad, int(math.floor(by0)) - pad
        nw = max(1, min(30000, int(math.ceil(bx1)) + pad - x0))
        nh = max(1, min(30000, int(math.ceil(by1)) + pad - y0))
        mesh = isinstance(xf, dict)
        if not mesh:
            T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], np.float64)
            H = T @ warp.to3(xf)
            px = warp.perspective(l.pixels, H, nw, nh, cv2.INTER_CUBIC)
            mk = None if l.mask is None else warp.perspective(l.mask, H, nw, nh)
        else:
            ctrl = np.asarray(xf["mesh"], np.float64)
            px = np.zeros((nh, nw, 4), l.pixels.dtype)
            mk = None if l.mask is None else np.zeros((nh, nw), np.uint8)
            n = int(np.clip(max(w, h) / 24, 16, 64))
            for sy in range(0, nh, 1024):
                sh = min(1024, nh - sy)
                mx, my = warp.mesh_maps(ctrl, w, h, x0, y0 + sy, nw, sh, 1, n)
                px[sy:sy + sh] = cv2.remap(l.pixels, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                                           borderValue=(0, 0, 0, 0))
                if mk is not None:
                    mk[sy:sy + sh] = cv2.remap(l.mask, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                                               borderValue=0)
        name = _t("变形") if mesh else _t("透视 / 扭曲")
        with self.edit(name):
            l.kind = "pixel"
            l.pixels, l.x, l.y = np.ascontiguousarray(px), x0, y0
            l.xform = None
            l.touch()
            if l.mask is not None:
                l.mask = mk
                l.touch_mask()

    # ---- 渲染 ----
    def level_size(self, level: int) -> tuple[int, int]:
        f = 1 << max(0, level)
        return _ceil_div(self.width, f), _ceil_div(self.height, f)

    def _margin(self, layers: list[Layer], level: int) -> int:
        rc = adjust.RenderCtx(1 / (1 << level), self.width, self.height)
        return max([adjust.margin(l.params, rc) for l in layers if l.visible and l.kind == "adjust"] + [0])

    def _sig(self, layers: list[Layer], level: int, E) -> tuple:
        return (level, E, id(self.selection)) + tuple(
            (l.id, l.version, l.mask_version, l.visible, l.opacity, l.blend, l.x, l.y, l.mask_enabled, l.clip,
             l.parent, l.fill, _style_key(l.style), None if l.xform is None else _xf_key(l.xform),
             self.previews.get(l.id, (None,))[0])
            for l in layers)

    def render(self, level: int = 0, rect=None, hot: int | None = None, layers: list[Layer] | None = None):
        """合成画布的一块：level 级别（2^-level）上的 rect = (x, y, w, h)，None = 整张。返回 (rgb, alpha) float32。
        layers 不为空时只合成这几层（“看原图”用）。"""
        LW, LH = self.level_size(level)
        x, y, w, h = rect if rect is not None else (0, 0, LW, LH)
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(LW, int(x + w)), min(LH, int(y + h))
        if x0 >= x1 or y0 >= y1:
            return np.zeros((0, 0, 3), np.float32), np.zeros((0, 0), np.float32)
        return self._compose(self.layers if layers is None else layers, level, (x0, y0, x1 - x0, y1 - y0),
                             hot=hot if layers is None else None)

    @staticmethod
    def _tree(layers: list[Layer]) -> dict:
        """父 id → 子图层（自下而上）。父组不在这批图层里的当作顶层。"""
        ids = {l.id for l in layers}
        kids: dict = {}
        for l in layers:
            kids.setdefault(l.parent if l.parent in ids else None, []).append(l)
        return kids

    def _compose(self, layers: list[Layer], level: int, rect, hot: int | None = None, clip: bool = True):
        rx, ry, rw, rh = rect
        m = self._margin(layers, level)
        ex0, ey0, ex1, ey1 = rx - m, ry - m, rx + rw + m, ry + rh + m
        if clip:
            LW, LH = self.level_size(level)
            ex0, ey0, ex1, ey1 = max(0, ex0), max(0, ey0), min(LW, ex1), min(LH, ey1)
        E = (ex0, ey0, ex1 - ex0, ey1 - ey0)
        cb = np.zeros((E[3], E[2], 3), np.float32)
        ab = np.zeros((E[3], E[2]), np.float32)
        kids = self._tree(layers)
        roots = kids.get(None, [])
        ctx = (kids, level, E)
        hi = -1
        if hot is not None:                                # 正在调的图层所在的顶层单元（剪贴图层算到基底）
            h = next((l for l in layers if l.id == hot), None)
            byid = {l.id: l for l in layers}
            while h is not None and h.parent in byid:
                h = byid[h.parent]
            if h is not None and h in roots:
                hi = roots.index(h)
                while hi > 0 and roots[hi].clip:
                    hi -= 1
        empty = True                                       # 还没画过任何像素：第一层直接拷贝，不用走合成公式
        start = 0
        sig = None
        if hi > 0:
            pos = {l.id: i for i, l in enumerate(layers)}
            first = min(pos[l.id] for l in layers if l is roots[hi] or self._under(l, roots[hi], layers))
            sig = self._sig(layers[:first], level, E)
            if self._below is not None and self._below[0] == sig:
                cb, ab = self._below[1].copy(), self._below[2].copy()
                start, empty = hi, self._below[3]
            else:
                cb, ab, empty = self._comp_nodes(roots[:hi], cb, ab, ctx, empty)
                self._below = (sig, cb.copy(), ab.copy(), empty)
                start = hi
        cb, ab, empty = self._comp_nodes(roots[start:], cb, ab, ctx, empty)
        ox, oy = rx - ex0, ry - ey0
        return cb[oy:oy + rh, ox:ox + rw], ab[oy:oy + rh, ox:ox + rw]

    @staticmethod
    def _under(l: Layer, g: Layer, layers: list[Layer]) -> bool:
        byid = {x.id: x for x in layers}
        p = byid.get(l.parent)
        while p is not None:
            if p is g:
                return True
            p = byid.get(p.parent)
        return False

    def _comp_nodes(self, nodes: list[Layer], cb, ab, ctx, empty: bool):
        """按顺序合成同一层级的图层；剪贴图层和它的基底一起算。"""
        i = 0
        while i < len(nodes):
            base = nodes[i]
            j = i + 1
            while j < len(nodes) and nodes[j].clip:
                j += 1
            clipped = [c for c in nodes[i + 1:j] if c.visible and c.opacity > 0]
            if base.visible and base.opacity > 0:
                if clipped and base.kind != "adjust":
                    cb, ab, empty = self._clip_stack(base, clipped, cb, ab, ctx, empty)
                else:
                    cb, ab, empty = self._node(base, cb, ab, ctx, empty)
                    for c in clipped:                          # 基底是调整图层：剪贴不起作用，照常合成
                        cb, ab, empty = self._node(c, cb, ab, ctx, empty)
            i = j
        return cb, ab, empty

    def _node(self, l: Layer, cb, ab, ctx, empty: bool):
        kids, level, E = ctx
        if l.kind == "adjust":
            return self._apply_adjust(l, cb, level, E), ab, empty
        if l.kind == "group":
            return self._group(l, cb, ab, ctx, empty)
        if l.style and l.xform is None and _style_key(l.style):
            if self._apply_styled(l, cb, ab, level, E):
                empty = False
            return cb, ab, empty
        if self._apply_pixels(l, cb, ab, level, E, empty):
            empty = False
        return cb, ab, empty

    def _canvas_mask(self, l: Layer, level: int, E):
        _, mk = l.proxy(level)
        if mk is None:
            return None
        return crop_canvas(mk, E[0], E[1], E[2], E[3], 255).astype(np.float32) / 255.0

    def _group(self, g: Layer, cb, ab, ctx, empty: bool):
        kids, level, E = ctx
        members = kids.get(g.id, [])
        k = self._canvas_mask(g, level, E)
        if g.blend == "pass_through":                      # 穿透：组里的图层直接叠在下面的结果上
            mix = k is not None or g.opacity < 1
            cb0, ab0 = (cb.copy(), ab.copy()) if mix else (None, None)
            cb, ab, e2 = self._comp_nodes(members, cb, ab, ctx, empty)
            if mix:
                kk = (np.float32(g.opacity) if k is None else k * np.float32(g.opacity))
                kk3 = kk[..., None] if isinstance(kk, np.ndarray) else kk
                cb = cb0 + (cb - cb0) * kk3
                ab = ab0 + (ab - ab0) * kk
            return cb, ab, e2
        gcb = np.zeros_like(cb)                            # 其他模式：组先单独合成，再按组的模式叠上去
        gab = np.zeros_like(ab)
        gcb, gab, ge = self._comp_nodes(members, gcb, gab, ctx, True)
        if ge:
            return cb, ab, empty
        a = gab * np.float32(g.opacity)
        if k is not None:
            a = a * k
        if empty:
            return gcb, a, False
        co, ao = composite(cb, ab, gcb, a, g.blend)
        return co, ao, False

    def _clip_stack(self, base: Layer, clipped: list[Layer], cb, ab, ctx, empty: bool):
        """剪贴蒙版：基底单独画出来，上面的剪贴图层只改基底有像素的地方，最后按基底的模式 / 不透明度叠上去。"""
        kids, level, E = ctx
        gcb = np.zeros_like(cb)
        gab = np.zeros_like(ab)
        solo = self._view(base, opacity=1.0, blend="normal")
        gcb, gab, ge = self._node(solo, gcb, gab, ctx, True)
        if ge:
            return cb, ab, empty
        ones = np.ones_like(gab)
        for c in clipped:
            if c.kind == "adjust":
                gcb = self._apply_adjust(c, gcb, level, E)
            else:
                gcb, _, _ = self._node(c, gcb.copy(), ones.copy(), ctx, False)
        a = gab * np.float32(base.opacity)
        if empty:
            return gcb, a, False
        mode = "normal" if base.blend == "pass_through" else base.blend
        co, ao = composite(cb, ab, gcb, a, mode)
        return co, ao, False

    def _apply_adjust(self, l: Layer, cb, level: int, E):
        if adjust.is_identity(l.params):
            return cb
        rc = adjust.RenderCtx(1 / (1 << level), self.width, self.height, E[0], E[1])
        res = adjust.develop(cb, l.params, rc)
        _, mk = l.proxy(level)
        if mk is not None:
            a = crop_canvas(mk, E[0], E[1], E[2], E[3], 255).astype(np.float32) * (l.opacity / 255.0)
            return cb + (res - cb) * a[..., None]
        if l.opacity < 1:
            return cb + (res - cb) * l.opacity
        return res

    def _layer_proxy_f(self, l: Layer, level: int):
        """图层在这个级别上的像素（float32 RGBA 0–1）；有滤镜预览时是预览结果。"""
        px, _ = l.proxy(level)
        pv = self.previews.get(l.id)
        if pv is None:
            return px, False
        key = (l.id, l.version, level, pv[0], id(self.selection))
        hit = self._preview_cache.get(key)
        if hit is None:
            src = to_float(px)
            out = pv[1](src, 1 / (1 << level))
            if self.selection is not None:
                f = 1 << level
                sp = self._sel_level(level)
                s = crop_canvas(sp, int(round(l.x / f)), int(round(l.y / f)), src.shape[1], src.shape[0], 0)
                out = src + (out - src) * (s.astype(np.float32) / 255.0)[..., None]
            self._preview_cache.clear()
            hit = self._preview_cache[key] = out.astype(np.float32, copy=False)
        return hit, True

    def _sel_level(self, level: int) -> np.ndarray:
        key = (id(self.selection), level)
        p = self._sel_proxy.get(key)
        if p is None:
            p = self._sel_proxy[key] = _downscale(self.selection, level)
        return p

    def _apply_pixels(self, l: Layer, cb, ab, level: int, E, empty: bool = False) -> bool:
        px, mk = l.proxy(level)
        if px is None:
            return False
        src, is_float = self._layer_proxy_f(l, level)
        f = 1 << level
        ex, ey, ew, eh = E
        if l.xform is not None:
            from .warp import warp_any
            size = (l.w, l.h)
            warped = warp_any(src, l.xform, f, ex, ey, ew, eh, size)
            if not is_float:
                warped = to_float(warped)
            a = warped[..., 3] * (l.opacity * l.fill)
            if mk is not None:
                a *= warp_any(mk, l.xform, f, ex, ey, ew, eh, size).astype(np.float32) / 255.0
            if empty:
                cb[...] = warped[..., :3]
                ab[...] = a
                return True
            co, ao = composite(cb, ab, warped[..., :3], a, l.blend)
            cb[...] = co
            ab[...] = ao
            return True
        lx, ly = int(round(l.x / f)), int(round(l.y / f))
        ph, pw = src.shape[:2]
        ix0, iy0 = max(ex, lx), max(ey, ly)
        ix1, iy1 = min(ex + ew, lx + pw), min(ey + eh, ly + ph)
        if ix0 >= ix1 or iy0 >= iy1:
            return False
        s = src[iy0 - ly:iy1 - ly, ix0 - lx:ix1 - lx]
        s = s if is_float else to_float(s)
        a = s[..., 3] * np.float32(l.opacity * l.fill)
        if mk is not None:
            a *= np.multiply(mk[iy0 - ly:iy1 - ly, ix0 - lx:ix1 - lx], np.float32(1 / 255), dtype=np.float32)
        sl = (slice(iy0 - ey, iy1 - ey), slice(ix0 - ex, ix1 - ex))
        if empty:                                          # 底下是透明的：结果就是这一层本身
            cb[sl] = s[..., :3]
            ab[sl] = a
            return True
        co, ao = composite(cb[sl], ab[sl], s[..., :3], a, l.blend)
        cb[sl] = co
        ab[sl] = ao
        return True

    def _apply_styled(self, l: Layer, cb, ab, level: int, E) -> bool:
        """带图层样式的图层：整层（加上效果的边距）在这个级别上算好各个效果平面并缓存，再按区域叠上去。"""
        from . import styles
        px, mk = l.proxy(level)
        if px is None:
            return False
        f = 1 << level
        pl = int(math.ceil(styles.pad(l.style) / f)) + 2
        key = (l.id, l.version, l.mask_version, l.mask_enabled, level, _style_key(l.style),
               self.previews.get(l.id, (None,))[0], id(self.selection) if l.id in self.previews else None)
        cache = self.__dict__.setdefault("_style_cache", {})
        hit = cache.get(l.id)
        if hit is None or hit[0] != key:
            src, is_float = self._layer_proxy_f(l, level)
            s = src if is_float else to_float(src)
            A = s[..., 3].copy()
            if mk is not None:
                A *= mk.astype(np.float32) / 255.0
            Ap = np.pad(A, pl)
            planes = styles.planes(Ap, l.style, 1 / f)
            hit = (key, s[..., :3], A, planes)
            cache[l.id] = hit
            if len(cache) > 12:
                cache.pop(next(iter(cache)))
        _, rgb, A, planes = hit
        lx, ly = int(round(l.x / f)), int(round(l.y / f))
        ph, pw = A.shape
        ex, ey, ew, eh = E
        bx, by, bw, bh = lx - pl, ly - pl, pw + 2 * pl, ph + 2 * pl
        ix0, iy0 = max(ex, bx), max(ey, by)
        ix1, iy1 = min(ex + ew, bx + bw), min(ey + eh, by + bh)
        if ix0 >= ix1 or iy0 >= iy1:
            return False
        sl = (slice(iy0 - ey, iy1 - ey), slice(ix0 - ex, ix1 - ex))
        ps = (slice(iy0 - by, iy1 - by), slice(ix0 - bx, ix1 - bx))
        op = np.float32(l.opacity)
        c, a = cb[sl], ab[sl]
        shape = (iy1 - iy0, ix1 - ix0, 3)

        def put(col, alpha, mode):
            nonlocal c, a
            col = np.broadcast_to(np.asarray(col, np.float32), shape) if np.ndim(col) == 1 else col[ps]
            c, a = composite(c, a, col, alpha[ps] * op, mode)
        for where, col, alpha, mode in planes:
            if where == "behind":
                put(col, alpha, mode)
        rgbp = np.pad(rgb, ((pl, pl), (pl, pl), (0, 0)))
        Ap = np.pad(A, pl) * np.float32(l.fill)
        c, a = composite(c, a, rgbp[ps], Ap[ps] * op, l.blend)
        for where, col, alpha, mode in planes:
            if where != "behind":
                put(col, alpha, mode)
        cb[sl] = c
        ab[sl] = a
        return True

    def render_full(self, dtype=None) -> np.ndarray:
        """原尺寸合成整张画布，返回文档位深（或 dtype）的 RGBA。分条算（省内存），几条并行（numpy / OpenCV 计算时不占 GIL）。"""
        import os
        from concurrent.futures import ThreadPoolExecutor
        dtype = self.dtype if dtype is None else dtype
        out = np.empty((self.height, self.width, 4), dtype)
        step = 512

        def strip(y0: int) -> None:
            h = min(step, self.height - y0)
            rgb, a = self.render(0, (0, y0, self.width, h))
            out[y0:y0 + h] = to_dtype(rgb, a, dtype)

        ys = list(range(0, self.height, step))
        workers = max(1, min(4, (os.cpu_count() or 2) - 1, len(ys)))
        if workers == 1:
            for y0 in ys:
                strip(y0)
        else:
            with ThreadPoolExecutor(workers) as ex:
                list(ex.map(strip, ys))
        return out

    def flat_rgba(self) -> np.ndarray:
        """8 位的合成结果（魔棒、吸管、对象选择用），按文档版本缓存。"""
        if self._flat is None or self._flat[0] != self.version:
            self._flat = (self.version, self.render_full(np.uint8))
        return self._flat[1]

    def flat_level(self, max_side: int = 1600) -> tuple[np.ndarray, int]:
        """缩小的合成结果（长边不小于 max_side），返回 (uint8 RGBA, 缩小倍数)。对象选择这类本来就缩小算的用它，快得多。"""
        lvl = max(0, int(math.floor(math.log2(max(1.0, max(self.width, self.height) / max_side)))))
        hit = getattr(self, "_flat_lv", None)
        if hit is None or hit[0] != (self.version, lvl):
            rgb, a = self.render(lvl)
            hit = self._flat_lv = ((self.version, lvl), to_uint8(rgb, a))
        return hit[1], 1 << lvl

    def sample_layer_rgba(self, l: Layer) -> np.ndarray:
        """把一个图层放到画布坐标上（画布尺寸 8 位 RGBA）。"""
        if not l.has_pixels:
            return self.flat_rgba()
        return to8(crop_canvas(l.pixels, -l.x, -l.y, self.width, self.height, 0))


def _style_key(style) -> tuple:
    if not style:
        return ()
    from .styles import key
    return key(style)


def _xf_key(xf) -> bytes:
    if isinstance(xf, dict):
        return np.asarray(xf["mesh"], np.float64).tobytes()
    return np.asarray(xf, np.float64).tobytes()
