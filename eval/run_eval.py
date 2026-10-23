# -*- coding: utf-8 -*-
"""评测闭环：消融阶梯 + 三套测试集 + 图像 + 账号级聚合。

跑法：python eval/run_eval.py
输出：artifacts/results/metrics.json（报告和 demo 都读它）

工程约定（很重要）：
1. 阈值只在 val / val_adv 上选，测试集全程不参与调参；
2. 所有配置共用同一份训练集、同一套预处理，唯一变量是"哪一层起作用"；
3. 图像特征集用**训练集上的 5 折交叉验证**选，选中后才在测试集上评一次——避免用测试集选特征。
"""
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from raven.image_forensics import FEATURE_NAMES, extract, iou_with_bbox, load as load_image
from raven.normalize import normalize
from raven.lexicon import THRESHOLDS
from raven.pipeline import (ART, TextEngine, build_bundle, image_dataset_features, read_jsonl,
                            save_bundle, text_splits, train_image_model, train_text_models)
from raven.text_model import (DIM, LogisticModel, featurize, featurize_hybrid, rule_score,
                              subtype_vote)

RESULT_DIR = os.path.join(ART, "results")


# ------------------------------------------------------------------ 指标
def prf(y, p, thr):
    y = np.asarray(y)
    pred = (np.asarray(p) >= thr).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(f1, 4), "fpr": round(fpr, 4),
            "accuracy": round((tp + tn) / max(1, len(y)), 4)}


def auc(y, p):
    """秩和法（Mann-Whitney U），并列取平均秩。"""
    y = np.asarray(y)
    p = np.asarray(p, dtype=float)
    pos = p[y == 1]
    neg = p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.0
    allv = np.concatenate([pos, neg])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(len(allv), float)
    ranks[order] = np.arange(1, len(allv) + 1)
    sorted_v = allv[order]
    i = 0
    while i < len(sorted_v):
        j = i
        while j + 1 < len(sorted_v) and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = ranks[order[i:j + 1]].mean()
        i = j + 1
    r_pos = ranks[:len(pos)].sum()
    return round(float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))), 4)


def labels_of(rows):
    return [1 if r["label"] == "risk" else 0 for r in rows]


def tune_threshold(p_list, y_list):
    """在验证集上搜阈值，目标是**两套场景 F1 的较小值**最大——偏保守，
    宁可 clean 上少拿一点，也不让对抗集塌掉。"""
    best = (0.5, -1.0)
    for thr in np.arange(0.20, 0.96, 0.01):
        s = min(prf(y, p, thr)["f1"] for p, y in zip(p_list, y_list))
        if s > best[1]:
            best = (round(float(thr), 2), s)
    return best[0]


def operating_point(p, y, target_fpr):
    """按"误报率不超过 X%"反查阈值，返回该工作点的 (阈值, 召回率, 实际 FPR)。
    内容风控的真实约束通常是这样给的（"误伤率别超过 1%"），不是"F1 最高"。"""
    best = None
    for thr in np.arange(0.05, 0.99, 0.01):
        m = prf(y, p, thr)
        if m["fpr"] <= target_fpr:
            if best is None or m["recall"] > best[1]:
                best = (round(float(thr), 2), m["recall"], m["fpr"], m["precision"])
    return best


# ------------------------------------------------------------------ 打分器
def make_scorers(models):
    def sc_rule_raw(r):
        return rule_score(r["text"])[0]

    def sc_rule_norm(r):
        return rule_score(normalize(r["text"]))[0]

    def sc_model_raw(r):
        return float(models["raw"].proba(featurize(r["text"])[None, :])[0])

    def sc_model_norm(r):
        return float(models["norm"].proba(featurize(normalize(r["text"]))[None, :])[0])

    def sc_hybrid(r):
        return float(models["hybrid"].proba(featurize_hybrid(normalize(r["text"]))[None, :])[0])

    return {
        "① 规则·原始文本": sc_rule_raw,
        "② 规则·归一化": sc_rule_norm,
        "③ 字符模型·原始文本": sc_model_raw,
        "④ 字符模型·归一化": sc_model_norm,
        "⑤ 混合模型（n-gram+规则特征）": sc_hybrid,
    }


def score_rows(scorer, rows):
    return np.array([scorer(r) for r in rows], dtype=float)


# ------------------------------------------------------------------ 图像特征选择
def kfold_auc(X, y, idx, k=5, seed=0):
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(y))
    folds = np.array_split(order, k)
    aucs = []
    for f in range(k):
        te = folds[f]
        tr = np.concatenate([folds[i] for i in range(k) if i != f])
        m, mu, sd = train_image_model(X[tr], y[tr], idx)
        p = m.proba((X[te][:, idx] - mu) / sd)
        aucs.append(auc(y[te], p))
    return float(np.mean(aucs)), float(np.std(aucs))


# ------------------------------------------------------------------ 主流程
def main():
    os.makedirs(RESULT_DIR, exist_ok=True)

    t0 = time.time()
    splits = text_splits()
    models = train_text_models(splits)
    train_sec = round(time.time() - t0, 2)
    print(f"[train] 文本模型训练完成 {train_sec}s  dim={DIM}  训练样本 {len(splits['train'])}")

    val_rows, valadv_rows = splits["val"], splits["val_adv"]
    y_val, y_valadv = labels_of(val_rows), labels_of(valadv_rows)

    # 1) 消融阶梯：五组配置 × 三套测试集
    scorers = make_scorers(models)
    splits_to_eval = ("test_clean", "test_adv", "test_pervasive", "test_holdout")
    configs = []
    for name, sc in scorers.items():
        pv, pa = score_rows(sc, val_rows), score_rows(sc, valadv_rows)
        thr = tune_threshold([pv, pa], [y_val, y_valadv])
        row = {"config": name, "threshold": thr}
        for split in splits_to_eval:
            rows = splits[split]
            p = score_rows(sc, rows)
            y = labels_of(rows)
            m = prf(y, p, thr)
            m["auc"] = auc(y, p)
            row[split] = m
        configs.append(row)
        print(f"[eval] {name:26s} thr={thr:.2f}  " + "  ".join(
            f"{k.replace('test_','')}={row[k]['f1']:.3f}" for k in splits_to_eval))

    # 2) 最终引擎 = ⑤（字符 n-gram + 规则证据特征，二者失败模式互补）
    FINAL_IDX = 4
    final_thr = configs[FINAL_IDX]["threshold"]
    engine = TextEngine(models["hybrid"], threshold=final_thr, use_norm=True)
    detail = []
    for r in splits["test_adv"]:
        s = engine.score(r["text"])
        d = dict(r)
        d.update({k: v for k, v in s.items() if k != "trace"})
        d["trace"] = s["trace"][:4]
        detail.append(d)

    wrong = [r for r in detail if (r["p"] >= final_thr) != (r["label"] == "risk")]
    fp = [r for r in wrong if r["label"] == "benign"]
    fn = [r for r in wrong if r["label"] == "risk"]

    per_class, benign_kinds = {}, {}
    for cls in ("cheat", "boost", "account", "traffic"):
        sub = [r for r in detail if r["source_class"] == cls]
        per_class[cls] = round(sum(1 for r in sub if r["p"] >= final_thr) / max(1, len(sub)), 4)
    for kind in ("benign", "mention"):
        sub = [r for r in detail if r["source_class"] == kind]
        benign_kinds[kind] = round(sum(1 for r in sub if r["p"] >= final_thr) / max(1, len(sub)), 4)

    adv_rows = [r for r in detail if r["perturb_ops"]]
    rec_hit_adv = sum(1 for r in adv_rows if r.get("recovered"))

    lat = []
    for r in detail[:120]:
        t = time.perf_counter()
        engine.score(r["text"])
        lat.append((time.perf_counter() - t) * 1000)

    # 3) 工作点：按"误报率上限"反查阈值（内容风控的真实约束）
    p_adv = np.array([r["p"] for r in detail])
    y_adv = np.array([1 if r["label"] == "risk" else 0 for r in detail])
    ops = []
    for target in (0.01, 0.03, 0.05, 0.10, 0.20):
        op = operating_point(p_adv, y_adv, target)
        if op:
            ops.append({"target_fpr": target, "threshold": op[0], "recall": op[1],
                        "fpr": op[2], "precision": op[3]})

    # 3.5) 分档视角：把"分数"翻译成运营语言（拦截多少、复核多少、漏多少）
    tier = {"block": {"n": 0, "bad": 0}, "review": {"n": 0, "bad": 0},
            "watch": {"n": 0, "bad": 0}, "allow": {"n": 0, "bad": 0}}
    for r in detail:
        tier[r["action"]]["n"] += 1
        tier[r["action"]]["bad"] += 1 if r["label"] == "risk" else 0
    total_bad = sum(1 for r in detail if r["label"] == "risk")
    tier_rows = []
    for k in ("block", "review", "watch", "allow"):
        t = tier[k]
        tier_rows.append({
            "tier": k, "n": t["n"], "true_risk": t["bad"],
            "precision": round(t["bad"] / t["n"], 4) if t["n"] else 0.0,
            "share_of_risk": round(t["bad"] / max(1, total_bad), 4),
            "threshold": THRESHOLDS.get(k),
        })

    # 4) 账号级聚合（同账号多帖时，一次命中不应只算"一条内容违规"）
    acc = {}
    for r in detail:
        a = acc.setdefault(r["account"], {"scores": [], "labels": []})
        a["scores"].append(r["p"])
        a["labels"].append(1 if r["label"] == "risk" else 0)
    ay, ap = [], []
    for a, v in acc.items():
        hits = sum(1 for x in v["scores"] if x >= final_thr)
        s = min(1.0, max(v["scores"]) + 0.12 * (hits - 1))
        ay.append(1 if max(v["labels"]) else 0)
        ap.append(s)
    acc_metric = prf(ay, ap, final_thr)
    acc_metric["accounts"] = len(acc)
    acc_metric["multi_post_accounts"] = sum(1 for v in acc.values() if len(v["scores"]) > 1)

    # 5) 图像侧
    t1 = time.time()
    train_img_rows, Xtr, test_img_rows, Xte = image_dataset_features()
    ytr = np.array([1 if r["label"] == "tampered" else 0 for r in train_img_rows])
    yte = np.array([1 if r["label"] == "tampered" else 0 for r in test_img_rows])

    ela_idx = [FEATURE_NAMES.index(n) for n in ("ela_p99_med", "ela_local_contrast",
                                               "ela_local_dip", "ela_tail", "ela_cv")]
    noise_idx = [FEATURE_NAMES.index(n) for n in ("noise_cv", "noise_p99_med",
                                                  "noise_local_drop", "noise_dip_area")]
    extra_idx = [FEATURE_NAMES.index(n) for n in ("edge_err_corr", "green_ratio")]
    candidates = [
        ("仅 ELA 特征（经典手法）", ela_idx),
        ("仅噪声残差特征", noise_idx),
        ("噪声 + 全局辅助特征", noise_idx + extra_idx),
        ("ELA + 噪声 + 辅助（全部）", list(range(len(FEATURE_NAMES)))),
    ]
    cv_rows, cv_best = [], (-1, None)
    for tag, idx in candidates:
        mu_a, sd_a = kfold_auc(Xtr, ytr, idx)
        cv_rows.append({"tag": tag, "cv_auc_mean": round(mu_a, 4),
                        "cv_auc_std": round(sd_a, 4), "n_features": len(idx),
                        "features": [FEATURE_NAMES[i] for i in idx]})
        if mu_a > cv_best[0]:
            cv_best = (mu_a, idx, tag)
    chosen_idx = cv_best[1]
    print(f"[cv] 选定特征集：{cv_best[2]}（CV AUC={round(cv_best[0],4)}）")

    img_model, mu, sd = train_image_model(Xtr, ytr, chosen_idx)
    img_train_sec = round(time.time() - t1, 2)
    p_img = img_model.proba((Xte[:, chosen_idx] - mu) / sd)
    img_m = prf(yte, p_img, 0.5)
    img_m["auc"] = auc(yte, p_img)
    img_m["chosen_features"] = [FEATURE_NAMES[i] for i in chosen_idx]

    kinds = [r["kind"] for r in test_img_rows]
    per_kind = {}
    for kind in ("pristine", "clean_resaved", "copy_move", "splice"):
        idx = [i for i, k in enumerate(kinds) if k == kind]
        p = p_img[idx]
        if kind in ("pristine", "clean_resaved"):
            per_kind[kind] = {"误报率": round(float((p >= 0.5).mean()), 4), "n": len(idx)}
        else:
            per_kind[kind] = {"召回率": round(float((p >= 0.5).mean()), 4), "n": len(idx)}

    loc = []
    for r in test_img_rows:
        if r["bbox"]:
            _, _, heat = extract(load_image(r["path"]))
            loc.append(iou_with_bbox(heat, r["bbox"]))
    img_m["localization"] = {"mean_hit": round(float(np.mean(loc)), 4),
                             "hit_over_30pct": round(float(np.mean([h >= 0.3 for h in loc])), 4)}

    # 特征消融（统一在训练集重训 / 测试集评，避免数据泄漏）
    feat_abl = []
    for tag, idx in candidates:
        m2, mu2, sd2 = train_image_model(Xtr, ytr, idx)
        p2 = m2.proba((Xte[:, idx] - mu2) / sd2)
        mm = prf(yte, p2, 0.5)
        mm["auc"] = auc(yte, p2)
        mm["tag"] = tag
        mm["n_features"] = len(idx)
        feat_abl.append(mm)

    t = time.perf_counter()
    for r in test_img_rows[:20]:
        extract(load_image(r["path"]))
    img_ms = round((time.perf_counter() - t) / 20 * 1000, 1)
    img_m["latency_ms"] = img_ms

    # 6) 输出
    bundle = build_bundle({
        "threshold": final_thr,
        "model": models["hybrid"],
        "image_model": {
            "features": [FEATURE_NAMES[i] for i in chosen_idx],
            "w": [round(float(v), 4) for v in img_model.w],
            "b": round(float(img_model.b), 4),
            "mu": [round(float(v), 5) for v in mu],
            "sd": [round(float(v), 5) for v in sd],
            "threshold": 0.5,
        },
    })
    save_bundle(bundle)

    # 抽样（每个"类别 × 判定结果"取一条，供报告展示证据链）
    samples, seen = [], set()
    for r in detail:
        key = (r["source_class"], r["p"] >= final_thr)
        if key in seen:
            continue
        seen.add(key)
        samples.append({
            "id": r["id"], "text": r["text"], "norm": r["norm"], "trace": r["trace"],
            "label": r["label"], "source_class": r["source_class"],
            "p": r["p"], "p_model": r["p_model"], "p_rule": r["p_rule"],
            "action": r["action"], "action_cn": r["action_cn"],
            "recovered": r.get("recovered", []),
            "hits": [h for h in r["hits"] if h["class"] != "context_neg"][:6],
            "neg_hits": [h for h in r["hits"] if h["class"] == "context_neg"][:4],
            "perturb_ops": r["perturb_ops"],
        })

    metrics = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": {k: {"n": len(v), "risk": sum(1 for r in v if r["label"] == "risk")}
                    for k, v in splits.items()},
        "text": {
            "threshold": final_thr,
            "final_config": configs[FINAL_IDX]["config"],
            "tier_table": tier_rows,
            "configs": configs,
            "operating_points": ops,
            "per_class_recall": per_class,
            "benign_hit_rate": benign_kinds,
            "subtype_accuracy": round(sum(
                1 for r in detail if r["label"] == "risk"
                and r["subtype"] == r["source_class"])
                / max(1, sum(1 for r in detail if r["label"] == "risk")), 4),
            "explain_coverage": round(
                sum(1 for r in detail if r["p"] >= final_thr and r["hits"])
                / max(1, sum(1 for r in detail if r["p"] >= final_thr)), 4),
            "recovered_rate_adv": round(rec_hit_adv / max(1, len(adv_rows)), 4),
            "latency_ms": {"mean": round(float(np.mean(lat)), 3),
                           "p95": round(float(np.percentile(lat, 95)), 3)},
            "errors": {
                "fp": len(fp), "fn": len(fn), "total": len(wrong),
                "fp_examples": [{"text": r["text"], "p": r["p"],
                                 "hits": [h["term"] for h in r["hits"]][:4],
                                 "neg": r["neg"]} for r in fp[:6]],
                "fn_examples": [{"text": r["text"], "p": r["p"],
                                 "ops": r["perturb_ops"]} for r in fn[:6]],
            },
            "train_seconds": train_sec,
            "samples": samples,
        },
        "account": acc_metric,
        "image": {"metrics": img_m, "per_kind": per_kind, "feature_ablation": feat_abl,
                  "cv": cv_rows, "chosen": cv_best[2],
                  "train_seconds": img_train_sec, "n_test": int(len(yte)),
                  "n_train": int(len(ytr))},
    }
    with open(os.path.join(RESULT_DIR, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=1)

    print("\n=== 最终引擎（混合模型·归一化）===")
    for split in splits_to_eval:
        m = configs[FINAL_IDX][split]
        print(f"  {split:14s} P={m['precision']:.3f} R={m['recall']:.3f} "
              f"F1={m['f1']:.3f} FPR={m['fpr']:.3f} AUC={m['auc']:.3f}")
    print(f"  类目判别={metrics['text']['subtype_accuracy']}  "
          f"解释覆盖={metrics['text']['explain_coverage']}  "
          f"对抗集还原命中={metrics['text']['recovered_rate_adv']}  总错误={len(wrong)}")
    print(f"  延迟 mean={metrics['text']['latency_ms']['mean']}ms "
          f"p95={metrics['text']['latency_ms']['p95']}ms")
    print("  工作点：" + "  ".join(
        f"FPR≤{o['target_fpr']:.0%}→thr={o['threshold']} R={o['recall']:.3f}"
        for o in ops))
    for t in tier_rows:
        print(f"  [{t['tier']:6s}] n={t['n']:3d} 真违规={t['true_risk']:3d} "
              f"精确率={t['precision']:.3f} 占违规总量={t['share_of_risk']:.1%}")
    print(f"  账号级 F1={acc_metric['f1']} (账号 {acc_metric['accounts']}, "
          f"多帖账号 {acc_metric['multi_post_accounts']})")
    print(f"\n=== 图像侧 === AUC={img_m['auc']} F1={img_m['f1']} "
          f"定位命中率={img_m['localization']['mean_hit']} 单图={img_ms}ms")
    for k, v in per_kind.items():
        print(f"  {k:14s} {v}")
    for a in feat_abl:
        print(f"  [消融] {a['tag']:24s} AUC={a['auc']:.3f} F1={a['f1']:.3f}")
    print(f"\n[ok] metrics -> {os.path.join(RESULT_DIR, 'metrics.json')}")


if __name__ == "__main__":
    main()
