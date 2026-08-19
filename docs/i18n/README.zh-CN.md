# docreconstruct — 简体中文指南

[英语](../../README.md) · [越南语](README.vi.md) · **简体中文** ·
[俄语](README.ru.md)

`docreconstruct` 可将 PDF、扫描件和文档照片重建为结构清晰、可继续编辑的
文档。在整个流程中，OCR 结果只是用于核对的输入之一，并非最终成品。项目
会通过统一的文档模型、版面规划和输出模块生成 DOCX 文档、网页或 JSON 数据。

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
一个独立的 Microsoft Word 分节，保留相应的物理页面尺寸，并强制从新页
开始。如果不同页面上的独立证据能够确认内容确实延续，同一语义组可以跨页；
空白页或被 OCR 遗漏的页面仍会保留为空节，不会把后一页的内容错误移到前面。
默认 QA 会检查规划出的节数；显式启用 LibreOffice 检查后，还会要求渲染页数
与原件页数完全一致。

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

这些文件均由 CLI 所使用的同一套通用重建流程生成。这里同时提供高分辨率
原图、可下载的可编辑 DOCX 文件和项目生成的渲染预览，便于您直接核对实际
效果，而不必仅凭截图判断。文件来源详情见[示例文件说明](../showcases/README.md)，
精确校验值见 [SHA-256 清单](../showcases/SHA256SUMS.txt)。

> **务必核验：** OCR 结果以及识别服务导出的 Markdown 可能含有拼写、变音
> 符号、公式、表格或阅读顺序错误。由于 Markdown 是文字内容的最终依据，
> DOCX 文件也可能原样保留这些错误。在依赖可编辑结果之前，请务必逐项对照
> 原图。

### 宣光省重点中学数学考试——第 1 页，试卷代码：0110（来源：VietnamNet）

**来源：** [VietnamNet](https://vietnamnet.vn/)；此署名由贡献者提供，相关
权利说明见[示例文件说明](../showcases/README.md)。

| 拍摄的原始试卷页 | 项目从可编辑 DOCX 文件生成的预览 |
| :---: | :---: |
| [<img src="../showcases/math-exam/source-original.png" alt="宣光省重点中学数学考试原始试卷页" width="420">](../showcases/math-exam/source-original.png) | [<img src="../showcases/math-exam/rendered-preview.png" alt="可编辑数学试卷 DOCX 文件的渲染预览" width="420">](../showcases/math-exam/rendered-preview.png) |

**相关文件：** [原图](../showcases/math-exam/source-original.png) ·
[可编辑 DOCX 文件](../showcases/math-exam/editable.docx) ·
[渲染预览](../showcases/math-exam/rendered-preview.png)

此示例展示了实拍页面、结构复杂的页眉、Microsoft Word 原生表格、可编辑的
原生公式、四选一选项布局，以及对原图中函数变化表的复用。手写内容、照片
透视畸变、OCR 漏识别的文字和某些页面装饰不保证能够重建为可编辑内容。

### 微积分推导——可编辑的 Microsoft Word 原生公式（来源：PaddleOCR）

**来源：** [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)；OCR 结果及
导出内容的出处说明由贡献者提供。

| 原图 | 项目从可编辑 DOCX 文件生成的预览 |
| :---: | :---: |
| [<img src="../showcases/calculus-derivation/source-original.jpg" alt="微积分推导原图" width="420">](../showcases/calculus-derivation/source-original.jpg) | [<img src="../showcases/calculus-derivation/rendered-preview.png" alt="可编辑微积分推导 DOCX 文件的渲染预览" width="420">](../showcases/calculus-derivation/rendered-preview.png) |

**相关文件：** [原图](../showcases/calculus-derivation/source-original.jpg) ·
[可编辑 DOCX 文件](../showcases/calculus-derivation/editable.docx) ·
[渲染预览](../showcases/calculus-derivation/rendered-preview.png)

此示例展示了分数、积分、极限、上下标、对齐推导和中英文混排中的可选择、
可编辑原生公式。通用版面规划器将 10 个可编辑内容块映射到原图中的全部
18 行内容；最终 DOCX 文件渲染为一页，保留 8 个原生公式和 13 行独立展示
内容，且不会露出公式排版用的对齐标记。项目 QA 的 34 项实测检查全部通过，
前景归一化视觉相似度为 92.58%。该分数只能说明效果有所改善，不能证明每个
字形或数学陈述在语义上都正确。

### 宣光省重点中学——越南语第二次考试（来源：VNExpress）

**来源：** [VNExpress](https://vnexpress.net/)；此署名由贡献者提供，相关
权利说明见[示例文件说明](../showcases/README.md)。

| 原图 | 项目从可编辑 DOCX 文件生成的预览 |
| :---: | :---: |
| [<img src="../showcases/vietnamese-exam/source-original.png" alt="越南语第二次考试原始试卷页" width="420">](../showcases/vietnamese-exam/source-original.png) | [<img src="../showcases/vietnamese-exam/rendered-preview.png" alt="可编辑越南语试卷 DOCX 文件的渲染预览" width="420">](../showcases/vietnamese-exam/rendered-preview.png) |

**相关文件：** [原图](../showcases/vietnamese-exam/source-original.png) ·
[可编辑 DOCX 文件](../showcases/vietnamese-exam/editable.docx) ·
[渲染预览](../showcases/vietnamese-exam/rendered-preview.png)

此示例展示了双区域试卷页眉、越南语衬线字体、缩进并两端对齐的文章段落、
出处标注位置、供考生填写信息的点线和可编辑题目。OCR 仍可能产生拼写及
变音符号错误；原图水印、已遮盖的考生信息和其他仅以像素图像存在的标记，
不会被悄然重建为可编辑文字。

上述来源署名由贡献者提供，仅用于记录贡献者所提供的出处信息，既不授予
再使用许可，也不代表原出版机构或 OCR 项目认可或推荐 `docreconstruct`。
项目代码的许可证不会自动授予对这些第三方试题内容、标志、水印、手写内容
或其他素材的使用权；原有权利仍归各权利人所有。重新分发或使用任何示例文件
前，请自行核查相关权利和隐私要求。

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
- 逐像素复刻原件与使用 Microsoft Word 原生对象实现深度编辑有时是相互
  冲突的目标。本项目无法保证对所有文档都做到 1:1 重建。

在考试、档案、法律、医疗、金融、合规或其他重要场景中使用之前，请务必
逐项对照原件，并请相关领域的专业人员复核。

如需了解完整的编程接口、支持的 OCR 来源、系统架构、参考项目、隐私说明和
许可证，请阅读[英语版说明文档](../../README.md)。
