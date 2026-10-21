"""内置分区维度。"""
from __future__ import annotations

import os

from ..analyzers.builtin import (BLUR_LABELS, FLAG_NAMES, LABEL_NAMES, ORIENTATION_LABELS, SOURCE_LABELS,
                                 blur_state, classify_color)
from .base import FORMAT_TOKEN, Dimension, Option, PartitionContext, register_dimension


# ---------------------------------------------------------------------------
# 日期
# ---------------------------------------------------------------------------

class DateDimension(Dimension):
    id = "date"
    label = "拍摄日期"
    en = "DATE"
    description = "按 EXIF 拍摄时间分组；没有时依次用文件名、修改时间推测。"
    unknown = "未知日期"
    order_by = "name"
    options = [
        Option("granularity", "层级", default="y/ym", choices=[
            ("y", "年"),
            ("y/ym", "年 / 年-月"),
            ("y/ym/ymd", "年 / 年-月 / 年-月-日"),
            ("ym", "年-月"),
            ("ymd", "年-月-日"),
            ("y/q", "年 / 季度"),
        ]),
        Option("guess", "推测日期", default="normal", choices=[
            ("normal", "正常归类"),
            ("separate", "单独放「日期待确认」"),
        ]),
    ]

    def segments(self, g, opts, ctx):
        dt = g.date
        if dt is None:
            return [self.unknown]
        if opts.get("guess") == "separate" and g.date_is_guess:
            prefix = ["日期待确认"]
        else:
            prefix = []
        y, ym, ymd = f"{dt.year:04d}", f"{dt.year:04d}-{dt.month:02d}", f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
        q = f"Q{(dt.month - 1) // 3 + 1}"
        return prefix + {
            "y": [y], "y/ym": [y, ym], "y/ym/ymd": [y, ym, ymd], "ym": [ym], "ymd": [ymd], "y/q": [y, f"{y}-{q}"],
        }.get(opts.get("granularity", "y/ym"), [y, ym])

    def tokens(self):
        def d(fmt):
            return lambda g, ctx: g.date.strftime(fmt) if g.date else None
        return {
            "年": d("%Y"), "月": d("%m"), "日": d("%d"),
            "年月": d("%Y-%m"), "年月日": d("%Y-%m-%d"),
            "日期": d("%Y%m%d"), "时间": d("%H%M%S"),          # 紧凑写法，适合文件名
            "季度": lambda g, ctx: f"Q{(g.date.month - 1) // 3 + 1}" if g.date else None,
            "日期来源": lambda g, ctx: {"exif": "EXIF", "exif_create": "EXIF", "xmp": "XMP",
                                    "filename": "文件名推测", "mtime": "修改时间推测"}.get(g.date_source),
        }


# ---------------------------------------------------------------------------
# 地区
# ---------------------------------------------------------------------------

class RegionDimension(Dimension):
    id = "region"
    label = "地区"
    en = "REGION"
    description = "按 GPS、轨迹 / 参考照片匹配或手动指定的地点，离线识别国家 / 省 / 市 / 区县；都没有的放入「未知地点」。"
    unknown = "未知地点"
    options = [
        Option("level", "层级", default="country/province/city", choices=[
            ("country", "国家"),
            ("country/province", "国家 / 省"),
            ("country/province/city", "国家 / 省 / 市"),
            ("country/province/city/district", "国家 / 省 / 市 / 区县"),
            ("province/city", "省 / 市（国内不写“中国”）"),
            ("city", "仅城市"),
        ]),
        Option("inferred", "使用推测地点", kind="bool", default=True,
               tip="设置中开启“按时间就近推断地点”后生效"),
    ]

    def segments(self, g, opts, ctx):
        geo = g.feats.get("geo") or {}
        if not geo.get("country"):
            return [self.unknown]
        if geo.get("inferred") and not opts.get("inferred", True):
            return [self.unknown]
        country, prov, city, dist = geo.get("country"), geo.get("province"), geo.get("city"), geo.get("district")
        level = opts.get("level", "country/province/city")
        if level == "province/city":
            parts = ([prov, city] if geo.get("in_china") else [country, prov, city])
        elif level == "city":
            parts = [city or prov or country]
        else:
            want = level.split("/")
            parts = [{"country": country, "province": prov, "city": city, "district": dist}[k] for k in want]
        out = []
        for p in parts:
            if not p:
                break           # 缺少更细层级时停在已知的最细一级
            if out and out[-1] == p:
                continue        # 北京市/北京市 → 北京市
            out.append(p)
        return out or [self.unknown]

    def tokens(self):
        def f(key):
            return lambda g, ctx: (g.feats.get("geo") or {}).get(key) or None
        def finest(g, ctx):
            geo = g.feats.get("geo") or {}
            return geo.get("district") or geo.get("city") or geo.get("province") or geo.get("country") or None
        return {"国家": f("country"), "省": f("province"), "市": f("city"), "区县": f("district"), "地区": finest}


# ---------------------------------------------------------------------------
# 色调
# ---------------------------------------------------------------------------

class ColorDimension(Dimension):
    id = "color"
    label = "色调"
    en = "COLOR TONE"
    description = "根据预览图的颜色统计：冷暖、主色、明暗。阈值可在设置中调整。"
    unknown = "未分析色调"
    options = [
        Option("mode", "分组方式", default="warmth", choices=[
            ("warmth", "冷暖（暖 / 冷 / 中性 / 黑白）"),
            ("main", "主色（红 / 橙 / 黄 / 绿 / 蓝 …）"),
            ("tone", "明暗（暗调 / 中间调 / 亮调）"),
            ("warmth/tone", "冷暖 / 明暗（两级）"),
            ("warmth+tone", "冷暖·明暗（合并一级）"),
        ]),
    ]

    def segments(self, g, opts, ctx):
        c = classify_color(g.feats.get("color"), ctx.settings)
        if not c:
            return [self.unknown]
        mode = opts.get("mode", "warmth")
        if mode == "main":
            return [c["main"]]
        if mode == "tone":
            return [c["tone"]]
        if mode == "warmth/tone":
            return [c["warmth"], c["tone"]]
        if mode == "warmth+tone":
            return [f"{c['warmth']}·{c['tone']}"]
        return [c["warmth"]]

    def tokens(self):
        def f(key):
            def fn(g, ctx):
                c = classify_color(g.feats.get("color"), ctx.settings)
                return c[key] if c else None
            return fn
        return {"冷暖": f("warmth"), "主色": f("main"), "明暗": f("tone")}


# ---------------------------------------------------------------------------
# 相机 / 镜头 / 来源
# ---------------------------------------------------------------------------

class CameraDimension(Dimension):
    id = "camera"
    label = "相机"
    en = "CAMERA"
    description = "按 EXIF 中的相机品牌 / 型号分组。"
    unknown = "未知相机"
    options = [
        Option("level", "层级", default="model", choices=[
            ("make", "品牌"), ("model", "型号"), ("make/model", "品牌 / 型号"),
        ]),
    ]

    def segments(self, g, opts, ctx):
        make, model = g.feat("camera", "make"), g.feat("camera", "model")
        lv = opts.get("level", "model")
        if lv == "make":
            return [make or self.unknown]
        if lv == "make/model":
            return [make, model] if make and model else [make or model or self.unknown]
        return [model or make or self.unknown]

    def tokens(self):
        return {"品牌": lambda g, c: g.feat("camera", "make"), "相机": lambda g, c: g.feat("camera", "model")}


class LensDimension(Dimension):
    id = "lens"
    label = "镜头"
    en = "LENS"
    description = "按 EXIF 镜头型号分组（手机照片通常为机身镜头）。"
    unknown = "未知镜头"
    options = []

    def segments(self, g, opts, ctx):
        return [g.feat("camera", "lens") or self.unknown]

    def tokens(self):
        return {"镜头": lambda g, c: g.feat("camera", "lens")}


class SourceDimension(Dimension):
    id = "source"
    label = "来源"
    en = "SOURCE"
    description = "相机 / 手机 / 无人机 / 截图 / 其他（无相机信息，如网图、聊天保存）。"
    unknown = "其他（无相机信息）"
    options = []

    def segments(self, g, opts, ctx):
        return [SOURCE_LABELS.get(g.feat("camera", "source"), self.unknown)]

    def tokens(self):
        return {"来源": lambda g, c: SOURCE_LABELS.get(g.feat("camera", "source"))}


# ---------------------------------------------------------------------------
# 文件类型 / 画幅 / 分辨率 / 焦段 / ISO / 清晰度 / 原文件夹
# ---------------------------------------------------------------------------

class FileTypeDimension(Dimension):
    id = "filetype"
    label = "文件类型"
    en = "FILE TYPE"
    description = "按格式分开放（ARW / JPG / HEIC …），或按整组类型（RAW+JPG 视为同一张）。"
    unknown = "其他类型"
    order_by = "count"
    options = [
        Option("mode", "分组方式", default="file", choices=[
            ("file", "按格式分开（RAW 与 JPG 各放各的）"),
            ("kind", "按整组类型（RAW+JPG 放一起）"),
            ("ext", "按整组扩展名（ARW+JPG 放一起）"),
        ]),
    ]

    def segments(self, g, opts, ctx):
        mode = opts.get("mode", "file")
        if mode == "ext":
            return [g.ext_label]
        if mode == "kind":
            return [g.kind_label]
        # 每个文件按自己的格式去对应文件夹；.xmp/.aae 等附属文件跟随所属图片
        return [FORMAT_TOKEN]

    def tokens(self):
        return {"格式": lambda g, c: FORMAT_TOKEN, "类型": lambda g, c: g.kind_label,
                "扩展名": lambda g, c: g.ext_label}


class OrientationDimension(Dimension):
    id = "orientation"
    label = "横竖幅"
    en = "ORIENTATION"
    description = "横幅 / 竖幅 / 方形 / 全景（已按 EXIF 方向旋转后判断）。"
    unknown = "未知画幅"
    options = []

    def segments(self, g, opts, ctx):
        return [ORIENTATION_LABELS.get(g.feat("shape", "orientation"), self.unknown)]

    def tokens(self):
        return {"方向": lambda g, c: ORIENTATION_LABELS.get(g.feat("shape", "orientation"))}


def _bucket(v, edges, labels):
    for e, lab in zip(edges, labels):
        if v < e:
            return lab
    return labels[-1]


class ResolutionDimension(Dimension):
    id = "resolution"
    label = "分辨率"
    en = "RESOLUTION"
    description = "按像素数分档。"
    unknown = "未知分辨率"
    order_by = "name"
    options = []
    EDGES = [2, 8, 16, 30]
    LABELS = ["1_小于200万像素", "2_200-800万像素", "3_800-1600万像素", "4_1600-3000万像素", "5_3000万像素以上"]

    def _label(self, g):
        mp = g.feat("shape", "mp")
        return _bucket(mp, self.EDGES, self.LABELS) if mp else None

    def segments(self, g, opts, ctx):
        return [self._label(g) or self.unknown]

    def tokens(self):
        return {"分辨率": lambda g, c: self._label(g)}


class FocalDimension(Dimension):
    id = "focal"
    label = "焦段"
    en = "FOCAL LENGTH"
    description = "按等效 35mm 焦距分档（无等效焦距时用实际焦距）。"
    unknown = "未知焦段"
    order_by = "name"
    options = []
    EDGES = [24, 35, 70, 135]
    LABELS = ["1_超广角(24mm以下)", "2_广角(24-34mm)", "3_标准(35-69mm)", "4_中长焦(70-134mm)", "5_长焦(135mm以上)"]

    def _label(self, g):
        f = g.meta.get("focal35") or g.meta.get("focal")
        return _bucket(f, self.EDGES, self.LABELS) if f else None

    def segments(self, g, opts, ctx):
        return [self._label(g) or self.unknown]

    def tokens(self):
        return {"焦段": lambda g, c: self._label(g)}


class IsoDimension(Dimension):
    id = "iso"
    label = "感光度"
    en = "ISO"
    description = "按 ISO 分档。"
    unknown = "未知ISO"
    order_by = "name"
    options = []
    EDGES = [201, 801, 3201]
    LABELS = ["1_ISO 200以下", "2_ISO 201-800", "3_ISO 801-3200", "4_ISO 3200以上"]

    def _label(self, g):
        iso = g.meta.get("iso")
        return _bucket(iso, self.EDGES, self.LABELS) if iso else None

    def segments(self, g, opts, ctx):
        return [self._label(g) or self.unknown]

    def tokens(self):
        return {"感光度": lambda g, c: self._label(g)}


class BlurDimension(Dimension):
    id = "blur"
    label = "清晰度"
    en = "SHARPNESS"
    description = "拉普拉斯方差检测模糊；阈值在「清晰度」页或设置中调整。"
    unknown = "无法判断"
    options = []

    def segments(self, g, opts, ctx):
        return [BLUR_LABELS[blur_state(g, float(ctx.settings.get("blur_threshold", 60)))]]

    def tokens(self):
        return {"清晰度": lambda g, c: BLUR_LABELS[blur_state(g, float(c.settings.get("blur_threshold", 60)))]}


class RatingDimension(Dimension):
    id = "rating"
    label = "星级 / 标记"
    en = "RATING"
    description = "按在本软件或 Lightroom / Bridge 里打的星级、留弃标记或色标分组。"
    unknown = "未评星"
    order_by = "name"
    options = [Option("mode", "分类方式", default="rating", choices=[
        ("rating", "星级（5星 … 1星 / 未评星，弃用单独一组）"),
        ("flag", "留用 / 弃用 / 未标记"),
        ("label", "色标"),
    ])]

    @staticmethod
    def _rating(g):
        if g.feat("rating", "flag") == "reject":
            return "弃用"
        r = int(g.feat("rating", "rating") or 0)
        return f"{r}星" if r > 0 else None

    @staticmethod
    def _flag(g):
        return FLAG_NAMES.get(g.feat("rating", "flag"))

    @staticmethod
    def _label(g):
        lab = g.feat("rating", "label")
        return (LABEL_NAMES.get(lab, lab) + "色标") if lab else None

    def segments(self, g, opts, ctx):
        mode = opts.get("mode", "rating")
        if mode == "flag":
            return [self._flag(g) or "未标记"]
        if mode == "label":
            return [self._label(g) or "无色标"]
        return [self._rating(g) or self.unknown]

    def tokens(self):
        return {"星级": lambda g, c: self._rating(g), "标记": lambda g, c: self._flag(g),
                "色标": lambda g, c: self._label(g)}


class TripDimension(Dimension):
    id = "trip"
    label = "旅行"
    en = "TRIP"
    description = "按自动分出的每次旅行归档（在「旅行与地图」页可设常住地、改旅行名）；不属于任何旅行的照片放进“日常”。"
    unknown = "日常"
    order_by = "name"
    options = [Option("mode", "层级", default="trip", choices=[
        ("trip", "旅行名（2025-12-31 日本 东京·京都·大阪）"),
        ("trip/day", "旅行名 / 第几天"),
        ("trip/daydate", "旅行名 / 第几天 日期（第3天 01-02）"),
        ("day", "只要第几天（放在别的条件下面用）"),
    ]),
        Option("other", "非旅行照片", kind="text", default="", tip="留空 = 使用设置里的“日常”")]

    @staticmethod
    def _trip(g):
        t = g.feats.get("trip")
        return t.get("name") if t else None

    @staticmethod
    def _day(g, with_date=False):
        t = g.feats.get("trip")
        if not t:
            return None
        from ..core.trips import day_label
        s = day_label(int(t["day"]), int(t.get("days") or 0))
        if with_date and g.date:
            s += g.date.strftime(" %m-%d")
        return s

    def segments(self, g, opts, ctx):
        other = (opts.get("other") or ctx.settings.get("trip_other_name") or self.unknown).strip()
        mode = opts.get("mode", "trip")
        name = self._trip(g)
        if not name:
            return [other]
        if mode == "day":
            return [self._day(g)]
        if mode == "trip/day":
            return [name, self._day(g)]
        if mode == "trip/daydate":
            return [name, self._day(g, True)]
        return [name]

    def tokens(self):
        return {"旅行": lambda g, c: self._trip(g), "第几天": lambda g, c: self._day(g),
                "旅行天数": lambda g, c: (f"{g.feats['trip']['days']}天" if g.feats.get("trip") else None)}


class SceneDimension(Dimension):
    id = "scene"
    label = "场景（AI）"
    en = "SCENE"
    description = "按 AI 识别的画面内容分组（山景、海边、夜景、美食、人像…）。需要先在「AI 搜图」页下载模型并分析。"
    unknown = "未识别"
    order_by = "count"
    options = [Option("mode", "分类", default="scene", choices=[("scene", "细分场景（山景 / 海边 / 夜景 / 美食…）"),
                                                             ("group", "大类（自然风光 / 城市建筑 / 人物 / 美食…）")]),
               Option("min_prob", "把握低于", kind="float", default=0.25, minimum=0, maximum=0.9, step=0.05,
                      tip="AI 把握不大的照片归入“其他场景”（0 = 一律按最像的场景）")]

    def segments(self, g, opts, ctx):
        sc = g.feats.get("scene")
        if not sc:
            return [self.unknown]
        if float(sc.get("prob") or 0) < float(opts.get("min_prob") or 0):
            return ["其他场景"]
        return [sc["group"] if opts.get("mode") == "group" else sc["scene"]]

    def tokens(self):
        return {"场景": lambda g, c: (g.feats.get("scene") or {}).get("scene"),
                "场景大类": lambda g, c: (g.feats.get("scene") or {}).get("group")}


class PersonDimension(Dimension):
    id = "person"
    label = "人物（AI）"
    en = "PEOPLE"
    description = "按照片里的人分组：在「人物」页给人脸组起名后生效。没有人脸的照片放进“无人像”。"
    unknown = "无人像"
    order_by = "count"
    options = [Option("mode", "分法", default="main", choices=[
        ("main", "主要人物（照片里脸最大的已命名的人）"),
        ("group", "单人按名字，两位及以上已命名的人放“合影”"),
        ("has", "有人像 / 无人像"),
    ])]

    def segments(self, g, opts, ctx):
        pp = g.feats.get("people")
        other = ctx.settings.get("people_other_name") or "其他人"
        if not pp or not pp.get("n"):
            return [self.unknown]
        mode = opts.get("mode", "main")
        if mode == "has":
            return ["有人像"]
        names = pp.get("names") or []
        if mode == "group" and len(names) >= 2:
            return ["合影"]
        return [names[0] if names else other]

    def tokens(self):
        return {"人物": lambda g, c: "、".join(((g.feats.get("people") or {}).get("names") or [])[:3]) or None}


class FolderDimension(Dimension):
    id = "folder"
    label = "原文件夹名"
    en = "ORIGIN FOLDER"
    description = "保留照片原来所在文件夹的名字（如“2023 云南旅行”）。"
    unknown = "根目录"
    options = []

    def segments(self, g, opts, ctx):
        return [os.path.basename(g.folder.rstrip("\\/")) or self.unknown]

    def tokens(self):
        return {"原文件夹": lambda g, c: os.path.basename(g.folder.rstrip("\\/")) or None}


class FixedDimension(Dimension):
    """固定文件夹名（例如「待确认_模糊」）。"""
    id = "fixed"
    label = "固定文件夹"
    en = "FIXED"
    description = "所有照片放进同一个指定名称的文件夹。"
    options = [Option("name", "文件夹名", kind="text", default="已整理")]

    def segments(self, g, opts, ctx):
        return [opts.get("name") or "已整理"]


def register_builtin() -> None:
    for d in (DateDimension(), RegionDimension(), ColorDimension(), CameraDimension(), LensDimension(),
              SourceDimension(), FileTypeDimension(), OrientationDimension(), BlurDimension(), RatingDimension(),
              TripDimension(), SceneDimension(), PersonDimension(),
              FocalDimension(), IsoDimension(), ResolutionDimension(), FolderDimension(), FixedDimension()):
        register_dimension(d)


__all__ = ["register_builtin", "PartitionContext"]
