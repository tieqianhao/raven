# -*- coding: utf-8 -*-
"""统一引擎层：训练、打分、融合、处置。

这一层是"业务语义"所在，也是唯一被 CLI / 评测 / 报告 / demo 共用的入口。
分开的理由：评测和线上必须跑**同一份代码**，否则"评测 92 分、上线一团乱"是必然的。
"""
import json
import os

import numpy as np

from .image_forensics import FEATURE_NAMES, extract, load as load_image
from .lexicon import SUBTYPE_CN, THRESHOLDS
from .normalize import normalize, recovered_terms
from .text_model import (
    DIM, HAND_DIM, HAND_NAMES, LogisticModel, featurize, featurize_hybrid,
    hand_features, rule_score, subtype_vote,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "artifacts")


# ------------------------------------------------------------------ 数据读取
def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def text_splits():
    names = ["train", "val", "val_adv", "test_clean", "test_adv", "test_pervasive", "test_holdout"]
    return {n: read_jsonl(os.path.join(ART, "dataset", f"text_{n}.jsonl")) for n in names}


# ------------------------------------------------------------------ 哨兵
class TextEngine:
    """文本侧完整链路：归一化 -> 混合模型判决 + 规则证据 -> 分级处置。

    混合模型 = 字符 n-gram（主力）+ 规则证据特征（解释通道 / 互补信号）。
    两者失败模式不重叠：逐字符分隔能打死规则（F1=0.000）但打不死字符模型；
    未登录谐音会削弱字符模型，而规则在归一化后仍能兜住一部分。
    """

    def __init__(self, model: LogisticModel, threshold=0.5, use_norm=True):
        self.model = model
        self.threshold = threshold
        self.use_norm = use_norm

    def score(self, text, explain=True):
        norm, trace = normalize(text, with_trace=True) if explain else (normalize(text), [])
        target = norm if self.use_norm else text
        p_model = float(self.model.proba(featurize_hybrid(target)[None, :])[0])
        p_rule, hits, per_class, neg = rule_score(target)
        out = {
            "raw": text,
            "norm": norm,
            "trace": trace,
            "p": round(p_model, 4),
            "p_model": round(p_model, 4),
            "p_rule": round(p_rule, 4),
            "subtype": subtype_vote(per_class),
            "hits": hits,
            "class_scores": {k: round(v, 2) for k, v in per_class.items()},
            "neg": round(neg, 2),
        }
        if explain:
            out["recovered"] = recovered_terms(text, norm)
        out.update(decide(p_model))
        return out


# ------------------------------------------------------------------ 图像引擎
class ImageEngine:
    def __init__(self, model: LogisticModel, mu, sd, threshold=0.5, feature_idx=None):
        self.model = model
        self.mu = np.asarray(mu, dtype=np.float32)
        self.sd = np.asarray(sd, dtype=np.float32)
        self.threshold = threshold
        self.feature_idx = feature_idx if feature_idx is not None else list(range(len(FEATURE_NAMES)))

    def score_path(self, path, explain=True):
        vec, feats, heat = extract(load_image(path))
        v = vec[self.feature_idx]
        p = float(self.model.proba(((v - self.mu) / self.sd)[None, :])[0])
        out = {
            "path": path,
            "p_image": round(p, 4),
            "features": {k: round(float(v), 4) for k, v in feats.items()},
            "heat": np.round(heat, 3).tolist() if explain else None,
        }
        out.update(decide(p))
        return out


# ------------------------------------------------------------------ 处置
def decide(p):
    if p >= THRESHOLDS["block"]:
        act, cn = "block", "拦截"
    elif p >= THRESHOLDS["review"]:
        act, cn = "review", "限流 + 人工复核"
    elif p >= THRESHOLDS["watch"]:
        act, cn = "watch", "观察（进抽检队列）"
    else:
        act, cn = "allow", "放行"
    return {"action": act, "action_cn": cn}


# ------------------------------------------------------------------ 训练
def train_text_models(splits=None, seed=0):
    """三个模型，构成消融阶梯：
    raw   —— 纯字符 n-gram，喂原始文本（不归一化）
    norm  —— 纯字符 n-gram，喂归一化文本
    hybrid—— 字符 n-gram + 规则证据特征（本方案）
    """
    splits = splits or text_splits()
    tr = splits["train"]
    y = np.array([1.0 if r["label"] == "risk" else 0.0 for r in tr])
    raw_texts = [r["text"] for r in tr]
    norm_texts = [normalize(t) for t in raw_texts]

    X_raw = np.vstack([featurize(t) for t in raw_texts])
    X_norm = np.vstack([featurize(t) for t in norm_texts])
    X_hyb = np.vstack([featurize_hybrid(t) for t in norm_texts])

    return {
        "raw": LogisticModel(DIM).fit(X_raw, y, epochs=120, lr=1.0, l2=1e-7,
                                      pos_weight=0.5, decay=0.01, seed=seed),
        "norm": LogisticModel(DIM).fit(X_norm, y, epochs=120, lr=1.0, l2=1e-7,
                                       pos_weight=0.5, decay=0.01, seed=seed),
        "hybrid": LogisticModel(DIM + HAND_DIM).fit(X_hyb, y, epochs=120, lr=1.0, l2=1e-7,
                                                    pos_weight=0.5, decay=0.01, seed=seed),
    }


def resolve_image_path(p):
    """把 jsonl 里的 path 解析成真实可读路径。

    数据集里存的是**相对仓库根**的路径（`artifacts/dataset/images/xxx.jpg`），
    这样换机器、换操作系统都能跑。但早期版本存过绝对路径，这里做兼容：
    绝对路径存在就直接用，不存在就退化成"按文件名在 images 目录里找"。
    """
    if os.path.isabs(p):
        if os.path.exists(p):
            return p
        cand = os.path.join(ART, "dataset", "images", os.path.basename(p))
        return cand if os.path.exists(cand) else p
    return os.path.normpath(os.path.join(ROOT, p))


def image_dataset_features():
    train_rows = read_jsonl(os.path.join(ART, "dataset", "images", "images_train.jsonl"))
    test_rows = read_jsonl(os.path.join(ART, "dataset", "images", "images_test.jsonl"))

    def build(rows):
        X = []
        for r in rows:
            vec, _, _ = extract(load_image(resolve_image_path(r["path"])))
            X.append(vec)
        return np.array(X, dtype=np.float32)

    return train_rows, build(train_rows), test_rows, build(test_rows)


def train_image_model(Xtr, ytr, feature_idx=None):
    idx = feature_idx if feature_idx is not None else list(range(Xtr.shape[1]))
    X = Xtr[:, idx]
    mu, sd = X.mean(0), X.std(0) + 1e-6
    m = LogisticModel(len(idx)).fit((X - mu) / sd, np.asarray(ytr, dtype=np.float32),
                                    epochs=250, lr=0.4, l2=1e-3, pos_weight=1.0,
                                    decay=0.01, seed=0)
    return m, mu, sd


# ------------------------------------------------------------------ 存档
def save_bundle(bundle, path=None):
    path = path or os.path.join(ART, "models", "engine.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f)
    return path


def build_bundle(ctx):
    """把模型权重、归一化参数、阈值、词表打包成一个 JSON —— demo 直接用它在浏览器里推理。"""
    from .lexicon import HOMOPHONE, KEYWORDS, PINYIN_ABBR, TRAD_TO_SIMP

    return {
        "version": "1.2",
        "threshold": ctx["threshold"],
        "decision_thresholds": THRESHOLDS,
        "dim": DIM,
        "text_model": {"w": [round(float(v), 4) for v in ctx["model"].w],
                       "b": round(float(ctx["model"].b), 4)},
        "hand_features": HAND_NAMES,
        "image_model": ctx["image_model"],
        "lexicon": {
            "homophone": HOMOPHONE,
            "abbr": PINYIN_ABBR,
            "trad": {k: v for k, v in TRAD_TO_SIMP.items()},
            "keywords": KEYWORDS,
        },
        "subtype_cn": SUBTYPE_CN,
    }
