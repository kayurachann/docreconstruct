"""Perform deterministic structural checks on committed DOCX packages."""

from __future__ import annotations

import argparse
import posixpath
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

_CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_OFFICE_RELATIONSHIPS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_PACKAGE_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
_WORDPROCESSING = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_REQUIRED_PARTS = frozenset({"[Content_Types].xml", "_rels/.rels", "word/document.xml"})


def _relationship_base(part_name: str) -> str:
    path = PurePosixPath(part_name)
    if path.name == ".rels" and path.parent == PurePosixPath("_rels"):
        return ""
    if path.parent.name != "_rels" or not path.name.endswith(".rels"):
        raise ValueError("relationship part is not stored below an _rels directory")
    return path.parent.parent.as_posix()


def _internal_target(part_name: str, target: str) -> str:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        raise ValueError("internal relationship target must not contain a URI origin")
    decoded = unquote(parsed.path).replace("\\", "/")
    if not decoded:
        raise ValueError("internal relationship target is empty")
    if decoded.startswith("/"):
        normalized = posixpath.normpath(decoded.lstrip("/"))
    else:
        normalized = posixpath.normpath(posixpath.join(_relationship_base(part_name), decoded))
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise ValueError("internal relationship escapes the package root")
    return normalized


def _parse_xml(package: zipfile.ZipFile, name: str) -> ElementTree.Element:
    return ElementTree.fromstring(package.read(name))


def validate_docx_package(path: Path) -> list[str]:
    """Return package-relative structural errors without invoking Microsoft Word."""

    errors: list[str] = []
    try:
        package = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"not a readable ZIP package: {exc}"]

    with package:
        names = package.namelist()
        name_set = set(names)
        duplicates = sorted(name for name in name_set if names.count(name) > 1)
        errors.extend(f"duplicate ZIP member: {name}" for name in duplicates)
        for name in names:
            normalized = name.replace("\\", "/")
            if normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
                errors.append(f"unsafe ZIP member path: {name}")

        missing = sorted(_REQUIRED_PARTS - name_set)
        errors.extend(f"missing required OPC part: {name}" for name in missing)
        corrupt = package.testzip()
        if corrupt is not None:
            errors.append(f"CRC failure in ZIP member: {corrupt}")

        roots: dict[str, ElementTree.Element] = {}
        for name in sorted(name_set):
            if not (name.endswith(".xml") or name.endswith(".rels")):
                continue
            try:
                roots[name] = _parse_xml(package, name)
            except (KeyError, ElementTree.ParseError, RuntimeError) as exc:
                errors.append(f"malformed XML part {name}: {exc}")

        content_types = roots.get("[Content_Types].xml")
        if content_types is not None:
            overrides = {
                node.get("PartName")
                for node in content_types.findall(f"{{{_CONTENT_TYPES}}}Override")
            }
            if "/word/document.xml" not in overrides:
                errors.append("[Content_Types].xml does not declare /word/document.xml")

        document = roots.get("word/document.xml")
        if document is not None:
            if document.tag != f"{{{_WORDPROCESSING}}}document":
                errors.append("word/document.xml has the wrong root element")
            if document.find(f"{{{_WORDPROCESSING}}}body") is None:
                errors.append("word/document.xml does not contain a WordprocessingML body")

        office_document_targets: list[str] = []
        for name, root in sorted(roots.items()):
            if not name.endswith(".rels"):
                continue
            for relationship in root.findall(f"{{{_PACKAGE_RELATIONSHIPS}}}Relationship"):
                target = relationship.get("Target", "")
                if relationship.get("TargetMode", "").casefold() == "external":
                    continue
                try:
                    resolved = _internal_target(name, target)
                except ValueError as exc:
                    errors.append(f"{name}: invalid relationship target {target!r}: {exc}")
                    continue
                if resolved not in name_set:
                    errors.append(f"{name}: relationship target is missing: {resolved}")
                if relationship.get("Type") == _OFFICE_RELATIONSHIPS:
                    office_document_targets.append(resolved)
        if office_document_targets != ["word/document.xml"]:
            errors.append(
                "package root must have exactly one officeDocument relationship to "
                "word/document.xml"
            )
    return errors


def discover_docx(inputs: Iterable[Path]) -> list[Path]:
    discovered: set[Path] = set()
    for candidate in inputs:
        if candidate.is_dir():
            discovered.update(path for path in candidate.rglob("*.docx") if path.is_file())
        elif candidate.is_file() and candidate.suffix.casefold() == ".docx":
            discovered.add(candidate)
    return sorted(discovered, key=lambda path: path.as_posix().casefold())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    packages = discover_docx(args.paths)
    if not packages:
        print("ERROR: no DOCX packages found")
        return 1
    failed = False
    for path in packages:
        errors = validate_docx_package(path)
        for error in errors:
            failed = True
            print(f"ERROR: {path}: {error}")
    if failed:
        return 1
    print(f"Validated {len(packages)} DOCX packages and all internal relationships.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
