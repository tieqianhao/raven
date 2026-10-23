# -*- coding: utf-8 -*-
"""一键跑通全链路：造数 → 训练/评测 → 出报告 → 出 demo → 前后端对拍。

    python run_all.py                # 完整链路，含重新造数据
    python run_all.py --skip-gen     # 跳过造数，直接用 artifacts/dataset 里已提交的数据集
    python run_all.py --only image   # 只跑含 image 字样的步骤（调试用）

预期耗时：CPU 上约 60~90 秒（图像特征提取占大头）。
产物：
    artifacts/results/metrics.json   全部指标
    artifacts/models/engine.json     模型权重 + 词表（demo 直接吃这个）
    report.html                      评测报告
    demo/index.html                  可交互演示

关于 --skip-gen：图像数据集是 Pillow 画出来的 JPEG。不同平台的 libjpeg
编码参数不完全一致，重新造图会得到像素级不同的输入，进而让图像侧指标漂移
（实测 Windows → Ubuntu 上 F1 从 0.863 变成 0.951）。仓库里提交的数据集是
固定的，CI 用 --skip-gen 才能验证"同一份数据，同一套指标"。
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# gen=True 表示"造数据"步骤，可被 --skip-gen 跳过
STEPS = [
    {"name": "生成文本数据集", "cmd": ["data/gen_dataset.py"], "gen": True},
    {"name": "生成图像数据集", "cmd": ["data/gen_images.py"], "gen": True},
    {"name": "训练 + 评测 + 消融", "cmd": ["eval/run_eval.py"], "gen": False},
    {"name": "生成演示页面", "cmd": ["tools/build_demo.py"], "gen": False},
    {"name": "前后端一致性对拍", "cmd": ["tools/parity_test.py"], "gen": False},
    {"name": "生成评测报告", "cmd": ["raven/report.py"], "gen": False},
    {"name": "demo 冒烟测试", "cmd": ["tools/dom_smoke_test.js"], "gen": False, "runner": "node"},
]


def main():
    argv = sys.argv[1:]
    skip_gen = "--skip-gen" in argv
    only = None
    if "--only" in argv:
        idx = argv.index("--only")
        only = argv[idx + 1] if idx + 1 < len(argv) else None

    steps = STEPS
    if skip_gen:
        steps = [s for s in steps if not s["gen"]]
    if only:
        steps = [s for s in steps if only.lower() in s["name"].lower()]

    if not steps:
        print("没有匹配的步骤，检查 --only / --skip-gen 参数")
        return 1

    if skip_gen:
        print(f"[--skip-gen] 跳过造数，直接用已提交的数据集，共 {len(steps)} 步")

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=ROOT)
    total = time.time()
    for i, step in enumerate(steps, 1):
        name, args = step["name"], step["cmd"]
        print(f"\n{'=' * 64}\n[{i}/{len(steps)}] {name}\n{'=' * 64}")
        t = time.time()
        if step.get("runner") == "node":
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
