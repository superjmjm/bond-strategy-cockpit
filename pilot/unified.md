# 研报观点隔离入口

在本次基线提交上创建独立 Python 环境，安装 `python -m pip install -r pilot/requirements-unified.txt`。清单可包含一篇或多篇 PDF，保存为 UTF-8 JSON 数组。每项包含 `pdf`（相对清单的路径或绝对路径）、`source`（原件 Drive ID）、`broker`（券商）及 `day`（报告发布日期，YYYY-MM-DD）。

```sh
python pilot/unified.py manifest.json --cache evidence.sqlite --output first-read.json
```

新 PDF 的首次抽取通过原有模型客户端完成，需要配置 OPENAI_API_KEY。已有证据按 PDF 指纹和抽取器版本复用。可用 `--offline` 禁止模型调用，首次在空环境重放时可用 `--seed` 指定包含 extractor_version 的经核验私有原始回执。直接给出基于清单的快照；不需要逐轮输入中间快照。

## 盲测、修订及回归

首次盲测记录提交 SHA、输入 PDF 指纹、输出中的 pipeline_id 和各模型请求编号，保存首次输出。现有结果路径拒绝覆盖。抽取、补读、归类或评分口径变更须在后续提交记录，并保留原参考和修订缘由。改动后将有价值的失败样本加入回归集；同一篇的二次成绩标记为修订复测。泛化能力用新的未见样本检验。

运行只写本地原始证据缓存和隔离结果。投票范围为当前输入清单，重复输入沿用同篇去重规则。
