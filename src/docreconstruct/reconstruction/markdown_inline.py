"""Conservative mixed prose/TeX parsing for authoritative Markdown text.

OCR exports occasionally omit Markdown's ``$...$`` delimiters around a small
inline formula.  This module recognizes only unambiguous TeX-shaped fragments;
everything else remains literal text.  In particular, ordinary underscores,
escaped underscores, code spans, URLs, email addresses, and filesystem paths
are protected from inference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarkdownInlineSegment:
    """One exact source slice and its renderable text or TeX payload."""

    source: str
    value: str
    is_math: bool = False


_GREEK_IDENTIFIER = r"\u0370-\u03ff"
_TEX_IDENTIFIER = (
    r"\\(?:alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|"
    r"iota|kappa|lambda|mu|nu|xi|pi|varpi|rho|varrho|sigma|varsigma|tau|"
    r"upsilon|phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|"
    r"Upsilon|Phi|Psi|Omega)"
)
_MATH_IDENTIFIER = rf"(?:[A-Za-z{_GREEK_IDENTIFIER}]|{_TEX_IDENTIFIER})"
_BRACED_SCRIPT = r"(?:[_^]\{[^{}\s]{1,64}\})"
_SIMPLE_GROUP = r"\{[^{}\r\n]{1,64}\}"
_KNOWN_BRACED_COMMAND = (
    rf"(?:\\frac{_SIMPLE_GROUP}{_SIMPLE_GROUP}"
    rf"|\\sqrt(?:\[[1-9][0-9]?\])?{_SIMPLE_GROUP}"
    rf"|\\(?:bar|overline|vec|hat|tilde|mathbb|mathrm|mathbf){_SIMPLE_GROUP})"
)

# The boundary deliberately excludes identifier, URL/path, attribute-access,
# and escape characters.  A bare fragment must therefore be a standalone math
# atom such as W_{t}, not the tail of ``array_{index}`` or ``obj.W_{t}``.
_BARE_MATH_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_{_GREEK_IDENTIFIER}\\/:.])"
    rf"(?P<math>(?:{_MATH_IDENTIFIER}{_BRACED_SCRIPT}+"
    rf"|{_KNOWN_BRACED_COMMAND}{_BRACED_SCRIPT}*))"
    r"(?![A-Za-z0-9_\\/:@])"
)

# Explicit Markdown/HTML math is recognized before protected literal tokens.
# The latter are consumed as whole slices, so a TeX-looking substring inside
# one of them can never be promoted to math by the bare-fragment pass.
_SPECIAL_PATTERN = re.compile(
    # The trailing digit guard is Pandoc's: a closing "$" immediately followed
    # by a digit is a second currency amount, not the end of a math span, so
    # "costs $5 and $10" keeps both dollar signs instead of turning "5 and "
    # into an equation.
    r"(?P<dollar>(?<!\\)\$(?P<dollar_body>[^$\r\n]+?)(?<!\\)\$(?![0-9]))"
    r"|(?P<eq><eq>(?P<eq_body>[^\r\n]*?)</eq>)"
    # A code span opens with a backtick run that is not itself preceded by a
    # backtick, and that run is maximal, so the fence never has to backtrack to
    # a shorter length. Stating both collapses a quadratic scan over a long
    # backtick run: 8000 backticks went from 68s to under a millisecond.
    r"|(?P<code>(?<!`)(?P<fence>`++)[^\r\n]*?(?P=fence))"
    r"|(?P<url>(?:(?:https?|ftp)://|www\.)[^\s<>()]+)"
    r"|(?P<email>[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
    r"|(?P<windows_path>(?:(?:[A-Za-z]:\\)|(?:\\\\))\S+)",
    flags=re.IGNORECASE,
)


def _append(
    segments: list[MarkdownInlineSegment],
    *,
    source: str,
    value: str,
    is_math: bool,
) -> None:
    if not source:
        return
    if not is_math and segments and not segments[-1].is_math:
        previous = segments[-1]
        segments[-1] = MarkdownInlineSegment(
            source=previous.source + source,
            value=previous.value + value,
        )
        return
    segments.append(MarkdownInlineSegment(source=source, value=value, is_math=is_math))


def _append_plain_with_bare_math(
    segments: list[MarkdownInlineSegment],
    value: str,
) -> None:
    cursor = 0
    for match in _BARE_MATH_PATTERN.finditer(value):
        _append(
            segments,
            source=value[cursor : match.start()],
            value=value[cursor : match.start()],
            is_math=False,
        )
        expression = match.group("math")
        _append(
            segments,
            source=expression,
            value=expression,
            is_math=True,
        )
        cursor = match.end()
    _append(
        segments,
        source=value[cursor:],
        value=value[cursor:],
        is_math=False,
    )


def parse_markdown_inline(value: str) -> tuple[MarkdownInlineSegment, ...]:
    """Split mixed Markdown prose without changing any source character.

    Explicit ``$...$`` and ``<eq>...</eq>`` spans retain their established
    meaning.  Outside those spans, only a standalone math identifier with a
    braced sub/superscript, or a short allow-listed TeX command with required
    braces, is inferred as math.
    """

    segments: list[MarkdownInlineSegment] = []
    cursor = 0
    for match in _SPECIAL_PATTERN.finditer(value):
        _append_plain_with_bare_math(segments, value[cursor : match.start()])
        source = match.group(0)
        if match.group("dollar") is not None:
            _append(
                segments,
                source=source,
                value=match.group("dollar_body"),
                is_math=True,
            )
        elif match.group("eq") is not None:
            _append(
                segments,
                source=source,
                value=match.group("eq_body"),
                is_math=True,
            )
        else:
            _append(segments, source=source, value=source, is_math=False)
        cursor = match.end()
    _append_plain_with_bare_math(segments, value[cursor:])
    return tuple(segments)


def inline_math_expressions(value: str) -> tuple[str, ...]:
    """Return TeX payloads in reading order using the conservative parser."""

    return tuple(segment.value for segment in parse_markdown_inline(value) if segment.is_math)


__all__ = ["MarkdownInlineSegment", "inline_math_expressions", "parse_markdown_inline"]
