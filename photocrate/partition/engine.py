"""分区引擎：条件链 / 模板 → 每个文件组的目标相对文件夹，以及预览树。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..ops.pathsafe import sanitize_component
from .base import (DIMENSIONS, FORMAT_TOKEN, PartitionContext, all_tokens, group_formats, is_unknown_label,
                   member_format)


@dataclass
class Rule:
    dim: str
    opts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"dim": self.dim, "opts": dict(self.opts)}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Rule":
        return Rule(d.get("dim", ""), dict(d.get("opts") or {}))


@dataclass
class PartitionSpec:
    mode: str = "rules"                # rules / template
    rules: list[Rule] = field(default_factory=list)
    template: str = ""

    def describe(self) -> str:
        if self.mode == "template":
            return f"模板：{self.template}"
        parts = []
        for r in self.rules:
            d = DIMENSIONS.get(r.dim)
            if d:
                parts.append(d.describe(d.resolve_opts(r.opts)))
        return " → ".join(parts) or "（无条件）"


# ---------------------------------------------------------------------------
# 模板：{年}/{年}-{月} {市|省|未知地点}
#   - 用 / 分隔文件夹层级
#   - {a|b|文字}：先取占位符 a，没有则取 b，再没有则用"文字"
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\{([^{}]*)\}")


def render_template(template: str, g, ctx: PartitionContext, tokens: dict | None = None) -> list[str]:
    tokens = tokens or all_tokens()
    segs = []
    for raw_seg in template.replace("\\", "/").split("/"):
        if not raw_seg.strip():
            continue

        def sub(m: re.Match) -> str:
            for alt in m.group(1).split("|"):
                alt = alt.strip()
                fn = tokens.get(alt)
                if fn is None:
                    if alt:
                        return alt          # 不是占位符 → 当作文字
                    continue
                try:
                    v = fn(g, ctx)
                except Exception:
                    v = None
                if v not in (None, ""):
                    return str(v)
            return "未知"

        seg = _TOKEN_RE.sub(sub, raw_seg).strip()
        if seg:
            segs.append(seg)
    return segs


def unknown_tokens(template: str) -> list[str]:
    tokens = all_tokens()
    bad = []
    for m in _TOKEN_RE.finditer(template):
        alts = [a.strip() for a in m.group(1).split("|")]
        # 若所有备选都不是占位符，并且只有一个 → 很可能是写错了
        if len(alts) == 1 and alts[0] not in tokens:
            bad.append(alts[0])
    return bad


def segments_with_order(g, spec: PartitionSpec, ctx: PartitionContext,
                        tokens: dict | None = None) -> list[tuple[str, str]]:
    """返回 [(文件夹名, 该级排序方式)]。"""
    pairs: list[tuple[str, str]] = []
    if spec.mode == "template":
        pairs = [(s, "name") for s in render_template(spec.template, g, ctx, tokens)]
    else:
        for r in spec.rules:
            d = DIMENSIONS.get(r.dim)
            if d is None:
                continue
            try:
                segs = d.segments(g, d.resolve_opts(r.opts), ctx)
            except Exception:
                segs = [d.unknown]
            pairs.extend((s, d.order_by) for s in segs)
    out = []
    for s, ob in pairs:
        s = sanitize_component(s)
        if s:
            out.append((s, ob))
    return out


def group_segments(g, spec: PartitionSpec, ctx: PartitionContext, tokens: dict | None = None) -> list[str]:
    return [s for s, _ in segments_with_order(g, spec, ctx, tokens)]


# ---------------------------------------------------------------------------
# 预览树
# ---------------------------------------------------------------------------

@dataclass
class TreeNode:
    name: str
    path: tuple[str, ...]
    children: dict[str, "TreeNode"] = field(default_factory=dict)
    gids: list[int] = field(default_factory=list)       # 直接位于此文件夹的组
    count: int = 0                                      # 含子文件夹的组数
    size: int = 0
    guess: int = 0                                      # 其中日期为推测的组数
    order_by: str = "count"

    def child(self, name: str, order_by: str) -> "TreeNode":
        n = self.children.get(name)
        if n is None:
            n = TreeNode(name, self.path + (name,), order_by=order_by)
            self.children[name] = n
        return n

    def sorted_children(self) -> list["TreeNode"]:
        kids = list(self.children.values())

        def key(n: TreeNode):
            unk = is_unknown_label(n.name) or n.name in ("日期待确认",)
            if n.order_by == "name":
                return (unk, n.name)
            return (unk, -n.count, n.name)
        return sorted(kids, key=key)

    def all_gids(self) -> list[int]:
        out = list(self.gids)
        for c in self.children.values():
            out.extend(c.all_gids())
        return out

    def folder_count(self) -> int:
        return len(self.children) + sum(c.folder_count() for c in self.children.values())


def build_tree(groups, spec: PartitionSpec, ctx: PartitionContext) -> tuple[TreeNode, dict[int, list[str]]]:
    root = TreeNode("", ())
    tokens = all_tokens()
    seg_map: dict[int, list[str]] = {}
    for g in groups:
        pairs = segments_with_order(g, spec, ctx, tokens)
        seg_map[g.gid] = [s for s, _ in pairs]
        root.count += 1
        root.size += g.total_size
        guess = 1 if g.date_is_guess else 0
        root.guess += guess
        if any(FORMAT_TOKEN in s for s, _ in pairs):
            # 按单个文件格式拆分：同一组照片的 RAW 与 JPG 分别出现在各自的文件夹里
            branches = []
            for fmt in group_formats(g):
                size = sum(f.size for f in g.files if member_format(f, g) == fmt)
                branches.append(([(s.replace(FORMAT_TOKEN, fmt), ob) for s, ob in pairs], size))
        else:
            branches = [(pairs, g.total_size)]
        for bp, size in branches:
            node = root
            for s, ob in bp:
                node = node.child(s, ob)
                node.count += 1
                node.size += size
                node.guess += guess
            node.gids.append(g.gid)
    return root, seg_map


# ---------------------------------------------------------------------------
# 预设
# ---------------------------------------------------------------------------

BUILTIN_PRESETS: list[dict[str, Any]] = [
    {"name": "按日期：年 / 年-月", "rules": [{"dim": "date", "opts": {"granularity": "y/ym"}}]},
    {"name": "按日期：年 / 年-月 / 日", "rules": [{"dim": "date", "opts": {"granularity": "y/ym/ymd"}}]},
    {"name": "按地区：国家 / 省 / 市", "rules": [{"dim": "region", "opts": {"level": "country/province/city"}}]},
    {"name": "旅行整理：年 / 地区（市）", "rules": [{"dim": "date", "opts": {"granularity": "y"}},
                                         {"dim": "region", "opts": {"level": "city"}}]},
    {"name": "按色调：冷暖", "rules": [{"dim": "color", "opts": {"mode": "warmth"}}]},
    {"name": "按色调：主色", "rules": [{"dim": "color", "opts": {"mode": "main"}}]},
    {"name": "摄影师：相机 / 年-月 / 类型", "rules": [{"dim": "camera", "opts": {"level": "model"}},
                                           {"dim": "date", "opts": {"granularity": "ym"}},
                                           {"dim": "filetype", "opts": {"mode": "kind"}}]},
    {"name": "组合：年 / 省 / 冷暖", "rules": [{"dim": "date", "opts": {"granularity": "y"}},
                                       {"dim": "region", "opts": {"level": "province/city"}},
                                       {"dim": "color", "opts": {"mode": "warmth"}}]},
    {"name": "清理：来源（相机 / 手机 / 截图）", "rules": [{"dim": "source", "opts": {}}]},
    {"name": "模板：年 / 年-月 城市", "template": "{年}/{年}-{月} {市|省|国家|未知地点}"},
]
