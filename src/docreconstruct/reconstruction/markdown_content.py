"""Lossless structural parsing for Markdown used as a content authority.

Two front-ends produce the same block-token stream:

* ``markdown-it`` (the default when markdown-it-py is installed) delegates
  block segmentation — headings, fences, thematic breaks, HTML blocks,
  paragraphs, lists, block quotes — to a maintained CommonMark
  implementation.  Only segmentation is delegated: block text is always
  taken verbatim from the source lines, never from rendered inline HTML,
  so the content authority is preserved character for character.
* ``legacy`` is the project's original line scanner, kept selectable behind
  ``DOCRECONSTRUCT_MARKDOWN_FRONTEND=legacy`` while the two run in parallel.

Both front-ends share one domain-classification layer (display-math
splitting, exam options, group labels, heading heuristics, structural
roles), so a front-end choice can only move block boundaries, never rewrite
text.  ``scripts/diff_markdown_frontends.py`` diffs the two block streams
over a corpus.
"""

from __future__ import annotations

import html
import os
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.renderers._utils import rows_and_spans_from_html

try:  # Segmentation library; the legacy scanner remains the offline fallback.
    from markdown_it import MarkdownIt
except ImportError:  # pragma: no cover - exercised on installs without the wheel
    MarkdownIt = None  # type: ignore[assignment,misc]

MARKDOWN_FRONTEND_ENV = "DOCRECONSTRUCT_MARKDOWN_FRONTEND"


class MarkdownBlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    OPTION = "option"
    TABLE = "table"
    IMAGE = "image"
    CODE = "code"
    EQUATION = "equation"
    LIST_ITEM = "list_item"
    RULE = "rule"


class MarkdownBlock(BaseModel):
    """One ordered, editable content unit from the Markdown source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    index: int = Field(ge=0)
    kind: MarkdownBlockKind
    text: str = ""
    source: str | None = None
    level: int | None = Field(default=None, ge=1, le=6)
    group_id: str | None = None
    starts_group: bool = False
    table_rows: list[list[str]] = Field(default_factory=list)
    # Parallel to `table_rows`: each slot's (colspan, rowspan), with a
    # covered slot marked (0, 0).  Empty when the source has no spans.
    table_spans: list[list[tuple[int, int]]] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)


class MarkdownContent(BaseModel):
    """Normalized block stream that never changes the source wording."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    blocks: list[MarkdownBlock]

    @property
    def image_blocks(self) -> list[MarkdownBlock]:
        return [block for block in self.blocks if block.kind is MarkdownBlockKind.IMAGE]

    @property
    def table_blocks(self) -> list[MarkdownBlock]:
        return [block for block in self.blocks if block.kind is MarkdownBlockKind.TABLE]


_IMAGE_PATTERN = re.compile(r"^!\[([^]]*)\]\(([^)]+)\)\s*$")
_HTML_IMAGE_PATTERN = re.compile(r"<img\b(?P<attributes>[^>]*)/?>", re.IGNORECASE)
_HTML_CONTAINER_PATTERN = re.compile(
    r"^<(?P<tag>div|p|center)\b(?P<attributes>[^>]*)>"
    r"(?P<body>.*)</(?P=tag)>\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_HTML_ATTRIBUTE_PATTERN = re.compile(
    r"(?P<name>[A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*"
    r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    flags=re.DOTALL,
)
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*)$")
_GROUP_PATTERN = re.compile(
    r"^(?P<label>(?:câu|question|ques\.?|q\.?|bài|item)\s*[\w.-]+\s*[:.)])\s*",
    flags=re.IGNORECASE,
)
_NUMBERED_GROUP_PATTERN = re.compile(r"^(?P<label>\d{1,4}[.)])\s+")
_SOLUTION_GROUP_PATTERN = re.compile(r"^(?P<label>[A-Z]\s*[-‒-―]\s*\d+)\b")
_DISPLAY_MATH_PATTERN = re.compile(r"^\$\$\s*(.*?)\s*\$\$$", flags=re.DOTALL)
# Display math is a block construct even when prose surrounds it on the same
# line, so a paragraph carrying one is split around it rather than keeping the
# literal ``$$`` delimiters in its text.
_DISPLAY_MATH_SPAN = re.compile(
    r"(?<!\\)\$\$\s*(?P<body>(?:(?!\$\$).)+?)\s*(?<!\\)\$\$", flags=re.DOTALL
)
_THEMATIC_BREAK_PATTERN = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_LIST_ITEM_PATTERN = re.compile(r"^(?:[-*+]|\d{1,4}[.)])\s+\S")
_OPTION_START = re.compile(r"(?<!\S)(?=[A-D][.)]\s+)")
_SECTION_START_PATTERN = re.compile(
    r"^(?:phần|phan|part|section)\s+[\wIVXLCDM.-]+\s*[:.)]",
    flags=re.IGNORECASE,
)
_CAPITALIZED_GROUP_PATTERN = re.compile(r"\s+(?=(?:Câu|Question|Q\.?)\s*[\w.-]+\s*[:.)])")
_CJK_SECTION_PATTERN = re.compile(
    r"^(?:"
    r"第[〇零一二三四五六七八九十百千\d]+(?:章|節|节|部|篇|題|题|問|问|回)"
    r"|(?:方法|解法|例|例題|例题|定理|證明|证明|解答|步驟|步骤|問|问|問題|问题)"
    r"\s*(?:[〇零一二三四五六七八九十百千]+|\d+)"
    r")\s*[:：.]?$"
)


# The exact inverse of renderers.markdown._escape_block: a backslash that only
# exists to stop a line opening a block is not part of the author's text.
_ESCAPED_BLOCK_MARKER = re.compile(
    r"^(\s{0,3})\\(?=(?:#{1,6}(?:\s|$)|[-+*]\s|\d{1,4}[.)]\s|>|```|~~~"
    r"|(?:-{3,}|\*{3,}|_{3,}|={3,})\s*$))",
    flags=re.MULTILINE,
)

_TokenTuple = tuple[MarkdownBlockKind, str, dict[str, str | int]]


def _unescape_block_markers(value: str) -> str:
    return _ESCAPED_BLOCK_MARKER.sub(r"\1", value)


def _plain(value: str) -> str:
    # The block-marker escape is deliberately left in place here: block kinds
    # are decided from this string, and unescaping first would let ``\- item``
    # be classified as a list again. It is stripped once, on the finished block.
    return html.unescape(value.strip()).replace(" ", " ")


def _html_attributes(value: str) -> dict[str, str]:
    return {
        match.group("name").casefold(): html.unescape(match.group("value")).strip()
        for match in _HTML_ATTRIBUTE_PATTERN.finditer(value)
    }


def _html_alignment(*attribute_sets: str, tag: str | None = None) -> str | None:
    if tag and tag.casefold() == "center":
        return "center"
    joined = " ".join(attribute_sets)
    match = re.search(r"text-align\s*:\s*(left|center|right|justify)", joined, re.I)
    return match.group(1).casefold() if match else None


def _html_image(value: str) -> tuple[str, str, dict[str, str | int]] | None:
    match = _HTML_IMAGE_PATTERN.search(value)
    if match is None:
        return None
    remainder = value[: match.start()] + value[match.end() :]
    remainder = re.sub(r"</?(?:div|p|center)\b[^>]*>", "", remainder, flags=re.I)
    if remainder.strip():
        return None
    attributes = _html_attributes(match.group("attributes"))
    source = attributes.get("src", "").strip()
    if not source:
        return None
    wrapper = _HTML_CONTAINER_PATTERN.match(value)
    wrapper_attributes = wrapper.group("attributes") if wrapper else ""
    wrapper_tag = wrapper.group("tag") if wrapper else None
    metadata: dict[str, str | int] = {"source": source}
    alignment = _html_alignment(
        wrapper_attributes,
        attributes.get("style", ""),
        tag=wrapper_tag,
    )
    if alignment:
        metadata["alignment"] = alignment
    width = attributes.get("width", "")
    width_match = re.fullmatch(r"\s*(\d{1,3})%\s*", width)
    if width_match:
        metadata["width_percent"] = min(100, int(width_match.group(1)))
    return attributes.get("alt", ""), source, metadata


def _html_text_container(value: str) -> tuple[str, dict[str, str]] | None:
    match = _HTML_CONTAINER_PATTERN.match(value)
    if match is None or _HTML_IMAGE_PATTERN.search(match.group("body")):
        return None
    body = re.sub(r"<br\s*/?>", " ", match.group("body"), flags=re.I)
    body = re.sub(r"<[^>]+>", "", body)
    metadata: dict[str, str] = {}
    alignment = _html_alignment(
        match.group("attributes"),
        tag=match.group("tag"),
    )
    if alignment:
        metadata["alignment"] = alignment
    return _plain(body), metadata


def _looks_like_cjk_section(text: str) -> bool:
    """Recognize compact numbered CJK section labels without treating prose as headings."""

    return bool(_CJK_SECTION_PATTERN.fullmatch(text.strip()))


def _looks_like_heading(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 240 or _DISPLAY_MATH_PATTERN.match(stripped):
        return False
    letters = [character for character in stripped if character.isalpha()]
    uppercase_ratio = (
        sum(character.isupper() for character in letters) / len(letters) if letters else 0.0
    )
    section_word = bool(
        re.match(
            r"^(?:phần|phan|section|chapter|chương|part)\s+[\wIVXLCDM.-]+\s*[:.)]",
            stripped,
            flags=re.IGNORECASE,
        )
    )
    roman_prefix = stripped.split("(", 1)[0]
    roman_letters = [character for character in roman_prefix if character.isalpha()]
    roman_uppercase_ratio = (
        sum(character.isupper() for character in roman_letters) / len(roman_letters)
        if roman_letters
        else 0.0
    )
    roman_section = bool(
        re.match(r"^[IVXLCDM]+[.)]\s+", stripped, flags=re.IGNORECASE)
        and roman_uppercase_ratio >= 0.80
    )
    return (
        section_word
        or roman_section
        or _looks_like_cjk_section(stripped)
        or (uppercase_ratio >= 0.88 and len(letters) >= 5)
    )


def _structural_roles(blocks: list[MarkdownBlock]) -> list[MarkdownBlock]:
    """Annotate reusable document roles without changing source wording."""

    roles: dict[int, str] = {}
    for index, block in enumerate(blocks):
        if block.kind is MarkdownBlockKind.HEADING:
            roles[index] = (
                "section_heading"
                if re.match(
                    r"^(?:(?:phần|phan|section|part)\s+[\wIVXLCDM.-]+\s*[:.)]"
                    r"|(?:[IVXLCDM]+|\d+)[.)]\s+)",
                    block.text,
                    re.IGNORECASE,
                )
                or _looks_like_cjk_section(block.text)
                else "document_heading"
            )
        elif len(block.text) <= 90 and block.text.rstrip().endswith(":"):
            roles[index] = "lead_in"

    # A run of parenthesized numbered prose bracketed by a lead-in and a
    # parenthetical source note is a generic quoted-passage structure common to
    # exams, reports, and annotated editions.  Styling it does not alter text.
    cursor = 0
    while cursor < len(blocks):
        if not re.match(r"^\(\d+\)\s+", blocks[cursor].text):
            cursor += 1
            continue
        end = cursor + 1
        while end < len(blocks) and re.match(r"^\(\d+\)\s+", blocks[end].text):
            end += 1
        if end - cursor >= 3:
            for index in range(cursor, end):
                roles[index] = "quoted_passage"
            if cursor > 0 and blocks[cursor - 1].text.rstrip().endswith(":"):
                roles[cursor - 1] = "passage_lead"
            if (
                end < len(blocks)
                and blocks[end].text.startswith("(")
                and blocks[end].text.endswith(")")
            ):
                roles[end] = "attribution"
        cursor = end

    for index in range(len(blocks) - 1):
        if blocks[index + 1].starts_group and blocks[index].text.rstrip().endswith(":"):
            roles[index] = "question_lead"

    # Short trailing labels in a masthead are form fields.  This is based on
    # position and punctuation, not language-specific labels.
    first_section = next(
        (index for index, role in roles.items() if role == "section_heading"),
        None,
    )
    if first_section is not None:
        index = first_section - 1
        form_indices: list[int] = []
        while index >= 0:
            text = blocks[index].text.strip()
            if (
                blocks[index].kind is MarkdownBlockKind.PARAGRAPH
                and len(text) <= 48
                and text.endswith(":")
            ):
                form_indices.append(index)
                index -= 1
                continue
            break
        if len(form_indices) >= 2:
            for form_index in form_indices:
                roles[form_index] = "form_field"

    return [
        block.model_copy(update={"metadata": {**block.metadata, "role": roles[block.index]}})
        if block.index in roles
        else block
        for block in blocks
    ]


def _split_options(text: str) -> list[str]:
    """Split compound option lines without changing any option text."""

    pieces = [piece.strip() for piece in _OPTION_START.split(text) if piece.strip()]
    option_pieces = [piece for piece in pieces if re.match(r"^[A-Da-d][.)]\s+", piece)]
    if len(option_pieces) >= 2 and len(option_pieces) >= len(pieces) - 1:
        return pieces
    return [text]


def _group_label(text: str) -> str | None:
    match = (
        _GROUP_PATTERN.match(text)
        or _NUMBERED_GROUP_PATTERN.match(text)
        or _SOLUTION_GROUP_PATTERN.match(text)
    )
    return match.group("label").strip().casefold() if match else None


class _LineScanner:
    """The shared block machine: exact text in, classified block tokens out.

    The legacy front-end feeds it every source line; the markdown-it
    front-end feeds it the line slices that CommonMark segmentation
    delimits.  All domain classification — display-math splitting, option
    splitting, heading heuristics — lives here so the two front-ends can
    never disagree about what a piece of text means, only about where a
    block begins and ends.
    """

    def __init__(self) -> None:
        self.tokens: list[_TokenTuple] = []
        self._paragraph: list[str] = []
        self._list_indent: int | None = None
        self._in_code = False
        self._code_lines: list[str] = []

    def append_text(
        self,
        value: str,
        metadata: Mapping[str, str | int] | None = None,
    ) -> None:
        value = _plain(value)
        if not value:
            return
        display_math = _DISPLAY_MATH_PATTERN.match(value)
        if display_math:
            self.tokens.append(
                (
                    MarkdownBlockKind.EQUATION,
                    display_math.group(1).strip(),
                    dict(metadata or {}),
                )
            )
            return
        spans = list(_DISPLAY_MATH_SPAN.finditer(value))
        if spans:
            cursor = 0
            for span in spans:
                head = value[cursor : span.start()].strip()
                if head:
                    self.append_text(head, metadata)
                self.tokens.append(
                    (
                        MarkdownBlockKind.EQUATION,
                        span.group("body").strip(),
                        dict(metadata or {}),
                    )
                )
                cursor = span.end()
            tail = value[cursor:].strip()
            if tail:
                self.append_text(tail, metadata)
            return
        if _SECTION_START_PATTERN.match(value):
            split = _CAPITALIZED_GROUP_PATTERN.search(value)
            if split is not None:
                self.tokens.append(
                    (
                        MarkdownBlockKind.HEADING,
                        value[: split.start()].rstrip(),
                        dict(metadata or {}),
                    )
                )
                self.append_text(value[split.end() :], metadata)
                return
        for option in _split_options(value):
            kind = (
                MarkdownBlockKind.OPTION
                if re.match(r"^[A-Da-d][.)]\s+", option)
                else MarkdownBlockKind.LIST_ITEM
                if re.match(r"^[-*+]\s+", option)
                else MarkdownBlockKind.HEADING
                if _looks_like_heading(option)
                else MarkdownBlockKind.PARAGRAPH
            )
            self.tokens.append((kind, option, dict(metadata or {})))

    def flush_paragraph(self) -> None:
        self._list_indent = None
        if not self._paragraph:
            return
        value = " ".join(line.strip() for line in self._paragraph)
        self._paragraph.clear()
        self.append_text(value)

    def feed(self, raw_lines: list[str]) -> None:
        """Process source lines exactly as the original scanner did."""

        index = 0
        while index < len(raw_lines):
            line = raw_lines[index]
            stripped = line.strip()
            if stripped.startswith("```"):
                self.flush_paragraph()
                if self._in_code:
                    self.tokens.append((MarkdownBlockKind.CODE, "\n".join(self._code_lines), {}))
                    self._code_lines = []
                self._in_code = not self._in_code
                index += 1
                continue
            if self._in_code:
                self._code_lines.append(line)
                index += 1
                continue
            if not stripped:
                self.flush_paragraph()
                index += 1
                continue
            if _THEMATIC_BREAK_PATTERN.fullmatch(stripped):
                self.flush_paragraph()
                self.tokens.append((MarkdownBlockKind.RULE, "", {"syntax": stripped}))
                index += 1
                continue
            html_image = _html_image(stripped)
            if html_image is not None:
                self.flush_paragraph()
                alt, image_source, image_metadata = html_image
                self.tokens.append(
                    (
                        MarkdownBlockKind.IMAGE,
                        _plain(alt),
                        {**image_metadata, "source": image_source},
                    )
                )
                index += 1
                continue
            html_container = _html_text_container(stripped)
            if html_container is not None:
                self.flush_paragraph()
                value, container_metadata = html_container
                self.append_text(value, container_metadata)
                index += 1
                continue
            heading = _HEADING_PATTERN.match(stripped)
            if heading:
                self.flush_paragraph()
                self.tokens.append(
                    (
                        MarkdownBlockKind.HEADING,
                        _plain(heading.group(2)),
                        {"level": len(heading.group(1))},
                    )
                )
                index += 1
                continue
            image = _IMAGE_PATTERN.match(stripped)
            if image:
                self.flush_paragraph()
                self.tokens.append(
                    (
                        MarkdownBlockKind.IMAGE,
                        _plain(image.group(1)),
                        {"source": image.group(2).strip()},
                    )
                )
                index += 1
                continue
            if stripped.lower().startswith("<table"):
                self.flush_paragraph()
                table_lines = [stripped]
                while "</table>" not in table_lines[-1].lower() and index + 1 < len(raw_lines):
                    index += 1
                    table_lines.append(raw_lines[index].strip())
                self.tokens.append((MarkdownBlockKind.TABLE, "\n".join(table_lines), {}))
                index += 1
                continue
            # A list marker starts its own block.  Without this every item fell
            # through to the paragraph buffer, and `flush_paragraph` joined the
            # whole list into one run-on block whose kind was decided by the first
            # marker alone — four bullets became a single justified Word paragraph,
            # and consecutive numbered items could not start their own groups.
            indent = len(line) - len(line.lstrip())
            if _LIST_ITEM_PATTERN.match(stripped):
                self.flush_paragraph()
                self._list_indent = indent
            elif self._list_indent is not None and indent <= self._list_indent:
                # Only a more-indented line continues the open item; anything at or
                # left of the marker has left the list.
                self.flush_paragraph()
            self._paragraph.append(stripped)
            index += 1

    def finish(self) -> list[_TokenTuple]:
        self.flush_paragraph()
        if self._in_code and self._code_lines:
            self.tokens.append((MarkdownBlockKind.CODE, "\n".join(self._code_lines), {}))
            self._in_code = False
            self._code_lines = []
        return self.tokens


def _legacy_tokens(raw_lines: list[str]) -> list[_TokenTuple]:
    scanner = _LineScanner()
    scanner.feed(raw_lines)
    return scanner.finish()


def _matching_close(stream: list, index: int, open_type: str, close_type: str) -> int:
    depth = 0
    for position in range(index, len(stream)):
        token_type = stream[position].type
        if token_type == open_type:
            depth += 1
        elif token_type == close_type:
            depth -= 1
            if depth == 0:
                return position
    return len(stream) - 1


_LIST_OPEN_TYPES = {"bullet_list_open", "ordered_list_open"}


def _feed_html_lines(scanner: _LineScanner, raw_lines: list[str], start: int, stop: int) -> int:
    """Route an HTML block through the shared per-line handlers.

    Returns the first line index not consumed.  A ``<table>`` runs to its
    closing tag even when blank lines split it across CommonMark HTML
    blocks, matching the legacy scanner's whole-table consumption.
    """

    index = start
    while index < stop:
        stripped = raw_lines[index].strip()
        if not stripped:
            scanner.flush_paragraph()
            index += 1
            continue
        if stripped.lower().startswith("<table"):
            scanner.flush_paragraph()
            table_lines = [stripped]
            while "</table>" not in table_lines[-1].lower() and index + 1 < len(raw_lines):
                index += 1
                table_lines.append(raw_lines[index].strip())
            scanner.tokens.append((MarkdownBlockKind.TABLE, "\n".join(table_lines), {}))
            index += 1
            continue
        html_image = _html_image(stripped)
        if html_image is not None:
            scanner.flush_paragraph()
            alt, image_source, image_metadata = html_image
            scanner.tokens.append(
                (
                    MarkdownBlockKind.IMAGE,
                    _plain(alt),
                    {**image_metadata, "source": image_source},
                )
            )
            index += 1
            continue
        html_container = _html_text_container(stripped)
        if html_container is not None:
            scanner.flush_paragraph()
            value, container_metadata = html_container
            scanner.append_text(value, container_metadata)
            index += 1
            continue
        scanner._paragraph.append(stripped)
        index += 1
    scanner.flush_paragraph()
    return index


def _markdown_it_tokens(raw_lines: list[str]) -> list[_TokenTuple]:
    """Segment with CommonMark, classify with the shared line machine."""

    parser = MarkdownIt("commonmark")
    # Two deliberate divergences from pure CommonMark, both inherited from
    # the corpus this parser serves: a ``---`` line after prose is a
    # thematic break (not a setext heading), and four-space indentation is
    # OCR page layout (not an indented code block).
    parser.disable(["lheading", "code"])
    stream = parser.parse("\n".join(raw_lines))
    scanner = _LineScanner()
    consumed_line = 0
    index = 0
    while index < len(stream):
        token = stream[index]
        if token.map is not None and token.map[1] <= consumed_line:
            index += 1
            continue
        if token.type == "heading_open":
            line = raw_lines[token.map[0]].strip() if token.map is not None else ""
            heading = _HEADING_PATTERN.match(line)
            if heading is not None:
                scanner.tokens.append(
                    (
                        MarkdownBlockKind.HEADING,
                        _plain(heading.group(2)),
                        {"level": len(heading.group(1))},
                    )
                )
            index = _matching_close(stream, index, "heading_open", "heading_close")
        elif token.type == "fence":
            body = token.content[:-1] if token.content.endswith("\n") else token.content
            scanner.tokens.append((MarkdownBlockKind.CODE, body, {}))
        elif token.type == "hr":
            line = raw_lines[token.map[0]].strip() if token.map is not None else ""
            scanner.tokens.append((MarkdownBlockKind.RULE, "", {"syntax": line}))
        elif token.type == "html_block" and token.map is not None:
            consumed_line = _feed_html_lines(
                scanner,
                raw_lines,
                max(token.map[0], consumed_line),
                token.map[1],
            )
        elif token.type == "paragraph_open" and token.map is not None:
            # Paragraph, list, and quote extents come from CommonMark, but the
            # lines themselves run through the shared machine so per-line
            # semantics (image lines, list-marker splits, indentation rules)
            # cannot drift between the two front-ends.
            scanner.feed(raw_lines[max(token.map[0], consumed_line) : token.map[1]])
            scanner.flush_paragraph()
            index = _matching_close(stream, index, "paragraph_open", "paragraph_close")
        elif token.type in _LIST_OPEN_TYPES and token.map is not None:
            scanner.feed(raw_lines[max(token.map[0], consumed_line) : token.map[1]])
            scanner.flush_paragraph()
            index = _matching_close(
                stream,
                index,
                token.type,
                token.type.replace("_open", "_close"),
            )
        elif token.type == "blockquote_open" and token.map is not None:
            scanner.feed(raw_lines[max(token.map[0], consumed_line) : token.map[1]])
            scanner.flush_paragraph()
            index = _matching_close(stream, index, "blockquote_open", "blockquote_close")
        index += 1
    return scanner.finish()


def _resolve_frontend(requested: str | None) -> str:
    value = (requested or os.environ.get(MARKDOWN_FRONTEND_ENV, "")).strip().casefold()
    if not value:
        return "markdown-it" if MarkdownIt is not None else "legacy"
    if value in {"markdown-it", "markdown_it", "markdownit"}:
        if MarkdownIt is None:
            raise ValueError(
                "the markdown-it front-end was requested but markdown-it-py is not installed"
            )
        return "markdown-it"
    if value == "legacy":
        return "legacy"
    raise ValueError(f"unknown Markdown front-end {value!r}; use 'markdown-it' or 'legacy'")


def parse_markdown_content(
    source: str | Path,
    *,
    frontend: str | None = None,
) -> MarkdownContent:
    """Parse Markdown locally while retaining exact text, HTML tables, and assets.

    ``frontend`` selects the block segmenter: ``"markdown-it"`` (default when
    markdown-it-py is installed), or ``"legacy"`` for the original line
    scanner.  The ``DOCRECONSTRUCT_MARKDOWN_FRONTEND`` environment variable
    selects it process-wide; an explicit argument wins.
    """

    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    # ``utf-8-sig`` drops a leading BOM if present; a BOM glued to the
    # first "#" turns the document title into an ordinary paragraph.
    raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    if _resolve_frontend(frontend) == "markdown-it":
        tokens = _markdown_it_tokens(raw_lines)
    else:
        tokens = _legacy_tokens(raw_lines)

    blocks: list[MarkdownBlock] = []
    current_group: str | None = None
    section = 0
    for block_index, (kind, text, metadata) in enumerate(tokens):
        if kind is MarkdownBlockKind.HEADING:
            section += 1
            current_group = None
        label = (
            _group_label(text)
            if kind in {MarkdownBlockKind.PARAGRAPH, MarkdownBlockKind.HEADING}
            else None
        )
        starts_group = label is not None
        if starts_group:
            current_group = f"section-{section}:{label}"
        source_reference = str(metadata.get("source")) if "source" in metadata else None
        level = int(metadata.get("level", 1)) if kind is MarkdownBlockKind.HEADING else None
        rows, spans = (
            rows_and_spans_from_html(text) if kind is MarkdownBlockKind.TABLE else ([], [])
        )
        block_metadata = {
            "section": section,
            **{key: value for key, value in metadata.items() if key not in {"source", "level"}},
        }
        blocks.append(
            MarkdownBlock(
                id=f"md-{block_index + 1}",
                index=block_index,
                kind=kind,
                text=""
                if kind in {MarkdownBlockKind.IMAGE, MarkdownBlockKind.TABLE}
                else _unescape_block_markers(text),
                source=source_reference,
                level=level,
                group_id=current_group,
                starts_group=starts_group,
                table_rows=rows,
                table_spans=spans,
                metadata=block_metadata,
            )
        )
    if not blocks:
        raise ValueError(f"Markdown source contains no content blocks: {path}")
    return MarkdownContent(source=str(path), blocks=_structural_roles(blocks))


__all__ = [
    "MARKDOWN_FRONTEND_ENV",
    "MarkdownBlock",
    "MarkdownBlockKind",
    "MarkdownContent",
    "parse_markdown_content",
]
