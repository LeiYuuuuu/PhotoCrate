"""人脸检测与特征（OpenCV YuNet + SFace），作为“像素分析器”在扫描时的后台进程里运行，结果按文件指纹缓存。

每张照片输出：{"n": 人脸数, "w", "h": 预览尺寸, "faces": [{"box": [x, y, w, h]（相对预览的比例）, "score",
"emb": 128 维特征（float16，base64）, "crop": 112×112 对齐后的人脸小图（JPEG，base64）}]}
模型文件不在时这个分析器自动停用（扫描不会因此反复重算）。
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from ..analyzers.base import Analyzer, PixelInput
from . import models

log = logging.getLogger(__name__)

DET = "face_detection_yunet_2023mar.onnx"
REC = "face_recognition_sface_2021dec.onnx"

_engine = None
_engine_failed = False


def encode_emb(v) -> str:
    import numpy as np
    return base64.b64encode(np.asarray(v, dtype=np.float16).tobytes()).decode("ascii")


def decode_emb(s: str):
    import numpy as np
    v = np.frombuffer(base64.b64decode(s), dtype=np.float16).astype(np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


class FaceEngine:
    def __init__(self, det_path: str, rec_path: str, score: float = 0.8):
        import cv2
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(det_path, "", (320, 320), score, 0.3, 5000)
        self.rec = cv2.FaceRecognizerSF.create(rec_path, "")

    def detect(self, rgb, min_size: int = 40, max_faces: int = 30) -> list[dict[str, Any]]:
        import numpy as np
        cv2 = self.cv2
        img = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR)
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(img)
        out = []
        if faces is None:
            return out
        faces = sorted(faces, key=lambda f: -(f[2] * f[3]))[:max_faces]
        for f in faces:
            if min(f[2], f[3]) < min_size:
                continue
            aligned = self.rec.alignCrop(img, f)
            feat = self.rec.feature(aligned).ravel()
            feat = feat / max(1e-8, float(np.linalg.norm(feat)))
            ok, jpg = cv2.imencode(".jpg", aligned, [cv2.IMWRITE_JPEG_QUALITY, 82])
            out.append({
                "box": [round(float(f[0]) / w, 4), round(float(f[1]) / h, 4),
                        round(float(f[2]) / w, 4), round(float(f[3]) / h, 4)],
                "score": round(float(f[14]), 3),
                "emb": encode_emb(feat),
                "crop": base64.b64encode(jpg.tobytes()).decode("ascii") if ok else "",
            })
        return out


def engine() -> FaceEngine | None:
    global _engine, _engine_failed
    if _engine is None and not _engine_failed:
        d, r = models.file_path(DET), models.file_path(REC)
        if d is None or r is None:
            return None
        try:
            _engine = FaceEngine(str(d), str(r))
        except Exception as e:
            log.warning("人脸模型加载失败：%s", e)
            _engine_failed = True
    return _engine


class FaceAnalyzer(Analyzer):
    id = "faces"
    label = "人脸"
    stage = "pixel"
    version = 1
    order = 60

    def available(self, settings: dict[str, Any]) -> bool:
        return bool(settings.get("ai_faces", True)) and models.ready("face")

    def analyze_pixels(self, px: PixelInput, settings: dict[str, Any]) -> dict[str, Any] | None:
        eng = engine()
        if eng is None:
            return None
        faces = eng.detect(px.rgb, int(settings.get("face_min_size", 40)))
        h, w = px.rgb.shape[:2]
        return {"n": len(faces), "w": int(w), "h": int(h), "faces": faces}
