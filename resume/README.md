# resume/ —— 把简历当工程产物来做

这个目录解决一个具体问题：**同一份简历要同时出 PDF 和 Word，两边数字不能对不上。**

手写两版会静默脱节——改了 HTML 忘了改 Python，PDF 上是新数据、Word 上还是旧的，
两个文件都不会报错，投出去才知道。所以这里只有**一份内容源**，两个产出都从它来。

> ⚠️ **本目录的 `resume.html` 与产出的 pdf/docx/png 未包含在仓库中**：
> 它们含真实姓名、手机号与邮箱。`.gitignore` 里已排除。
> 下面的脚本是通用的，指向任意同结构的 HTML 即可复用。

## 两个产出怎么来的

| 产物 | 路径 | 说明 |
|---|---|---|
| PDF | Chrome headless 打印 | CSS 用 mm 定义版面，`@page { size: A4; margin: 0 }` |
| DOCX | `build_docx.py` | 解析 HTML 后**用 python-docx 重建**，不是格式转换 |

**为什么不用 HTML→DOCX 转换器**：它会把"项目名 …… 日期"这种左右两端对齐的
flex 布局拍平成一行文本，产出的 docx 连 `sectPr`（页面设置）都没有。简历里
"一行两头对齐"本质是**制表位**问题，得用 Word 的 tab stop + 段落边框表达才准。

## 内容源需要的 HTML 结构

`build_docx.py` 按 class 抓取内容，最小骨架如下：

```html
<div class="page">
  <div class="head"><div class="name">姓名</div><div class="contact">…</div></div>

  <div class="sec">
    <div class="sec-t">教育背景</div>
    <div class="row"><span class="l">学校 · 专业</span><span class="r">2024.09 – 2028.06</span></div>
    <ul class="bul"><li>一条要点</li></ul>
  </div>

  <div class="proj">
    <div class="ph"><span class="pn">项目名</span><span class="pd">2026.10</span></div>
    <div class="role">角色 | 技术栈</div>
    <ul class="bul"><li>一条要点，<b>加粗</b>也支持</li></ul>
  </div>
</div>
```

## 三步走

```bash
# 1) 改内容 → 打印 PDF（Chrome / Edge 都在）
chrome --headless=new --print-to-pdf=resume.pdf resume.html

# 2) 由同一份 HTML 生成 docx（输出名见脚本顶部 OUT 常量）
python resume/build_docx.py

# 3) 两份一起体检，不一致就非零退出
python resume/verify.py
```

## `verify.py` 查什么

1. **PDF 页数**必须等于 1，纸张必须 210×297mm（用 pymupdf 读，不信"看起来是一页"）
2. **PDF 与 DOCX 逐段文字比对**——少一句话就失败退出
3. **孤行检测**：段落末行过短（只剩一两个字）会被标出来

这一步是被坑出来的：`build_docx.py` 里加的"字宽测量估高"只是估算，
而 Word 的真实折行由它自己算。估算能过、Word 排成两页，只有真的打开数一遍才知道。

## 已知的两个坑

- **字号有半磅粒度**：docx 的 `w:sz` 单位是半磅，9.6pt 会被截成 9.5。正文统一用半磅整数值。
- **`w:pBdr` 必须排在 `w:spacing` 前面**：Word 对 `pPr` 子元素顺序有严格 schema，
  顺序错了不报错，静默丢掉边框。调样式时如果边框"设了但没出现"，先查这里。
