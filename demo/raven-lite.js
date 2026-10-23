/* Raven-Lite —— 浏览器端推理内核。
 *
 * 存在意义：demo 不连后端也能算，说明"这套判定逻辑是可以被完整解释和迁移的"，
 * 而不是"调了个接口返回 0.91"。
 *
 * 与 Python 侧必须逐位一致，因此：
 *   - 哈希用 FNV-1a（对 UTF-8 字节做运算，JS 用 TextEncoder 取同样的字节）
 *   - 特征、规则分、饱和函数、阈值全部照抄 raven/ 下的实现
 * 一致性由 tools/parity_test.py 用 200 条样本对拍验证。
 */
(function (root) {
  "use strict";

  var CFG = null;

  // ------------------------------------------------------------ 基础
  var ZERO_WIDTH = /[\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\u2061\u2062\u2063\u2064\ufeff\u00ad\u180e]/g;
  var EMOJI = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{2190}-\u{21FF}\u{1F1E6}-\u{1F1FF}\uFE0F\u20E3\u3030\u303D]+/gu;
  var SEPS = "·・.-_~—–*│|\\/ ";
  var LATIN = /(?<![a-z0-9])[a-z]{2,6}(?![a-z0-9])/g;

  function isCJK(ch) {
    if (!ch) return false;
    var o = ch.codePointAt(0);
    return (o >= 0x4e00 && o <= 0x9fff) || (o >= 0x3400 && o <= 0x4dbf) ||
           (o >= 0xf900 && o <= 0xfaff) || (o >= 0x3000 && o <= 0x303f);
  }

  function stripInvisible(s) {
    return s.replace(ZERO_WIDTH, "").replace(/[\u{E0000}-\u{E007F}]/gu, "");
  }

  function collapseSep(s) {
    var changed = true, guard = 0;
    while (changed && guard++ < 6) {
      changed = false;
      var out = [], i = 0;
      while (i < s.length) {
        var ch = s[i];
        if (SEPS.indexOf(ch) >= 0) {
          var j = i;
          while (j < s.length && SEPS.indexOf(s[j]) >= 0) j++;
          var prev = out.length ? out[out.length - 1][out[out.length - 1].length - 1] : "";
          var nxt = j < s.length ? s[j] : "";
          if (isCJK(prev) || isCJK(nxt)) { changed = true; i = j; continue; }
        }
        out.push(ch); i++;
      }
      s = out.join("");
    }
    return s;
  }

  function collapseRepeat(s) {
    var out = [], i = 0;
    while (i < s.length) {
      var j = i;
      while (j < s.length && s[j] === s[i]) j++;
      var run = j - i;
      if (run >= 3 && isCJK(s[i])) out.push(s[i]);
      else out.push(s.slice(i, j));
      i = j;
    }
    return out.join("");
  }

  function buildVariantMap(homophone) {
    var rev = [];
    for (var canon in homophone) {
      var vs = homophone[canon];
      for (var k = 0; k < vs.length; k++) rev.push([vs[k].toLowerCase(), canon]);
    }
    rev.sort(function (a, b) { return b[0].length - a[0].length; });
    return rev;
  }

  var VARIANT_MAP = null;

  var STEPS = [
    ["NFKC 兼容字符标准化", function (s) { return s.normalize("NFKC"); }],
    ["剥离零宽/不可见字符", stripInvisible],
    ["剥离 emoji 分隔", function (s) { return s.replace(EMOJI, ""); }],
    ["转小写", function (s) { return s.toLowerCase(); }],
    ["展开拼音缩写", function (s) {
      return s.replace(LATIN, function (t) { return CFG.lexicon.abbr[t] || t; });
    }],
    ["折叠分隔符", collapseSep],
    ["谐音形近折叠", function (s) {
      for (var i = 0; i < VARIANT_MAP.length; i++) {
        var v = VARIANT_MAP[i];
        if (s.indexOf(v[0]) >= 0) s = s.split(v[0]).join(v[1]);
      }
      return s;
    }],
    ["繁转简", function (s) {
      var out = "";
      for (var i = 0; i < s.length; i++) out += (CFG.lexicon.trad[s[i]] || s[i]);
      return out;
    }],
    ["再折叠一次分隔符", collapseSep],
    ["折叠重复字符", collapseRepeat]
  ];

  function normalize(text) {
    var cur = text || "", trace = [];
    for (var r = 0; r < 3; r++) {
      var before = cur;
      for (var i = 0; i < STEPS.length; i++) {
        var nxt = STEPS[i][1](cur);
        if (nxt !== cur) trace.push({ step: STEPS[i][0], before: cur, after: nxt });
        cur = nxt;
      }
      if (cur === before) break;
    }
    return { text: cur.trim(), trace: trace };
  }

  // ------------------------------------------------------------ 特征
  var FNV_OFFSET = 0x811c9dc5, FNV_PRIME = 0x01000193;

  function fnv1a(str) {
    var bytes = new TextEncoder().encode(str);
    var h = FNV_OFFSET;
    for (var i = 0; i < bytes.length; i++) {
      h ^= bytes[i];
      h = Math.imul(h, FNV_PRIME) >>> 0;
    }
    return h >>> 0;
  }

  function featurize(text) {
    var dim = CFG.dim, vec = new Float64Array(dim);
    var cps = Array.from(text);           // 按码点切分，与 Python 的字符串索引语义一致
    for (var n = 1; n <= 3; n++) {
      var limit = cps.length - n + 1;
      for (var i = 0; i < limit; i++) {
        var gram = cps.slice(i, i + n).join("");
        vec[fnv1a(n + "|" + gram) % dim] += 1;
      }
    }
    var norm = 0, k;
    for (k = 0; k < dim; k++) {
      if (vec[k] > 0) { vec[k] = 1 + Math.log(vec[k]); norm += vec[k] * vec[k]; }
    }
    norm = Math.sqrt(norm);
    if (norm > 0) for (k = 0; k < dim; k++) vec[k] /= norm;
    return vec;
  }

  function countOcc(hay, needle) {
    if (!needle) return 0;
    var c = 0, i = 0;
    while ((i = hay.indexOf(needle, i)) >= 0) { c++; i += needle.length; }
    return c;
  }

  function ruleScore(text) {
    var hits = [], perClass = {}, cls, kws, total, i, cnt;
    for (cls in CFG.lexicon.keywords) {
      kws = CFG.lexicon.keywords[cls];
      total = 0;
      for (i = 0; i < kws.length; i++) {
        cnt = countOcc(text, kws[i][1]);
        if (cnt > 0) {
          total += kws[i][0] * Math.min(cnt, 3);
          hits.push({ cls: cls, term: kws[i][1], weight: kws[i][0], count: cnt });
        }
      }
      if (total > 0) perClass[cls] = total;
    }
    var neg = perClass["context_neg"] || 0;
    delete perClass["context_neg"];
    var top = 0;
    for (cls in perClass) top = Math.max(top, perClass[cls]);
    var risk = 1 - Math.exp(-top / 3.2);
    risk = risk * (1 - 0.55 * Math.min(1, neg / 4));
    return { risk: risk, hits: hits, perClass: perClass, neg: neg, top: top };
  }

  var CONTACT = ["微信", "加v", "加微", "群", "私信", "扣1", "扣个1", "私聊", "简介", "头像"];

  function handFeatures(text, rs) {
    var kwHits = rs.hits.filter(function (h) { return h.cls !== "context_neg"; });
    var digits = 0, cur = 0, i, ch;
    for (i = 0; i < text.length; i++) {
      ch = text[i];
      cur = (ch >= "0" && ch <= "9") ? cur + 1 : 0;
      digits = Math.max(digits, cur);
    }
    var contact = 0;
    for (i = 0; i < CONTACT.length; i++) if (text.indexOf(CONTACT[i]) >= 0) { contact = 1; break; }
    var f = [
      (rs.perClass["cheat"] || 0) / 10,
      (rs.perClass["boost"] || 0) / 10,
      (rs.perClass["account"] || 0) / 10,
      (rs.perClass["traffic"] || 0) / 10,
      rs.neg / 10,
      Math.min(kwHits.length / 6, 1),
      contact,
      Math.min(Math.log1p(Array.from(text).length) / 4, 1),
      Math.min(digits / 4, 1),
      Math.min(rs.top / 10, 1)
    ];
    return f;
  }

  // ------------------------------------------------------------ 推理
  function sigmoid(z) { return 1 / (1 + Math.exp(-Math.max(-30, Math.min(30, z)))); }

  function decide(p) {
    var t = CFG.decision_thresholds;
    if (p >= t.block) return { action: "block", action_cn: "拦截" };
    if (p >= t.review) return { action: "review", action_cn: "限流 + 人工复核" };
    if (p >= t.watch) return { action: "watch", action_cn: "观察（进抽检队列）" };
    return { action: "allow", action_cn: "放行" };
  }

  function subtype(perClass) {
    var best = "benign", val = 0;
    for (var k in perClass) if (perClass[k] > val) { val = perClass[k]; best = k; }
    return val >= 1.5 ? best : "benign";
  }

  function score(text) {
    var nz = normalize(text);
    var vec = featurize(nz.text);
    var rs = ruleScore(nz.text);
    var h = handFeatures(nz.text, rs);
    var z = CFG.text_model.b;
    for (var i = 0; i < CFG.dim; i++) if (vec[i] !== 0) z += CFG.text_model.w[i] * vec[i];
    for (var j = 0; j < h.length; j++) z += CFG.text_model.w[CFG.dim + j] * (2.0 * h[j]);
    var p = sigmoid(z);
    var d = decide(p);
    return {
      raw: text,
      norm: nz.text,
      trace: nz.trace,
      p: p,
      p_rule: rs.risk,
      subtype: subtype(rs.perClass),
      hits: rs.hits.filter(function (x) { return x.cls !== "context_neg"; }),
      neg: rs.neg,
      negHits: rs.hits.filter(function (x) { return x.cls === "context_neg"; }),
      action: d.action,
      action_cn: d.action_cn
    };
  }

  function init(cfg) {
    CFG = cfg;
    VARIANT_MAP = buildVariantMap(cfg.lexicon.homophone);
    return true;
  }

  root.RavenLite = { init: init, score: score, normalize: normalize, _cfg: function () { return CFG; } };
})(typeof window !== "undefined" ? window : globalThis);
