"""Small dependency-free LaTeX-to-OMML bridge for editable Word mathematics.

The content authority remains the original LaTeX string.  This module only
maps presentation commands to Office Math objects; it never evaluates or
algebraically rewrites an expression.
"""

from __future__ import annotations

import re
from typing import Any

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

_SYMBOLS = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "theta": "θ",
    "lambda": "λ",
    "mu": "μ",
    "pi": "π",
    "phi": "ϕ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
    "angle": "∠",
    "cdot": "·",
    "times": "×",
    "div": "÷",
    "pm": "±",
    "mp": "∓",
    "neq": "≠",
    "ne": "≠",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "approx": "≈",
    "equiv": "≡",
    "in": "∈",
    "notin": "∉",
    "subset": "⊂",
    "subseteq": "⊆",
    "cup": "∪",
    "cap": "∩",
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "leftrightarrow": "↔",
    "infty": "∞",
    "sum": "∑",
    "prod": "∏",
    "int": "∫",
    "oint": "∮",
    "partial": "∂",
    "nabla": "∇",
    "ldots": "…",
    "cdots": "⋯",
    "vdots": "⋮",
    "prime": "′",
    "lfloor": "⌊",
    "rfloor": "⌋",
    "lceil": "⌈",
    "rceil": "⌉",
    "vert": "|",
    "Vert": "‖",
    "mid": "∣",
}

_UPRIGHT = {
    "arccos",
    "arcsin",
    "arctan",
    "cos",
    "cosh",
    "det",
    "dim",
    "exp",
    "gcd",
    "ker",
    "lim",
    "ln",
    "log",
    "max",
    "min",
    "mod",
    "sin",
    "sinh",
    "tan",
    "tanh",
}

_ALIGNED_ENVIRONMENTS = {"aligned", "alignedat", "gathered", "split"}
_ENVIRONMENT_PATTERN = re.compile(
    r"^\s*\\begin\{(?P<environment>aligned|alignedat|gathered|split)\}"
    r"(?:\{[^{}]*\})?(?P<body>.*)\\end\{(?P=environment)\}\s*$",
    flags=re.DOTALL,
)
_DELIMITER_COMMANDS = {
    "{": "{",
    "}": "}",
    "|": "‖",
    "langle": "⟨",
    "rangle": "⟩",
    "lbrace": "{",
    "rbrace": "}",
    "lvert": "|",
    "rvert": "|",
    "lVert": "‖",
    "rVert": "‖",
}
_SIZE_COMMANDS = {
    "big",
    "Big",
    "bigg",
    "Bigg",
    "bigl",
    "bigr",
    "Bigl",
    "Bigr",
    "biggl",
    "biggr",
    "Biggl",
    "Biggr",
    "displaystyle",
    "textstyle",
}
_DIRECTIONAL_SIZE_LEFT = {"bigl", "Bigl", "biggl", "Biggl"}
_DIRECTIONAL_SIZE_RIGHT = {"bigr", "Bigr", "biggr", "Biggr"}
_DIRECTIONAL_SIZE_PATTERN = re.compile(
    r"\\(?P<command>"
    + "|".join(
        sorted(
            _DIRECTIONAL_SIZE_LEFT | _DIRECTIONAL_SIZE_RIGHT,
            key=len,
            reverse=True,
        )
    )
    + r")\b"
)
_MATH_JUSTIFICATIONS = {"center", "centerGroup", "left", "right"}
_CONTROL_PROPERTY_TAGS = {
    qn("m:accPr"),
    qn("m:barPr"),
    qn("m:borderBoxPr"),
    qn("m:boxPr"),
    qn("m:dPr"),
    qn("m:eqArrPr"),
    qn("m:fPr"),
    qn("m:funcPr"),
    qn("m:groupChrPr"),
    qn("m:limLowPr"),
    qn("m:limUppPr"),
    qn("m:mPr"),
    qn("m:naryPr"),
    qn("m:phantPr"),
    qn("m:radPr"),
    qn("m:sPrePr"),
    qn("m:sSubPr"),
    qn("m:sSubSupPr"),
    qn("m:sSupPr"),
}


def _normalize_sized_delimiters(source: str) -> str:
    """Convert paired TeX size hints into native stretchy delimiters."""

    stack: list[re.Match[str]] = []
    replacements: dict[int, tuple[int, str]] = {}
    for match in _DIRECTIONAL_SIZE_PATTERN.finditer(source):
        command = match.group("command")
        if command in _DIRECTIONAL_SIZE_LEFT:
            stack.append(match)
            continue
        if not stack:
            continue
        opening = stack.pop()
        replacements[opening.start()] = (opening.end(), r"\left")
        replacements[match.start()] = (match.end(), r"\right")

    if not replacements:
        return source
    normalized: list[str] = []
    cursor = 0
    for start in sorted(replacements):
        end, replacement = replacements[start]
        normalized.extend((source[cursor:start], replacement))
        cursor = end
    normalized.append(source[cursor:])
    return "".join(normalized)


def _split_equation_rows(source: str) -> list[str]:
    """Split a display environment at top-level LaTeX row separators."""

    rows: list[str] = []
    start = 0
    depth = 0
    position = 0
    while position < len(source):
        character = source[position]
        if character == "{" and (position == 0 or source[position - 1] != "\\"):
            depth += 1
        elif character == "}" and (position == 0 or source[position - 1] != "\\"):
            depth = max(0, depth - 1)
        elif (
            character == "\\"
            and position + 1 < len(source)
            and source[position + 1] == "\\"
            and depth == 0
        ):
            rows.append(source[start:position].strip())
            position += 2
            start = position
            continue
        position += 1
    rows.append(source[start:].strip())
    return [row for row in rows if row]


def _split_alignment_segments(source: str) -> list[tuple[bool, str]]:
    """Split one equation-array row at top-level LaTeX alignment markers.

    The boolean records whether the segment begins at an alignment point.  A
    backslash escapes the following character, so ``\\&`` remains ordinary
    mathematical content.  Ampersands inside a group are likewise content;
    only the top-level markers owned by ``aligned``-style environments become
    Office Math alignment points.
    """

    segments: list[tuple[bool, str]] = []
    start = 0
    depth = 0
    position = 0
    follows_alignment = False
    while position < len(source):
        character = source[position]
        if character == "\\" and position + 1 < len(source):
            position += 2
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth = max(0, depth - 1)
        elif character == "&" and depth == 0:
            segments.append((follows_alignment, source[start:position]))
            follows_alignment = True
            start = position + 1
        position += 1
    segments.append((follows_alignment, source[start:]))
    return segments


def _equation_environment(latex: str) -> tuple[str | None, list[str]]:
    match = _ENVIRONMENT_PATTERN.match(latex.strip())
    if match is None:
        return None, [latex.strip()]
    return match.group("environment"), _split_equation_rows(match.group("body"))


def equation_row_count(latex: str) -> int:
    """Return the number of native display rows represented by ``latex``."""

    _, rows = _equation_environment(latex)
    return max(1, len(rows))


def _run(
    text: str,
    *,
    upright: bool = False,
    script: str | None = None,
) -> Any:
    run = OxmlElement("m:r")
    properties = OxmlElement("m:rPr")
    if script is not None:
        alphabet = OxmlElement("m:scr")
        alphabet.set(qn("m:val"), script)
        properties.append(alphabet)
    if upright:
        style = OxmlElement("m:sty")
        style.set(qn("m:val"), "p")
        properties.append(style)
    run.append(properties)
    value = OxmlElement("m:t")
    value.text = text
    if text[:1].isspace() or text[-1:].isspace():
        value.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    run.append(value)
    return run


def _container(tag: str, children: list[Any]) -> Any:
    node = OxmlElement(tag)
    for child in children:
        node.append(child)
    return node


def _alignment_run() -> Any:
    """Return a zero-width native Office Math alignment point."""

    run = _run("")
    properties = run.find(qn("m:rPr"))
    alignment = OxmlElement("m:aln")
    alignment.set(qn("m:val"), "1")
    properties.append(alignment)
    return run


def _mark_alignment_point(elements: list[Any]) -> list[Any]:
    """Place a native alignment point at the start of parsed row content."""

    if elements and elements[0].tag == qn("m:r"):
        properties = elements[0].find(qn("m:rPr"))
        if properties is None:
            properties = OxmlElement("m:rPr")
            elements[0].insert(0, properties)
        alignment = OxmlElement("m:aln")
        alignment.set(qn("m:val"), "1")
        properties.append(alignment)
        return elements
    return [_alignment_run(), *elements]


def _parse_equation_array_row(source: str) -> list[Any]:
    """Parse one row while mapping TeX ``&`` anchors to native OMML."""

    result: list[Any] = []
    for follows_alignment, segment in _split_alignment_segments(source):
        elements = _LatexParser(segment, display_math=True).parse()
        if follows_alignment:
            elements = _mark_alignment_point(elements)
        result.extend(elements)
    return result


def _script(base: list[Any], subscript: list[Any] | None, superscript: list[Any] | None) -> Any:
    if subscript is not None and superscript is not None:
        node = OxmlElement("m:sSubSup")
        node.append(OxmlElement("m:sSubSupPr"))
        node.append(_container("m:e", base))
        node.append(_container("m:sub", subscript))
        node.append(_container("m:sup", superscript))
        return node
    if subscript is not None:
        node = OxmlElement("m:sSub")
        node.append(OxmlElement("m:sSubPr"))
        node.append(_container("m:e", base))
        node.append(_container("m:sub", subscript))
        return node
    node = OxmlElement("m:sSup")
    node.append(OxmlElement("m:sSupPr"))
    node.append(_container("m:e", base))
    node.append(_container("m:sup", superscript or []))
    return node


class _LatexParser:
    def __init__(self, source: str, *, display_math: bool = False) -> None:
        self.source = source
        self.position = 0
        self.display_math = display_math

    def parse(self, stop: str | None = None) -> list[Any]:
        result: list[Any] = []
        while self.position < len(self.source):
            if stop is not None and self.source[self.position] == stop:
                self.position += 1
                break
            atom = self._atom()
            if not atom:
                continue
            result.extend(self._postfix_scripts(atom))
        return result

    def _postfix_scripts(self, atom: list[Any]) -> list[Any]:
        subscript: list[Any] | None = None
        superscript: list[Any] | None = None
        while self.position < len(self.source) and self.source[self.position] in "_^":
            marker = self.source[self.position]
            self.position += 1
            value = self._argument()
            if marker == "_":
                subscript = value
            else:
                superscript = value
        if subscript is not None or superscript is not None:
            return [_script(atom, subscript, superscript)]
        return atom

    def _argument(self) -> list[Any]:
        self._skip_spaces()
        if self.position >= len(self.source):
            return []
        character = self.source[self.position]
        if character == "{":
            self.position += 1
            return self.parse("}")
        if character in "\\}_^":
            # Control sequences (``x^\alpha``) and the deliberate handling of
            # malformed ``_``/``^``/``}`` both belong to ``_atom``.
            return self._atom()
        # TeX binds an unbraced argument to exactly one character, so the
        # superscript of ``a^2+b^2`` is ``2`` alone.  ``_atom`` would instead
        # run to the next delimiter and swallow ``+b`` into the script.
        self.position += 1
        return [_run(character)]

    def _raw_group(self) -> str:
        self._skip_spaces()
        if self.position >= len(self.source) or self.source[self.position] != "{":
            return ""
        self.position += 1
        start = self.position
        depth = 1
        while self.position < len(self.source) and depth:
            character = self.source[self.position]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
            self.position += 1
        return self.source[start : self.position - 1] if depth == 0 else self.source[start:]

    def _skip_spaces(self) -> None:
        while self.position < len(self.source) and self.source[self.position].isspace():
            self.position += 1

    def _delimiter_character(self) -> str:
        self._skip_spaces()
        if self.position >= len(self.source):
            return ""
        if self.source[self.position] != "\\":
            character = self.source[self.position]
            self.position += 1
            return "" if character == "." else character
        self.position += 1
        start = self.position
        while self.position < len(self.source) and self.source[self.position].isalpha():
            self.position += 1
        if start == self.position and self.position < len(self.source):
            self.position += 1
        command = self.source[start : self.position]
        return _DELIMITER_COMMANDS.get(command, _SYMBOLS.get(command, "\\" + command))

    def _delimited(self) -> list[Any]:
        beginning = self._delimiter_character()
        content_start = self.position
        cursor = self.position
        depth = 1
        closing_command = re.compile(r"\\(left|right)\b")
        while cursor < len(self.source):
            match = closing_command.search(self.source, cursor)
            if match is None:
                self.position = content_start
                return [_run(beginning)]
            if match.group(1) == "left":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    content = self.source[content_start : match.start()]
                    self.position = match.end()
                    ending = self._delimiter_character()
                    delimiter = OxmlElement("m:d")
                    properties = OxmlElement("m:dPr")
                    begin = OxmlElement("m:begChr")
                    begin.set(qn("m:val"), beginning)
                    end = OxmlElement("m:endChr")
                    end.set(qn("m:val"), ending)
                    grow = OxmlElement("m:grow")
                    grow.set(qn("m:val"), "1")
                    properties.extend((begin, end, grow))
                    delimiter.append(properties)
                    delimiter.append(
                        _container(
                            "m:e",
                            _LatexParser(
                                content,
                                display_math=self.display_math,
                            ).parse(),
                        )
                    )
                    return [delimiter]
            cursor = match.end()
        self.position = content_start
        return [_run(beginning)]

    def _nary_limit_directive(self) -> str | None:
        self._skip_spaces()
        match = re.match(
            r"\\(?P<command>limits|nolimits)(?![A-Za-z])",
            self.source[self.position :],
        )
        if match is None:
            return None
        self.position += match.end()
        return match.group("command")

    def _nary_operand(self) -> list[Any]:
        self._skip_spaces()
        while True:
            spacing = re.match(
                r"\\(?:[,;:!]|quad\b|qquad\b)",
                self.source[self.position :],
            )
            if spacing is None:
                break
            self.position += spacing.end()
            self._skip_spaces()
        if self.position >= len(self.source):
            return [_run("")]
        if self.source.startswith(r"\right", self.position):
            return [_run("")]
        if self.source[self.position] in "}])&=,+-":
            return [_run("")]
        return self._postfix_scripts(self._atom()) or [_run("")]

    def _nary(self, command: str) -> list[Any]:
        subscript: list[Any] = []
        superscript: list[Any] = []
        self._skip_spaces()
        directive = self._nary_limit_directive()
        while self.position < len(self.source) and self.source[self.position] in "_^":
            marker = self.source[self.position]
            self.position += 1
            value = self._argument()
            if marker == "_":
                subscript = value
            else:
                superscript = value
            self._skip_spaces()
        directive = self._nary_limit_directive() or directive
        if directive == "limits":
            limit_location = "undOvr"
        elif directive == "nolimits" or command in {"int", "oint"}:
            limit_location = "subSup"
        else:
            limit_location = "undOvr" if self.display_math else "subSup"
        operand = self._nary_operand()
        node = OxmlElement("m:nary")
        properties = OxmlElement("m:naryPr")
        character = OxmlElement("m:chr")
        character.set(qn("m:val"), _SYMBOLS[command])
        limits = OxmlElement("m:limLoc")
        limits.set(qn("m:val"), limit_location)
        grow = OxmlElement("m:grow")
        grow.set(qn("m:val"), "1")
        properties.extend((character, limits, grow))
        if not subscript:
            hidden_subscript = OxmlElement("m:subHide")
            hidden_subscript.set(qn("m:val"), "1")
            properties.append(hidden_subscript)
        if not superscript:
            hidden_superscript = OxmlElement("m:supHide")
            hidden_superscript.set(qn("m:val"), "1")
            properties.append(hidden_superscript)
        node.append(properties)
        node.append(_container("m:sub", subscript))
        node.append(_container("m:sup", superscript))
        node.append(_container("m:e", operand))
        return [node]

    def _limit(self) -> list[Any]:
        self._skip_spaces()
        if self.position >= len(self.source) or self.source[self.position] != "_":
            return [_run("lim", upright=True)]
        self.position += 1
        limit = OxmlElement("m:limLow")
        limit.append(OxmlElement("m:limLowPr"))
        limit.append(_container("m:e", [_run("lim", upright=True)]))
        limit.append(_container("m:lim", self._argument()))
        return [limit]

    def _atom(self) -> list[Any]:
        if self.position >= len(self.source):
            return []
        character = self.source[self.position]
        if character == "{":
            self.position += 1
            return self.parse("}")
        if character == "}":
            self.position += 1
            return []
        if character == "\\":
            return self._command()
        if character.isspace():
            self._skip_spaces()
            # TeX ignores ordinary whitespace in math mode.  In particular,
            # the separator after a control word (``\\sin x``) is not content.
            return []
        if character in "_^":
            # Preserve malformed input visibly instead of silently dropping it.
            self.position += 1
            return [_run(character, upright=True)]
        start = self.position
        while self.position < len(self.source):
            current = self.source[self.position]
            if current in "\\{}_^" or current.isspace():
                break
            self.position += 1
        return [_run(self.source[start : self.position])]

    def _command(self) -> list[Any]:
        self.position += 1
        start = self.position
        while self.position < len(self.source) and self.source[self.position].isalpha():
            self.position += 1
        if start == self.position and self.position < len(self.source):
            self.position += 1
        command = self.source[start : self.position]
        if command == "left":
            return self._delimited()
        if command == "right":
            return [_run(self._delimiter_character())]
        if command in _SIZE_COMMANDS:
            return []
        if command in {"frac", "dfrac", "tfrac"}:
            numerator = self._argument()
            denominator = self._argument()
            fraction = OxmlElement("m:f")
            fraction.append(OxmlElement("m:fPr"))
            fraction.append(_container("m:num", numerator))
            fraction.append(_container("m:den", denominator))
            return [fraction]
        if command in {"bar", "overline"}:
            bar = OxmlElement("m:bar")
            properties = OxmlElement("m:barPr")
            position = OxmlElement("m:pos")
            position.set(qn("m:val"), "top")
            properties.append(position)
            bar.append(properties)
            bar.append(_container("m:e", self._argument()))
            return [bar]
        if command == "sqrt":
            degree: list[Any] = []
            self._skip_spaces()
            if self.position < len(self.source) and self.source[self.position] == "[":
                self.position += 1
                start_degree = self.position
                while self.position < len(self.source) and self.source[self.position] != "]":
                    self.position += 1
                degree_source = self.source[start_degree : self.position]
                self.position += self.position < len(self.source)
                degree = _LatexParser(
                    degree_source,
                    display_math=self.display_math,
                ).parse()
            radicand = self._argument()
            radical = OxmlElement("m:rad")
            properties = OxmlElement("m:radPr")
            if not degree:
                hidden = OxmlElement("m:degHide")
                hidden.set(qn("m:val"), "1")
                properties.append(hidden)
            radical.append(properties)
            radical.append(_container("m:deg", degree))
            radical.append(_container("m:e", radicand))
            return [radical]
        if command == "mathbb":
            return [_run(self._raw_group(), upright=True, script="double-struck")]
        if command in {"text", "textrm", "mathrm", "operatorname", "mathbf", "mathit"}:
            return [_run(self._raw_group(), upright=command != "mathit")]
        if command == "lim":
            return self._limit()
        if command in {"int", "oint", "sum", "prod"}:
            return self._nary(command)
        if command in {"limits", "nolimits"}:
            return []
        if command in _UPRIGHT:
            return [_run(command, upright=True)]
        if command in _SYMBOLS:
            return [_run(_SYMBOLS[command])]
        if command in {",", ";", ":", "quad", "qquad"}:
            return [_run(" ")]
        if command == "!":
            return []
        if command in {"%", "#", "&", "_", "{", "}"}:
            return [_run(command, upright=True)]
        # Unknown commands remain visible and editable, preserving provenance.
        return [_run("\\" + command, upright=True)]


def _apply_word_math_format(properties: Any, font_size: float) -> None:
    """Apply one base math font/size without overriding structural scaling."""

    half_points = str(max(2, round(font_size * 2)))
    fonts = properties.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        properties.insert(0, fonts)
    for attribute in ("ascii", "hAnsi", "cs"):
        fonts.set(qn(f"w:{attribute}"), "Cambria Math")
    size = properties.find(qn("w:sz"))
    if size is None:
        size = OxmlElement("w:sz")
        properties.append(size)
    size.set(qn("w:val"), half_points)
    complex_size = properties.find(qn("w:szCs"))
    if complex_size is None:
        complex_size = OxmlElement("w:szCs")
        properties.append(complex_size)
    complex_size.set(qn("w:val"), half_points)


def _apply_math_format(equation: Any, font_size: float | None) -> None:
    """Give text runs and composite controls one consistent base typography."""

    if font_size is None:
        return
    if font_size <= 0:
        raise ValueError("font_size must be greater than zero")
    for math_run in equation.iter(qn("m:r")):
        properties = math_run.find(qn("w:rPr"))
        if properties is None:
            properties = OxmlElement("w:rPr")
            math_properties = math_run.find(qn("m:rPr"))
            insert_at = 1 if math_properties is not None else 0
            math_run.insert(insert_at, properties)
        _apply_word_math_format(properties, font_size)

    # Fractions, radicals, n-ary operators, delimiters, limits, scripts, and
    # equation arrays have their own control character formatting.  Leaving
    # these properties empty makes Word combine the document default size with
    # the explicit m:r size, producing visibly large/small symbols in one
    # expression.  A shared base size still lets OMML naturally reduce scripts
    # and enlarge operators according to their semantic nesting.
    for math_properties in equation.iter():
        if math_properties.tag not in _CONTROL_PROPERTY_TAGS:
            continue
        control = math_properties.find(qn("m:ctrlPr"))
        if control is None:
            control = OxmlElement("m:ctrlPr")
            math_properties.append(control)
        properties = control.find(qn("w:rPr"))
        if properties is None:
            properties = OxmlElement("w:rPr")
            control.append(properties)
        _apply_word_math_format(properties, font_size)


def _equation_array_properties(row_spacing: int | None) -> Any:
    properties = OxmlElement("m:eqArrPr")
    maximum_distance = OxmlElement("m:maxDist")
    maximum_distance.set(qn("m:val"), "1")
    row_spacing_rule = OxmlElement("m:rSpRule")
    row_spacing_value = OxmlElement("m:rSp")
    if row_spacing is None:
        row_spacing_rule.set(qn("m:val"), "4")
        row_spacing_value.set(qn("m:val"), "3")
    else:
        if isinstance(row_spacing, bool) or not isinstance(row_spacing, int):
            raise TypeError("row_spacing must be an integer number of points")
        if row_spacing < 1:
            raise ValueError("row_spacing must be at least one point")
        row_spacing_rule.set(qn("m:val"), "3")
        row_spacing_value.set(qn("m:val"), str(row_spacing))
    properties.extend((maximum_distance, row_spacing_rule, row_spacing_value))
    return properties


def build_omml(
    latex: str,
    *,
    font_size: float | None = None,
    display: bool = False,
    row_spacing: int | None = None,
) -> Any:
    """Return an ``m:oMath`` element retaining the source expression order."""

    equation = OxmlElement("m:oMath")
    normalized = _normalize_sized_delimiters(latex)
    environment, rows = _equation_environment(normalized)
    if environment in _ALIGNED_ENVIRONMENTS:
        array = OxmlElement("m:eqArr")
        array.append(_equation_array_properties(row_spacing))
        for row in rows:
            array.append(
                _container(
                    "m:e",
                    _parse_equation_array_row(row),
                )
            )
        equation.append(array)
    else:
        for element in _LatexParser(
            normalized.strip(),
            display_math=display,
        ).parse():
            equation.append(element)
    _apply_math_format(equation, font_size)
    return equation


def build_omml_paragraph(
    latex: str,
    *,
    font_size: float | None = None,
    justification: str = "centerGroup",
    row_spacing: int | None = None,
) -> Any:
    """Return a display-math ``m:oMathPara`` wrapper for a Word paragraph."""

    if justification not in _MATH_JUSTIFICATIONS:
        choices = ", ".join(sorted(_MATH_JUSTIFICATIONS))
        raise ValueError(f"justification must be one of: {choices}")
    wrapper = OxmlElement("m:oMathPara")
    properties = OxmlElement("m:oMathParaPr")
    alignment = OxmlElement("m:jc")
    alignment.set(qn("m:val"), justification)
    properties.append(alignment)
    wrapper.append(properties)
    wrapper.append(
        build_omml(
            latex,
            font_size=font_size,
            display=True,
            row_spacing=row_spacing,
        )
    )
    return wrapper


def latex_visible_text(latex: str) -> str:
    """Return the editable presentation text emitted into ``m:t`` nodes."""

    return "".join(node.text or "" for node in build_omml(latex).iter(qn("m:t")))


def unsupported_latex_commands(latex: str) -> list[str]:
    """List control words that the dependency-free bridge cannot structure."""

    known = (
        set(_SYMBOLS)
        | _UPRIGHT
        | _SIZE_COMMANDS
        | {
            "begin",
            "end",
            "left",
            "right",
            "frac",
            "dfrac",
            "tfrac",
            "sqrt",
            "bar",
            "overline",
            "mathbb",
            "text",
            "textrm",
            "mathrm",
            "operatorname",
            "mathbf",
            "mathit",
            "limits",
            "nolimits",
            "quad",
            "qquad",
        }
    )
    return sorted(
        {command for command in re.findall(r"\\([A-Za-z]+)", latex) if command not in known}
    )


def append_omml(
    paragraph: Paragraph,
    latex: str,
    *,
    font_size: float | None = None,
    display: bool = False,
    justification: str = "centerGroup",
    row_spacing: int | None = None,
) -> None:
    """Append one editable Office Math expression to a Word paragraph."""

    if display:
        paragraph._p.append(
            build_omml_paragraph(
                latex,
                font_size=font_size,
                justification=justification,
                row_spacing=row_spacing,
            )
        )
        return
    paragraph._p.append(
        build_omml(
            latex,
            font_size=font_size,
            row_spacing=row_spacing,
        )
    )


__all__ = [
    "append_omml",
    "build_omml",
    "build_omml_paragraph",
    "equation_row_count",
    "latex_visible_text",
    "unsupported_latex_commands",
]
