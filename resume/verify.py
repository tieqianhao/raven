# -*- coding: utf-8 -*-
"""简历产物体检：PDF 与 DOCX 必须“同页、同内容”，且不能有孤字行。

为什么需要它：PDF 由 Chrome 渲染、DOCX 由 python-docx 重建，两条链路各有各的
折行规则。肉眼看单页不代表内容一致——少一句话、日期错位、数字不同步，都不会
让哪一边报错。这个脚本把这种“静默不一致”变成失败退出码。

跑法：
    python resume/verify.py
"""
import os
import re
import sys

import docx
import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
PDF = os.path.join(HERE, "余星乐-个人简历.pdf")
DOCX = os.path.join(HERE, "余星乐-个人简历.docx")

MM = 72 / 25.4
fails, warns = [], []


def norm(s):
    """只保留文字与数字，用来做内容比对（排版差异不该影响结果）。"""
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[▪•·\s]+", "", s)
    return s


def check_pdf():
    d = pymupdf.open(PDF)
    if d.page_count != 1:
        fails.append(f"PDF 有 {d.page_count} 页（要求单页）")
    p = d[0]
    w, h = p.rect.width / MM, p.rect.height / MM
    if abs(w - 210) > 0.6 or abs(h - 297) > 0.6:
        fails.append(f"PDF 纸张不是 A4：{w:.1f}×{h:.1f}mm")

    blocks = p.get_text("blocks")
    bottom = max(b[3] for b in blocks)
    top = min(b[1] for b in blocks)
    slack_mm = (p.rect.height - bottom) / MM
    print(f"PDF   页数 1 · {w:.1f}×{h:.1f}mm · 首行距顶 {top / MM:.1f}mm · 底部余 {slack_mm:.1f}mm")
    if slack_mm < 3:
        fails.append(f"PDF 底部只剩 {slack_mm:.1f}mm，一改内容就会溢出到第二页")
    if slack_mm > 26:
        warns.append(f"PDF 底部空了 {slack_mm:.1f}mm，版面偏松")

    # 段落末行过短：PDF 的一个 block 通常就是一个段落，
    # 多行 block 的最后一行如果只排到 45% 以内，视觉上就是"孤行"，要重排文字。
    body_left, body_right = 14 * MM, p.rect.width - 14 * MM
    avail = body_right - body_left
    thin = []
    for b in p.get_text("dict")["blocks"]:
        lines = [(ln["bbox"], "".join(s["text"] for s in ln["spans"]).strip())
                 for ln in b.get("lines", [])]
        lines = [(bb, t) for bb, t in lines if t]
        if len(lines) < 2:
            continue
        # 只看正文段落：首行必须基本占满行宽，否则是“标题+日期”这类同一基线的行
        f0 = (lines[0][0][2] - lines[0][0][0]) / avail
        if f0 < 0.85:
            continue
        (x0, _, x1, _), last = lines[-1]
        fill = (x1 - x0) / avail
        if fill < 0.45:
            thin.append(f"{last[:26]!r}（末行仅占 {fill:.0%}）")
    if thin:
        warns.append("段落末行过短（孤行）：" + "；".join(thin[:5]))
    return p.get_text("text")


def check_docx():
    d = docx.Document(DOCX)
    s = d.sections[0]
    w, h = s.page_width.mm, s.page_height.mm
    print(f"DOCX  纸张 {w:.1f}×{h:.1f}mm · 边距 上{s.top_margin.mm:.1f} 下{s.bottom_margin.mm:.1f} "
          f"左{s.left_margin.mm:.1f} 右{s.right_margin.mm:.1f}mm")

    if abs(w - 210) > 0.6 or abs(h - 297) > 0.6:
        fails.append(f"DOCX 纸张不是 A4：{w:.1f}×{h:.1f}mm")
    # 简历页边距一旦落在 Word 默认值，说明 sectPr 没生效
    if abs(s.left_margin.mm - 14) > 0.3:
        fails.append("DOCX 边距没生效（疑似 sectPr 丢失）")

    # 半磅粒度：字号必须是 0.5 的整数倍，否则会被静默截断
    bad = set()
    for p in d.paragraphs:
        for r in p.runs:
            if r.font.size and abs(r.font.size.pt * 2 - round(r.font.size.pt * 2)) > 1e-6:
                bad.add(round(r.font.size.pt, 2))
    for t in d.tables:
        for row in t.rows:
            for c in row.cells:
                for p in c.paragraphs:
                    for r in p.runs:
                        if r.font.size and abs(r.font.size.pt * 2 - round(r.font.size.pt * 2)) > 1e-6:
                            bad.add(round(r.font.size.pt, 2))
    if bad:
        fails.append(f"字号不是半磅整数倍（Word 会截断）：{sorted(bad)}")

    texts = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            for c in row.cells:
                texts += [p.text for p in c.paragraphs if p.text.strip()]
    return texts


def main():
    pdf_text = norm(check_pdf())
    docx_texts = check_docx()

    missing = []
    for t in docx_texts:
        key = norm(t)
        if key and key not in pdf_text:
            missing.append(t)
    if missing:
        fails.append(f"{len(missing)} 段 DOCX 文字在 PDF 里找不到（两份内容不同步）：\n    "
                     + "\n    ".join(m[:70] for m in missing[:6]))

    print(f"内容比对：DOCX {len(docx_texts)} 段 / PDF {len(pdf_text)} 字 · 缺失 {len(missing)} 段")

    for w in warns:
        print(f"[提示] {w}")
    if fails:
        print("\n[失败]")
        for f in fails:
            print("  - " + f)
        return 1
    print("\n[通过] 单页、A4、内容一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
