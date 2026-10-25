"""全景拼接（Photomerge）与景深合成（焦点堆栈）。

- 全景：OpenCV Stitcher（特征匹配 + 光束法平差 + 曝光补偿 + 接缝线 + 多频段融合）；拼完可以自动裁掉四周的空白。
- 景深合成：同一场景、对焦在不同距离的几张——先对齐（复用合并曝光的亚像素对齐），再按每个像素哪张最清晰
  （拉普拉斯能量）算权重，在拉普拉斯金字塔里融合（接缝处不会出现硬边）。
"""
from __future__ import annotations

import numpy as np

from ..i18n import t as _t


class StitchError(Exception):
    pass


_STATUS = {1: _t("照片之间重叠太少，找不到足够的共同特征（每两张相邻的照片最好重叠 30% 以上）"),
           2: _t("对齐失败：照片之间的关系估计不出来"), 3: _t("相机参数估计失败")}


def panorama(images: list[np.ndarray], mode: str = "auto", crop: bool = True, max_side: int = 0) -> np.ndarray:
    """images：RGB uint8；返回 RGBA uint8（拼接范围以外透明，crop 时裁成矩形）。"""
    import cv2
    if len(images) < 2:
        raise StitchError(_t("至少需要两张照片"))
    imgs = []
    for im in images:
        im = np.ascontiguousarray(im[..., :3])
        if max_side and max(im.shape[:2]) > max_side:
            s = max_side / max(im.shape[:2])
            im = cv2.resize(im, (int(im.shape[1] * s), int(im.shape[0] * s)), interpolation=cv2.INTER_AREA)
        imgs.append(cv2.cvtColor(im, cv2.COLOR_RGB2BGR))
    st = cv2.Stitcher_create(cv2.Stitcher_SCANS if mode == "scans" else cv2.Stitcher_PANORAMA)
    status, pano = st.stitch(imgs)
    if status != 0 or pano is None:
        raise StitchError(_STATUS.get(status, _t('拼接失败（代码 {0}）', status)))
    rgb = cv2.cvtColor(pano, cv2.COLOR_BGR2RGB)
    valid = (cv2.cvtColor(pano, cv2.COLOR_BGR2GRAY) > 0).astype(np.uint8)
    valid = cv2.morphologyEx(valid, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(valid, 8)
    if n > 1:
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        valid = (lab == big).astype(np.uint8)
    if crop:
        x0, y0, x1, y1 = inner_rect(valid)
        rgb, valid = rgb[y0:y1, x0:x1], valid[y0:y1, x0:x1]
    a = (valid * 255).astype(np.uint8)
    return np.ascontiguousarray(np.dstack([rgb, a]))


def inner_rect(valid: np.ndarray) -> tuple[int, int, int, int]:
    """在有效区域里找一个尽量大的矩形：从外接框开始，每次把空白最多的一条边往里收。"""
    ys, xs = np.nonzero(valid)
    if not len(xs):
        return 0, 0, valid.shape[1], valid.shape[0]
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    for _ in range(4 * max(valid.shape)):
        sub = valid[y0:y1, x0:x1]
        if sub.size == 0:
            break
        bad = [(1 - sub[0]).sum() / sub.shape[1], (1 - sub[-1]).sum() / sub.shape[1],
               (1 - sub[:, 0]).sum() / sub.shape[0], (1 - sub[:, -1]).sum() / sub.shape[0]]
        k = int(np.argmax(bad))
        if bad[k] <= 0:
            break
        step = max(1, int(min(y1 - y0, x1 - x0) * 0.005))
        if k == 0:
            y0 += step
        elif k == 1:
            y1 -= step
        elif k == 2:
            x0 += step
        else:
            x1 -= step
        if x1 - x0 < 8 or y1 - y0 < 8:
            break
    return int(x0), int(y0), int(x1), int(y1)


# ---------------------------------------------------------------------------
# 景深合成
# ---------------------------------------------------------------------------

def _sharpness(gray: np.ndarray, sigma: float) -> np.ndarray:
    import cv2
    lap = cv2.Laplacian(cv2.GaussianBlur(gray, (0, 0), 0.8), cv2.CV_32F, ksize=3)
    return cv2.GaussianBlur(lap * lap, (0, 0), sigma)


def _pyr_blend(images: list[np.ndarray], weights: list[np.ndarray]) -> np.ndarray:
    import cv2
    h, w = images[0].shape[:2]
    levels = max(1, int(np.log2(min(h, w))) - 4)
    acc = None
    for img, wt in zip(images, weights):
        gp = [wt]
        for _ in range(levels):
            gp.append(cv2.pyrDown(gp[-1]))
        lp = []
        cur = img
        for _ in range(levels):
            down = cv2.pyrDown(cur)
            lp.append(cur - cv2.pyrUp(down, dstsize=(cur.shape[1], cur.shape[0])))
            cur = down
        lp.append(cur)
        contrib = [l * g[..., None] for l, g in zip(lp, gp)]
        acc = contrib if acc is None else [a + c for a, c in zip(acc, contrib)]
    out = acc[-1]
    for l in reversed(acc[:-1]):
        out = cv2.pyrUp(out, dstsize=(l.shape[1], l.shape[0])) + l
    return out


def focus_stack(frames: list[np.ndarray], align: bool = True, progress=None):
    """frames：同尺寸 RGB uint8。返回 (结果 RGB uint8, 每个像素取自哪一张的索引图, 对齐后的各张 RGB uint8)。"""
    import cv2
    from . import hdr
    if len(frames) < 2:
        raise StitchError(_t("至少需要两张照片"))
    h, w = frames[0].shape[:2]
    if any(f.shape[:2] != (h, w) for f in frames):
        raise StitchError(_t("几张照片的尺寸不一样"))
    ref = len(frames) // 2
    imgs = []
    for i, f in enumerate(frames):
        if progress:
            progress(i, len(frames) * 2)
        img = np.ascontiguousarray(f[..., :3])
        if align and i != ref:
            H, _, _ = hdr.estimate_alignment(frames[ref][..., :3], img)
            if H is not None:
                img, _ = hdr.warp(img, H)
        imgs.append(img.astype(np.float32) / 255)
    sigma = max(2.0, max(h, w) / 600)
    sh = []
    for i, img in enumerate(imgs):
        if progress:
            progress(len(frames) + i, len(frames) * 2)
        sh.append(_sharpness(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY), sigma))
    S = np.stack(sh)
    idx = np.argmax(S, axis=0)
    p = 4.0
    Wt = (S / (S.max(axis=0, keepdims=True) + 1e-12)) ** p
    Wt = Wt / (Wt.sum(axis=0, keepdims=True) + 1e-12)
    out = _pyr_blend(imgs, [Wt[i].astype(np.float32) for i in range(len(imgs))])
    aligned = [np.clip(i * 255 + 0.5, 0, 255).astype(np.uint8) for i in imgs]
    return np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8), idx.astype(np.uint8), aligned
