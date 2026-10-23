# -*- coding: utf-8 -*-
"""命令行入口。

用法：
    python -m raven.cli "出原神外挂，自瞄透视，稳定不封，加微信abc123"
    python -m raven.cli --file artifacts/dataset/text_test_adv.jsonl --limit 5
    python -m raven.cli --image artifacts/dataset/images/test_000_copy_move.jpg
    python -m raven.cli --demo
"""
import argparse
import json
import os
import sys

import numpy as np

from .image_forensics import FEATURE_NAMES, extract, load as load_image
from .pipeline import ART, ImageEngine, TextEngine, read_jsonl
from .text_model import HAND_DIM, LogisticModel


def load_engines():
    path = os.path.join(ART, "models", "engine.json")
    with open(path, encoding="utf-8") as f:
        b = json.load(f)
    tm = LogisticModel(b["dim"] + HAND_DIM)
    tm.w = np.array(b["text_model"]["w"], dtype=np.float32)
    tm.b = b["text_model"]["b"]
    text_engine = TextEngine(tm, threshold=b["threshold"])

    im = b["image_model"]
    idx = [FEATURE_NAMES.index(n) for n in im["features"]]
    model = LogisticModel(len(idx))
    model.w = np.array(im["w"], dtype=np.float32)
    model.b = im["b"]
    img_engine = ImageEngine(model, im["mu"], im["sd"], im["threshold"], feature_idx=idx)
    return text_engine, img_engine


def print_text(result, verbose=True):
    bar = "█" * int(result["p"] * 24)
    print(f"\n原文  {result['raw']}")
    if result["norm"] != result["raw"]:
        print(f"归一化 {result['norm']}")
        if result["recovered"]:
            print(f"      还原命中：{'、'.join(result['recovered'])}")
    print(f"判定  {result['p']:.3f} [{bar:<24}] → {result['action_cn']}"
          f"   类目：{result['subtype']}  规则分：{result['p_rule']:.2f}")
    if verbose and result["hits"]:
        ev = "、".join(f"{h['term']}({h['weight']})" for h in result["hits"][:5])
        print(f"证据  {ev}")
    if result.get("neg"):
        print(f"      否定语境信号 {result['neg']} —— 可能是在举报/提醒，而非广告")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Raven · 内容风控研判引擎")
    ap.add_argument("text", nargs="?", help="要检测的文本")
    ap.add_argument("--file", help="批量检测 jsonl 文件")
    ap.add_argument("--image", help="检测图片路径")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--demo", action="store_true", help="跑一组内置示例")
    args = ap.parse_args(argv)

    text_engine, img_engine = load_engines()

    if args.demo or (not args.text and not args.file and not args.image):
        demo = [
            "出原神外挂，自瞄透视锁头全功能，稳定不封，需要的加微信abc123",
            "出·原·神·外·挂·稳·定·不·封·加·薇·信",
            "出王\u200b者荣耀外褂，透視自瞄，包退",
            "今天在群里看见有人卖原神外挂，已经举报了",
            "求推荐原神里适合新手的角色，我现在只有三个五星",
            "刚抽到新角色，这版本强度怎么样，值得练吗",
        ]
        print("=" * 74)
        for t in demo:
            print_text(text_engine.score(t))
        print("\n" + "=" * 74)
        return

    if args.text:
        print_text(text_engine.score(args.text), verbose=True)

    if args.file:
        rows = read_jsonl(args.file)
        for r in rows[:args.limit]:
            res = text_engine.score(r["text"])
            mark = "✓" if (res["p"] >= text_engine.threshold) == (r["label"] == "risk") else "✗"
            print_text(res, verbose=False)
            print(f"      真值={r['label']}  判定={'违规' if res['p'] >= text_engine.threshold else '正常'} {mark}")

    if args.image:
        res = img_engine.score_path(args.image)
        heat = np.array(res["heat"])
        print(f"\n图片  {os.path.basename(args.image)}")
        print(f"判定  {res['p_image']:.3f} → {res['action_cn']}")
        print(f"热力图 {heat.shape[0]}x{heat.shape[1]} 块（区块字符示意）：")
        chars = " .:-=+*#%@"
        for row in heat:
            print("      " + "".join(chars[min(len(chars) - 1, int(v * (len(chars) - 1)))]
                                     for v in row))
        top = sorted(res["features"].items(), key=lambda kv: -abs(kv[1]))[:4]
        print("关键特征 " + "、".join(f"{k}={v}" for k, v in top))


if __name__ == "__main__":
    main(sys.argv[1:])
