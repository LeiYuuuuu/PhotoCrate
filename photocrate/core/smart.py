"""「相册」的数据层：时间线、自动归类的智能相簿、收藏与手动相簿、一个搜索框搜所有东西。纯计算，不碰 Qt。

- 时间线：按天 / 月 / 年把照片分段（新的在前），每段带地点摘要（「东京 · 京都」）。
- 智能相簿：人物（人脸分组）、地点（城市）、旅行（自动切分的行程）、事物与场景（CLIP）、
  媒体类型（视频 / 截图 / RAW / 实况 / 全景 / 含文字 / 文档 / 收藏 / 最近添加）、我的相簿。都随照片库自动更新。
- 收藏 ♥ 和手动相簿存在 annotations.db（按图片文件指纹，移动 / 改名后依然有效），不改动照片文件。
- 搜索：一句话按空格拆成几个词，每个词在 照片里的文字 / 地点 / 人物 / 场景 / 日期与时段 / 相机 / 旅行 / 文件名 /
  类型 里找，几个词同时满足才算（“东京 拉面”= 在东京、照片里有“拉面”两个字…或场景是拉面）。
  每个词命中的类别给出“建议”，点一个就只按那一类找。AI 画面搜索（CLIP）由界面在后台另外补上。
"""
from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from ..ai.ocr import full_text, norm
from ..i18n import is_en, t as _t

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
SCENE_MIN_PROB = 0.25         # 场景把握低于这个不归类（与分区维度「场景」的默认值一致）
TEXT_MIN_CHARS = 2            # 含文字：至少识别出这么多个字
DOC_MIN_CHARS = 40            # 文档：文字很多
PANORAMA_RATIO = 2.2


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def visible(groups: Iterable) -> list:
    return [g for g in groups if g.files]


def sort_key(g) -> tuple:
    d = g.date
    return (d is not None, d or datetime.min, g.gid)


def newest_first(groups: Iterable) -> list:
    return sorted(groups, key=sort_key, reverse=True)


def place_of(g) -> str | None:
    """照片的“城市级”地名（中文原文）：城市 → 省 / 州 → 国家。"""
    geo = g.feats.get("geo") or {}
    for k in ("city", "province", "country"):
        v = geo.get(k)
        if v:
            return v
    return None


def places_summary(groups: Iterable, n: int = 3) -> str:
    c = Counter(p for p in (place_of(g) for g in groups) if p)
    return " · ".join(_t(p) for p, _ in c.most_common(n))


def day_title(d: date) -> str:
    if is_en():
        return f"{d:%a}, {d:%b} {d.day}, {d.year}"
    return f"{d.year}年{d.month}月{d.day}日 {WEEKDAYS[d.weekday()]}"


def month_title(y: int, m: int) -> str:
    return date(y, m, 1).strftime("%B %Y") if is_en() else f"{y}年{m}月"


def year_title(y: int) -> str:
    return str(y) if is_en() else f"{y}年"


def ocr_lines(g) -> list[dict]:
    return g.feats.get("ocr") or []


def ocr_chars(g) -> int:
    return sum(len(l.get("t", "")) for l in ocr_lines(g))


def is_live(g) -> bool:
    return any(f.role == "live" for f in g.files)


def is_screenshot(g) -> bool:
    return g.feat("camera", "source") == "screenshot"


def is_panorama(g) -> bool:
    pv = g.feats.get("preview") or {}
    w, h = pv.get("w") or 0, pv.get("h") or 0
    return bool(w and h) and max(w, h) / max(1, min(w, h)) >= PANORAMA_RATIO


def scene_of(g) -> str | None:
    sc = g.feats.get("scene") or {}
    if float(sc.get("prob") or 0) < SCENE_MIN_PROB:
        return None
    return sc.get("scene")


# ---------------------------------------------------------------------------
# 时间线
# ---------------------------------------------------------------------------

@dataclass
class Section:
    key: str
    title: str
    sub: str
    gids: list[int]
    cover: int | None = None        # 卡片封面（年 / 月视图、相簿）


def timeline(groups: Iterable, level: str = "day") -> list[Section]:
    """level：day（每天一段）/ month / year（每段一张卡片）/ all（按月分段的连续网格）。新的在前；没有日期的放最后。"""
    gs = newest_first(visible(groups))
    buckets: dict[Any, list] = {}
    undated: list = []
    for g in gs:
        d = g.date
        if d is None:
            undated.append(g)
            continue
        k = d.year if level == "year" else ((d.year, d.month) if level in ("month", "all") else d.date())
        buckets.setdefault(k, []).append(g)
    out = []
    for k, items in buckets.items():
        if level == "year":
            title, key = year_title(k), f"y{k}"
        elif level in ("month", "all"):
            title, key = month_title(*k), f"m{k[0]}-{k[1]:02d}"
        else:
            title, key = day_title(k), f"d{k.isoformat()}"
        sub = places_summary(items)
        out.append(Section(key, title, sub, [g.gid for g in items], cover=_cover(items)))
    if undated:
        out.append(Section("undated", _t("没有拍摄日期"), "", [g.gid for g in undated], cover=_cover(undated)))
    return out


def _cover(items: list) -> int | None:
    """封面：优先挑打了星 / 收藏的、清晰的照片，其次中间那张。"""
    if not items:
        return None

    def score(g):
        r = g.feats.get("rating") or {}
        sharp = float(g.feat("blur", "score") or 0)
        return (int(r.get("rating") or 0) + (3 if r.get("flag") == "pick" else 0), not g.is_video, sharp)
    best = max(items, key=score)
    if score(best)[0] > 0:
        return best.gid
    vid = [g for g in items if not g.is_video] or items
    return vid[len(vid) // 2].gid


# ---------------------------------------------------------------------------
# 收藏与手动相簿
# ---------------------------------------------------------------------------

@dataclass
class UserAlbum:
    aid: str
    name: str
    created: str
    order: int = 0


class Marks:
    """收藏 ♥ 与手动相簿。数据在 annotations.db，按照片的图片文件指纹保存。"""

    def __init__(self, store):
        self.store = store
        self.reload()

    def reload(self) -> None:
        self.fav: set[str] = set(self.store.all("favorite"))
        self.items: dict[str, list[str]] = {s: list(d.get("ids") or []) for s, d in self.store.all("album_items").items()}
        self.albums: dict[str, UserAlbum] = {}
        for aid, d in self.store.all("albums").items():
            self.albums[aid] = UserAlbum(aid, d.get("name") or _t("未命名相簿"), d.get("created") or "", int(d.get("order") or 0))

    # ---- 收藏 ----
    def is_fav(self, g) -> bool:
        return any(s in self.fav for s in g.member_sigs)

    def set_fav(self, groups: list, on: bool) -> int:
        sigs = [s for g in groups if g.files for s in g.member_sigs]
        if on:
            self.store.set_many({s: {"fav": True} for s in sigs}, "favorite")
            self.fav.update(sigs)
        else:
            self.store.clear_many(sigs, "favorite")
            self.fav.difference_update(sigs)
        return len(groups)

    def toggle_fav(self, groups: list) -> bool:
        """有一张没收藏 → 全部收藏；全都收藏了 → 全部取消。返回收藏后的状态。"""
        on = not all(self.is_fav(g) for g in groups)
        self.set_fav(groups, on)
        return on

    # ---- 相簿 ----
    def album_list(self) -> list[UserAlbum]:
        return sorted(self.albums.values(), key=lambda a: (a.order, a.created, a.name))

    def create(self, name: str) -> str:
        aid = "a" + uuid.uuid4().hex[:10]
        now = datetime.now().isoformat(timespec="seconds")
        a = UserAlbum(aid, name.strip() or _t("未命名相簿"), now, len(self.albums))
        self.store.set_many({aid: {"name": a.name, "created": now, "order": a.order}}, "albums")
        self.albums[aid] = a
        return aid

    def rename(self, aid: str, name: str) -> None:
        a = self.albums.get(aid)
        if a is None or not name.strip():
            return
        a.name = name.strip()
        self.store.set_many({aid: {"name": a.name, "created": a.created, "order": a.order}}, "albums")

    def delete(self, aid: str) -> None:
        """删除相簿（照片本身不动）。"""
        self.albums.pop(aid, None)
        self.store.clear_many([aid], "albums")
        changed = {}
        for s, ids in self.items.items():
            if aid in ids:
                ids.remove(aid)
                changed[s] = ids
        self._save_items(changed)

    def _save_items(self, changed: dict[str, list[str]]) -> None:
        keep = {s: {"ids": ids} for s, ids in changed.items() if ids}
        drop = [s for s, ids in changed.items() if not ids]
        if keep:
            self.store.set_many(keep, "album_items")
        if drop:
            self.store.clear_many(drop, "album_items")
            for s in drop:
                self.items.pop(s, None)

    def add(self, groups: list, aid: str) -> int:
        changed = {}
        n = 0
        for g in groups:
            if not g.files or self.in_album(g, aid):
                continue
            s = g.member_sigs[0]
            ids = self.items.setdefault(s, [])
            ids.append(aid)
            changed[s] = ids
            n += 1
        self._save_items(changed)
        return n

    def remove(self, groups: list, aid: str) -> int:
        changed = {}
        n = 0
        for g in groups:
            hit = False
            for s in g.member_sigs:
                ids = self.items.get(s)
                if ids and aid in ids:
                    ids.remove(aid)
                    changed[s] = ids
                    hit = True
            n += hit
        self._save_items(changed)
        return n

    def in_album(self, g, aid: str) -> bool:
        return any(aid in self.items.get(s, ()) for s in g.member_sigs)

    def albums_of(self, g) -> list[str]:
        out: list[str] = []
        for s in g.member_sigs:
            for a in self.items.get(s, ()):
                if a not in out and a in self.albums:
                    out.append(a)
        return out

    def album_gids(self, groups: Iterable, aid: str) -> list[int]:
        return [g.gid for g in newest_first(visible(groups)) if self.in_album(g, aid)]


# ---------------------------------------------------------------------------
# 智能相簿
# ---------------------------------------------------------------------------

@dataclass
class Album:
    key: str                  # people:<cid> / place:<名> / trip:<key> / scene:<名> / media:<类型> / user:<aid>
    title: str
    sub: str
    gids: list[int]
    cover: int | None = None
    icon: str = ""            # 媒体类型相簿的小图标（文字符号）


@dataclass
class AlbumShelf:
    key: str
    title: str
    en: str
    albums: list[Album] = field(default_factory=list)


MEDIA_KINDS = [
    # (键, 名称, 图标)
    ("favorite", "收藏", "♥"),
    ("recent", "最近添加", "◷"),
    ("video", "视频", "▶"),
    ("live", "实况照片", "◎"),
    ("screenshot", "截图", "▣"),
    ("text", "含文字", "文"),
    ("document", "文档与票据", "≣"),
    ("raw", "RAW", "R"),
    ("panorama", "全景", "⇿"),
    ("picks", "精选（4 星以上 / 留用）", "★"),
]


def media_filter(kind: str, marks: Marks | None):
    if kind == "favorite":
        return lambda g: marks is not None and marks.is_fav(g)
    if kind == "video":
        return lambda g: g.is_video
    if kind == "live":
        return is_live
    if kind == "screenshot":
        return is_screenshot
    if kind == "text":
        return lambda g: ocr_chars(g) >= TEXT_MIN_CHARS
    if kind == "document":
        return lambda g: ocr_chars(g) >= DOC_MIN_CHARS or scene_of(g) == "文档截图"
    if kind == "raw":
        return lambda g: g.has_raw
    if kind == "panorama":
        return is_panorama
    if kind == "picks":
        def f(g):
            r = g.feats.get("rating") or {}
            return int(r.get("rating") or 0) >= 4 or r.get("flag") == "pick"
        return f
    raise KeyError(kind)


def recent_added(groups: Iterable, n: int = 300) -> list:
    """最近添加：按文件修改时间（导入 / 拷贝进来的时间）最新的 n 张。"""
    return sorted(visible(groups), key=lambda g: g.primary_file.mtime_ns, reverse=True)[:n]


def shelves(lib, marks: Marks | None) -> list[AlbumShelf]:
    groups = visible(lib.groups)
    by_gid = {g.gid: g for g in groups}
    out: list[AlbumShelf] = []

    # 媒体类型
    media = AlbumShelf("media", _t("媒体类型与收藏"), "MEDIA")
    for key, name, icon in MEDIA_KINDS:
        if key == "recent":
            gs = recent_added(groups)
        else:
            f = media_filter(key, marks)
            gs = newest_first(g for g in groups if f(g))
        if gs or key == "favorite":
            media.albums.append(Album(f"media:{key}", _t(name), "", [g.gid for g in gs], _cover(gs), icon))
    out.append(media)

    # 我的相簿
    if marks is not None:
        mine = AlbumShelf("user", _t("我的相簿"), "MY ALBUMS")
        for a in marks.album_list():
            gids = marks.album_gids(groups, a.aid)
            mine.albums.append(Album(f"user:{a.aid}", a.name, "", gids, _cover([by_gid[i] for i in gids]), "▤"))
        out.append(mine)

    # 人物
    people = AlbumShelf("people", _t("人物"), "PEOPLE")
    clusters = list(getattr(lib.ai, "people", []) or [])
    clusters.sort(key=lambda c: (not c.named, -len(c.gids)))
    for c in clusters:
        gids = [i for i in c.gids if i in by_gid]
        if not gids or (not c.named and len(gids) < 3):
            continue
        cover = c.cover.gid if c.cover is not None and c.cover.gid in by_gid else gids[0]
        gs = newest_first(by_gid[i] for i in gids)
        people.albums.append(Album(f"people:{c.cid}", c.name if c.named else _t("未命名"), "",
                                   [g.gid for g in gs], cover))
    if people.albums:
        out.append(people)

    # 地点
    places = AlbumShelf("places", _t("地点"), "PLACES")
    pl: dict[str, list] = defaultdict(list)
    country: dict[str, Counter] = defaultdict(Counter)
    for g in groups:
        p = place_of(g)
        if p:
            pl[p].append(g)
            c = (g.feats.get("geo") or {}).get("country")
            if c and c != p:
                country[p][c] += 1
    for p, gs in sorted(pl.items(), key=lambda kv: -len(kv[1])):
        gs = newest_first(gs)
        sub = _t(country[p].most_common(1)[0][0]) if country[p] else ""
        places.albums.append(Album(f"place:{p}", _t(p), sub, [g.gid for g in gs], _cover(gs)))
    if places.albums:
        out.append(places)

    # 旅行
    trips = AlbumShelf("trips", _t("旅行"), "TRIPS")
    for tr in sorted(getattr(lib, "trips", []) or [], key=lambda t: t.start, reverse=True):
        gs = newest_first(by_gid[i] for i in tr.gids if i in by_gid)
        if gs:
            trips.albums.append(Album(f"trip:{tr.key}", tr.name, tr.span_text, [g.gid for g in gs], _cover(gs)))
    if trips.albums:
        out.append(trips)

    # 事物与场景
    scenes = AlbumShelf("scenes", _t("事物与场景"), "THINGS & SCENES")
    sc: dict[str, list] = defaultdict(list)
    for g in groups:
        s = scene_of(g)
        if s:
            sc[s].append(g)
    for s, gs in sorted(sc.items(), key=lambda kv: -len(kv[1])):
        gs = sorted(gs, key=lambda g: -float((g.feats.get("scene") or {}).get("prob") or 0))
        scenes.albums.append(Album(f"scene:{s}", _t(s), "", [g.gid for g in newest_first(gs)], gs[0].gid))
    if scenes.albums:
        out.append(scenes)
    return out


def album_by_key(lib, marks: Marks | None, key: str) -> Album | None:
    for sh in shelves(lib, marks):
        for a in sh.albums:
            if a.key == key:
                return a
    return None


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------

CATEGORIES = {
    "text": "照片里的文字",
    "place": "地点",
    "person": "人物",
    "scene": "场景",
    "date": "日期 / 时段",
    "trip": "旅行",
    "camera": "相机 / 镜头",
    "kind": "类型",
    "album": "相簿",
    "name": "文件名",
}
CAT_ORDER = ["date", "place", "person", "trip", "album", "kind", "scene", "text", "camera", "name"]

KIND_WORDS: dict[str, list[str]] = {
    "video": ["视频", "录像", "video", "videos"],
    "screenshot": ["截图", "截屏", "screenshot", "screenshots"],
    "live": ["实况", "实况照片", "live", "livephoto"],
    "raw": ["raw"],
    "favorite": ["收藏", "喜欢", "我喜欢", "♥", "favorite", "favorites", "favourite"],
    "panorama": ["全景", "panorama"],
    "text": ["文字", "含文字", "有字"],
    "document": ["文档", "票据", "收据", "小票", "发票", "document", "receipt"],
    "picks": ["精选", "留用", "picks"],
}

HOURS: dict[str, tuple[int, int]] = {
    "凌晨": (0, 5), "深夜": (0, 4), "清晨": (5, 8), "早上": (5, 9), "早晨": (5, 9), "上午": (8, 12),
    "中午": (11, 14), "下午": (13, 18), "傍晚": (17, 20), "黄昏": (17, 20), "晚上": (18, 24), "夜里": (19, 24),
    "夜晚": (19, 24), "morning": (5, 12), "afternoon": (12, 18), "evening": (17, 21), "night": (19, 24),
}
SEASONS: dict[str, tuple[int, ...]] = {
    "春天": (3, 4, 5), "春季": (3, 4, 5), "夏天": (6, 7, 8), "夏季": (6, 7, 8), "秋天": (9, 10, 11), "秋季": (9, 10, 11),
    "冬天": (12, 1, 2), "冬季": (12, 1, 2), "spring": (3, 4, 5), "summer": (6, 7, 8), "autumn": (9, 10, 11),
    "fall": (9, 10, 11), "winter": (12, 1, 2),
}
CN_MONTH = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
EN_MONTH = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_DATE_RE = re.compile(r"^(?P<y>(?:19|20)\d{2})(?:[年./-](?P<m>\d{1,2})(?:[月./-](?P<d>\d{1,2})日?)?月?)?年?$")
_MD_RE = re.compile(r"^(?P<m>\d{1,2})月(?:(?P<d>\d{1,2})[日号])?$")
_CNM_RE = re.compile(r"^(?P<m>十[一二]?|[一二三四五六七八九])月$")


def date_matcher(token: str, today: date | None = None):
    """把一个词理解成日期条件。返回 fn(datetime) -> bool；不是日期词返回 None。"""
    today = today or date.today()
    t = token.strip().lower()
    m = _DATE_RE.match(t)
    if m:
        y, mo, d = int(m["y"]), m["m"], m["d"]
        if mo and d:
            return lambda dt: dt.year == y and dt.month == int(mo) and dt.day == int(d)
        if mo:
            return lambda dt: dt.year == y and dt.month == int(mo)
        return lambda dt: dt.year == y
    m = _MD_RE.match(t)
    if m and 1 <= int(m["m"]) <= 12:
        mo, d = int(m["m"]), m["d"]
        return (lambda dt: dt.month == mo and dt.day == int(d)) if d else (lambda dt: dt.month == mo)
    m = _CNM_RE.match(t)
    if m:
        mo = CN_MONTH[m["m"]]
        return lambda dt: dt.month == mo
    if t[:3] in EN_MONTH and t in ("january", "february", "march", "april", "may", "june", "july", "august",
                                   "september", "october", "november", "december", t[:3]):
        mo = EN_MONTH[t[:3]]
        return lambda dt: dt.month == mo
    rel = {"今天": 0, "today": 0, "昨天": 1, "yesterday": 1, "前天": 2}
    if t in rel:
        day = today - timedelta(days=rel[t])
        return lambda dt: dt.date() == day
    if t in ("今年", "this year"):
        return lambda dt: dt.year == today.year
    if t in ("去年", "last year"):
        return lambda dt: dt.year == today.year - 1
    if t in ("前年",):
        return lambda dt: dt.year == today.year - 2
    if t in ("这个月", "本月", "this month"):
        return lambda dt: (dt.year, dt.month) == (today.year, today.month)
    if t in ("上个月", "上月", "last month"):
        first = today.replace(day=1) - timedelta(days=1)
        return lambda dt: (dt.year, dt.month) == (first.year, first.month)
    if t in ("最近", "最近一周", "这周", "本周", "recent", "this week"):
        start = today - timedelta(days=7)
        return lambda dt: dt.date() >= start
    if t in ("周末", "weekend"):
        return lambda dt: dt.weekday() >= 5
    if t in SEASONS:
        months = SEASONS[t]
        return lambda dt: dt.month in months
    if t in HOURS:
        lo, hi = HOURS[t]
        return lambda dt: lo <= dt.hour < hi
    return None


@dataclass
class Suggestion:
    token: int                # 第几个词
    cat: str
    label: str                # 显示文字（如 地点「东京」）
    count: int


@dataclass
class SearchResult:
    query: str
    tokens: list[str]
    gids: list[int]                                   # 全部词都满足的照片，新的在前
    suggestions: list[Suggestion] = field(default_factory=list)
    text_hits: dict[int, list[str]] = field(default_factory=dict)    # gid → 在照片文字里命中的词（归一化）
    by_cat: dict[tuple[int, str], set[int]] = field(default_factory=dict)


def tokenize(query: str) -> list[str]:
    q = (query or "").replace("，", " ").replace(",", " ").replace("、", " ").strip()
    out = []
    for m in re.finditer(r'"([^"]+)"|“([^”]+)”|(\S+)', q):
        tok = next(x for x in m.groups() if x)
        if tok and tok not in out:
            out.append(tok)
    return out


class SearchIndex:
    """为一次搜索准备好的文字（每张照片的各类可搜索文字，已归一化）。照片库变了重新建。"""

    def __init__(self, lib, marks: Marks | None):
        self.lib = lib
        self.marks = marks
        self.groups = visible(lib.groups)
        self.text: dict[int, str] = {}
        self.place: dict[int, str] = {}
        self.camera: dict[int, str] = {}
        self.trip: dict[int, str] = {}
        self.scene: dict[int, str] = {}
        self.name: dict[int, str] = {}
        self.album: dict[int, str] = {}
        self.person: dict[int, set[str]] = defaultdict(set)
        self.person_names: dict[str, set[int]] = defaultdict(set)
        from ..ai.scenes import SCENES
        scene_words = {name: [name, grp, *prompts] for name, grp, prompts in SCENES}
        for g in self.groups:
            i = g.gid
            lines = ocr_lines(g)
            if lines:
                self.text[i] = norm(full_text(lines).replace("\n", ""))
            geo = g.feats.get("geo") or {}
            parts = [v for k in ("country", "province", "city", "district") if (v := geo.get(k))]
            parts += [_t(v) for v in parts]
            if parts:
                self.place[i] = norm(" ".join(parts))
            cam = g.feats.get("camera") or {}
            c = " ".join(str(v) for v in (cam.get("make"), cam.get("model"), cam.get("lens")) if v)
            if c:
                self.camera[i] = norm(c)
            tr = g.feats.get("trip") or {}
            if tr.get("name"):
                self.trip[i] = norm(tr["name"])
            s = scene_of(g)
            if s:
                words = scene_words.get(s, [s])
                self.scene[i] = norm(" ".join(words + [_t(w) for w in words]))
            self.name[i] = norm(" ".join(f.name for f in g.image_files) or g.name)
            if marks is not None:
                names = [marks.albums[a].name for a in marks.albums_of(g)]
                if names:
                    self.album[i] = norm(" ".join(names))
        for c in getattr(lib.ai, "people", []) or []:
            if c.named:
                for gid in c.gids:
                    self.person[gid].add(norm(c.name))
                    self.person_names[norm(c.name)].add(gid)

    def match(self, token: str) -> dict[str, set[int]]:
        """一个词在各类别里命中的照片。"""
        q = norm(token)
        out: dict[str, set[int]] = {}
        if not q:
            return out

        def sub(table: dict[int, str]) -> set[int]:
            return {i for i, s in table.items() if q in s}

        dm = date_matcher(token)
        if dm is not None:
            out["date"] = {g.gid for g in self.groups if g.date is not None and dm(g.date)}
        for cat, table in (("place", self.place), ("trip", self.trip), ("album", self.album), ("scene", self.scene),
                           ("text", self.text), ("camera", self.camera)):
            hit = sub(table)
            if hit:
                out[cat] = hit
        people = set()
        for name, gids in self.person_names.items():
            if q in name:
                people |= gids
        if people:
            out["person"] = people
        for kind, words in KIND_WORDS.items():
            if q in (norm(w) for w in words):
                f = media_filter(kind, self.marks)
                out["kind"] = {g.gid for g in self.groups if f(g)}
                break
        if len(q) >= 3 or not out:                       # 短词不搜文件名（“东”会命中一堆 DSC…）
            hit = sub(self.name)
            if hit:
                out["name"] = hit
        return {k: v for k, v in out.items() if v}


def search(lib, marks: Marks | None, query: str, restrict: dict[int, str] | None = None,
           index: SearchIndex | None = None) -> SearchResult:
    """restrict：{第几个词: 类别}，只按这一类匹配那个词（用户点了某条建议）。"""
    tokens = tokenize(query)
    res = SearchResult(query, tokens, [])
    if not tokens:
        return res
    idx = index or SearchIndex(lib, marks)
    restrict = restrict or {}
    combined: set[int] | None = None
    for ti, tok in enumerate(tokens):
        cats = idx.match(tok)
        for cat, gids in cats.items():
            res.by_cat[(ti, cat)] = gids
            res.suggestions.append(Suggestion(ti, cat, _suggest_label(cat, tok), len(gids)))
        if ti in restrict and restrict[ti] in cats:
            hit = cats[restrict[ti]]
        else:
            hit = set().union(*cats.values()) if cats else set()
        combined = hit if combined is None else combined & hit
        for gid in cats.get("text", ()):
            if ti not in restrict or restrict[ti] == "text":
                res.text_hits.setdefault(gid, []).append(norm(tok))
    if len(tokens) > 1:                                  # 整句（去掉空格）也在照片文字里找一遍
        whole = norm("".join(tokens))
        extra = {i for i, s in idx.text.items() if whole in s}
        for gid in extra:
            res.text_hits.setdefault(gid, []).append(whole)
        combined = (combined or set()) | extra
    by_gid = {g.gid: g for g in idx.groups}
    res.gids = [g.gid for g in newest_first(by_gid[i] for i in (combined or set()) if i in by_gid)]
    res.text_hits = {k: v for k, v in res.text_hits.items() if k in set(res.gids)}
    res.suggestions.sort(key=lambda s: (s.token, CAT_ORDER.index(s.cat) if s.cat in CAT_ORDER else 99, -s.count))
    return res


def _suggest_label(cat: str, token: str) -> str:
    return _t("{0}「{1}」", _t(CATEGORIES.get(cat, cat)), token)


def matched_lines(g, needles: list[str]) -> list[int]:
    """照片文字里包含任一搜索词的行号（检测框可以高亮）。一个词跨了两行时，两行都算。"""
    lines = ocr_lines(g)
    if not lines or not needles:
        return []
    normed = [norm(l.get("t", "")) for l in lines]
    hit: list[int] = []
    for n in needles:
        found = [i for i, s in enumerate(normed) if n in s]
        if not found:
            for i in range(len(normed) - 1):
                if n in normed[i] + normed[i + 1]:
                    found += [i, i + 1]
        for i in found:
            if i not in hit:
                hit.append(i)
    return sorted(hit)
