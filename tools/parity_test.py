# -*- coding: utf-8 -*-
"""前后端一致性对拍（parity test）。

为什么要有这个：demo 声称"页面里跑的就是同一套模型"。这句话如果不验证，
它就是一个营销话术。这个脚本把 200+ 条真实样本同时喂给
  (a) Python 引擎（raven/），(b) 浏览器内核（demo/raven-lite.js，用 Node 跑），
逐条比概率，输出最大绝对误差。

跑法：python tools/parity_test.py
"""
import json
import os
import random
import shutil
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from raven.pipeline import ART, TextEngine, read_jsonl
from raven.text_model import HAND_DIM, LogisticModel


def python_engine(bundle):
    m = LogisticModel(bundle["dim"] + HAND_DIM)
    m.w = np.array(bundle["text_model"]["w"], dtype=np.float32)
    m.b = bundle["text_model"]["b"]
    return TextEngine(m, threshold=bundle["threshold"])


def collect_texts(n=220):
    rng = random.Random(7)
    texts = []
    for split in ("test_clean", "test_adv", "test_pervasive", "test_holdout"):
        rows = read_jsonl(os.path.join(ART, "dataset", f"text_{split}.jsonl"))
        texts += [r["text"] for r in rng.sample(rows, min(55, len(rows)))]
    # 再加几条人为构造的刁钻输入
    texts += [
        "出·原·神·外·挂·加·薇·信",
        "外\u200b挂 自瞄 包退",
        "ＣＳ２ 出号 走平台",
        "今天看到有人卖外挂，举报了",
        "求推荐新手角色",
    ]
    return texts


def main():
    with open(os.path.join(ART, "models", "engine.json"), encoding="utf-8") as f:
        bundle = json.load(f)
    eng = python_engine(bundle)
    texts = collect_texts()

    payload = {"cfg": bundle, "texts": texts}
    tmp_in = os.path.join(ART, "parity_in.json")
    tmp_out = os.path.join(ART, "parity_out.json")
    with open(tmp_in, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    node = shutil.which("node")
    if node is None:
        # 常见兜底位置（不同机器/不同安装方式），仍然找不到就跳过对拍
        for cand in (
            r"C:\Program Files\nodejs\node.exe",
            "/usr/local/bin/node",
            "/usr/bin/node",
        ):
            if os.path.exists(cand):
                node = cand
                break
    if node is None:
        print("未找到 node，跳过对拍（不影响其余产物，但 demo 的一致性未被验证）")
        for p in (tmp_in, tmp_out):
            if os.path.exists(p):
                os.remove(p)
        return
    r = subprocess.run([node, os.path.join(ROOT, "tools", "parity_node.js"), tmp_in, tmp_out],
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        print("Node 执行失败，跳过对拍")
        return

    with open(tmp_out, encoding="utf-8") as f:
        js = json.load(f)

    diffs, ndiff_norm, worst = [], 0, None
    for t, j in zip(texts, js):
        py = eng.score(t, explain=False)
        d = abs(py["p"] - j["p"])
        diffs.append(d)
        if py["norm"] != j["norm"]:
            ndiff_norm += 1
            if worst is None:
                worst = (t, py["norm"], j["norm"])
    diffs = np.array(diffs)
    print(f"样本数 {len(texts)} ｜ 最大概率差 {diffs.max():.6f} ｜ 平均 {diffs.mean():.8f}")
    print(f"归一化结果不一致 {ndiff_norm} 条")
    if worst:
        print("  首条差异：", worst)
    ok = diffs.max() < 5e-3 and ndiff_norm == 0
    print("对拍结论：" + ("通过（前端与后端是同一套判定）" if ok else "未通过，需要修"))
    os.remove(tmp_in)
    os.remove(tmp_out)
    return ok


if __name__ == "__main__":
    main()
