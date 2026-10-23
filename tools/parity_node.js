// Node 侧对拍脚本：读入文本列表 + 配置，输出 raven-lite.js 的打分
const fs = require("fs");
const path = require("path");

const code = fs.readFileSync(path.join(__dirname, "..", "demo", "raven-lite.js"), "utf8");
// raven-lite.js 把自身挂到 window 或 globalThis 上
eval(code);

const payload = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
RavenLite.init(payload.cfg);

const out = payload.texts.map(t => {
  const r = RavenLite.score(t);
  return { p: r.p, p_rule: r.p_rule, norm: r.norm };
});
fs.writeFileSync(process.argv[3], JSON.stringify(out));
console.log(`[node] scored ${out.length} texts`);
