"""Independent expected-text derivation for editable Office mathematics.

Hybrid QA used to derive its expected visible math text from
``math_omml.latex_visible_text`` — the same parser that produced the artifact
under test.  A parser defect therefore moved the expectation and the artifact
together, and QA confirmed the defect instead of catching it: when unbraced
script arguments swallowed ``a^2+b^2=c^2`` into ``a2``, the expected text was
``a2`` too.

This module derives a second expectation through latex2mathml, an external
LaTeX implementation that shares no code with the OMML bridge, and compares
the two derivations.  A disagreement is an error signal in one of the paths.

The two notations legitimately differ in presentation detail — n-ary and
fence characters live in OMML attributes but in MathML text, ``\\sqrt[3]{x}``
orders its degree differently, alphabets like ``\\mathbb{R}`` are letters plus
a style in OMML but dedicated codepoints in MathML — so the comparison is a
normalized visible-character multiset: NFKC folding, glyph equivalences,
structural characters removed.  That keeps the check exact for the failure
class that motivated it (content silently swallowed, invented, or mismapped)
without encoding either renderer's layout conventions as truth.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Any
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict

from docreconstruct.reconstruction.math_omml import (
    latex_visible_text,
    unsupported_latex_commands,
)

try:  # The reference path must not share code with the bridge under test.
    import latex2mathml.converter as _latex2mathml
except ImportError:  # pragma: no cover - exercised on installs without the extra
    _latex2mathml = None  # type: ignore[assignment]

# Characters that are structural in one notation but textual in the other:
# OMML keeps n-ary operators and stretchy fences in attributes (``m:chr``,
# ``m:begChr``) while MathML writes them as ``<mo>`` text, and bar accents
# appear as drawn glyphs only in MathML.  Both sides drop them so neither
# notation's convention is treated as the expected one.
_STRUCTURAL_CHARACTERS = set(
    "()[]{}|‖⟨⟩⌊⌋⌈⌉"  # fences: visible text in MathML, m:dPr attributes in OMML
    "∑∏∫∮⋀⋁⋂⋃⨀⨁⨂"  # n-ary heads: m:naryPr/m:chr attributes in OMML
    "√"  # radical sign: structural in both, occasionally textual in MathML
    "―¯‾"  # bar/overline accent glyphs drawn by MathML renderers
    "̄̅"  # combining macron/overline forms of the same accents
)
_INVISIBLE_CHARACTERS = set(
    "⁡⁢⁣⁤"  # function application, invisible times/comma/plus
    "​﻿"
)
# Same mathematical symbol, different codepoint conventions between the paths.
_GLYPH_EQUIVALENCES = str.maketrans(
    {
        "−": "-",  # minus sign vs hyphen-minus
        "⋅": "·",  # dot operator vs middle dot
        "′": "'",  # prime vs apostrophe
        "″": "''",  # double prime
        "⁄": "/",  # fraction slash
        "∕": "/",  # division slash
        "∖": "\\",  # set minus
    }
)

_ENVIRONMENT_PATTERN = re.compile(
    r"^\s*\\begin\{(?P<environment>aligned|alignedat|gathered|split)\}"
    r"(?:\{[^{}]*\})?(?P<body>.*)\\end\{(?P=environment)\}\s*$",
    flags=re.DOTALL,
)


class MathCrossCheck(BaseModel):
    """One expression compared across the OMML bridge and the reference path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    latex: str
    status: str  # "agree" | "disagree" | "unanswered"
    reason: str | None = None
    bridge_visible_text: str | None = None
    reference_visible_text: str | None = None
    bridge_only_characters: str | None = None
    reference_only_characters: str | None = None


def independent_math_available() -> bool:
    """Report whether the latex2mathml reference path can answer at all."""

    return _latex2mathml is not None


def _reference_rows(latex: str) -> list[str]:
    """Split an aligned-style environment into convertible single rows.

    latex2mathml does not accept the display environments the OMML bridge
    supports, so QA owns an equivalent split: rows break at top-level ``\\\\``
    and alignment ``&`` markers are dropped at top level only.  A backslash
    escapes the following character, and markers inside a braced group are
    ordinary content.  This is comparison plumbing, not a reuse of the bridge
    under test.
    """

    match = _ENVIRONMENT_PATTERN.match(latex.strip())
    if match is None:
        return [latex.strip()]
    body = match.group("body")
    rows: list[str] = []
    current: list[str] = []
    depth = 0
    position = 0
    while position < len(body):
        character = body[position]
        if character == "\\" and position + 1 < len(body):
            if body[position + 1] == "\\" and depth == 0:
                rows.append("".join(current))
                current = []
                position += 2
                continue
            current.append(body[position : position + 2])
            position += 2
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth = max(0, depth - 1)
        elif character == "&" and depth == 0:
            position += 1
            continue
        current.append(character)
        position += 1
    rows.append("".join(current))
    return [row.strip() for row in rows if row.strip()]


def mathml_visible_text(latex: str) -> str | None:
    """Return the reference visible text, or None when it cannot answer."""

    if _latex2mathml is None:
        return None
    parts: list[str] = []
    for row in _reference_rows(latex):
        try:
            markup = _latex2mathml.convert(row)
            root = ElementTree.fromstring(markup)
        except Exception:  # latex2mathml raises library-specific parse errors
            return None
        parts.extend(node.text for node in root.iter() if node.text)
    return "".join(parts)


def comparable_math_characters(text: str) -> str:
    """Reduce visible math text to the normalized character multiset."""

    folded = unicodedata.normalize("NFKC", text).translate(_GLYPH_EQUIVALENCES)
    kept = [
        character
        for character in folded
        if not character.isspace()
        and character not in _STRUCTURAL_CHARACTERS
        and character not in _INVISIBLE_CHARACTERS
    ]
    return "".join(sorted(kept))


def cross_check_math(latex: str) -> MathCrossCheck:
    """Compare the OMML bridge and the reference derivation for one source."""

    if _latex2mathml is None:
        return MathCrossCheck(
            latex=latex,
            status="unanswered",
            reason="latex2mathml_unavailable",
        )
    # Vocabulary is judged per split row: on the raw source a row break
    # directly followed by a letter (``...=1\\y&=2...``) would read as an
    # unknown command ``\y`` and wrongly excuse the whole expression.
    unsupported = sorted(
        {command for row in _reference_rows(latex) for command in unsupported_latex_commands(row)}
    )
    if unsupported:
        # The bridge already names these through its own unsupported-command
        # gate; the two grammars diverge most exactly there, so a comparison
        # would measure vocabulary coverage, not correctness.
        return MathCrossCheck(
            latex=latex,
            status="unanswered",
            reason="unsupported_commands: " + ", ".join(unsupported),
        )
    reference = mathml_visible_text(latex)
    if reference is None:
        return MathCrossCheck(
            latex=latex,
            status="unanswered",
            reason="reference_conversion_error",
        )
    bridge = latex_visible_text(latex)
    bridge_characters = Counter(comparable_math_characters(bridge))
    reference_characters = Counter(comparable_math_characters(reference))
    if bridge_characters == reference_characters:
        return MathCrossCheck(
            latex=latex,
            status="agree",
            bridge_visible_text=bridge,
            reference_visible_text=reference,
        )
    return MathCrossCheck(
        latex=latex,
        status="disagree",
        bridge_visible_text=bridge,
        reference_visible_text=reference,
        bridge_only_characters="".join(
            sorted((bridge_characters - reference_characters).elements())
        ),
        reference_only_characters="".join(
            sorted((reference_characters - bridge_characters).elements())
        ),
    )


def cross_check_math_sources(expressions: Sequence[str]) -> dict[str, Any]:
    """Cross-check every source expression and summarize for a QA report."""

    checks = [cross_check_math(expression) for expression in expressions]
    disagreements = [check for check in checks if check.status == "disagree"]
    unanswered = [check for check in checks if check.status == "unanswered"]
    return {
        "available": independent_math_available(),
        "expressions": len(checks),
        "agreements": sum(check.status == "agree" for check in checks),
        "disagreements": [
            {
                "latex": check.latex,
                "bridge_visible_text": check.bridge_visible_text,
                "reference_visible_text": check.reference_visible_text,
                "bridge_only_characters": check.bridge_only_characters,
                "reference_only_characters": check.reference_only_characters,
            }
            for check in disagreements
        ],
        "unanswered": [{"latex": check.latex, "reason": check.reason} for check in unanswered],
    }


__all__ = [
    "MathCrossCheck",
    "comparable_math_characters",
    "cross_check_math",
    "cross_check_math_sources",
    "independent_math_available",
    "mathml_visible_text",
]
