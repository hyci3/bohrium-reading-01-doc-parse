# 约 256K tokens 的上下文预算

## 1. 默认 profile

下表是面向约 256K tokens 名义上下文的**保守工程初值，不是模型实测结论**。真实可用上限取模型、Harness 和用户约束中最严格者。

| 参数 | 初始值 | 用途 |
|---|---:|---|
| `context_budget_tokens` | 256000 | 名义总上下文 |
| `runtime_reserve_tokens` | 65536 | 系统、Skill、工具调用和未逐项记录的运行交互预留 |
| `case_source_injection_budget_tokens` | 96000 | 全案索引、证据摘录及论文原文累计软上限 |
| `single_selection_max_tokens` | 24000 | 单批新证据上限 |
| `response_reserve_tokens` | 16384 | 单次生成及推理最低预留 |
| `document_index_max_tokens` | 12000 | 紧凑索引上限，计入全案源注入 |
| `single_tool_report_max_tokens` | 2000 | 状态与错误说明推荐上限，详细结果落盘 |
| 页图 | 单批 2、全案 6 | 独立于文本计费的最低风险页范围 |

页图按每幅 1500 estimated tokens 暂记，明确 `cost_verified=false`；这不是任何视觉模型的实测费率。不得把图像算作零成本。

## 2. 估算与预算状态

脚本当前采用混合字符启发式：CJK 字符各计 1，其他非空白字符除以 3，再乘安全系数 1.25。JSON 标点、结构和证据字段也进入估算。输出 `token_count_method=estimated`、`safety_factor=1.25`，误差未知；这些值绝不能报告为 tokenizer 精确值。

状态文件 `context-budget.json` 记录：

- 文档和案例哈希、索引是否已计入、当前所有源注入累计值；
- 新证据区块、历史选择产物、续接游标和全文分段状态；
- 已登记实际和估算非原文 token、页图、紧凑术语表以及未测历史项。

`actual_tokens` 必须来自 Host 的真实计量，优先于同一 event 的 `estimated_tokens`。没有可用 tokenizer 或历史未知不能冒充精确。初始未测历史及无 token 数值的 usage event 将有效源注入软上限乘以 0.85，收紧可用预算。

Agent 在主要模型响应或大工具结果后，写出如下事件并在下一次选择中登记：

```json
{
  "event_id": "agent-response-001",
  "kind": "model_interaction",
  "actual_tokens": 1800
}
```

若 1800 无真实计量则改为 `estimated_tokens`。相同 `event_id` 只登记一次。event 记录的是新增非论文原文交互，不重复登记已选择区块，也不能把整次总上下文的数直接当成后续增量。没有可靠新增量时可记录空数值事件并采用更保守预算。

当前剩余新证据额度取下列最小值：`--max-tokens`、单批上限、未测历史折减后的全案源预算剩余、总上下文扣除双预留/已用原文及历史后的剩余。历史不能释放时，分段本身不会恢复上下文。

## 3. 分层读取与续接

1. 先读任务、契约、状态和紧凑索引。
2. 再按问题读相关章节、页码、题注、表格、公式或指定区块。
3. 只读回答关键问题所需的完整入选原文和少量相关页图。

`document.json`、inventory、原始 bbox、完整日志和 OCR 结果是磁盘证据层。可让脚本按 `--page`、`--section`、`--query`、`--block-id` 查找，但不能为了“检查”把整份文档加载进模型。一次选择中按原阅读顺序输出，保留 `block_id`、物理页、章节路径、完整源文本和必要的公式但省略原坐标细节。

只在完整证据块边界截断，不截半句话、半个数值或一张表。`truncated=true` 时保存 `omitted_block_ids`、首尾范围及 `next_cursor`。query 型任务续接显式传 `--cursor`；全文模式还从预算状态取最后游标。没有查询匹配时返回警告而不是自动制造摘要。

相同 `block_id` 的重复选择返回 `reuse_only` 和先前选择文件位置，不重复发送原文、不重复计费。应为每次选择使用不同的文件名；若必须重读，可引用先前完整选择文件并计及该次实际注入占用。

单批部分选择但仍有续接额度时返回 0，保留下一批。安全预算耗尽、下一最小完整块无法纳入或全文仍未覆盖时返回 11，保存已完成内容和剩余范围。无声截断、用无依据摘要填缺口或标为完整成功都是错误行为。

## 4. 全文献分段

全文翻译、全文提取和全结构化使用：

```bash
python <skill-dir>/scripts/run.py select-context --plan <work>/case-plan.json --document <work>/document.json --index-output <work>/context-index.json --budget-state <work>/context-budget.json --output <work>/full-selection-001.json --full-document --segments-dir <work>/segments --glossary <work>/glossary.json
```

主流程默认排除单独分类的页眉、页脚，并在 manifest 的范围中说明。每段 `segment-NNNN.input.json` 包含原文完整区块、页码、原顺序、本文档哈希、本段 `input_sha256` 与实物 page/block 覆盖。状态记录真实剩余区块。术语表为最多 16 项实际 Agent 约束，每项长度受限；不会在每段重复历史全文或译文。

当前段处理完成后，Agent 写 manifest 指定的 `segment-NNNN.segment-result.json`：

```json
{
  "segment_id": "segment-0001",
  "document_sha256": "<与该 input 文件相同的文档哈希>",
  "input_sha256": "<与该 input 文件相同的本段哈希>",
  "block_ids": ["<必须与 input 的 block_ids 完整且同序一致>"],
  "status": "complete",
  "fields": {
    "<原 output_contract 中的字段名>": "<这一段的真实结果>"
  },
  "sources": [{"block_id": "<有效区块>", "page_number": 1}],
  "errors": []
}
```

示例仅描述接口。`fields` 只可使用原输出契约已有字段及其原类型；string 按段拼接，array 顺序连接，object 检查冲突后合并。数值或布尔字段不是可安全自动累加的全文结果，需要 Agent 另行综合。哈希、覆盖或字段不符都会写入 merge 错误；顺序采用 manifest 序号和原区块顺序，不允许后来的段覆盖先段而无声接续。

最终调用：

```bash
python <skill-dir>/scripts/run.py finalize-result --plan <work>/case-plan.json --document <work>/document.json --answer <work>/answer.json --segments-dir <work>/segments --merge-output <work>/merged-segments.json --output <output>/result.json
```

`answer.json` 的实际结论与已合并字段不一致时拒绝自动覆盖。合并会补齐其支持的字段和证据引用。分段缺失、未完成或存在错误时 `merged-segments.json` 标记 `partial`；若因此不能形成完整答案，保留该合并产物与其他分段，向人工明确报告未完成页段、校验错误或下一步 cursor。没有实测真实 Agent 操作的全文结果时，不把脚本的估算测试表述为端到端模型通过。
