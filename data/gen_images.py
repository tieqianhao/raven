# -*- coding: utf-8 -*-
"""生成图像侧数据集：程序化海报 + 两种篡改 + 一个"硬负样本"。

硬负样本（clean_resaved）是这个子任务里最容易被做假的地方：
如果只拿"原图"对"篡改图"，模型学到的其实是"有没有被二次压缩"，而不是"有没有被改过"。
所以这里刻意加入"干净但被重新保存"的图，逼模型去学真正的局部痕迹。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from raven.image_forensics import load, make_poster, save_jpeg, tamper

OUT = os.path.join(os.path.dirname(__file__), "..", "artifacts", "dataset", "images")


def build(split: str, n_base: int, seed0: int):
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for i in range(n_base):
        seed = seed0 + i
        base = make_poster(seed)
        donor = make_poster(seed + 5000)

        p = os.path.join(OUT, f"{split}_{i:03d}_pristine.jpg")
        save_jpeg(base, p, 88)
        rows.append({"path": p, "label": "clean", "kind": "pristine", "base": seed, "bbox": None})

        p = os.path.join(OUT, f"{split}_{i:03d}_resaved.jpg")
        save_jpeg(base, p, 92)
        rows.append({"path": p, "label": "clean", "kind": "clean_resaved", "base": seed, "bbox": None})

        for mode in ("copy_move", "splice"):
            t, bbox = tamper(base, seed + (11 if mode == "copy_move" else 23), mode, donor)
            p = os.path.join(OUT, f"{split}_{i:03d}_{mode}.jpg")
            save_jpeg(t, p, 92)
            rows.append({"path": p, "label": "tampered", "kind": mode, "base": seed, "bbox": bbox})
    return rows


def main():
    train = build("train", 46, 1000)
    test = build("test", 26, 9000)
    for name, rows in (("train", train), ("test", test)):
        with open(os.path.join(OUT, f"images_{name}.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        kinds = {}
        for r in rows:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        print(f"{name}: {len(rows)} 张 {kinds}")


if __name__ == "__main__":
    main()
