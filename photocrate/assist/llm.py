"""可选的大模型后端：Claude / DeepSeek / GPT / Grok / Kimi。只用标准库 urllib，不加依赖。

可靠性靠三层，而不是相信模型：
1. 给模型的系统提示里带完整的功能目录（id、路径、用法）和滑块表（键、范围、含义），本地助手的初稿也一并给它参考；
2. 要求按 JSON Schema 输出：功能只能填目录里的 id（enum），参数只能填滑块键（enum），支持的服务商用结构化输出强制；
3. 回来之后 ground() 再校验一遍：不认识的 id / 参数键丢掉，数值夹到滑块范围，回答里用「」引用的界面名称
   不在影匣里就标出来。界面上的菜单路径永远来自本地知识库，不用模型写的。
任何一步失败（没网、Key 不对、模型名不对、JSON 坏了）都退回本地回答。

隐私：默认只发问题文字、当前模式和照片的统计数字；缩略图要在设置里勾选才发。
"""
from __future__ import annotations

import base64
import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from types import SimpleNamespace

from ..edit import adjust
from . import effects
from .engine import _GROUP_OF, HSL_PARTS, QUICK_KEYS, Answer, Line, Plan, _clamp, _fmt
from .features import BY_ID, DESCRIPTIVE, FEATURES
from .photo import PhotoStats

ANTHROPIC_VERSION = "2026-10-21"
TIMEOUT = 60


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    kind: str                 # anthropic / responses（OpenAI）/ chat（OpenAI 兼容的 chat/completions）
    base: str
    models: tuple
    json_mode: str = "schema"  # schema / object（只保证是 JSON）/ none
    key_url: str = ""


PROVIDERS: list[Provider] = [
    Provider("claude", "Claude", "anthropic", "https://api.anthropic.com/v1",
             ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"), key_url="https://platform.claude.com"),
    Provider("deepseek", "DeepSeek", "chat", "https://api.deepseek.com",
             ("deepseek-flash", "deepseek-v4-pro"), "object", "https://platform.deepseek.com"),
    Provider("openai", "GPT", "responses", "https://api.openai.com/v1",
             ("gpt-6-luna", "gpt-6-astra"), key_url="https://platform.openai.com"),
    Provider("grok", "Grok", "chat", "https://api.x.ai/v1", ("grok-4.7", "grok-build-0.1"), key_url="https://console.x.ai"),
    Provider("kimi", "Kimi", "chat", "https://api.moonshot.cn/v1", ("kimi-k3.1-pro", "kimi-k3.1-pro-highspeed"),
             "object", "https://platform.kimi.com"),
]
BY_PID = {p.id: p for p in PROVIDERS}

DEFAULT_CONFIG = {"provider": "local", "models": {}, "base_urls": {}, "send_image": False}


def load_config(settings) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(settings.get("assistant_llm", {}) or {})
    return cfg


def model_of(cfg: dict, pid: str) -> str:
    return (cfg.get("models") or {}).get(pid) or BY_PID[pid].models[0]


def base_of(cfg: dict, pid: str) -> str:
    return ((cfg.get("base_urls") or {}).get(pid) or BY_PID[pid].base).rstrip("/")


class LLMError(Exception):
    """message 是给用户看的中文。"""


# ---------------------------------------------------------------------------
# 提示词与输出格式
# ---------------------------------------------------------------------------

_HSL_KEYS = [f"hsl:{b}:{part}" for b, _, _ in adjust.HSL_BANDS for part in ("hue", "sat", "lum")]
PARAM_KEYS = list(adjust.SLIDERS) + _HSL_KEYS
_MEANING = {
    "exposure": "整体亮度，单位档，+1 = 亮一倍", "contrast": "明暗反差", "highlights": "只调亮部，负值找回过曝细节",
    "shadows": "只调暗部，正值提亮阴影", "whites": "白场", "blacks": "黑场，负值压实黑色",
    "temp": "负 = 偏蓝（冷），正 = 偏黄（暖）", "tint": "负 = 偏绿，正 = 偏洋红",
    "vibrance": "只加不够鲜艳的颜色，保护肤色", "saturation": "所有颜色一起",
    "clarity": "中等尺度的细节和质感，负值柔化", "sharpen": "边缘锐化", "denoise": "降噪",
    "vignette": "负 = 四周变暗，正 = 四周变亮", "grain": "胶片颗粒", "fade": "抬黑场的褪色 / 哑光感",
    "lv_lo": "色阶输入黑场（0–254）", "lv_hi": "色阶输入白场（1–255）", "lv_gamma": "色阶中间调（1 = 不变）",
    "lv_out_lo": "输出黑场", "lv_out_hi": "输出白场",
    "sp_hi_hue": "分离色调：高光染色的色相（度）", "sp_hi_sat": "高光染色的浓度",
    "sp_sh_hue": "阴影染色的色相（度）", "sp_sh_sat": "阴影染色的浓度", "sp_balance": "高光 / 阴影的平衡",
}


def schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "reply": {"type": "string"},
            "features": {"type": "array", "items": {"type": "string", "enum": [f.id for f in FEATURES]}},
            "params": {"type": "array", "items": {
                "type": "object",
                "properties": {"key": {"type": "string", "enum": PARAM_KEYS}, "value": {"type": "number"},
                               "why": {"type": "string"}},
                "required": ["key", "value", "why"], "additionalProperties": False}},
            "steps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["reply", "features", "params", "steps"],
        "additionalProperties": False,
    }


def _catalog() -> str:
    return "\n".join(f"{f.id} | {f.title} | {' › '.join(f.path)} | {f.how}" for f in FEATURES)


def _sliders() -> str:
    d = adjust.default_params()
    rows = [f"{k} | {name} | {lo}–{hi}，默认 {d[k]} | {_MEANING.get(k, '')}"
            for k, (name, lo, hi, _nd) in adjust.SLIDERS.items()]
    bands = "、".join(f"{b}={label}" for b, label, _ in adjust.HSL_BANDS)
    rows.append(f"hsl:<颜色>:hue|sat|lum | HSL 某种颜色的色相 / 饱和度 / 明亮度 | -100–100，默认 0 | 颜色：{bands}；"
                "hue ±100 约等于色相转 ±30°")
    return "\n".join(rows)


_SYSTEM: str | None = None


def system_prompt() -> str:
    """固定不变（不含时间、问题），服务商可以缓存。"""
    global _SYSTEM
    if _SYSTEM is None:
        _SYSTEM = f"""你是照片管理和修图软件「影匣 PhotoCrate」里的助手。用户会问某个功能在哪、怎么用，或者描述想要的画面效果。
影匣有两个主界面：相册（浏览、搜索、整理照片）和修图（图层、蒙版、调整图层、滤镜，类似 Photoshop）。

规则：
1. 只回答和影匣有关的问题。别的问题在 reply 里礼貌说明只能回答影匣的功能和调色，其余字段留空。
2. 提到影匣的功能时，把它的 id 放进 features（只能用下面目录里的 id，最多 5 个，最相关的放前面）。
   界面会按 id 显示准确的菜单路径，所以 reply 里不要自己写菜单路径，也不要编造目录里没有的功能、按钮或菜单。
   影匣没有的功能就直接说没有，并给出最接近的替代做法。
3. 调色需求给 params：每项是调整图层上某个滑块的最终值（不是增量），只列需要改的，key 只能用下面滑块表里的键，
   数值必须在范围内。why 用一句中文说明作用。用户只问位置时 params 留空。
4. 滑块做不到的（选区、工具、滤镜、局部处理），写进 steps，每步一句中文；引用功能时用目录里的功能标题，放在「」里。
5. 会给你本地助手的初稿（规则匹配，可能不准）和照片的统计数字，可以参考、纠正或补充。
6. reply 用中文，简洁（120 字以内），不要客套。拿不准就说拿不准。
7. 只输出一个 JSON 对象：{{"reply": "...", "features": ["id", ...], "params": [{{"key": "...", "value": 0, "why": "..."}}], "steps": ["..."]}}

功能目录（id | 标题 | 位置 | 用法）：
{_catalog()}

调整图层滑块（key | 名称 | 范围 | 含义）：
{_sliders()}

滤镜预设（「图像 → 滤镜预设」，一键套用后是普通调整图层）：{"、".join(adjust.PRESETS)}
"""
    return _SYSTEM


def user_prompt(question: str, local: Answer | None, stats: PhotoStats | None, mode: int,
                history: list[tuple[str, str]] = (), image: bool = False) -> str:
    parts = [f"当前界面：{'修图' if mode == 1 else '相册'}"]
    if stats is not None:
        parts.append(f"当前照片统计：平均亮度 {stats.mean:.0%}，最暗 1% {stats.p1:.0%}，最亮 99% {stats.p99:.0%}，"
                     f"接近纯白 {stats.hi_clip:.1%}，接近纯黑 {stats.lo_clip:.1%}，平均饱和度 {stats.sat:.0%}，"
                     f"按中性灰估计的白平衡修正：色温 {stats.temp_fix:+.0f}、色调 {stats.tint_fix:+.0f}"
                     + ("（中性灰太少，偏色估计不可靠）" if stats.neutral < 0.05 else ""))
    else:
        parts.append("没有打开的照片（或不在修图界面），看不到画面。")
    if image:
        parts.append("附带了当前画面的缩略图。")
    if history:
        parts.append("前面的对话：\n" + "\n".join(f"用户：{q}\n助手：{a}" for q, a in history[-3:]))
    if local is not None:
        draft = []
        if local.features:
            draft.append("相关功能 id：" + "、".join(f.id for f in local.features))
        if local.plan is not None:
            draft.append("参数：" + "；".join(f"{l.label} {l.value}" for l in local.plan.lines))
        for r in local.recipes:
            draft.append(f"{r.name}：" + " / ".join(r.steps))
        if draft:
            parts.append("本地助手初稿：\n" + "\n".join(draft))
    parts.append(f"用户的问题：{question}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _headers(p: Provider, key: str) -> dict:
    h = {"Content-Type": "application/json", "User-Agent": "PhotoCrate-Assistant"}
    if p.kind == "anthropic":
        h.update({"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION})
    else:
        h["Authorization"] = f"Bearer {key}"
    return h


def _http(url: str, headers: dict, body: dict | None = None, timeout: float = TIMEOUT) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:      # 走系统代理（Windows 读注册表里的代理设置）
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:600]
        except Exception:
            detail = ""
        err = _status_error(e.code, detail)
        err.status, err.detail = e.code, detail
        raise err from None
    except (socket.timeout, TimeoutError):
        raise LLMError(f"等了 {timeout:.0f} 秒没有回应，网络可能不通或服务商太忙。") from None
    except urllib.error.URLError as e:
        raise LLMError(f"连不上服务器（{e.reason}）。在国内用 Claude / GPT / Grok 通常需要代理，"
                       "或在设置里填中转地址。") from None
    except ValueError:
        raise LLMError("服务器返回的不是 JSON，地址可能填错了。") from None


def _status_error(code: int, detail: str) -> LLMError:
    msg = _api_message(detail)
    if code in (401, 403):
        return LLMError(f"API Key 无效或没有权限（{code}）。{msg}")
    if code == 404:
        return LLMError(f"找不到这个模型或地址（404），可以在设置里点「刷新」看看有哪些模型。{msg}")
    if code == 402:
        return LLMError(f"账户余额不足（402）。{msg}")
    if code == 429:
        return LLMError(f"请求太频繁或额度用完了（429），稍后再试。{msg}")
    if code >= 500:
        return LLMError(f"服务商那边出错了（{code}），稍后再试。")
    return LLMError(f"请求被拒绝（{code}）。{msg}")


def _api_message(detail: str) -> str:
    try:
        d = json.loads(detail)
        e = d.get("error", d)
        m = e.get("message") if isinstance(e, dict) else str(e)
        return str(m)[:200] if m else ""
    except Exception:
        return detail[:200]


def _body(p: Provider, model: str, system: str, user: str, image: bytes | None, structured: bool) -> tuple[str, dict]:
    img64 = base64.b64encode(image).decode("ascii") if image else None
    if p.kind == "anthropic":
        content = ([{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img64}}]
                   if img64 else []) + [{"type": "text", "text": user}]
        body = {"model": model, "max_tokens": 8000,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": content}]}
        if structured:
            body["output_config"] = {"effort": "low", "format": {"type": "json_schema", "schema": schema()}}
        return "/messages", body
    if p.kind == "responses":
        content = [{"type": "input_text", "text": user}]
        if img64:
            content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{img64}"})
        body = {"model": model, "instructions": system, "max_output_tokens": 8000,
                "input": [{"role": "user", "content": content}]}
        if structured:
            body["text"] = {"format": {"type": "json_schema", "name": "photocrate_answer", "schema": schema(),
                                       "strict": True}}
        return "/responses", body
    content = ([{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img64}"}}]
               if img64 else user)
    body = {"model": model, "max_tokens": 8000,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
    if structured and p.json_mode == "schema":
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "photocrate_answer", "schema": schema(), "strict": True}}
    elif structured and p.json_mode == "object":
        body["response_format"] = {"type": "json_object"}
    return "/chat/completions", body


def _text_of(p: Provider, d: dict) -> str:
    if p.kind == "anthropic":
        if d.get("stop_reason") == "refusal":
            raise LLMError("模型拒绝回答这个问题。")
        return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    if p.kind == "responses":
        if isinstance(d.get("output_text"), str):
            return d["output_text"]
        out = []
        for item in d.get("output", []):
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") == "refusal":
                        raise LLMError("模型拒绝回答这个问题。")
                    if c.get("type") in ("output_text", "text"):
                        out.append(c.get("text", ""))
        return "".join(out)
    ch = (d.get("choices") or [{}])[0].get("message") or {}
    if ch.get("refusal"):
        raise LLMError("模型拒绝回答这个问题。")
    c = ch.get("content")
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c or ""


def complete(pid: str, model: str, key: str, system: str, user: str, image: bytes | None = None,
             base: str | None = None, timeout: float = TIMEOUT) -> tuple[str, list[str]]:
    """返回 (模型输出的文字, 提示)。服务商不支持结构化输出 / 图片时自动去掉重试一次。"""
    p = BY_PID[pid]
    if not key:
        raise LLMError(f"还没有设置 {p.name} 的 API Key。")
    url0 = (base or p.base).rstrip("/")
    notes: list[str] = []
    structured = p.json_mode != "none"
    for _attempt in range(3):
        path, body = _body(p, model, system, user, image, structured)
        try:
            return _text_of(p, _http(url0 + path, _headers(p, key), body, timeout)), notes
        except LLMError as e:
            if getattr(e, "status", None) not in (400, 415, 422):
                raise
            d = (getattr(e, "detail", "") or "").lower()
            if image is not None and any(w in d for w in ("image", "vision", "multimodal", "content type")):
                image = None
                notes.append("这个模型不支持看图，这次只发了文字。")
            elif structured:
                structured = False                      # 有的模型不支持 json_schema / effort：退回提示词约束 + 本地校验
            else:
                raise
    raise LLMError("请求失败。")


def list_models(pid: str, key: str, base: str | None = None) -> list[str]:
    p = BY_PID[pid]
    if not key:
        raise LLMError(f"还没有设置 {p.name} 的 API Key。")
    d = _http((base or p.base).rstrip("/") + "/models", _headers(p, key), None, 20)
    ids = [m.get("id") for m in d.get("data", []) if isinstance(m, dict) and m.get("id")]
    return sorted(set(ids))


# ---------------------------------------------------------------------------
# 校验：模型说的只当建议，界面上的东西全部来自本地知识库
# ---------------------------------------------------------------------------

def parse_json(text: str) -> dict:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        d = json.loads(s)
    except ValueError:
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            raise LLMError("模型没有按要求返回 JSON。") from None
        try:
            d = json.loads(s[i:j + 1])
        except ValueError:
            raise LLMError("模型返回的 JSON 不完整。") from None
    if not isinstance(d, dict):
        raise LLMError("模型返回的格式不对。")
    return d


def _known_labels() -> set[str]:
    s = set()
    for f in FEATURES:
        s.add(f.title)
        s.update(x for x in f.path if x not in DESCRIPTIVE)
    s.update(v[0] for v in adjust.SLIDERS.values())
    s.update(label for _, label, _ in adjust.HSL_BANDS)
    s.update(adjust.PRESETS)
    s.update(st.name for st in effects.STYLES)
    s.update(r.name for r in effects.RECIPES)
    s.update(("HSL", "曲线", "色阶", "分离色调", "基本", "颜色", "细节", "效果", "调整图层", "属性", "图层", "蒙版",
              "历史记录", "相册", "修图", "选项栏", "工具栏"))
    return {x.rstrip("…").strip() for x in s}


_KNOWN: set[str] | None = None


def unknown_names(text: str) -> list[str]:
    """回答里用「」引用、但影匣界面上没有的名字。"""
    global _KNOWN
    if _KNOWN is None:
        _KNOWN = _known_labels()
    bad = []
    for q in re.findall(r"「([^」]{1,40})」", text or ""):
        for part in re.split(r"\s*(?:→|›|>|->|/)\s*", q):
            part = part.rstrip("…").strip()
            if not part or part in _KNOWN or any(part in k or k in part for k in _KNOWN if len(k) >= 2):
                continue
            if part not in bad:
                bad.append(part)
    return bad


def ground(d: dict, source: str, extra_notes=()) -> Answer:
    reply = str(d.get("reply") or "").strip()[:600]
    feats = []
    for fid in d.get("features") or []:
        if isinstance(fid, str) and fid in BY_ID and BY_ID[fid] not in feats:
            feats.append(BY_ID[fid])
    feats = feats[:5]
    notes = list(extra_notes)

    p = adjust.default_params()
    d0 = adjust.default_params()
    lines: list[Line] = []
    dropped = []
    for item in d.get("params") or []:
        if not isinstance(item, dict):
            continue
        key, why = item.get("key"), str(item.get("why") or "")[:120]
        try:
            v = float(item.get("value"))
        except (TypeError, ValueError):
            dropped.append(str(key))
            continue
        if key in adjust.SLIDERS:
            p[key] = _clamp(key, v)
            if abs(p[key] - float(d0[key])) > 1e-6:
                lines = [l for l in lines if l.key != key]
                lines.append(Line(key, _GROUP_OF.get(key, ""), adjust.SLIDERS[key][0], _fmt(key, p[key]), why))
        elif isinstance(key, str) and key in _HSL_KEYS:
            _, band, part = key.split(":")
            i = ("hue", "sat", "lum").index(part)
            p["hsl"][band][i] = round(max(-100.0, min(100.0, v)))
            label = dict((b, lb) for b, lb, _ in adjust.HSL_BANDS)[band]
            lines = [l for l in lines if not (l.key == "hsl:" + band and l.label.endswith(HSL_PARTS[i]))]
            if p["hsl"][band][i]:
                lines.append(Line("hsl:" + band, "HSL", f"{label} · {HSL_PARTS[i]}", f"{p['hsl'][band][i]:+.0f}", why))
        else:
            dropped.append(str(key))
    if dropped:
        notes.append(f"模型给了影匣没有的参数（{'、'.join(dropped[:4])}），已忽略。")
    plan = None
    if lines:
        order = {k: i for i, k in enumerate(list(adjust.SLIDERS) + ["hsl:" + b for b, _, _ in adjust.HSL_BANDS])}
        lines.sort(key=lambda l: order.get(l.key, 999))
        plan = Plan(f"{source.split(' · ')[0]} 的建议", p, lines, [], "", all(l.key in QUICK_KEYS for l in lines))

    steps = [str(s)[:200] for s in (d.get("steps") or []) if isinstance(s, str) and s.strip()][:8]
    recipes = [SimpleNamespace(name="做法", steps=tuple(steps))] if steps else []
    bad = unknown_names(reply + "\n" + "\n".join(steps))
    if bad:
        notes.append(f"回答里提到的「{'」「'.join(bad[:4])}」在影匣界面上没找到，请以下面列出的位置为准。")
    if not (reply or feats or plan or recipes):
        raise LLMError("模型的回答是空的。")
    return Answer("llm", reply, features=feats, plan=plan, recipes=recipes, notes=notes, source=source)


def ask(cfg: dict, key: str, question: str, local: Answer | None, stats: PhotoStats | None, mode: int,
        history=(), image: bytes | None = None) -> Answer:
    pid = cfg["provider"]
    model = model_of(cfg, pid)
    text, notes = complete(pid, model, key, system_prompt(), user_prompt(question, local, stats, mode, history,
                                                                         image is not None),
                           image, base_of(cfg, pid))
    return ground(parse_json(text), f"{BY_PID[pid].name} · {model}", notes)
