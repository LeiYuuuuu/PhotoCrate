"""分析器接口 —— 新功能的主要扩展点之一。

分析器为每个文件组计算"特征"，结果存到 group.feats[分析器 id]，供分区维度、统计、界面使用。

两种阶段：
- stage = "meta"：只用元数据，运行很快，每次扫描/改设置后在主进程重新计算，不缓存。
    def analyze(self, group, ctx) -> dict
- stage = "pixel"：需要像素（预览图），在后台多进程中运行，结果按文件指纹缓存到 SQLite。
    def analyze_pixels(self, px: PixelInput, settings: dict) -> dict
  修改算法后把 version 加 1，旧缓存会自动失效并重新计算。

将来的 AI 功能（场景分类 CLIP、人脸聚类等）就是一个新的 pixel 分析器 + 一个新的分区维度，
放进 plugins/ 目录即可被加载，不需要改动核心代码。参见 plugins_examples/。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING
from ..i18n import t as _t

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np
    from ..core.models import PhotoGroup


class PixelInput:
    """一张照片的预览像素（RGB uint8，长边 ≤1024），附带常用的缩放/灰度缓存。"""

    def __init__(self, rgb: "np.ndarray", path: str, kind: str, source: str = ""):
        self.rgb = rgb
        self.path = path
        self.kind = kind
        self.source = source
        self._cache: dict[Any, Any] = {}

    @property
    def long_side(self) -> int:
        return int(max(self.rgb.shape[:2]))

    def small(self, n: int) -> "np.ndarray":
        """长边缩放到 n 像素（不放大）。"""
        key = ("small", n)
        if key not in self._cache:
            import cv2

            h, w = self.rgb.shape[:2]
            s = n / max(h, w)
            if s >= 1:
                self._cache[key] = self.rgb
            else:
                self._cache[key] = cv2.resize(self.rgb, (max(1, round(w * s)), max(1, round(h * s))),
                                              interpolation=cv2.INTER_AREA)
        return self._cache[key]

    def gray(self, n: int | None = None) -> "np.ndarray":
        key = ("gray", n)
        if key not in self._cache:
            import cv2

            src = self.rgb if n is None else self.small(n)
            self._cache[key] = cv2.cvtColor(src, cv2.COLOR_RGB2GRAY)
        return self._cache[key]


@dataclass
class AnalysisContext:
    """meta 分析器可用的共享资源。"""
    settings: dict[str, Any]
    geo: Any = None                     # GeoService
    date_patterns: Any = None           # 编译后的文件名日期规则
    extras: dict[str, Any] = field(default_factory=dict)


class Analyzer:
    id: str = ""
    label: str = ""
    version: int = 1
    stage: str = "meta"        # "meta" 或 "pixel"
    order: int = 100           # meta 分析器的执行顺序（小的先执行）

    def available(self, settings: dict[str, Any]) -> bool:
        """pixel 分析器：此刻能否运行（例如需要的模型文件已下载）。不可用时扫描会跳过它，也不会因此反复重算。"""
        return True

    def analyze(self, group: "PhotoGroup", ctx: AnalysisContext) -> dict[str, Any] | None:
        raise NotImplementedError

    def analyze_pixels(self, px: PixelInput, settings: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError


ANALYZERS: dict[str, Analyzer] = {}


def register_analyzer(a: Analyzer) -> Analyzer:
    if not a.id:
        raise ValueError(_t("分析器必须有 id"))
    ANALYZERS[a.id] = a
    return a


def meta_analyzers() -> list[Analyzer]:
    return sorted((a for a in ANALYZERS.values() if a.stage == "meta"), key=lambda a: (a.order, a.id))


def pixel_analyzers() -> list[Analyzer]:
    return sorted((a for a in ANALYZERS.values() if a.stage == "pixel"), key=lambda a: (a.order, a.id))
