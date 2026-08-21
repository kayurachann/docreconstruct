"""Parity contracts between the markdown-it and legacy Markdown front-ends.

The markdown-it front-end delegates block segmentation to CommonMark while
the legacy line scanner stays selectable behind
``DOCRECONSTRUCT_MARKDOWN_FRONTEND=legacy``.  Both share one classification
layer, so these tests pin the remaining degree of freedom: block
boundaries.  The corpus deliberately includes every Markdown defect the
legacy parser historically shipped — the swallowed list branch, display
``$$`` inside prose, the UTF-8 BOM demoting a title, currency dollars, and
quadratic backtick scans arrived one bug report at a time, which is exactly
why segmentation now belongs to a maintained library.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from docreconstruct.reconstruction.markdown_content import (
    MARKDOWN_FRONTEND_ENV,
    parse_markdown_content,
)

pytest.importorskip("markdown_it")

_SHOWCASES = Path(__file__).resolve().parent.parent / "docs" / "showcases"

# One entry per historically shipped or structurally risky construct.
_PARITY_CORPUS: dict[str, str] = {
    "bom_title": "﻿# Title\n\nBody paragraph.\n",
    "display_math_inside_prose": (
        "The derivation $$ \\frac{a}{b} $$ continues in the same line.\n\n"
        "$$ x^2 $$\n\n"
        "Two spans $$a$$ then $$b$$ in one paragraph.\n"
    ),
    "currency_stays_text": "It costs $5 and $10 at most.\n",
    "list_branches": (
        "Intro line:\n\n"
        "- first bullet\n"
        "- second bullet\n"
        "* star bullet\n\n"
        "1. numbered one\n"
        "2) numbered two\n\n"
        "A. option one B. option two C. option three D. option four\n"
    ),
    "list_item_continuation": ("- item head\n  continuation stays with the item\nleft the list\n"),
    "fenced_code_keeps_markers": (
        "```\n- not a list\n# not a heading\n$$ not math $$\n```\n\nAfter the fence.\n"
    ),
    "unterminated_fence": "```\ncode until the end\n",
    "thematic_break_not_setext": "Some prose\n---\nMore prose\n",
    "html_image_and_container": (
        '<img src="figure.png" alt="A figure" width="50%">\n\n'
        '<div style="text-align:center">Centered label</div>\n\n'
        "<center>Old-style center</center>\n"
    ),
    "html_table": (
        "<table>\n<tr><td>a</td><td>b</td></tr>\n<tr><td>c</td><td>d</td></tr>\n</table>\n\n"
        "After the table.\n"
    ),
    "html_table_with_blank_lines": (
        "<table>\n<tr><td>a</td></tr>\n\n<tr><td>b</td></tr>\n</table>\n\nAfter.\n"
    ),
    "exam_groups": (
        "PHẦN I. Câu trắc nghiệm nhiều phương án lựa chọn.\n\n"
        "Câu 1: Nội dung câu hỏi thứ nhất?\n\n"
        "A. một. B. hai. C. ba. D. bốn.\n\n"
        "Câu 2: Nội dung câu hỏi thứ hai?\n"
    ),
    "markdown_image": "![alt text](assets/picture.png)\n",
    "escaped_block_markers": "\\- not a list item\n\n\\# not a heading\n",
    "blockquote_stays_prose": "> quoted line one\n> quoted line two\n",
    "cjk_section": "方法二\n\n第1章\n",
}


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.md"
    path.write_text(body, encoding="utf-8")
    return path


def _dumps(path: Path, frontend: str) -> list[dict[str, object]]:
    return [block.model_dump() for block in parse_markdown_content(path, frontend=frontend).blocks]


@pytest.mark.parametrize("name", sorted(_PARITY_CORPUS))
def test_both_frontends_agree_on_the_defect_corpus(tmp_path: Path, name: str) -> None:
    path = _write(tmp_path, name, _PARITY_CORPUS[name])

    assert _dumps(path, "markdown-it") == _dumps(path, "legacy")


@pytest.mark.parametrize("case", ["calculus-derivation", "math-exam", "vietnamese-exam"])
def test_both_frontends_agree_on_every_committed_showcase(case: str) -> None:
    path = _SHOWCASES / case / "content.md"

    assert _dumps(path, "markdown-it") == _dumps(path, "legacy")


def test_extra_parity_corpus_from_environment() -> None:
    """Real, non-committable documents join the parity bed via an env var.

    ``DOCRECONSTRUCT_PARITY_CORPUS`` holds ``os.pathsep``-separated Markdown
    files (for example a real exam export); each must parse identically
    through both front-ends.
    """

    configured = os.environ.get("DOCRECONSTRUCT_PARITY_CORPUS", "")
    paths = [Path(part) for part in configured.split(os.pathsep) if part.strip()]
    existing = [path for path in paths if path.is_file()]
    if not existing:
        pytest.skip("DOCRECONSTRUCT_PARITY_CORPUS names no existing files")
    for path in existing:
        assert _dumps(path, "markdown-it") == _dumps(path, "legacy"), str(path)


def test_default_frontend_is_markdown_it_when_installed(tmp_path: Path) -> None:
    path = _write(tmp_path, "default", "# Title\n\nBody.\n")

    assert [block.model_dump() for block in parse_markdown_content(path).blocks] == _dumps(
        path, "markdown-it"
    )


def test_environment_variable_selects_the_legacy_frontend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write(tmp_path, "env-legacy", "# Title\n\nBody.\n")
    monkeypatch.setenv(MARKDOWN_FRONTEND_ENV, "legacy")

    assert [block.model_dump() for block in parse_markdown_content(path).blocks] == _dumps(
        path, "legacy"
    )


def test_explicit_argument_overrides_the_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write(tmp_path, "override", "# Title\n\nBody.\n")
    monkeypatch.setenv(MARKDOWN_FRONTEND_ENV, "legacy")

    parsed = parse_markdown_content(path, frontend="markdown-it")

    assert [block.model_dump() for block in parsed.blocks] == _dumps(path, "markdown-it")


def test_unknown_frontend_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "unknown", "Body.\n")

    with pytest.raises(ValueError, match="unknown Markdown front-end"):
        parse_markdown_content(path, frontend="pandoc")
