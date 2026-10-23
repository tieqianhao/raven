# -*- coding: utf-8 -*-
"""对抗性文本归一化层。

设计前提（也是整个项目最核心的一条判断）：
    变形手法是无穷的，规则是有限的。所以检测不能建立在"枚举变体"上，
    而要先做**语义等价的折叠**——把 (薇信 / VX / 卫星 / 加vx) 折叠成同一个
    规范形，让下游只需要认识一种写法。

归一化链路（顺序不可随意调换，理由见每步注释）：
    NFKC -> 去不可见字符 -> 去 emoji -> 转小写 -> 展开拼音缩写
         -> 谐音/形近折叠 -> 繁转简 -> 折叠 CJK 间分隔符 -> 折叠重复字符
"""
import re
import unicodedata

from .lexicon import HOMOPHONE, PINYIN_ABBR, TRAD_TO_SIMP

# 不可见 / 格式控制字符（零宽空格、方向控制符、软连字符、BOM）
INVISIBLE = set(
    "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u2060\u2061\u2062\u2063\u2064\ufeff\u00ad\u180e"
)

EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF"
    "\U00002190-\U000021FF"
    "\U0001F1E6-\U0001F1FF"
    "\uFE0F\u20E3\u3030\u303D"
    "]+"
)

# CJK 之间可能被插入的分隔符（点、短横、下划线、空格、星号、中点、波浪号等）
SEP_RE = re.compile(r"[·・\.\-_~—–\*\s│|/\\]+")

LATIN_TOKEN = re.compile(r"(?<![a-z0-9])[a-z]{2,6}(?![a-z0-9])")

CJK_RANGES = (
    (0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF),
    (0x3000, 0x303F),  # CJK 标点
)


def is_cjk(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in CJK_RANGES)


# ----------------------------------------------------------------- 反向索引
def _build_reverse():
    """变体 -> 规范词，按变体长度降序，保证长词优先（'加vx' 先于 'vx'）"""
    rev = {}
    for canon, variants in HOMOPHONE.items():
        for v in variants:
            rev[v.lower()] = canon
    return dict(sorted(rev.items(), key=lambda kv: -len(kv[0])))


VARIANT2CANON = _build_reverse()


# --------------------------------------------------------------- 分步算子
def step_nfkc(s: str) -> str:
    """① 全角/兼容字符标准化：ＷＸ->wx、①->1、㈱->(株)。
    必须先做，否则后续正则和关键词全部对不上全角写法。"""
    return unicodedata.normalize("NFKC", s)


def step_strip_invisible(s: str) -> str:
    """② 去掉零宽字符（'微\\u200b信' 是社区最常见的绕过手法，肉眼不可见）"""
    return "".join(ch for ch in s if ch not in INVISIBLE and unicodedata.category(ch) != "Cf")


def step_strip_emoji(s: str) -> str:
    """③ 去 emoji。emoji 常被用来做视觉分隔（'外🔥挂'）"""
    return EMOJI_RE.sub("", s)


def step_lower(s: str) -> str:
    return s.lower()


def step_expand_abbr(s: str) -> str:
    """⑤ 展开独立出现的拼音缩写（vx / wx / dl / wg）。
    只处理独立 latin token，避免把 'game' 里的 'g' 展开成 '挂'。"""
    def repl(m):
        tok = m.group(0)
        return PINYIN_ABBR.get(tok, tok)
    return LATIN_TOKEN.sub(repl, s)


def step_homophone(s: str) -> str:
    """⑥ 谐音/形近折叠。长变体优先，避免 '加vx' 被 'vx' 先吃掉变成 '加微信微信'。"""
    for variant, canon in VARIANT2CANON.items():
        if variant in s:
            s = s.replace(variant, canon)
    return s


def step_trad2simp(s: str) -> str:
    """⑦ 繁转简（本项目只覆盖语料涉及字符，非完整 OpenCC）"""
    return "".join(TRAD_TO_SIMP.get(ch, ch) for ch in s)


def step_collapse_sep(s: str) -> str:
    """⑧ 折叠分隔符：'外·挂' / '外 挂' / '出-售' -> '外挂' / '出售'。
    取舍：只在分隔符挨着 CJK 时删除，纯英文之间的空格保留（否则会把英文单词粘死）。
    反复扫描，处理 '外·-·挂' 这种多重分隔。"""
    changed = True
    while changed:
        changed = False
        out = []
        i = 0
        while i < len(s):
            ch = s[i]
            if ch == " " or ch in "·・.-_~—–*│|/\\":
                j = i
                while j < len(s) and (s[j] == " " or s[j] in "·・.-_~—–*│|/\\"):
                    j += 1
                prev = out[-1] if out else ""
                nxt = s[j] if j < len(s) else ""
                # 只要分隔符的任一侧是 CJK 就吞掉：
                # 不然 "外·挂·，·自·瞄" 会在中文标点处断掉，只还原一半。
                if is_cjk(prev) or is_cjk(nxt):
                    changed = True   # 丢弃这段分隔符
                    i = j
                    continue
            out.append(ch)
            i += 1
        s = "".join(out)
    return s


def step_collapse_repeat(s: str) -> str:
    """⑨ 折叠重复字符：'稳稳定定不封' -> '稳定不封'。
    只折叠 CJK 字符的 >=3 次重复——数字/英文不折，否则 '999' 会变成 '9'。"""
    out = []
    i = 0
    while i < len(s):
        j = i
        while j < len(s) and s[j] == s[i]:
            j += 1
        run = j - i
        if run >= 3 and is_cjk(s[i]):
            out.append(s[i])
        else:
            out.append(s[i:j])
        i = j
    return "".join(out)


# 顺序说明：**必须先折叠分隔符，再做谐音折叠**。
# 反例（我第一版就踩了）：'薇·信' 被分隔符切开，谐音表里只有 '薇信'，
# 于是 '薇·信' 永远匹配不上，整条链路白跑。这个顺序错误在 clean 测试集上完全看不出来，
# 只有逐字符分隔的对抗样本会把它暴露出来——这也是为什么要专门造 pervasive 测试集。
PIPELINE = [
    ("NFKC 兼容字符标准化", step_nfkc),
    ("剥离零宽/不可见字符", step_strip_invisible),
    ("剥离 emoji 分隔", step_strip_emoji),
    ("转小写", step_lower),
    ("展开拼音缩写", step_expand_abbr),
    ("折叠分隔符", step_collapse_sep),
    ("谐音形近折叠", step_homophone),
    ("繁转简", step_trad2simp),
    ("再折叠一次分隔符", step_collapse_sep),
    ("折叠重复字符", step_collapse_repeat),
]

MAX_ROUNDS = 3


def normalize(text: str, with_trace: bool = False):
    """返回 (规范形, trace)。trace 记录每一步的真实输入输出，供报告和 demo 展示。"""
    trace = []
    cur = text or ""
    for _round in range(MAX_ROUNDS):
        before_round = cur
        for name, fn in PIPELINE:
            nxt = fn(cur)
            if with_trace and nxt != cur:
                trace.append({"step": name, "before": cur, "after": nxt})
            cur = nxt
        if cur == before_round:
            break
    cur = cur.strip()
    return (cur, trace) if with_trace else cur


def recovered_terms(raw: str, norm: str):
    """找出"原始文本里没有、但归一化后出现"的词典命中项。

    这是可解释性的一部分：报告里给人工复核看的不是"模型打了 0.91"，
    而是"外挂在原文里写作 外·卦，还原后命中'外挂'"。
    """
    from .lexicon import KEYWORDS
    terms = set()
    for kws in KEYWORDS.values():
        for _, w in kws:
            terms.add(w)
    hit = []
    for t in sorted(terms, key=lambda x: -len(x)):
        if t in norm and t not in raw.lower() and not (t in raw):
            hit.append(t)
    return hit
