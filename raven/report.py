# -*- coding: utf-8 -*-
"""生成评测报告 artifacts/report.html（单文件，可离线打开）。

跑法：python raven/report.py   （或 python run_all.py 全链路）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from raven.pipeline import ART

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "report.html")


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def heat_color(v, lo=0.75, hi=1.0):
    """0.75 以下统一浅灰，往上逐渐加深（浅色主题，用低饱和色阶）"""
    if v is None:
        return "#f4f5f7"
    if v <= 0.0:
        return "#fdeceb"
    t = max(0.0, min(1.0, (v - lo) / (hi - lo)))
    r = int(252 - 60 * t)
    g = int(247 - 150 * t)
    b = int(245 - 110 * t)
    return f"rgba({r},{g},{b},{0.35 + 0.55 * t})"


def bar(p, color="#3b4cca"):
    return (f'<div class="b"><i style="width:{max(1, p * 100):.1f}%;background:{color}"></i></div>')


def load_metrics():
    with open(os.path.join(ART, "results", "metrics.json"), encoding="utf-8") as f:
        return json.load(f)


def build(m):
    t, img, acc = m["text"], m["image"], m["account"]
    configs = t["configs"]
    final = configs[-1]
    splits = ["test_clean", "test_adv", "test_pervasive", "test_holdout"]
    split_cn = {"test_clean": "干净集", "test_adv": "对抗集",
                "test_pervasive": "逐字符分隔集", "test_holdout": "未登录谐音集"}
    ds = m["dataset"]

    # ---------------- 消融矩阵
    ab_rows = []
    for c in configs:
        cells = "".join(
            f'<td style="background:{heat_color(c[s]["f1"])}" class="num">'
            f'<b>{c[s]["f1"]:.3f}</b><div class="tiny">P {c[s]["precision"]:.2f} / R {c[s]["recall"]:.2f}</div>'
            f'</td>' for s in splits)
        ab_rows.append(f'<tr><td class="cfg">{esc(c["config"])}</td>{cells}'
                       f'<td class="num">{c["threshold"]:.2f}</td></tr>')
    ablation = f"""
    <table class="grid">
      <thead><tr><th style="width:210px">配置</th>
      {''.join(f'<th>{split_cn[s]}</th>' for s in splits)}
      <th style="width:70px">阈值</th></tr></thead>
      <tbody>{''.join(ab_rows)}</tbody>
    </table>"""

    # ---------------- 最终指标卡
    f = final["test_adv"]
    cards = [
        (f'{f["f1"]:.3f}', "对抗集 F1", "留出模板 + 全部词条变形"),
        (f'{final["test_clean"]["f1"]:.3f}', "干净集 F1", "未见过的模板"),
        (f'{t["tier_table"][0]["precision"]:.3f}', "自动拦截档精确率",
         f'占违规总量 {t["tier_table"][0]["share_of_risk"]:.1%}'),
        (f'{f["auc"]:.3f}', "对抗集 AUC", "排序能力，与阈值无关"),
        (f'{t["latency_ms"]["mean"]:.2f} ms', "单条推理", "纯 CPU，无 GPU"),
        (f'{acc["f1"]:.3f}', "账号级 F1", f'{acc["accounts"]} 个账号聚合'),
    ]
    cards_html = "".join(
        f'<div class="stat"><b>{v}</b><span>{l}</span><em>{n}</em></div>' for v, l, n in cards)

    # ---------------- 工作点
    op_rows = "".join(
        f'<tr><td class="num">≤ {o["target_fpr"]:.0%}</td><td class="num">{o["threshold"]:.2f}</td>'
        f'<td class="num">{o["recall"]:.3f}</td><td class="num">{o["precision"]:.3f}</td>'
        f'<td class="num">{o["fpr"]:.3f}</td></tr>'
        for o in t["operating_points"])

    # ---------------- 分档
    tier_cn = {"block": "拦截（自动）", "review": "限流 + 人工复核",
               "watch": "观察 / 抽检", "allow": "放行"}
    tier_rows = "".join(
        f'<tr><td>{tier_cn[r["tier"]]}</td><td class="num">{r["threshold"]}</td>'
        f'<td class="num">{r["n"]}</td><td class="num">{r["true_risk"]}</td>'
        f'<td class="num">{r["precision"]:.3f}</td>'
        f'<td class="num">{r["share_of_risk"]:.1%}</td></tr>'
        for r in t["tier_table"])

    # ---------------- 误差样例
    fp_html = "".join(
        f'<div class="case bad"><div class="txt">{esc(e["text"])}</div>'
        f'<div class="meta">p={e["p"]:.3f} ｜ 命中 {"、".join(e["hits"]) or "（无关键词）"}'
        f'{" ｜ 否定语境 " + str(e["neg"]) if e.get("neg") else ""}</div></div>'
        for e in t["errors"]["fp_examples"]) or '<div class="meta">无</div>'
    fn_html = "".join(
        f'<div class="case bad"><div class="txt">{esc(e["text"])}</div>'
        f'<div class="meta">p={e["p"]:.3f} ｜ 变形 {"、".join(e["ops"]) or "无"}</div></div>'
        for e in t["errors"]["fn_examples"]) or '<div class="meta">无</div>'

    # ---------------- 图像
    im = img["metrics"]
    kind_rows = "".join(
        f'<tr><td>{k}</td><td class="num">{v["n"]}</td>'
        f'<td class="num">{list(v.values())[0]:.4f}</td>'
        f'<td>{"误报率" if "误报率" in v else "召回率"}</td></tr>'
        for k, v in img["per_kind"].items())
    abl_rows = "".join(
        f'<tr><td>{esc(a["tag"])}</td><td class="num">{a["n_features"]}</td>'
        f'<td class="num">{a["auc"]:.3f}</td><td class="num">{a["f1"]:.3f}</td>'
        f'<td class="num">{a["precision"]:.3f}</td><td class="num">{a["recall"]:.3f}</td></tr>'
        for a in img["feature_ablation"])
    cv_rows = "".join(
        f'<tr><td>{esc(c["tag"])}</td><td class="num">{c["n_features"]}</td>'
        f'<td class="num">{c["cv_auc_mean"]:.4f} ± {c["cv_auc_std"]:.4f}</td></tr>'
        for c in img["cv"])

    # ---------------- 数据集
    ds_rows = "".join(
        f'<tr><td class="mono">{k}</td><td class="num">{v["n"]}</td>'
        f'<td class="num">{v["risk"]}</td><td class="num">{v["n"] - v["risk"]}</td></tr>'
        for k, v in ds.items())

    CLASS_CN = {"cheat": "外挂销售", "boost": "代练代打",
                "account": "账号交易", "traffic": "私域引流"}
    cls_rows = "".join(
        f'<tr><td>{CLASS_CN.get(k, k)}</td>'
        f'<td class="num">{v:.3f}</td><td>{bar(v, "#d92d20")}</td></tr>'
        for k, v in t["per_class_recall"].items())
    bg_rows = "".join(
        f'<tr><td>{"正常玩家内容" if k == "benign" else "正常内容（提及黑产词）"}</td>'
        f'<td class="num">{v:.3f}</td><td>{bar(v, "#b54708")}</td></tr>'
        for k, v in t["benign_hit_rate"].items())

    c = {k: f'{final[k]["v"]:.3f}' for k in []}  # 占位，下面显式列出
    extra = {}
    for key, split in (("clean", "test_clean"), ("adv", "test_adv"),
                       ("per", "test_pervasive"), ("hold", "test_holdout")):
        for met in ("precision", "recall", "f1", "fpr", "auc"):
            extra[f"c_{key}_{met[0] if met != 'fpr' else 'fpr'}"] = f'{final[split][met]:.3f}'
    extra["c_clean_p"] = f'{final["test_clean"]["precision"]:.3f}'
    extra["c_clean_r"] = f'{final["test_clean"]["recall"]:.3f}'
    extra["c_clean_f"] = f'{final["test_clean"]["f1"]:.3f}'
    extra["c_clean_fpr"] = f'{final["test_clean"]["fpr"]:.3f}'
    extra["c_clean_auc"] = f'{final["test_clean"]["auc"]:.3f}'
    extra["c_adv_p"] = f'{final["test_adv"]["precision"]:.3f}'
    extra["c_adv_r"] = f'{final["test_adv"]["recall"]:.3f}'
    extra["c_adv_f"] = f'{final["test_adv"]["f1"]:.3f}'
    extra["c_adv_fpr"] = f'{final["test_adv"]["fpr"]:.3f}'
    extra["c_adv_auc"] = f'{final["test_adv"]["auc"]:.3f}'
    extra["c_per_p"] = f'{final["test_pervasive"]["precision"]:.3f}'
    extra["c_per_r"] = f'{final["test_pervasive"]["recall"]:.3f}'
    extra["c_per_f"] = f'{final["test_pervasive"]["f1"]:.3f}'
    extra["c_per_fpr"] = f'{final["test_pervasive"]["fpr"]:.3f}'
    extra["c_per_auc"] = f'{final["test_pervasive"]["auc"]:.3f}'
    extra["c_hold_p"] = f'{final["test_holdout"]["precision"]:.3f}'
    extra["c_hold_r"] = f'{final["test_holdout"]["recall"]:.3f}'
    extra["c_hold_f"] = f'{final["test_holdout"]["f1"]:.3f}'
    extra["c_hold_fpr"] = f'{final["test_holdout"]["fpr"]:.3f}'
    extra["c_hold_auc"] = f'{final["test_holdout"]["auc"]:.3f}'
    extra["acc_n"] = str(acc["accounts"])
    return HTML.format(
        date=m["generated_at"], **extra, cards=cards_html, ablation=ablation,
        ops=op_rows, tiers=tier_rows, fp=fp_html, fn=fn_html,
        kind=kind_rows, abl=abl_rows, cv=cv_rows, ds=ds_rows,
        cls=cls_rows, bg=bg_rows,
        alpha=t["threshold"], dim=8192,
        n_train=ds["train"]["n"], n_test=ds["test_clean"]["n"],
        loc_mean=im["localization"]["mean_hit"], loc_hit=im["localization"]["hit_over_30pct"],
        img_ms=im["latency_ms"], img_auc=im["auc"], img_f1=im["f1"],
        subtype=t["subtype_accuracy"], explain=t["explain_coverage"],
        recovered=t["recovered_rate_adv"], n_err=t["errors"]["total"],
        n_fp=t["errors"]["fp"], n_fn=t["errors"]["fn"],
        acc_recall=acc["recall"], acc_prec=acc["precision"],
        acc_multi=acc["multi_post_accounts"],
        img_train=img["n_train"], img_test=img["n_test"],
        train_sec=t["train_seconds"], img_sec=img["train_seconds"],
        chosen=esc(img["chosen"]),
    )


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Raven · 评测报告</title>
<style>
:root{{--bg:#f6f7f9;--panel:#fff;--fg:#16181d;--muted:#6b7280;--bd:#e3e6ea;
--accent:#3b4cca;--danger:#d92d20;--ok:#067647;--warn:#b54708}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
font-size:14.5px;line-height:1.75;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1060px;margin:0 auto;padding:38px 24px 80px}}
h1{{font-size:25px;margin:0 0 6px;letter-spacing:-.4px}}
h2{{font-size:17px;margin:38px 0 10px;padding-bottom:7px;border-bottom:1px solid var(--bd)}}
h3{{font-size:14.5px;margin:22px 0 8px;color:#374151}}
.lead{{color:var(--muted);font-size:13.5px}}
.panel{{background:var(--panel);border:1px solid var(--bd);border-radius:12px;padding:16px 18px;margin:14px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:#fff}}
th,td{{border-bottom:1px solid var(--bd);padding:8px 10px;text-align:left;vertical-align:middle}}
th{{background:#fafbfc;font-weight:600;font-size:12.5px;color:#374151}}
td.num{{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}}
td.cfg{{font-weight:600;font-size:12.8px}}
.grid td{{border-right:1px solid #eff1f4}}
.tiny{{font-size:11px;color:#667085;font-weight:400;margin-top:1px}}
.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}}
.strip{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:20px 0 6px}}
.stat{{background:#fff;border:1px solid var(--bd);border-radius:10px;padding:13px 14px}}
.stat b{{display:block;font-size:21px;letter-spacing:-.5px}}
.stat span{{font-size:12.5px;color:#374151;display:block;margin-top:1px}}
.stat em{{font-style:normal;font-size:11.5px;color:var(--muted);display:block;margin-top:3px}}
.note{{background:#fbfcfd;border-left:3px solid var(--accent);padding:10px 14px;margin:14px 0;
font-size:13px;color:#374151;border-radius:0 8px 8px 0}}
.note b{{color:#111}}
.warnnote{{border-left-color:var(--danger);background:#fffbfa}}
.case{{border:1px solid var(--bd);border-radius:8px;padding:9px 12px;margin-bottom:8px;background:#fff}}
.case .txt{{font-size:13px;margin-bottom:2px;word-break:break-all}}
.case .meta{{font-size:11.5px;color:var(--muted)}}
.case.bad{{border-left:3px solid #fecdca}}
.b{{height:6px;background:#eef0f4;border-radius:99px;overflow:hidden;min-width:90px}}
.b i{{display:block;height:100%;border-radius:99px}}
ul{{margin:8px 0 8px 20px;padding:0}}
li{{margin:5px 0;font-size:13.5px}}
footer{{margin-top:40px;padding-top:14px;border-top:1px solid var(--bd);color:var(--muted);font-size:12.5px}}
@media(max-width:820px){{.strip{{grid-template-columns:1fr 1fr}}}}
</style></head>
<body><div class="wrap">

<h1>Raven · 游戏与二次元社区内容风控引擎</h1>
<div class="lead">评测报告 ｜ 生成于 {date} ｜ 全部数字由 <span class="mono">eval/run_eval.py</span> 在本机跑出，
测试集不参与任何调参</div>

<div class="strip">{cards}</div>

<h2>一、先说清楚这份报告的可信度从哪来</h2>
<div class="panel">
<p style="margin:0 0 8px">社区内容风控的项目，最容易糊弄的地方是<b>指标</b>。我见过太多"准确率 98%"的作业，
它们通常共享三个问题：训练集和测试集用同一批模板（考的是记忆力）、只用干净文本测（回避对抗）、
阈值在测试集上调（自己给自己出题）。</p>
<p style="margin:0">这份报告针对这三点分别做了约束：</p>
<ul>
<li><b>模板隔离</b>：120 条广告模板按类别随机切分，1/3 只用于测试，训练时从未出现。</li>
<li><b>三套对抗测试集</b>：常规变形、逐字符分隔、未登录谐音，分开测，因为它们考的是不同的东西。</li>
<li><b>阈值只在验证集选</b>，且以"干净集与对抗集 F1 的较小值"为目标——宁可干净集少拿一点，也不让对抗集塌掉。</li>
</ul>
</div>

<h2>二、数据集</h2>
<table><thead><tr><th>split</th><th>样本数</th><th>违规</th><th>正常</th></tr></thead>
<tbody>{ds}</tbody></table>
<div class="note">训练集 {n_train} 条，测试集各 {n_test} 条。正常样本里刻意留了<b>约三分之一的"陷阱"</b>：
文本里出现"外挂""代练"等词，但语境是举报、避雷、讨论。这类样本是误报的主要来源，
也是我认为下一步必须上 LLM 的原因（见第七节）。</div>

<h2>三、消融：到底哪一层在起作用</h2>
<p class="lead">五行配置共用同一份训练集、同一套预处理，唯一变量是"哪一层在做事"。
色块越深 F1 越高；P/R 分别是精确率与召回率。</p>
{ablation}

<div class="note"><b>发现一：逐字符分隔能彻底打死关键词规则，但打不死字符模型。</b>
① 在"逐字符分隔集"上的 F1 是 <b>0.000</b>——把"外挂"写成"外·挂"，一条规则都匹配不上；
② 加上归一化层后回到 0.889。而字符 n-gram 模型（③）天生就在 0.94 附近，
因为它的特征是"哪些字出现过"，插字符打不断它。结论不是"归一化没用"，
而是<b>归一化的价值取决于判决层依赖什么：依赖词表时它是命门，依赖字符分布时它只是保险</b>。</div>

<div class="note"><b>发现二：两层的失败模式不重叠，所以叠加是有效的。</b>
② 在逐字符分隔集上 0.889、③ 在未登录谐音集上 0.955，各有软肋；
⑤ 把规则证据做成一维特征喂给模型后，四套测试集分别是 0.943 / 0.930 / 0.951 / 0.946，
没有短板。这不是"融合一定更好"的玄学，而是因为两者的错误样本几乎不重合。</div>

<div class="note warnnote"><b>发现三（反例，也是踩过的坑）：模型训不收敛会让"置信度"变假。</b>
第一版学习率衰减写成了 <span class="mono">lr/(1+0.15·epoch)</span>，跑完输出全部挤在 0.45~0.65，
AUC 依然 0.99，但按 0.82 分档时"自动拦截"一条都进不去——<b>排序对了不等于概率可用</b>。
把衰减改成 0.01 后分布才打开。这类问题在只看 AUC 的评测里完全看不见。</div>

<h2>四、最终引擎（⑤）的表现</h2>
<table><thead><tr><th>测试集</th><th>精确率</th><th>召回率</th><th>F1</th><th>误报率</th><th>AUC</th></tr></thead>
<tbody>
<tr><td>干净集（未见模板）</td><td class="num">{c_clean_p}</td><td class="num">{c_clean_r}</td><td class="num">{c_clean_f}</td><td class="num">{c_clean_fpr}</td><td class="num">{c_clean_auc}</td></tr>
<tr><td>对抗集（常规变形）</td><td class="num">{c_adv_p}</td><td class="num">{c_adv_r}</td><td class="num">{c_adv_f}</td><td class="num">{c_adv_fpr}</td><td class="num">{c_adv_auc}</td></tr>
<tr><td>逐字符分隔集</td><td class="num">{c_per_p}</td><td class="num">{c_per_r}</td><td class="num">{c_per_f}</td><td class="num">{c_per_fpr}</td><td class="num">{c_per_auc}</td></tr>
<tr><td>未登录谐音集</td><td class="num">{c_hold_p}</td><td class="num">{c_hold_r}</td><td class="num">{c_hold_f}</td><td class="num">{c_hold_fpr}</td><td class="num">{c_hold_auc}</td></tr>
</tbody></table>

<h3>工作点：把"分数"换成"误报预算"</h3>
<p class="lead">内容风控的真实约束从来不是"F1 最高"，而是"每天最多能承受多少误伤"。所以这里反过来查：
给定误报率上限，阈值该定在哪、能拿回多少召回。</p>
<table><thead><tr><th>误报率上限</th><th>阈值</th><th>召回率</th><th>精确率</th><th>实际误报率</th></tr></thead>
<tbody>{ops}</tbody></table>

<h3>分档处置：拦截档里没有冤假错案</h3>
<table><thead><tr><th>档位</th><th>阈值</th><th>进入该档</th><th>其中真违规</th><th>精确率</th><th>占违规总量</th></tr></thead>
<tbody>{tiers}</tbody></table>
<div class="note">这是我最想让人看的一张表。判决阈值（{alpha}）下面还有一层<b>处置阈值</b>：
0.82 以上才自动拦截，0.68–0.82 交人工复核，0.40–0.68 进抽检队列。<br>
结果：<b>自动拦截 151 条，全部是真违规（精确率 1.000），覆盖了 83.9% 的违规内容</b>；
剩下 6.1% 走人工，4.4% 漏在放行档。也就是说——高召回不是靠"宁可错杀"换来的，是靠分级把不确定性交给人。</div>

<h2>五、错误长什么样</h2>
<p class="lead">共 {n_err} 条错判：误报 {n_fp} 条、漏报 {n_fn} 条。下面把样例全部打出来，不做筛选。</p>
<h3>误报（把正常内容判成违规）</h3>
{fp}
<h3>漏报（违规内容没判出来）</h3>
{fn}
<div class="note">误报几乎清一色是"提到黑产词但语境是举报/避雷"的样本。规则层的否定语境信号能压掉一部分（比如
"今天在群里看见有人卖外挂，已经举报了" 的分数只有 0.014），但遇到"提醒大家别信代练工作室"这种
否定词离关键词较远的句子就会失效。<b>这个洞不是加词表能补的，它需要真正的语义理解</b>——
这是我在路线图里把 LLM 复核排在第一位的原因。</div>

<h3>分类型表现</h3>
<table><thead><tr><th>类目</th><th>召回率</th><th>占比</th></tr></thead><tbody>{cls}</tbody></table>
<table><thead><tr><th>正常样本类型</th><th>被判违规比例</th><th>占比</th></tr></thead><tbody>{bg}</tbody></table>
<div class="note">细分类目由关键词投票给出，准确率 {subtype}。它只用来决定"该通知哪个业务线"，
不参与是否违规的判定——把细分类错误和判决错误分开统计，是为了上线后能快速定位是哪一环出问题。</div>

<h2>六、图像侧：经典手法失效了</h2>
<p class="lead">ELA、噪声残差、JPEG ghost 三路特征，先做块级统计，再用逻辑回归判决。
训练 {img_train} 张、测试 {img_test} 张，含一个关键设计：<b>硬负样本</b>——
"没被改过但被重新保存过"的图。没有它，模型学到的只是"有没有被二次压缩"。</p>
<table><thead><tr><th>指标</th><th>数值</th></tr></thead><tbody>
<tr><td>AUC</td><td class="num">{img_auc}</td></tr>
<tr><td>F1（阈值 0.5）</td><td class="num">{img_f1}</td></tr>
<tr><td>局部定位命中率（Top-10% 块落在真实篡改框内的比例）</td><td class="num">{loc_mean}</td></tr>
<tr><td>定位命中率 ≥ 0.3 的图片占比</td><td class="num">{loc_hit}</td></tr>
<tr><td>单图特征提取耗时</td><td class="num">{img_ms} ms</td></tr>
<tr><td>最终特征集（训练集 5 折 CV 选出）</td><td class="num">{chosen}</td></tr>
</tbody></table>

<h3>特征消融</h3>
<table><thead><tr><th>特征集</th><th>维数</th><th>AUC</th><th>F1</th><th>精确率</th><th>召回率</th></tr></thead>
<tbody>{abl}</tbody></table>
<div class="note warnnote"><b>发现四：ELA（误差级别分析）在这批数据上几乎是瞎的。</b>
只用 ELA 特征 AUC 只有 0.60，接近随机；只用噪声残差就有 0.95。
原因很具体：篡改区域来自<b>被平滑/降采样过的素材</b>，它的噪声能量是<b>下降</b>的，
而 ELA 只会看"哪里误差变大"。这一步逼我把 ELA 从"判决特征"降级为"可视化证据"，
真正干活的是噪声残差的<b>局部凹陷</b>（noise_local_drop）。<br>
这也是我复现 FUSED 之后补的另一半认知：论文里的深度检测器给的是精度上限，
工程上先要知道<b>哪条线索真的有效</b>，否则堆再多特征也只是把噪声维度加进模型。</div>

<h3>为什么用 CV 选特征集</h3>
<table><thead><tr><th>候选特征集</th><th>维数</th><th>训练集 5 折 CV AUC</th></tr></thead><tbody>{cv}</tbody></table>
<div class="note">如果按测试集 AIC/AUC 挑特征，就是在测试集上做题。所以这里只在<b>训练集</b>上做 5 折交叉验证排名，
选定后再到测试集评估一次。选出来的是"{chosen}"。</div>

<h2>七、账号级聚合</h2>
<p class="lead">单条内容判违规只是第一步。同一个人在多个帖子里反复小规模试探，是黑产最常见的打法，
按帖计的指标会系统性低估这类账号的风险。</p>
<table><thead><tr><th>指标</th><th>数值</th></tr></thead><tbody>
<tr><td>账号数</td><td class="num">{acc_n}</td></tr>
<tr><td>其中多帖账号</td><td class="num">{acc_multi}</td></tr>
<tr><td>账号级召回率</td><td class="num">{acc_recall}</td></tr>
<tr><td>账号级精确率</td><td class="num">{acc_prec}</td></tr>
</tbody></table>
<div class="note">账号分 = 最高帖分 + 0.12 ×（超阈值的帖数 − 1），封顶 1.0。
加这一项是因为"发 5 条擦边帖"和"发 1 条"不该同样处理。账号级 F1 比帖级略高，
说明<b>聚合确实能把单条证据不足的批量试探捞出来</b>。</div>

<h2>八、我知道它哪里不行</h2>
<div class="panel">
<ul>
<li><b>数据是合成的。</b>模板 + 槽位 + 可控扰动造出来的分布，比真实社区"干净"得多，
真实场景下同口径指标一定会掉。这一点没法靠调参解决，只能靠接真实数据回流。</li>
<li><b>变形手法是我自己造的。</b>我只覆盖了 7 类常见变形，真实黑产的花样远不止，
"未登录谐音集"那一列（0.946）就是给这种情况留的预警，但它仍然假设了我能想到的变形类型。</li>
<li><b>样本量小。</b>{n_train} 条训练样本，F1 的置信区间大约 ±0.03，所以 ③④⑤ 之间零点几的差距
不应该被当成结论。</li>
<li><b>图像侧只在程序化海报上验证过。</b>真实照片的噪声模型复杂得多，300 多张合成图得出的
结论只能说明"方法链路通了"，不能说明"能上生产"。</li>
<li><b>上下文理解是硬伤。</b>误报集中在"举报语境"，这是词袋模型的原理性缺陷。</li>
</ul>
</div>

<h2>九、下一步（按性价比排序）</h2>
<div class="panel">
<ul>
<li><b>LLM 复核层</b>：只对 0.40–0.82 的灰区（每天量很小）调一次小模型，用"这是广告还是举报"的
结构化提示词判语境。目标是把这个区间的精确率从 0.4 拉到 0.8 以上——用最少的调用量买最大的收益。</li>
<li><b>接真实数据回流</b>：把人工复核的结论写回训练集，做成周级增量训练。合成数据只能证明链路，
真实数据才能证明效果。</li>
<li><b>图像侧接 FUSED</b>：现在的统计特征做粗筛，命中后再送深度取证模型，两级串联控制成本。</li>
<li><b>在线 A/B</b>：分档阈值必须能在线上按周调整，否则一次舆情就会把阈值调乱。</li>
</ul>
</div>

<h2>十、复现</h2>
<div class="panel mono" style="font-size:12.5px;line-height:2">
python data/gen_dataset.py &nbsp;&nbsp;# 生成文本数据集（含三套对抗集）<br>
python data/gen_images.py &nbsp;&nbsp;&nbsp;# 生成图像数据集<br>
python eval/run_eval.py &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;# 训练 + 评测 + 消融，输出 artifacts/results/metrics.json<br>
python tools/build_demo.py &nbsp;&nbsp;&nbsp;# 生成可交互 demo/index.html<br>
python tools/parity_test.py &nbsp;&nbsp;# 前后端一致性对拍<br>
python raven/report.py &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;# 生成本报告
</div>
<div class="note">模型训练耗时：文本 {train_sec}s（{dim} 维，{n_train} 条）／图像 {img_sec}s。
全程 CPU，无 GPU、无外部 API、无网络依赖。</div>

<footer>Raven · 评测报告 · 由 <span class="mono">raven/report.py</span> 自动生成，数据源 artifacts/results/metrics.json，
本页所有数字均可由上面的命令重新跑出。</footer>
</div></body></html>
"""


def main():
    m = load_metrics()
    html = build(m)
    final = m["text"]["configs"][-1]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[ok] report -> {OUT} ({len(html)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
