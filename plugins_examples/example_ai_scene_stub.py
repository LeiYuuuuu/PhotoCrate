"""示例插件骨架：本地 AI 场景分类（风景 / 人像 / 美食 / 宠物 / 文档 / 夜景 …）。

这是为将来接入 CLIP 类模型（推荐 Chinese-CLIP 导出的 ONNX）预留的完整接口示范：
  1. SceneAnalyzer（stage="pixel"）：在后台进程里拿到预览像素，算出图片向量与场景标签，结果自动缓存
  2. SceneDimension：把“场景”变成一个分区条件，同时提供模板占位符 {场景}

当前未接入模型时，分析器会用一个“极简启发式”占位（根据亮度/色彩给出 夜景 / 文档 / 其他），
保证插件可以直接运行、看到效果；接入真正模型时只需实现 _ClipModel 的两个方法。

接入步骤（源码运行模式）：
  1) .venv\\Scripts\\pip install onnxruntime
  2) 把 image_encoder.onnx 与 labels.npy（各场景文字向量，提前用文本编码器算好）放到
     %APPDATA%\\PhotoCrate\\models\\scene\\
  3) 实现下面 _ClipModel.encode_image()，把版本号 version 加 1（旧缓存会自动失效）
注意：打包好的 exe 里没有 onnxruntime，AI 插件需要用 run.bat 源码模式运行，或安装 onnxruntime 后重新打包。
"""
from __future__ import annotations

import os
from pathlib import Path

from photocrate.analyzers.base import Analyzer
from photocrate.partition.base import Dimension, Option

PLUGIN_INFO = {
    "name": "AI 场景分类（骨架）",
    "version": "0.1",
    "description": "未接入模型时使用启发式占位；接入 CLIP ONNX 后即可按场景分区",
}

SCENES = ["风景", "人像", "美食", "宠物", "建筑", "文档截图", "夜景", "其他"]
MODEL_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PhotoCrate" / "models" / "scene"


class _ClipModel:
    """真正的模型封装（示意）。每个后台进程各加载一次。"""
    _inst = None

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def __init__(self):
        self.ok = False
        try:
            import numpy as np
            import onnxruntime as ort  # noqa: F401
            enc = MODEL_DIR / "image_encoder.onnx"
            lab = MODEL_DIR / "labels.npy"
            if enc.exists() and lab.exists():
                self.sess = ort.InferenceSession(str(enc), providers=["CPUExecutionProvider"])
                self.text = np.load(lab)          # (场景数, 维度)，已归一化
                self.ok = True
        except Exception:
            self.ok = False

    def encode_image(self, rgb):
        """TODO：按模型要求缩放到 224×224、归一化，运行 self.sess，返回 L2 归一化后的向量。"""
        raise NotImplementedError


class SceneAnalyzer(Analyzer):
    id = "scene"
    label = "场景"
    stage = "pixel"
    version = 1

    def analyze_pixels(self, px, settings):
        import numpy as np

        model = _ClipModel.get()
        if model.ok:
            try:
                v = model.encode_image(px.small(224))
                sims = model.text @ v
                i = int(np.argmax(sims))
                return {"label": SCENES[i], "score": float(sims[i]), "by": "clip"}
            except NotImplementedError:
                pass
        # ---- 启发式占位 ----
        small = px.small(64).astype("float32") / 255
        lum = float(small.mean())
        sat = float((small.max(axis=2) - small.min(axis=2)).mean())
        if lum < 0.18:
            label = "夜景"
        elif sat < 0.06 and lum > 0.75:
            label = "文档截图"
        else:
            label = "其他"
        return {"label": label, "score": 0.0, "by": "heuristic"}


class SceneDimension(Dimension):
    id = "scene"
    label = "场景（AI）"
    en = "SCENE"
    description = "按画面内容分组。未接入模型时只能识别 夜景 / 文档截图。"
    unknown = "未识别场景"
    options = [Option("min_score", "最低置信度", kind="float", default=0.0, minimum=0, maximum=1, step=0.05)]

    def segments(self, g, opts, ctx):
        s = g.feats.get("scene") or {}
        if not s.get("label") or s.get("score", 0) < float(opts.get("min_score", 0)):
            return [self.unknown]
        return [s["label"]]

    def tokens(self):
        return {"场景": lambda g, ctx: (g.feats.get("scene") or {}).get("label")}


def register(api):
    api.register_analyzer(SceneAnalyzer())
    api.register_dimension(SceneDimension())
