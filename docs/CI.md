# Pull-request quality gates

Pull-request CI is deliberately separate from the manual, failure-inclusive
OmniDocBench workflow. A pull request never downloads or evaluates the full
296-page or 1,651-page corpus.

The workflow has three parallel lanes:

1. Static contracts run Ruff, the Ruff formatter, mypy, portable JSON Schema
   validation, runtime-model/schema parity, committed DOCX package validation,
   and both distribution builds on Ubuntu with Python 3.11.
2. The complete test suite runs once on Ubuntu with Python 3.11. This includes
   adversarial and Hypothesis property tests and records line plus branch
   coverage.
3. Focused compatibility smoke tests exercise the IR, pipeline, evaluator, and
   DOCX path on Ubuntu with Python 3.11–3.13 and on Windows and macOS with
   Python 3.11.

All third-party GitHub Actions are pinned to complete commit hashes. The
checkout step also disables persisted Git credentials.

## Coverage ratchets

Coverage is failure-sensitive and scope-specific. The initial enforceable
floors reflect measured repository coverage rather than an unsupported claim:

| Scope | Branch-aware floor |
| --- | ---: |
| Whole project | 75% |
| Quality-critical evaluator and evidence matcher | 85% |
| Evidence fusion | 80% |
| DOCX rendering and validation | 78% |

These are regression floors, not quality targets. They may only move upward.
The target for critical evaluator, matcher, and fusion code remains at least
95%, and the target for the broader core remains at least 85%. Reaching those
targets requires tests that exercise meaningful failure behavior; excluding
files or adding no-op tests does not satisfy the contract.

The manual source-only benchmark remains responsible for real raw-page
comparisons, official OmniDocBench evaluation, and retaining timeout, OOM,
crash, and invalid-output cases in the denominator.
