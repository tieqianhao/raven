# -*- coding: utf-8 -*-
"""模拟环境定义：把三套变形测试集编排成一场有时间的社区攻击事件。

为什么需要这一层：
评测是静态批量——打一遍、算 P/R/F1，它回答不了"上线之后每分钟会发生什么"。
这个模块只做一件事：给每条样本一个发生时刻和所属波次。
时间轴定下来之后，处置、审核队列、账号聚合才有意义。

编排依据（不是随手拍的时间）：
- 三套测试集对应三种攻击手法，攻击者通常会一轮打不通再换手法，所以按波次串行推进；
- 每波内部前密后疏：开打时集中刷屏，被限流后衰减；
- 基线流量全程均匀铺开：真实社区任何时候都有正常人在说话，误报只能放在这层才量得准。
"""
import random
from datetime import datetime, timedelta

SEED = 7
T0 = datetime(2026, 11, 7, 21, 0, 0)
TOTAL_MIN = 60

# 三波攻击：key 与 artifacts/dataset/text_test_<key>.jsonl 一一对应
WAVES = [
    {
        "key": "adv",
        "name": "第一波 · 混合变形",
        "start": 6,
        "end": 28,
        "note": "繁体 / 全角 / 零宽字符 / emoji 填充 / 重复字符叠用，单条常叠加多种手法",
    },
    {
        "key": "pervasive",
        "name": "第二波 · 逐字符分隔",
        "start": 30,
        "end": 46,
        "note": "每字之间插空格或 emoji，关键词规则完全失效",
    },
    {
        "key": "holdout",
        "name": "第三波 · 未登录谐音",
        "start": 48,
        "end": 58,
        "note": "换用训练集里没出现过的谐音写法（外桂 / 辅住 / 代煉）",
    },
]

BASELINE = {
    "key": "clean",
    "name": "基线流量",
    "note": "未变形的日常发帖，混有直白广告与举报语境",
}


def _spread(n, start, end, rng, burst=2.0):
    """把 n 条消息摊进 [start, end)，burst 越大越集中在窗口开头。"""
    ts = []
    for _ in range(n):
        u = rng.random()
        x = 1.0 - (1.0 - u) ** burst
        ts.append(start + (end - start) * x)
    return sorted(ts)


def build(splits):
    """返回按时间排序的事件列表：[{t, ts, wave, row}]。"""
    rng = random.Random(SEED)
    events = []

    for w in WAVES:
        rows = splits[f"test_{w['key']}"]
        for t, row in zip(_spread(len(rows), w["start"], w["end"], rng), rows):
            events.append({"t": round(t, 3), "wave": w["key"], "row": row})

    rows = splits["test_clean"]
    for t, row in zip(_spread(len(rows), 0.0, TOTAL_MIN, rng, burst=1.0), rows):
        events.append({"t": round(t, 3), "wave": "clean", "row": row})

    events.sort(key=lambda e: (e["t"], e["row"]["id"]))
    for e in events:
        e["ts"] = (T0 + timedelta(minutes=e["t"])).strftime("%H:%M:%S")
    return events


def meta():
    return {
        "title": "某二次元手游社区 · 内容风控作战室",
        "t0": T0.strftime("%H:%M:%S"),
        "total_min": TOTAL_MIN,
        "waves": WAVES,
        "baseline": BASELINE,
    }
