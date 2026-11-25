# Evidence PDF Reader 真实论文与多后端测试报告

## 结论与范围

本工作在当前工作副本上完成了六篇真实论文的针对性验证。PyMuPDF 处理六篇共 117 页；Poppler 对其中的 BERT、DETR、LoRA 再处理 68 页。九次后端运行合计 185 个运行页，失败页和跳过页均为 0；这里的 68 页是相同论文的后端重复处理，不计作新的独立论文页数。

主路径形成五份 `success` 和一份 ResNet `partial`，Poppler 形成三份 `success`；九份最终结果均通过本地契约与来源校验。全量 `unittest` 共运行 **69 项，0 项跳过，全部通过**；包校验为 **18 个文件、0 个错误**。`valid=true` 只表示本地结果契约、来源块和覆盖检查通过，不表示全文语义、数值单元格、Host 发现或官方评测准确率已经验证。

报告中的结果、页图与校验链接指向本地工作副本的 `.work/` 目录，该目录不随仓库发布；链接改为相对路径仅为保持文字可回溯，读者无法在本仓库中直接打开它们。本报告保留 2026-11-25 那次运行的原始记录；2026-11-26 缺陷修复后的回归为 78 项测试全部通过、包校验 18 个文件 / 184939 字节 / 0 错误 0 警告（见 `docs/development-plan.md` 第 11.3 节）。

总代理直接查看了六篇论文的十个唯一目标页图，形成 15 条后端观察记录；共享论文的主路径观察复用同一 PDF 的 Poppler 页图，来源引文和块 ID 仍分别按各后端文档校验。页图渲染、文件存在和哈希一致均未被视为视觉复核本身。

## 最终结果与预算

下表的 token 数均为启发式估算，不是 Host tokenizer 实测。旧入选块数分别来自主路径 v4 和 Poppler v6，仅用于显示修复前后进入目标范围的完整块数量变化，不是准确率指标。

| 后端 | 论文 | 处理页 | 状态 | 来源数 | 当前入选块（批次；旧值） | 索引＋文本估算 | 剩余 | 结果与校验 |
|---|---|---:|---|---:|---:|---:|---:|---|
| pymupdf | BERT | 16 | success | 4 | 43（1 批；旧 43） | 11,948＋7,319＝19,267 | 0 | [结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/bert/output/result.json)／[有效](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/bert/final-result-validation.json) |
| pymupdf | ViT | 22 | success | 4 | 28（1 批；旧 18） | 11,872＋5,767＝17,639 | 0 | [结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/vit/output/result.json)／[有效](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/vit/final-result-validation.json) |
| pymupdf | DETR | 26 | success | 4 | 13（1 批；旧 13） | 11,957＋2,761＝14,718 | 0 | [结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/detr/output/result.json)／[有效](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/detr/final-result-validation.json) |
| pymupdf | LoRA | 26 | success | 5 | 37（1 批；旧 37） | 11,831＋7,047＝18,878 | 0 | [结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/lora/output/result.json)／[有效](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/lora/final-result-validation.json) |
| pymupdf | Attention Is All You Need | 15 | success | 4 | 36（1 批；旧 36） | 11,358＋5,721＝17,079 | 0 | [结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/attention/output/result.json)／[有效](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/attention/final-result-validation.json) |
| pymupdf | ResNet | 12 | partial | 4 | 255（2 批；旧 247） | 11,967＋28,282＝40,249 | 0 | [结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/resnet/output/result.json)／[有效](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/resnet/final-result-validation.json) |
| poppler | BERT | 16 | success | 12 | 82（1 批；旧 81） | 3,795＋12,841＝16,636 | 0 | [结果](.work/main-agent-suite-20261124/poppler-runs-repair-v7/bert/output/result.json)／[有效](.work/main-agent-suite-20261124/poppler-runs-repair-v7/bert/result-validation.json) |
| poppler | DETR | 26 | success | 8 | 48（1 批；旧 48） | 4,848＋7,849＝12,697 | 0 | [结果](.work/main-agent-suite-20261124/poppler-runs-repair-v7/detr/output/result.json)／[有效](.work/main-agent-suite-20261124/poppler-runs-repair-v7/detr/result-validation.json) |
| poppler | LoRA | 26 | success | 8 | 48（1 批；旧 45） | 9,060＋12,833＝21,893 | 0 | [结果](.work/main-agent-suite-20261124/poppler-runs-repair-v7/lora/output/result.json)／[有效](.work/main-agent-suite-20261124/poppler-runs-repair-v7/lora/result-validation.json) |

所有目标续读范围均以 `status=complete` 且 `remaining_blocks=0` 结束；末尾非空 `next_cursor` 未被误判为未完成。每批新证据估算不超过 24,000，紧凑索引不超过 12,000。图像成本未实测，自动生成但未被总代理读取的风险页图不被计作已复核。

## 六篇论文的图注与表头证据

以下图注引文直接取自最终 PyMuPDF 结果，文本块保留原始物理换行、断词和标点。表头结论保留中文答案的定位与限制；完整原始表头块、bbox、raw_id、警告和几何修正事件见结构化 JSON。

### BERT

状态为 `success`。图注位于第 5 页、块 `p0005-b0014`；表题位于第 6 页、块 `p0006-b0002`；原始表头块为 `p0006-b0001`。[最终结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/bert/output/result.json)；页图：[第 5 页图](.work/main-agent-suite-20261124/manual-page-review-v7/bert/page-0005.png)、[第 6 页图](.work/main-agent-suite-20261124/manual-page-review-v7/bert/page-0006.png)。

完整图注原文：

```text
Figure 2: BERT input representation. The input embeddings are the sum of the token embeddings, the segmenta-
tion embeddings and the position embeddings.
```

表头与候选结构结论：

第6页 Table 1（p0006-b0002）的表头包含 System 及 MNLI-(m/mm)、QQP、QNLI、SST-2、CoLA、STS-B、MRPC、RTE、Average，并保留下一行的 392k、363k、108k、67k、8.5k、5.7k、3.5k、2.5k 等训练样本量。原始多行内容位于 p0006-b0001（第6页），不是已经验证的二维单元格。
另在第5页图2内部检测到局部网格候选 p0005-b0002，其内容为 he | likes | play，为1行、最多3列。该自动候选既不是第6页 Table 1 的结构，也不能用来绑定图2或表1的数值。

### ViT

状态为 `success`。图注位于第 6 页、块 `p0006-b0023`；表题位于第 6 页、块 `p0006-b0005`；原始表头块为 `p0006-b0002`。[最终结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/vit/output/result.json)；页图：[第 6 页图](.work/main-agent-suite-20261124/manual-page-review-primary-v5b/vit/page-0006.png)。

完整图注原文：

```text
Figure 2: Breakdown of VTAB performance in Natural, Specialized, and Structured task groups.
```

表头与候选结构结论：

第6页 Table 2（p0006-b0005）的表头模型区域保留了 Ours-JFT、Ours-JFT、Ours-I21k、BiT-L、Noisy Student 及下一行的 (ViT-H/14)、(ViT-L/16)、(ViT-L/16)、(ResNet152x4)、(EfﬁcientNet-L2)。这些名称和多行别名保持在 p0006-b0002（第6页） 的原始块内；不把自动 table 候选当作其可靠的列绑定关系。
该页图候选 p0006-b0010 被输出为10行、最多26列，并混入图形数值、空单元格和切碎的正文。该尺寸仅是候选输出的描述，不能作为原表尺寸。

### DETR

状态为 `success`。图注位于第 13 页、块 `p0013-b0004`；表题位于第 13 页、块 `p0013-b0005`；原始表头块为 `p0013-b0007`。[最终结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/detr/output/result.json)；页图：[第 13 页图](.work/main-agent-suite-20261124/manual-page-review-v7/detr/page-0013.png)。

完整图注原文：

```text
Fig. 6: Visualizing decoder attention for every predicted object (images from COCO
val set). Predictions are made with DETR-DC5 model. Attention scores are coded with
diﬀerent colors for diﬀerent objects. Decoder typically attends to object extremities,
such as legs and heads. Best viewed in color.
```

表头与候选结构结论：

同页 Table 3（p0013-b0005）的真实多行表头块为 p0013-b0007（第13页）。按源块保留顺序，标签为 spatial pos. enc.、output pos. enc.、encoder、decoder、decoder、AP、∆、AP50、∆，保留原始 ∆ 与 AP50 符号。
自动候选 p0013-b0006 把该表头聚合成1行、最多2列，不能据此证明空间位置编码、输出位置编码与 encoder/decoder 的母列和子列对应已恢复。这里只报告原始表头标签，不推断数值单元格归属。

### LoRA

状态为 `success`。图注位于第 1 页、块 `p0001-b0018`；表题位于第 6 页、块 `p0006-b0012`；原始表头块为 `p0006-b0002`、`p0006-b0003`。[最终结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/lora/output/result.json)；页图：[第 1 页图](.work/main-agent-suite-20261124/manual-page-review-v7/lora/page-0001.png)、[第 6 页图](.work/main-agent-suite-20261124/manual-page-review-v7/lora/page-0006.png)。

完整图注原文：

```text
Figure 1: Our reparametriza-
tion. We only train A and B.
```

表头与候选结构结论：

第6页 Table 2（p0006-b0012）的两行表头由相邻的物理块 p0006-b0002（第6页）、p0006-b0003（第6页） 共同保存。第一块保留 Model & Method 和 # Trainable；下一块保留 Parameters，以及 MNLI、SST-2、MRPC、CoLA、QNLI、QQP、RTE、STS-B、Avg.。页图中 # Trainable 与下一行 Parameters 对齐，这是跨两个源块保留的表头，不要求每个源块内部都含换行。
自动候选 p0006-b0001 虽输出一行、最多三列，并为部分表头保留换行，但该候选仍不足以证明多行表头边缘或跨列关系已完整恢复。

### Attention Is All You Need

状态为 `success`。图注位于第 4 页、块 `p0004-b0005`；表题位于第 9 页、块 `p0009-b0001`；原始表头块为 `p0009-b0007`。[最终结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/attention/output/result.json)；页图：[第 4 页图](.work/main-agent-suite-20261124/manual-page-review-primary-v5b/attention/page-0004.png)、[第 9 页图](.work/main-agent-suite-20261124/manual-page-review-primary-v5b/attention/page-0009.png)。

完整图注原文：

```text
Figure 2: (left) Scaled Dot-Product Attention. (right) Multi-Head Attention consists of several
attention layers running in parallel.
```

表头与候选结构结论：

第9页 Table 3（p0009-b0001）的多行参数表头原始块为 p0009-b0007（第9页）。源块依次保留 N、dmodel、dff、h、dk、dv、Pdrop、ϵls、train、PPL、BLEU、params、steps、(dev)、(dev)、×106。页面可辨 train/steps、PPL/(dev)、BLEU/(dev) 及 params/×106 的堆叠排版，其中ϵls和乘法符号按原字符保留；页图中的上标格式不等同于在未验证 rows 中已恢复列语义。
自动候选 p0009-b0002 输出8行、最多3列，明显不能承载该多行参数表头的完整二维对应，因而没有用其绑定任何模型配置数值。

### ResNet

状态为 `partial`。图注位于第 4 页、块 `p0004-b0116`；表题位于第 5 页、块 `p0005-b0099`；原始表头块为 `p0005-b0002`。[最终结果](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/resnet/output/result.json)；页图：[第 4 页图](.work/main-agent-suite-20261124/manual-page-review-primary-v5b/resnet/page-0004.png)、[第 5 页图](.work/main-agent-suite-20261124/manual-page-review-primary-v5b/resnet/page-0005.png)。

完整图注原文：

```text
Figure 3. Example network architectures for ImageNet. Left: the
VGG-19 model [41] (19.6 billion FLOPs) as a reference. Mid-
dle: a plain network with 34 parameter layers (3.6 billion FLOPs).
Right: a residual network with 34 parameter layers (3.6 billion
FLOPs). The dotted shortcuts increase dimensions. Table 1 shows
more details and other variants.
```

表头与候选结构结论：

第5页 Table 1（p0005-b0099）的原始块 p0005-b0002（第5页） 保留 layer name、output size、18-layer、34-layer、50-layer、101-layer、152-layer，以及后续 conv1、112×112、7×7, 64, stride 2。人工页图显示模型列名主要位于一行，后面包含多行卷积配置；不能把这些配置数据直接归为合格的二级表头。
因此本例能够回答图注并保留表格源文本，但未严格满足所选多行表头示例，状态保留为 partial。自动候选 p0005-b0001 只有1行、最多4列，缺少源表头中的152-layer，不能代表原表结构。

## Poppler 后端差异、表头与 XML 控制码

逐字比较只在比较层归一化空白，不做全局 Unicode 兼容性归一化；有序词元／标点比较只展开声明的拉丁连字。两种比较分别记录，字面不同不被改写为相同。

| 论文 | 图注逐字 | 表题逐字 | 有序词元与标点 | 控制码点计数 | 最终结果 |
|---|---|---|---|---|---|
| BERT | 一致 | 不同 | 图／表均通过 | 未检出 | [结果](.work/main-agent-suite-20261124/poppler-runs-repair-v7/bert/output/result.json) |
| DETR | 不同 | 不同 | 图／表均通过 | U+0001×1、U+0012×1、U+0013×1、U+001C×1 | [结果](.work/main-agent-suite-20261124/poppler-runs-repair-v7/detr/output/result.json) |
| LoRA | 一致 | 不同 | 图／表均通过 | U+0001×1、U+0010×1、U+0011×1、U+001C×3 | [结果](.work/main-agent-suite-20261124/poppler-runs-repair-v7/lora/output/result.json) |

Poppler 路径不提供已验证的自动二维表格结构。BERT 的 `Average` 原始块同时保留下方短横线和后续数值，但答案只将 `Average` 列为表头标签，未把这些数值提升为已恢复单元格。DETR 的 `AP50` 保留几何拼接前后文本和原词 bbox；LoRA 的模型名下标、紧随标点及浮动图注拆分同样保留审计事件。控制码点的页码、原始 XML 行列和源 XML SHA-256 见结构化 JSON；这些码点不是 OCR 字形结论。

当前 Poppler 章节导航仍有明确缺陷：BERT 目标题注的 `section_path` 为 `Abstract`，DETR／LoRA 为目标题注空路径。此次答案按物理页与块 ID 定位，未把这些路径作为正确章节层级，也未据此宣称全文导航修复。

## 修复前后物理来源与 ID 影响

九组修复前后对照均满足：同一页 `raw_id` 集合不变，逐 `raw_id` 的 `raw_text`、bbox、自动候选 `rows` 变化数均为 0，物理文本／几何／行候选多重集合一致。与此同时，九组的 raw_id 阅读顺序均发生变化，且存在大量块 ID 重编号；因此不能将物理多重集合一致表述为阅读顺序或章节语义完全等价。

| 运行 | 原文／bbox／rows 变化 | 块 ID 重编号 | 类型迁移（按 raw_id） | raw_id 顺序变化页数 |
|---|---:|---:|---|---:|
| pymupdf:bert | 0／0／0 | 18 | footer→body 5 | 1 |
| pymupdf:vit | 0／0／0 | 638 | footer→body 224；header→body 15 | 8 |
| pymupdf:detr | 0／0／0 | 77 | heading→body 3；heading→header 13；header→body 9；footer→body 11 | 4 |
| pymupdf:lora | 0／0／0 | 77 | footer→body 22；header→body 2 | 3 |
| pymupdf:attention | 0／0／0 | 50 | footer→body 2 | 1 |
| pymupdf:resnet | 0／0／0 | 95 | footer→body 24；header→body 3 | 3 |
| poppler:bert | 0／0／0 | 63 | footer→body 18；heading→body 2 | 8 |
| poppler:detr | 0／0／0 | 124 | footer→body 61；header→body 13；heading→body 1 | 8 |
| poppler:lora | 0／0／0 | 250 | footer→body 78；heading→body 1 | 12 |

ViT 旧结果在新文档上的失败被保留为 `sources[0] quote does not match source block text`。严格物理匹配后来源 ID 变化为 `p0006-b0013`→`p0006-b0023`；`p0006-b0007`→`p0006-b0010`，其余目标来源 ID 不变；Figure 2 的 raw_id 保持为 `r0021`，原文、bbox、原始类型、rows 和图像标志均匹配。迁移后来源及最终结果分别通过校验。[旧结果失败报告](.work/main-agent-suite-20261124/primary-runs-margin-recheck-v5b/vit/previous-result-current-document-validation.json)；[完整 raw_id 影响记录](.work/main-agent-suite-20261124/analysis/final-fix-impact-v5b-v7.json)。

该对照只证明物理来源被保留和当前引用被重新绑定，不证明全篇阅读顺序已经具备人工金标准。此前对照已定位 DETR 第 2、12 页局部顺序变化；最终逐页 raw_id 顺序差异清单见结构化 JSON。

## 同版本重复性

当前代码对同一 BERT PDF 再提取一次，逐页有序块 JSON、页面 layout 和 layout_analysis 均一致，变化块数为 0；第 3、5、15、16 页的四张自动渲染图 SHA-256 均一致。两次产物级 `document_sha256` 分别保存，未将该项检查表述为所有文件字节或产物哈希相同。[重复性记录](.work/main-agent-suite-20261124/analysis/bert-current-repeatability-v5b.json)。

## 全量测试与包校验

- `python -m unittest discover -s tests -v`：**Ran 69 tests in 12.158s；OK**，0 项跳过。首次 `python -m pytest tests -q` 因环境未安装 pytest 而未执行测试，随后使用标准库完整发现同一 `tests/` 目录，没有筛选测试或放宽断言。
- `validate-package`：**18 个文件、182,905 bytes、0 个错误、0 个警告**。[包校验报告](bohrium/package-validation.json)。
- 显式视觉观察及其哈希、九份结果、来源迁移、后端差异、预算和原始 PDF SHA-256 均已纳入 [结构化报告](bohrium/evidence-pdf-reader-real-paper-test-report.json)。

## 输入 PDF 与完整哈希

- BERT：[源 PDF](.work/main-agent-suite-20261124/papers/bert.pdf)；SHA-256 `5692a5514787a8c6727b4ff3b726a3385798bc68e12138d1d4af83947e2acf6e`。
- ViT：[源 PDF](.work/main-agent-suite-20261124/papers/vit.pdf)；SHA-256 `8ce7b83971a14508ca711a27c875c9b6914c4f6767cf3150fb1ca6c07aa056d6`。
- DETR：[源 PDF](.work/main-agent-suite-20261124/papers/detr.pdf)；SHA-256 `d4b846bd44896e1d21527f48eddc422c263c9a4f8a3fa4be249750956882698d`。
- LoRA：[源 PDF](.work/main-agent-suite-20261124/papers/lora.pdf)；SHA-256 `e9a0d3128767db616085dc0f4e6e455e672e89af823e8ed1282793682787395a`。
- Attention Is All You Need：[源 PDF](.work/fixtures/real/attention.pdf)；SHA-256 `bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697`。
- ResNet：[源 PDF](.work/fixtures/real/resnet.pdf)；SHA-256 `1e0651b6810ecba34a3dbc5b5b0209226f889004607c1f203540a48d64e5a93a`。

## 保留的限制与部分结果

1. 一般标题和章节层级仍为启发式；短脚注、分离的章节编号与标题可能误判，Poppler 目标题注的实际异常路径已单独披露。
2. 页边唯一数字候选仍有歧义；同页多个数字块保留 body 并告警，不等于识别为页码或表格单元格。
3. 自动表候选、Poppler 原始块和页图是不同证据层；多行表头标签可核对，不代表合并单元格、二维列绑定或数值归属已恢复。
4. XML 控制码只恢复原码点，不恢复未验证的印刷字形；几何下标拼接与浮动文本拆分也不构成通用 OCR。
5. ResNet 保持 `partial`：Table 1 的真正列标题只有一行，后续 `conv1` 与多行卷积配置是数据，不作为满足多行表头要求的证据；这不表示页面提取失败，也不证明全文不存在其他多行表头。
6. 直接视觉复核仅覆盖十个目标页，而非全部 185 个运行页。测试结论是本地实现回归与目标证据核验，不是官方基准或完整科学正确性证明。
