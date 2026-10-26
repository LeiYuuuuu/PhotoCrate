"""导出与分享：把选中的照片导出成适合发朋友圈 / 小红书的 JPG，或把一次旅行做成网页相册。

只读原片、写新文件到用户选的文件夹，原片和照片库都不受影响（所以不进操作日志；导出文件夹里的东西随便删）。

导出 JPG：
- 来源：有 JPG / HEIC / PNG 时用它；只有 RAW 时用相机内嵌的全尺寸预览（索尼 / 佳能 / 尼康通常是全尺寸 JPEG）；
- 长边缩到指定像素（或保持原尺寸），按 EXIF 方向转正，sRGB 色彩配置文件照旧保留；
- 隐私：默认去掉 GPS（拍摄时间、相机型号保留）；也可以去掉全部 EXIF；
- 可加文字水印（右下 / 左下 / 居中…，透明度可调）；
- 分享前隐私体检（可选，默认关）：ops/privacy 找出手机号、身份证、车牌、快递单、二维码、路人脸，确认后只在导出的副本上打码；
- 文件名：沿用原名，或按改名模板（与批量重命名相同）；重名自动加编号，绝不覆盖。
视频不导出（会在结果里列出跳过了几个）。
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from ..i18n import is_en, t as _t

SIZES = [(1080, _t("1080 px（朋友圈 / 小红书够用）")), (1440, "1440 px"), (2048, _t("2048 px（高清）")),
         (3840, _t("3840 px（4K）")), (0, _t("原始尺寸"))]
POSITIONS = {"br": _t("右下"), "bl": _t("左下"), "tr": _t("右上"), "tl": _t("左上"), "c": _t("居中")}


@dataclass
class ExportOptions:
    long_edge: int = 2048
    quality: int = 90
    strip_gps: bool = True
    strip_all: bool = False
    watermark: str = ""
    wm_pos: str = "br"
    wm_opacity: float = 0.7
    wm_scale: float = 0.028          # 字号 = 长边 × 比例
    name_template: str = ""          # 空 = 原文件名
    redact: dict = field(default_factory=dict)    # gid → [(x0, y0, x1, y1)]（0–1，转正后的图）：要打码的区域
    redact_style: str = "mosaic"                  # mosaic / blur / block


@dataclass
class ExportResult:
    written: list[str] = field(default_factory=list)
    skipped_video: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    bytes: int = 0


# ---------------------------------------------------------------------------
# 读原片
# ---------------------------------------------------------------------------

def source_file(g):
    imgs = [f for f in g.files if f.role in ("jpeg", "heif", "png", "image")]
    if imgs:
        return max(imgs, key=lambda f: ({"jpeg": 3, "heif": 2, "png": 1}.get(f.role, 0), f.size))
    raws = [f for f in g.files if f.role == "raw"]
    return raws[0] if raws else None


def load_full(g, exiftool: str | None = None):
    """返回 (PIL.Image RGB 已转正, exif 对象或 None, icc_profile 或 None)。"""
    from PIL import Image, ImageOps

    f = source_file(g)
    if f is None:
        raise ValueError(_t("没有可导出的图片文件"))
    if f.role == "raw":
        from ..core.imaging import load_raw_preview
        im, _ = load_raw_preview(f.path, 100000, exiftool)
        im = im.convert("RGB")
        exif = Image.Exif()
        _fill_exif_from_meta(exif, g)
        return im, exif, None
    if f.role == "heif":
        from ..core.imaging import _ensure_heif
        _ensure_heif()
    im = Image.open(f.path)
    exif = im.getexif()
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB",):
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
    return im, exif, icc


def _fill_exif_from_meta(exif, g) -> None:
    """RAW 的内嵌预览没有完整 EXIF：从已读到的元数据补上拍摄时间、相机、镜头参数。"""
    m = g.meta or {}
    d = g.date
    if m.get("make"):
        exif[0x010F] = str(m["make"])
    if m.get("model"):
        exif[0x0110] = str(m["model"])
    ifd = exif.get_ifd(0x8769)
    if d:
        ifd[0x9003] = d.strftime("%Y:%m:%d %H:%M:%S")
    tz = g.feat("date", "tz")
    if tz:
        ifd[0x9011] = tz
    if m.get("fnumber"):
        ifd[0x829D] = float(m["fnumber"])
    if m.get("exposure"):
        ifd[0x829A] = float(m["exposure"])
    if m.get("iso"):
        ifd[0x8827] = int(m["iso"])
    if m.get("focal"):
        ifd[0x920A] = float(m["focal"])
    if m.get("lens"):
        ifd[0xA434] = str(m["lens"])


# ---------------------------------------------------------------------------
# 水印
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int):
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def add_watermark(im, text: str, pos: str = "br", opacity: float = 0.7, scale: float = 0.028):
    from PIL import Image, ImageDraw

    if not text.strip():
        return im
    size = max(12, int(max(im.size) * scale))
    font = _font(size)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(layer)
    l, t, r, b = dr.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    m = int(size * 0.9)
    x = {"br": im.width - tw - m, "tr": im.width - tw - m, "bl": m, "tl": m, "c": (im.width - tw) // 2}[pos]
    y = {"br": im.height - th - m, "bl": im.height - th - m, "tr": m, "tl": m, "c": (im.height - th) // 2}[pos]
    a = int(255 * max(0.05, min(1.0, opacity)))
    sh = max(1, size // 18)
    dr.text((x - l + sh, y - t + sh), text, font=font, fill=(0, 0, 0, int(a * 0.55)))
    dr.text((x - l, y - t), text, font=font, fill=(255, 255, 255, a))
    out = im.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert("RGB")


# ---------------------------------------------------------------------------
# 导出 JPG
# ---------------------------------------------------------------------------

def _unique(folder: str, stem: str, ext: str, taken: set[str]) -> str:
    n = 0
    while True:
        name = f"{stem}{ext}" if n == 0 else f"{stem} ({n}){ext}"
        if name.lower() not in taken and not os.path.exists(os.path.join(folder, name)):
            taken.add(name.lower())
            return os.path.join(folder, name)
        n += 1


def render_one(g, opts: ExportOptions, exiftool: str | None = None, long_edge: int | None = None):
    """返回 (处理好的 PIL 图, 要写入的 exif bytes 或 None, icc)。"""
    from PIL import Image

    im, exif, icc = load_full(g, exiftool)
    boxes = opts.redact.get(g.gid) if opts.redact else None
    if boxes:                                  # 先在原尺寸上打码，再缩小（缩小后的马赛克格子大小才一致）
        from .privacy import redact
        im = redact(im, boxes, opts.redact_style)
    le = opts.long_edge if long_edge is None else long_edge
    if le and max(im.size) > le:
        im.thumbnail((le, le), Image.Resampling.LANCZOS)
    if opts.watermark:
        im = add_watermark(im, opts.watermark, opts.wm_pos, opts.wm_opacity, opts.wm_scale)
    data = None
    if not opts.strip_all and exif is not None:
        exif[0x0112] = 1                       # 已经转正
        if opts.strip_gps:
            exif.pop(0x8825, None)             # GPS IFD
        for tag in (0x0100, 0x0101):           # 旧的宽高
            exif.pop(tag, None)
        try:
            data = exif.tobytes()
        except Exception:
            data = None
    return im, data, icc


def export_jpegs(groups, dst_dir: str, opts: ExportOptions, ctx=None, exiftool: str | None = None,
                 progress: Callable[[int, int, str], None] | None = None,
                 cancel: threading.Event | None = None) -> ExportResult:
    res = ExportResult()
    os.makedirs(dst_dir, exist_ok=True)
    taken: set[str] = set()
    items = [g for g in groups if g.files]
    items.sort(key=lambda g: (g.date is None, g.date or datetime.min, g.name.lower()))
    tokens = None
    if opts.name_template:
        from ..partition.base import all_tokens
        tokens = all_tokens()
    day_n: dict[str, int] = {}
    for i, g in enumerate(items, 1):
        if cancel is not None and cancel.is_set():
            res.cancelled = True
            break
        if g.is_video:
            res.skipped_video += 1
            continue
        try:
            stem = g.stem
            if opts.name_template:
                from .renamer import render_name
                day = g.date.strftime("%Y-%m-%d") if g.date else ""
                day_n[day] = day_n.get(day, 0) + 1
                stem = render_name(opts.name_template, g, ctx, i, day_n[day], tokens) or g.stem
            im, exif, icc = render_one(g, opts, exiftool)
            path = _unique(dst_dir, stem, ".jpg", taken)
            kw: dict[str, Any] = {"quality": int(opts.quality), "optimize": True, "progressive": True,
                                  "subsampling": "4:2:0" if opts.quality < 92 else "4:4:4"}
            if exif:
                kw["exif"] = exif
            if icc:
                kw["icc_profile"] = icc
            tmp = path + ".part"
            im.save(tmp, "JPEG", **kw)
            os.replace(tmp, path)
            res.written.append(path)
            res.bytes += os.path.getsize(path)
        except Exception as e:
            res.failed += 1
            res.errors.append(_t('{0}：{1}', g.name, e))
        if progress:
            progress(i, len(items), g.name)
    return res


# ---------------------------------------------------------------------------
# 网页相册
# ---------------------------------------------------------------------------

def _round_coord(v: float) -> float:
    return round(float(v), 2)       # 约 1 km，只到城市 / 街区级别，不暴露具体位置


def export_album(groups, dst_dir: str, title: str, opts: ExportOptions, subtitle: str = "",
                 exiftool: str | None = None, show_map: bool = True,
                 progress: Callable[[int, int, str], None] | None = None,
                 cancel: threading.Event | None = None) -> tuple[str, ExportResult]:
    """生成一个文件夹：index.html + photos/（长边 opts.long_edge）+ thumbs/（长边 480）。
    按天分组；有位置时显示地图（每天一个点、按时间连线，坐标只保留到约 1 km）。返回 (index.html 路径, 结果)。"""
    from ..core.trips import photo_coords

    res = ExportResult()
    safe = "".join(c for c in title if c not in '\\/:*?"<>|').strip() or _t("相册")
    root = dst_dir if os.path.basename(os.path.normpath(dst_dir)) == safe else os.path.join(dst_dir, safe)
    if os.path.exists(os.path.join(root, "index.html")):
        n = 2
        while os.path.exists(os.path.join(f"{root} ({n})", "index.html")):
            n += 1
        root = f"{root} ({n})"
    pdir, tdir = os.path.join(root, "photos"), os.path.join(root, "thumbs")
    os.makedirs(pdir, exist_ok=True)
    os.makedirs(tdir, exist_ok=True)
    items = [g for g in groups if g.files]
    items.sort(key=lambda g: (g.date is None, g.date or datetime.min, g.name.lower()))
    days: dict[str, dict[str, Any]] = {}
    taken: set[str] = set()
    for i, g in enumerate(items, 1):
        if cancel is not None and cancel.is_set():
            res.cancelled = True
            break
        if g.is_video:
            res.skipped_video += 1
            continue
        try:
            im, _, icc = render_one(g, ExportOptions(long_edge=opts.long_edge or 1920, watermark=opts.watermark,
                                                     wm_pos=opts.wm_pos, wm_opacity=opts.wm_opacity,
                                                     strip_all=True, redact=opts.redact,
                                                     redact_style=opts.redact_style), exiftool)
            name = os.path.basename(_unique(pdir, f"{i:04d}_{g.stem}", ".jpg", taken))
            kw = {"quality": int(opts.quality), "optimize": True, "progressive": True}
            if icc:
                kw["icc_profile"] = icc
            im.save(os.path.join(pdir, name), "JPEG", **kw)
            th = im.copy()
            th.thumbnail((480, 480))
            th.save(os.path.join(tdir, name), "JPEG", quality=82, optimize=True)
            res.written.append(os.path.join(pdir, name))
            res.bytes += os.path.getsize(os.path.join(pdir, name)) + os.path.getsize(os.path.join(tdir, name))
            key = g.date.strftime("%Y-%m-%d") if g.date else _t("日期未知")
            day = days.setdefault(key, {"date": key, "photos": [], "places": [], "pts": []})
            geo = g.feats.get("geo") or {}
            place = _t(geo.get("city") or geo.get("province") or geo.get("country"))
            if place and place not in day["places"]:
                day["places"].append(place)
            c = photo_coords(g)
            if c is not None:
                day["pts"].append(c)
            sc = _t((g.feats.get("scene") or {}).get("scene"))
            day["photos"].append({"f": name, "w": im.width, "h": im.height,
                                  "t": g.date.strftime("%H:%M") if g.date else "",
                                  "p": place or "", "s": sc or ""})
        except Exception as e:
            res.failed += 1
            res.errors.append(_t('{0}：{1}', g.name, e))
        if progress:
            progress(i, len(items), g.name)
    day_list = []
    for n, d in enumerate(sorted(days.values(), key=lambda d: d["date"]), 1):
        pts = d.pop("pts")
        if pts:
            d["lat"] = _round_coord(sum(p[0] for p in pts) / len(pts))
            d["lon"] = _round_coord(sum(p[1] for p in pts) / len(pts))
        d["n"] = n
        day_list.append(d)
    data = {"title": title, "subtitle": subtitle, "days": day_list,
            "made": datetime.now().strftime("%Y-%m-%d"), "map": bool(show_map and any("lat" in d for d in day_list))}
    index = os.path.join(root, "index.html")
    with open(index, "w", encoding="utf-8") as f:
        # 一次替换两个占位符：标题、地名里恰好写着 __DATA__ / __TITLE__ 也不会被再替换一次
        f.write(re.sub(r"__(TITLE|DATA)__", lambda m: html.escape(title) if m.group(1) == "TITLE" else _script_json(data),
                       _album_html()))
    return index, res


def _script_json(data) -> str:
    """嵌进 <script> 的 JSON：< > & 写成 \\u 转义，标题、地名里的 </script>、<!-- 都不会提前结束或打乱脚本。"""
    s = json.dumps(data, ensure_ascii=False)
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026") \
        .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _album_html() -> str:
    """网页相册模板；英文界面下把模板里的几处中文换成英文。"""
    if not is_en():
        return _ALBUM_HTML
    out = _ALBUM_HTML
    for zh, en in (('lang="zh-CN"', 'lang="en"'), ('aria-label="关闭"', 'aria-label="Close"'),
                   ('aria-label="上一张"', 'aria-label="Previous"'), ('aria-label="下一张"', 'aria-label="Next"'),
                   ("用 影匣 PhotoCrate 导出", "Made with PhotoCrate"), ('" 天 · "', '" days · "'),
                   ('" 张"', '" photos"'), ('"第" + d.n + "天 " + d.date.slice(5)', '"Day " + d.n + " " + d.date.slice(5)'),
                   ('"第" + d.n + "天 " + (d.places', '"Day " + d.n + " " + (d.places')):
        out = out.replace(zh, en)
    return out


_ALBUM_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{--bg:#f4f3ef;--ink:#1d1f22;--dim:#6b6f75;--line:#d9d8d2;--accent:#f2c230;--card:#ffffff}
@media (prefers-color-scheme:dark){:root{--bg:#121315;--ink:#ecebe6;--dim:#9a9ea4;--line:#2a2c30;--accent:#f2c230;--card:#1b1d20}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",system-ui,sans-serif}
header{padding:48px 20px 24px;max-width:1200px;margin:0 auto}
header h1{font-size:clamp(26px,4vw,40px);margin:0 0 6px;font-weight:800;letter-spacing:.02em}
header h1:before{content:"";display:inline-block;width:10px;height:.9em;background:var(--accent);margin-right:12px;vertical-align:-2px}
header p{margin:0;color:var(--dim)}
#map{height:340px;max-width:1200px;margin:0 auto 8px;border:1px solid var(--line)}
nav{position:sticky;top:0;z-index:500;background:var(--bg);border-bottom:1px solid var(--line)}
nav div{max-width:1200px;margin:0 auto;padding:10px 20px;display:flex;gap:8px;overflow-x:auto}
nav a{flex:none;text-decoration:none;color:var(--ink);border:1px solid var(--line);padding:4px 10px;font-size:14px;white-space:nowrap}
nav a:hover{border-color:var(--accent)}
section{max-width:1200px;margin:0 auto;padding:28px 20px 8px}
section h2{font-size:20px;margin:0 0 4px}
section h2 span{color:var(--accent);margin-right:8px}
section .meta{color:var(--dim);font-size:14px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px}
.grid figure{margin:0;background:var(--card);cursor:zoom-in;overflow:hidden;position:relative}
.grid img{width:100%;height:220px;object-fit:cover;display:block;transition:transform .25s}
.grid figure:hover img{transform:scale(1.04)}
.grid figcaption{position:absolute;left:0;right:0;bottom:0;padding:16px 8px 6px;font-size:12px;color:#fff;background:linear-gradient(transparent,rgba(0,0,0,.55))}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;z-index:1000}
#lb.on{display:flex}
#lb img{max-width:96vw;max-height:88vh}
#lb .cap{position:fixed;bottom:14px;left:0;right:0;text-align:center;color:#ddd;font-size:14px}
#lb button{position:fixed;top:50%;background:none;border:0;color:#fff;font-size:44px;cursor:pointer;padding:20px}
#lb .prev{left:4px}#lb .next{right:4px}#lb .x{top:8px;right:8px;font-size:32px}
footer{color:var(--dim);font-size:12px;text-align:center;padding:40px 20px}
</style>
</head>
<body>
<header><h1 id="t"></h1><p id="st"></p></header>
<div id="map" hidden></div>
<nav><div id="nav"></div></nav>
<main id="main"></main>
<div id="lb"><button class="x" aria-label="关闭">×</button><button class="prev" aria-label="上一张">‹</button>
<img alt=""><button class="next" aria-label="下一张">›</button><div class="cap"></div></div>
<footer>用 影匣 PhotoCrate 导出 · <span id="made"></span></footer>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => "&#" + c.charCodeAt(0) + ";");
$("#t").textContent = D.title; document.title = D.title;
const total = D.days.reduce((a, d) => a + d.photos.length, 0);
$("#st").textContent = (D.subtitle ? D.subtitle + " · " : "") + D.days.length + " 天 · " + total + " 张";
$("#made").textContent = D.made;
const flat = [];
for (const d of D.days) {
  const a = document.createElement("a"); a.href = "#d" + d.n; a.textContent = "第" + d.n + "天 " + d.date.slice(5);
  $("#nav").appendChild(a);
  const s = document.createElement("section"); s.id = "d" + d.n;
  s.innerHTML = "<h2><span>DAY " + String(d.n).padStart(2, "0") + "</span>" + esc(d.date) + "</h2><div class=meta></div><div class=grid></div>";
  s.querySelector(".meta").textContent = (d.places.join(" · ") || "") + (d.places.length ? " · " : "") + d.photos.length + " 张";
  const grid = s.querySelector(".grid");
  for (const p of d.photos) {
    const i = flat.length; flat.push(p);
    const f = document.createElement("figure");
    f.innerHTML = "<img loading=lazy alt=''><figcaption></figcaption>";
    f.querySelector("img").src = "thumbs/" + encodeURIComponent(p.f);
    f.querySelector("figcaption").textContent = [p.t, p.p, p.s].filter(Boolean).join(" · ");
    f.onclick = () => show(i);
    grid.appendChild(f);
  }
  $("#main").appendChild(s);
}
let cur = 0;
function show(i) {
  cur = (i + flat.length) % flat.length; const p = flat[cur];
  $("#lb img").src = "photos/" + encodeURIComponent(p.f);
  $("#lb .cap").textContent = (cur + 1) + " / " + flat.length + "   " + [p.t, p.p, p.s].filter(Boolean).join(" · ");
  $("#lb").classList.add("on");
}
$("#lb .x").onclick = () => $("#lb").classList.remove("on");
$("#lb .prev").onclick = e => { e.stopPropagation(); show(cur - 1); };
$("#lb .next").onclick = e => { e.stopPropagation(); show(cur + 1); };
$("#lb").onclick = e => { if (e.target.id === "lb") $("#lb").classList.remove("on"); };
document.addEventListener("keydown", e => {
  if (!$("#lb").classList.contains("on")) return;
  if (e.key === "Escape") $("#lb").classList.remove("on");
  if (e.key === "ArrowLeft") show(cur - 1);
  if (e.key === "ArrowRight") show(cur + 1);
});
if (D.map) {
  const s = document.createElement("script");
  s.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
  s.onload = () => {
    const pts = D.days.filter(d => d.lat !== undefined);
    $("#map").hidden = false;
    const m = L.map("map", {scrollWheelZoom: false});
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 18, attribution: "© OpenStreetMap contributors"}).addTo(m);
    const ll = pts.map(d => [d.lat, d.lon]);
    if (ll.length > 1) L.polyline(ll, {color: "#e0a800", weight: 3, dashArray: "6 6"}).addTo(m);
    for (const d of pts) L.circleMarker([d.lat, d.lon], {radius: 8, color: "#1d1f22", fillColor: "#f2c230", fillOpacity: 1})
      .addTo(m).bindTooltip(esc("第" + d.n + "天 " + (d.places.join("·") || d.date))).on("click", () => location.hash = "d" + d.n);
    m.fitBounds(L.latLngBounds(ll).pad(0.3), {maxZoom: 12});
  };
  document.head.appendChild(s);
}
</script>
</body>
</html>
"""
