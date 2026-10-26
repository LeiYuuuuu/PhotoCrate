"""连拍里的闭眼 / 表情检查：同一组连拍里，每个人只和他自己在其他几张里比——
别的几张睁着眼、这张闭着，就标“闭眼”；这张在哭、皱眉、做鬼脸、生气，而别的几张没有，就标“表情不佳”。
没在笑不算表情不佳（严肃、平静都是正常表情），只在挑“最佳”时让在笑的那张优先。
每组的“最佳”优先挑没人闭眼、表情好、又够清晰的那张，其余的“建议待确认”也就跟着变。

- 人脸：在长边 1600 的预览上用 YuNet 重新检测（要五官关键点对齐；扫描时缓存的人脸没有关键点），
  按 ArcFace 的五点模板对齐成 224×224（看眼睛）和 112×112（表情模型的输入）。
- 闭眼分 = 中文 CLIP 看整张脸“闭着眼 / 睁着眼”的把握 − 像素上的睁眼程度（眼睛中心那几列里穿过眼睛中线的
  连续暗像素有多高 ÷ 两眼间距：睁眼是虹膜 + 瞳孔约 0.10–0.25，闭眼只剩睫毛线）。单看像素分不开浓睫毛的闭眼
  和戴美瞳的睁眼，CLIP 偶尔把眯眼看成闭眼，两个合起来最稳（在约 300 张真实人脸上人工标注检验过）。
  再和同一个人在同一组里最“睁眼”的那张比：分够高、又比它高出一截才算闭眼；只出现在一张里的人不判断。
  没有下载 CLIP 模型时不判断闭眼（只看像素误报太多）。
- 表情：OpenCV Zoo 的 MobileFaceNet 表情识别（7 类，4.8 MB，可选下载）。它常把严肃、低头、闭眼的脸判成“难过”
  （本机约 260 张没有一张真的表情不好的人脸里，“负面 ≥ 0.5”误报 19 张，全来自“难过”；生气 / 嫌弃 / 害怕最高只到 0.17）。
  所以“难过”单独不算数，要中文 CLIP 也看出“在哭 / 皱眉 / 做鬼脸”才算（两者同时误报 0 张）；生气 / 嫌弃 / 害怕把握够高可以单独算。
  用 30 张公开授权的图（Wikimedia Commons）检验：16 张真·表情不好里标出 11 张，7 张平静 / 严肃的 0 误报；
  漏掉的是闭眼大哭（会被标成闭眼）、皱纹很深的老人皱眉、露齿的鬼脸（模型看成在笑）。
  同样只和自己在别的几张里比，闭着眼的那张不判断表情。
- 结果按主图指纹缓存在 cache.db（借用 ocr 表按版本键存 JSON 的通用存储，键 FACEQ_KEY），移动 / 改名后依然命中。
"""
from __future__ import annotations

import logging
import math
import threading
from collections import defaultdict
from typing import Callable

import numpy as np

from . import models
from ..i18n import t as _t

log = logging.getLogger(__name__)

FACEQ_KEY = "faceq-v2"        # v2：多存 CLIP 的“哭 / 皱眉 / 鬼脸”把握（pn）
PREVIEW_SIDE = 1600
MIN_FACE = 48                 # 预览里比这小的脸不看（像素太少，量不准）
CLOSED_SCORE = 0.70           # 闭眼分至少这么高……
CLOSED_MARGIN = 0.15          # ……而且比同组里自己最低的一张高出这么多，才算闭眼
DEFAULT_OPEN = 0.15           # 量不出睁眼程度（墨镜、过暗）时按“正常睁着”算，只看 CLIP
BAD_SCORE = 0.5               # 表情不佳分至少这么高……
BAD_MARGIN = 0.3              # ……而且比同组里自己最好的一张高出这么多，才算表情不佳
SMILE_HI, SMILE_LO = 0.7, 0.3  # 别的几张笑的把握 ≥ 0.7、这张 < 0.3：“没在笑”（不标，只影响挑最佳）

TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                     [41.5493, 92.3655], [70.7299, 92.2041]], np.float32)       # 112×112 上的五个点
EXPR_FILE = "facial_expression_recognition_mobilefacenet_2022july.onnx"
EXPR_LABELS = ["angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised"]
EXPR_NAMES = {"angry": "生气", "disgust": "嫌弃", "fearful": "害怕", "happy": "开心", "neutral": "平静",
              "sad": "难过", "surprised": "惊讶"}
BAD = (0, 1, 2, 5)
STRONG = (0, 1, 2)            # 生气 / 嫌弃 / 害怕：在平静的脸上几乎不会误报
CLOSED_PROMPTS = ["一个闭着眼睛的人", "闭上眼睛的人像照片", "眨眼的瞬间，眼睛闭上了", "a person with closed eyes"]
OPEN_PROMPTS = ["一个睁着眼睛的人", "睁大眼睛看着前方的人像照片", "眼睛睁开的人", "a person with open eyes"]
# “哭 / 皱眉 / 鬼脸”对“平静 / 严肃 / 低头 / 闭眼 / 浓妆”：中英各一套合在一起。比较过几组描述：本机 263 张 + 公开图 7 张正常表情上
# 0 误报的前提下，这一组在 16 张公开的真·表情不好（哭、皱眉、做鬼脸、生气喊叫）里抓到 11 张（只用中文的抓 12 张但有 2 张误报）
NEG_PROMPTS = ["一个正在哭的人", "一个哭泣的婴儿", "一个皱着眉头、愁眉苦脸的人", "一个表情痛苦的人", "一个生气地大喊大叫的人",
               "一个龇牙咧嘴、表情扭曲的人", "一个满脸不高兴的人",
               "a person crying", "a crying baby", "a person frowning unhappily", "a person in pain",
               "a person shouting angrily", "a person grimacing", "an upset, unhappy person"]
CALM_PROMPTS = ["一个表情平静的人", "一个表情严肃的人", "一个面无表情的人", "一个微笑的人", "一个低着头的人",
                "一个闭着眼睛的人", "一个化着浓妆的人",
                "a person with a calm face", "a person with a serious face", "an expressionless face", "a smiling person",
                "a person looking down", "a person with eyes closed", "a person wearing heavy makeup"]


# ---------------------------------------------------------------------------
# 单张脸
# ---------------------------------------------------------------------------

def similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """最小二乘的相似变换（旋转 + 等比缩放 + 平移，Umeyama），2×3。"""
    src = np.asarray(src, np.float64)
    dst = np.asarray(dst, np.float64)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s, d = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(d.T @ s / len(src))
    D = np.eye(2)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        D[1, 1] = -1
    R = U @ D @ Vt
    var = (s ** 2).sum() / len(src)
    c = float((S * np.diag(D)).sum() / max(var, 1e-12))
    t = mu_d - c * R @ mu_s
    return np.hstack([c * R, t[:, None]]).astype(np.float32)


def align(img: np.ndarray, landmarks: np.ndarray, size: int) -> np.ndarray:
    """按五个关键点把脸对齐成 size×size（与 SFace / 表情模型用的模板一致）。"""
    import cv2
    M = similarity_transform(landmarks, TEMPLATE * (size / 112.0))
    return cv2.warpAffine(img, M, (size, size), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _dark_run(col: np.ndarray, lo: int, hi: int) -> int:
    """一列里穿过 [lo, hi) 的最长连续 True 段的长度。"""
    best = 0
    n = len(col)
    y = 0
    while y < n:
        if not col[y]:
            y += 1
            continue
        a = y
        while y < n and col[y]:
            y += 1
        if a < hi and y > lo:
            best = max(best, y - a)
    return best


def eye_openness(gray: np.ndarray) -> list[float | None]:
    """对齐好的脸（灰度，正方形，任意边长）→ [右眼, 左眼]（画面左边、右边）的睁眼程度（÷ 两眼间距）。
    眼睛那块几乎没有明暗（墨镜、过暗、糊成一片）时给 None。"""
    import cv2
    s = gray.shape[0] / 112.0
    ied = float(TEMPLATE[1, 0] - TEMPLATE[0, 0]) * s
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), max(0.6, 0.012 * ied))
    out: list[float | None] = []
    for cx, cy in TEMPLATE[:2] * s:
        x0, x1 = int(round(cx - 0.34 * ied)), int(round(cx + 0.34 * ied))
        y0, y1 = int(round(cy - 0.26 * ied)), int(round(cy + 0.26 * ied))
        roi = g[max(0, y0):y1, max(0, x0):x1]
        if roi.size < 16:
            out.append(None)
            continue
        ref = float(np.percentile(roi, 80))          # 皮肤 / 眼白
        dark = float(np.percentile(roi, 2))          # 瞳孔 / 睫毛
        if ref - dark < 14:
            out.append(None)
            continue
        mask = roi < dark + 0.42 * (ref - dark)
        h, w = mask.shape
        band_lo, band_hi = int(h / 2 - 0.1 * ied), int(math.ceil(h / 2 + 0.1 * ied))
        c0, c1 = int(w / 2 - 0.2 * ied), int(math.ceil(w / 2 + 0.2 * ied))
        runs = [_dark_run(mask[:, x], band_lo, band_hi) for x in range(max(0, c0), min(w, c1))]
        out.append(round(float(np.percentile(runs, 75)) / ied, 4) if runs else None)
    return out


def openness(eo) -> float | None:
    v = [x for x in (eo or []) if x is not None]
    return float(np.mean(v)) if v else None


def closed_score(f: dict) -> float | None:
    """闭眼分（越高越像闭着眼）；没有 CLIP 的判断时 None。"""
    pc = f.get("pc")
    if pc is None:
        return None
    o = openness(f.get("eo"))
    return float(pc) - (DEFAULT_OPEN if o is None else o)


def expr_score(p) -> tuple[float, float]:
    """(好的程度, 负面表情的把握)。开心最好，平静也不错，惊讶一般。"""
    if p is None or len(p) < 7:
        return 0.0, 0.0
    good = p[3] + 0.7 * p[4] + 0.4 * p[6]
    bad = sum(p[i] for i in BAD)
    return float(good), float(bad)


def bad_score(f: dict) -> float | None:
    """表情不佳分：生气 / 嫌弃 / 害怕的把握；或者“负面（含难过）”与 CLIP 的“哭 / 皱眉 / 鬼脸”两者里较小的那个
    （难过要两边都同意才算）。没有表情模型的结果时 None。"""
    ex = f.get("ex")
    if not ex or len(ex) < 7:
        return None
    strong = float(sum(ex[i] for i in STRONG))
    pn = f.get("pn")
    both = min(expr_score(ex)[1], float(pn)) if pn is not None else 0.0
    return max(strong, both)


def smile(f: dict) -> float | None:
    ex = f.get("ex")
    return float(ex[3]) if ex and len(ex) >= 7 else None


class FaceQEngine:
    def __init__(self, det_path: str, expr_path: str | None):
        import cv2
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(det_path, "", (320, 320), 0.8, 0.3, 5000)
        self.expr = cv2.dnn.readNetFromONNX(expr_path) if expr_path else None
        self._lock = threading.Lock()
        self._prompts = None

    def clip_probs(self, clip, faces_bgr: list[np.ndarray]) -> list[tuple[float, float]]:
        """中文 CLIP 看对齐后的整张脸 → [(闭着眼的把握, 在哭 / 皱眉 / 做鬼脸的把握)]。
        两组描述各自 softmax，取前一半（闭眼 / 负面表情）的和；同一次图像特征算两样。"""
        from PIL import Image
        if self._prompts is None:
            self._prompts = (clip.encode_texts(CLOSED_PROMPTS + OPEN_PROMPTS), clip.encode_texts(NEG_PROMPTS + CALM_PROMPTS))
        ims = [Image.fromarray(self.cv2.cvtColor(f, self.cv2.COLOR_BGR2RGB)) for f in faces_bgr]
        emb = clip.encode_images(ims)

        def part(T, n):
            logits = 100.0 * (emb @ T.T)
            logits -= logits.max(axis=1, keepdims=True)
            p = np.exp(logits)
            p /= p.sum(axis=1, keepdims=True)
            return p[:, :n].sum(axis=1)
        pc = part(self._prompts[0], len(CLOSED_PROMPTS))
        pn = part(self._prompts[1], len(NEG_PROMPTS))
        return [(round(float(a), 4), round(float(b), 4)) for a, b in zip(pc, pn)]

    def expression(self, bgr112: np.ndarray) -> list[float]:
        cv2 = self.cv2
        x = cv2.cvtColor(bgr112, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        x = (x - 0.5) / 0.5
        self.expr.setInput(cv2.dnn.blobFromImage(x))
        logits = np.asarray(self.expr.forward(), np.float64).ravel()[:7]
        e = np.exp(logits - logits.max())
        return [round(float(v), 4) for v in e / e.sum()]

    def analyze(self, rgb: np.ndarray, clip=None) -> dict:
        """一张预览（RGB uint8）→ {"w", "h", "fer", "clip", "faces": [{"b": [x,y,w,h]（0–1）, "eo": [右, 左],
        "pc": 闭着眼的把握, "pn": 在哭 / 皱眉 / 做鬼脸的把握, "ex": 7 类表情概率}]}。
        clip = 中文 CLIP 引擎（没有就不判断闭眼，表情只认生气 / 嫌弃 / 害怕）。"""
        cv2 = self.cv2
        bgr = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        with self._lock:
            self.det.setInputSize((w, h))
            _, faces = self.det.detect(bgr)
            out, bigs = [], []
            for f in sorted(faces if faces is not None else [], key=lambda f: -(f[2] * f[3]))[:30]:
                if min(f[2], f[3]) < MIN_FACE:
                    continue
                lm = np.asarray(f[4:14], np.float32).reshape(5, 2)
                face = {"b": [round(float(f[0]) / w, 4), round(float(f[1]) / h, 4),
                              round(float(f[2]) / w, 4), round(float(f[3]) / h, 4)]}
                big = align(bgr, lm, 224)
                face["eo"] = eye_openness(cv2.cvtColor(big, cv2.COLOR_BGR2GRAY))
                if self.expr is not None:
                    try:
                        face["ex"] = self.expression(cv2.resize(big, (112, 112), interpolation=cv2.INTER_AREA))
                    except Exception as e:                    # 模型异常：只看眼睛
                        log.warning("表情识别失败：%s", e)
                        self.expr = None
                out.append(face)
                bigs.append(big)
            if clip is not None and bigs:
                try:
                    for face, (pc, pn) in zip(out, self.clip_probs(clip, bigs)):
                        face["pc"], face["pn"] = pc, pn
                except Exception as e:
                    log.warning("闭眼判断（CLIP）失败：%s", e)
                    clip = None
        return {"w": int(w), "h": int(h), "fer": self.expr is not None, "clip": clip is not None, "faces": out}


_engine: FaceQEngine | None = None
_engine_key = None


def ready() -> bool:
    return models.file_path("face_detection_yunet_2023mar.onnx") is not None


def expr_ready() -> bool:
    return models.ready("expr")


def engine() -> FaceQEngine | None:
    global _engine, _engine_key
    det = models.file_path("face_detection_yunet_2023mar.onnx")
    if det is None:
        return None
    ex = models.file_path(EXPR_FILE)
    key = (str(det), str(ex))
    if _engine is None or _engine_key != key:
        _engine = FaceQEngine(str(det), str(ex) if ex else None)
        _engine_key = key
    return _engine


# ---------------------------------------------------------------------------
# 一组连拍
# ---------------------------------------------------------------------------

def clusters(groups) -> dict[int, list]:
    out: dict[int, list] = defaultdict(list)
    for g in groups:
        sim = g.feats.get("similar")
        if sim and g.files and not g.is_video:
            out[int(sim["cid"])].append(g)
    return out


def _wants(g) -> bool:
    """扫描时检测过人脸、而且一张脸都没有的照片不用再看。"""
    fd = g.feats.get("faces")
    return fd is None or int(fd.get("n") or 0) > 0


def pending(groups, clip: bool = False) -> list:
    """连拍组里还没检查过的照片（表情模型 / CLIP 是后来才下载的，有脸的也重新看一遍）。"""
    fer = expr_ready()
    out = []
    for cs in clusters(groups).values():
        for g in cs:
            fq = g.feats.get("faceq")
            if fq is None:
                if _wants(g):
                    out.append(g)
            elif fq.get("faces") and ((fer and not fq.get("fer")) or (clip and not fq.get("clip"))):
                out.append(g)
    return out


def load_cached(groups, cache) -> int:
    todo = {g.primary_file.sig: g for cs in clusters(groups).values() for g in cs if "faceq" not in g.feats}
    if not todo or cache is None:
        return 0
    got = cache.get_ocr_many(list(todo), FACEQ_KEY)
    for sig, d in got.items():
        if isinstance(d, dict):
            todo[sig].feats["faceq"] = d
    return len(got)


def _tracks(gs: list) -> dict[tuple[int, int], int]:
    """同一组连拍里把每张的脸连成“同一个人”：按时间顺序，位置相近、大小相近的就是同一个人。"""
    tracks: list[list[float]] = []          # 每个人最近一次的框
    owner: dict[tuple[int, int], int] = {}
    for g in sorted(gs, key=lambda g: (g.date, g.name)):
        faces = (g.feats.get("faceq") or {}).get("faces") or []
        used: set[int] = set()
        for i, f in sorted(enumerate(faces), key=lambda kv: -(kv[1]["b"][2] * kv[1]["b"][3])):
            x, y, w, h = f["b"]
            cx, cy, sz = x + w / 2, y + h / 2, math.sqrt(max(1e-9, w * h))
            best, best_d = None, 0.9
            for ti, (tx, ty, tw, th) in enumerate(tracks):
                if ti in used:
                    continue
                tsz = math.sqrt(max(1e-9, tw * th))
                if not 0.6 < sz / tsz < 1.65:
                    continue
                d = math.hypot(cx - (tx + tw / 2), cy - (ty + th / 2)) / math.sqrt(sz * tsz)
                if d < best_d:
                    best, best_d = ti, d
            if best is None and len(faces) == 1 and len(tracks) == 1:
                best = 0                     # 一直只有一个人：拉近 / 拉远、走动了也还是他
            if best is None:
                tracks.append(list(f["b"]))
                best = len(tracks) - 1
            else:
                tracks[best] = list(f["b"])
            used.add(best)
            owner[(g.gid, i)] = best
    return owner


def judge(gs: list) -> None:
    """给一组连拍里的每张照片写上 feats["faceq"]["closed"] / ["bad"]（闭眼 / 表情不佳的人脸序号，会标在缩略图上）
    和 ["nosmile"]（别的几张在笑、这张没笑：不标，只在挑“最佳”时往后排）。"""
    owner = _tracks(gs)
    by_gid = {g.gid: g for g in gs}
    least_closed: dict[int, float] = {}
    count: dict[int, int] = defaultdict(int)
    for (gid, i), t in owner.items():
        f = by_gid[gid].feats["faceq"]["faces"][i]
        count[t] += 1
        sc = closed_score(f)
        if sc is not None:
            least_closed[t] = min(least_closed.get(t, sc), sc)

    def is_closed(f, t) -> bool:
        sc = closed_score(f)
        return sc is not None and sc >= CLOSED_SCORE and sc - least_closed.get(t, sc) >= CLOSED_MARGIN

    least_bad: dict[int, float] = {}                   # 每个人睁着眼的几张里最低的表情不佳分
    most_smile: dict[int, float] = {}                  # ……和笑得最明显的把握
    for (gid, i), t in owner.items():
        f = by_gid[gid].feats["faceq"]["faces"][i]
        if is_closed(f, t):
            continue
        b, s = bad_score(f), smile(f)
        if b is not None:
            least_bad[t] = min(least_bad.get(t, b), b)
        if s is not None:
            most_smile[t] = max(most_smile.get(t, s), s)
    for g in gs:
        fq = g.feats.get("faceq")
        if fq is None:
            continue
        closed, bad, nosmile = [], [], []
        for i, f in enumerate(fq.get("faces") or []):
            t = owner.get((g.gid, i))
            if t is None or count[t] < 2:
                continue
            if is_closed(f, t):
                closed.append(i)
                continue
            b, s = bad_score(f), smile(f)
            if b is not None and b >= BAD_SCORE and b - least_bad.get(t, b) >= BAD_MARGIN:
                bad.append(i)
            elif s is not None and s < SMILE_LO and most_smile.get(t, 0.0) >= SMILE_HI:
                nosmile.append(i)
        fq["closed"], fq["bad"], fq["nosmile"] = closed, bad, nosmile


def issues(g) -> tuple[int, int]:
    fq = g.feats.get("faceq") or {}
    return len(fq.get("closed") or ()), len(fq.get("bad") or ())


def rerank(gs: list, blur_threshold: float = 60.0) -> None:
    """组内重新排“最佳”：先看清不清楚（低于模糊阈值的往后放），再看几个人闭眼、几个人表情不佳、
    几个人在别的几张里笑了这张却没笑，最后比清晰度。"""
    def key(g):
        sc = float(g.feat("blur", "score") or 0)
        n_closed, n_bad = issues(g)
        n_nosmile = len((g.feats.get("faceq") or {}).get("nosmile") or ())
        return (sc < blur_threshold, n_closed, n_bad, n_nosmile, -sc, -g.total_size, g.name)
    for r, g in enumerate(sorted(gs, key=key)):
        g.feats["similar"]["rank"] = r + 1
        g.feats["similar"]["best"] = r == 0


def apply(groups, cache=None, blur_threshold: float = 60.0) -> int:
    """连拍分组之后调用：载入缓存的检查结果、判断闭眼 / 表情、重排每组的“最佳”。返回有问题的照片数。"""
    load_cached(groups, cache)
    cs = clusters(groups)
    member = {g.gid for v in cs.values() for g in v}
    for g in groups:                        # 不在连拍组里了：没有可比的，不标
        fq = g.feats.get("faceq")
        if fq is not None and g.gid not in member:
            fq["closed"], fq["bad"], fq["nosmile"] = [], [], []
    n = 0
    for gs in cs.values():
        if any("faceq" in g.feats for g in gs):
            judge(gs)
            rerank(gs, blur_threshold)
            n += sum(1 for g in gs if any(issues(g)))
    return n


def describe(g) -> str:
    n_closed, n_bad = issues(g)
    parts = []
    if n_closed:
        parts.append(_t("{0} 人闭眼", n_closed))
    if n_bad:
        parts.append(_t("{0} 人表情不佳", n_bad))
    n_nosmile = len((g.feats.get("faceq") or {}).get("nosmile") or ())
    if n_nosmile:                                     # 只在悬停说明里提一句，不打标签
        parts.append(_t("{0} 人没在笑（别的几张在笑）", n_nosmile))
    return _t("、").join(parts)


def analyze(groups, lib, progress: Callable[[int, int], None] | None = None,
            cancel: threading.Event | None = None) -> int:
    """检查连拍组里还没看过的照片，结果写入 g.feats["faceq"] 并缓存。返回检查了几张。"""
    from ..core.imaging import load_preview
    load_cached(groups, lib.cache)
    ai = getattr(lib, "ai", None)
    use_clip = bool(ai is not None and ai.clip_ready())
    todo = pending(groups, use_clip)
    eng = engine() if todo else None
    if eng is None:
        return 0
    clip = ai.clip() if use_clip else None
    buf: dict[str, dict] = {}
    done = 0
    for g in todo:
        if cancel is not None and cancel.is_set():
            break
        try:
            im = load_preview(g.primary, g.primary_file.role, PREVIEW_SIDE, lib.exiftool_path)[0]
            res = eng.analyze(np.asarray(im.convert("RGB")), clip)
        except Exception as e:                   # 读不了的照片记成没有脸，下次不再试
            log.debug("闭眼检查：读取 %s 失败：%s", g.primary, e)
            res = {"w": 0, "h": 0, "fer": expr_ready(), "clip": use_clip, "faces": []}
        g.feats["faceq"] = res
        buf[g.primary_file.sig] = res
        done += 1
        if len(buf) >= 24:
            lib.cache.put_ocr_many(buf, FACEQ_KEY)
            buf = {}
        if progress:
            progress(done, len(todo))
    if buf:
        lib.cache.put_ocr_many(buf, FACEQ_KEY)
    return done
