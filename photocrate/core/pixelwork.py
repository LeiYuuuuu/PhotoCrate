"""像素阶段的工作函数（在后台进程/线程中运行）。

每个任务：解码一次预览 → 保存缩略图 → 依次运行需要的 pixel 分析器。
必须是模块级函数，才能被多进程（Windows 为 spawn 模式）序列化调用。
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

THUMB_SIZE = 360
PREVIEW_SIZE = 1024

_settings: dict[str, Any] = {}
_initialized = False


def init_worker(plugin_dirs: list[str], settings: dict[str, Any], in_process: bool = True) -> None:
    global _settings, _initialized
    _settings = settings
    if _initialized:
        return
    from ..analyzers.builtin import register_builtin
    from ..plugins import load_plugins

    register_builtin()
    load_plugins(plugin_dirs, headless=True)
    if in_process:
        try:
            import cv2
            cv2.setNumThreads(1)  # 每个进程单线程，避免与进程池叠加造成过度争抢
        except Exception:
            pass
    _initialized = True


def thumb_path_for(thumb_root: str, sig: str) -> str:
    return os.path.join(thumb_root, sig[:2], sig + ".jpg")


def process_task(task: dict[str, Any]) -> dict[str, Any]:
    from ..analyzers.base import PixelInput, pixel_analyzers
    from .imaging import load_preview

    sig = task["sig"]
    out: dict[str, Any] = {"sig": sig, "gid": task.get("gid"), "feats": {}}
    try:
        import numpy as np

        im, src = load_preview(task["path"], task["kind"], PREVIEW_SIZE, task.get("exiftool"))
        out["preview"] = {"w": im.width, "h": im.height, "src": src}
        if task.get("need_thumb"):
            tp = task["thumb_path"]
            os.makedirs(os.path.dirname(tp), exist_ok=True)
            th = im.copy()
            th.thumbnail((THUMB_SIZE, THUMB_SIZE))
            tmp = tp + ".tmp"
            th.save(tmp, "JPEG", quality=84)
            os.replace(tmp, tp)
            out["thumb"] = tp
        need = set(task.get("need") or [])
        if need:
            px = PixelInput(np.asarray(im), task["path"], task["kind"], src)
            for a in pixel_analyzers():
                if a.id not in need:
                    continue
                try:
                    r = a.analyze_pixels(px, _settings)
                    if r is not None:
                        out["feats"][a.id] = {"v": a.version, "data": r}
                except Exception as e:
                    log.warning("分析器 %s 处理 %s 失败：%s", a.id, task["path"], e)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out
