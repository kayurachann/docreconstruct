# docreconstruct

[English](../../README.md) · [Tiếng Việt](README.vi.md) · 简体中文 · [Русский](README.ru.md)

将扫描文档重建为**可编辑**的 DOCX/HTML/Markdown，保留版面。实验阶段。

实测数字（非承诺）：三个示例的视觉相似度 23–57%；真实四页试卷通过 40/40 项
QA 检查，含 22 个原生 Office Math 公式。完整架构、基准与诚实的局限说明见
[英文 README](../../README.md)——本译文仅包含快速开始。

## 快速开始

```bash
pip install -e ".[all]"
```

最佳质量——三输入（人工校对的 Markdown + 原始扫描 + OCR JSON）：

```bash
python -m docreconstruct.cli hybrid content.md original.pdf -E evidence.json -o out.docx
```

单文件转换（质量取决于所用 OCR 引擎）：

```bash
python -m docreconstruct.cli reconstruct scan.pdf -o out.docx
```

用与 CI 相同的指标对照原图评分：

```bash
python -m docreconstruct.cli hybrid content.md original.pdf -o out.docx --qa-backend libreoffice
```

核心原则：**绝不从像素中臆造文字**——校对后的 Markdown 是内容权威，其中的
错误会被保留，而不是被悄悄"修正"。

许可证 Apache-2.0。示例图片版权归原发布者所有。
