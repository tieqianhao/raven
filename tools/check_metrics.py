# -*- coding: utf-8 -*-
"""指标回归闸门（metric gate）。

为什么需要它：这个项目里所有结论都是数字，README 上的 F1、报告里的消融矩阵、
简历上的每一行，全部来自 `artifacts/results/metrics.json`。如果某次改动让某个
指标悄悄变了 0.02，报告和 README 就成了假的——而且不会有任何报错。

这个脚本把**质量类指标**钉成基线，跑完 `run_all.py` 后逐一比对，超出容差即非零退出。
CI 里跑它，等于给"我说过的数字"上了一道回归测试。

刻意**不**校验的字段（它们本来就不该稳定，钉住只会制造假警报）：
    generated_at    时间戳
    *latency_ms*    单条推理耗时，依赖机器负载
    *train_seconds* 训练耗时，同上

用法：
    python tools/check_metrics.py                 # 比对，默认容差 1e-3
    python tools/check_metrics.py --tol 5e-3      # 跨平台/跨环境时放宽（CI 用这个）
    python tools/check_metrics.py --update        # 把当前结果写回基线（确认是有意改动后再用）

关于容差：模型训练里有 `x @ w`，走 BLAS。同一平台逐位可复现，但不同 OS 的
OpenBLAS 在浮点累加顺序上可能有 1e-7 级差异，经 SGD 迭代可能翻转极个别样本。
所以容差不是"随便给的宽松度"，而是"这台机器与基线机器的环境差异预算"。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS = os.path.join(ROOT, "artifacts", "results", "metrics.json")
BASELINE = os.path.join(ROOT, "artifacts", "results", "baseline.json")

# 基线：字段路径 → 期望值
# 路径用 "/" 分隔，列表用下标。末尾的 [0] 表示 configs 里最后一项（最终配置）。
BASELINE_SPEC = {
    # ---- 数据规模（造数是确定性的，规模不该变）----
    "dataset/train/n": 660,
    "dataset/test_clean/n": 270,
    "dataset/test_adv/n": 270,
    "dataset/test_pervasive/n": 270,
    "dataset/test_holdout/n": 270,

    # ---- 最终配置（⑤ 混合模型）在四套测试集上的表现 ----
    "text/threshold": 0.38,
    "text/configs/[-1]/test_clean/f1": 0.9431,
    "text/configs/[-1]/test_clean/precision": 0.9206,
    "text/configs/[-1]/test_clean/recall": 0.9667,
    "text/configs/[-1]/test_adv/f1": 0.9297,
    "text/configs/[-1]/test_adv/precision": 0.9053,
    "text/configs/[-1]/test_adv/recall": 0.9556,
    "text/configs/[-1]/test_pervasive/f1": 0.9505,
    "text/configs/[-1]/test_pervasive/precision": 0.9402,
    "text/configs/[-1]/test_pervasive/recall": 0.9611,
    "text/configs/[-1]/test_holdout/f1": 0.9456,
    "text/configs/[-1]/test_holdout/precision": 0.9763,
    "text/configs/[-1]/test_holdout/recall": 0.9167,

    # ---- 对抗能力的关键证据：规则层 vs 混合模型 ----
    # 逐字符分隔集上，规则层 F1 = 0（一条都检不出）
    "text/configs/[0]/test_pervasive/f1": 0.0,
    # 归一化让规则层从 0 回到 0.889（即 0.8889）
    "text/configs/[1]/test_pervasive/f1": 0.8889,

    # ---- 分档处置 ----
    "text/tier_table/[0]/n": 151,          # block 档条数
    "text/tier_table/[0]/precision": 1.0,  # 拦截精确率 100%
    "text/tier_table/[0]/share_of_risk": 0.8389,
    "text/tier_table/[1]/share_of_risk": 0.0611,

    # ---- 账号级聚合 ----
    "account/f1": 0.9512,
    "account/precision": 0.9435,
    "account/recall": 0.959,
    "account/accounts": 163,

    # ---- 图像侧 ----
    "image/metrics/auc": 0.9734,
    "image/metrics/f1": 0.8632,
    "image/metrics/localization/mean_hit": 0.3042,
}


def resolve(obj, path):
    """按 'a/b/[0]/c' 取嵌套值，支持 [-1] 取末项。"""
    cur = obj
    for seg in path.split("/"):
        if seg.startswith("[") and seg.endswith("]"):
            idx = int(seg[1:-1])
            cur = cur[idx]
        else:
            cur = cur[seg]
    return cur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=1e-3, help="允许的绝对误差（默认 1e-3）")
    ap.add_argument("--update", action="store_true", help="把当前结果写成新基线")
    args = ap.parse_args()

    if not os.path.exists(METRICS):
        print(f"[失败] 找不到 {METRICS}，请先跑 python run_all.py")
        return 2

    with open(METRICS, encoding="utf-8") as f:
        m = json.load(f)

    # --update：从当前 metrics.json 重新采样一份基线快照
    if args.update:
        snap = {}
        for path in BASELINE_SPEC:
            try:
                snap[path] = resolve(m, path)
            except Exception as e:
                print(f"[跳过] {path} → {e}")
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        print(f"基线已更新：{BASELINE}（{len(snap)} 项）")
        print("注意：确认这是有意的改动，再提交这个文件。")
        return 0

    rows, bad = [], []
    for path, expect in BASELINE_SPEC.items():
        try:
            got = resolve(m, path)
        except Exception as e:
            rows.append((path, expect, f"取不到 ({type(e).__name__})", "缺失"))
            bad.append(path)
            continue
        if isinstance(got, (int, float)) and isinstance(expect, (int, float)):
            diff = abs(float(got) - float(expect))
            ok = diff <= args.tol
            rows.append((path, expect, got, "OK" if ok else f"漂移 {diff:.6f}"))
            if not ok:
                bad.append(path)
        else:
            ok = got == expect
            rows.append((path, expect, got, "OK" if ok else "不一致"))
            if not ok:
                bad.append(path)

    w = max(len(r[0]) for r in rows)
    print(f"指标回归比对  容差 {args.tol}  共 {len(rows)} 项")
    print("-" * (w + 40))
    for path, expect, got, status in rows:
        mark = "  " if status == "OK" else "✗ "
        print(f"{mark}{path:<{w}}  期望 {expect!s:<10} 实测 {got!s:<12} {status}")
    print("-" * (w + 40))

    if bad:
        print(f"[失败] {len(bad)} 项漂移：")
        for p in bad:
            print(f"   - {p}")
        print("\n如果是无意的，回去查改动；如果是有意的（换了特征 / 改了数据），")
        print("先跑 --update 记下新基线，再同步改 README 与 docs/EVAL.md 里的数字。")
        return 1

    print(f"[通过] {len(rows)} 项全部在容差内，质量指标可复现。")
    print("（耗时类字段刻意不校验：它们依赖机器负载，见 docs/EVAL.md）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
