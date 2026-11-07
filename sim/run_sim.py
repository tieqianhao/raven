# -*- coding: utf-8 -*-
"""在模拟环境里跑一遍真实引擎，产出可复现的运营指标。

和 eval/run_eval.py 的区别：
- 评测回答"模型准不准"，模拟回答"上线后运营会长什么样"——
  审核队列会不会积压、拦截档每分钟误伤几条、单条漏检能不能靠账号聚合兜住。
- 判定用的是同一个 TextEngine、同一份 engine.json、同一个阈值，
  所以三波攻击的 F1 必须与 artifacts/results/metrics.json 逐位一致。
  这个对账在末尾自动做，差一点就报警——模拟环境不允许自己编数字。

用法：
    python sim/run_sim.py                  # 完整跑一遍并生成看板
    python sim/run_sim.py --no-dashboard   # 只要指标
    python sim/run_sim.py --review-capacity 6   # 换审核产能
"""
import argparse
import json
import os
import statistics
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from raven.cli import load_engines                      # noqa: E402
from raven.lexicon import THRESHOLDS                   # noqa: E402
from raven.pipeline import ART, text_splits            # noqa: E402

from sim import scenario                               # noqa: E402

SIM_OUT = os.path.join(ART, "sim")

# 运营侧假设：显式写出来，看板页脚同步展示，避免被当成实测值
REVIEW_CAPACITY = 12.0   # 条/分钟（一名审核员 5 秒/条）
SAMPLE_RATE = 0.10       # 观察档抽检比例


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 4), round(r, 4), round(f, 4)


def score_all(events):
    """逐条过引擎，顺带量真实耗时。

    hit 用引擎自带的二分类阈值（0.38），与 eval/run_eval.py 完全一致；
    act 用四档处置阈值（0.82 / 0.68 / 0.40）。
    两者之间 0.38~0.40 有个缝：模型说"这是违规"，处置层却说"放行"。
    这个缝是真实存在的，单独计数，不藏起来。
    """
    engine, _ = load_engines()
    thr = engine.threshold
    out = []
    for i, e in enumerate(events):
        row = e["row"]
        t0 = time.perf_counter()
        res = engine.score(row["text"], explain=True)
        ms = (time.perf_counter() - t0) * 1000.0
        out.append({
            "i": i,
            "t": e["t"],
            "ts": e["ts"],
            "w": e["wave"],
            "id": row["id"],
            "acct": row.get("account", "?"),
            "raw": res["raw"],
            "norm": res["norm"],
            "p": res["p"],
            "pr": res["p_rule"],
            "act": res["action"],
            "hit": res["p"] >= thr,
            "sub": res["subtype"],
            "label": row["label"],
            "ops": row.get("perturb_ops") or [],
            "hits": [h["term"] for h in (res["hits"] or [])][:4],
            "rec": (res.get("recovered") or [])[:3],
            "neg": res.get("neg") or 0,
            "ms": round(ms, 3),
        })
    return out


def wave_stats(records, keys, names):
    """分类口径 = p ≥ 引擎阈值，与评测逐位同源。"""
    rows = []
    for key in keys:
        sub = [r for r in records if r["w"] == key]
        tp = sum(1 for r in sub if r["hit"] and r["label"] == "risk")
        fp = sum(1 for r in sub if r["hit"] and r["label"] == "benign")
        fn = sum(1 for r in sub if not r["hit"] and r["label"] == "risk")
        tn = sum(1 for r in sub if not r["hit"] and r["label"] == "benign")
        p, rec, f1 = prf(tp, fp, fn)
        lat = [r["ms"] for r in sub]
        rows.append({
            "key": key,
            "name": names[key],
            "n": len(sub),
            "risk": tp + fn,
            "benign": fp + tn,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": p, "recall": rec, "f1": f1,
            "latency_p50": round(statistics.median(lat), 3),
            "latency_p95": round(sorted(lat)[int(len(lat) * 0.95) - 1], 3),
        })
    return rows


def tier_stats(records):
    names = {"block": "拦截", "review": "限流 + 人工复核", "watch": "观察（抽检）", "allow": "放行"}
    total_risk = sum(1 for r in records if r["label"] == "risk")
    out = []
    for tier in ("block", "review", "watch", "allow"):
        sub = [r for r in records if r["act"] == tier]
        hit = sum(1 for r in sub if r["label"] == "risk")
        out.append({
            "tier": tier,
            "name": names[tier],
            "n": len(sub),
            "true_risk": hit,
            "precision": round(hit / len(sub), 4) if sub else 0.0,
            "share_of_risk": round(hit / total_risk, 4) if total_risk else 0.0,
            "threshold": THRESHOLDS.get(tier),
        })
    return out


def _stable_hash(s):
    """确定性哈希：Python 内置 hash() 对 str 每个进程都加盐，跨次运行会变。"""
    return zlib.crc32(s.encode("utf-8"))


def ops_stats(records, capacity, sample_rate):
    """审核队列：单工位、先到先审、限产能；观察档按比例抽样进人工。"""
    arrivals = []
    sampled_watch = 0
    for r in records:
        if r["act"] == "review":
            arrivals.append(r["t"])
        elif r["act"] == "watch":
            if _stable_hash(r["id"]) % 100 < int(sample_rate * 100):
                arrivals.append(r["t"])
                sampled_watch += 1
    arrivals.sort()

    service = 1.0 / capacity
    free_at = 0.0
    waits = []
    ends = []
    for t in arrivals:
        if free_at < t:
            free_at = t
        waits.append(free_at - t)
        free_at += service
        ends.append(free_at)

    # 峰值积压：第 i 条到达时，前面还有多少条没审完（只看已到达的，不能算未来）
    peak = 0
    j = 0
    for i, t in enumerate(arrivals):
        while j < i and ends[j] <= t:
            j += 1
        peak = max(peak, i - j)

    sim_minutes = max((r["t"] for r in records), default=0) or 1
    total_review = len(arrivals)
    capacity_total = capacity * sim_minutes
    return {
        "review_arrivals": total_review,
        "from_review": total_review - sampled_watch,
        "from_watch_sampling": sampled_watch,
        "review_rate": round(total_review / len(records), 4),
        "peak_backlog": peak,
        "avg_wait_min": round(statistics.mean(waits), 3) if waits else 0.0,
        "p95_wait_min": round(sorted(waits)[int(len(waits) * 0.95) - 1], 3) if waits else 0.0,
        "max_wait_min": round(max(waits), 3) if waits else 0.0,
        "capacity": capacity,
        "utilization": round(total_review / capacity_total, 4) if capacity_total else 0.0,
        "sample_rate": sample_rate,
    }


def account_stats(records, thr):
    """账号级聚合：单条漏检能否被同账号的其他帖子兜住。

    注意读法：模拟里每个账号平均发 6.6 条，池化后账号级等价于"多次投票"，
    所以这个 F1 会显著高于单测试集口径（EVAL 里是 0.9512）。
    它说明的是"账号重复发帖时聚合很有效"，不说明"模型更强"。
    """
    groups = {}
    for r in records:
        groups.setdefault(r["acct"], []).append(r)

    tp = fp = fn = tn = 0
    caught_by_account = 0
    missed_posts = [r for r in records if r["label"] == "risk" and not r["hit"]]
    rows = []
    for acct, posts in groups.items():
        max_p = max(p["p"] for p in posts)
        flagged = max_p >= thr
        is_risk = any(p["label"] == "risk" for p in posts)
        if flagged and is_risk:
            tp += 1
        elif flagged and not is_risk:
            fp += 1
        elif not flagged and is_risk:
            fn += 1
        else:
            tn += 1
        blocked = [p for p in posts if p["act"] == "block"]
        reviews = [p for p in posts if p["act"] == "review"]
        if blocked:
            policy = "封禁"
        elif len(reviews) >= 2:
            policy = "禁言 7 天"
        elif max_p >= THRESHOLDS["watch"]:
            policy = "观察"
        else:
            policy = "正常"
        rows.append({
            "acct": acct,
            "posts": len(posts),
            "max_p": max_p,
            "hits": len(blocked) + len(reviews),
            "risk_posts": sum(1 for p in posts if p["label"] == "risk"),
            "policy": policy,
        })

    # 单条漏检，但该账号被同账号的其他帖子抓住了
    by_acct = {r["acct"]: r for r in rows}
    for r in missed_posts:
        if by_acct[r["acct"]]["max_p"] >= thr:
            caught_by_account += 1

    p, rec, f1 = prf(tp, fp, fn)
    rows.sort(key=lambda x: (-x["hits"], -x["max_p"]))
    single = sum(1 for r in missed_posts if len(groups[r["acct"]]) == 1)
    return {
        "accounts": len(groups),
        "multi_post_accounts": sum(1 for v in groups.values() if len(v) > 1),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": p, "recall": rec, "f1": f1,
        "missed_posts": len(missed_posts),
        "missed_from_single_post_account": single,
        "rescued_by_account": caught_by_account,
        "rescued_rate": round(caught_by_account / len(missed_posts), 4) if missed_posts else 0.0,
        "top": rows[:8],
        "policy_count": {k: sum(1 for r in rows if r["policy"] == k)
                         for k in ("封禁", "禁言 7 天", "观察", "正常")},
    }


def reconcile(waves):
    """与评测结果对账：同一模型同一数据，F1 必须逐位一致。

    顺带把评测自己测到的时延读出来，避免文档里写死一个会变的值。
    """
    path = os.path.join(ART, "results", "metrics.json")
    if not os.path.exists(path):
        return {"ok": None, "rows": [], "note": "未找到 metrics.json，跳过对账"}
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    base = m["text"]["configs"][-1]
    rows = []
    ok = True
    for w in waves:
        if w["key"] == "clean":
            continue
        eval_f1 = base[f"test_{w['key']}"]["f1"]
        diff = round(w["f1"] - eval_f1, 6)
        if abs(diff) > 1e-9:
            ok = False
        rows.append({
            "wave": w["name"],
            "key": w["key"],
            "sim_f1": w["f1"],
            "eval_f1": eval_f1,
            "diff": diff,
        })
    return {
        "ok": ok,
        "rows": rows,
        "baseline_config": base["config"],
        "eval_latency_ms": m["text"].get("latency_ms", {}),
        "note": "",
    }


def print_report(scn, waves, tiers, ops, accts, lat, rec, thr, gap):
    line = "=" * 78
    print(line)
    print("Raven · 模拟环境演练")
    print(f"{scn['title']}  |  {scn['t0']} 起 {scn['total_min']} 分钟")
    print(line)

    print(f"\n【波次】分类口径 p≥{thr}（与评测同源），三套变形测试集依次推进")
    print(f"{'波次':<22}{'条数':>6}{'违规':>6}{'精确率':>9}{'召回':>8}{'F1':>8}{'评测F1':>9}{'差':>11}")
    eval_map = {r["key"]: r["eval_f1"] for r in rec.get("rows", [])}
    for w in waves:
        e = eval_map.get(w["key"])
        eval_txt = f"{e:.4f}" if e is not None else "—"
        diff_txt = f"{w['f1'] - e:+.6f}" if e is not None else "—"
        print(f"{w['name']:<22}{w['n']:>6}{w['risk']:>6}{w['precision']:>9.4f}"
              f"{w['recall']:>8.4f}{w['f1']:>8.4f}{eval_txt:>9}{diff_txt:>11}")

    print("\n【处置分档】全量 1080 条池化（EVAL 的 151 条拦截档是 test_adv 单集口径）")
    for t in tiers:
        thr_txt = f"p≥{t['threshold']}" if t["threshold"] is not None else "其余"
        print(f"  {t['name']:<16}{thr_txt:<8} {t['n']:>4} 条  其中违规 {t['true_risk']:>3}"
              f"  精确率 {t['precision']:.3f}  覆盖违规 {t['share_of_risk']*100:>5.1f}%")
    if gap:
        print(f"  ⚠ 另有 {gap} 条 p 落在 {thr}~0.40 之间：模型判违规，处置层却落进放行档。")
        print("    这是二分类阈值(0.38)与分档阈值(0.40)不接缝造成的，见 sim/README.md")

    print("\n【审核队列】单工位 · 先到先审 · 产能 %.0f 条/分钟" % ops["capacity"])
    print(f"  进审 {ops['review_arrivals']} 条（复核档 {ops['from_review']} + 观察档抽检 {ops['from_watch_sampling']}）"
          f"  占全量 {ops['review_rate']*100:.1f}%")
    print(f"  峰值积压 {ops['peak_backlog']} 条 · 平均等待 {ops['avg_wait_min']:.2f} 分钟"
          f" · P95 等待 {ops['p95_wait_min']:.2f} 分钟 · 工位利用率 {ops['utilization']*100:.0f}%")

    print("\n【账号级】跨波次池化，每个账号平均 6.6 条，等价于多次投票，故高于单集口径的 0.9512")
    print(f"  {accts['accounts']} 个账号，其中 {accts['multi_post_accounts']} 个发过多条")
    print(f"  账号级 P={accts['precision']:.4f} R={accts['recall']:.4f} F1={accts['f1']:.4f}")
    print(f"  单条漏检 {accts['missed_posts']} 条，其中 {accts['rescued_by_account']} 条的同账号被其他帖子拉黑"
          f"（兜底率 {accts['rescued_rate']*100:.1f}%）")
    if accts["missed_from_single_post_account"] == 0:
        print("  ↑ 这个 100% 没有区分度：本题漏检全部来自发帖 ≥2 条的账号，")
        print("    账号聚合必然兜住。换成'一账号只发一条'的真实分布，兜底率会归零。")
    print("  处置：" + " / ".join(f"{k} {v}" for k, v in accts["policy_count"].items()))

    print("\n【时延】单条端到端（归一化 + 特征 + 打分），已丢弃前 20 条预热")
    print(f"  p50 {lat['p50']:.3f} ms · p95 {lat['p95']:.3f} ms · 均值 {lat['mean']:.3f} ms"
          f" · 冷启动首条 {lat['cold_first']:.3f} ms")
    el = rec.get("eval_latency_ms") or {}
    if el:
        print(f"  评测里记的是 mean={el.get('mean')} ms / p95={el.get('p95')} ms —— 训练后立刻采 120 条，"
              f"含冷启动，口径不同不可直接比。")
    print("  这类数只能看量级，对外说『0.2~0.5 ms 取决于机器与预热』，不要报三位小数。")

    print("\n【对账】模拟结果 vs artifacts/results/metrics.json")
    if rec["ok"] is None:
        print("  " + rec["note"])
    else:
        print(f"  基准配置：{rec['baseline_config']}")
        for r in rec["rows"]:
            flag = "OK" if abs(r["diff"]) < 1e-9 else "不符"
            print(f"  {r['wave']:<16} 模拟 {r['sim_f1']:.4f} / 评测 {r['eval_f1']:.4f}"
                  f"  差 {r['diff']:+.6f}  [{flag}]")
        print("  " + ("三波全部逐位一致，模拟环境没有引入任何自有数字。"
                      if rec["ok"] else "存在差异，模拟链路与评测链路不一致，需排查。"))
    print(line)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Raven 模拟环境演练")
    ap.add_argument("--out", default=SIM_OUT)
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--review-capacity", type=float, default=REVIEW_CAPACITY,
                    help="审核产能，条/分钟")
    ap.add_argument("--sample-rate", type=float, default=SAMPLE_RATE,
                    help="观察档抽检比例")
    args = ap.parse_args(argv)

    splits = text_splits()
    scn = scenario.meta()
    events = scenario.build(splits)

    print(f"编排完成：{len(events)} 条内容，{scn['total_min']} 分钟，"
          f"{len(scn['waves'])} 个攻击波次 + 基线流量")

    t0 = time.perf_counter()
    records = score_all(events)
    total_s = time.perf_counter() - t0

    engine, _ = load_engines()
    thr = engine.threshold

    names = {w["key"]: w["name"] for w in scn["waves"]}
    names["clean"] = scn["baseline"]["name"]
    keys = ["clean"] + [w["key"] for w in scn["waves"]]

    waves = wave_stats(records, keys, names)
    tiers = tier_stats(records)
    ops = ops_stats(records, args.review_capacity, args.sample_rate)
    accts = account_stats(records, thr)
    gap = sum(1 for r in records if r["hit"] and r["act"] == "allow")

    # 丢掉前 20 条预热：首次调用要建特征缓存，混进统计会把均值拉高
    lat = [r["ms"] for r in records[20:]]
    latency = {
        "p50": round(statistics.median(lat), 3),
        "p95": round(sorted(lat)[int(len(lat) * 0.95) - 1], 3),
        "mean": round(statistics.mean(lat), 3),
        "cold_first": records[0]["ms"],
        "total_s": round(total_s, 2),
        "note": "预热 20 条后统计；评测是训练后立刻采 120 条（含冷启动），口径不同不可直接比",
    }
    rec = reconcile(waves)

    metrics = {
        "scenario": scn,
        "engine": {
            "threshold": thr,
            "tiers": THRESHOLDS,
            "source": "artifacts/models/engine.json",
        },
        "assumptions": {
            "review_capacity_per_min": args.review_capacity,
            "sample_rate": args.sample_rate,
        },
        "n": len(records),
        "judged_but_allowed": gap,
        "waves": waves,
        "tiers": tiers,
        "ops": ops,
        "accounts": accts,
        "latency": latency,
        "reconcile": rec,
    }

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "events.jsonl"), "w", encoding="utf-8", newline="") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(args.out, "sim_metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=1)

    print_report(scn, waves, tiers, ops, accts, latency, rec, thr, gap)
    print(f"\n产物：{os.path.relpath(os.path.join(args.out, 'events.jsonl'), ROOT)}"
          f" · {os.path.relpath(os.path.join(args.out, 'sim_metrics.json'), ROOT)}")

    if not args.no_dashboard:
        from sim import build_dashboard
        path = build_dashboard.render(metrics, records, os.path.join(args.out, "dashboard.html"))
        print(f"看板：{os.path.relpath(path, ROOT)}")

    return 0 if rec["ok"] is not False else 1


if __name__ == "__main__":
    sys.exit(main())
