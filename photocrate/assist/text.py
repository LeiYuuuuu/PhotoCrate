"""问题文字的规整与词语匹配（中文不分词：词典里的词直接在原句里找，长词优先）。"""
from __future__ import annotations

import re
import unicodedata

# 只是语气 / 问法，不参与找功能
FILLERS = ("请问", "请", "帮我", "帮忙", "麻烦", "我想要", "我想", "我要", "想要", "怎么样", "怎么", "怎样", "如何",
           "在哪里", "在哪儿", "在哪", "哪里", "哪儿", "可以", "能不能", "能", "一下", "吗", "呢", "啊", "吧", "呀",
           "的", "了", "把", "给", "让", "使", "得", "这张", "照片", "图片", "这个", "功能")

_PUNCT = re.compile(r"[\s,;:!?，。；：！？、…·'\"“”‘’（）()【】\[\]<>《》~～/\\|]+|\.(?!\d)|(?<!\d)\.")


def normalize(s: str) -> str:
    """全角转半角、英文小写、标点换成空格（小数点保留）。"""
    s = unicodedata.normalize("NFKC", s or "").lower()
    return _PUNCT.sub(" ", s).strip()


def find_terms(text: str, terms) -> list[tuple[int, int, str]]:
    """在 text 里找 terms 中的词，返回不重叠的 (起点, 终点, 词)，按出现位置排序。
    同一处被几个词覆盖时取最长的（“天空更蓝”优先于“蓝”）。"""
    hits = []
    for t in sorted(set(terms), key=len, reverse=True):
        if not t:
            continue
        start = 0
        while True:
            i = text.find(t, start)
            if i < 0:
                break
            hits.append((i, i + len(t), t))
            start = i + 1
    taken: list[tuple[int, int]] = []
    out = []
    for i, j, t in sorted(hits, key=lambda h: (-(h[1] - h[0]), h[0])):
        if any(i < b and a < j for a, b in taken):
            continue
        taken.append((i, j))
        out.append((i, j, t))
    return sorted(out)


def strip_fillers(text: str) -> str:
    for w in sorted(FILLERS, key=len, reverse=True):
        text = text.replace(w, " ")
    return re.sub(r"\s+", " ", text).strip()


def bigrams(text: str) -> set[str]:
    s = text.replace(" ", "")
    return {s[i:i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def similarity(a: str, b: str) -> float:
    """两段话的相似度（二字组的 Dice 系数，0–1）。只用来给“没听懂”时推荐最接近的条目。"""
    x, y = bigrams(a), bigrams(b)
    if not x or not y:
        return 0.0
    return 2 * len(x & y) / (len(x) + len(y))
