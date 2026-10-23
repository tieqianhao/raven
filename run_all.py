# -*- coding: utf-8 -*-
"""一键跑通全链路：造数 → 训练/评测 → 出报告 → 出 demo → 前后端对拍。

    python run_all.py

预期耗时：CPU 上约 60~90 秒（图像特征提取占大头）。
产物：
    artifacts/results/metrics.json   全部指标
    artifacts/models/engine.json     模型权重 + 词表（demo 直接吃这个）
    report.html                      评测报告
    demo/index.html                  可交互演示
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

STEPS = [
    ("生成文本数据集", ["data/gen_dataset.py"]),
    ("生成图像数据集", ["data/gen_images.py"]),
    ("训练 + 评测 + 消融", ["eval/run_eval.py"]),
    ("生成演示页面", ["tools/build_demo.py"]),
    ("前后端一致性对拍", ["tools/parity_test.py"]),
    ("生成评测报告", ["raven/report.py"]),
    ("demo 冒烟测试", ["tools/dom_smoke_test.js"], "node"),
]


def main():
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=ROOT)
    total = time.time()
    for i, step in enumerate(STEPS, 1):
        name, args = step[0], step[1]
        runner = step[2] if len(step) > 2 else None
        print(f"\n{'=' * 64}\n[{i}/{len(STEPS)}] {name}\n{'=' * 64}")
        t = time.time()
        if runner == "node":
            exe = shutil.which("node")
            if not exe:
                print("[跳过] 未找到 node，demo 冒烟测试跳过（不影响其余产物）")
                continue
            r = subprocess.run([exe] + args, cwd=ROOT, env=env)
        else:
            r = subprocess.run([PY] + args, cwd=ROOT, env=env)
        if r.returncode != 0:
            print(f"[失败] {name}（退出码 {r.returncode}）")
            return r.returncode
        print(f"[完成] {name}  {time.time() - t:.1f}s")
    print(f"\n全部完成，总耗时 {time.time() - total:.1f}s")
    print("看这两个文件：report.html（评测报告） / demo/index.html（可交互演示）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
