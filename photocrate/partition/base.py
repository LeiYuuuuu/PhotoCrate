"""分区维度接口 —— 新功能的另一个扩展点。

一个"维度"决定照片该放进哪个文件夹：
    segments(group, opts, ctx) -> ["2026", "2026-10"]   返回一级或多级文件夹名
    tokens() -> {"年": fn, ...}                         提供给"高级模板"使用的占位符

用户在界面里按顺序选择多个维度（最多 4 级），目标路径 = 目标根目录 / 维度1 / 维度2 / ...
界面会根据 options 自动生成选项控件，新维度不需要写任何界面代码。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from ..i18n import t as _t

TokenFn = Callable[[Any, "PartitionContext"], "str | None"]


@dataclass
class Option:
    key: str
    label: str
    kind: str = "choice"           # choice / bool / int / float
    default: Any = None
    choices: list[tuple[Any, str]] = field(default_factory=list)
    minimum: float = 0
    maximum: float = 100
    step: float = 1
    suffix: str = ""
    tip: str = ""


@dataclass
class PartitionContext:
    settings: dict[str, Any]
    extras: dict[str, Any] = field(default_factory=dict)


class Dimension:
    id: str = ""
    label: str = ""                # 中文名
    en: str = ""                   # 英文小标签（界面装饰用）
    description: str = ""
    unknown: str = _t("未知")          # 无法判断时的文件夹名
    order_by: str = "count"        # 预览树中同级文件夹排序：name / count
    options: list[Option] = []

    def defaults(self) -> dict[str, Any]:
        return {o.key: o.default for o in self.options}

    def resolve_opts(self, opts: dict[str, Any] | None) -> dict[str, Any]:
        d = self.defaults()
        if opts:
            d.update({k: v for k, v in opts.items() if k in d})
        return d

    def segments(self, group, opts: dict[str, Any], ctx: PartitionContext) -> list[str]:
        raise NotImplementedError

    def tokens(self) -> dict[str, TokenFn]:
        return {}

    def describe(self, opts: dict[str, Any]) -> str:
        """一句话描述当前选项（用于日志/计划摘要）。"""
        parts = []
        for o in self.options:
            v = opts.get(o.key, o.default)
            if o.kind == "choice":
                label = next((lab for val, lab in o.choices if val == v), str(v))
                parts.append(label)
            elif o.kind == "bool":
                if v:
                    parts.append(o.label)
            else:
                parts.append(f"{o.label}{v}{o.suffix}")
        return self.label + (_t('（{0}）', _t('，').join(parts)) if parts else "")


DIMENSIONS: dict[str, Dimension] = {}


def register_dimension(d: Dimension) -> Dimension:
    if not d.id:
        raise ValueError(_t("维度必须有 id"))
    DIMENSIONS[d.id] = d
    return d


# 模板占位符的英文写法（两种语言都能用：{年} 和 {year} 是同一个）
TOKEN_ALIASES = {
    "year": "年", "month": "月", "day": "日", "ym": "年月", "ymd": "年月日", "date": "日期", "time": "时间",
    "quarter": "季度", "datesource": "日期来源",
    "country": "国家", "province": "省", "city": "市", "district": "区县", "place": "地区",
    "warmth": "冷暖", "color": "主色", "tone": "明暗",
    "brand": "品牌", "camera": "相机", "lens": "镜头", "source": "来源",
    "format": "格式", "type": "类型", "ext": "扩展名", "orientation": "方向", "resolution": "分辨率",
    "focal": "焦段", "iso": "感光度", "sharpness": "清晰度",
    "rating": "星级", "flag": "标记", "label": "色标",
    "trip": "旅行", "tripday": "第几天", "tripdays": "旅行天数",
    "scene": "场景", "scenegroup": "场景大类", "person": "人物", "folder": "原文件夹",
}


def all_tokens() -> dict[str, TokenFn]:
    out: dict[str, TokenFn] = {}
    for d in DIMENSIONS.values():
        out.update(d.tokens())
    for en, zh in TOKEN_ALIASES.items():
        if zh in out and en not in out:
            out[en] = out[zh]
    return out


# ---------------------------------------------------------------------------
# 按“单个文件”的格式分开：维度返回这个占位符，生成计划 / 预览时逐个文件替换成 ARW、JPG、HEIC…
# （其他维度都按“整组照片”决定位置；只有格式需要把一组里的 RAW 和 JPG 分开放）
# ---------------------------------------------------------------------------
FORMAT_TOKEN = "@@FORMAT@@"
RAW_SIDECAR_EXTS = {".xmp", ".pp3", ".dop", ".on1"}
_ROLE_FORMAT = {"jpeg": "JPG", "heif": "HEIC", "png": "PNG"}


def is_raw_side(member, group) -> bool:
    """该文件是否属于 RAW 一侧（RAW 本身，或 RAW 的 .xmp 修图记录）。"""
    import os
    if member.role == "raw":
        return True
    if not group.has_raw or member.role != "sidecar":
        return False
    name = member.name.lower()
    ext = os.path.splitext(name)[1]
    if ext not in RAW_SIDECAR_EXTS:
        return False
    raws = [f.name.lower() for f in group.files if f.role == "raw"]
    if any(name.startswith(r + ".") for r in raws):
        return True                          # IMG_1.CR3.xmp
    others = [f.name.lower() for f in group.image_files if f.role != "raw"]
    if any(name.startswith(o + ".") for o in others):
        return False                         # IMG_1.JPG.xmp
    return True                              # IMG_1.xmp：Lightroom/C1 默认写给 RAW


def _ext_label(path: str) -> str:
    import os
    return os.path.splitext(path)[1].lstrip(".").upper() or _t("其他")


def member_format(member, group) -> str:
    """单个文件应归入的格式文件夹名：RAW 用真实扩展名（ARW、CR3…），其他用 JPG / HEIC / PNG…；
    附属文件（xmp / aae / mov / json）跟随它所属的图片。"""
    if member.role == "raw":
        return _ext_label(member.path)
    if member.role in _ROLE_FORMAT:
        return _ROLE_FORMAT[member.role]
    if member.role == "image":
        return _ext_label(member.path)
    if is_raw_side(member, group):
        raw = next((f for f in group.files if f.role == "raw"), None)
        return _ext_label(raw.path) if raw else "RAW"
    prim = group.primary_file
    return _ROLE_FORMAT.get(prim.role) or _ext_label(prim.path)


def expand_format(seg: str, member, group) -> str:
    return seg.replace(FORMAT_TOKEN, member_format(member, group)) if FORMAT_TOKEN in seg else seg


def group_formats(group) -> list[str]:
    """一组照片涉及的格式文件夹（预览树用）。"""
    out = []
    for f in group.files:
        fmt = member_format(f, group)
        if fmt not in out:
            out.append(fmt)
    return out


def is_unknown_label(name: str) -> bool:
    from ..i18n import is_unknown
    return is_unknown(name)
