# -*- coding: utf-8 -*-
"""把一个 60 分钟的模拟演练渲染成单文件看板（可离线打开、可播放）。

设计取舍：
- 数据全部内嵌，页面不请求任何外部资源——面试现场断网也能打开；
- 只画数据里有的东西：没有"在线人数""服务器负载"这类编出来的指标，
  运营侧假设（审核产能、抽检率）单独列在页脚，与实测值分开；
- 时间轴是真时间轴：事件按模拟时刻推进，KPI 是边跑边累计的，
  跑完才解锁与评测的对账表。
"""
import json
import os


def _payload(metrics, records):
    ev = []
    for r in records:
        ev.append({
            "i": r["i"], "t": r["t"], "ts": r["ts"], "w": r["w"],
            "id": r["id"], "acct": r["acct"],
            "raw": r["raw"], "norm": r["norm"],
            "p": r["p"], "pr": r["pr"], "act": r["act"], "hit": r["hit"],
            "sub": r["sub"],
            "label": r["label"], "ops": r["ops"], "hits": r["hits"],
            "rec": r["rec"], "neg": r["neg"], "ms": r["ms"],
        })
    return {
        "meta": metrics["scenario"],
        "assumptions": metrics["assumptions"],
        "n": metrics["n"],
        "events": ev,
        "waves": metrics["waves"],
        "tiers": metrics["tiers"],
        "ops": metrics["ops"],
        "accounts": metrics["accounts"],
        "latency": metrics["latency"],
        "reconcile": metrics["reconcile"],
        "engine": metrics["engine"],
    }


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{
    --bg:#f3f5f8; --panel:#fff; --line:#e4e8ee; --line2:#eef1f5;
    --ink:#161a20; --ink2:#3d454f; --dim:#77808c; --faint:#9aa3ae;
    --block:#cf3a2c; --review:#dd7d14; --watch:#b6921f; --allow:#9aa3ae;
    --ok:#1f7a4d;
    --mono:ui-monospace,"JetBrains Mono","Cascadia Mono",Consolas,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{background:var(--bg);color:var(--ink);font-family:var(--sans);
       font-size:13px;line-height:1.55;-webkit-font-smoothing:antialiased}
  h1,h2,h3,p{margin:0}
  button{font-family:inherit;cursor:pointer}

  .wrap{max-width:1440px;margin:0 auto;padding:0 20px 28px}

  /* ---------- 顶栏 ---------- */
  .hdr{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.94);
       backdrop-filter:blur(8px);border-bottom:1px solid var(--line);
       margin:0 -20px 16px;padding:12px 20px;
       display:flex;align-items:center;gap:18px;flex-wrap:wrap}
  .hdr-l{display:flex;align-items:center;gap:11px;min-width:300px;flex:1}
  .pulse{width:9px;height:9px;border-radius:50%;background:var(--block);
         box-shadow:0 0 0 0 rgba(207,58,44,.5);animation:p 2s infinite}
  @keyframes p{0%{box-shadow:0 0 0 0 rgba(207,58,44,.45)}
               70%{box-shadow:0 0 0 9px rgba(207,58,44,0)}
               100%{box-shadow:0 0 0 0 rgba(207,58,44,0)}}
  .hdr h1{font-size:15px;font-weight:650;letter-spacing:.2px}
  .hdr p{font-size:11.5px;color:var(--dim);margin-top:1px}

  .clock{display:flex;align-items:baseline;gap:9px}
  .clock b{font-family:var(--mono);font-size:22px;font-weight:600;
           letter-spacing:.5px;font-variant-numeric:tabular-nums}
  .clock em{font-style:normal;font-size:11px;color:var(--dim);
            padding:2px 7px;border-radius:20px;background:#f0f2f6;
            border:1px solid var(--line);white-space:nowrap}
  .clock em.hot{background:#fdeceb;border-color:#f6cfcb;color:var(--block)}

  .ctrl{display:flex;gap:6px;align-items:center}
  .btn{border:1px solid var(--line);background:#fff;color:var(--ink2);
       border-radius:7px;padding:6px 12px;font-size:12px;transition:.14s}
  .btn:hover{border-color:#c9d0d9;background:#fafbfc}
  .btn.primary{background:var(--ink);border-color:var(--ink);color:#fff;min-width:66px}
  .btn.primary:hover{background:#2b323b}
  .btn.on{background:#eef2f7;border-color:#c3ccd8;color:var(--ink);font-weight:600}

  .bar{height:3px;background:var(--line2);border-radius:2px;overflow:hidden;
       margin:-16px -20px 0;position:relative;z-index:19}
  .bar i{display:block;height:100%;width:0;background:var(--ink);transition:width .1s linear}

  /* ---------- KPI ---------- */
  .kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:16px 0}
  .kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;
       padding:11px 13px 10px;position:relative;overflow:hidden}
  .kpi span{display:block;font-size:11px;color:var(--dim);margin-bottom:3px}
  .kpi b{font-family:var(--mono);font-size:23px;font-weight:600;
         font-variant-numeric:tabular-nums;letter-spacing:-.4px}
  .kpi b small{font-size:11px;color:var(--faint);font-weight:500;margin-left:2px}
  .kpi.block b{color:var(--block)}
  .kpi.review b{color:var(--review)}
  .kpi.watch b{color:var(--watch)}
  .kpi.bad b{color:var(--block)}
  .kpi.good b{color:var(--ok)}

  /* ---------- 布局 ---------- */
  .grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:14px;align-items:start}
  @media (max-width:1080px){.grid{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(3,1fr)}}

  .panel{background:var(--panel);border:1px solid var(--line);border-radius:11px;overflow:hidden}
  .panel-h{display:flex;align-items:center;gap:10px;padding:10px 13px;
           border-bottom:1px solid var(--line2);flex-wrap:wrap}
  .panel-h h2{font-size:12.5px;font-weight:640;letter-spacing:.2px}
  .panel-h .hint{font-size:11px;color:var(--faint);margin-left:auto;font-variant-numeric:tabular-nums}

  .filters{display:flex;gap:5px;margin-left:auto}
  .fchip{border:1px solid var(--line);background:#fff;color:var(--dim);
         border-radius:20px;padding:2.5px 10px;font-size:11px}
  .fchip.on{background:var(--ink);border-color:var(--ink);color:#fff}

  /* ---------- 事件流 ---------- */
  .stream{height:calc(100vh - 300px);min-height:460px;overflow-y:auto;
          padding:8px;scroll-behavior:auto}
  .stream::-webkit-scrollbar{width:8px}
  .stream::-webkit-scrollbar-thumb{background:#d8dde4;border-radius:4px}

  .ev{border:1px solid var(--line2);border-left:3px solid var(--allow);
       border-radius:8px;padding:7px 10px 8px;margin-bottom:6px;background:#fff;
       transition:border-color .18s,box-shadow .18s;cursor:pointer}
  .ev:hover{box-shadow:0 1px 6px rgba(20,26,34,.06)}
  .ev.block{border-left-color:var(--block)}
  .ev.review{border-left-color:var(--review)}
  .ev.watch{border-left-color:var(--watch)}
  .ev.miss{background:#fff8f2}
  .ev.fp{background:#f7fafc}

  .ev-top{display:flex;align-items:center;gap:7px;font-size:11px;
          font-family:var(--mono);color:var(--faint);margin-bottom:3px}
  .ev-t{font-variant-numeric:tabular-nums}
  .ev-a{color:var(--dim)}
  .wtag{padding:.5px 6px;border-radius:4px;background:#f0f2f6;color:#6d7683;
        font-family:var(--sans);font-size:10px;letter-spacing:.2px}
  .wtag.w1{background:#eef1fa;color:#3f5aa8}
  .wtag.w2{background:#f2eefa;color:#6b46a8}
  .wtag.w3{background:#fbeef2;color:#a83f66}
  .ev-p{margin-left:auto;font-weight:600;font-size:12px}
  .chip{padding:1px 8px;border-radius:20px;font-family:var(--sans);font-size:10.5px;
        font-weight:600;letter-spacing:.2px}
  .chip.block{background:var(--block);color:#fff}
  .chip.review{background:var(--review);color:#fff}
  .chip.watch{background:var(--watch);color:#fff}
  .chip.allow{background:#eef1f5;color:#7d8794}

  .ev-txt{font-size:12.5px;color:var(--ink);word-break:break-all;line-height:1.5}
  .ev-norm{font-size:11.5px;color:var(--dim);word-break:break-all;margin-top:2px}
  .ev-norm b{color:#2f6f4f;font-weight:500}

  .ev-d{display:none;margin-top:7px;padding-top:7px;border-top:1px dashed var(--line);
        font-size:11px;color:var(--dim);line-height:1.8}
  .ev.open .ev-d{display:block}
  .ev-d code{font-family:var(--mono);background:#f4f6f9;padding:1px 5px;
             border-radius:4px;color:var(--ink2);font-size:10.5px}
  .ev-d .kv{margin-right:14px;white-space:nowrap}
  .truth-ok{color:var(--ok)} .truth-bad{color:var(--block);font-weight:600}

  /* ---------- 右栏 ---------- */
  .side{display:flex;flex-direction:column;gap:12px}
  .side .panel{padding-bottom:4px}
  .wave{padding:9px 13px;border-bottom:1px solid var(--line2)}
  .wave:last-child{border-bottom:0}
  .wave-t{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:600}
  .wave-t .dot{width:6px;height:6px;border-radius:50%;background:#d7dce3}
  .wave.active .dot{background:var(--block);animation:p 1.6s infinite}
  .wave-n{margin-left:auto;font-family:var(--mono);font-size:11px;
          color:var(--faint);font-variant-numeric:tabular-nums}
  .wave-s{font-size:10.5px;color:var(--faint);margin:2px 0 5px;padding-left:13px}
  .track{height:4px;background:#eef1f5;border-radius:3px;overflow:hidden;margin-left:13px}
  .track i{display:block;height:100%;width:0;border-radius:3px;
           background:linear-gradient(90deg,#c9ced6,#8d97a3);transition:width .25s}
  .wave.hit .track i{background:linear-gradient(90deg,#e8a08f,var(--block))}

  .tier{padding:7px 13px;display:flex;align-items:center;gap:9px;font-size:12px}
  .tier .nm{width:88px;color:var(--ink2);flex:none}
  .tier .tb{flex:1;height:7px;background:#f1f4f7;border-radius:4px;overflow:hidden}
  .tier .tb i{display:block;height:100%;width:0;border-radius:4px;transition:width .25s}
  .tier.block .tb i{background:var(--block)}
  .tier.review .tb i{background:var(--review)}
  .tier.watch .tb i{background:var(--watch)}
  .tier.allow .tb i{background:#c3cad3}
  .tier .vv{width:52px;text-align:right;font-family:var(--mono);
            font-size:11.5px;color:var(--ink2);font-variant-numeric:tabular-nums;flex:none}

  .acct{padding:6px 13px;display:flex;align-items:center;gap:9px;font-size:11.5px}
  .acct .nm{font-family:var(--mono);color:var(--ink2);width:56px;flex:none}
  .acct .pl{font-size:10.5px;padding:.5px 7px;border-radius:20px;
            background:#fdeceb;color:var(--block);font-weight:600}
  .acct .pl.ban{background:var(--block);color:#fff}
  .acct .pl.mute{background:#fdeedb;color:#a9630d}
  .acct .pl.watch{background:#f8f2da;color:#8a6f13}
  .acct .pl.none{background:#f0f2f5;color:var(--faint)}
  .acct .n{margin-left:auto;font-family:var(--mono);color:var(--faint);font-size:11px}

  /* ---------- 对账 ---------- */
  .recon{margin-top:14px;border-color:#cfd8e2}
  .recon .panel-h{background:#f8fafc}
  table{width:100%;border-collapse:collapse;font-size:12px}
  th,td{padding:8px 13px;text-align:left;border-bottom:1px solid var(--line2)}
  th{font-weight:600;color:var(--dim);font-size:11px;background:#fcfdfe}
  td.num{font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right}
  tr:last-child td{border-bottom:0}
  .tag-ok{color:var(--ok);font-weight:600}
  .tag-no{color:var(--block);font-weight:600}

  .empty{padding:26px 13px;text-align:center;color:var(--faint);font-size:12px}

  footer{margin-top:16px;padding-top:12px;border-top:1px solid var(--line);
         font-size:11px;color:var(--faint);line-height:1.85}
  footer b{color:var(--dim);font-weight:600}
  footer .sep{margin:0 7px;color:#dfe4ea}
</style>
</head>
<body>
<div class="wrap">

  <header class="hdr">
    <div class="hdr-l">
      <div class="pulse" id="pulse"></div>
      <div>
        <h1>__HEAD_TITLE__</h1>
        <p id="sub">模拟环境 · 真实引擎重放 · 每条判定都来自 artifacts/models/engine.json</p>
      </div>
    </div>
    <div class="clock">
      <b id="clock">__T0__</b>
      <em id="phase">待开始</em>
    </div>
    <div class="ctrl">
      <button class="btn primary" id="play">播放</button>
      <button class="btn sp on" data-speed="1">1×</button>
      <button class="btn sp" data-speed="4">4×</button>
      <button class="btn sp" data-speed="16">16×</button>
      <button class="btn" id="reset">重置</button>
    </div>
  </header>
  <div class="bar"><i id="prog"></i></div>

  <section class="kpis">
    <div class="kpi"><span>已处理内容</span><b id="k-total">0<small>/__N__</small></b></div>
    <div class="kpi block"><span>直接拦截</span><b id="k-block">0</b></div>
    <div class="kpi review"><span>转人工复核</span><b id="k-review">0</b></div>
    <div class="kpi watch"><span>观察（进抽检池）</span><b id="k-watch">0</b></div>
    <div class="kpi bad"><span>拦截档误伤</span><b id="k-fp">0</b></div>
    <div class="kpi good"><span>漏检放行</span><b id="k-miss">0</b></div>
  </section>

  <main class="grid">
    <section class="panel">
      <div class="panel-h">
        <h2>实时内容流</h2>
        <div class="filters">
          <button class="fchip on" data-f="all">全部</button>
          <button class="fchip" data-f="hit">仅处置</button>
          <button class="fchip" data-f="miss">仅漏检</button>
          <button class="fchip" data-f="fp">仅误伤</button>
        </div>
      </div>
      <div class="stream" id="stream">
        <div class="empty" id="empty">点「播放」开始重放 · 建议 4× 速度看全流程</div>
      </div>
    </section>

    <aside class="side">
      <section class="panel">
        <div class="panel-h"><h2>攻击波次</h2><span class="hint" id="w-hint">0 / 3 已开打</span></div>
        <div id="waves"></div>
      </section>

      <section class="panel">
        <div class="panel-h"><h2>处置分档</h2><span class="hint" id="t-hint">累计</span></div>
        <div id="tiers"></div>
      </section>

      <section class="panel">
        <div class="panel-h"><h2>高危账号</h2><span class="hint">按命中条数</span></div>
        <div id="accts"><div class="empty">暂无</div></div>
      </section>
    </aside>
  </main>

  <section class="panel recon" id="recon" hidden>
    <div class="panel-h">
      <h2>跑完了 · 与静态评测对账</h2>
      <span class="hint">同一引擎、同一阈值、同一份数据，F1 必须逐位一致</span>
    </div>
    <table>
      <thead><tr><th>波次</th><th style="text-align:right">模拟 F1</th>
      <th style="text-align:right">评测 F1</th><th style="text-align:right">差</th>
      <th>结论</th></tr></thead>
      <tbody id="recon-body"></tbody>
    </table>
  </section>

  <footer>
    <b>场景设定</b>（人为假设，非实测）：审核工位 1 人 · 产能 __CAP__ 条/分钟（5 秒/条）<span class="sep">|</span>
    观察档抽检 __SAMPLE__%<span class="sep">|</span>时间轴 __MIN__ 分钟，事件按模拟时刻推进
    <br><b>数据来源</b>：三波分别回放 <code>text_test_adv</code> / <code>text_test_pervasive</code> /
    <code>text_test_holdout</code> 各 __NW__ 条；基线回放 <code>text_test_clean</code>。
    判定调用 <code>raven.pipeline.TextEngine</code>，阈值 __THR__，分档 0.82 / 0.68 / 0.40。
    <br><b>边界</b>：本演练的违规内容占 __RISK__%（合成数据按 2:1 抽样），真实社区远低于此，
    所以"拦截 598 条"这种绝对量不能当线上容量规划的依据，只能看比例与相对关系。
    单条时延为本次运行的实测值，跨机器会变，对外只能说 0.4~0.5 ms 区间；
    运营指标依赖上面两项假设，换假设数值就变。
  </footer>
</div>

<script>
const D = __DATA__;

const EV = D.events, N = EV.length;
const $ = id => document.getElementById(id);
const WNAME = {}, WSHORT = {};
D.meta.waves.forEach(w => { WNAME[w.key] = w.name; WSHORT[w.key] = w.name.split('·')[0].trim(); });
WNAME.clean = D.meta.baseline.name; WSHORT.clean = '基线';

const ACT_TXT = {block:'拦截', review:'复核', watch:'观察', allow:'放行'};

let clock = 0, idx = 0, timer = null, speed = 1, filter = 'all';
let st = null, recent = [];

function initState(){
  st = {total:0, block:0, review:0, watch:0, allow:0, fp:0, miss:0,
        waves:{}, tiers:{block:0, review:0, watch:0, allow:0}, acct:{}};
  D.meta.waves.concat([D.meta.baseline]).forEach(w => st.waves[w.key] = {n:0, hit:0});
}

/* ---------- 右侧面板骨架 ---------- */
function buildSide(){
  $('waves').innerHTML = D.meta.waves.map(w => `
    <div class="wave" id="wv-${w.key}">
      <div class="wave-t"><span class="dot"></span>${w.name}
        <span class="wave-n" id="wn-${w.key}">0 / 0</span></div>
      <div class="wave-s">${w.note}</div>
      <div class="track"><i id="wb-${w.key}"></i></div>
    </div>`).join('') + `
    <div class="wave" id="wv-clean">
      <div class="wave-t"><span class="dot"></span>${D.meta.baseline.name}
        <span class="wave-n" id="wn-clean">0 / 0</span></div>
      <div class="wave-s">${D.meta.baseline.note}</div>
      <div class="track"><i id="wb-clean"></i></div>
    </div>`;

  const TIER = {block:'拦截', review:'限流 + 复核', watch:'观察', allow:'放行'};
  $('tiers').innerHTML = ['block','review','watch','allow'].map(t => `
    <div class="tier ${t}"><span class="nm">${TIER[t]}</span>
      <span class="tb"><i id="tb-${t}"></i></span>
      <span class="vv" id="tv-${t}">0</span></div>`).join('');
}

/* ---------- 事件卡片 ---------- */
function card(e){
  const cls = ['ev', e.act];
  if (e.label === 'risk' && e.act === 'allow') cls.push('miss');
  if (e.label === 'benign' && e.act === 'block') cls.push('fp');
  const wkey = e.w === 'clean' ? 'clean' : e.w;
  const wtag = e.w === 'clean' ? '<span class="wtag">基线</span>'
      : `<span class="wtag w${D.meta.waves.findIndex(x=>x.key===e.w)+1}">${WSHORT[e.w]}</span>`;
  const normLine = (e.norm && e.norm !== e.raw)
      ? `<div class="ev-norm">→ <b>${esc(e.norm)}</b></div>` : '';
  const diff = e.label === 'risk'
      ? (e.act === 'allow' ? '<span class="truth-bad">漏检</span>' : '<span class="truth-ok">正确命中</span>')
      : (e.act === 'block' ? '<span class="truth-bad">误伤</span>'
         : (e.act === 'review' ? '<span class="truth-bad">误限流</span>' : '<span class="truth-ok">正确放行</span>'));
  const hits = e.hits.length ? e.hits.join('、') : '—';
  const ops = e.ops.length ? e.ops.join(' / ') : '无变形';
  const rec = e.rec.length ? ` · 还原：${e.rec.join('、')}` : '';
  return `<div class="${cls.join(' ')}" data-i="${e.i}">
    <div class="ev-top">
      <span class="ev-t">${e.ts}</span><span class="ev-a">${e.acct}</span>${wtag}
      <span class="ev-p" style="color:${pcolor(e.act)}">${e.p.toFixed(3)}</span>
      <span class="chip ${e.act}">${ACT_TXT[e.act]}</span>
    </div>
    <div class="ev-txt">${esc(e.raw)}</div>
    ${normLine}
    <div class="ev-d">
      <span class="kv">类目 <code>${e.sub}</code></span>
      <span class="kv">规则分 <code>${e.pr.toFixed(2)}</code></span>
      <span class="kv">命中 <code>${esc(hits)}</code>${rec}</span><br>
      <span class="kv">变形 <code>${esc(ops)}</code></span>
      <span class="kv">真值 <code>${e.label === 'risk' ? '违规' : '正常'}</code> → ${diff}</span>
      <span class="kv">耗时 <code>${e.ms} ms</code></span>
      ${e.neg > 0 ? `<br><span class="kv">否定语境信号 <code>${e.neg}</code>（可能在举报而非广告）</span>` : ''}
    </div>
  </div>`;
}
const pcolor = a => ({block:'var(--block)', review:'var(--review)',
                      watch:'var(--watch)', allow:'var(--faint)'}[a]);
function esc(s){return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

function match(e){
  if (filter === 'all') return true;
  if (filter === 'hit') return e.act !== 'allow';
  if (filter === 'miss') return e.label === 'risk' && e.act === 'allow';
  if (filter === 'fp') return e.label === 'benign' && e.act === 'block';
  return true;
}

let frag = null;
function reveal(e){
  st.total++;
  st[e.act]++;
  st.tiers[e.act]++;
  const w = st.waves[e.w] || (st.waves[e.w] = {n:0, hit:0});
  w.n++;
  if (e.act === 'block' || e.act === 'review') w.hit++;
  if (e.label === 'benign' && e.act === 'block') st.fp++;
  if (e.label === 'risk' && e.act === 'allow') st.miss++;
  if (e.act === 'block' || e.act === 'review'){
    const a = st.acct[e.acct] || (st.acct[e.acct] = {hits:0, maxp:0});
    a.hits++; a.maxp = Math.max(a.maxp, e.p);
  }
  if (match(e)){
    recent.unshift(e);
    if (recent.length > 220) recent.pop();
    if (!frag) frag = document.createDocumentFragment();
    const tpl = document.createElement('template');
    tpl.innerHTML = card(e);
    frag.appendChild(tpl.content.firstElementChild);
  }
}

function flush(){
  if (!frag) return;
  const s = $('stream');
  const e0 = $('empty'); if (e0) e0.remove();
  s.insertBefore(frag, s.firstChild);
  frag = null;
  while (s.children.length > 240) s.removeChild(s.lastChild);
  s.scrollTop = 0;
}

/* ---------- 刷新面板 ---------- */
function renderSide(){
  let open = 0;
  D.meta.waves.forEach(w => {
    const s = st.waves[w.key] || {n:0, hit:0};
    const total = D.waves.find(x => x.key === w.key).n;
    $('wn-' + w.key).textContent = `${s.n} / ${total}`;
    $('wb-' + w.key).style.width = (100 * s.n / total) + '%';
    const el = $('wv-' + w.key);
    const active = clock >= w.start && clock <= w.end;
    el.classList.toggle('active', active);
    el.classList.toggle('hit', s.hit > 0);
    if (s.n > 0) open++;
  });
  const c = st.waves.clean || {n:0};
  $('wn-clean').textContent = `${c.n} / ${D.n - D.waves.filter(x=>x.key!=='clean').reduce((a,b)=>a+b.n,0)}`;
  $('wb-clean').style.width = (100 * c.n / 270) + '%';
  $('w-hint').textContent = `${open} / 3 已开打`;

  const max = Math.max(1, st.total);
  ['block','review','watch','allow'].forEach(t => {
    $('tv-' + t).textContent = st.tiers[t];
    $('tb-' + t).style.width = (100 * st.tiers[t] / max) + '%';
  });

  const arr = Object.entries(st.acct).sort((a,b) => b[1].hits - a[1].hits).slice(0, 5);
  if (!arr.length){ $('accts').innerHTML = '<div class="empty">暂无</div>'; }
  else {
    $('accts').innerHTML = arr.map(([k,v]) => {
      const cls = v.hits >= 3 ? 'ban' : (v.hits === 2 ? 'mute' : 'watch');
      const pn = v.hits >= 3 ? '封禁' : (v.hits === 2 ? '禁言' : '观察');
      return `<div class="acct"><span class="nm">${k}</span>
        <span class="pl ${cls}">${pn}</span>
        <span class="n">${v.hits} 次 · max ${v.maxp.toFixed(2)}</span></div>`;
    }).join('');
  }

  $('k-total').innerHTML = `${st.total}<small>/${N}</small>`;
  $('k-block').textContent = st.block;
  $('k-review').textContent = st.review;
  $('k-watch').textContent = st.watch;
  $('k-fp').textContent = st.fp;
  $('k-miss').textContent = st.miss;

  const hh = String(21 + Math.floor(clock / 60)).padStart(2, '0');
  const mm = String(Math.floor(clock % 60)).padStart(2, '0');
  const ss = String(Math.floor((clock % 1) * 60)).padStart(2, '0');
  $('clock').textContent = `${hh}:${mm}:${ss}`;
  $('prog').style.width = (100 * clock / D.meta.total_min) + '%';

  let ph = '基线流量', hot = false;
  D.meta.waves.forEach(x => { if (clock >= x.start && clock <= x.end){ ph = x.name; hot = true; } });
  if (clock >= D.meta.total_min) ph = '演练结束';
  $('phase').textContent = ph;
  $('phase').classList.toggle('hot', hot);
}

/* ---------- 结束对账 ---------- */
function finish(){
  const r = D.reconcile;
  $('recon').hidden = false;
  const gapRisk = D.events.filter(e => e.label === 'risk' && e.hit && e.act === 'allow').length;
  $('recon-body').innerHTML = r.rows.map(x => {
    const ok = Math.abs(x.diff) < 1e-9;
    return `<tr>
      <td>${x.wave}</td>
      <td class="num">${x.sim_f1.toFixed(4)}</td>
      <td class="num">${x.eval_f1.toFixed(4)}</td>
      <td class="num">${x.diff >= 0 ? '+' : ''}${x.diff.toFixed(6)}</td>
      <td class="${ok ? 'tag-ok' : 'tag-no'}">${ok ? '一致' : '不一致'}</td></tr>`;
  }).join('') + `<tr>
      <td colspan="5" style="color:var(--dim);line-height:1.95">
        <b style="color:var(--ink2)">这 60 分钟里发生了什么</b> ——
        进审 ${D.ops.review_arrivals} 条（占全量 ${(D.ops.review_rate*100).toFixed(1)}%），
        峰值积压 ${D.ops.peak_backlog} 条，平均等 ${D.ops.avg_wait_min} 分钟，工位利用率 ${(D.ops.utilization*100).toFixed(0)}%；
        ${D.accounts.accounts} 个账号，单条漏检 ${D.accounts.missed_posts} 条，
        其中 ${D.accounts.rescued_by_account} 条被同账号的其他帖子兜住（兜底率 ${(D.accounts.rescued_rate*100).toFixed(1)}%）。
        <br>放行的违规内容共 ${st.miss} 条：${D.accounts.missed_posts} 条是模型没命中，
        ${gapRisk} 条是 p 落在 __THR__~0.40 的阈值缝隙里（模型判违规、处置层判放行）。
        这个缝隙是二分类阈值与分档阈值不成套造成的，改法见 sim/README.md。</td></tr>`;
  $('phase').textContent = '演练结束 · 下方有对账';
}

/* ---------- 播放控制 ---------- */
function tick(){
  clock += 0.05 * speed;
  if (clock > D.meta.total_min) clock = D.meta.total_min;
  while (idx < N && EV[idx].t <= clock) reveal(EV[idx++]);
  flush();
  renderSide();
  if (idx >= N && clock >= D.meta.total_min){
    clearInterval(timer); timer = null;
    $('play').textContent = '重播';
    finish();
  }
}

function play(){
  if (timer) { clearInterval(timer); timer = null; $('play').textContent = '继续'; return; }
  if (idx >= N && clock >= D.meta.total_min) reset();
  $('play').textContent = '暂停';
  timer = setInterval(tick, 50);
}

function reset(){
  clearInterval(timer); timer = null;
  clock = 0; idx = 0; recent = []; initState();
  $('stream').innerHTML = '<div class="empty" id="empty">点「播放」开始重放 · 建议 4× 速度看全流程</div>';
  $('recon').hidden = true;
  $('play').textContent = '播放';
  buildSide(); renderSide();
}

$('play').onclick = play;
$('reset').onclick = reset;
document.querySelectorAll('.sp').forEach(b => b.onclick = () => {
  document.querySelectorAll('.sp').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); speed = +b.dataset.speed;
});
document.querySelectorAll('.fchip').forEach(b => b.onclick = () => {
  document.querySelectorAll('.fchip').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); filter = b.dataset.f;
  const s = $('stream');
  // 按"已经出现过的事件"筛，不是只筛最近 220 条缓冲区
  const list = EV.slice(0, idx).filter(match).slice(-300).reverse();
  if (!list.length) { s.innerHTML = '<div class="empty">当前筛选下没有事件</div>'; return; }
  s.innerHTML = list.map(card).join('');
  s.scrollTop = 0;
});

$('stream').addEventListener('click', ev => {
  const c = ev.target.closest('.ev');
  if (c) c.classList.toggle('open');
});

initState(); buildSide(); renderSide();
</script>
</body>
</html>"""


def render(metrics, records, out_path):
    data = _payload(metrics, records)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    scn = metrics["scenario"]
    risk_pct = 100.0 * sum(1 for r in records if r["label"] == "risk") / max(1, len(records))
    html = (TEMPLATE
            .replace("__TITLE__", scn["title"] + " · Raven")
            .replace("__HEAD_TITLE__", scn["title"])
            .replace("__T0__", scn["t0"])
            .replace("__N__", str(metrics["n"]))
            .replace("__CAP__", f"{metrics['assumptions']['review_capacity_per_min']:g}")
            .replace("__SAMPLE__", f"{metrics['assumptions']['sample_rate'] * 100:g}")
            .replace("__MIN__", str(scn["total_min"]))
            .replace("__NW__", "270")
            .replace("__RISK__", f"{risk_pct:.1f}")
            .replace("__THR__", str(metrics["engine"]["threshold"]))
            .replace("__DATA__", blob))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    return out_path
