# 贡献指南

这个项目的核心资产不是代码，是**数字**。README、`report.html`、`docs/EVAL.md`
里每一个 F1、每一条混淆矩阵，都必须能由 `python run_all.py` 重新算出来。
所以这里的规矩只有一条主线的变体：**改代码可以，改完要说清楚数字为什么变。**

## 改动前先跑一遍基线

```bash
pip install -r requirements.txt
python run_all.py            # 约 60~90 秒
python tools/check_metrics.py # 31 项质量指标，应当全绿
```

如果基线本来就红了，说明当前环境有问题（Python 版本、numpy 版本），
先解决这个，再谈改动。

## 提交前必须做

```bash
python run_all.py && python tools/check_metrics.py && node tools/dom_smoke_test.js
```

三件事都得过：

1. **全链路能跑通**——七步没有中途失败。
2. **31 项质量指标在容差内**——容差默认 1e-3。
3. **demo 页面脚本不报错**——`dom_smoke_test.js` 用 DOM 桩跑一遍页面脚本。

指标变了不等于错。但变了就必须三处一起改，不允许只改一处：

| 改哪里 | 谁依赖它 |
|---|---|
| `tools/check_metrics.py` 的基线常量 | 回归闸门 |
| `README.md` 的结论表 | 对外门面 |
| `docs/EVAL.md` 的逐条解读 | 方法论 |
| `report.html` | 由 `run_all.py` 自动重建，不用手改 |

确认是有意的改动，用 `python tools/check_metrics.py --update` 记下新基线，
**不要**为了让 CI 变绿去放宽 `--tol`。

## 代码风格

- 纯标准库 + numpy + Pillow。**不引入新的重依赖**，这是项目的取舍：
  任何人 `pip install` 两条就能跑。
- 训练与推理必须确定性：固定随机种子，不依赖字典遍历顺序。
- 归一化层（`raven/normalize.py`）的每一步都要在 trace 里可见——
  这是"能解释为什么判违规"的前提，不要为了快而省略。

## 数据集

`data/gen_dataset.py` 和 `data/gen_images.py` 是**造数脚本**，数据集由它们生成，
不要手工编辑 `artifacts/dataset/*.jsonl`——下次跑 `run_all.py` 会被覆盖。

生成逻辑是模板隔离的：训练集和测试集用**不同的模板**，测试集测的是
"没见过的表达方式"，不是"背过的句子"。加新模板时注意别打破这个隔离。

## 报告问题

请附上：

- `python --version` 与 `pip show numpy pillow` 的输出
- 完整报错栈
- 如果是指标问题，贴 `python tools/check_metrics.py` 的输出

## 许可

提交即表示同意以 [MIT](LICENSE) 许可发布你的贡献。
