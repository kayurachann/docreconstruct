"""Contracts for the independent math expectation path.

The point of this suite is the broken self-reference loop: hybrid QA's
expected math text used to come from the exact parser under test, so the
``a^2+b^2=c^2`` swallow shipped with a passing QA report.  Here the
expectation comes from latex2mathml and every case that class of bug touched
must (a) agree across the two paths today and (b) demonstrably disagree if
the bridge ever swallows content again.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from docreconstruct.evaluation.math_reference import (
    comparable_math_characters,
    cross_check_math,
    cross_check_math_sources,
    independent_math_available,
    mathml_visible_text,
)
from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlockKind,
    parse_markdown_content,
)
from docreconstruct.reconstruction.markdown_inline import inline_math_expressions

pytestmark = pytest.mark.skipif(
    not independent_math_available(),
    reason="latex2mathml is not installed; the reference path reports unmeasured",
)

_SHOWCASES = Path(__file__).resolve().parent.parent / "docs" / "showcases"

# Every expression here sits inside the grammar both paths support, so the
# two independently derived visible texts must agree.  The first entry is the
# exact historical defect: unbraced script arguments swallowed the rest of
# the expression and self-referential QA reported the loss as expected.
_AGREEMENT_CORPUS = [
    r"a^2+b^2=c^2",
    r"x^\alpha",
    r"x_i^2",
    r"\frac{a}{b}",
    r"\frac12",
    r"\frac{\sin x}{x}",
    r"\sqrt{x}",
    r"\sqrt[3]{x}",  # MathML orders the degree after the radicand
    r"\sum_{i=1}^{n} i^2",
    r"\prod_{k=1}^{n} k",
    r"\int_0^1 x\,dx",
    r"\oint_C f",
    r"\left(\frac{1}{2}\right)",  # fences are attributes in OMML, text in MathML
    r"\lim_{x\to 0}\frac{\sin x}{x}",
    r"\begin{aligned}x&=1\\y&=2\end{aligned}",
    r"\mathbb{R}",  # letter plus style in OMML, dedicated codepoint in MathML
    r"f'(x)",  # apostrophe vs prime
    r"2\cdot 3",  # middle dot vs dot operator
    r"A(1;3;-5)",  # hyphen-minus vs minus sign
    r"\text{abc def}",  # ordinary space vs no-break space
    r"\operatorname{argmax}",
    r"\overline{AB}",  # bar is structure in OMML, a drawn glyph in MathML
    r"\alpha+\beta\le\gamma",
    r"e^x=1+x",
]


@pytest.mark.parametrize("latex", _AGREEMENT_CORPUS)
def test_supported_grammar_agrees_across_the_two_paths(latex: str) -> None:
    check = cross_check_math(latex)

    assert check.status == "agree", (
        f"{latex!r}: bridge={check.bridge_visible_text!r} "
        f"reference={check.reference_visible_text!r} "
        f"bridge_only={check.bridge_only_characters!r} "
        f"reference_only={check.reference_only_characters!r} "
        f"reason={check.reason!r}"
    )


def test_swallowed_script_content_would_disagree() -> None:
    """The historical ``a^2+b^2=c^2`` -> ``a2`` output must be detectable.

    The reference path answers from latex2mathml, so if the bridge ever
    regresses to emitting ``a2`` the multiset comparison flags exactly the
    characters that went missing instead of agreeing with the loss.
    """

    reference = mathml_visible_text(r"a^2+b^2=c^2")
    assert reference is not None

    regressed = Counter(comparable_math_characters("a2"))
    expected = Counter(comparable_math_characters(reference))

    assert regressed != expected
    assert "".join(sorted((expected - regressed).elements())) == "+22=bc"


def test_mismapped_symbol_would_disagree() -> None:
    assert comparable_math_characters("α") != comparable_math_characters("β")


def test_unsupported_commands_are_unanswered_not_compared() -> None:
    check = cross_check_math(r"\vec{a}+\foo{x}")

    assert check.status == "unanswered"
    assert check.reason is not None and check.reason.startswith("unsupported_commands")
    assert "foo" in check.reason and "vec" in check.reason


def test_reference_conversion_failure_is_unanswered_not_a_crash() -> None:
    # The bridge tolerates a dangling script; latex2mathml rejects it.  The
    # cross-check must degrade to "unanswered", never abort validation.
    check = cross_check_math("a^")

    assert check.status in {"unanswered", "agree"}
    if check.status == "unanswered":
        assert check.reason == "reference_conversion_error"


def test_summary_counts_and_shapes() -> None:
    summary = cross_check_math_sources([r"a^2+b^2=c^2", r"\vec{a}"])

    assert summary["available"] is True
    assert summary["expressions"] == 2
    assert summary["agreements"] == 1
    assert summary["disagreements"] == []
    assert summary["unanswered"][0]["latex"] == r"\vec{a}"


def test_every_showcase_expression_is_agreed_or_honestly_unanswered() -> None:
    """The committed showcase corpus is the regression bed for this gate.

    A disagreement on an expression both grammars support means one of the
    two derivations is wrong; the showcases are exactly the artifacts whose
    QA numbers the README publishes, so they may never ship with one.
    """

    expressions: list[str] = []
    for case in ("calculus-derivation", "math-exam", "vietnamese-exam"):
        content = parse_markdown_content(_SHOWCASES / case / "content.md")
        for block in content.blocks:
            if block.kind is MarkdownBlockKind.EQUATION:
                expressions.append(block.text)
            elif block.kind is not MarkdownBlockKind.IMAGE:
                expressions.extend(inline_math_expressions(block.text))
    assert len(expressions) >= 15

    summary = cross_check_math_sources(expressions)

    assert summary["disagreements"] == [], summary["disagreements"]
    assert summary["agreements"] >= 1
