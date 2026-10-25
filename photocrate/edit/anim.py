"""时间轴（帧动画）与视频帧导入。

每一帧 = 这一帧显示哪些图层（其余隐藏）+ 停留多久。导出 GIF（Pillow）或 MP4（OpenCV）。
「导入视频帧到图层」：每隔几帧取一帧，作为图层放进来，同时建好对应的动画帧（相当于 Photoshop 的
“视频帧到图层”，之后可以在每一帧上修图再导出）。
"""
from __future__ import annotations

import os

import numpy as np

from ..i18n import t as _t


def frames_from_layers(doc, delay: int = 100) -> None:
    """每个（顶层的）像素图层一帧：最底下的图层当背景一直显示。"""
    content = [l for l in doc.layers if l.parent is None and l.kind != "adjust"]
    if not content:
        return
    bg = content[0]
    adj = [l.id for l in doc.layers if l.kind == "adjust" and l.visible]
    frames = [{"show": [bg.id] + ([l.id] if l is not bg else []) + adj, "delay": int(delay)} for l in content]
    with doc.edit(_t("从图层建立帧"), kind="guides"):
        doc.frames = frames


def add_current(doc, delay: int = 100) -> None:
    show = [l.id for l in doc.layers if l.visible]
    with doc.edit(_t("新建帧"), kind="guides"):
        doc.frames = doc.frames + [{"show": show, "delay": int(delay)}]


def render_frame(doc, frame: dict, dtype=np.uint8, max_side: int = 0) -> np.ndarray:
    """按这一帧的显示设置合成（不改文档里图层的显示状态）。"""
    show = set(frame.get("show") or [])
    views = [doc._view(l, visible=l.id in show or l.kind == "group") for l in doc.layers]
    if max_side and max(doc.width, doc.height) > max_side:
        import math
        lvl = max(0, int(math.floor(math.log2(max(doc.width, doc.height) / max_side))))
        rgb, a = doc.render(lvl, layers=views)
        from .document import to_uint8
        return to_uint8(rgb, a)
    return doc.compose_rect(views, 0, 0, doc.width, doc.height, dtype)


def export(doc, path: str, loop: int = 0, max_side: int = 0) -> int:
    """GIF / MP4（看扩展名）。返回帧数。"""
    from PIL import Image
    frames = doc.frames or [{"show": [l.id for l in doc.layers if l.visible], "delay": 100}]
    imgs = [render_frame(doc, f, np.uint8, max_side) for f in frames]
    ext = os.path.splitext(path)[1].lower()
    tmp = path + ".part" + ext
    if ext == ".mp4":
        import cv2
        h, w = imgs[0].shape[:2]
        w2, h2 = w - w % 2, h - h % 2
        fps = 1000 / max(10, float(np.median([f["delay"] for f in frames])))
        vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w2, h2))
        for im, f in zip(imgs, frames):
            a = im[..., 3:4].astype(np.float32) / 255
            rgb = (im[..., :3].astype(np.float32) * a + 255 * (1 - a)).astype(np.uint8)[:h2, :w2]
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            for _ in range(max(1, int(round(f["delay"] / (1000 / fps))))):
                vw.write(bgr)
        vw.release()
    else:
        pil = []
        for im in imgs:
            p = Image.fromarray(im, "RGBA")
            bg = Image.new("RGBA", p.size, (255, 255, 255, 255))
            pil.append(Image.alpha_composite(bg, p).convert("RGB").quantize(256, dither=Image.Dither.FLOYDSTEINBERG))
        pil[0].save(tmp, "GIF", save_all=True, append_images=pil[1:], duration=[int(f["delay"]) for f in frames],
                    loop=int(loop), disposal=1)
    os.replace(tmp, path)
    return len(imgs)


def import_video(doc, path: str, every: int = 5, max_frames: int = 60, delay: int | None = None) -> int:
    """把视频的帧作为图层放进来（缩放到画布大小），并建好动画帧。返回导入了几帧。"""
    import cv2
    from .document import Layer, _resize, convert
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(_t("打不开这个视频（格式不支持或文件损坏）"))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    layers, i = [], 0
    while len(layers) < max_frames:
        ok, fr = cap.read()
        if not ok:
            break
        if i % max(1, every) == 0:
            rgb = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
            rgba = np.dstack([rgb, np.full(rgb.shape[:2], 255, np.uint8)])
            if rgba.shape[:2] != (doc.height, doc.width):
                rgba = _resize(rgba, doc.width, doc.height)
            layers.append(Layer("pixel", _t('帧 {0}', len(layers) + 1), np.ascontiguousarray(convert(rgba, doc.dtype))))
        i += 1
    cap.release()
    if not layers:
        return 0
    d = int(delay or round(1000 / fps * max(1, every)))
    with doc.edit(_t("导入视频帧到图层")):
        for k, l in enumerate(layers):
            l.visible = k == 0
            doc.layers.append(doc._register(l))
        doc.frames = [{"show": [l.id], "delay": d} for l in layers]
        doc.active_id = layers[0].id
    return len(layers)
