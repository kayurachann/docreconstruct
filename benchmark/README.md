# Public benchmark evidence

This directory contains versioned evaluation protocols and unfiltered reports.
It is deliberately separate from `docs/showcases`: showcases explain individual
artifacts, while benchmarks must retain every selected case, including crashes,
rejections, and zero scores.

| Suite | Lane | Scope | Status |
| --- | --- | --- | --- |
| [OmniDocBench official demo](omnidocbench-demo/README.md) | Oracle reconstruction | All 18 official demo pages | Baseline published; 8/18 operational, 1/18 accepted |

The first published lane supplies ground-truth Markdown and ground-truth
geometry. It isolates reconstruction behavior and is **not** an OCR benchmark.
No number in this directory should be compared directly with an OmniDocBench
parser leaderboard number unless the input authority, dataset revision, and
metric implementation are identical.
