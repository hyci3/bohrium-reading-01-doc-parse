# 实测基线、独立复核与失败模式

本文件记录本 Skill 在**真实论文**上的实测结果、复核方法，以及已知失败模式。它的作用是让使用者判断"能不能信这套输出、该在哪儿加一次人工复核"，而不是宣称任何形式的准确率。

## 1. 为什么用真实论文而不是合成 fixture

合成 PDF 只能验证契约与坐标计算，无法暴露真实排版的三种麻烦：跨栏标题被拆成多个字号 run、公式上下标散成十几个 glyph、表格合并单元格导致空白格。因此基线一律用公开论文 PDF，覆盖单栏、双栏、长文、含大图与大量公式四类版面。

## 2. 已验证的论文

| 论文 | 页数 | 版面 | 本轮用途 |
| --- | --- | --- | --- |
| BERT (1810.04805) | 16 | 单栏 | 题注/章节路径、Poppler 回退 |
| ViT (2010.11929) | 22 | 单栏 + 大图 | 图表配对 |
| DETR (2005.12872) | 26 | 双栏 | 跨栏恢复、Poppler 回退 |
| LoRA (2106.09685) | 26 | 单栏 + 表格 | 表格候选、Poppler 回退 |
| Attention Is All You Need (1706.03762) | 15 | 单栏 + 公式密集 | 公式行序、上下标 |
| ResNet (1512.03385) | 12 | 双栏 | 跨栏、partial 状态 |
| Segment Anything (2304.02643) | 30 | 双栏 + 大量图 | 图表与题注、表格误检 |
| LLaMA 2 (2307.09288) | 77 | 长文双栏 | 分段预算、附录编号 |
| QLoRA (2305.14314) | 26 | 单栏 + 公式/表格 | 公式碎片合并、表格行边界 |

九篇合计 250 页，PyMuPDF 与 Poppler 两个后端共 13 次完整运行，失败页 0。

## 3. 本轮修复前后（同一批 PDF、同一套参数）

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| 被误判为标题的句子（SAM/LLaMA 2/QLoRA） | 14 / 1 / 5 | 0 / 0 / 0 |
| `section_path` 被正文污染的页（QLoRA） | 3 | 0 |
| 保留行边界的表格（SAM/LLaMA 2/QLoRA） | 0/24, 0/36, 0/1 | 24/24, 36/36, 1/1 |
| 识别出的附录小节标题（LLaMA 2/QLoRA） | 0 / 0 | 28 / 2 |
| 合并后的公式块（QLoRA 式 4/5/6） | 11+5+5 个碎片 | 3 个完整块 |
| Poppler 后端的章节标题（QLoRA） | 6 | 15 |
| 独立复核的引文命中率 | 61/87 | 87/87 |

引文复核用的是不 import 本 Skill 的独立脚本：PyMuPDF 逐页取文本，做 NFKC + 连字符断词 + 空白折叠两级归一化，再逐条比对 `result.json` 里的 `quote` 与它声明的页码。这比 `validate-result` 更严格，因为后者只检查区块内子串。

## 4. 独立复核怎么做（给使用者的可复制步骤）

```bash
# 1) 让 Skill 产出 result.json（含 sources[].quote / block_id / page_number）
# 2) 用与 Skill 无关的工具取同一页原文
pdftotext -f 10 -l 10 -layout paper.pdf - | grep -F "<quote>"
# 或 PyMuPDF 单页取值后自行归一化
python - <<'PY'
import pymupdf, unicodedata, re
page = pymupdf.open("paper.pdf")[9]
text = re.sub(r"\s+", " ", page.get_text()).lower()
print("HIT" if "<quote>".lower() in text else "MISS")
PY
```

两条判断标准：页码必须对得上（不是"文档里某处出现过"）；引文必须逐字一致（含上下标写法，不含你改写过的句子）。

## 5. 已知失败模式

| 现象 | 触发条件 | 现在的处理 | 建议 |
| --- | --- | --- | --- |
| 公式被拆成多块 | 上下标或分式行分别成为 layout block | 按垂直重叠与水平间隙合并，标 `formula_merged_fragments` | 引用公式前看页图，标 `formula_layout_unverified` 的不要直接引用 |
| 符号缺字形 | 数学字体无 Unicode 映射（常见大括号） | 保留原始控制码点并标 `symbol_font_glyphs_missing` | 只引用公式编号与已核对的片段 |
| 表格空白格 | 合并单元格、跨行表头 | 标 `table_merged_cells_unresolved`，逐行输出 | 读数值前核对行列 |
| 表格整体不可靠 | 图片式表格被误检为表 | 空格占比 ≥50% 时降级为编号行并标 `table_layout_unreliable` | 视为候选，不要当数据源 |
| 标题编号被拆块 | Poppler 遇字号变化切块 | 同基线窄编号块回拼，标 `poppler_split_numbering_rejoined` | 看到该警告时核对页图 |
| 扫描页无文字层 | 纯图像 PDF | `probe-pdf` 返回 5，页面标 `review_ocr_required` | 确认 OCR 可执行后再接管，见 runtime-contract |
| 页脚/脚注被当正文 | 底部编号短句 | 词数 ≥9 且以句末标点结尾才降级为 body | 交叉引用的脚注需人工确认 |

## 6. 复现

```bash
python scripts/run.py capabilities --output work/capabilities.json
python scripts/run.py probe-pdf --plan work/case-plan.json --capabilities work/capabilities.json --output work/pdf-probe.json
python scripts/run.py extract --plan work/case-plan.json --capabilities work/capabilities.json --probe work/pdf-probe.json --work work --output work/document.json
python scripts/run.py coverage-report --document work/document.json --output work/coverage.json
```

`document.json` 里每个区块都带 `page_number`、`block_id`、`bbox`、`section_path`、`warnings` 与 `extraction_method`，因此上面每条结论都能回到具体页与具体区块复核。
