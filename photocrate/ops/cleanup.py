"""清理 RAW / 删除弃用照片：生成删除计划（模拟运行）→ 确认 → 逐组放入回收站（先写日志，可撤销）。

三种方式：
- keep_jpg     只保留 JPG：有 JPG/HEIC 配对的照片删除 RAW（以及 RAW 的 .xmp 等修图记录）；只有 RAW 的照片不动。
- unrated_raw  删除没打星的 RAW：未评星、也没标“留用”的照片删除 RAW；只有 RAW 的照片默认不动
               （勾选“也删除没有 JPG 的”时整张删除）。
- rejected     删除标记为“弃用”的照片：整组（RAW + JPG + 附属文件）。
删除一律进系统回收站；撤销时从回收站放回原处（见 trashbin.py）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .planner import Plan, PlanItem, PlanOp
from ..i18n import t as _t

RAW_SIDECAR_EXT = (".xmp", ".pp3", ".dop", ".cos", ".cof", ".arp", ".on1")
TRASH_DST = _t("（回收站）")

MODES = {
    "keep_jpg": _t("只保留 JPG（删除有 JPG 配对的 RAW）"),
    "unrated_raw": _t("删除没打星的 RAW"),
    "rejected": _t("删除标记为“弃用”的照片（整组）"),
}


def _raw_side(g) -> list:
    """RAW 文件及属于 RAW 的修图记录。"""
    raws = [f for f in g.files if f.role == "raw"]
    stems = {os.path.splitext(f.name)[0].lower() for f in raws} | {f.name.lower() for f in raws}
    side = [f for f in g.files if f.role == "sidecar" and f.name.lower().endswith(RAW_SIDECAR_EXT)
            and os.path.splitext(f.name)[0].lower() in stems]
    return raws + side


def _has_other_image(g) -> bool:
    return any(f.role in ("jpeg", "heif", "png", "image") for f in g.files)


def build_cleanup_plan(groups, mode: str, include_raw_only: bool = False) -> Plan:
    plan = Plan(spec_text=_t('清理：{0}', MODES.get(mode, mode)), target_root="", operation="delete")
    skipped_only_raw = 0
    skipped_kept = 0
    dirs = set()
    for g in groups:
        if not g.files:
            continue
        r = g.feats.get("rating") or {}
        files: list = []
        if mode == "keep_jpg":
            if not g.has_raw:
                continue
            if not _has_other_image(g):
                skipped_only_raw += 1
                continue
            files = _raw_side(g)
        elif mode == "unrated_raw":
            if not g.has_raw:
                continue
            if int(r.get("rating") or 0) > 0 or r.get("flag") == "pick":
                skipped_kept += 1
                continue
            if _has_other_image(g):
                files = _raw_side(g)
            elif include_raw_only:
                files = list(g.files)
            else:
                skipped_only_raw += 1
                continue
        elif mode == "rejected":
            if r.get("flag") != "reject":
                continue
            files = list(g.files)
        if not files:
            continue
        ops = [PlanOp(f.path, TRASH_DST, f.size, f.mtime_ns, f.role) for f in files]
        plan.items.append(PlanItem(g.gid, ops, os.path.dirname(g.primary)))
        plan.total_bytes += sum(f.size for f in files)
        dirs.add(os.path.dirname(g.primary))
    if dirs:
        try:
            plan.target_root = os.path.commonpath(sorted(dirs))
        except ValueError:
            plan.target_root = sorted(dirs)[0]
    if skipped_only_raw:
        plan.warnings.append(_t('{0} 张照片只有 RAW、没有 JPG，未删除', skipped_only_raw)
                             + (_t("（可勾选“也删除没有 JPG 的 RAW”）。") if mode == "unrated_raw" else _t("。")))
    if skipped_kept:
        plan.warnings.append(_t('{0} 张已打星或标记“留用”的照片保留。', skipped_kept))
    if not plan.items:
        plan.warnings.append(_t("没有符合条件的文件。"))
    return plan


@dataclass
class DeleteResult:
    batch_id: int
    deleted: list[str] = field(default_factory=list)
    done_groups: int = 0
    failed_groups: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)
    freed: int = 0
