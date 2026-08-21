"""Rebuild every committed showcase from its committed inputs and diff the result.

A showcase that cannot be regenerated is marketing, not evidence. CI runs this
on every push: each showcase's DOCX is rebuilt from ``content.md`` plus the
committed source image, and the measurable invariants are compared against
``showcase-lock.json``. Run with ``--update`` after an intentional change to
re-pin the lock.

The committed ``content.md`` files are the content authority for regeneration.
They were recovered from the shipped ``editable.docx`` artifacts (the original
review files were never published), which is exactly what makes this honest:
the numbers in the README are whatever *this* pipeline produces from *these*
inputs, re-measured on every push.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOWCASES = ROOT / "docs" / "showcases"
LOCK = SHOWCASES / "showcase-lock.json"

CASES = {
    "calculus-derivation": "source-original.jpg",
    "math-exam": "source-original.png",
    "vietnamese-exam": "source-original.png",
}


def _measure(name: str, layout_name: str, output_dir: Path) -> dict[str, object]:
    from docreconstruct.reconstruction.hybrid_job import run_hybrid_job

    base = SHOWCASES / name
    output = output_dir / f"{name}.docx"
    result = run_hybrid_job(base / "content.md", base / layout_name, output=output)
    with zipfile.ZipFile(output) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
        media = [n for n in archive.namelist() if n.startswith("word/media/")]
    gates = result.validation.gates
    return {
        "qa_gates_passed": sum(1 for gate in gates if gate.passed),
        "qa_gates_total": len(gates),
        "office_math": body.count("<m:oMath"),
        "media_parts": len(media),
        "pages": body.count("<w:sectPr"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", help="re-pin the lock file")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "showcases")
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)

    measured = {name: _measure(name, layout, arguments.output) for name, layout in CASES.items()}
    if arguments.update:
        LOCK.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"lock updated: {LOCK}")
        return 0

    expected = json.loads(LOCK.read_text(encoding="utf-8"))
    failures = []
    for name, values in measured.items():
        pinned = expected.get(name, {})
        for key, value in values.items():
            if pinned.get(key) != value:
                failures.append(f"{name}.{key}: expected {pinned.get(key)!r}, rebuilt {value!r}")
        print(f"{name}: {values}")
    if failures:
        print("\nSHOWCASE DRIFT — the committed artifacts no longer match what the")
        print("pipeline produces from the committed inputs:")
        for failure in failures:
            print("  ", failure)
        print("If the change is intentional, rerun with --update and commit the lock.")
        return 1
    print("\nall showcases reproduce their pinned invariants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
