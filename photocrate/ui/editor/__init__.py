"""修图窗口（图层、蒙版、选区、调整图层、画笔 / 修复 / 仿制图章、文字、滤镜）。核心算法在 photocrate/edit。"""
from .window import EditorWindow, open_editor

__all__ = ["EditorWindow", "open_editor"]
