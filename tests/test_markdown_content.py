from __future__ import annotations

from pathlib import Path

import pytest

from docreconstruct.reconstruction.markdown_content import (
    MarkdownBlockKind,
    parse_markdown_content,
)


def _blocks(tmp_path: Path, body: str, *, encoding: str = "utf-8") -> list[tuple[str, str]]:
    source = tmp_path / "reviewed.md"
    source.write_text(body, encoding=encoding)
    return [(block.kind.value, block.text) for block in parse_markdown_content(source).blocks]


def test_utf8_bom_does_not_demote_the_document_title(tmp_path: Path) -> None:
    """A BOM glued to the leading ``#`` used to hide the heading marker.

    Windows editors and many OCR exports write UTF-8 with a BOM, so the
    reviewed Markdown lost its title and every downstream structure check
    compared against a document whose first block was a paragraph.
    """

    with_bom = _blocks(tmp_path, "﻿# Quarterly Report\n\nOpening paragraph.\n")

    assert with_bom[0] == (MarkdownBlockKind.HEADING.value, "Quarterly Report")
    assert with_bom == _blocks(tmp_path, "# Quarterly Report\n\nOpening paragraph.\n")


def test_bom_is_stripped_before_the_heading_text(tmp_path: Path) -> None:
    blocks = _blocks(tmp_path, "﻿Plain opening line.\n")

    assert blocks == [(MarkdownBlockKind.PARAGRAPH.value, "Plain opening line.")]


def test_display_math_surrounded_by_prose_becomes_an_equation(tmp_path: Path) -> None:
    """``$$...$$`` was only recognized when it was the whole paragraph.

    Anywhere else the literal delimiters survived into the reconstructed
    document as ordinary text.
    """

    assert _blocks(tmp_path, "The identity is $$E = mc^2$$ and it is famous.\n") == [
        (MarkdownBlockKind.PARAGRAPH.value, "The identity is"),
        (MarkdownBlockKind.EQUATION.value, "E = mc^2"),
        (MarkdownBlockKind.PARAGRAPH.value, "and it is famous."),
    ]

    assert _blocks(tmp_path, "$$E = mc^2$$ closes the section.\n") == [
        (MarkdownBlockKind.EQUATION.value, "E = mc^2"),
        (MarkdownBlockKind.PARAGRAPH.value, "closes the section."),
    ]

    assert _blocks(tmp_path, "First $$a+b$$ then $$c+d$$ done.\n") == [
        (MarkdownBlockKind.PARAGRAPH.value, "First"),
        (MarkdownBlockKind.EQUATION.value, "a+b"),
        (MarkdownBlockKind.PARAGRAPH.value, "then"),
        (MarkdownBlockKind.EQUATION.value, "c+d"),
        (MarkdownBlockKind.PARAGRAPH.value, "done."),
    ]


def test_whole_paragraph_display_math_is_unchanged(tmp_path: Path) -> None:
    assert _blocks(tmp_path, "$$E = mc^2$$\n") == [(MarkdownBlockKind.EQUATION.value, "E = mc^2")]


def test_empty_and_escaped_dollar_pairs_stay_text(tmp_path: Path) -> None:
    assert _blocks(tmp_path, "Nothing here $$$$ at all.\n") == [
        (MarkdownBlockKind.PARAGRAPH.value, "Nothing here $$$$ at all.")
    ]
    assert _blocks(tmp_path, r"A literal \$\$ pair stays text." + "\n") == [
        (MarkdownBlockKind.PARAGRAPH.value, r"A literal \$\$ pair stays text.")
    ]


@pytest.mark.parametrize(
    "text",
    [
        "# not a heading, just prose",
        "- not a list item",
        "* not a bullet",
        "1. not an ordered item",
        "2) not an item either",
        "> not a quote",
        "``` not a code fence",
        "~~~ not a fence",
        "---",
        "***",
        "___",
        "===",
        "###### deep",
        "#hashtag",
        "ordinary sentence",
        "use *stars* inline",
    ],
)
def test_paragraph_text_round_trips_without_becoming_structure(tmp_path: Path, text: str) -> None:
    """The Markdown renderer emitted element text verbatim.

    A paragraph reading "# Section 3" came back a heading, and one reading
    "---" came back a thematic rule with no text at all — the content was
    simply gone. The renderer now escapes a line that would open a block and
    the parser strips that escape once the block kind is settled.
    """

    from docreconstruct.ir import BBox, Document, Element, ElementType, Page
    from docreconstruct.renderers import MarkdownRenderer

    document = Document(
        id="round-trip",
        pages=[
            Page(
                id="page-1",
                number=1,
                width=600,
                height=800,
                elements=[
                    Element(
                        id="e1",
                        type=ElementType.TEXT,
                        bbox=BBox(x0=0, y0=0, x1=100, y1=20),
                        text=text,
                    )
                ],
            )
        ],
    )

    source = tmp_path / "rendered.md"
    source.write_text(MarkdownRenderer().render(document), encoding="utf-8")
    blocks = parse_markdown_content(source).blocks

    assert [block.kind.value for block in blocks] == [MarkdownBlockKind.PARAGRAPH.value]
    assert blocks[0].text == text.strip()


def test_genuine_markdown_structure_is_still_parsed(tmp_path: Path) -> None:
    """The escape must not make the parser blind to real structure."""

    source = tmp_path / "real.md"
    source.write_text("# Real heading\n\n- real item\n\n---\n", encoding="utf-8")

    kinds = [block.kind.value for block in parse_markdown_content(source).blocks]

    assert MarkdownBlockKind.HEADING.value in kinds
    assert MarkdownBlockKind.LIST_ITEM.value in kinds
