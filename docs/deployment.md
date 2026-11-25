# evidence-pdf-reader 部署说明

本文面向将 `evidence-pdf-reader` 部署到实际 Agent 或 PDF 处理环境的操作。文中命令是部署模板，不表示已经在新环境中执行成功。正式运行时，Skill 只调用已激活环境的默认 `python`，不包含本机解释器前缀、Conda/uv 激活命令或 OCR 模型路径。

## 1. 目录与前置条件

先区分以下目录，后续命令中的相对路径以开发仓库根目录为起点：

| 名称 | 含义 | 权限要求 |
|---|---|---|
| 工作源码 | 根目录下的 `evidence-pdf-reader/` | 保留可审查副本，不作为可擦除缓存 |
| Skill 加载目录 | Host 明确允许的扫描根，或其临时模拟目录 | 读取；同名目标不存在时才可直接复制 |
| 案例工作目录 | 保存 capabilities、probe、document、index、selection、budget 和渲染页 | 当前运行用户可写，按案例隔离 |
| 输出目录 | 保存早期及最终 `result.json` | 当前运行用户可写，不能与另一案例混用 |

建议 Python 3.12；本机实际开发验证版本为 3.12.16。当前 Skill 不要求 GPU，不会自动联网下载依赖或模型。运行用户至少需要一项真实文字坐标提取能力：

- 首选：`PyMuPDF==1.28.2`；
- 回退：Poppler 的 `pdftotext`，最好同时提供 `pdfinfo` 和 `pdftoppm`；
- 可选 OCR：实际可执行的 Tesseract 及已安装的语言数据。其他 OCR 模型文件的存在不等于已经具备可执行识别能力。

## 2. 安装 Python 依赖

可以使用既有的、已获授权的隔离 Python 环境。若准备新建本地虚拟环境，可使用以下任一方式。安装依赖属于实际环境变更，执行前应确认目标解释器和写入权限；无需为了运行本文校验而重新安装已经满足的依赖。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Linux 或类 Unix shell：

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

若采用已有的 Conda 等受控环境，应先按本机管理流程完整激活，再执行 Python 命令；不得仅通过环境内解释器绝对路径绕过其运行时初始化。验证解释器及依赖：

```bash
python --version
python -c "from importlib.metadata import version; print(version('PyMuPDF'))"
python -m pip check
```

预期 PyMuPDF 版本为 1.28.2。若需要使用其他版本，应先读取实际能力清单并完成同输入回归，不能由版本可导入直接推断兼容性。

## 3. 配置 Poppler 与可选 OCR

通过操作系统的软件包管理方式安装 Poppler，确保相关可执行文件在本次进程的 `PATH` 中。不要依赖开发机器的绝对安装位置。

```bash
pdftotext -v
pdfinfo -v
pdftoppm -v
```

扫描页处理可安装 Tesseract，并检查所要求的语言数据：

```bash
tesseract --version
tesseract --list-langs
```

默认 OCR 语言为 `eng`，可通过本次进程的 `TESSERACT_LANG` 环境变量改为实际已安装的语言。OCR 引擎或语言包不可用时应保留扫描页失败范围；仅检测到模块、模型文件或路径，不能记为 OCR 成功。

所有 Python 模块之外的通用处理使用标准库；`requirements.txt` 不承担 Poppler、OCR 可执行文件、语言数据或平台依赖的安装。

## 4. 验证工作副本

在仓库根目录执行：

```bash
$workDir = "<writable-work-directory>"
python evidence-pdf-reader/scripts/run.py validate-package --skill-dir evidence-pdf-reader --output "$workDir/package-validation.json"
python evidence-pdf-reader/scripts/run.py capabilities --output "$workDir/capabilities.json"
```

预期 `package-validation.json` 中 `valid=true`，且命令输出 `package_valid=True`。正式资源包当前包含 18 个文件，总计 192081 字节；后续合法变更可能改变这些数字，但始终要满足最多 50 个 UTF-8 文本文件、单文件不超过 500 KB、总量不超过 2 MB、无符号链接的限制。

检查 `capabilities.json`：

- `preferred_extractor` 由运行环境实际选择为 `pymupdf` 或 `poppler`；两者均不可用时为 `none`，命令返回 3；
- 默认 Python 没有 PyMuPDF 时，若 Poppler 可用，应实际观察到 `poppler`；
- `ocr.available=true` 仍不等于真实识别质量通过；
- capabilities、其他 manifest 和日志只写入工作目录，不放入 Skill 资源包。

## 5. 安装到 Agent 扫描根

只有用户已经选定并授权扫描根时才执行本节。扫描根应取 Host 明确暴露的目录，不猜测隐藏注册表、发布 API 或个人专用目录。工作源码继续保留；复制安装，不移动源码。

### 5.1 Windows 示例

以下命令在仓库根目录执行。先将 `<approved-scan-root>` 替换为实际授权的扫描根：

```powershell
$scanRoot = "<approved-scan-root>"
$destination = Join-Path $scanRoot "evidence-pdf-reader"
if (Test-Path -LiteralPath $destination) {
    throw "Destination exists; inspect and decide before replacing or merging"
}
Copy-Item -LiteralPath ".\evidence-pdf-reader" -Destination $scanRoot -Recurse
Get-Content -LiteralPath (Join-Path $destination "SKILL.md") -TotalCount 8
```

### 5.2 Linux 示例

```bash
scanRoot="<approved-scan-root>"
if [ -e "$scanRoot/evidence-pdf-reader" ]; then
    printf '%s\n' 'Destination exists; inspect and decide before replacing or merging' >&2
    exit 1
fi
cp -R "evidence-pdf-reader" "$scanRoot/"
sed -n '1,8p' "$scanRoot/evidence-pdf-reader/SKILL.md"
```

如果目标目录已经存在，上述命令会停止。不得直接用复制、删除或递归覆盖替代对已有用户文件的检查。更新时先读新旧 `SKILL.md` 及相关资源，审查差异，再由用户决定更新、合并或取消。

从安装目标再次验证，确保不依赖开发仓库：

```bash
python "<installed-skill-dir>/scripts/run.py" --help
python "<installed-skill-dir>/scripts/run.py" validate-package --skill-dir "<installed-skill-dir>" --output "$workDir/installed-package-validation.json"
```

复制并读回只能记为 Installed，不能记为已经发现或 Ready。

### 5.3 项目级扫描根（DSH）

DSH 除了用户级技能目录，还会扫描工作目录所属项目根的 `.dsh/skills/` 与 `.agents/skills/`。因此可以只在本仓库内安装，不改动用户全局技能目录：

```bash
mkdir -p .dsh/skills
cp -r evidence-pdf-reader .dsh/skills/evidence-pdf-reader
find .dsh/skills/evidence-pdf-reader -name __pycache__ -type d -exec rm -rf {} +
python .dsh/skills/evidence-pdf-reader/scripts/run.py validate-package --skill-dir .dsh/skills/evidence-pdf-reader --output work/installed-package-check.json
```

- 把 `.dsh/skills/` 写入 `.gitignore`：安装副本不进版本库，源码仍以 `evidence-pdf-reader/` 为准。
- 副本必须与源码一致：`diff -r evidence-pdf-reader .dsh/skills/evidence-pdf-reader` 应无输出（比较前清理双方的 `__pycache__`）。
- 更新流程是「改源码 → 重新复制 → 校验」，不要直接修改安装副本。
- 新会话的技能清单出现 `evidence-pdf-reader`、描述取自 `SKILL.md` frontmatter，只说明已被发现；触发与不触发是否符合预期，仍须按第 8 节在真实任务里验证。

## 6. Harness 发布目录与运行目录

若真实 Harness 使用约定的 `/app/submission/`、`/app/work/`、`/app/output/` 路径，应在获得该环境写入授权后，将工作副本的**内容**复制到 submission 根，使 `/app/submission/SKILL.md` 存在。不要多包一层同名父目录，也不要覆盖已有正式案例材料。

```bash
# 先检查目标内容与写入权限，再根据实际平台约定执行。
cp -R "evidence-pdf-reader/." "/app/submission/"
mkdir -p "/app/work/case-001" "/app/output"
```

评测器负责将 submission 安装到其指定加载位置；本地复制不能证明平台已经完成安装。外部 `case.json`、`task.md` 和真实 PDF 属于输入材料，不应拷入正式 Skill 资源包。

只读安装目录可设 `PYTHONDONTWRITEBYTECODE=1`，以免尝试生成 Python 字节码缓存。所有实际 JSON、页图、OCR 输出和日志仍应放到案例工作目录；输出目录只保留实际案例结果。

## 7. 最小部署验收

准备一份有真实 PDF 的小型文字版案例。将下列变量替换为实际绝对路径：

- `$skillDir`：安装后的 Skill 目录；
- `$workDir`：本案例独立、可写的工作目录；
- `$outputDir`：本案例结果目录；
- `$caseJson`、`$taskMd`、`$pdfPath`：本案例输入材料。

```bash
python "$skillDir/scripts/run.py" capabilities --output "$workDir/capabilities.json"
python "$skillDir/scripts/run.py" inspect-case --case "$caseJson" --task "$taskMd" --pdf "$pdfPath" --work "$workDir" --output "$workDir/case-plan.json" --early-result "$outputDir/result.json"
python "$skillDir/scripts/run.py" probe-pdf --plan "$workDir/case-plan.json" --capabilities "$workDir/capabilities.json" --output "$workDir/pdf-probe.json"
python "$skillDir/scripts/run.py" extract --plan "$workDir/case-plan.json" --probe "$workDir/pdf-probe.json" --capabilities "$workDir/capabilities.json" --work "$workDir" --output "$workDir/document.json"
python "$skillDir/scripts/run.py" select-context --plan "$workDir/case-plan.json" --document "$workDir/document.json" --index-output "$workDir/context-index.json" --budget-state "$workDir/context-budget.json" --output "$workDir/selection-001.json" --query "problem method results" --max-tokens 8000
```

Agent 接着读取任务、紧凑索引和必要的 selection 原文，而不是整体加载 `document.json`。由 Agent 生成实际 `answer.json` 后，再执行：

```bash
python "$skillDir/scripts/run.py" finalize-result --plan "$workDir/case-plan.json" --document "$workDir/document.json" --answer "$workDir/answer.json" --output "$outputDir/result.json"
python "$skillDir/scripts/run.py" validate-result --plan "$workDir/case-plan.json" --document "$workDir/document.json" --result "$outputDir/result.json" --output "$workDir/result-validation.json"
```

最终化返回 0 且独立验证为真，表示该案例的契约和证据结构通过。仍须由 Agent 检查证据是否真正支撑科学结论。`assets/example_case.json` 与 `example_output.json` 只是合成接口示例，不能作为实际部署答案或评分。

全文任务应使用 `--full-document --segments-dir` 并保存实际分段结果；不能把每批原文和历史全文反复注入模型。估算是保守参数，不是模型计量，详见[上下文预算说明](../evidence-pdf-reader/references/context-policy.md)。

## 8. 真正的安装验证与新会话试用

完成本地 smoke test 后，仍需在**新的 Agent 会话**确认 Host 发现并触发 Skill，至少检查：

1. 代表任务：真实双栏科研 PDF 的问答，查看页码和区块是否正确，是否先读索引和选择结果；
2. 长上下文任务：全文提取或翻译，核查分段、术语表、原文哈希、续接和覆盖；
3. 边界任务：无文字层扫描 PDF。没有可执行 OCR 时应诚实部分完成或失败；
4. 最近邻非触发：仅要求普通网页总结时，不应调用本 Skill。

还应核查真实模型请求数、历史上下文、单次响应和运行时限。只有实际 Host 记录支持时，才可报告约 256K 上下文、900 秒、64 次请求或相关平台指标的验证结果。未经这些检查，状态保持“已安装但待新会话/平台验证”，不标为 Ready。

## 9. 常见问题

| 现象 | 检查与处理 |
|---|---|
| `capabilities` 返回 3 | 当前解释器与 PATH 中没有提取器。核对环境和实际工具；不要手工伪造 capabilities。 |
| Python 没有 PyMuPDF | 使用 `requirements.txt` 安装，或用已检测到的 Poppler 路径。两条路径需分别验证。 |
| `probe-pdf` 返回 5 | 可能是扫描、加密或损坏输入，或 OCR 尚不可用。先读 probe 页级状态，不把 5 一律当作工具崩溃。 |
| `extract` 返回 6 | 没有成功处理任何正文页。检查 extract 中真实失败页、日志位置及渲染/OCR 条件。 |
| 混排文字乱码 | 检查输入与文件编码是否为 UTF-8，并设置当前进程的控制台编码。不要据此判定源 PDF 是扫描件。 |
| `finalize-result` 返回 8 | 查看同目录的 `*.finalize-validation.json`。旧的有效结果未被覆盖；修复字段、引用或状态后重试。 |
| `select-context` 返回 11 | 当前安全预算不足。使用已有 selection、分段和游接续接；不要静默截断或把未完成范围标为全文完成。 |
| 技能已复制但未触发 | 必须在 Host 接受的扫描根复制并开启新会话；旧会话不会因此自动证明重新发现。 |

九个命令的完整参数与退出码见[运行契约](../evidence-pdf-reader/references/runtime-contract.md)和[调用说明](../evidence-pdf-reader/references/usage.md)。

## 10. 开发与部署的目录边界

`docs/`、`tests/`、`requirements.txt`、`requirements-dev.txt` 和开发记录留在开发仓库。正式 submission 只包含经过审查的 `evidence-pdf-reader/` 资源内容，不带测试夹具、真实开发 PDF、模型、沙箱日志、缓存、能力清单或模拟原生轨迹。

完整开发测试从仓库根目录运行，需要先按 `tests/fixtures/manifest.json` 准备测试材料及真实 PDF；不能假设只安装资源包就会包含测试输入。当前工作区最后一次全量验证为 84 项测试通过，该结果用于当前提交审查，不替代目标环境的最小部署验收。
