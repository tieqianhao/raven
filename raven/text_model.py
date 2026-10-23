# -*- coding: utf-8 -*-
"""文本检测引擎：字符 n-gram 哈希特征 + 逻辑回归 + 关键词规则评分。

为什么不用 sklearn / 预训练模型：
1. 环境要可复现——评委/面试官 clone 下来 `python run_all.py` 就能跑，不能要求装 torch；
2. 样本量只有千级，字符 n-gram + 线性模型在这个量级上更稳，且权重可导出到浏览器端做实时推理；
3. 手写 SGD 是刻意的——它逼我把特征、正则、类权重这些细节说清楚，而不是 import 一个黑盒。

哈希函数用 FNV-1a，因为它在 Python 和 JS 里都能逐位复现（demo 里要在浏览器跑同一套权重）。
"""
import json
import math

import numpy as np

from .lexicon import KEYWORDS

DIM = 1 << 13          # 8192 维
NGRAM_NS = (1, 2, 3)


def fnv1a(data: bytes) -> int:
    h = 0x811C9DC5
    for byte in data:
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def ngrams(text: str, ns=NGRAM_NS):
    for n in ns:
        for i in range(len(text) - n + 1):
            yield n, text[i:i + n]


def featurize(text: str, dim: int = DIM) -> np.ndarray:
    """字符 1~3 gram，FNV-1a 哈希到固定维度，计数取 1+log(c) 后 L2 归一化。
    计数压缩是为了防止"某个词重复 20 次"把权重带偏（黑产广告爱刷关键词）。"""
    vec = np.zeros(dim, dtype=np.float32)
    for n, g in ngrams(text):
        idx = fnv1a(f"{n}|{g}".encode("utf-8")) % dim
        vec[idx] += 1.0
    nz = vec > 0
    vec[nz] = 1.0 + np.log(vec[nz])
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec


def featurize_batch(texts, dim: int = DIM) -> np.ndarray:
    return np.vstack([featurize(t, dim) for t in texts]) if texts else np.zeros((0, dim), np.float32)


# ------------------------------------------------------------------ 训练
class LogisticModel:
    def __init__(self, dim: int = DIM):
        self.dim = dim
        self.w = np.zeros(dim, dtype=np.float32)
        self.b = 0.0

    def proba(self, x: np.ndarray) -> np.ndarray:
        z = x @ self.w + self.b
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

    def fit(self, X, y, epochs=18, lr=0.6, l2=2e-6, batch=64, pos_weight=1.0,
            decay=0.15, seed=0):
        """小批量 SGD + L2。pos_weight 用来抵消正负样本比（本数据集 2:1）。

        decay 控制学习率衰减：早期用 0.15 衰减太快，模型 150 轮后输出仍挤在 0.45~0.65，
        导致"置信度"没有区分度、分档阈值失效。现在按收敛情况重设（见 docs/EVAL.md 校准一节）。
        """
        rng = np.random.default_rng(seed)
        y = np.asarray(y, dtype=np.float32)
        n = len(y)
        idx = np.arange(n)
        for ep in range(epochs):
            rng.shuffle(idx)
            cur_lr = lr / (1.0 + decay * ep)
            for s in range(0, n, batch):
                bidx = idx[s:s + batch]
                xb, yb = X[bidx], y[bidx]
                p = self.proba(xb)
                wgt = np.where(yb > 0.5, pos_weight, 1.0).astype(np.float32)
                err = (p - yb) * wgt
                grad_w = xb.T @ err / len(bidx) + l2 * self.w
                grad_b = float(err.mean())
                self.w -= cur_lr * grad_w
                self.b -= cur_lr * grad_b
        return self

    def save(self, path, digits=4):
        payload = {
            "dim": self.dim,
            "b": round(float(self.b), digits),
            "w": [round(float(v), digits) for v in self.w],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            p = json.load(f)
        m = cls(p["dim"])
        m.w = np.array(p["w"], dtype=np.float32)
        m.b = p["b"]
        return m


# ------------------------------------------------------------------ 规则引擎
def rule_score(text: str):
    """关键词加权打分 + 上下文否定修正。

    返回 (风险分, 命中证据列表, 类目分)。这一步是**可解释**的来源：
    人工复核看到的不是"模型 0.91"，而是"命中 外挂(3.0)、稳定不封(2.0)"。
    """
    hits = []
    per_class = {}
    for cls, kws in KEYWORDS.items():
        total = 0.0
        for weight, term in kws:
            c = text.count(term)
            if c:
                total += weight * min(c, 3)
                hits.append({"class": cls, "term": term, "weight": weight, "count": c})
        if total:
            per_class[cls] = total
    neg = per_class.pop("context_neg", 0.0)

    top = max(per_class.values()) if per_class else 0.0
    risk = 1.0 - math.exp(-top / 3.2)          # 饱和函数，避免长广告刷到 1.0
    neg_factor = 1.0 - 0.55 * min(1.0, neg / 4.0)
    risk = risk * neg_factor
    return risk, hits, per_class, neg


def subtype_vote(per_class):
    if not per_class:
        return "benign"
    cls, val = max(per_class.items(), key=lambda kv: kv[1])
    return cls if val >= 1.5 else "benign"


# ------------------------------------------------------- 人工特征（可解释通道）
CONTACT_TERMS = ("微信", "加v", "加微", "群", "私信", "扣1", "扣个1", "私聊", "简介", "头像")

HAND_NAMES = [
    "分-外挂", "分-代练", "分-账号", "分-引流",
    "否定语境", "命中词密度", "有联系方式词",
    "文本长度", "有长数字串", "最高类目分",
]
HAND_DIM = len(HAND_NAMES)


def hand_features(text: str) -> np.ndarray:
    """把规则引擎的"判断依据"直接变成特征喂给模型。

    这是本项目最实用的一处设计。纯字符 n-gram 能记住"怎么写的"，但记不住
    "同一个词出现在举报语境里该不该算违规"——样本太少，模型学不出这个交互。
    所以把 (各类目分, 否定语境分) 显式做成一维特征，让线性模型只需要学
    "否定语境分高时把总分拉下来"这一条权重，660 条样本就够学了。
    """
    risk, hits, per_class, neg = rule_score(text)
    kw_hits = [h for h in hits if h["class"] != "context_neg"]
    digits = 0
    cur = 0
    for ch in text:
        cur = cur + 1 if ch.isdigit() else 0
        digits = max(digits, cur)
    has_contact = 1.0 if any(t in text for t in CONTACT_TERMS) else 0.0
    top = max(per_class.values()) if per_class else 0.0
    f = np.array([
        per_class.get("cheat", 0.0) / 10.0,
        per_class.get("boost", 0.0) / 10.0,
        per_class.get("account", 0.0) / 10.0,
        per_class.get("traffic", 0.0) / 10.0,
        neg / 10.0,
        min(len(kw_hits) / 6.0, 1.0),
        has_contact,
        min(math.log1p(len(text)) / 4.0, 1.0),
        min(digits / 4.0, 1.0),
        min(top / 10.0, 1.0),
    ], dtype=np.float32)
    return f


def featurize_hybrid(text: str, dim: int = DIM) -> np.ndarray:
    """字符 n-gram（归一化后 L2=1）+ 人工特征（放大 2 倍使其量级相当）。"""
    return np.concatenate([featurize(text, dim), 2.0 * hand_features(text)])


def featurize_hybrid_batch(texts, dim: int = DIM):
    return np.vstack([featurize_hybrid(t, dim) for t in texts])

