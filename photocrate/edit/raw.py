"""RAW 显影（Camera Raw）：用 LibRaw（rawpy）从传感器数据重新解马赛克，输出 16 位 sRGB。

和只读相机内嵌的 JPEG 预览相比：高光 / 暗部的宽容度完整保留（16 位、可以按“重建”找回过曝的高光），
白平衡可以事后改（拍摄时设置 / 自动 / 日光 / 吸管点一下中性灰），曝光可以 ±EV 调。
之后的调色仍然用修图窗口里的调整图层，16 位下拉曲线不会出现断层。
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np

from ..i18n import t as _t

WB_MODES = [("camera", _t("拍摄时设置")), ("auto", _t("自动")), ("daylight", _t("日光")), ("custom", _t("自定义（吸管）"))]
HIGHLIGHTS = [("clip", _t("剪切（最干净）")), ("blend", _t("混合（找回一点高光）")), ("rebuild", _t("重建（尽量找回过曝的高光）"))]
DEMOSAIC = [("AHD", _t("AHD（默认，细节和伪色平衡）")), ("DCB", _t("DCB（边缘更锐利）")), ("DHT", _t("DHT（细节最多）")),
            ("AAHD", _t("AAHD（伪色最少）")), ("LINEAR", _t("线性（最快）"))]
DENOISE = [("off", _t("关闭")), ("light", _t("轻度")), ("full", _t("强"))]


@dataclass
class RawOptions:
    wb: str = "camera"
    wb_mul: list[float] | None = None        # 自定义白平衡：R G B G2 四个系数
    exposure: float = 0.0                     # EV（-2 … +3）
    auto_bright: bool = True                  # LibRaw 自动亮度（只让 0.1% 的像素溢出）
    highlight: str = "blend"
    demosaic: str = "AHD"
    denoise: str = "off"                      # 解马赛克之前的 FBDD 降噪
    ca_red: float = 1.0                       # 色差校正：红 / 蓝通道缩放（1 = 不校正）
    ca_blue: float = 1.0
    half: bool = False                        # 预览用：半尺寸、快很多
    extra: dict = field(default_factory=dict)


def is_raw(path: str) -> bool:
    from ..core.formats import file_kind
    return file_kind(path) == "raw"


def camera_wb(path: str) -> dict:
    """相机记录的白平衡系数、日光系数和基本信息。"""
    import rawpy
    with rawpy.imread(path) as raw:
        cam = [float(v) for v in raw.camera_whitebalance]
        day = [float(v) for v in raw.daylight_whitebalance]
        s = raw.sizes
        return {"camera": cam, "daylight": day, "width": int(s.width), "height": int(s.height),
                "colors": str(raw.color_desc.decode(errors="replace") if isinstance(raw.color_desc, bytes)
                              else raw.color_desc)}


def _params(opts: RawOptions, info: dict | None = None) -> dict:
    import rawpy
    hl = {"clip": rawpy.HighlightMode.Clip, "blend": rawpy.HighlightMode.Blend,
          "rebuild": rawpy.HighlightMode.ReconstructDefault}[opts.highlight if opts.highlight in
                                                              ("clip", "blend", "rebuild") else "blend"]
    algo = getattr(rawpy.DemosaicAlgorithm, opts.demosaic, rawpy.DemosaicAlgorithm.AHD)
    if not algo.isSupported:
        algo = rawpy.DemosaicAlgorithm.AHD
    nr = {"off": rawpy.FBDDNoiseReductionMode.Off, "light": rawpy.FBDDNoiseReductionMode.Light,
          "full": rawpy.FBDDNoiseReductionMode.Full}.get(opts.denoise, rawpy.FBDDNoiseReductionMode.Off)
    kw = dict(output_bps=16, output_color=rawpy.ColorSpace.sRGB, gamma=(2.222, 4.5), half_size=bool(opts.half),
              demosaic_algorithm=algo, highlight_mode=hl, fbdd_noise_reduction=nr,
              no_auto_bright=not opts.auto_bright, auto_bright_thr=0.001)
    if opts.wb == "camera":
        kw["use_camera_wb"] = True
    elif opts.wb == "auto":
        kw["use_auto_wb"] = True
    elif opts.wb == "custom" and opts.wb_mul:
        kw["user_wb"] = [float(v) for v in opts.wb_mul]
    elif opts.wb == "daylight" and info and any(info.get("daylight") or []):
        kw["user_wb"] = list(info["daylight"])
    ev = float(np.clip(opts.exposure, -2, 3))
    if abs(ev) > 1e-3:
        kw["exp_shift"] = float(2 ** ev)
        kw["exp_preserve_highlights"] = 0.85 if ev > 0 else 0.0
    if abs(opts.ca_red - 1) > 1e-4 or abs(opts.ca_blue - 1) > 1e-4:
        kw["chromatic_aberration"] = (float(opts.ca_red), float(opts.ca_blue))
    return kw


def develop(path: str, opts: RawOptions | None = None, max_side: int | None = None) -> np.ndarray:
    """显影成 uint16 RGB（已按相机记录的方向转正）。max_side：缩小到长边不超过它（预览用）。"""
    import cv2
    import rawpy
    opts = opts or RawOptions()
    info = camera_wb(path) if opts.wb == "daylight" else None
    with rawpy.imread(path) as raw:
        rgb = raw.postprocess(**_params(opts, info))
    rgb = np.ascontiguousarray(rgb)
    if max_side and max(rgb.shape[:2]) > max_side:
        s = max_side / max(rgb.shape[:2])
        rgb = cv2.resize(rgb, (max(1, int(rgb.shape[1] * s)), max(1, int(rgb.shape[0] * s))),
                         interpolation=cv2.INTER_AREA)
    return rgb


def pick_white(preview: np.ndarray, x: int, y: int, base_mul: list[float], r: int = 3) -> list[float]:
    """吸管白平衡：preview（按 base_mul 显影的 16 位图）上 (x, y) 附近应该是中性灰，算出新的四个系数。"""
    h, w = preview.shape[:2]
    x0, y0, x1, y1 = max(0, x - r), max(0, y - r), min(w, x + r + 1), min(h, y + r + 1)
    v = np.clip(preview[y0:y1, x0:x1].reshape(-1, 3).astype(np.float64).mean(axis=0) / 65535.0, 1e-5, 1)
    lin = np.where(v < 0.081, v / 4.5, ((v + 0.099) / 1.099) ** (1 / 0.45))   # 去掉 BT.709 伽马
    rr, gg, bb = lin
    m = list(base_mul[:4]) + [0.0] * (4 - len(base_mul[:4]))
    if m[3] <= 0:
        m[3] = m[1]
    return [m[0] * gg / rr, m[1], m[2] * gg / bb, m[3]]


def embedded_exif(path: str) -> bytes | None:
    """从 RAW 里内嵌的 JPEG 预览取 EXIF（拍摄时间、相机、镜头…），导出时带上。"""
    try:
        import rawpy
        from PIL import Image
        with rawpy.imread(path) as raw:
            th = raw.extract_thumb()
        if th.format != rawpy.ThumbFormat.JPEG:
            return None
        with Image.open(io.BytesIO(th.data)) as im:
            ex = im.getexif()
            return ex.tobytes() if len(ex) else None
    except Exception:
        return None
