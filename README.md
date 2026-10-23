# Raven · 游戏与二次元社区内容风控引擎

[![CI](https://github.com/OWNER/raven/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/raven/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Dependencies: numpy + Pillow](https://img.shields.io/badge/deps-numpy%20%2B%20Pillow-informational.svg)](requirements.txt)

> 一个从零到能跑、能测、能被质疑的黑产内容识别系统。纯 CPU、无外部 API、无网络依赖，
> `python run_all.py` 一条命令复现全部数字。

**它解决的问题**：社区里"外挂 / 代练 / 账号交易 / 私域引流"四类黑产内容，攻击者会把它写成
`外·挂`、`外褂`、`Ｏ-Ｕ-Ｔ`、`ｗｘ`、`外​挂`（中间夹零宽字符），关键词规则形同虚设。
这个项目做的是：**先把语义等价的写法折叠成规范形，再用字符级模型判决，最后按置信度分级处置**，
并且用四套测试集把这套东西的真实水位量出来。

```bash
git clone <本仓库地址> && cd raven
pip install -r requirements.txt
python run_all.py          # 约 60~90 秒，产出全部报告与演示页面
```


---

## 30 秒看结论

| 测试集 | F1 | 说明 |
|---|---|---|
| 干净集（未见模板） | **0.943** | 训练时从未出现过的表达方式 |
| 对抗集（常规变形） | **0.930** | 零宽 / 谐音 / 繁体 / emoji / 全角 / 拆字混合 |
| 逐字符分隔集 | **0.951** | "出·原·神·外·挂" 这种最土的绕过 |
| 未登录谐音集 | **0.946** | 归一化词表里**没有**的变体 |

处置层面：**自动拦截档 151 条，精确率 1.000，覆盖 83.9% 的违规内容**，其余 6.1% 走人工复核。

三个我认为比数字更值钱的发现：

1. **逐字符分隔能把关键词规则打到 F1 = 0.000**（一条都检不出），归一化后回到 0.889；
   但字符 n-gram 模型天生免疫这类攻击。→ 归一化的价值取决于判决层依赖什么。
2. **规则层与字符模型层的失败样本几乎不重合**，所以融合后没有短板（四套测试集无一低于 0.93）。
3. **模型排序对了不等于概率可用**：第一批模型 AUC 0.99，但输出全挤在 0.45~0.65，
   分档阈值形同虚设。只看 AUC 的评测永远发现不了这个问题。

---

## 快速开始

```bash
# 全链路（造数 → 训练 → 评测 → 报告 → demo → 对拍），约 60~90 秒
python run_all.py

# 指标回归闸门：31 项质量指标是否与基线一致（CI 里也跑这个）
python tools/check_metrics.py

# 只跑 demo 冒烟测试（验证页面脚本不报错）
node tools/dom_smoke_test.js

# 单条判定
python -m raven.cli "出原神外挂，自瞄透视，稳定不封，加微信abc123"

# 看一组内置示例（含对抗样例与"举报语境"陷阱）
python -m raven.cli --demo

# 图片篡改粗筛（终端里直接画热力图）
python -m raven.cli --image artifacts/dataset/images/test_000_splice.jpg
```

只依赖 `numpy` 和 `Pillow`：

```bash
pip install -r requirements.txt
```

---

## 它长什么样

- `report.html` —— 评测报告：消融矩阵、工作点、分档表、错误样例、特征消融
- `demo/index.html` —— 可交互演示：**权重内嵌在页面里**，改文本立刻重算，
  归一化每一步的输入输出都可视化。前端与后端的判定一致性由 `tools/parity_test.py` 用 225 条样本对拍
  （当前最大概率差 5e-5）

---

## 目录

```
raven/
  normalize.py        对抗性归一化层（10 步流水线，带可追溯 trace）
  text_model.py       字符 n-gram 哈希特征 + 手写 SGD 逻辑回归 + 规则引擎
  image_forensics.py  ELA / 噪声残差 / JPEG ghost 三类痕迹 + 块级定位图
  lexicon.py          谐音表、拼音缩写表、繁简表、关键词权重、处置阈值
  pipeline.py         引擎层（训练 / 打分 / 分级处置 / 权重打包）
  cli.py              命令行
  report.py           评测报告生成
data/
  gen_dataset.py      文本数据集（模板隔离 + 可控变形算子）
  gen_images.py       图像数据集（程序化海报 + copy-move / splice + 硬负样本）
eval/
  run_eval.py         消融阶梯 + 四套测试集 + 图像 + 账号级聚合
tools/
  build_demo.py       生成 demo/index.html
  parity_test.py      前后端一致性对拍
  parity_node.js      Node 侧对拍脚本
  dom_smoke_test.js   demo 冒烟测试（DOM 桩跑一遍页面脚本）
  check_metrics.py    指标回归闸门：31 项质量指标与基线比对，漂移即失败
demo/
  raven-lite.js       浏览器端推理内核（与 Python 逐位一致）
docs/
  ARCHITECTURE.md     架构与设计取舍
  EVAL.md             评测方法论与逐条解读
  ADR.md              关键决策记录（含被推翻的方案）
  AI_COLLAB.md        与 AI 协作的过程、提示词与验证纪律
  RESUME.md           简历条目 + 面试问答
  BILI_SCRIPT.md      视频选题脚本（逐字符分隔打穿规则这件事怎么讲）
resume/
  README.md           这套简历流水线怎么用（含必需的 HTML 结构骨架）
  build_docx.py       解析 HTML 生成 docx（毫米级尺寸 + 字宽测量保证单页）
  verify.py           产物体检：单页 / A4 / PDF 与 DOCX 内容一致 / 孤行检测
  ↳ resume.html 与 pdf/docx/png 未入库（含个人联系方式，已 gitignore）
```

---

## 简历是怎么生成的

> 简历源文件含真实联系方式，未包含在仓库中。两个脚本是通用的，指向任意同结构
> HTML 即可复用，所需结构见 [`resume/README.md`](resume/README.md)。

**一份内容，两种表达**。`resume.html` 是唯一内容源，PDF 与 DOCX 都从它出发：

- **PDF** 走 Chrome headless 打印。CSS 用 mm 定义版面，行距 1.62、正文 9.6pt。
- **DOCX** 走 `build_docx.py`：解析 HTML 的 `.sec / .row / .proj / ul.bul / .skill`
  结构，再用 python-docx 重建。**不用** HTML→DOCX 转换器，因为它会把
  "项目名 …… 日期"这种左右两端对齐的 flex 布局拍平成一行文本，产出的 docx
  连 `sectPr`（页面设置）都没有。简历里"一行两头对齐"本质是制表位问题，
  用 Word 的 tab stop + 段落边框表达才准。

这一步是被坑出来的：前两版内容分别手写在 HTML 和 Python 里，改了一边忘了另一边
就会静默脱节——PDF 上是新数字、Word 上还是旧的，两个文件都不会报错。现在解析
HTML 之后，Word 想脱节也没有机会。

`build_docx.py` 保存前会用**微软雅黑的真实字宽**逐字累加做折行估算，把总高度和
可用高度比一遍，超了就报错退出，不静默产出两页。`verify.py` 再把关三道：PDF 页数
与纸张、PDF/DOCX 逐段文字比对（少一句话就失败退出）、段落末行过短的孤行检测。

```bash
# 1) 改内容 → 打印 PDF
chrome --headless=new --print-to-pdf=resume/resume.pdf resume/resume.html
# 2) 由同一份 HTML 生成 docx（输出名见 build_docx.py 顶部 OUT 常量）
python resume/build_docx.py
# 3) 两份一起体检（不一致就非零退出）
python resume/verify.py
```

---

## 这些数字凭什么可信

数字来自 `artifacts/results/metrics.json`，不是手抄的。为了让"我说过的数字"
不等于"我某次跑出来的数字"，加了一道回归闸门：

```bash
python tools/check_metrics.py
```

它把 31 项**质量类**指标（四套测试集 F1 / 精确率 / 召回、分档表、账号级聚合、
图像 AUC 与定位命中）钉成基线，`run_all.py` 之后逐一比对，漂移超过 1e-3 就非零退出。
CI 里跑它——README 和报告里的每个数字都被这道闸门看着。

**耗时类字段刻意不校验**。在面试包里重跑过一次 `run_all.py`，对比 76 个指标：

| 类别 | 项数 | 结果 |
|---|---|---|
| 质量指标（精确率 / 召回 / F1 / AUC / 混淆矩阵 / 分档 / 消融） | 71 | 逐位一致 |
| 耗时指标（单条推理 / p95 / 训练 / 图像特征提取） | 5 | 全部波动 |

结论：**质量指标可复现，耗时不可**（依赖机器负载）。所以对外报延时说的是
"0.4~0.5 ms 取决于机器"，而不是一个精确到小数点后三位的数。

另外做过一次**干净克隆验证**：把仓库 `git clone` 到一个空目录，不装任何额外东西，
直接 `python run_all.py` → 55.9 秒跑完七步，`check_metrics.py` 31 项全部一致。
也就是说上面这些数字不是"在我这台机器上凑出来的"。

---

## 边界（先说清楚，免得被问到）

- 数据是**合成**的：模板 + 槽位 + 可控扰动。真实社区分布更脏，同口径指标一定会掉。
- 变形手法是我自己造的 7 类 + 1 类未登录谐音，真实黑产花样更多。
- 训练样本 660 条，F1 置信区间约 ±0.03，③④⑤ 之间零点几的差距不该当结论。
- 图像侧只在程序化海报上验证，说明"链路通了"，不说明"能上生产"。
- 上下文理解是原理性硬伤：误报集中在"举报语境"（提到黑产词但在举报/避雷）。

下一步优先级见 `report.html` 第九节：**LLM 复核灰区 > 真实数据回流 > 图像接深度取证 > 在线 A/B**。

---

## 许可

MIT，见 [LICENSE](LICENSE)。代码与合成数据集可自由取用。
如果拿去做真实业务，请先换真实数据重测一遍——见上面「边界」一节。

