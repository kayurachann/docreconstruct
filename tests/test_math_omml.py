from __future__ import annotations

import pytest
from docx import Document

from docreconstruct.reconstruction.math_omml import (
    append_omml,
    build_omml,
    build_omml_paragraph,
    equation_row_count,
    latex_visible_text,
    unsupported_latex_commands,
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def test_aligned_latex_becomes_native_equation_array_without_control_leaks() -> None:
    latex = (
        r"\begin{aligned}"
        r"&=\lim_{x\to0}\frac{\sin x}{x}\\"
        r"&=\frac{1}{2}"
        r"\end{aligned}"
    )

    equation = build_omml(latex)
    arrays = [node for node in equation.iter() if _local_name(node.tag) == "eqArr"]
    assert len(arrays) == 1
    assert len([node for node in arrays[0] if _local_name(node.tag) == "e"]) == 2
    assert equation_row_count(latex) == 2
    visible = latex_visible_text(latex)
    assert "\\begin" not in visible
    assert "\\end" not in visible
    assert "\\\\" not in visible
    assert "&" not in visible
    assert visible == "=limx→0sinxx=12"
    assert len([node for node in equation.iter() if _local_name(node.tag) == "aln"]) == 2
    assert unsupported_latex_commands(latex) == []


def test_limits_integrals_and_stretchy_delimiters_use_native_omml_structures() -> None:
    equation = build_omml(r"\lim_{x\to0}\left(\frac{\int_{0}^{x}e^{t^2}dt}{e^x-1}\right)")
    tags = {_local_name(node.tag) for node in equation.iter()}

    assert {"limLow", "d", "f", "nary"} <= tags


def test_sizing_commands_do_not_leak_into_visible_math() -> None:
    latex = r"\biggl(\frac{1+x}{x}\biggr)"

    equation = build_omml(latex)

    assert latex_visible_text(latex) == "1+xx"
    assert unsupported_latex_commands(latex) == []
    delimiters = [node for node in equation.iter() if _local_name(node.tag) == "d"]
    assert len(delimiters) == 1
    values = {
        _local_name(node.tag): next(iter(node.attrib.values()), None)
        for node in delimiters[0].iter()
        if _local_name(node.tag) in {"begChr", "endChr", "grow"}
    }
    assert values == {"begChr": "(", "endChr": ")", "grow": "1"}


def test_exam_set_notation_and_vector_bar_use_native_math_structures() -> None:
    latex = r"\bar{u}\in\mathbb{R},\quad k\mid k\in\mathbb{Z}"

    equation = build_omml(latex, font_size=12)

    assert unsupported_latex_commands(latex) == []
    assert "\\bar" not in latex_visible_text(latex)
    assert "\\mathbb" not in latex_visible_text(latex)
    assert "\\mid" not in latex_visible_text(latex)
    assert any(_local_name(node.tag) == "bar" for node in equation.iter())
    assert any(
        _local_name(node.tag) == "scr" and next(iter(node.attrib.values()), None) == "double-struck"
        for node in equation.iter()
    )
    assert "∣" in latex_visible_text(latex)


def test_equation_array_retains_native_alignment_and_row_spacing_controls() -> None:
    equation = build_omml(r"\begin{aligned}&=x\\&=y\end{aligned}", font_size=12)
    tags = {_local_name(node.tag) for node in equation.iter()}
    values = {
        _local_name(node.tag): next(iter(node.attrib.values()), None)
        for node in equation.iter()
        if _local_name(node.tag) in {"rSpRule", "rSp"}
    }

    assert {"eqArr", "rSpRule", "rSp", "aln"} <= tags
    assert values == {"rSpRule": "4", "rSp": "3"}
    assert latex_visible_text(r"\begin{aligned}&=x\\&=y\end{aligned}") == "=x=y"
    alignment_runs = [
        node.getparent() for node in equation.iter() if _local_name(node.tag) == "aln"
    ]
    assert len(alignment_runs) == 2
    assert all(_local_name(node.tag) == "rPr" for node in alignment_runs)
    assert all(_local_name(node.getparent().tag) == "r" for node in alignment_runs)
    assert any(_local_name(node.tag) == "sz" for node in equation.iter())


def test_equation_array_supports_multiple_native_alignment_points_per_row() -> None:
    latex = r"\begin{aligned}a&=b&+c\\d&=e&+f\end{aligned}"

    equation = build_omml(latex)
    rows = [node for node in equation.iter() if _local_name(node.tag) == "e"]
    alignment_points = [node for node in equation.iter() if _local_name(node.tag) == "aln"]

    assert len(rows) == 2
    assert len(alignment_points) == 4
    assert all(next(iter(node.attrib.values())) == "1" for node in alignment_points)
    assert latex_visible_text(latex) == "a=b+cd=e+f"


def test_escaped_and_grouped_ampersands_remain_literal_content() -> None:
    latex = r"\begin{split}x\&y&=z\\\mathrm{R&D}&=q\end{split}"

    equation = build_omml(latex)

    assert len([node for node in equation.iter() if _local_name(node.tag) == "aln"]) == 2
    assert latex_visible_text(latex) == "x&y=zR&D=q"


def test_alignment_before_composite_math_uses_a_zero_width_native_point() -> None:
    latex = r"\begin{gathered}&\frac{a}{b}\\&\sqrt{x}\end{gathered}"

    equation = build_omml(latex)
    alignment_runs = [
        node.getparent().getparent() for node in equation.iter() if _local_name(node.tag) == "aln"
    ]

    assert len(alignment_runs) == 2
    assert all(_local_name(node.tag) == "r" for node in alignment_runs)
    assert all(
        "".join(child.text or "" for child in node.iter() if _local_name(child.tag) == "t") == ""
        for node in alignment_runs
    )
    assert latex_visible_text(latex) == "abx"


def test_display_wrapper_uses_native_justification_and_exact_point_spacing() -> None:
    wrapper = build_omml_paragraph(
        r"\begin{aligned}&=x\\&=y\end{aligned}",
        font_size=11.5,
        justification="left",
        row_spacing=17,
    )
    children = [_local_name(node.tag) for node in wrapper]
    values = {
        _local_name(node.tag): next(iter(node.attrib.values()), None)
        for node in wrapper.iter()
        if _local_name(node.tag) in {"jc", "rSpRule", "rSp", "sz", "szCs"}
    }

    assert _local_name(wrapper.tag) == "oMathPara"
    assert children == ["oMathParaPr", "oMath"]
    assert values["jc"] == "left"
    assert values["rSpRule"] == "3"
    assert values["rSp"] == "17"
    assert values["sz"] == "23"
    assert values["szCs"] == "23"


def test_append_display_math_nests_omathpara_inside_word_paragraph() -> None:
    paragraph = Document().add_paragraph()

    append_omml(paragraph, r"\frac{1}{2}", display=True, justification="center")

    assert [_local_name(node.tag) for node in paragraph._p] == ["oMathPara"]
    assert any(_local_name(node.tag) == "oMath" for node in paragraph._p.iter())


def test_integral_uses_side_limits_and_has_a_native_operand() -> None:
    equation = build_omml(r"\int_{0}^{x} e^{t^2} dt", display=True)
    nary = next(node for node in equation.iter() if _local_name(node.tag) == "nary")
    limit_location = next(node for node in nary.iter() if _local_name(node.tag) == "limLoc")
    operand = next(node for node in nary if _local_name(node.tag) == "e")

    assert next(iter(limit_location.attrib.values())) == "subSup"
    assert len(operand) > 0
    assert "e" in "".join(node.text or "" for node in operand.iter())


def test_nary_hides_absent_limits_in_compatible_office_renderers() -> None:
    equation = build_omml(r"\int 7f(x)\,dx")
    properties = next(node for node in equation.iter() if _local_name(node.tag) == "naryPr")
    values = {
        _local_name(node.tag): next(iter(node.attrib.values()), None)
        for node in properties
        if _local_name(node.tag) in {"subHide", "supHide"}
    }

    assert values == {"subHide": "1", "supHide": "1"}

    lower_only = build_omml(r"\int_{0} f(x)dx")
    lower_properties = next(node for node in lower_only.iter() if _local_name(node.tag) == "naryPr")
    hidden = {
        _local_name(node.tag)
        for node in lower_properties
        if _local_name(node.tag) in {"subHide", "supHide"}
    }
    assert hidden == {"supHide"}


def test_explicit_limits_override_integral_and_sum_follows_math_context() -> None:
    explicit = build_omml(r"\int\limits_{0}^{1}f(x)", display=True)
    display_sum = build_omml(r"\sum_{n=0}^{2}x_n", display=True)
    inline_sum = build_omml(r"\sum_{n=0}^{2}x_n")

    def limit_location(equation: object) -> str:
        node = next(
            child
            for child in equation.iter()  # type: ignore[union-attr]
            if _local_name(child.tag) == "limLoc"
        )
        return next(iter(node.attrib.values()))

    assert limit_location(explicit) == "undOvr"
    assert limit_location(display_sum) == "undOvr"
    assert limit_location(inline_sum) == "subSup"


def test_composite_math_controls_share_one_explicit_base_typography() -> None:
    equation = build_omml(
        r"\begin{aligned}"
        r"&=\lim_{x\to0}\left(\frac{\int_{0}^{x}e^{t^2}dt}{\sqrt{y}}\right)\\"
        r"&=z_1^2"
        r"\end{aligned}",
        display=True,
        font_size=11.5,
        row_spacing=17,
    )
    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    math = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
    supported_properties = {
        f"{math}dPr",
        f"{math}eqArrPr",
        f"{math}fPr",
        f"{math}limLowPr",
        f"{math}naryPr",
        f"{math}radPr",
        f"{math}sSubPr",
        f"{math}sSubSupPr",
        f"{math}sSupPr",
    }
    controls = [
        node.find(f"{math}ctrlPr") for node in equation.iter() if node.tag in supported_properties
    ]

    assert controls
    assert all(control is not None for control in controls)
    control_sizes = {
        size.get(f"{word}val")
        for control in controls
        if control is not None
        for size in control.findall(f"{word}rPr/{word}sz")
    }
    control_fonts = {
        fonts.get(f"{word}ascii")
        for control in controls
        if control is not None
        for fonts in control.findall(f"{word}rPr/{word}rFonts")
    }
    run_sizes = {
        size.get(f"{word}val")
        for run in equation.iter(f"{math}r")
        for size in run.findall(f"{word}rPr/{word}sz")
    }

    assert control_sizes == run_sizes == {"23"}
    assert control_fonts == {"Cambria Math"}


def test_tex_separator_spaces_are_suppressed_but_explicit_text_spaces_remain() -> None:
    assert latex_visible_text(r"\sin x + \cos x") == "sinx+cosx"
    assert latex_visible_text(r"\text{a b}\quad c") == "a b c"


def test_invalid_display_geometry_options_are_rejected() -> None:
    with pytest.raises(ValueError, match="justification"):
        build_omml_paragraph("x", justification="diagonal")
    with pytest.raises(TypeError, match="integer number of points"):
        build_omml(r"\begin{aligned}x\\y\end{aligned}", row_spacing=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one point"):
        build_omml(r"\begin{aligned}x\\y\end{aligned}", row_spacing=0)


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        (r"a^2+b^2=c^2", "a2+b2=c2"),
        (r"a^2b^2", "a2b2"),
        (r"n^2-n^1", "n2-n1"),
        (r"a_i+b_i", "ai+bi"),
        (r"x_1+x_2", "x1+x2"),
        # Braced arguments already behaved; they must keep behaving.
        (r"a^{2}+b^{2}=c^{2}", "a2+b2=c2"),
        # A control sequence is still a single argument.
        (r"x^\alpha+1", "x\u03b1+1"),
        (r"\frac\alpha\beta", "\u03b1\u03b2"),
    ],
)
def test_unbraced_script_argument_binds_to_one_character(latex: str, expected: str) -> None:
    """TeX binds ``a^2+b`` as ``(a^2)+b``, not ``a^(2+b)``.

    Consuming to the next delimiter dropped everything after the first script
    from the rendered equation.
    """

    assert latex_visible_text(latex) == expected


def test_unbraced_command_arguments_split_across_operands() -> None:
    math = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
    fraction = build_omml(r"\frac12").find(f"{math}f")

    assert fraction is not None
    numerator = fraction.find(f"{math}num")
    denominator = fraction.find(f"{math}den")
    assert numerator is not None and denominator is not None
    assert "".join(node.text or "" for node in numerator.iter(f"{math}t")) == "1"
    assert "".join(node.text or "" for node in denominator.iter(f"{math}t")) == "2"

    radical = build_omml(r"\sqrt2x")
    radicand = radical.find(f"{math}rad/{math}e")
    assert radicand is not None
    assert "".join(node.text or "" for node in radicand.iter(f"{math}t")) == "2"
    # ``x`` belongs beside the radical, not underneath it.
    assert latex_visible_text(r"\sqrt2x") == "2x"
