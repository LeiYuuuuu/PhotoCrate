"""统计概览数据。"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..analyzers.builtin import (BLUR_LABELS, ORIENTATION_LABELS, SOURCE_LABELS, blur_state,
                                 classify_color)

MAIN_COLOR_SWATCH = {
    "红": "#D9443B", "橙": "#EE8A2E", "黄": "#E9C83A", "黄绿": "#A5C23A", "绿": "#4BA35A", "青": "#2FB3B0",
    "蓝": "#3C7BD9", "紫": "#7E5AC9", "粉紫": "#C95AA8", "棕": "#8A5A3A", "黑白灰": "#8C8C8C", "低饱和": "#B9B4AA",
}
WARMTH_SWATCH = {"暖色调": "#F08A3C", "冷色调": "#3D86D6", "中性": "#A8A39A", "黑白": "#5C5C5C"}
TONE_SWATCH = {"暗调": "#2E2E2E", "中间调": "#8A8A8A", "亮调": "#E4E2DC"}


def compute_stats(groups, settings: dict[str, Any]) -> dict[str, Any]:
    s: dict[str, Any] = {}
    groups = [g for g in groups if g.files]
    s["total"] = len(groups)
    s["files"] = sum(len(g.files) for g in groups)
    s["bytes"] = sum(g.total_size for g in groups)
    s["raw"] = sum(1 for g in groups if g.has_raw)
    s["gps"] = sum(1 for g in groups if g.feat("geo", "has_gps") and not g.feat("geo", "inferred"))
    s["exif_date"] = sum(1 for g in groups if g.date_source in ("exif", "exif_create", "xmp"))
    s["guess_date"] = sum(1 for g in groups if g.date_is_guess)
    s["no_date"] = sum(1 for g in groups if g.date is None)
    s["screenshot"] = sum(1 for g in groups if g.feat("camera", "source") == "screenshot")

    months: Counter = Counter()
    for g in groups:
        if g.date:
            months[f"{g.date.year:04d}-{g.date.month:02d}"] += 1
    s["months"] = dict(sorted(months.items()))

    region: Counter = Counter()
    for g in groups:
        geo = g.feats.get("geo") or {}
        if not geo.get("country"):
            region["未知地点"] += 1
        elif geo.get("in_china"):
            region[geo.get("province") or "中国"] += 1
        else:
            region[geo.get("country")] += 1
    s["region"] = region.most_common()

    warmth: Counter = Counter()
    main: Counter = Counter()
    tone: Counter = Counter()
    for g in groups:
        c = classify_color(g.feats.get("color"), settings)
        if c:
            warmth[c["warmth"]] += 1
            main[c["main"]] += 1
            tone[c["tone"]] += 1
    s["warmth"] = warmth.most_common()
    s["main_color"] = main.most_common()
    s["tone"] = [(k, tone[k]) for k in ("暗调", "中间调", "亮调") if tone[k]]

    s["camera"] = Counter(g.feat("camera", "model") or "未知相机" for g in groups).most_common()
    s["lens"] = Counter(g.feat("camera", "lens") or "未知镜头" for g in groups).most_common()
    s["kind"] = Counter(g.kind_label for g in groups).most_common()
    s["source"] = Counter(SOURCE_LABELS.get(g.feat("camera", "source"), "其他") for g in groups).most_common()
    s["orientation"] = Counter(ORIENTATION_LABELS.get(g.feat("shape", "orientation"), "未知") for g in groups).most_common()
    thr = float(settings.get("blur_threshold", 60))
    s["blur"] = Counter(BLUR_LABELS[blur_state(g, thr)] for g in groups).most_common()
    return s
