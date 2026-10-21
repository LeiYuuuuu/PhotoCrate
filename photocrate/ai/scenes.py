"""场景识别（零样本）：用 CLIP 比较照片与每个场景的几句中文描述，取最像的场景。

不需要训练，也可以随时增删场景：每个场景 = (名称, 大类, [描述…])，描述会套进“一张…的照片”。
"""
from __future__ import annotations

SCENES: list[tuple[str, str, list[str]]] = [
    ("山景", "自然风光", ["山峰", "群山风景", "登山步道"]),
    ("海边", "自然风光", ["海边沙滩", "大海和海浪", "海岸线"]),
    ("湖泊河流", "自然风光", ["湖泊", "河流", "瀑布"]),
    ("森林田野", "自然风光", ["森林", "草地田野", "公园里的树木"]),
    ("雪景", "自然风光", ["雪景", "冬天的雪地"]),
    ("日落天空", "自然风光", ["日落和晚霞", "蓝天白云", "星空"]),
    ("花卉植物", "动物植物", ["花朵特写", "盆栽植物", "樱花"]),
    ("动物", "动物植物", ["野生动物", "鸟", "鱼"]),
    ("猫狗宠物", "动物植物", ["猫", "狗"]),
    ("城市街景", "城市建筑", ["城市街道", "繁华的商业街", "城市天际线"]),
    ("建筑", "城市建筑", ["建筑物外观", "高楼大厦", "桥"]),
    ("夜景", "城市建筑", ["城市夜景", "夜晚的灯光", "灯火通明的夜晚"]),
    ("寺庙古建", "城市建筑", ["寺庙", "神社和鸟居", "古建筑"]),
    ("交通工具", "城市建筑", ["火车和地铁", "汽车", "飞机"]),
    ("室内", "室内", ["室内房间", "商场内部", "博物馆展厅"]),
    ("美食", "美食", ["美食", "一碗拉面", "餐桌上的饭菜"]),
    ("饮品甜点", "美食", ["咖啡和饮料", "甜点蛋糕", "冰淇淋"]),
    ("人像", "人物", ["一个人的肖像", "人像写真"]),
    ("合影", "人物", ["多人合影", "一群人"]),
    ("自拍", "人物", ["自拍"]),
    ("文档截图", "文档截图", ["文字文档", "手机屏幕截图", "票据和收据"]),
]

SCENE_GROUPS = ["自然风光", "城市建筑", "人物", "美食", "动物植物", "室内", "文档截图"]


def prompts() -> tuple[list[str], list[int]]:
    texts, owner = [], []
    for i, (_, _, descs) in enumerate(SCENES):
        for d in descs:
            texts.append(f"一张{d}的照片")
            owner.append(i)
    return texts, owner


def label_matrix(engine) -> "np.ndarray":
    """每个场景一行：几句描述的向量取平均再归一化。"""
    import numpy as np
    texts, owner = prompts()
    emb = engine.encode_texts(texts)
    out = np.zeros((len(SCENES), emb.shape[1]), dtype=np.float32)
    for e, i in zip(emb, owner):
        out[i] += e
    out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-8)
    return out


def classify(img_emb: "np.ndarray", labels: "np.ndarray", scale: float = 100.0) -> list[dict]:
    """img_emb (N, D)、labels (K, D) → 每张照片 {scene, group, prob, top3}。"""
    import numpy as np
    logits = scale * img_emb @ labels.T
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    p /= p.sum(axis=1, keepdims=True)
    out = []
    for row in p:
        order = np.argsort(-row)[:3]
        top = int(order[0])
        out.append({"scene": SCENES[top][0], "group": SCENES[top][1], "prob": round(float(row[top]), 3),
                    "top3": [[SCENES[int(k)][0], round(float(row[int(k)]), 3)] for k in order]})
    return out
