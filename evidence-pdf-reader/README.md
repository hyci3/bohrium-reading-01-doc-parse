# evidence-pdf-reader

把科研 PDF 变成**可回到物理页与原区块**的证据包，再完成具体阅读任务：问答、翻译、字段提取、事实核验、知识结构化。

三个技术增量（不是"又一个 PDF 转文本"）：

1. **多栏阅读顺序恢复**——按坐标与栏簇判定左栏→右栏，跨栏标题/图注/脚注单独处理，而不是按 PDF 内部绘制顺序串行输出。
2. **区块级证据**——每个区块有 `block_id`、`page_number`、`bbox`、`section_path`，答案里的每条引文都能回到原页原块复核，支持"未报告/无法确认"的诚实结论。
3. **上下文预算与诚实降级**——先建紧凑索引再按任务取证据，默认禁止整体读入 `document.json`；预算耗尽、公式不可读、表格不可靠都显式标注，不伪装成已完成。

## 快速开始

```bash
# 1 能力探测（不因文档声明而假定工具可用）
python scripts/run.py capabilities --output work/capabilities.json
# 2 读取案例与输出契约，先写一份诚实的部分结果
python scripts/run.py inspect-case --case case.json --task task.md --work work \
  --output work/case-plan.json --early-result out/result.json
# 3 探测文字层
python scripts/run.py probe-pdf --plan work/case-plan.json \
  --capabilities work/capabilities.json --output work/pdf-probe.json
# 4 抽取区块与阅读顺序
python scripts/run.py extract --plan work/case-plan.json \
  --capabilities work/capabilities.json --probe work/pdf-probe.json \
  --work work --output work/document.json
# 5 先索引、再按任务取证据
python scripts/run.py select-context --plan work/case-plan.json \
  --document work/document.json --index-output work/context-index.json \
  --budget-state work/context-budget.json --output work/selection-001.json \
  --query "数值与结论"
# 6 写答案后合并并校验
python scripts/run.py finalize-result --plan work/case-plan.json \
  --document work/document.json --answer work/answer.json --output out/result.json
python scripts/run.py validate-result --plan work/case-plan.json \
  --document work/document.json --result out/result.json \
  --output work/result-validation.json
```

结构覆盖清单（十项：标题、作者与单位、摘要、章节层级、图表与题注、公式编号、参考文献、页眉页脚、脚注）：

```bash
python scripts/run.py coverage-report --document work/document.json --output work/coverage.json
```

`finalize-result` 会自动把它写进 `result.json` 的 `coverage_report`，每项标 `present`/`partial`/`uncertain`/`absent` 并给出缺失项建议。

## 命令与退出码

| 命令 | 作用 |
| --- | --- |
| `capabilities` | 探测 PyMuPDF、Poppler、OCR、图像渲染的真实可用性 |
| `inspect-case` | 解析 `case.json`/`task.md` 与动态输出契约，写早期结果 |
| `probe-pdf` | 逐页判定文字层、扫描、加密、损坏 |
| `extract` | 生成 `document.json`：区块、坐标、阅读顺序、章节路径、警告 |
| `evaluate-order` | 用人工金标准评估阅读顺序（双栏回归用） |
| `select-context` | 建紧凑索引并按查询/页码/章节选证据，维护预算状态 |
| `coverage-report` | 十项结构覆盖清单 |
| `finalize-result` | 合并答案与文档元数据，落 `output_contract` 全字段 |
| `validate-result` | 校验结果契约、来源块匹配、页面覆盖 |
| `validate-package` | 校验资源包（文件数、大小、frontmatter、符号链接） |

退出码：`2` 通用错误、`3` 无可用提取器、`4` 契约/输入问题、`5` PDF 无文字层、`6` 无成功正文页、`7` 金标准无效、`8` 合并被拒（旧结果保留）、`9` 结果校验失败、`10` 资源包不合法、`11` 预算耗尽。

## 输出契约

`result.json` 含 `case_id`、`status`、`fields`（`output_contract` 全部字段）、`sources`（每条含 `block_id`、`page_number`、`quote`）、`uncertainty`、`errors`、`coverage`、`coverage_report`、`artifacts`。

`status` 的语义：`success` 只表示任务按契约完成且引文可回原块；`partial` 表示有明确缺口（缺证据、公式不可读、预算不足），缺口必须同时出现在 `uncertainty` 里。

## 边界

- 正式运行环境不保证有 PyMuPDF 或 OCR；脚本不自动 `pip install`，也不使用机器专用环境命令。
- 扫描页只在 OCR 可执行确认后接管；没有 OCR 就标 `review_ocr_required`，不猜写文本。
- 表格来自提取器，合并单元格、跨行表头仍需页图核对。
- 公式可能缺少字形或上下标顺序；带 `formula_layout_unverified`/`symbol_font_glyphs_missing` 的公式不要直接引用。
- `validate-result` 通过 ≠ 科学结论正确，也不代表 Host 已发现 Skill。

## 复核

实测基线（9 篇真实论文、250 页、13 次后端运行）、修复前后对比、独立复核脚本与已知失败模式见 [references/benchmark.md](references/benchmark.md)。运行契约见 [references/runtime-contract.md](references/runtime-contract.md)，布局规则见 [references/layout-policy.md](references/layout-policy.md)，上下文预算见 [references/context-policy.md](references/context-policy.md)，全参数示例见 [references/usage.md](references/usage.md)。
