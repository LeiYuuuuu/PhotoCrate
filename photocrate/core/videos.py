"""视频的拍摄时间校正。

手机 / 相机视频（.mp4 / .mov）的 QuickTime CreateDate 记录的是 UTC。ExifTool 读取时按“这台电脑的时区”换算成本地时间，
在家拍的视频没问题，旅行中拍的就会差几个小时（在日本拍、在美国的电脑上看，会差 14 小时，甚至跨天）。

做法：有带时区的时间（苹果 CreationDate、索尼 XML 的 CreationDateValue）时直接用；只有 UTC 时，
找拍摄时间（按 UTC 比较）最接近、且时区已知的照片，用它的时区换算——同一趟旅行里照片和视频的时区一致。
24 小时内都没有照片时保持按电脑时区换算的结果，并标记为“时区推测”。
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timedelta

from .geotime import format_offset, parse_offset


def _utc(dt: datetime, tz: str | None) -> datetime | None:
    off = parse_offset(tz)
    if off is None:
        return None
    return dt - timedelta(seconds=off)


def align_video_times(groups, window_hours: float = 24) -> int:
    """返回校正了时区的视频数量。"""
    refs = []
    for g in groups:
        d = g.feats.get("date") or {}
        if g.files and not g.is_video and d.get("dt") and d.get("tz") and not d.get("tz_guess"):
            u = _utc(datetime.fromisoformat(d["dt"]), d["tz"])
            if u is not None:
                refs.append((u, d["tz"], g.name))
    refs.sort(key=lambda r: r[0])
    keys = [r[0] for r in refs]
    win = timedelta(hours=window_hours)
    n = 0
    for g in groups:
        if not g.files or not g.is_video or not g.meta.get("qt_utc"):
            continue
        d = g.feats.get("date") or {}
        if not d.get("dt") or d.get("source") not in ("exif", "exif_create") or d.get("corrected"):
            continue
        u = _utc(datetime.fromisoformat(d["dt"]), d.get("tz"))
        if u is None:
            continue
        best = None
        if refs:
            i = bisect_left(keys, u)
            for j in (i - 1, i):
                if 0 <= j < len(refs) and abs(refs[j][0] - u) <= win:
                    if best is None or abs(refs[j][0] - u) < abs(best[0] - u):
                        best = refs[j]
        nd = dict(d)
        if best is None:
            nd["tz_guess"] = True
        else:
            off = parse_offset(best[1])
            local = u + timedelta(seconds=off)
            nd.update({"dt": local.isoformat(), "tz": format_offset(off), "tz_from": best[2]})
            nd.pop("tz_guess", None)
            if nd["dt"] != d["dt"]:
                n += 1
        g.feats["date"] = nd
    return n
