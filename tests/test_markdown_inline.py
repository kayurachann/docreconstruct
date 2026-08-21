from __future__ import annotations

from docx import Document as WordDocument
from docx.oxml.ns import qn

from docreconstruct.reconstruction.hybrid_docx import _add_rich_text
from docreconstruct.reconstruction.markdown_inline import (
    inline_math_expressions,
    parse_markdown_inline,
)


def test_bare_braced_script_is_a_lossless_inline_math_segment() -> None:
    source = "The work at time W_{t} remains authoritative."

    segments = parse_markdown_inline(source)

    assert "".join(segment.source for segment in segments) == source
    assert [segment.value for segment in segments if segment.is_math] == ["W_{t}"]
    assert [segment.value for segment in segments if not segment.is_math] == [
        "The work at time ",
        " remains authoritative.",
    ]


def test_literal_underscores_escapes_urls_paths_and_code_are_not_inferred() -> None:
    literals = [
        r"array_{index}",
        r"W\_{t}",
        "W_t",
        r"tensor_W_{t}",
        r"obj.W_{t}",
        r"https://example.test/W_{t}",
        r"www.example.test/W_{t}",
        r"reader+W_{t}@example.test",
        r"C:\tmp\W_{t}",
        r"\\server\share\W_{t}",
        r"`W_{t}`",
    ]

    for source in literals:
        segments = parse_markdown_inline(source)
        assert "".join(segment.source for segment in segments) == source
        assert not any(segment.is_math for segment in segments), source


def test_explicit_math_is_not_reparsed_and_allowlisted_commands_are_supported() -> None:
    source = r"Already $W_{t}$; ratio \frac{1}{2}; root \sqrt{x}."

    segments = parse_markdown_inline(source)

    assert "".join(segment.source for segment in segments) == source
    assert inline_math_expressions(source) == (r"W_{t}", r"\frac{1}{2}", r"\sqrt{x}")
    explicit = next(segment for segment in segments if segment.source == "$W_{t}$")
    assert explicit.value == "W_{t}"


def test_bare_script_renders_as_one_native_omml_expression() -> None:
    document = WordDocument()
    paragraph = document.add_paragraph()

    _add_rich_text(
        paragraph,
        "The work W_{t} keeps file_name literal.",
        size=11,
    )

    equations = paragraph._p.xpath(".//m:oMath")
    assert len(equations) == 1
    assert "".join(node.text or "" for node in equations[0].iter(qn("m:t"))) == "Wt"
    assert equations[0].find(qn("m:sSub")) is not None
    ordinary = "".join(node.text or "" for node in paragraph._p.iter(qn("w:t")))
    assert ordinary == "The work  keeps file_name literal."
    assert "W_{t}" not in ordinary


def test_currency_prose_is_not_read_as_inline_math() -> None:
    """A closing ``$`` followed by a digit is a second amount, not a delimiter.

    Without Pandoc's digit guard, "costs $5 and $10" matched ``$5 and $`` and
    both dollar signs disappeared from the rendered document.
    """

    for text in (
        "Total cost is $5 and $10 for shipping.",
        "The fee ($25) applies; the deposit is $100.",
        "Budget $5 vs $7 per unit.",
    ):
        segments = parse_markdown_inline(text)
        assert [segment.value for segment in segments if segment.is_math] == []
        assert "".join(segment.source for segment in segments) == text


def test_genuine_inline_math_still_parses() -> None:
    segments = parse_markdown_inline("Einstein wrote $E=mc^2$ in 1905.")

    assert [segment.value for segment in segments if segment.is_math] == ["E=mc^2"]
