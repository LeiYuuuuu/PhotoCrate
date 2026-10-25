"""合并曝光（HDR）的界面：选项对话框、后台合并、在修图窗口里打开结果，以及「修补重影」对话框。

入口：「05 选片」多选几张后点「合并曝光…」、缩略图右键菜单、修图窗口「文件 → 合并曝光…」（从磁盘选文件）。
结果是一份修图文档：最上面「合并结果」，下面是对齐后的各张源图（参考帧可见，其余隐藏），
所以按住 \\ 看原图看到的是参考帧；源图层留着，自动去不干净的重影可以框出来，挑一张源图补上。
"""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QSpinBox,
                               QToolButton)

from ...edit import hdr
from ...edit.document import Layer, SourceInfo, convert, to8
from ...i18n import t as _t
from ..dialogs import _Base, info, run_with_progress
from ..theme import font
from ..widgets.deco import TacButton, caption

DEGHOST_CHOICES = [("normal", _t("标准（推荐）")), ("high", _t("强：连细小的晃动也去掉")),
                   ("low", _t("弱：只去明显的移动物体")), ("off", _t("关闭"))]


# ---------------------------------------------------------------------------
# 待合并的照片：照片库里的组，或者磁盘上的文件
# ---------------------------------------------------------------------------

class _Item:
    def __init__(self, name: str, path: str, ev: float | None, desc: str, group=None):
        self.name, self.path, self.ev, self.desc, self.group = name, path, ev, desc, group


def _desc(exposure, fnumber, iso) -> str:
    parts = []
    try:
        t = float(exposure)
        parts.append(f"1/{round(1 / t)} s" if 0 < t < 0.5 else f"{t:g} s")
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    if fnumber:
        parts.append(f"f/{float(fnumber):g}")
    if iso:
        parts.append(f"ISO {int(float(iso))}")
    return " · ".join(parts)


def items_from_groups(groups) -> list[_Item]:
    from ...ops.exporter import source_file
    out = []
    for g in groups:
        if getattr(g, "is_video", False):
            continue
        f = source_file(g)
        if f is None:
            continue
        m = g.meta or {}
        out.append(_Item(os.path.splitext(g.name)[0], f.path, hdr.exif_ev(m.get("exposure"), m.get("fnumber"), m.get("iso")),
                         _desc(m.get("exposure"), m.get("fnumber"), m.get("iso")), g))
    return out


def items_from_paths(paths: list[str]) -> list[_Item]:
    from PIL import Image
    out = []
    for p in paths:
        ev, desc = None, ""
        try:
            with Image.open(p) as im:
                sub = im.getexif().get_ifd(0x8769)
                e, n, i = sub.get(0x829A), sub.get(0x829D), sub.get(0x8827)
                ev, desc = hdr.exif_ev(e, n, i), _desc(e, n, i)
        except Exception:
            pass
        out.append(_Item(os.path.splitext(os.path.basename(p))[0], p, ev, desc))
    return out


def _load(item: _Item, exiftool: str | None):
    """返回 (RGB uint8, exif 字节, icc)。和修图窗口打开照片用同一套读图。"""
    from PIL import Image, ImageOps
    from ...core.formats import file_kind
    if item.group is not None:
        from ...ops.exporter import load_full
        im, exif, icc = load_full(item.group, exiftool)
        ex = None
        try:
            ex = exif.tobytes() if exif is not None else None
        except Exception:
            pass
        return np.array(im.convert("RGB"), dtype=np.uint8), ex, icc
    kind = file_kind(item.path)
    if kind == "raw":
        from ...core.imaging import load_raw_preview
        im, _ = load_raw_preview(item.path, 100000, exiftool)
        return np.array(im.convert("RGB"), dtype=np.uint8), None, None
    if kind == "heif":
        from ...core.imaging import _ensure_heif
        _ensure_heif()
    with Image.open(item.path) as im:
        exif = im.getexif()
        icc = im.info.get("icc_profile")
        im = ImageOps.exif_transpose(im)
        rgb = np.array(im.convert("RGB"), dtype=np.uint8)
    try:
        ex = exif.tobytes()
    except Exception:
        ex = None
    return rgb, ex, icc


# ---------------------------------------------------------------------------
# 选项对话框
# ---------------------------------------------------------------------------

class MergeDialog(_Base):
    def __init__(self, parent, items: list[_Item]):
        super().__init__(parent, _t("合并曝光（HDR）"), "MERGE // HDR")
        self.setMinimumWidth(560)
        self.items = items
        lab = QLabel(_t("把同一场景、不同曝光的 {0} 张照片合成一张：亮部、暗部的细节都保留，曝光正常。", len(items)))
        lab.setWordWrap(True)
        self.body.addWidget(lab)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        known = [it.ev for it in items if it.ev is not None]
        mid = sorted(known)[len(known) // 2] if len(known) == len(items) else None
        for r, it in enumerate(items):
            n = QLabel(it.name)
            n.setFont(font("mono", 9))
            grid.addWidget(n, r, 0)
            d = QLabel(it.desc or _t("（没有曝光信息）"))
            d.setProperty("role", "dim")
            grid.addWidget(d, r, 1)
            if mid is not None:
                e = QLabel(hdr.ev_label(it.ev - mid))
                e.setFont(font("mono", 9))
                grid.addWidget(e, r, 2)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)
        if mid is not None and max(known) - min(known) < 0.3:
            self.body.addWidget(caption(_t("这几张的曝光几乎一样：合并后亮部、暗部不会多出细节。包围曝光一般每张相差 1–2 EV。"), "warn", 8.5))

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.addWidget(QLabel(_t("参考帧：")), 0, 0)
        self.ref = QComboBox()
        self.ref.addItem(_t("自动（曝光居中的一张）"), -1)
        for i, it in enumerate(items):
            self.ref.addItem(it.name, i)
        self.ref.setToolTip(_t("参考帧不重新取样，清晰度原样保留；画面里移动的东西以它为准。"))
        form.addWidget(self.ref, 0, 1)
        form.addWidget(QLabel(_t("去除重影：")), 1, 0)
        self.deghost = QComboBox()
        for k, t in DEGHOST_CHOICES:
            self.deghost.addItem(t, k)
        self.deghost.setToolTip(_t("行人、树叶、水面等在几张之间动过的地方，只用参考帧那一张，避免出现半透明的重影。"))
        form.addWidget(self.deghost, 1, 1)
        form.setColumnStretch(1, 1)
        self.body.addLayout(form)
        for attr, text, note in (("align", _t("自动对齐"), _t("手持拍摄时打开；三脚架拍的也可以开着，没动就不会重新取样。")),
                                 ("keep", _t("保留各张源图为隐藏图层"), _t("之后可以框出残留的重影，挑一张补上。"))):
            cb = QCheckBox(text)
            cb.setChecked(True)
            setattr(self, attr, cb)
            self.body.addWidget(cb)
            c = caption(note, "dim", 8.5)
            c.setContentsMargins(26, 0, 0, 0)
            self.body.addWidget(c)
        self.body.addWidget(caption(_t("原片不会被改动。合并结果在修图窗口里打开，可以接着调色、修图，再导出或保存工程。"), "faint", 8.5))
        bc = TacButton(_t("取消"), "secondary")
        bo = TacButton(_t("开始合并"), "primary")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self.accept)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)
        bo.setFocus()

    def options(self) -> dict:
        r = int(self.ref.currentData())
        return {"ref": None if r < 0 else r, "deghost": self.deghost.currentData(),
                "align": self.align.isChecked(), "keep": self.keep.isChecked()}


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------

def merge_groups(ctx, parent, groups) -> None:
    items = items_from_groups(groups)
    if len(items) < 2:
        info(parent, _t("合并曝光"), _t("先选中同一场景、不同曝光的 2–{0} 张照片（视频除外）。", hdr.MAX_FRAMES))
        return
    run_merge(ctx, parent, items)


def merge_paths(ctx, parent, paths: list[str]) -> None:
    if len(paths) < 2:
        info(parent, _t("合并曝光"), _t("至少选两张照片。"))
        return
    run_merge(ctx, parent, items_from_paths(paths))


def run_merge(ctx, parent, items: list[_Item]) -> None:
    if len(items) > hdr.MAX_FRAMES:
        info(parent, _t("合并曝光"), _t("一次最多合并 {0} 张，现在选了 {1} 张。", hdr.MAX_FRAMES, len(items)))
        return
    if all(it.ev is not None for it in items):
        items = sorted(items, key=lambda it: it.ev)
    dlg = MergeDialog(parent, items)
    if dlg.exec() != MergeDialog.DialogCode.Accepted:
        return
    opt = dlg.options()
    exiftool = ctx.library.exiftool_path if ctx is not None else None
    n = len(items)

    def job(prog, cancel):
        frames, extra = [], []
        for i, it in enumerate(items):
            if cancel.is_set():
                return {"cancelled": True}
            prog((i, n * 4, _t("读取 {0}…", it.name)))
            rgb, ex, icc = _load(it, exiftool)
            frames.append(hdr.Frame(rgb, it.name, it.ev))
            extra.append((ex, icc))

        def mp(d, tot, text):
            prog((n + round(d * 3 * n / max(1, tot)), n * 4, text))
        try:
            res = hdr.merge(frames, opt["ref"], opt["align"], opt["deghost"], mp, cancel)
        except hdr.Cancelled:
            return {"cancelled": True}
        except hdr.MergeError as e:
            return {"error": str(e)}
        except MemoryError:
            return {"error": _t("内存不够：照片太大或张数太多。少选几张，或者关掉别的程序再试。")}
        return {"res": res, "frames": frames, "extra": extra}

    if ctx is None:
        class _Ev:
            def is_set(self):
                return False
        box = job(lambda v: None, _Ev())
        err = None
    else:
        box, err = run_with_progress(ctx, parent, _t("合并曝光"), "MERGING", job,
                                     _t("取消后不会生成任何文件，原片不受影响。"))
    if err:
        info(parent, _t("合并失败"), err.strip().splitlines()[-1], "ERROR")
        return
    if not box or box.get("cancelled"):
        return
    if box.get("error"):
        info(parent, _t("不能合并"), box["error"], "ERROR")
        return
    res, frames, extra = box["res"], box["frames"], box["extra"]
    doc = hdr.to_document(res, frames, opt["keep"])
    ref = items[res.ref]
    ex, icc = extra[res.ref]
    doc.source = SourceInfo(path=ref.path, name=ref.name, exif=ex, icc=icc,
                            gid=ref.group.gid if ref.group is not None else None, suffix="_HDR")
    doc.modified = True
    from .window import open_editor
    w = open_editor(ctx, parent, doc=doc)
    if w is None:
        return
    msg = _t("已合并 {0} 张，参考帧：{1}。", n - len(res.skipped), ref.name)
    if res.ghost_fraction > 0.0005:
        msg += _t("约 {0:.0%} 的画面有移动的物体，已用参考帧代替。", max(0.01, res.ghost_fraction))
    if opt["keep"]:
        msg += _t("还有重影：圈出来 → 图像 → 修补重影。")
    w.status_msg(msg)
    if res.skipped:
        names = _t("、").join(items[i].name for i in res.skipped)
        info(w, _t("有照片没对齐"), _t("{0} 和参考帧对不上（可能不是同一个场景，或者画面变化太大），没有参与合并。", names))


# ---------------------------------------------------------------------------
# 修补重影
# ---------------------------------------------------------------------------

def _qimg(rgb: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(rgb[..., :3])
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class GhostFixDialog(_Base):
    """选区里用哪一张源图：每张都按“补上去之后的样子”给一张预览。"""

    THUMB = 210

    def __init__(self, win, sources: list[Layer], target: Layer, sel: np.ndarray):
        super().__init__(win, _t("修补重影"), "DEGHOST // PATCH")
        self.sources, self.target, self.sel = sources, target, sel
        self.body.addWidget(QLabel(_t("选区里换成哪一张？点一下预览图选中它（预览就是补上之后的样子）。")))
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.tiles: list[QToolButton] = []
        cols = 3 if len(sources) > 4 else max(2, len(sources))
        for k, l in enumerate(sources):
            b = QToolButton()
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(self.THUMB, self.THUMB))
            inside = to8(l.pixels)[..., :3][sel > 127]
            clipped = float(((inside.min(axis=1) > 250) | (inside.max(axis=1) < 8)).mean()) if inside.size else 0.0
            b.setText(l.name + (_t("\n（这里过曝 / 欠曝，没有细节）") if clipped > 0.5 else ""))
            b.setToolTip(l.name)
            self.group.addButton(b, k)
            self.grid.addWidget(b, k // cols, k % cols)
            self.tiles.append(b)
            if l.params.get("ref"):
                b.setChecked(True)
        if self.group.checkedId() < 0 and self.tiles:
            self.tiles[0].setChecked(True)
        self.body.addLayout(self.grid)
        row = QHBoxLayout()
        self.match = QCheckBox(_t("亮度匹配到合并结果"))
        self.match.setChecked(True)
        self.match.setToolTip(_t("按选区周围一圈把这张的明暗调成和合并结果一致，补上去不会突然变暗 / 变亮。"))
        row.addWidget(self.match)
        row.addSpacing(16)
        row.addWidget(QLabel(_t("边缘过渡")))
        self.feather = QSpinBox()
        self.feather.setRange(0, 200)
        self.feather.setValue(12)
        self.feather.setSuffix(" px")
        row.addWidget(self.feather)
        row.addStretch(1)
        self.body.addLayout(row)
        self.body.addWidget(caption(_t("补丁放在新的图层上，带蒙版：不满意可以用画笔在蒙版上修边，或者直接删掉这一层。"), "faint", 8.5))
        self.match.toggled.connect(self._previews)
        self.feather.valueChanged.connect(self._previews)
        bc = TacButton(_t("取消"), "secondary")
        bo = TacButton(_t("补上"), "primary")
        bc.clicked.connect(self.reject)
        bo.clicked.connect(self.accept)
        self.buttons.addWidget(bc)
        self.buttons.addWidget(bo)
        self._previews()

    def _previews(self) -> None:
        from ...edit.document import mask_bbox
        bb = mask_bbox(self.sel > 127)
        if bb is None:
            return
        x, y, w, h = bb
        pad = max(24, round(0.35 * max(w, h)))
        H, W = self.sel.shape
        x0, y0, x1, y1 = max(0, x - pad), max(0, y - pad), min(W, x + w + pad), min(H, y + h + pad)
        tgt = to8(self.target.pixels)
        for b, l in zip(self.tiles, self.sources):
            r = hdr.ghost_patch(to8(l.pixels), tgt, self.sel, float(self.feather.value()), self.match.isChecked())
            view = tgt[y0:y1, x0:x1, :3].astype(np.float32)
            if r is not None:
                px, mk, px0, py0 = r
                ax0, ay0 = max(px0, x0), max(py0, y0)
                ax1, ay1 = min(px0 + px.shape[1], x1), min(py0 + px.shape[0], y1)
                if ax1 > ax0 and ay1 > ay0:
                    a = mk[ay0 - py0:ay1 - py0, ax0 - px0:ax1 - px0, None].astype(np.float32) / 255
                    p = px[ay0 - py0:ay1 - py0, ax0 - px0:ax1 - px0, :3].astype(np.float32)
                    v = view[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0]
                    view[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0] = v * (1 - a) + p * a
            img = _qimg(np.clip(view + 0.5, 0, 255).astype(np.uint8))
            pm = QPixmap.fromImage(img).scaled(self.THUMB, self.THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
            b.setIcon(QIcon(pm))

    def choice(self) -> tuple[Layer, float, bool]:
        return self.sources[max(0, self.group.checkedId())], float(self.feather.value()), self.match.isChecked()


def fix_ghost(win) -> None:
    """修图窗口「图像 → 修补重影…」。"""
    d = win.doc
    sources = hdr.merge_sources(d)
    target = hdr.merge_result_layer(d)
    if target is None or target.pixels is None or not sources:
        info(win, _t("修补重影"), _t("这张图不是「合并曝光」得到的，或者合并时没有保留源图层，没有可以拿来补的源图。"))
        return
    if (target.x, target.y) != (0, 0) or target.pixels.shape[:2] != (d.height, d.width) or \
            any(l.pixels.shape[:2] != (d.height, d.width) or (l.x, l.y) != (0, 0) for l in sources):
        info(win, _t("修补重影"), _t("合并结果或源图层被移动 / 变换过，和画布对不上了，不能再修补。"))
        return
    if d.selection is None or not (d.selection > 127).any():
        win.status_msg(_t("先用套索 / 选框把有重影的地方圈出来（圈大一点），再「图像 → 修补重影」"))
        return
    win._leave_transform()
    dlg = GhostFixDialog(win, sources, target, d.selection)
    if dlg.exec() != GhostFixDialog.DialogCode.Accepted:
        return
    src, feather, match = dlg.choice()
    r = hdr.ghost_patch(to8(src.pixels), to8(target.pixels), d.selection, feather, match)
    if r is None:
        return
    px, mk, x, y = r
    name = src.name.split(" · ")[0] if " · " in src.name else src.name
    l = Layer("pixel", _t("重影修补 · {0}", name), convert(px, d.dtype), x, y, params={"ghost_fix": src.params.get("merge_source")})
    l.mask = mk
    i = d.index_of(target.id) + 1
    while i < len(d.layers) and "ghost_fix" in d.layers[i].params:
        i += 1
    d.add_layer(l, i, _t("修补重影"))
    win.status_msg(_t("已用「{0}」补上；边缘不自然可以用画笔在这一层的蒙版上修，不要了就删掉这一层", src.name))
