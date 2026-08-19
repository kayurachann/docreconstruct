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
visible TeX alignment markers. Its project QA run passed 34/34 measured gates
and reported 92.58% foreground-normalized visual similarity. That measurement
does not override the content-authority rule: if OCR Markdown omits or misreads
a source token, the project records the discrepancy instead of silently adding
text from the image.

## Rights and privacy

The Apache-2.0 license covers the project code. It does not automatically grant
rights to third-party exam content, logos, watermarks, handwriting, or other
material visible in these user-provided examples. Original rights remain with
their respective owners. Inclusion does not imply endorsement. Review source
rights and privacy before redistributing or reusing any showcase asset.

See [SHA256SUMS.txt](SHA256SUMS.txt) for exact artifact fingerprints.
