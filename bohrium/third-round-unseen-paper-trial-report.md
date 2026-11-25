# 第三轮独立试用报告：三篇未见论文与四类抽取缺陷

日期：2026-11-26
Skill 提交：`f71c70e`（`feat/evidence-pdf-reader`，已推送 `origin/main`）
试验对象：`evidence-pdf-reader`（本项目资源包，安装于 `<workspace>/.dsh/skills/evidence-pdf-reader`）

## 1. 为什么再做一轮

前两轮验证（六篇论文、双后端、69→78 项测试）都由本项目的开发者撰写用例并复盘，容易沿用同一套假设。本轮改用三篇**从未参与开发与调参**的论文，由三个互不通信的子智能体在**不点名技能**的自然语言请求下自行决定是否触发 Skill，再由主 Agent 用独立脚本复核引文。目的是回答两个问题：

1. 换一批论文、换一个执行者，Skill 还能不能被正确触发并产出可核验结果？
2. 真实论文的版式会不会暴露开发用例没有覆盖的抽取缺陷？

## 2. 试验设置

| 子代理 | 论文 | arXiv | 页数 | 阅读请求 |
| --- | --- | --- | --- | --- |
| sam-test | Segment Anything | 2304.02643 | 30 | Section 3 三个组件的输入输出与"可提示分割" |
| llama2-test | Llama 2 | 2307.09288v2 | 77 | Section 3 的结构改动与超参数数值 |
| qlora-test | QLoRA | 2305.14314v1 | 26 | NF4 / Double Quantization / Paged Optimizers 与 Guanaco 数值 |

约束与做法：

- 请求只用自然语言描述阅读目标，**不提技能名**，因此这一轮同时是触发测试；
- 三个子代理各自在 `.work/agent-tests/<name>/` 内工作，互不通信，不改动 Skill 源码；
- 必须产出技能约定的 `result.json` 并通过 `validate-result`；
- 主 Agent 另写 `.work/agent-tests/tools/verify_result.py`，**不 import 技能包**，用 PyMuPDF 取页文本，按 NFKC、软连字符、跨行连字符与空白折叠两级归一化后逐条回查引文与页码。

## 3. 结果

### 3.1 触发与流程

三篇全部触发 Skill，全部走完 `capabilities → inspect-case → probe-pdf → extract → select-context → finalize-result → validate-result`，`validate-result` 均为 `valid=true`、`errors=0`。LLaMA 2 因 77 页且单文档超出单批预算，按 Skill 要求改为分段读取并在结果中报告页覆盖。

### 3.2 引文可核验性

| 论文 | 引文条数 | 独立回查结果 |
| --- | --- | --- |
| Segment Anything | 20 | 20 命中，页码全对 |
| LLaMA 2 | 8 | 8 命中，页码全对 |
| QLoRA | 59 | 59 命中，页码全对 |

两级归一化是必要的：QLoRA 引用 `Paged Optimiz- ers` 这类跨行断词时，只对页文本做断词会误报未命中。

### 3.3 诚实性表现

- **LLaMA 2**：请求预设 "Section 3 讲架构改动"，但该 PDF 的 Section 3 实为 `3 Fine-tuning`；GQA 与上下文长度改动在 2.2 节与附录 A.2.1。子代理据实纠正任务前提，并在 PDF 内没有层数、宽度与 query 头数时**拒绝用外部常识补齐**，结果标为 `partial` 并列出缺失字段。
- **QLoRA**：式 (4) 的文本抽取结果不可读（被拆成多个乱码块），子代理渲染页图核对后把"公式以图像为准"写入 `uncertainty` 与 notes，没有伪装成已解析成功。

这两点是本轮最重要的产品质量信号：Skill 允许结果不完整，但不允许无依据的确定。

## 4. 暴露的四类抽取缺陷与修复

| 缺陷 | 证据 | 修复 |
| --- | --- | --- |
| 公式碎片乱序 | QLoRA 式 (4) 被拆成 11 个块（`p0004-b0012`–`p0004-b0022`），含 `U+0012`/`U+0013` 控制码 | 新增公式碎片判定（控制码、公式编号续行、字母与运算符占比），标为 `formula` 并附 `formula_fragment_unverified`，要求引用前看页图 |
| 碎片被标成标题 | QLoRA p5 `section_path` 被 `2 , WNF4) + XBF16LBF16` 污染；SAM p26 的编号列表句被判标题 | 短语级（≥4 词）标题遇终止标点即拒绝；第二词元为功能词、纯标点或数字/大写混合（`LBF16`）时拒绝；纯数字行与"数值+单位"重复行（`41 GB 41 GB`）不可能是小节标题 |
| 附录标题漏判 | LLaMA 2 附录 A.2–A.7 全部落入 `References` 路径 | 新增 `A.2`、`A.2.1`、`Appendix B` 识别，级别取点号数 + 1 |
| 表格行被压平 | QLoRA Table 6 的 `rows` 已存在，但文本被压成一行，容易把相邻行读成本行 | 表格文本按检测行逐行输出（` | ` 分隔），记录 `table_row_count`/`table_column_count` 与"合并单元格仍需看图"的警告 |

### 4.1 修复前后对比（同一批 PDF、同一批阅读请求）

| 论文 | 误判标题 | 被污染的章节路径 | 保留行边界的表格 | 识别出的附录标题 |
| --- | --- | --- | --- | --- |
| Segment Anything | 14 → 0 | 0 → 0 | 0/24 → 24/24 | — |
| LLaMA 2 | 1 → 0 | 0 → 0 | 0/36 → 36/36 | 0 → 28 |
| QLoRA | 5 → 0 | 3 → 0 | 0/1 → 1/1 | 0 → 2 |

LLaMA 2 的标题总数从 55 升到 68，因为附录小节开始被识别；同时目录页的点线引导行与图表坐标轴标签不再被误计为标题。三篇的 `formula` 计数上升（QLoRA 4 → 23、SAM 0 → 17），这是把原先混在 body/heading 里的公式碎片显式标出，不是新增错误。

### 4.2 回归中发现的预算缺陷

真实 attention 文档的紧凑索引一度达到 12028 tokens，越过 12000 的索引预算：裁剪逻辑先按估算扣减，再写出"省略了多少页/多少区块"的计数器，而这些计数器本身也占 token。改为每步重测、必要时继续丢弃可选段落（渲染页清单、标题候选），实测降到 11944，并新增"特殊区块裁剪后必须落在预算内"的测试。

## 5. 回归结果

- `python -m unittest discover -s tests -q`：**84 项全部通过**（此前 78 项；新增 6 项覆盖标题拒绝、公式碎片、附录级别、表格逐行、索引预算收敛）。
- `validate-package`：**18 个文件、192081 字节、0 错误 0 警告**（上限为 50 文件 / 500 KB 单文件 / 2 MB 总量）。
- 三篇论文用新文档重跑 `finalize-result` + `validate-result`：全部 `valid=true`；独立脚本复核 87 条引文，**0 未命中**。
- Poppler 回退路径复跑 QLoRA：26 页全部处理，无表格候选（Poppler 路径本就不产出表格候选），标题误判同样为 0。

## 6. 仍然存在的限制

1. 公式碎片现在会被显式标注，但**不等于已还原公式**；引用公式必须以页图为准。
2. Poppler 路径的章节路径仍弱于 PyMuPDF（本轮复跑中 QLoRA 第 5 页只到 `Abstract`），这是已记录的已知限制，不作为"已修复"宣称。
3. 表格只保留提取器检测到的行边界；合并单元格、跨行表头与单元格归属仍需人工或看图核对。
4. 标题判定仍是启发式：短脚注、分离的章节编号与标题、无字体元数据的 PDF 仍可能误判，`section_path` 只能当导航用。
5. 本轮三个子代理都使用 PyMuPDF 后端；Poppler 只做了一篇的回归，未做三篇全量对比。

## 7. 复现方式

```bash
# 依赖：PyMuPDF（conda 环境 evidence-pdf-reader），Poppler 可选
python evidence-pdf-reader/scripts/run.py capabilities --output capabilities.json
python evidence-pdf-reader/scripts/run.py extract --plan case-plan.json --probe pdf-probe.json \
  --capabilities capabilities.json --work work --output document.json
python evidence-pdf-reader/scripts/run.py finalize-result --plan case-plan.json --document document.json \
  --answer answer.json --output result.json
python evidence-pdf-reader/scripts/run.py validate-result --plan case-plan.json --document document.json \
  --result result.json --output validation.json
```

三篇论文与全部中间产物在本地 `.work/papers2/` 与 `.work/agent-tests/`、`.work/regress-20261126/`，不随仓库发布；本文记录的是 2026-11-26 的实测数字。
