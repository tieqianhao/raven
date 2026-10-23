# -*- coding: utf-8 -*-
"""把 resume.html 转成单页 A4 简历的 .docx（Word 原生元素，可继续编辑）。

两个决定：

1. **不用 HTML→DOCX 转换器**。内置转换器对 flex 布局支持不好，项目名与日期的
   左右对齐会被拍平成一行文本，产物也缺少 sectPr（页面设置），打开就是 Letter 纸。
   简历这种"一行两头对齐"本质是制表位问题，用 tab stop + 段落边框 + 固定行距最准确。

2. **内容从 resume.html 里解析，不再手写第二份**。之前两版内容各存一处，改一次
   PDF 忘记改 Word 就会静默脱节——这是最容易出、也最难被发现的错。现在 HTML 是
   唯一内容源，本脚本只负责换一种"表达方式"。

单页保证：尺寸按 pt 精确计算，用微软雅黑真实字宽做折行测量，累加高度与可用高度
比较；超出直接报错，不静默出两页。

跑法：
    python resume/build_docx.py
"""
import os
import re
import sys
from html.parser import HTMLParser

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from PIL import ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "resume.html")
OUT = os.path.join(HERE, "余星乐-个人简历.docx")

FONT = "微软雅黑"
NAVY = RGBColor(0x1F, 0x3A, 0x5F)     # 主色：深墨蓝
INK = RGBColor(0x14, 0x29, 0x3F)      # 名字/小标题
BODY = RGBColor(0x24, 0x31, 0x3F)     # 正文
MUTED = RGBColor(0x5A, 0x6A, 0x7D)    # 日期等次要信息
ACCENT = RGBColor(0x12, 0x3A, 0x63)   # 数字强调

# ---------------- 版面尺寸（mm → pt） ----------------
MM = 72 / 25.4
PAGE_W, PAGE_H = 210 * MM, 297 * MM
M_TOP, M_BOT, M_L, M_R = 10.5 * MM, 9 * MM, 14 * MM, 14 * MM
CONTENT_W = PAGE_W - M_L - M_R          # ≈ 516pt
AVAIL_H = PAGE_H - M_TOP - M_BOT        # ≈ 786pt

BODY_SIZE = 9.5
LINE = 15.2                             # 固定行距（pt）。比 PDF 版略紧，为折行误差留余量
BULLET_IND = 3.1 * MM
SKILL_KEY_W = 18 * MM                   # 技能左列宽度，与 HTML 的 18mm 对齐

# ---------------- 字宽测量（用于折行与高度校验） ----------------
_F = {
    False: ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 100),
    True: ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 100),
}


def width_pt(text, size=BODY_SIZE, bold=False):
    """返回文本在给定字号下的宽度（pt）。PIL 用 100px em 测量后按比例缩放。"""
    return _F[bold].getlength(text) * size / 100.0


TOKEN = re.compile(r"(\*\*.+?\*\*|<<.+?>>)")


def runs_of(text):
    """把标记文本切成 (内容, 样式) 序列：**加粗** / <<强调数字>> / 普通。"""
    out = []
    for part in TOKEN.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append((part[2:-2], "bold"))
        elif part.startswith("<<") and part.endswith(">>"):
            out.append((part[2:-2], "accent"))
        else:
            out.append((part, "plain"))
    return out


def count_lines(text, size=BODY_SIZE, avail=CONTENT_W, first_avail=None):
    """贪心折行估算。中文逐字累加即可，西文可能偏保守（多算一点更安全）。"""
    segs = runs_of(text)
    lines, cur, limit = 1, 0.0, (first_avail if first_avail else avail)
    for seg, style in segs:
        bold = style != "plain"
        for ch in seg:
            w = width_pt(ch, size, bold)
            if cur + w > limit:
                lines += 1
                cur, limit = 0.0, avail
            cur += w
    return lines


# ---------------- 低层 XML 工具 ----------------
def set_font(run, size=BODY_SIZE, bold=False, color=BODY, spacing_pt=None):
    run.font.name = FONT
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:ascii"), FONT)
    rFonts.set(qn("w:hAnsi"), FONT)
    rFonts.set(qn("w:eastAsia"), FONT)
    if spacing_pt:                       # 字间距，单位 twips（1pt = 20）
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:val"), str(int(spacing_pt * 20)))
        rPr.append(sp)


def para_format(p, before=0.0, after=0.0, line=LINE, align=None,
                left_ind=0.0, first_ind=0.0):
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = Pt(line)           # 固定行距，保证与 PDF 版行高一致
    pf.left_indent = Pt(left_ind) if left_ind else Pt(0)
    pf.first_line_indent = Pt(first_ind) if first_ind else Pt(0)
    pf.widow_control = False
    if align is not None:
        p.alignment = align
    return p


# w:pBdr 在 CT_PPr 里必须排在这些元素之前；append 到末尾会违反 schema，
# Word 打开时会静默丢掉边框（WPS 更宽松，所以很容易漏检）。
_PBDR_SUCCESSORS = (
    "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
    "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN",
    "w:bidi", "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind",
    "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc",
    "w:textDirection", "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl",
    "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)


def _add_pbdr(p, edge, sz, color, space):
    pPr = p._p.get_or_add_pPr()
    pBdr = pPr.find(qn("w:pBdr"))
    if pBdr is None:
        pBdr = OxmlElement("w:pBdr")
        pPr.insert_element_before(pBdr, *_PBDR_SUCCESSORS)
    e = OxmlElement(f"w:{edge}")
    e.set(qn("w:val"), "single")
    e.set(qn("w:sz"), str(sz))
    e.set(qn("w:space"), str(space))
    e.set(qn("w:color"), color)
    pBdr.append(e)


def bottom_border(p, sz=5, color="C9D2DC", space=2):
    """段落下边框（分节标题的细线）。sz 单位 1/8 pt。"""
    _add_pbdr(p, "bottom", sz, color, space)


def top_border(p, sz=5, color="DFE5EC", space=4):
    _add_pbdr(p, "top", sz, color, space)


def add_text(p, text, size=BODY_SIZE, color=BODY, base_bold=False):
    for seg, style in runs_of(text):
        r = p.add_run(seg)
        if style == "bold":
            set_font(r, size, True, INK)
        elif style == "accent":
            set_font(r, size, True, ACCENT)
        else:
            set_font(r, size, base_bold, color)
    return p


# ---------------- 从 resume.html 读内容 ----------------
BUFFER_ROLES = {"sec-title", "sec-en", "row-left", "row-right", "note", "li",
                "skill-k", "skill-v", "name", "target", "contact"}


def _clean(s):
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]*\n\s*", " ", s)
    s = re.sub(r"[ ]{2,}", " ", s)
    s = re.sub(r"\s*｜\s*", "｜", s)
    return s.strip()


class _Parser(HTMLParser):
    """只认这份简历用到的结构，不是通用 HTML 解析器——够用且好改。

    内联标记的还原：<b> 与 .proj-name/.org → **；.num → <<>>；.role 前置竖线
    （HTML 里那是 CSS 伪元素，解析文本时拿不到，必须补）。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.name = ""
        self.target = ""
        self.contact = []
        self.sections = []
        self._stack = []
        self._bufs = []
        self._inline = []
        self._sec = None
        self._row = None
        self._bul = None
        self._skill = None
        self._skip = 0

    # --- 写入当前缓冲区 ---
    def _put(self, s):
        if self._bufs:
            self._bufs[-1][1].append(s)

    # --- 结构回调 ---
    def _commit(self, role, text):
        if role == "contact":
            self.contact.extend(_clean(x) for x in text.split("\n") if _clean(x))
            return
        text = _clean(text)
        if role == "sec-title":
            self._sec["title"] = text
        elif role == "sec-en":
            self._sec["en"] = text
        elif role == "row-left":
            self._row["left"] = text
        elif role == "row-right":
            self._row["right"] = text
        elif role == "note":
            self._sec["items"].append(("note", text))
        elif role == "li":
            self._bul.append(text)
        elif role == "skill-k":
            self._skill["k"] = text
        elif role == "skill-v":
            self._skill["v"] = text
        elif role == "name":
            self.name = text
        elif role == "target":
            self.target = text

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag in ("style", "script", "head", "title"):
            self._skip += 1
            return
        if self._skip:
            return

        if tag == "br":
            self._put("\n")
            return

        # 内联标记：必须成对，且不能落到结构栈上（否则 </span> 会把外层 div 弹掉）
        if tag == "b":
            self._inline.append(("b", "bold"))
            self._put("**")
            return
        if tag == "span":
            if cls == "num":
                self._inline.append(("span", "num"))
                self._put("<<")
            elif cls == "en":
                self._inline.append(("span", "en"))
                self._bufs.append(("sec-en", []))
            elif cls in ("proj-name", "org"):
                self._inline.append(("span", "bold"))
                self._put("**")
            elif cls == "role":
                self._inline.append(("span", None))
                self._put("｜")
            else:
                self._inline.append(("span", None))
            return

        role = None
        opened = None
        if tag == "div":
            if cls == "sec":
                self._sec = {"title": "", "en": "", "items": []}
                self.sections.append(self._sec)
                opened = "sec"
            elif cls == "sec-title":
                role = "sec-title"
            elif cls == "row":
                self._row = {"left": "", "right": ""}
                opened = "row"
            elif cls == "left":
                role = "row-left"
            elif cls == "right":
                role = "row-right"
            elif cls == "note":
                role = "note"
            elif cls == "proj":
                opened = "proj"
            elif cls == "skill":
                self._skill = {"k": "", "v": ""}
                opened = "skill"
            elif cls == "k":
                role = "skill-k"
            elif cls == "v":
                role = "skill-v"
            elif cls == "name":
                role = "name"
            elif cls == "target":
                role = "target"
            elif cls == "contact":
                role = "contact"
        elif tag == "ul":
            self._bul = []
            opened = "ul"
        elif tag == "li":
            role = "li"

        if role:
            self._bufs.append((role, []))
        self._stack.append((tag, opened, role))

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return
        if tag in ("b", "span"):
            m = None
            if self._inline:
                t, m = self._inline.pop()
            if m == "num":
                self._put(">>")
            elif m == "bold":
                self._put("**")
            elif m == "en" and self._bufs:
                r, buf = self._bufs.pop()
                self._commit(r, "".join(buf))
            return

        # 弹出到匹配的标签
        while self._stack:
            t, opened, role = self._stack.pop()
            if role:
                r, buf = self._bufs.pop()
                self._commit(r, "".join(buf))
            if opened == "row":
                self._sec["items"].append(("row", self._row["left"], self._row["right"]))
            elif opened == "ul":
                for text in self._bul:
                    self._sec["items"].append(("bul", text))
                self._bul = None
            elif opened == "skill":
                self._sec["items"].append(("skill", self._skill["k"], self._skill["v"]))
            if t == tag:
                break

    def handle_data(self, data):
        if self._skip:
            return
        self._put(data)


def parse_resume(path=SRC):
    with open(path, encoding="utf-8") as f:
        html = f.read()
    p = _Parser()
    p.feed(html)
    if not p.name or not p.sections:
        raise SystemExit("解析失败：resume.html 结构变了，请检查 .sec / .name 等 class")
    return p


# 间距（pt，对应 HTML 版 mm）
GAP_HEAD = 3.2 * MM      # 抬头下内边距
GAP_SEC = 4.0 * MM       # 板块上间距
GAP_SEC_TITLE = 1.5 * MM # 板块标题下间距
GAP_PROJ = 2.2 * MM      # 项目块下间距
GAP_FOOT = 4.0 * MM

# ---------------- 构建 ----------------
_TBLPR_AFTER_TBLW = (
    "w:jc", "w:tblCellSpacing", "w:tblInd", "w:tblBorders", "w:shd",
    "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption",
    "w:tblDescription",
)


def build():
    src = parse_resume()
    doc = Document()

    # 默认样式：不设的话 Word 会用 Calibri/宋体，中文版式全变
    st = doc.styles["Normal"]
    st.font.name = FONT
    st.font.size = Pt(BODY_SIZE)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    pf = st.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing = Pt(LINE)

    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin, sec.bottom_margin = Mm(10.5), Mm(9)
    sec.left_margin, sec.right_margin = Mm(14), Mm(14)
    sec.header_distance = Mm(6)
    sec.footer_distance = Mm(6)

    total = 0.0   # 已用高度累计（pt），用于单页校验

    # ---------- 抬头：1×2 无边框表格，左名字右联系方式 ----------
    t = doc.add_table(rows=1, cols=2)
    t.autofit = False
    tblPr = t._tbl.tblPr

    # 固定表格总宽 = 正文宽，否则 Word 会按内容重算列宽，右列被顶出去
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is None:
        tblW = OxmlElement("w:tblW")
        tblPr.insert_element_before(tblW, *_TBLPR_AFTER_TBLW)
    tblW.set(qn("w:type"), "dxa")
    tblW.set(qn("w:w"), str(int(CONTENT_W * 20)))

    # 去掉所有框线（简历不需要表格线）
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "none")
        e.set(qn("w:sz"), "0")
        borders.append(e)
    tblPr.insert_element_before(borders, "w:shd", "w:tblLayout",
                                "w:tblCellMar", "w:tblLook", "w:tblCaption",
                                "w:tblDescription")

    # 固定布局 + 单元格内边距清零：默认左右各 0.19cm 会压缩可用宽度，
    # 让抬头那行联系方式提前折行，白多吃一行高度。
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.insert_element_before(layout, "w:tblCellMar", "w:tblLook",
                                "w:tblCaption", "w:tblDescription")

    cellmar = OxmlElement("w:tblCellMar")
    for edge in ("top", "left", "bottom", "right"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:w"), "0")
        e.set(qn("w:type"), "dxa")
        cellmar.append(e)
    tblPr.insert_element_before(cellmar, "w:tblLook", "w:tblCaption",
                                "w:tblDescription")

    w0 = CONTENT_W * 0.58
    t.columns[0].width = Pt(w0)
    t.columns[1].width = Pt(CONTENT_W - w0)
    t.rows[0].cells[0].width = Pt(w0)
    t.rows[0].cells[1].width = Pt(CONTENT_W - w0)

    c0, c1 = t.rows[0].cells
    p = c0.paragraphs[0]
    para_format(p, line=24)
    r = p.add_run(src.name)
    set_font(r, 21, True, INK, spacing_pt=5)
    p2 = c0.add_paragraph()
    para_format(p2, before=1.6 * MM, line=14)
    add_text(p2, src.target, 9.0)

    c1.vertical_alignment = WD_ALIGN_VERTICAL.BOTTOM
    for i, line in enumerate(src.contact):
        p = c1.paragraphs[0] if i == 0 else c1.add_paragraph()
        para_format(p, line=8.5 * 1.75, align=WD_ALIGN_PARAGRAPH.RIGHT)
        r = p.add_run(line)
        set_font(r, 8.5, False, RGBColor(0x40, 0x53, 0x6B))

    # 表格行高取左右两格的较大值，不是相加
    head_h = max(24 + 1.6 * MM + 14, 8.5 * 1.75 * 2)
    total += head_h

    # 抬头下分隔线：用一段 0 高、带上边框的段落模拟 1.6pt 线
    sep = doc.add_paragraph()
    para_format(sep, before=GAP_HEAD, line=1)
    bottom_border(sep, sz=13, color="1F3A5F", space=0)
    total += GAP_HEAD + 1 + 13 / 8

    # ---------- 各板块 ----------
    for section in src.sections:
        items = section["items"]

        p = doc.add_paragraph()
        para_format(p, before=GAP_SEC, after=GAP_SEC_TITLE, line=14)
        r = p.add_run(section["title"])
        set_font(r, 10, True, NAVY, spacing_pt=3)
        r2 = p.add_run("   " + section["en"])
        set_font(r2, 7.4, False, RGBColor(0x93, 0xA1, 0xB2), spacing_pt=0.8)
        bottom_border(p, sz=5, color="C9D2DC", space=2)
        total += GAP_SEC + 14 + GAP_SEC_TITLE + 5 / 8

        n = len(items)
        for idx, item in enumerate(items):
            kind = item[0]
            last = idx == n - 1
            after = GAP_PROJ if last else 0.0

            if kind == "row":
                left, right = item[1], item[2]
                p = doc.add_paragraph()
                para_format(p, after=after, line=LINE + 0.6)
                p.paragraph_format.tab_stops.add_tab_stop(
                    Pt(CONTENT_W), WD_TAB_ALIGNMENT.RIGHT)
                add_text(p, left, 9.9)
                r = p.add_run("\t" + right)
                set_font(r, 8.5, False, MUTED)
                used = width_pt(re.sub(r"\*\*|<<|>>", "", left), 9.9, True) \
                    + width_pt(right, 8.5) + 6
                total += (LINE + 0.6) * (1 if used <= CONTENT_W else 2) + after
                if used > CONTENT_W:
                    print(f"[警告] 标题行超出可用宽度 {used:.0f}pt：{left[:24]}")

            elif kind == "bul":
                p = doc.add_paragraph()
                para_format(p, after=after, left_ind=BULLET_IND,
                            first_ind=-BULLET_IND, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
                r = p.add_run("▪ ")
                set_font(r, BODY_SIZE, False, NAVY)
                add_text(p, item[1])
                total += LINE * count_lines("▪ " + item[1]) + after

            elif kind == "note":
                p = doc.add_paragraph()
                para_format(p, after=after, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
                add_text(p, item[1], 8.8)
                total += LINE * count_lines(item[1], 8.8) + after

            elif kind == "skill":
                k, v = item[1], item[2]
                p = doc.add_paragraph()
                para_format(p, after=after, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
                p.paragraph_format.tab_stops.add_tab_stop(
                    Pt(SKILL_KEY_W), WD_TAB_ALIGNMENT.LEFT)
                r = p.add_run(k + "\t")
                set_font(r, 9.0, True, NAVY)
                add_text(p, v)
                total += LINE * count_lines(v, avail=CONTENT_W - SKILL_KEY_W) + after

    # ---------- 页脚自我评价（HTML 里是 .foot） ----------
    foot = re.search(r'<div class="foot">(.*?)</div>', open(SRC, encoding="utf-8").read(), re.S)
    if foot:
        text = _clean(re.sub(r"<[^>]+>", lambda m: "**" if m.group(0).startswith(("<b", "</b")) else "", foot.group(1)))
        p = doc.add_paragraph()
        para_format(p, before=GAP_FOOT, line=LINE, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
        top_border(p, sz=5, color="DFE5EC", space=4)
        add_text(p, text, 8.5)
        total += GAP_FOOT + LINE * count_lines(text, 8.5)

    doc.save(OUT)

    print(f"已生成：{OUT}")
    print(f"内容估算高度：{total:.0f}pt = {total / MM:.1f}mm")
    print(f"可用高度：   {AVAIL_H:.0f}pt = {AVAIL_H / MM:.1f}mm")
    slack = AVAIL_H - total
    print(f"余量：       {slack:.0f}pt = {slack / MM:.1f}mm", end="  ")
    if slack < 0:
        print("← 超出，会变成两页！")
        return 1
    print("← 单页" if slack > 6 else "← 单页（偏紧，改动内容后请复跑）")
    return 0


if __name__ == "__main__":
    sys.exit(build())
