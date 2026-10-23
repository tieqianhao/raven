// demo 冒烟测试：用一个极简 DOM 桩跑一遍页面脚本，确认初始化与渲染不抛异常。
// 跑法：node tools/dom_smoke_test.js
//
// 注意：这里**按页面里的顺序依次执行所有 <script> 块**，不再单独 load raven-lite.js。
// 因为内核已经内联进 index.html 了，这个测试顺带就在验证"单文件自包含"这件事成立——
// 一旦有人把内核改回外部引用，这里会因为找不到 RavenLite 直接失败。
//
// 用 vm.runInThisContext 而不是 eval：页面脚本里的 const/let 是词法声明，
// eval 会给它们一个独立作用域，外面的断言根本看不见 CFG，会误判成"没注入"。
const fs = require('fs'), path = require('path');
const assert = require('assert');
const vm = require('vm');

const store = {};
function el(id) {
  if (store[id]) return store[id];
  const o = {
    id, _html: '', _text: '', style: {}, dataset: {}, value: '', className: '',
    set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html; },
    set textContent(v) { this._text = v; }, get textContent() { return this._text; },
    addEventListener() {},
  };
  store[id] = o; return o;
}
global.document = { getElementById: el };
global.window = global;
global.TextEncoder = TextEncoder;

const html = fs.readFileSync(path.join(__dirname, '..', 'demo', 'index.html'), 'utf8');
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert(blocks.length >= 2, `期望至少 2 个内联 script（内核 + 主逻辑），实际 ${blocks.length}`);

for (const code of blocks) vm.runInThisContext(code);

// 在同一个全局词法环境里取结果
const probe = vm.runInThisContext(`(function(){
  const g = document.getElementById.bind(document);
  const strip = g('strip').innerHTML, steps = g('steps').innerHTML, imgrow = g('imgrow').innerHTML;
  return {
    hasKernel: typeof RavenLite !== 'undefined',
    dim: (typeof CFG !== 'undefined' && CFG && CFG.dim) || 0,
    score: g('score').textContent,
    badge: g('badge').textContent,
    stats: (strip.match(/class="stat"/g) || []).length,
    steps: (steps.match(/class="step"/g) || []).length,
    hits: g('hits').innerHTML.replace(/<[^>]+>/g, '').trim(),
    imgs: (imgrow.match(/class="icase"/g) || []).length,
  };
})()`);

assert(probe.hasKernel, '内核未加载：index.html 可能又变回外部引用 raven-lite.js 了');
assert(probe.dim > 0, 'CFG 未注入');
assert(probe.score && probe.score.length, 'score 未渲染');
assert(probe.stats >= 5, `指标卡数量异常：${probe.stats}`);
assert(probe.steps >= 1, `归一化步骤未渲染：${probe.steps}`);
assert(probe.hits.length > 0, '证据区为空');
assert(probe.imgs >= 1, `图像案例未渲染：${probe.imgs}`);

console.log('score =', probe.score, '| badge =', probe.badge);
console.log('指标卡 =', probe.stats, '| 步骤行 =', probe.steps, '| 图像案例 =', probe.imgs);
console.log('证据 =', probe.hits.slice(0, 60));
console.log('[ok] demo 渲染正常，单文件自包含成立');
