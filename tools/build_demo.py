# -*- coding: utf-8 -*-
"""生成 demo/index.html（单文件、可离线打开、前端实时推理）。

跑法：python tools/build_demo.py
前置：先跑过 eval/run_eval.py（需要 artifacts/models/engine.json 与 metrics.json）
"""
import base64
import io
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from raven.image_forensics import FEATURE_NAMES, extract, load as load_image
from raven.pipeline import ART, read_jsonl
from raven.text_model import HAND_DIM, LogisticModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "demo")


def load_engine():
    with open(os.path.join(ART, "models", "engine.json"), encoding="utf-8") as f:
        return json.load(f)


def image_cases(bundle):
    """挑 3 个有代表性的图像案例：干净图、copy-move 命中、splice 命中。"""
    im = bundle["image_model"]
    idx = [FEATURE_NAMES.index(n) for n in im["features"]]
    m = LogisticModel(len(idx))
    m.w = np.array(im["w"], dtype=np.float32)
    m.b = im["b"]
    mu, sd = np.array(im["mu"], dtype=np.float32), np.array(im["sd"], dtype=np.float32)

    rows = read_jsonl(os.path.join(ART, "dataset", "images", "images_test.jsonl"))
    picked = []
    want = [("clean_resaved", lambda p: p < 0.3), ("copy_move", lambda p: p > 0.85),
            ("splice", lambda p: p > 0.85)]
    for kind, cond in want:
        for r in rows:
            if r["kind"] != kind:
                continue
            vec, feats, heat = extract(load_image(r["path"]))
            p = float(m.proba(((vec[idx] - mu) / sd)[None, :])[0])
            if not cond(p):
                continue
            img = load_image(r["path"])
            img.thumbnail((320, 320))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=72)
            b64 = base64.b64encode(buf.getvalue()).decode()
            picked.append({
                "kind": kind,
                "name": os.path.basename(r["path"]),
                "p": round(p, 4),
                "bbox": r["bbox"],
                "heat": np.round(heat, 3).tolist(),
                "img": "data:image/jpeg;base64," + b64,
                "top_feats": sorted(feats.items(), key=lambda kv: -abs(kv[1]))[:3],
            })
            break
    return picked


TEXT_SAMPLES = [
    ("外挂广告", "出原神外挂，自瞄透视锁头全功能，稳定不封，需要的加微信abc123"),
    ("逐字符分隔绕过", "出·原·神·外·挂·稳·定·不·封·加·薇·信"),
    ("零宽 + 繁体", "出王\u200b者荣耀外褂，透視自瞄，包退"),
    ("拼音缩写", "接dl上分，hg国标手打，价格便宜私m"),
    ("账号交易", "出三角洲行动账号，全皮肤，可小刀走平台，秒换绑"),
    ("举报语境（陷阱）", "今天在群里看见有人卖原神外挂，已经举报了"),
    ("正常讨论", "求推荐原神里适合新手的角色，我现在只有三个五星"),
    ("正常玩家吐槽", "今天排位连跪五把，队友全程挂机，太难了"),
]


def build_html(bundle, cases, metrics):
    cfg_json = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    cases_json = json.dumps(cases, ensure_ascii=False, separators=(",", ":"))
    samples_json = json.dumps(TEXT_SAMPLES, ensure_ascii=False)

    t = metrics["text"]
    final = next(c for c in t["configs"] if c["config"].startswith("⑤"))
    summary = {
        "clean": final["test_clean"]["f1"],
        "adv": final["test_adv"]["f1"],
        "pervasive": final["test_pervasive"]["f1"],
        "holdout": final["test_holdout"]["f1"],
        "auc": final["test_adv"]["auc"],
        "block_precision": t["tier_table"][0]["precision"],
        "block_share": t["tier_table"][0]["share_of_risk"],
        "latency": t["latency_ms"]["mean"],
        "threshold": t["threshold"],
        "image_auc": metrics["image"]["metrics"]["auc"],
        "image_f1": metrics["image"]["metrics"]["f1"],
    }
    summary_json = json.dumps(summary, ensure_ascii=False)

    return HTML_TEMPLATE.replace("__CFG__", cfg_json) \
                        .replace("__CASES__", cases_json) \
                        .replace("__SAMPLES__", samples_json) \
                        .replace("__SUMMARY__", summary_json)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Raven · 内容风控研判引擎（可交互演示）</title>
<style>
:root{
  --bg:#f6f7f9; --panel:#ffffff; --fg:#16181d; --muted:#6b7280; --bd:#e3e6ea;
  --accent:#3b4cca; --accent-soft:#eef0ff; --danger:#d92d20; --warn:#b54708;
  --ok:#067647; --chip:#f2f4f7;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  font-size:14.5px;line-height:1.7;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:34px 22px 70px}
header{margin-bottom:22px}
h1{font-size:24px;margin:0 0 6px;letter-spacing:-.3px}
.sub{color:var(--muted);font-size:13.5px}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;background:var(--accent-soft);
  color:var(--accent);font-size:12px;font-weight:600;margin-right:6px}
.strip{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:20px 0 26px}
.stat{background:var(--panel);border:1px solid var(--bd);border-radius:10px;padding:12px 13px}
.stat b{display:block;font-size:19px;letter-spacing:-.4px}
.stat span{color:var(--muted);font-size:11.5px;line-height:1.4;display:block;margin-top:2px}
.card{background:var(--panel);border:1px solid var(--bd);border-radius:12px;padding:18px 20px;margin-bottom:16px}
.card h2{font-size:15px;margin:0 0 3px}
.card .hint{color:var(--muted);font-size:12.5px;margin-bottom:14px}
.grid2{display:grid;grid-template-columns:1.15fr .85fr;gap:16px}
textarea{width:100%;min-height:104px;border:1px solid var(--bd);border-radius:9px;padding:11px 12px;
  font-family:inherit;font-size:14px;resize:vertical;background:#fcfcfd;color:var(--fg);line-height:1.65}
textarea:focus{outline:2px solid var(--accent-soft);border-color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin:11px 0 4px}
.chip{border:1px solid var(--bd);background:var(--chip);border-radius:99px;padding:4px 11px;
  font-size:12.5px;cursor:pointer;color:#374151;transition:.15s}
.chip:hover{border-color:var(--accent);color:var(--accent);background:#fff}
.verdict{display:flex;align-items:baseline;gap:10px;margin-bottom:6px}
.score{font-size:40px;font-weight:700;letter-spacing:-1.5px;line-height:1}
.badge{padding:3px 11px;border-radius:7px;font-size:12.5px;font-weight:700}
.b-block{background:#fee4e2;color:var(--danger)}
.b-review{background:#fef0c7;color:var(--warn)}
.b-watch{background:#eef0f4;color:#475467}
.b-allow{background:#d1fadf;color:var(--ok)}
.bar{height:8px;border-radius:99px;background:#eef0f4;overflow:hidden;margin:12px 0 4px}
.bar i{display:block;height:100%;border-radius:99px;transition:width .35s ease}
.meta{color:var(--muted);font-size:12.5px}
.kv{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed var(--bd);font-size:13px}
.kv:last-child{border:none}
.kv b{font-weight:600}
.hit{display:inline-block;background:#fff1f0;color:#b42318;border:1px solid #fecdca;
  border-radius:6px;padding:1px 7px;font-size:12px;margin:3px 5px 0 0}
.hit.neg{background:#eff8ff;color:#175cd3;border-color:#b2ddff}
.steps{margin-top:4px}
.step{border-left:2px solid var(--accent-soft);padding:4px 0 4px 12px;margin-bottom:2px;font-size:12.5px}
.step .nm{color:var(--accent);font-weight:600;font-size:11.5px;display:block}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;
  word-break:break-all;line-height:1.65}
del{background:#fee4e2;text-decoration:none;border-radius:3px;padding:0 1px}
ins{background:#d1fadf;text-decoration:none;border-radius:3px;padding:0 1px}
.imgrow{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.icase{border:1px solid var(--bd);border-radius:10px;overflow:hidden;background:#fff;cursor:pointer}
.icase:hover{border-color:var(--accent)}
.ihead{display:flex;justify-content:space-between;align-items:center;padding:8px 11px;font-size:12.5px}
.ibody{position:relative;aspect-ratio:1/1;background:#f0f1f3}
.ibody img{width:100%;height:100%;object-fit:cover;display:block}
.heat{position:absolute;inset:0;display:grid;grid-template-columns:repeat(15,1fr);grid-template-rows:repeat(15,1fr)}
.heat span{display:block}
footer{color:var(--muted);font-size:12.5px;margin-top:26px;border-top:1px solid var(--bd);padding-top:14px}
@media(max-width:860px){.grid2,.imgrow,.strip{grid-template-columns:1fr 1fr}}
@media(max-width:560px){.strip{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Raven · 面向游戏与二次元社区的内容风控研判引擎</h1>
  <div class="sub"><span class="pill">离线可跑</span><span class="pill">前端实时推理</span>
    这套页面里的打分不是截图，也不是调接口——权重直接内嵌在页面里，改动上面的文本，下面立刻重算。</div>
</header>

<div class="strip" id="strip"></div>

<div class="card">
  <h2>文本研判</h2>
  <div class="hint">试试把「外挂」写成「外·挂·」，或者加几个零宽字符——归一化层会先把它折叠回原形，再看证据。</div>
  <div class="grid2">
    <div>
      <textarea id="input" spellcheck="false"></textarea>
      <div class="chips" id="chips"></div>
    </div>
    <div>
      <div class="verdict">
        <span class="score" id="score">—</span>
        <span class="badge b-allow" id="badge">待检测</span>
      </div>
      <div class="bar"><i id="bar" style="width:0%;background:var(--ok)"></i></div>
      <div class="meta" id="meta">阈值 0.38 · 处置阈值 拦截 0.82 / 复核 0.68 / 观察 0.40</div>
      <div style="margin-top:12px">
        <div class="kv"><span>命中证据</span><b id="evn">—</b></div>
        <div class="kv"><span>类目</span><b id="cat">—</b></div>
        <div class="kv"><span>规则分</span><b id="rule">—</b></div>
      </div>
      <div id="hits" style="margin-top:10px"></div>
    </div>
  </div>
</div>

<div class="card">
  <h2>归一化流水线（真的在跑，不是示意图）</h2>
  <div class="hint">每一步的输入输出都来自页面里的实现，红色是被去掉的字符，绿色是替换后得到的规范形。</div>
  <div class="steps" id="steps"><div class="meta">（等待输入）</div></div>
</div>

<div class="card">
  <h2>图像篡改粗筛</h2>
  <div class="hint">ELA + 噪声残差 + JPEG ghost 的块级特征；右下图是热力图，颜色越深表示越"跟周围不像"。</div>
  <div class="imgrow" id="imgrow"></div>
</div>

<footer id="foot"></footer>
</div>

<script src="raven-lite.js"></script>
<script>
const CFG = __CFG__;
const CASES = __CASES__;
const SAMPLES = __SAMPLES__;
const SUMMARY = __SUMMARY__;
RavenLite.init(CFG);

const $ = id => document.getElementById(id);
const CAT_CN = CFG.subtype_cn;
const BADGE = {block:'b-block', review:'b-review', watch:'b-watch', allow:'b-allow'};

function fmtPct(v){ return (v*100).toFixed(1) + '%'; }

/* ---------- 顶部指标 ---------- */
const stats = [
  [fmtPct(SUMMARY.clean), '干净集 F1'],
  [fmtPct(SUMMARY.adv), '对抗集 F1'],
  [fmtPct(SUMMARY.pervasive), '逐字符分隔集 F1'],
  [SUMMARY.auc.toFixed(3), '对抗集 AUC'],
  [fmtPct(SUMMARY.block_precision), '自动拦截档精确率'],
  [SUMMARY.latency.toFixed(2)+'ms', '单条推理耗时']
];
$('strip').innerHTML = stats.map(([v,l]) =>
  `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join('');
$('foot').innerHTML =
  `阈值 ${SUMMARY.threshold} · 惩罚性误伤（自动拦截档）${fmtPct(1-SUMMARY.block_precision)} · ` +
  `图像侧 AUC ${SUMMARY.image_auc} / F1 ${SUMMARY.image_f1} · ` +
  `全部指标由 <span class="mono">eval/run_eval.py</span> 在留出模板的测试集上跑出，页面不做任何人为修饰。`;

/* ---------- 字符级 diff ---------- */
function diffHtml(before, after){
  // 极简 LCS：只为可视化，长度短，O(nm) 可接受
  const a = Array.from(before), b = Array.from(after);
  const m = a.length, n = b.length;
  const dp = Array.from({length:m+1}, () => new Uint16Array(n+1));
  for (let i=m-1;i>=0;i--) for (let j=n-1;j>=0;j--)
    dp[i][j] = a[i]===b[j] ? dp[i+1][j+1]+1 : Math.max(dp[i+1][j], dp[i][j+1]);
  let i=0,j=0,out='';
  while(i<m && j<n){
    if(a[i]===b[j]){ out+=esc(a[i]); i++; j++; }
    else if(dp[i+1][j] >= dp[i][j+1]){ out+='<del>'+esc(a[i])+'</del>'; i++; }
    else { out+='<ins>'+esc(b[j])+'</ins>'; j++; }
  }
  while(i<m){ out+='<del>'+esc(a[i++])+'</del>'; }
  while(j<n){ out+='<ins>'+esc(b[j++])+'</ins>'; }
  return out;
}
function esc(s){ return s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

/* ---------- 文本检测 ---------- */
function render(text){
  if(!text.trim()){ return; }
  const r = RavenLite.score(text);
  const p = r.p;
  $('score').textContent = p.toFixed(3);
  const badge = $('badge');
  badge.textContent = r.action_cn;
  badge.className = 'badge ' + BADGE[r.action];
  const col = p >= CFG.decision_thresholds.block ? 'var(--danger)'
            : p >= CFG.decision_thresholds.review ? 'var(--warn)'
            : p >= CFG.decision_thresholds.watch ? '#667085' : 'var(--ok)';
  $('bar').style.width = Math.max(2, p*100) + '%';
  $('bar').style.background = col;
  $('meta').textContent = `阈值 ${CFG.threshold} · 处置阈值 拦截 ${CFG.decision_thresholds.block} / 复核 ${CFG.decision_thresholds.review} / 观察 ${CFG.decision_thresholds.watch}`;
  $('evn').textContent = r.hits.length + ' 项';
  $('cat').textContent = CAT_CN[r.subtype] || r.subtype;
  $('rule').textContent = r.p_rule.toFixed(3);
  $('hits').innerHTML =
    r.hits.slice(0,8).map(h => `<span class="hit">${esc(h.term)} ×${h.count}</span>`).join('') +
    (r.neg > 0 ? `<div style="margin-top:8px" class="meta">否定语境信号 ${r.neg.toFixed(1)}：`
      + r.negHits.slice(0,4).map(h=>`<span class="hit neg">${esc(h.term)}</span>`).join('')
      + ` —— 更可能是在举报/提醒，而不是在卖</div>` : '');

  const steps = r.trace.length
    ? r.trace.map(s => `<div class="step"><span class="nm">${s.step}</span>
        <span class="mono">${diffHtml(s.before, s.after)}</span></div>`).join('')
    : '<div class="meta">原文已是规范形，归一化层没有做任何改动。</div>';
  $('steps').innerHTML = steps;
}

$('input').addEventListener('input', e => render(e.target.value));
$('chips').innerHTML = SAMPLES.map(([name, txt]) =>
  `<span class="chip" data-t="${esc(txt)}">${name}</span>`).join('');
$('chips').addEventListener('click', e => {
  if(!e.target.dataset.t) return;
  $('input').value = e.target.dataset.t;
  render(e.target.dataset.t);
});
$('input').value = SAMPLES[0][1];
render(SAMPLES[0][1]);

/* ---------- 图像 ---------- */
$('imgrow').innerHTML = CASES.map((c, i) => {
  const cells = c.heat.map(row => row.map(v =>
    `<span style="background:rgba(217,45,32,${(v*0.75).toFixed(2)})"></span>`).join('')).join('');
  const col = c.p >= 0.5 ? 'var(--danger)' : 'var(--ok)';
  return `<div class="icase" title="${c.name}">
    <div class="ihead"><span>${c.kind}</span><b style="color:${col}">${c.p.toFixed(2)}</b></div>
    <div class="ibody"><img src="${c.img}" alt=""><div class="heat">${cells}</div></div>
    <div class="ihead" style="color:var(--muted);font-size:11.5px">${
      c.top_feats.map(([k,v]) => k+'='+v).join(' · ')}</div>
  </div>`;
}).join('');
</script>
</body>
</html>
"""


def main():
    bundle = load_engine()
    with open(os.path.join(ART, "results", "metrics.json"), encoding="utf-8") as f:
        metrics = json.load(f)
    cases = image_cases(bundle)
    html = build_html(bundle, cases, metrics)

    # 把推理内核内联进页面，做成真正的单文件。
    # 原来用 <script src="raven-lite.js">，只要把 index.html 单独发出去（微信、邮件、
    # 面试时拷到别人电脑）就会白屏，而报错只在控制台——演示时最尴尬的一种失败。
    with open(os.path.join(DEMO, "raven-lite.js"), encoding="utf-8") as f:
        kernel = f.read()
    assert "<script" not in kernel, "内核里出现 script 标签会破坏内联"
    assert '<script src="raven-lite.js"></script>' in html
    html = html.replace('<script src="raven-lite.js"></script>',
                        f"<script>\n{kernel}\n</script>")

    out = os.path.join(DEMO, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[ok] demo -> {out}  ({len(html)/1024:.1f} KB, 图像案例 {len(cases)} 个, 单文件自包含)")


if __name__ == "__main__":
    main()
