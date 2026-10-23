# -*- coding: utf-8 -*-
"""合成评测数据集生成器。

为什么不用真实数据：真实社区数据涉及平台合规与隐私，无法公开分发；而"随便编几条样例"
又无法算指标。所以这里用**模板 + 槽位 + 可控扰动**的方式造一个带真值的数据集，
优点是每条样本的"变形手法"都有记录，可以按变形类型做分层评测（这正是项目要证明的东西）。

三套测试集，对应三种"泛化"问题：
    clean    —— 无变形，考察基础判别力
    adv      —— 使用**归一化层认识**的变形，考察"折叠"是否生效
    holdout  —— 使用归一化层**没见过**的谐音变体，考察"折叠"的天花板在哪
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from raven.lexicon import HOMOPHONE, KEYWORDS, PINYIN_ABBR, TRAD_TO_SIMP

GAMES = ["原神", "王者荣耀", "和平精英", "永劫无间", "三角洲行动", "无畏契约",
         "暗区突围", "英雄联盟", "崩坏星穹铁道", "蛋仔派对", "CS2", "我的世界"]
PRICES = ["30", "58", "88", "128", "188", "298", "520", "999"]
CITIES = ["成都", "重庆", "广州", "长沙", "杭州", "郑州", "武汉"]
IDS = ["abc123", "kf8800", "vip2026", "gzs666", "yx7788", "dd1688"]
EMOJI = ["🔥", "💰", "⚡", "✅", "🎮", "👉", "❗", "🚀"]

# ------------------------------------------------------------------ 模板库
TEMPLATES = {
    "cheat": [
        "出{game}外挂，自瞄透视锁头全功能，稳定不封，需要的加微信{id}",
        "最新{game}辅助工具{price}一个月，驱动级防封，一天一更新，卡密私信我",
        "卖{game}外挂，无后座一键爆头，内核驱动稳定三个月没封过，主页有联系方式",
        "{game}透视辅助{price}永久，支持安卓和模拟器，进群看演示视频",
        "外挂{game}，自瞄可调，稳定不封号，{price}一个月，加微信{id}发货",
        "接{game}内存修改定制，透视穿墙自瞄，工作室直供，价格便宜可小刀",
        "出{game}辅助{price}，防封驱动已更新，老客户包更新，私信我拿卡密",
        "{game}外挂{price}月卡，自瞄锁头透视三合一，国服第一辅助，稳定不封",
        "手机也能用的{game}辅助，自瞄+透视，{price}永久，点头像看简介",
        "出{game}外挂，{price}一个月，稳定不封，被封包退，加微信{id}",
        "工作室长期出{game}辅助，驱动级内核，支持全平台，{price}起",
        "{game}物理外挂{price}，自瞄透视，稳定不封，需要私聊",
        "卖{game}辅助工具，卡密{price}，一天一更，稳定不封，私信发货",
        "全网最稳{game}外挂，自瞄透视穿墙，{price}月付，加微信{id}",
        "{game}辅助{price}，自瞄锁头无后座，驱动级防封，进群领试用",
        "出{game}自瞄外挂，{price}一个月，稳定不封，客服在线秒回",
        "{game}外挂{price}起，透视自瞄，防封内核，需要的加微信{id}",
        "有人出{game}辅助吗，{price}以内的，加微信{id}",  # 买家口吻，仍然算黑产链条
        "{game}外挂{price}，稳定不封，被封全额退，私信我发卡密",
        "{game}辅助工具{price}永久，自瞄透视，支持手机，稳定不封号",
    ],
    "boost": [
        "{game}代练上分，手打国标，2天上王者，价格便宜可私聊",
        "接{game}代打，包王者包大师，不掉段，{price}起，可小刀",
        "专业{game}工作室代练，车队五排，{price}上分，量大优惠",
        "{game}代肝日常，包月{price}，代练等级任务，需要的私信我",
        "陪玩陪练{game}，小姐姐打手，代练上分，主页有联系方式",
        "接{game}代练，国标打手手打，不掉分，{price}起，私聊看图",
        "{game}代打上分，{price}一段，车队稳定连胜，包退",
        "代练{game}，手打不刷机，包上分，加微信{id}",
        "{game}代练工作室，{price}包王者，打手上号，全程手打",
        "接单{game}代打，{price}起，可小刀，包上分不掉段",
        "{game}代练{price}，国服打手，连胜包王者，私聊",
        "代肝{game}日常活动，{price}一周，代练任务，工作室直供",
        "{game}车队代练，五排上分，{price}包王者，稳定不掉段",
        "接{game}代练单，{price}一段，手打国标，包退",
    ],
    "account": [
        "出{game}账号，全皮肤满命，可小刀走平台，秒换绑",
        "收{game}号，价格好商量，私信发图，走平台交易",
        "卖{game}账号，{price}带走，全皮肤，可小刀，秒换绑解绑",
        "{game}账号交易，满级满配，{price}，走平台，找回包赔",
        "出{game}号，{price}，全皮肤，可小刀，绑定手机可解绑",
        "收{game}账号，预算{price}以内，走平台，私信我",
        "出{game}账号{price}，全皮肤满命，秒换绑，走平台担保",
        "买号卖号{game}，{price}起，全皮肤号，找回包赔",
        "租{game}账号，{price}一天，全皮肤，可小刀",
        "{game}号{price}出，满级全皮肤，走平台，秒换绑",
        "出{game}账号，{price}，可小刀，先到先得，私信我",
        "收{game}号{price}左右，全皮肤优先，走平台担保交易",
    ],
    "traffic": [
        "新人福利进群领{game}礼包，主页有联系方式",
        "点头像看简介，扣1拉你进群，{game}资源全都有",
        "加微信{id}领{game}内部福利，进群每天送皮肤",
        "看简介加微信，{game}礼包码免费送，先到先得",
        "评论区扣1，我私信你，{game}资源群每天更新",
        "{game}交流群，扣1进群，资源免费领，主页有方式",
        "扫码进群领{game}福利，活动仅限今天，加微信{id}",
        "引流{game}玩家进群，扣个1，我私信发你",
        "主页有联系方式，加微信{id}，{game}内部号领取",
        "进群领{game}皮肤兑换码，评论区留言扣1，我拉你",
        "私信我领{game}福利，加vx{id}，每天更新",
        "扣1拉群，{game}礼包免费领，点头像看简介",
    ],
    "benign": [
        "求推荐{game}里适合新手的角色，我现在只有三个五星",
        "今天排位连跪五把，队友全程挂机，太难了",
        "{game}这次活动肝完了，奖励一般，不太值",
        "萌新问一下{game}的{game}怎么打，卡了三天了",
        "刚抽到新角色，{game}这版本强度怎么样，值得练吗",
        "{game}新版本更新了，地图改动有点大，需要重新熟悉",
        "有人一起打{game}吗，晚上八点，都是新手别嫌弃",
        "{game}这个皮肤好看，但是价格有点贵，纠结中",
        "看完{game}的剧情，感觉支线比主线做得好",
        "求{game}的配队思路，我这套阵容总是被秒",
        "今天在{game}里遇到一个很厉害的大佬，全程指挥",
        "{game}的掉率是不是改了，刷了一晚上什么都没出",
        "求问{game}的隐藏任务怎么触发，攻略说得含糊",
        "被{game}的新手引导气到了，半小时全是教学",
        "{game}这波联动挺用心的，角色设计我很喜欢",
        "打{game}被对面的走位秀了，操作确实强",
        "记录一下{game}的第100小时，还是挺好玩的",
        "{game}我玩了两年，最近有点腻了，想换换",
        "求个{game}的开黑群，人多一点的，正规玩家群",
        "{game}的手柄适配做得很一般，摇杆漂移严重",
        "刚入坑{game}，请问先练哪个角色比较好",
        "{game}这次的版本平衡性调整，感觉有点过头了",
        "看完{game}的比赛，职业选手的意识真的强",
        "有没有{game}的同人图推荐，最近迷上了这个CP",
        "{game}的音乐是真的顶，原声带已经循环一天了",
        "想问下{game}的内存占用怎么这么大，我电脑快带不动",
        "{game}的客服回复好慢，提交的问题三天没处理",
        "今天终于把{game}全成就了，纪念一下",
        "{game}的联机延迟太高了，是不是我网络问题",
        "求{game}的模组推荐，想要画质增强的",
        "{game}这赛季的通行证奖励有点少，犹豫要不要买",
        "刚被{game}的boss教育了一晚上，容错太低了",
        "{game}的地图设计我很喜欢，探索感很强",
        "想找{game}的攻略视频，有推荐的吗",
        "{game}这个版本我角色满命了，开心",
        "请问{game}的存档能跨平台同步吗",
        "{game}最近是不是在打折，等好久了",
        "玩{game}三年了，第一次打上高段位，激动",
        "{game}的UI重做之后清爽多了，好评",
        "有没有人知道{game}这个bug怎么触发，我想复现玩一下",
    ],
    # 困难负样本：提到黑产词，但语境是举报/避雷/讨论，不是广告
    "mention": [
        "今天在群里看见有人卖{game}外挂，已经举报了",
        "提醒大家别信那些代练工作室，我被坑了{price}块",
        "官方最近在打击账号交易，别再买号了，风险很大",
        "刚遇到一个加微信卖外挂的，话术一模一样，别上当",
        "刷到有人发{game}辅助广告，我私信骂了一顿然后举报了",
        "{game}外挂最近好像变多了，官方是不是该管管",
        "有人私信我代练，我直接举报了，这种人真的多",
        "看到有人收号，提醒一句：走平台也有找回风险，小心",
        "前几天倒卖账号被封了，亏了{price}，血泪教训",
        "别买那些{price}永久的外挂，基本都是骗钱的",
        "{game}社区里那些进群领福利的，八成是钓鱼，别点",
        "今天举报了三个卖外挂的账号，希望官方快点处理",
        "有同学问我代练能不能买，我说别买，被封十年划不来",
        "网上那些透视自瞄的视频是剪辑的，实际用了就封号",
        "{game}官方公告说严查代练，抓到直接封，别碰",
        "上次有人拉我进群说送皮肤，我一看就是引流，直接退了",
        "买号被找回的经历：{price}买的，三天就没了，报警也没用",
        "微博上那些卖卡的，十个九个是骗子，别信",
        "说实话{game}外挂根本治不完，封一批又出一批",
        "请教一下，遇到卖外挂的应该在哪举报，效率高一点",
        "宿舍有人用外挂被封了机器码，笑死，活该",
        "代练这行业真的该管管，破坏游戏环境",
    ],
}

SUBTYPE_OF = {
    "cheat": "cheat", "boost": "boost", "account": "account",
    "traffic": "traffic", "benign": "benign", "mention": "mention",
}

CANON_TERMS = sorted(
    {w for kws in KEYWORDS.values() for _, w in kws} | set(HOMOPHONE),
    key=len, reverse=True,
)
SIMP2TRAD = {}
for t, s in TRAD_TO_SIMP.items():
    SIMP2TRAD.setdefault(s, t)
ABBR_OF = {v: k for k, v in PINYIN_ABBR.items() if len(v) >= 1}

# 归一化层【没见过】的谐音变体：用来测"折叠"的泛化天花板
HOLDOUT_HOMO = {
    "微信": ["围信", "薇芯儿", "w-e-i"],
    "外挂": ["外桂", "外卦儿"],
    "代练": ["带练", "袋练"],
    "账号": ["账昊", "帐昊"],
    "群": ["麇", "囷"],
    "私信": ["撕信"],
    "出售": ["出授"],
}

ZERO_WIDTH = ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"]
SEPS = ["·", " ", "-", "_", ".", "*", "|"]


# ---------------------------------------------------------------- 模板划分
def split_templates():
    """把模板分成 train / test 两份，**互不重叠**。

    这一步是整个评测诚实性的关键。如果训练集和测试集共用同一批模板，
    模型只要记住"这句话我见过"就能拿满分，测出来的不是判别力而是记忆力。
    """
    rng = random.Random(4242)
    train_tpl, test_tpl = {}, {}
    for cls, tpls in TEMPLATES.items():
        idx = list(range(len(tpls)))
        rng.shuffle(idx)
        k = max(1, len(tpls) // 3)
        test_idx = set(idx[:k])
        test_tpl[cls] = [t for i, t in enumerate(tpls) if i in test_idx]
        train_tpl[cls] = [t for i, t in enumerate(tpls) if i not in test_idx]
    return train_tpl, test_tpl


TRAIN_TPL, TEST_TPL = split_templates()
RISK_CLASSES = ("cheat", "boost", "account", "traffic")
MIX_PREFIX = ["楼主说得对，", "路过顺便说一句，", "同求，另外问下，", "谢谢分享，"]


# ---------------------------------------------------------------- 变形算子
def _spans(text):
    out = []
    for t in CANON_TERMS:
        start = 0
        while True:
            i = text.find(t, start)
            if i < 0:
                break
            out.append((i, i + len(t), t))
            start = i + 1
    return out


def _apply_on_span(text, s, e, t, op, rng, homo):
    """在一个指定词条上施加一种变形，返回 (新文本, 算子名)。"""
    if op == "zero_width":
        k = s + rng.randint(1, max(1, len(t) - 1))
        return text[:k] + rng.choice(ZERO_WIDTH) + text[k:], "zero_width"
    if op == "separator":
        return text[:s] + rng.choice(SEPS).join(t) + text[e:], "separator"
    if op == "emoji":
        k = s + rng.randint(1, max(1, len(t) - 1))
        return text[:k] + rng.choice(EMOJI) + text[k:], "emoji_inject"
    if op == "homophone":
        if t in homo:
            return text[:s] + rng.choice(homo[t]) + text[e:], "homophone"
        return text, None
    if op == "abbr":
        if t in ABBR_OF:
            return text[:s] + ABBR_OF[t] + text[e:], "pinyin_abbr"
        return text, None
    if op == "trad":
        conv = "".join(SIMP2TRAD.get(ch, ch) for ch in t)
        if conv != t:
            return text[:s] + conv + text[e:], "traditional"
        return text, None
    if op == "repeat":
        k = s + rng.randint(1, max(1, len(t) - 1))
        return text[:k] + text[k - 1] * 3 + text[k:], "char_repeat"
    return text, None


ADV_OPS = ["zero_width", "separator", "emoji", "homophone", "abbr", "trad", "repeat"]


def perturb_pervasive(text, rng):
    """逐字符分隔：把每个字符之间都塞一个分隔符 / 零宽字符。

    这是社区里最土但最有效的一种绕过：肉眼仍然一眼能读，关键词匹配全部失效。
    它对字符 n-gram 模型是毁灭性的（几乎没有一个原始 n-gram 能活下来），
    但对"先把分隔符折叠掉"的归一化层几乎无损——这正是本项目的核心论据。
    """
    sep = rng.choice(["·", "\u200b", "-", " ", "*", "🙂"])
    out = []
    for ch in text:
        out.append(ch)
        out.append(sep)
    text = "".join(out)
    # 再叠一点谐音，避免变成纯分隔符题
    ops = ["pervasive_sep"]
    for s0, e0, t in sorted(_spans(text), key=lambda x: -x[0]):
        if rng.random() < 0.4 and t in HOMOPHONE:
            text = text[:s0] + rng.choice(HOMOPHONE[t]) + text[e0:]
            ops.append("homophone")
    return text, ops


def perturb(text, rng, mode):
    """mode: None | 'adv' | 'pervasive' | 'holdout'

    说明：pervasive 走单独实现（不分词条，整句变形）。

    关键设计：**不只变形一个词**，而是对文本里所有可识别的词条逐条变形（概率 0.7）。
    真实规避者就是这么干的——他不会只把"外挂"写成"外·挂"，而是把整句都改掉。
    只变形一个词的话，剩下 90% 的原文仍然是强信号，测出来的"鲁棒性"是假的。
    """
    if mode is None:
        return text, []
    if mode == "pervasive":
        return perturb_pervasive(text, rng)
    homo = HOMOPHONE if mode == "adv" else HOLDOUT_HOMO
    ops = []
    # 全局：把个别字母/数字换成全角（NFKC 才能救）
    idxs = [i for i, ch in enumerate(text) if ch.isascii() and ch.isalnum()]
    if idxs and rng.random() < 0.35:
        for k in rng.sample(idxs, min(len(idxs), rng.randint(1, 2))):
            text = text[:k] + chr(ord(text[k]) + 0xFEE0) + text[k + 1:]
        ops.append("fullwidth")
    if rng.random() < 0.2:
        text = rng.choice(EMOJI) + text + rng.choice(EMOJI)
        ops.append("emoji_pad")

    spans = sorted(_spans(text), key=lambda x: -x[0])   # 从右往左，索引不失效
    for s, e, t in spans:
        if rng.random() > 0.7:
            continue
        op = "homophone" if mode == "holdout" else rng.choice(ADV_OPS)
        text, name = _apply_on_span(text, s, e, t, op, rng, homo)
        if name:
            ops.append(name)
    return text, ops


def fill(tpl, rng):
    return (tpl.replace("{game}", rng.choice(GAMES))
               .replace("{price}", rng.choice(PRICES))
               .replace("{city}", rng.choice(CITIES))
               .replace("{id}", rng.choice(IDS)))


def build_text(seed=2026, n_per_class=200, mode=None, tpl="train", with_accounts=True):
    import re
    rng = random.Random(seed)
    pool = TRAIN_TPL if tpl == "train" else TEST_TPL
    rows = []
    idx = 0
    for cls, tpls in pool.items():
        for _ in range(n_per_class):
            base = fill(rng.choice(tpls), rng)
            if rng.random() < 0.3:
                base = rng.choice(["【求助】", "求助各位大佬 ", "纯讨论：", "萌新问一句，"]) + base
            # 25% 截成短广告：只保留第一个分句，关键词密度成为唯一可用信号
            if rng.random() < 0.25:
                cut = re.split(r"[，,]", base)[0]
                if len(cut) >= 6:
                    base = cut
            # 4% 混合长文：正常评论里夹带广告（真实社区里最常见的一种）
            if cls in RISK_CLASSES and rng.random() < 0.04:
                base = rng.choice(MIX_PREFIX) + base

            text, ops = perturb(base, rng, mode)
            label = "benign" if cls in ("benign", "mention") else "risk"
            rows.append({
                "id": f"t{idx:05d}",
                "text": text,
                "label": label,
                "subtype": SUBTYPE_OF[cls],
                "source_class": cls,
                "perturb_ops": ops,
            })
            idx += 1
    rng.shuffle(rows)
    if with_accounts:
        # 账号聚合：70% 单帖账号，30% 多帖账号（同账号可能同时发正常帖与广告帖）
        acc = 0
        i = 0
        while i < len(rows):
            acc += 1
            k = rng.randint(2, 5) if rng.random() < 0.3 else 1
            for r in rows[i:i + k]:
                r["account"] = f"u{acc:04d}"
            i += k
    return rows


def main():
    out = os.path.join(os.path.dirname(__file__), "..", "artifacts", "dataset")
    os.makedirs(out, exist_ok=True)
    splits = {
        "train": build_text(seed=2026, n_per_class=110, mode=None),
        "val": build_text(seed=7, n_per_class=22, mode=None),
        "val_adv": build_text(seed=77, n_per_class=22, mode="adv"),
        "test_clean": build_text(seed=101, n_per_class=45, mode=None, tpl="test"),
        "test_adv": build_text(seed=202, n_per_class=45, mode="adv", tpl="test"),
        "test_pervasive": build_text(seed=404, n_per_class=45, mode="pervasive", tpl="test"),
        "test_holdout": build_text(seed=303, n_per_class=45, mode="holdout", tpl="test"),
    }
    for name, rows in splits.items():
        path = os.path.join(out, f"text_{name}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        nrisk = sum(1 for r in rows if r["label"] == "risk")
        ops = {}
        for r in rows:
            for o in r["perturb_ops"]:
                ops[o] = ops.get(o, 0) + 1
        print(f"{name:14s} n={len(rows):5d} risk={nrisk:4d} benign={len(rows)-nrisk:4d} ops={ops}")


if __name__ == "__main__":
    main()
