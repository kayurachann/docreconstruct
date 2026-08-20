from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from tools.ci.check_coverage import GATES, evaluate_coverage
from tools.ci.validate_docx_packages import validate_docx_package
from tools.ci.validate_json_schemas import validate_schema_directory

_ROOT = Path(__file__).parents[1]
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_PINNED_ACTION = re.compile(r"^\s*uses:\s*[^\s@]+@([0-9a-f]{40})(?:\s+#.*)?$", re.MULTILINE)
_ANY_ACTION = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)


def test_ci_uses_only_full_commit_action_pins() -> None:
    workflow = _CI.read_text(encoding="utf-8")
    all_actions = _ANY_ACTION.findall(workflow)
    pinned_actions = _PINNED_ACTION.findall(workflow)
    assert all_actions
    assert len(pinned_actions) == len(all_actions)
    assert all(not action.endswith(("@main", "@master")) for action in all_actions)


def test_ci_covers_claimed_python_and_platform_compatibility_without_full_benchmark() -> None:
    workflow = _CI.read_text(encoding="utf-8")
    for version in ("3.11", "3.12", "3.13"):
        assert f'python-version: "{version}"' in workflow
    for runner in ("ubuntu-24.04", "windows-2022", "macos-14"):
        assert runner in workflow
    assert workflow.count("--cov=docreconstruct") == 1
    assert "python -m mypy src/docreconstruct tools/ci" in workflow
    assert "omnidocbench" not in workflow.casefold()
    assert "source-benchmark" not in workflow.casefold()


def test_repository_json_schemas_are_valid_and_match_runtime_models() -> None:
    assert validate_schema_directory(_ROOT / "schemas") == []


def test_committed_showcase_docx_packages_are_structurally_valid() -> None:
    packages = sorted((_ROOT / "docs" / "showcases").glob("*/editable.docx"))
    assert packages
    for package in packages:
        assert validate_docx_package(package) == []


def test_docx_validator_rejects_a_missing_internal_relationship(tmp_path: Path) -> None:
    source = _ROOT / "docs" / "showcases" / "math-exam" / "editable.docx"
    damaged = tmp_path / "damaged.docx"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(damaged, "w") as target:
        for item in original.infolist():
            if item.filename.startswith("word/media/"):
                continue
            target.writestr(item, original.read(item.filename))
    assert any(
        "relationship target is missing" in error for error in validate_docx_package(damaged)
    )


def test_coverage_policy_reports_every_failed_scope() -> None:
    files: dict[str, object] = {}
    for gate in GATES:
        for name in gate.files or ():
            files[name] = {
                "summary": {
                    "covered_lines": 1,
                    "num_statements": 10,
                    "covered_branches": 0,
                    "num_branches": 2,
                }
            }
    payload = {
        "totals": {
            "covered_lines": 1,
            "num_statements": 10,
            "covered_branches": 0,
            "num_branches": 2,
        },
        "files": files,
    }
    failures = evaluate_coverage(json.loads(json.dumps(payload)))
    assert len(failures) == len(GATES)
    assert all(gate.name in "\n".join(failures) for gate in GATES)


def test_coverage_policy_tracks_every_fusion_implementation_module() -> None:
    fusion_gate = next(gate for gate in GATES if gate.name == "evidence fusion")
    assert set(fusion_gate.files or ()) == {
        "src/docreconstruct/normalization/fusion.py",
        "src/docreconstruct/normalization/fusion_assignment.py",
        "src/docreconstruct/normalization/fusion_clustering.py",
        "src/docreconstruct/normalization/fusion_reduction.py",
        "src/docreconstruct/normalization/fusion_sources.py",
        "src/docreconstruct/normalization/fusion_spatial.py",
    }
