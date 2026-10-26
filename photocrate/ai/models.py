"""AI 模型文件：在哪里、是否就绪、从哪里下载。

模型不随程序分发（太大），第一次使用时在「AI 搜图」页一键下载到用户数据目录 models\\：
- clip：中文 CLIP（OFA-Sys Chinese-CLIP ViT-B/16，Xenova 转成 ONNX 并做了 int8 量化，约 190 MB）
        —— 场景识别、文字搜图、以图搜图。来源 Hugging Face，国内网络可改用镜像 hf-mirror.com。
- face：OpenCV Zoo 的 YuNet（人脸检测，230 KB）+ SFace（人脸特征，37 MB）—— 人物分组。来源 GitHub。
- ocr：PaddleOCR PP-OCRv4 移动版的文字检测（4.7 MB）+ 识别（10.9 MB），RapidOCR 转成 ONNX
       —— 识别照片里的文字，「相册」里搜文字就能找到照片。来源 Hugging Face（可用镜像）。
- expr：OpenCV Zoo 的 MobileFaceNet 表情识别（4.8 MB）—— 挑片时连拍组里找出表情不佳的。来源 GitHub。
也可以手动把文件放进 models\\ 文件夹（或程序目录下的 models\\），文件名与下表一致即可。
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

from ..config import find_resource, user_data_dir
from ..i18n import t as _t

HF = "https://huggingface.co"
HF_MIRROR = "https://hf-mirror.com"
GH_LFS = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
GH_RAW = "https://github.com/opencv/opencv_zoo/raw/main/models"
OCR_PATH = "SWHL/RapidOCR/resolve/main/PP-OCRv4"

# 文件名 → (下载地址列表, 大概大小（字节，用于进度与校验）)
FILES: dict[str, tuple[list[str], int]] = {
    "chinese_clip_vitb16_q8.onnx": (
        [f"{HF}/Xenova/chinese-clip-vit-base-patch16/resolve/main/onnx/model_quantized.onnx",
         f"{HF_MIRROR}/Xenova/chinese-clip-vit-base-patch16/resolve/main/onnx/model_quantized.onnx"], 191_000_000),
    "chinese_clip_vocab.txt": (
        [f"{HF}/Xenova/chinese-clip-vit-base-patch16/resolve/main/vocab.txt",
         f"{HF_MIRROR}/Xenova/chinese-clip-vit-base-patch16/resolve/main/vocab.txt"], 110_000),
    "face_detection_yunet_2023mar.onnx": (
        [f"{GH_LFS}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
         f"{GH_RAW}/face_detection_yunet/face_detection_yunet_2023mar.onnx"], 232_589),
    "face_recognition_sface_2021dec.onnx": (
        [f"{GH_LFS}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
         f"{GH_RAW}/face_recognition_sface/face_recognition_sface_2021dec.onnx"], 38_696_353),
    "facial_expression_recognition_mobilefacenet_2022july.onnx": (
        [f"{GH_LFS}/facial_expression_recognition/facial_expression_recognition_mobilefacenet_2022july.onnx",
         f"{GH_RAW}/facial_expression_recognition/facial_expression_recognition_mobilefacenet_2022july.onnx"], 4_791_892),
    "ch_PP-OCRv4_det_infer.onnx": (
        [f"{HF}/{OCR_PATH}/ch_PP-OCRv4_det_infer.onnx", f"{HF_MIRROR}/{OCR_PATH}/ch_PP-OCRv4_det_infer.onnx"], 4_745_517),
    "ch_PP-OCRv4_rec_infer.onnx": (
        [f"{HF}/{OCR_PATH}/ch_PP-OCRv4_rec_infer.onnx", f"{HF_MIRROR}/{OCR_PATH}/ch_PP-OCRv4_rec_infer.onnx"], 10_857_958),
}

# 下载后核对的 SHA-256（取自 Hugging Face / GitHub LFS 记录的原始文件）：镜像站或网络中途被篡改的文件不会被使用
SHA256: dict[str, str] = {
    "chinese_clip_vitb16_q8.onnx": "a2c2849329eafb24100acec5a9421a39f8cb445296444c5c98855b69e5e7a751",
    "chinese_clip_vocab.txt": "45bbac6b341c319adc98a532532882e91a9cefc0329aa57bac9ae761c27b291c",
    "face_detection_yunet_2023mar.onnx": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    "face_recognition_sface_2021dec.onnx": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    "facial_expression_recognition_mobilefacenet_2022july.onnx":
        "4f61307602fc089ce20488a31d4e4614e3c9753a7d6c41578c854858b183e1a9",
    "ch_PP-OCRv4_det_infer.onnx": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
    "ch_PP-OCRv4_rec_infer.onnx": "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
}

MODELS = {
    "clip": {"label": _t("中文 CLIP（场景识别 · 文字搜图）"), "files": ["chinese_clip_vitb16_q8.onnx"],
             "optional": ["chinese_clip_vocab.txt"], "license": _t("Chinese-CLIP：MIT；ONNX 转换：Xenova")},
    "face": {"label": _t("人脸检测 + 识别（YuNet · SFace）"),
             "files": ["face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx"],
             "optional": [], "license": _t("OpenCV Zoo：MIT / Apache 2.0")},
    "expr": {"label": _t("表情识别（MobileFaceNet · 连拍里挑表情好的）"),
             "files": ["facial_expression_recognition_mobilefacenet_2022july.onnx"],
             "optional": [], "license": _t("OpenCV Zoo：Apache 2.0")},
    "ocr": {"label": _t("文字识别（PP-OCRv4 · 照片里的文字可搜索）"),
            "files": ["ch_PP-OCRv4_det_infer.onnx", "ch_PP-OCRv4_rec_infer.onnx"],
            "optional": [], "license": _t("PaddleOCR：Apache 2.0；ONNX 转换：RapidOCR")},
}


def models_dir() -> Path:
    env = os.environ.get("PHOTOCRATE_MODELS")
    d = Path(env) if env else user_data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_path(name: str) -> Path | None:
    """已存在的模型文件（用户数据目录优先，其次程序目录 models\\）。"""
    p = models_dir() / name
    if p.is_file() and p.stat().st_size > 1000:
        return p
    q = find_resource("models", name)
    if q and q.is_file():
        return q
    return None


def vocab_path() -> Path | None:
    return file_path("chinese_clip_vocab.txt") or find_resource("data", "clip_vocab_zh.txt")


def ready(model: str) -> bool:
    return all(file_path(f) is not None for f in MODELS[model]["files"])


def download_size(model: str) -> int:
    return sum(FILES[f][1] for f in MODELS[model]["files"] + MODELS[model]["optional"] if file_path(f) is None)


def onnxruntime_ok() -> bool:
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def status_text(model: str) -> str:
    if model in ("clip", "ocr") and not onnxruntime_ok():
        return _t("缺少 onnxruntime：请重新运行 run.bat 自动安装（或 pip install onnxruntime）")
    if ready(model):
        return _t("已就绪")
    return _t('未下载（约 {0:.0f} MB）', download_size(model) / 1e6)


ProgressFn = Callable[[int, int, str], None]


def _urls(name: str, mirror: str) -> list[str]:
    urls = list(FILES[name][0])
    if mirror == "mirror":
        urls.sort(key=lambda u: 0 if HF_MIRROR in u else 1)
    elif mirror == "hf":
        urls = [u for u in urls if HF_MIRROR not in u] or urls
    return urls


def _fetch(url: str, dst: Path, done_before: int, total: int, label: str, progress: ProgressFn | None,
           cancel: threading.Event | None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "PhotoCrate/1.4"})
    tmp = dst.with_suffix(dst.suffix + ".part")
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            size = int(r.headers.get("Content-Length") or 0)
            got = 0
            last = 0.0
            while True:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError(_t("已取消"))
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                now = time.time()
                if progress and now - last > 0.2:
                    last = now
                    progress(done_before + got, max(total, done_before + size), f"{label}  {got / 1e6:.1f} MB")
        if size and os.path.getsize(tmp) != size:
            raise IOError(_t('下载不完整（{0} / {1} 字节）', os.path.getsize(tmp), size))
        with open(tmp, "rb") as f:
            head = f.read(64)
        if head.lstrip().startswith((b"<", b"version https://git-lfs")):
            raise IOError(_t("下载到的是网页或 LFS 指针，不是模型文件"))
        want = SHA256.get(dst.name)
        if want and h.hexdigest() != want:
            raise IOError(_t("下载的文件校验值不对（可能被镜像站或网络篡改），没有使用"))
        os.replace(tmp, dst)
    except BaseException:
        try:                                    # 取消 / 失败时不留下几百 MB 的半截文件
            tmp.unlink()
        except OSError:
            pass
        raise


def download(model: str, progress: ProgressFn | None = None, cancel: threading.Event | None = None,
             mirror: str = "auto") -> list[str]:
    """下载某个模型缺少的文件。返回下载了的文件名；全部来源都失败时抛出异常（说明每个来源的错误）。"""
    names = [f for f in MODELS[model]["files"] + MODELS[model]["optional"] if file_path(f) is None]
    total = sum(FILES[n][1] for n in names)
    done = 0
    got = []
    for name in names:
        dst = models_dir() / name
        errors = []
        for url in _urls(name, mirror):
            try:
                _fetch(url, dst, done, total, name, progress, cancel)
                errors = []
                break
            except InterruptedError:
                raise
            except Exception as e:
                errors.append(_t('{0}：{1}', url.split('/')[2], e))
        if errors:
            if name in MODELS[model]["optional"]:
                continue
            raise IOError(_t('{0} 下载失败：\n', name) + "\n".join(errors))
        done += FILES[name][1]
        got.append(name)
    if progress:
        progress(total, total, _t("完成"))
    return got
