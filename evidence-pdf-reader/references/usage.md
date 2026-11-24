# 独立调用说明

## 1. 可用路径与依赖

```bash
python <skill-dir>/scripts/run.py --help
python <skill-dir>/scripts/run.py capabilities --output <work>/capabilities.json
```

`<skill-dir>` 是本次加载的 Skill 目录，不是开发仓库或个人路径。脚本本身只使用 Python 标准库；处理 PDF 需实际可用的一条路径：

| 需要 | 可选工具 | 不可用后果 |
|---|---|---|
| 坐标文字提取 | PyMuPDF 的 `pymupdf` 模块，或 Poppler `pdftotext -bbox-layout` | 无法声称完成布局恢复，退出 3 或保存失败结果 |
| 页数与元数据 | 同上或 `pdfinfo` | 保留未知范围与具体错误 |
| 页图 | PyMuPDF 或 `pdftoppm` | 保留文本并明确未进行视觉复核 |
| 扫描 OCR | 实际可执行且适配的 OCR；默认适配器使用 Tesseract 接口 | 不猜写扫描正文，页级部分完成或失败 |

运行环境默认 `python` 是指正式运行选择的解释器。Skill 不会自动装包、创造环境或下载模型。用户授权安装后，应重新生成 `capabilities.json` 并在同一隔离环境运行实际 PDF 往返检查；`importlib.util.find_spec` 或磁盘上的模型文件本身不能证明实际识别成功。

## 2. 九个命令

| 命令 | 主要参数 | 结果 |
|---|---|---|
| `capabilities` | `--resources`、`--output` | 实际工具清单；无提取器返回 3 |
| `inspect-case` | `--case`、`--task`、`--work`、`--output`、`--early-result`、可选重复 `--pdf` | 输入哈希、输出契约、早期部分结果 |
| `probe-pdf` | `--plan`、`--capabilities`、`--output`、可选 `--backend pymupdf|poppler` | 逐页文字层与处理路径；无直接提取工作返回 5 |
| `extract` | `--plan`、`--probe`、`--capabilities`、`--work`、`--output`、可选 `--order layout|naive` | 结构化证据；无成功正文页返回 6 |
| `evaluate-order` | `--document`、`--gold`、`--output` | 与独立金标准的顺序比较；无效匹配返回 7 |
| `select-context` | `--plan`、`--document`、`--index-output`、`--budget-state`、`--output` 与过滤器 | 索引、证据选择、计量、游标与页图；预算耗尽返回 11 |
| `finalize-result` | `--plan`、`--document`、`--answer`、`--output`、可选 `--segments-dir`、`--merge-output` | 实际合同字段与证据合并；不合法拒写返回 8 |
| `validate-result` | `--plan`、`--document`、`--result`、`--output` | 契约、字段、证据、覆盖和状态一致性；失败返回 9 |
| `validate-package` | `--skill-dir`、`--output` | UTF-8、路径、元数据及资源限额；失败返回 10 |

通用运行和数据错误返回 2。命令的实际参数以 `--help` 为准。

## 3. 最小执行序列

以下不是官方案例，也不包含任何论文答案。将尖括号替换为实际参数：

```bash
python <skill-dir>/scripts/run.py capabilities --output <work>/capabilities.json
python <skill-dir>/scripts/run.py inspect-case --case <case.json> --task <task.md> --work <work> --output <work>/case-plan.json --early-result <output>/result.json
python <skill-dir>/scripts/run.py probe-pdf --plan <work>/case-plan.json --capabilities <work>/capabilities.json --output <work>/pdf-probe.json
python <skill-dir>/scripts/run.py extract --plan <work>/case-plan.json --probe <work>/pdf-probe.json --capabilities <work>/capabilities.json --work <work> --output <work>/document.json
python <skill-dir>/scripts/run.py select-context --plan <work>/case-plan.json --document <work>/document.json --index-output <work>/context-index.json --budget-state <work>/context-budget.json --output <work>/selection-001.json --query "the method and evidence for the reported comparison"
```

脚本工具知道完整文档在哪，但 Agent 应先读紧凑索引和所选完整区块。后续仅针对所需页段运行：

```bash
python <skill-dir>/scripts/run.py select-context --plan <work>/case-plan.json --document <work>/document.json --index-output <work>/context-index.json --budget-state <work>/context-budget.json --output <work>/selection-002.json --cursor <上一次 next_cursor> --usage-event <agent-usage-001.json>
```

也可以指定 `--page 4 --page 5`、`--section Methods`、`--block-id p0004-b0006`、`--max-tokens 8000`。传新的选择路径，保留旧证据文件以便复用。页图必须读取 selection 中受限制的实际本地文件，而非渲染所有页面。

## 4. Agent 答案与最终结果

`answer.json` 的字段名和类型服从实际 `output_contract`。证据至少包含稳定区块和物理页，直接引文必须逐字属于该区块；可省略次要空白但不得修改数值、数学符号或语义。示意结构：

```json
{
  "fields": {"answer": "<仅基于已读证据写的答案>"},
  "status": "success",
  "sources": [{"block_id": "p0001-b0002", "page_number": 1}],
  "uncertainty": [],
  "errors": []
}
```

该结构要求原始契约确有 `answer` 字段时才能直接使用；其他命名需保持实际字段。Agent 生成真实答案后：

```bash
python <skill-dir>/scripts/run.py finalize-result --plan <work>/case-plan.json --document <work>/document.json --answer <work>/answer.json --output <output>/result.json
python <skill-dir>/scripts/run.py validate-result --plan <work>/case-plan.json --document <work>/document.json --result <output>/result.json --output <work>/result-validation.json
```

若最终化拒绝，修复所指字段/证据或明确部分完成，不能保留旧答案继续宣称最新结果有效。成功文件仍须由 Agent 判断原文是否真正支撑主结论。

全文任务和分段结果的完整格式见 [context-policy.md](context-policy.md)，异常状态见 [runtime-contract.md](runtime-contract.md)。需要核查分栏时先读 [layout-policy.md](layout-policy.md)，不要手工伪造 block、bbox 或继续跨页关系。

## 5. 包验证与模拟调用

```bash
python <skill-dir>/scripts/run.py validate-package --skill-dir <skill-dir> --output <work>/package-validation.json
```

安装到新扫描目录前需用户明确批准。将完整工作副本复制后，必须重读目标 `SKILL.md`、运行 `--help` 和小案例，以排除开发路径依赖。正常新会话才能证明 Skill 被发现及实际触发。

最小资源包含已声明的 `SKILL.md`、四份按需引用、两个合成契约示例和确定性脚本，不包含测试 PDF、虚拟环境、缓存、模型、页图、运行 manifest、原生轨迹或平台答案。示例文件的 PDF 路径和输出均为合成占位，不得作为执行结果或评分。

## 6. 已知待验证边界

- 公式拆分、重叠字形、复杂表格与扫描 OCR 不保证高保真，关键依赖需要真实页图检查。
- 当前 token 计数是保守估算，不代表模型 tokenizer、图像成本、Harness 历史预算或使用效果。
- 默认约 256K profile 不是对单轮可注入 256K 原文的许可，也没有实测最优配置的含义。
- 900 秒、64 次模型请求和 16K 单次响应限制需由相应平台实际验证。
- 本机 PyMuPDF 或其他开发环境的通过不证明默认环境可用；默认环境必须独立探测并实际回退 Poppler。
