"""动作（录制 / 播放）与批处理。

动作 = 一串步骤 {"op": 名称, …参数}，和界面无关，可以在没有窗口的情况下对任何文档播放（批处理就是这样做的）。
能录的步骤：滤镜、新建调整图层（含最终的参数）、套用预设、图像大小（按比例或长边）、旋转 / 翻转、
转 8 / 16 位、拼合、盖印、自动色调 / 去色 / 反相。尺寸相关的步骤记成比例，换一张大小不同的照片也能用。
"""
from __future__ import annotations

import os

from ..i18n import t as _t

OP_NAMES = {"filter": _t("滤镜"), "adjust": _t("新建调整图层"), "resize": _t("图像大小"), "rotate": _t("图像旋转"),
            "flip": _t("翻转"), "depth": _t("位深"), "flatten": _t("拼合图像"), "stamp": _t("盖印可见图层"),
            "long_side": _t("缩放到长边")}


def describe(step: dict) -> str:
    op = step.get("op")
    name = OP_NAMES.get(op, op)
    if op == "filter":
        from .filters import FILTERS
        f = FILTERS.get(step.get("fid"))
        return f"{name}：{f.name if f else step.get('fid')}"
    if op == "adjust":
        return f"{name}：{step.get('name', '')}"
    if op == "resize":
        return f"{name}：{step.get('sx', 1) * 100:.0f}% × {step.get('sy', 1) * 100:.0f}%"
    if op == "long_side":
        return f"{name}：{step.get('px')} px"
    if op == "rotate":
        return f"{name}：{ {1: '90°', 2: '180°', 3: '-90°'}.get(step.get('k'), '') }"
    if op == "flip":
        return f"{name}：{_t('水平') if step.get('horizontal', True) else _t('垂直')}"
    if op == "depth":
        return f"{name}：{step.get('depth')}"
    return name


def play(doc, steps: list[dict]) -> int:
    """在文档上播放动作，返回执行了几步。作用在当前图层上的步骤（滤镜）找不到像素图层时，先拼合。"""
    n = 0
    for st in steps:
        op = st.get("op")
        if op == "filter":
            l = doc.active
            if l is None or not l.has_pixels:
                l = next((x for x in reversed(doc.layers) if x.has_pixels), None)
                if l is None:
                    continue
                doc.set_active(l.id)
            doc.apply_filter(l.id, st["fid"], dict(st.get("params") or {}))
        elif op == "adjust":
            doc.new_adjust_layer(dict(st.get("params") or {}), st.get("name") or _t("调整"))
        elif op == "resize":
            doc.resize_image(max(1, round(doc.width * float(st.get("sx", 1)))),
                             max(1, round(doc.height * float(st.get("sy", 1)))))
        elif op == "long_side":
            px = int(st.get("px", 2048))
            k = px / max(doc.width, doc.height)
            if k < 1 or st.get("enlarge"):
                doc.resize_image(max(1, round(doc.width * k)), max(1, round(doc.height * k)))
        elif op == "rotate":
            doc.rotate(int(st.get("k", 1)))
        elif op == "flip":
            doc.flip(bool(st.get("horizontal", True)))
        elif op == "depth":
            doc.convert_depth(int(st.get("depth", 8)))
        elif op == "flatten":
            doc.flatten()
        elif op == "stamp":
            doc.stamp_visible()
        else:
            continue
        n += 1
    return n


def batch(paths: list[str], steps: list[dict], out_dir: str, ext: str = ".jpg", quality: int = 90,
          suffix: str = "", deep: bool | None = None, progress=None, cancel=None) -> list[tuple[str, str]]:
    """对每个文件：打开（RAW 按默认设置显影）→ 播放动作 → 导出到 out_dir。返回 [(源文件, 结果或错误)]。"""
    from . import io as eio
    os.makedirs(out_dir, exist_ok=True)
    res = []
    for i, p in enumerate(paths):
        if cancel is not None and cancel():
            break
        if progress is not None:
            progress(i, len(paths), os.path.basename(p))
        try:
            doc = eio.open_file(p)
            play(doc, steps)
            stem = os.path.splitext(os.path.basename(p))[0]
            out = os.path.join(out_dir, stem + suffix + ext)
            k = 1
            while os.path.exists(out) or os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(p)):
                out = os.path.join(out_dir, f"{stem}{suffix} ({k}){ext}")
                k += 1
            eio.export_image(doc, out, quality, True, False, deep=deep)
            res.append((p, out))
        except Exception as e:                                      # 一张失败不影响其他的
            res.append((p, f"ERROR: {e}"))
    if progress is not None:
        progress(len(paths), len(paths), "")
    return res
