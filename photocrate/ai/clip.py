"""中文 CLIP（ONNX，onnxruntime CPU 推理）：把图片和文字映射到同一个向量空间，余弦相似度越高越相关。

模型是 Xenova 导出的 Chinese-CLIP（图文合在一个 ONNX 图里）：一次推理需要同时给文字和图片输入，
只编码图片时给一句最短的占位文字，只编码文字时给一张 1 张的空白图片，开销可以忽略。
"""
from __future__ import annotations

import os
import threading
import unicodedata
from pathlib import Path
from typing import Iterable

MEAN = (0.48145466, 0.4578275, 0.40821073)
STD = (0.26862954, 0.26130258, 0.27577711)
SIZE = 224
CONTEXT = 52            # Chinese-CLIP 的文字长度上限


# ---------------------------------------------------------------------------
# BERT 分词（中文按字切分，英文小写 + WordPiece）
# ---------------------------------------------------------------------------

def _is_cjk(cp: int) -> bool:
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0x20000 <= cp <= 0x2A6DF or 0x2A700 <= cp <= 0x2B73F
            or 0x2B740 <= cp <= 0x2B81F or 0x2B820 <= cp <= 0x2CEAF or 0xF900 <= cp <= 0xFAFF
            or 0x2F800 <= cp <= 0x2FA1F)


def _is_punct(ch: str) -> bool:
    cp = ord(ch)
    if 33 <= cp <= 47 or 58 <= cp <= 64 or 91 <= cp <= 96 or 123 <= cp <= 126:
        return True
    return unicodedata.category(ch).startswith("P")


class BertTokenizer:
    def __init__(self, vocab_file: str | Path):
        self.vocab: dict[str, int] = {}
        with open(vocab_file, encoding="utf-8") as f:
            for i, line in enumerate(f):
                tok = line.rstrip("\n")
                if tok and tok not in self.vocab:
                    self.vocab[tok] = i
        self.unk = self.vocab.get("[UNK]", 100)
        self.cls = self.vocab.get("[CLS]", 101)
        self.sep = self.vocab.get("[SEP]", 102)
        self.pad = self.vocab.get("[PAD]", 0)

    def _basic(self, text: str) -> list[str]:
        out, buf = [], []

        def flush():
            if buf:
                out.append("".join(buf))
                buf.clear()
        text = unicodedata.normalize("NFD", text.lower())
        for ch in text:
            cp = ord(ch)
            if cp == 0 or cp == 0xFFFD or unicodedata.category(ch) == "Mn":
                continue
            if ch.isspace():
                flush()
            elif _is_cjk(cp) or _is_punct(ch):
                flush()
                out.append(ch)
            else:
                buf.append(ch)
        flush()
        return out

    def _wordpiece(self, word: str) -> list[int]:
        if len(word) > 100:
            return [self.unk]
        ids, start = [], 0
        while start < len(word):
            end, cur = len(word), None
            while start < end:
                sub = word[start:end] if start == 0 else "##" + word[start:end]
                if sub in self.vocab:
                    cur = self.vocab[sub]
                    break
                end -= 1
            if cur is None:
                return [self.unk]
            ids.append(cur)
            start = end
        return ids

    def encode(self, text: str, max_len: int = CONTEXT) -> list[int]:
        ids = [i for w in self._basic(text) for i in self._wordpiece(w)]
        return [self.cls] + ids[: max_len - 2] + [self.sep]


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------

def preprocess(images: Iterable) -> "np.ndarray":
    """PIL 图片 → (N, 3, 224, 224) float32（直接缩放到 224×224，与 Chinese-CLIP 的预处理一致）。"""
    import numpy as np
    from PIL import Image

    mean = np.array(MEAN, dtype=np.float32).reshape(3, 1, 1)
    std = np.array(STD, dtype=np.float32).reshape(3, 1, 1)
    arr = []
    for im in images:
        im = im.convert("RGB").resize((SIZE, SIZE), Image.Resampling.BICUBIC)
        a = np.asarray(im, dtype=np.float32).transpose(2, 0, 1) / 255.0
        arr.append((a - mean) / std)
    return np.stack(arr).astype(np.float32)


def _normalize(x):
    import numpy as np
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return (x / np.maximum(n, 1e-8)).astype(np.float32)


class ClipEngine:
    def __init__(self, model_path: str | Path, vocab_path: str | Path, threads: int = 0):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = threads or max(1, min(8, (os.cpu_count() or 2) - 1))
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name: i for i in self.sess.get_inputs()}
        outs = [o.name for o in self.sess.get_outputs()]
        self.img_out = next((o for o in outs if "image_embed" in o), None)
        self.txt_out = next((o for o in outs if "text_embed" in o), None)
        if not self.img_out or not self.txt_out or "pixel_values" not in self.inputs \
                or "input_ids" not in self.inputs:
            raise RuntimeError(f"模型的输入输出不是预期的 CLIP 格式：inputs={list(self.inputs)} outputs={outs}")
        self.tok = BertTokenizer(vocab_path)
        self._lock = threading.Lock()
        self.dim: int | None = None

    def _text_feed(self, texts: list[str]):
        import numpy as np
        ids = [self.tok.encode(t) for t in texts]
        n = max(len(x) for x in ids)
        arr = np.full((len(ids), n), self.tok.pad, dtype=np.int64)
        mask = np.zeros((len(ids), n), dtype=np.int64)
        for i, x in enumerate(ids):
            arr[i, :len(x)] = x
            mask[i, :len(x)] = 1
        feed = {"input_ids": arr}
        if "attention_mask" in self.inputs:
            feed["attention_mask"] = mask
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(arr)
        return feed

    def encode_images(self, images: list) -> "np.ndarray":
        """PIL 图片列表 → (N, D) 单位向量。"""
        feed = self._text_feed(["图"])
        feed["pixel_values"] = preprocess(images)
        with self._lock:
            out = self.sess.run([self.img_out], feed)[0]
        self.dim = out.shape[-1]
        return _normalize(out)

    def encode_texts(self, texts: list[str]) -> "np.ndarray":
        import numpy as np
        feed = self._text_feed(texts)
        feed["pixel_values"] = np.zeros((1, 3, SIZE, SIZE), dtype=np.float32)
        with self._lock:
            out = self.sess.run([self.txt_out], feed)[0]
        self.dim = out.shape[-1]
        return _normalize(out)
