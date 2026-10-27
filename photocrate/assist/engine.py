"""小匣助手的入口：Assistant().ask(问题, 照片统计, 当前模式) → Answer。不依赖 Qt。

判断顺序：打招呼 → 直接给参数（“曝光 +0.5”）/ 调色描述 / 做法 → 诊断当前照片 → 找功能 → 没听懂（推荐最接近的条目）。
同一句里既像“在哪”又像调色时，有“在哪 / 菜单 / 入口”这类问法就以找功能为主，调色建议附在后面。
"""
from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass, field

from ..edit import adjust
from ..edit.quick import SLIDER_KEYS as QUICK_KEYS
from . import effects
from .features import BY_ID, FEATURES, Feature
from .photo import PhotoStats
from .text import find_terms, normalize, similarity, strip_fillers

WHERE = ("在哪", "哪里", "哪儿", "怎么找", "找不到", "入口", "菜单", "按钮", "怎么打开", "如何打开", "打开", "有没有",
         "支持", "怎么用", "如何使用", "快捷键", "功能", "在什么地方", "位置在", "哪个页面", "哪一页")
DIAGNOSE = ("怎么修", "怎么调", "有什么问题", "帮我看看", "看看这张", "诊断", "分析一下", "分析这张", "修一下这张", "这张怎么",
            "这张照片怎么", "哪里不好", "给点建议", "有什么建议", "怎么改进", "还能怎么", "问题在哪", "缺点")
VAGUE = ("漂亮", "好看", "高级", "有感觉", "有氛围", "氛围感", "美一点", "更美", "出片", "惊艳")
GREET = ("你好", "您好", "hi", "hello", "嗨", "你是谁", "能做什么", "会什么", "帮助", "help", "怎么问", "能干嘛", "你能")

EXAMPLES_FIND = ["抠图在哪", "怎么去掉照片里的路人", "批量调色怎么弄", "怎么把照片传到手机", "拍摄时间不对怎么改"]
EXAMPLES_LOOK = ["想要日系的感觉", "天空更蓝一点，不要太暖", "太灰了，想通透一些", "皮肤白一点、背景虚化", "这张照片怎么修"]

HSL_PARTS = ("色相", "饱和度", "明亮度")
_GROUP_OF = {k: g for g, keys in adjust.GROUPS for k in keys}


@dataclass
class Line:
    key: str
    where: str            # 滑块在属性面板的哪一组
    label: str
    value: str
    reason: str


@dataclass
class Plan:
    title: str
    params: dict                       # 完整的调整图层参数，可直接 new_adjust
    lines: list[Line]
    notes: list[str] = field(default_factory=list)
    preset: str = ""
    quick_ok: bool = False             # 只用到相册「编辑」里也有的滑块


@dataclass
class Answer:
    kind: str                          # greet / feature / effect / diagnose / unknown
    intro: str = ""
    features: list[Feature] = field(default_factory=list)
    plan: Plan | None = None
    recipes: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    source: str = ""                   # 大模型回答时是“Claude · 模型名”，本地回答为空


# ---------------------------------------------------------------------------
# 找功能
# ---------------------------------------------------------------------------

_KEYS: dict[str, list[str]] = defaultdict(list)
for _f in FEATURES:
    for _k in set(_f.keys) | {normalize(_f.title)}:
        if _k:
            _KEYS[normalize(_k)].append(_f.id)


def rank_features(q: str, mode: int = 0, limit: int = 4, spans: dict | None = None) -> list[tuple[Feature, float]]:
    """spans 不是 None 时，把每个功能命中的词在句子里的位置填进去（判断调色词是不是只是功能名的一部分）。"""
    scores: dict[str, float] = defaultdict(float)
    for i, j, k in find_terms(q, _KEYS):
        for fid in _KEYS[k]:
            scores[fid] += (j - i) ** 1.3
            if spans is not None:
                spans.setdefault(fid, []).append((i, j))
    if not scores:
        return []
    for fid in scores:
        if BY_ID[fid].mode == mode:
            scores[fid] *= 1.1
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], FEATURES.index(BY_ID[kv[0]])))
    best = ranked[0][1]
    if best < 1.0:
        return []
    return [(BY_ID[fid], s) for fid, s in ranked if s >= best * 0.6][:limit]


def closest_features(q: str, n: int = 3) -> list[Feature]:
    q = strip_fillers(q).replace("一点", "").replace("一些", "")
    scored = [(max(similarity(q, normalize(f.title)), *(similarity(q, k) for k in f.keys)), f) for f in FEATURES]
    scored.sort(key=lambda x: -x[0])
    return [f for s, f in scored[:n] if s >= 0.25]


# ---------------------------------------------------------------------------
# 调色方案
# ---------------------------------------------------------------------------

def _adapt(axis: effects.Axis, sign: int, st: PhotoStats | None) -> tuple[float, dict, str]:
    """按照片现状收放：(强度系数, 额外参数, 提示)。"""
    if st is None:
        return 1.0, {}, ""
    a = axis.id
    if a == "bright" and sign > 0:
        extra = {"highlights": -20} if st.hi_clip > 0.01 else {}
        if st.mean > 0.6:
            return 0.5, extra, f"这张已经偏亮（平均亮度 {st.mean:.0%}），提亮幅度减半，主要靠提阴影。"
        if extra:
            return 1.0, extra, f"已有 {st.hi_clip:.1%} 的地方接近纯白，提亮时顺便压了高光。"
    if a == "bright" and sign < 0:
        extra = {"shadows": 15} if st.lo_clip > 0.03 else {}
        if st.mean < 0.3:
            return 0.5, extra, f"这张已经偏暗（平均亮度 {st.mean:.0%}），压暗幅度减半。"
        if extra:
            return 1.0, extra, "暗部已经有不少死黑，压暗时把阴影抬回来一点。"
    if a == "contrast" and sign > 0 and st.spread > 0.92:
        return 0.6, {}, "这张明暗范围已经很宽，对比加得少一点，免得高光暗部丢细节。"
    if a == "contrast" and sign < 0 and st.spread < 0.55:
        return 0.5, {}, "这张本来就偏灰，再降对比会更闷，幅度减半。"
    if a == "warm" and st.neutral > 0.05:
        if sign > 0 and st.temp_fix < -12:
            return 0.5, {}, "照片已经偏暖，再加暖幅度减半。"
        if sign < 0 and st.temp_fix > 12:
            return 0.5, {}, "照片已经偏冷，再加冷幅度减半。"
    if a == "sat" and sign > 0 and st.sat > 0.42:
        return 1.0, {"saturation": -8}, "颜色已经挺浓，只加自然饱和度，不动饱和度。"
    if a == "clear" and sign > 0 and st.p1 > 0.06:
        lv = round(min(40.0, st.p1 * 255 * 0.7))
        return 1.0, {"lv_lo": lv}, f"最暗处只到 {st.p1:.0%} 的亮度（发灰的主要原因），用色阶把黑场拉到 {lv:.0f}。"
    return 1.0, {}, ""


def _fmt(key: str, v: float) -> str:
    if key == "exposure":
        return f"{v:+.2f}"
    if key in ("lv_gamma",):
        return f"{v:.2f}"
    if key.startswith("lv_") or key.startswith("sp_") and key.endswith("sat"):
        return f"{v:.0f}"
    if key.startswith("sp_") and key.endswith("hue"):
        return f"{v:.0f}°"
    return f"{v:+.0f}"


def _clamp(key: str, v: float) -> float:
    _, lo, hi, nd = adjust.SLIDERS[key]
    return round(max(lo, min(hi, v)), nd)


def compose(hits: list[effects.Hit], explicit=(), st: PhotoStats | None = None) -> Plan | None:
    styles = [h for h in hits if h.kind == "style"]
    axes = [h for h in hits if h.kind == "axis"]
    recipes = [h for h in hits if h.kind == "recipe" and h.obj.params]
    if not (styles or axes or recipes or explicit):
        return None
    p = adjust.default_params()
    d0 = adjust.default_params()
    reasons: dict[str, list[str]] = defaultdict(list)
    notes: list[str] = []
    preset = ""
    titles: list[str] = []
    if styles:
        s = styles[0].obj
        p = copy.deepcopy(s.params)
        preset = s.preset
        titles.append(s.name)
        for k in adjust.SLIDERS:
            if abs(float(p[k]) - float(d0[k])) > 1e-6:
                reasons[k].append(f"「{s.name}」：{s.why}")
        for band, v in p["hsl"].items():
            if any(v):
                reasons["hsl:" + band].append(f"「{s.name}」：{s.why}")
        if any(pts != d0["curve"][c] for c, pts in p["curve"].items()):
            reasons["curve"].append(f"「{s.name}」：{s.why}")
        others = [h.obj.name for h in styles[1:] if h.obj is not s]
        if others:
            notes.append(f"同时提到了「{s.name}」和「{'」「'.join(others)}」，先按「{s.name}」；想要另一种就单独问。")

    def add(key: str, dv, why: str) -> None:
        if key.startswith("hsl:"):
            band = key[4:]
            cur = p["hsl"][band]
            p["hsl"][band] = [cur[i] + float(dv[i]) for i in range(3)]
        else:
            p[key] = float(p[key]) + float(dv)
        if why not in reasons[key]:
            reasons[key].append(why)

    seen = set()
    for h in axes:
        k = (h.obj.id, h.sign)
        if k in seen:                                      # “亮一点，再亮一点”不要叠两次
            continue
        seen.add(k)
        mult, extra, note = _adapt(h.obj, h.sign, st)
        if note:
            notes.append(note)
        delta = h.obj.up if h.sign > 0 else h.obj.down
        why = h.obj.why_up if h.sign > 0 else h.obj.why_down
        for key, dv in delta.items():
            sc = h.scale * mult
            add(key, tuple(x * sc for x in dv) if isinstance(dv, tuple) else dv * sc, why)
        for key, v in extra.items():
            if key.startswith("lv_"):
                p[key] = max(float(p[key]), v)
                reasons[key].append(note)
            else:
                add(key, v, note)
        titles.append(h.label)
    for h in recipes:
        for key, v in h.obj.params.items():
            add(key, v, f"「{h.obj.name}」")
        titles.append(h.obj.name)
    for key, op, v, _span in explicit:
        p[key] = v if op == "set" else float(p[key]) + v
        reasons[key] = ["按你给的数值"]
        titles.append(adjust.SLIDERS[key][0])

    for key in adjust.SLIDERS:
        p[key] = _clamp(key, float(p[key]))
    for band in p["hsl"]:
        p["hsl"][band] = [round(max(-100.0, min(100.0, x))) for x in p["hsl"][band]]

    lines: list[Line] = []
    for key in adjust.SLIDERS:
        if abs(float(p[key]) - float(d0[key])) > 1e-6:
            lines.append(Line(key, _GROUP_OF.get(key, ""), adjust.SLIDERS[key][0], _fmt(key, p[key]),
                              "；".join(reasons.get(key) or ["配合上面的调整"])))
    for band, label, _c in adjust.HSL_BANDS:
        v = p["hsl"][band]
        for i in range(3):
            if v[i]:
                lines.append(Line("hsl:" + band, "HSL", f"{label} · {HSL_PARTS[i]}", f"{v[i]:+.0f}",
                                  "；".join(reasons.get("hsl:" + band) or [""])))
    if reasons.get("curve"):
        lines.append(Line("curve", "曲线", "RGB 曲线", "按预设", reasons["curve"][0]))
    if not lines:
        return None
    if any(h.complaint for h in axes) and not styles:
        notes.append("先按建议值试，觉得过了就把调整图层的不透明度调低，或者拖回一点。")
    quick_ok = all(l.key in QUICK_KEYS for l in lines)
    return Plan(" + ".join(dict.fromkeys(titles)), p, lines, notes, preset, quick_ok)


def diagnose(st: PhotoStats) -> Plan | None:
    """看直方图找问题，给一组修正参数。"""
    p = adjust.default_params()
    lines: list[Line] = []
    found: list[str] = []

    def put(key, v, why):
        p[key] = _clamp(key, float(p[key]) + v)
        lines.append(Line(key, _GROUP_OF.get(key, ""), adjust.SLIDERS[key][0], _fmt(key, p[key]), why))

    if st.mean < 0.28 and st.p99 < 0.9:
        import math
        ev = min(1.5, max(0.3, math.log2(0.42 / max(st.mean, 0.02))))
        found.append(f"整体偏暗（平均亮度 {st.mean:.0%}）")
        put("exposure", round(ev, 2), "欠曝：把平均亮度拉回 40% 左右")
        put("shadows", 20, "顺便提一点暗部")
    elif st.mean > 0.7:
        found.append(f"整体偏亮（平均亮度 {st.mean:.0%}）")
        put("exposure", -0.4, "过亮：整体压一点")
    if st.hi_clip > 0.02:
        found.append(f"{st.hi_clip:.1%} 的地方已经接近纯白")
        put("highlights", -45, "压高光，找回亮部细节（已经纯白的找不回来）")
    if st.lo_clip > 0.06:
        found.append(f"{st.lo_clip:.1%} 的地方接近纯黑")
        put("shadows", 30, "提阴影，救回暗部细节")
    if st.spread < 0.6:
        found.append(f"明暗范围窄（{st.p1:.0%}–{st.p99:.0%}），画面发灰")
        lo = round(min(40.0, st.p1 * 255 * 0.8))
        hi = round(max(215.0, 255 - (1 - st.p99) * 255 * 0.8))
        if lo > 2:
            p["lv_lo"] = lo
            lines.append(Line("lv_lo", "色阶", adjust.SLIDERS["lv_lo"][0], f"{lo:.0f}", "把最暗处拉到接近黑"))
        if hi < 253:
            p["lv_hi"] = hi
            lines.append(Line("lv_hi", "色阶", adjust.SLIDERS["lv_hi"][0], f"{hi:.0f}", "把最亮处拉到接近白"))
        put("contrast", 12, "再加一点对比")
    if st.neutral > 0.05 and not st.mono:
        if abs(st.temp_fix) > 12:
            found.append("可能偏暖（发黄）" if st.temp_fix < 0 else "可能偏冷（发蓝）")
            put("temp", round(st.temp_fix * 0.7), "按画面里接近灰白的地方估计的白平衡（如果是故意的暖调 / 冷调就不用改）")
        if abs(st.tint_fix) > 10:
            found.append("可能偏绿" if st.tint_fix > 0 else "可能偏紫红")
            put("tint", round(st.tint_fix * 0.7), "按接近灰白的地方估计的色调偏差")
    if not st.mono:
        if st.sat < 0.12:
            found.append(f"颜色偏淡（平均饱和度 {st.sat:.0%}）")
            put("vibrance", 25, "自然饱和度：先加不够鲜艳的颜色")
        elif st.sat > 0.5:
            found.append(f"颜色很浓（平均饱和度 {st.sat:.0%}）")
            put("saturation", -12, "收一点饱和度，免得颜色溢出")
    if not lines:
        return None
    return Plan("修正：" + "、".join(found), p, lines, [], "", all(l.key in QUICK_KEYS for l in lines))


# ---------------------------------------------------------------------------

class Assistant:
    def ask(self, question: str, stats: PhotoStats | None = None, mode: int = 0) -> Answer:
        q = normalize(question)
        explicit = effects.explicit_values(q)
        rest = q
        for *_x, (i, j) in explicit:                      # 数值已经说清楚了，其余部分再按描述理解
            rest = rest[:i] + " " * (j - i) + rest[j:]
        hits = effects.parse(rest)
        recipes = list(dict.fromkeys(h.obj for h in hits if h.kind == "recipe"))
        where = any(w in q for w in WHERE)
        spans: dict = {}
        ranked = rank_features(q, mode, spans=spans)
        look = [h for h in hits if h.kind in ("axis", "style")] or explicit
        # “找出拍糊的照片”：调色词“糊”只是相册功能名的一部分，按找功能处理
        if look and not explicit and ranked and ranked[0][0].mode == 0:
            cover = spans.get(ranked[0][0].id, [])
            if all(any(a <= h.span[0] and h.span[1] <= b for a, b in cover) for h in hits):
                hits, look, recipes = [], [], []
        if not q or (any(g in q for g in GREET) and not (look or recipes or ranked)):
            return Answer("greet", "我是小匣助手，在本机运行、不联网。可以这样问我：",
                          suggestions=EXAMPLES_FIND[:3] + EXAMPLES_LOOK[:3])

        # 只问位置（“清晰度在哪”“曲线怎么打开”）：找功能
        if where and ranked and not recipes:
            return self._features(ranked, q)

        if look or recipes:
            plan = compose(hits, explicit, stats)
            feats = []
            for r in recipes:
                feats += [BY_ID[i] for i in r.features if i in BY_ID]
            if plan is not None:
                feats.append(BY_ID["presets" if plan.preset else "adjust_layer"])
                feats += [f for f, _ in ranked[:2] if f.mode == 1 and f.id not in ("adjust_layer", "presets")]
            ans = Answer("effect", features=list(dict.fromkeys(feats)), plan=plan, recipes=recipes)
            if plan is not None:
                ans.intro = "新建一个调整图层，按下面的值拖滑块（或直接点「应用」）："
                if plan.preset:
                    ans.notes.append(f"也可以直接用「图像 → 滤镜预设 → {plan.preset}」，效果一样，再按需要微调。")
                ans.notes.append("相册里「编辑」也有这些滑块，可以在那里直接调。" if plan.quick_ok else
                                 "其中 HSL / 分离色调 / 曲线这些在修图的调整图层里才有。")
                if stats is None:
                    ans.notes.append("在修图里打开照片后再问，我会按这张照片的明暗和偏色调整幅度。")
            elif recipes:
                ans.intro = "这个要分几步做："
            return ans

        if any(w in q for w in DIAGNOSE) or q in ("这张", "这张照片"):
            if stats is None:
                return Answer("diagnose", "我需要看到照片：先在「修图」里打开它，再问一次“这张照片怎么修”。",
                              features=[BY_ID["open_studio"]])
            plan = diagnose(stats)
            if plan is None:
                return Answer("diagnose", "看了一下直方图：曝光、对比、颜色都在正常范围，没有明显问题。"
                              "想换个感觉可以说说想要的效果，比如“更通透”“胶片感”。",
                              suggestions=EXAMPLES_LOOK[:3])
            return Answer("diagnose", "看了一下这张照片的直方图，发现这些问题，建议这样调：", plan=plan,
                          notes=["这是按统计数字给的起点，最终以你的眼睛为准。"])

        if ranked:
            return self._features(ranked, q)

        if any(w in q for w in VAGUE):
            return Answer("unknown", "“好看”有很多种，说得具体一点我才能给参数，比如：",
                          suggestions=["更通透一些", "颜色更鲜艳", "要胶片感", "这张照片怎么修"])
        close = closest_features(q)
        return Answer("unknown", "没太听懂。我只回答影匣的功能位置和调色问题。" +
                      ("你是不是想找：" if close else "可以这样问："),
                      features=close, suggestions=[] if close else EXAMPLES_FIND[:2] + EXAMPLES_LOOK[:2])

    @staticmethod
    def _features(ranked, q) -> Answer:
        feats = [f for f, _ in ranked]
        intro = "在这里：" if len(feats) == 1 else "找到这几个相关的功能："
        return Answer("feature", intro, features=feats)
