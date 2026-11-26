# evidence-pdf-reader

玻尔（Bohrium）产品赛第 08 题「阅读01｜文献文档解析」参赛作品：一个可被 Agent 调用的科研 PDF 解析 Skill，把 PDF 变成**能回到原页、原区块的证据文档**，再用它完成翻译、抽取、核验与问答。

当前状态：`Installed` —— 结构有效、已本地验证（96 项测试、真实论文与双后端验证）。尚未完成 Host 扫描根安装后的新会话触发验证与 Harbor 官方平台评测，因此**不声称 `Ready`**。

## 它比通用 PDF 读取多做了什么

1. **多栏阅读顺序恢复**：按页宽比例与水平间隙识别 2–3 栏，跨栏区块（宽 ≥68% 页宽，或左 ≤33% 且右 >62%）与图注、脚注、题注单独处理，并用 `evaluate-order` 对独立金标准计分。
2. **区块级证据**：每个区块带页号、bbox、稳定 `block_id` 与文档 sha256；引文必须与区块文本逐字匹配才算有效证据。
3. **上下文预算**：紧凑索引（默认 12000 tokens）、单批 24000、全案源文本 96000、响应预留 16384、页图 2 张/批与 6 张/案，预算耗尽显式报错而不是静默截断。
4. **诚实的状态与契约**：`success` / `partial` / `failed` 与 `errors`、`uncertainty`、`change records` 一起写入 `result.json`，`validate-result` 与 `validate-package` 做本地校验。

## 目录

| 路径 | 内容 |
| --- | --- |
| `evidence-pdf-reader/` | 提交给平台的 Skill：`SKILL.md`、`references/`、`scripts/`、`assets/` |
| `docs/requirements.md` | 需求规格（FR-001～FR-029，含 P0/P1/P2 与验收口径） |
| `docs/skill-structure-plan.md` | 设计决策、数据契约、依赖降级矩阵 |
| `docs/development-plan.md` | 阶段计划与执行状态（第 11 节记录实际进展与缺陷修复） |
| `docs/deployment.md` | 依赖安装、Poppler/OCR、Host 安装与最小验收 |
| `tests/` | 合成夹具、双栏金标准、契约/顺序/预算/压力测试 |
| `bohrium/` | 真实论文与多后端测试报告 |
| `reading-01-doc-parse/` | 题目材料与评分口径 |

## 环境要求

- Python ≥3.10（开发用 3.12）。
- `pymupdf`（首选提取器）或 Poppler `pdftotext`（回退）；OCR 仅在运行时实际可用时启用，不可用则报告 `ocr_unavailable` 并降级。
- 开发依赖见 `requirements-dev.txt`；运行时不需要安装本仓库即可执行脚本。

## 九个命令

```bash
python evidence-pdf-reader/scripts/run.py --help

# 1 能力探测          2 读输入契约并写早期结果      3 探测 PDF 页面与可用路径
capabilities          inspect-case                   probe-pdf
# 4 抽坐标并恢复顺序   5 与独立金标准比对顺序        6 生成紧凑索引并按预算选证据
extract               evaluate-order                 select-context
# 7 合并答案与证据     8 校验结果契约与证据          9 校验 Skill 资源包
finalize-result       validate-result               validate-package
```

典型顺序：`capabilities` → `inspect-case` → `probe-pdf` → `extract` → `select-context` → （写 `answer.json`）→ `finalize-result` → `validate-result`；长文档可先按页段写入 `segments/`，再由 `finalize-result --segments-dir` 确定性合并。退出码：2 通用错误、3 无提取器、4 契约/输入、5 PDF 不可处理、6 无成功正文页、7 金标准无效、8 合并拒写、9 结果校验失败、10 包不合法、11 预算耗尽。

完整流程与约束见 [`evidence-pdf-reader/SKILL.md`](evidence-pdf-reader/SKILL.md)、`references/runtime-contract.md`、`references/context-policy.md`。

## 验证数字（2026-11-26）

- `unittest`：**96 项全部通过**（0 跳过）。
- 包校验：**22 个文件、259131 字节、0 错误 0 警告**。
- 真实论文：BERT / ViT / DETR / LoRA / Attention / ResNet 六篇，PyMuPDF 117 页 + Poppler 重复处理 68 页 = 9 次后端运行 185 运行页，失败与跳过页均为 0。

## 已知局限

- 章节层级仍是启发式，Poppler 后端的 `section_path` 有已知缺漏。
- `table` 候选只表示版面可能构成表格，不代表已验证二维表结构。
- 扫描页 OCR 只有通用适配接口，本机尚未跑通真实 OCR 成功路径。
- 视觉复核覆盖 10 个目标页，不等于全文图像语义已验证。

## 提交信息

题面要求在提交根放置 `GITHUB_URL.txt`，首行为公开仓库地址，见 [`GITHUB_URL.txt`](GITHUB_URL.txt)。
