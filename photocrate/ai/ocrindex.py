"""照片里的文字索引：每张照片识别一次，结果按主图指纹缓存在 cache.db 的 ocr 表，移动 / 改名后依然命中。

识别结果放在 g.feats["ocr"]（[{"t", "s", "b"}]，见 ai/ocr.py），没有文字的照片是空列表（同样缓存，不会重复识别）。
用 1600 像素长边的预览识别：检测在 960 上做，识别从 1600 的图上裁，小字也认得清；视频不识别。
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import models
from ..i18n import t as _t

log = logging.getLogger(__name__)

OCR_KEY = "ppocrv4-m1"
PREVIEW_SIDE = 1600
DET_FILES = ("ch_PP-OCRv4_det_infer.onnx", "ch_PP-OCRv4_rec_infer.onnx")


class OcrIndex:
    def __init__(self, library):
        self.lib = library
        self._engine = None
        self._lock = threading.RLock()

    def ready(self) -> bool:
        return models.ready("ocr") and models.onnxruntime_ok()

    def engine(self):
        with self._lock:
            if self._engine is None:
                from .ocr import OcrEngine
                det, rec = (models.file_path(f) for f in DET_FILES)
                if det is None or rec is None:
                    raise RuntimeError(_t("文字识别模型尚未下载"))
                self._engine = OcrEngine(det, rec)
            return self._engine

    def unload(self) -> None:
        with self._lock:
            self._engine = None

    @staticmethod
    def eligible(g) -> bool:
        return bool(g.files) and not g.is_video

    def load_cached(self, groups) -> int:
        sigs = {g.gid: g.primary_file.sig for g in groups if self.eligible(g)}
        cached = self.lib.cache.get_ocr_many(list(sigs.values()), OCR_KEY)
        n = 0
        for g in groups:
            s = sigs.get(g.gid)
            if s is not None and s in cached:
                g.feats["ocr"] = cached[s]
                n += 1
            else:
                g.feats.pop("ocr", None)
        return n

    def pending(self, groups) -> list:
        return [g for g in groups if self.eligible(g) and "ocr" not in g.feats]

    def _bgr(self, g):
        import numpy as np
        from ..core.imaging import load_preview
        im = load_preview(g.primary, g.primary_file.role, PREVIEW_SIDE, self.lib.exiftool_path)[0]
        return np.ascontiguousarray(np.asarray(im.convert("RGB"))[..., ::-1])

    def read_group(self, g) -> list[dict]:
        return self.engine().read(self._bgr(g))

    def index(self, groups, progress: Callable[[int, int], None] | None = None,
              cancel: threading.Event | None = None) -> int:
        """识别还没有结果的照片。返回新识别的数量（解码预览在后台线程里预取，识别本身串行）。"""
        self.load_cached(groups)
        todo = self.pending(groups)
        if not todo:
            return 0
        eng = self.engine()
        done = 0
        buf: dict[str, list] = {}

        def load(g):
            try:
                return g, self._bgr(g), None
            except Exception as e:            # 坏图 / 读不了：记成没有文字，下次不再试
                return g, None, e

        with ThreadPoolExecutor(max_workers=2) as pool:
            ahead = 4                                # 最多预取几张，取消时不用等全部解码完
            futs = [pool.submit(load, g) for g in todo[:ahead]]
            for i in range(len(todo)):
                g, bgr, err = futs[i].result()
                futs[i] = None
                if i + ahead < len(todo):
                    futs.append(pool.submit(load, todo[i + ahead]))
                if cancel is not None and cancel.is_set():
                    break
                lines: list = []
                if err is not None:
                    log.debug("文字识别：读取 %s 失败：%s", g.primary, err)
                else:
                    try:
                        lines = eng.read(bgr)
                    except Exception as e:
                        log.warning("文字识别失败 %s：%s", g.primary, e)
                g.feats["ocr"] = lines
                buf[g.primary_file.sig] = lines
                done += 1
                if len(buf) >= 24:
                    self.lib.cache.put_ocr_many(buf, OCR_KEY)
                    buf = {}
                if progress:
                    progress(done, len(todo))
        if buf:
            self.lib.cache.put_ocr_many(buf, OCR_KEY)
        return done

    def stats(self, groups) -> dict[str, int]:
        el = [g for g in groups if self.eligible(g)]
        done = [g for g in el if "ocr" in g.feats]
        return {"eligible": len(el), "done": len(done), "with_text": sum(1 for g in done if g.feats["ocr"])}
