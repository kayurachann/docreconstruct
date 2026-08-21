# Showcase artifact notes

The root README includes these files as inspectable examples of the generic
`docreconstruct` hybrid pipeline. Each source image is the exact image associated
with the linked reconstruction artifact; the calculus source was independently
matched to its QA manifest by SHA-256.

The rendered previews were produced from the linked DOCX files through the
project's LibreOffice-backed rendering API. They are not manually retouched
screenshots. The DOCX files retain native editable paragraphs, tables, and/or
Office Math where supported.

Contributor-supplied source attributions are: VietnamNet for the Tuyen Quang
math exam image, PaddleOCR for the calculus OCR/export example, and VNExpress
for the Vietnamese exam image. These labels document provenance claims; they
do not grant a license, imply endorsement, or replace verification against the
original publisher.

## Important accuracy notice

These artifacts are demonstrations, not ground-truth transcriptions. OCR and
provider Markdown can contain spelling, diacritic, symbol, formula, table, and
reading-order errors. A reconstruction can preserve those errors exactly.
Always compare the DOCX with its source image and perform qualified human review
before any consequential use.

The calculus sample renders as one A4 page with native Office Math and no
visible TeX alignment markers.

### Measured similarity, and what the number means

Re-measuring these committed artifacts against their source images with the
visual metric the project ships today (`VISUAL_METRIC_VERSION = 2.2`, LibreOffice
backend) gives:

| Showcase | Headline score | Pixel similarity | Foreground F1 | Edge | Region |
| --- | --- | --- | --- | --- | --- |
| calculus-derivation | 31.46% | 94.71% | 28.33% | 41.95% | 20.21% |
| math-exam | 30.80% | 86.23% | 24.53% | 44.90% | 22.24% |
| vietnamese-exam | 57.35% | 79.50% | 50.29% | 74.25% | 48.20% |

An earlier revision of this file quoted "92.58% foreground-normalized visual
similarity" for the calculus sample against "34/34 measured gates". Neither
figure reproduces: that artifact scores 31.46% under metric 2.2, and the gate
set has since grown to 39. The 92.58% is the shape of a *pixel* similarity, not
a foreground-normalized one — most of an A4 page is blank paper, so pixel
agreement is high even when glyph placement differs. Metric 2.2 deliberately
scores foreground agreement, edges and regions instead, which is why the
headline is roughly a third of the pixel figure. The project's own
OmniDocBench demo report records rendered visual scores of 0.07-0.36 on the same
family of metric, so this range is the expected one.

Read the headline score as "how much of the ink lands where the source put it",
not as a percentage of the page that looks right. None of this overrides the
content-authority rule: if OCR Markdown omits or misreads a source token, the
project records the discrepancy instead of silently adding text from the image.

## Rights and privacy

The Apache-2.0 license covers the project code. It does not automatically grant
rights to third-party exam content, logos, watermarks, handwriting, or other
material visible in these user-provided examples. Original rights remain with
their respective owners. Inclusion does not imply endorsement. Review source
rights and privacy before redistributing or reusing any showcase asset.

See [SHA256SUMS.txt](SHA256SUMS.txt) for exact artifact fingerprints.
