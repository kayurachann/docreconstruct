# docreconstruct — 简体中文指南

[English](../../README.md) · [Tiếng Việt](README.vi.md) · **简体中文** ·
[Русский](README.ru.md)

`docreconstruct` 可将 PDF、扫描件和文档照片重建为结构清晰、可继续编辑的
文档。在整个流程中，OCR 结果只是用于核对的输入之一，并非最终成品。项目
会通过统一的文档模型、版面规划和输出模块生成 DOCX、HTML 或 JSON。

## 推荐的三类输入

同时提供以下三类相互补充的输入，通常能得到最可靠的结果。三者各有用途，
不能简单地互相替代：

| 输入 | 项目以此作为判断依据的内容 |
| --- | --- |
| 已人工校对的 `content.md` | 准确的文字内容和预期阅读顺序；项目不会擅自改写或补写文字 |
| 一个或多个由 OCR/版面分析服务生成的 `.json` 文件 | 页面与内容块的对应关系、坐标、内容类型、表格、公式、格式、识别置信度以及来源信息 |
| 原始 PDF 或图像 | 实际页面尺寸、视觉外观、分栏、表格、插图以及需要取用的原图区域 |

项目会先分别对每个 JSON 文件进行规范化和对齐，再综合各来源的信息。JSON
可以补充版面和结构信息，但不能覆盖 Markdown 中的文字；原始文件则始终是
页面外观和几何尺寸的最终参照。与当前文档无关或存在冲突的 JSON 会被拒绝，
或者在 QA 报告中明确列出。如果缺少其中一类输入，项目仍可运行，但可核对
的项目会减少，因此应将输出视为可信度较低的结果。

默认情况下，来源文件中的页码必须准确对应。只有当两个页面序列都完整、
连续且页数相同，项目才会按先后顺序重新配对，例如把 OCR 标记为第 5–6 页
的内容对应到编号为第 1–2 页的两张裁剪图。每次重新配对都会在检查报告中
留下警告。项目绝不会猜测缺页或页码不连续的序列。各 OCR 来源使用的坐标
单位和预处理信息也会保留下来，以便将位置正确换算回原始页面。

```powershell
docreconstruct hybrid content.md original.png `
  --evidence paddleocr.json `
  --evidence mineru.json `
  --output output/result.docx `
  --qa-report output/result.qa.json
```

可以多次使用 `--evidence`，同时引入彼此独立的识别结果。如果项目无法可靠
判断 JSON 的数据结构，请明确指定它来自哪个 OCR 服务，不要让项目猜测：

```powershell
docreconstruct hybrid content.md original.pdf `
  --evidence result.json `
  --evidence-provider result.json=paddleocr `
  --output output/result.docx
```

### 多页原始文档

处理多页 PDF 时，项目会逐页分析并分别规划版面。原件中的每一页都会生成
一个独立的 Word 节，保留相应的物理页面尺寸，并强制从新页开始。如果不同
页面上的独立证据能够确认内容确实延续，同一语义组可以跨页；空白页或被 OCR
遗漏的页面仍会保留为空节，不会把后一页的内容错误移到前面。默认 QA 会检查
规划出的节数；显式启用 LibreOffice 检查后，还会要求渲染页数与原件页数完全
一致。

```powershell
docreconstruct hybrid complete-document.md multi-page-original.pdf `
  --evidence provider-result.json `
  --output output/complete-document.docx `
  --qa-backend libreoffice `
  --qa-report output/complete-document.qa.json
```

## 快速安装

```powershell
python -m pip install -e ".[hybrid]"
docreconstruct hybrid content.md original.png -o output/result.docx
```

只有在用户主动启用图像对比检查时，项目才会调用 LibreOffice 进行文档
渲染：

```powershell
docreconstruct hybrid content.md original.png -o output/result.docx `
  --qa-backend libreoffice `
  --min-visual-score 0.80 `
  --qa-report output/result.qa.json
```

## 成功重建示例

以下文件均由 CLI 所使用的同一套通用流程生成。您可以直接查看原图、项目
根据 DOCX 生成的渲染图以及可编辑的 Word 文件，自行比较效果：

- **Tuyen Quang gifted school Math exam - Page 1 - Exam code: 0110** — 来源：
  [VietnamNet](https://vietnamnet.vn/)。
- **Calculus derivation - editable Office Math** — 来源：
  [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)。
- **Tuyen Quang gifted school - Vietnamese 2nd exam** — 来源：
  [VNExpress](https://vnexpress.net/)。

原图、DOCX 渲染图、可编辑的 DOCX 文件和 SHA-256 校验值均保存在
[示例目录](../showcases/README.md)中。上述来源信息由贡献者提供，用于说明
文件出处和便于追溯；它不等同于再使用许可，也不代表出版机构或 OCR 项目
对 `docreconstruct` 的认可或背书。

## 必须人工复核的限制

- OCR 或 Markdown 可能会漏掉内容，也可能错误识别拼写、变音符号、数字、
  标点、科学记号、数学运算符和手写文字。
- 公式即使保持可编辑，也可能在运算符、括号、对齐位置、极限位置或换行处
  出错。
- 表格、分栏、阅读顺序、字体、间距、插图、页眉、页脚和分页效果可能在
  原件、Microsoft Word 与 LibreOffice 之间有所不同。
- OCR 来源给出的置信度和通过自动 QA 检查只能作为参考，不能证明数学、
  法律或其他专业内容正确无误。
- 如果 Markdown 与原件不一致，组合流程仍以 Markdown 的文字为准，不会
  擅自猜测或修改内容。
- 逐像素复刻原件与使用 Word 原生对象实现深度编辑有时是相互冲突的目标。
  本项目无法保证对所有文档都做到 1:1 重建。

在考试、档案、法律、医疗、金融、合规或其他重要场景中使用之前，请务必
逐项对照原件，并请相关领域的专业人员复核。

如需了解完整的 API、支持的 OCR 来源、系统架构、参考项目、隐私说明和
许可证，请阅读[英文 README](../../README.md)。
