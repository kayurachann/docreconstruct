#!/usr/bin/env python3
"""Download only declared immutable Hugging Face model snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def repository_cache_name(repo: str) -> str:
    return "models--" + repo.replace("/", "--")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_record_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or pure.is_absolute()
        or pure.as_posix() != relative
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise RuntimeError(f"unsafe declared cache path: {relative!r}")
    return root.joinpath(*pure.parts)


def verify_file_record(path: Path, record: dict[str, Any], *, allow_symlink: bool = False) -> None:
    if not path.is_file() or (path.is_symlink() and not allow_symlink):
        raise RuntimeError(f"declared cache file is missing or unsafe: {path}")
    size = path.stat().st_size
    digest = sha256_file(path)
    if size != record["bytes"] or digest != record["sha256"]:
        raise RuntimeError(
            f"pinned cache file changed: {path}; expected "
            f"{record['bytes']}/{record['sha256']}, got {size}/{digest}"
        )


def declared_revision_aliases(snapshot: dict[str, Any]) -> list[str]:
    aliases = snapshot.get("revision_aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
        raise RuntimeError("revision_aliases must be a list of strings")
    if len(aliases) != len(set(aliases)):
        raise RuntimeError(f"duplicate revision alias for {snapshot['repo']}: {aliases}")
    for alias in aliases:
        pure = PurePosixPath(alias)
        if (
            not alias
            or "\\" in alias
            or pure.is_absolute()
            or pure.as_posix() != alias
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise RuntimeError(f"unsafe revision alias for {snapshot['repo']}: {alias!r}")
    return aliases


def _actual_revision_aliases(refs: Path) -> dict[str, Path]:
    if not refs.exists():
        return {}
    if not refs.is_dir() or refs.is_symlink():
        raise RuntimeError(f"unsafe Hugging Face refs directory: {refs}")
    aliases: dict[str, Path] = {}
    for item in refs.rglob("*"):
        if item.is_symlink():
            raise RuntimeError(f"revision alias must not be a symlink: {item}")
        if item.is_file():
            aliases[item.relative_to(refs).as_posix()] = item
    return aliases


def verify_revision_aliases(repo_root: Path, snapshot: dict[str, Any]) -> None:
    aliases = declared_revision_aliases(snapshot)
    revision = snapshot["revision"]
    if not isinstance(revision, str) or not _COMMIT.fullmatch(revision):
        raise RuntimeError(f"snapshot revision must be a full commit SHA: {revision!r}")
    actual = _actual_revision_aliases(repo_root / "refs")
    if set(actual) != set(aliases):
        raise RuntimeError(
            f"revision aliases differ from contract for {snapshot['repo']}; "
            f"missing={sorted(set(aliases) - set(actual))}, "
            f"extra={sorted(set(actual) - set(aliases))}"
        )
    for alias, path in actual.items():
        target = path.read_text(encoding="ascii").strip()
        if target != revision:
            raise RuntimeError(
                f"revision alias {snapshot['repo']}@{alias} targets {target!r}, expected {revision}"
            )


def write_revision_aliases(repo_root: Path, snapshot: dict[str, Any]) -> None:
    aliases = declared_revision_aliases(snapshot)
    revision = snapshot["revision"]
    if not isinstance(revision, str) or not _COMMIT.fullmatch(revision):
        raise RuntimeError(f"snapshot revision must be a full commit SHA: {revision!r}")
    refs = repo_root / "refs"
    actual = _actual_revision_aliases(refs)
    extras = set(actual) - set(aliases)
    if extras:
        raise RuntimeError(
            f"undeclared revision aliases already exist for {snapshot['repo']}: {sorted(extras)}"
        )
    for alias, path in actual.items():
        target = path.read_text(encoding="ascii").strip()
        if target != revision:
            raise RuntimeError(
                f"refusing to replace revision alias {snapshot['repo']}@{alias}: "
                f"found {target!r}, expected {revision}"
            )
    for alias in aliases:
        reference = refs.joinpath(*PurePosixPath(alias).parts)
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_text(revision, encoding="ascii")
    verify_revision_aliases(repo_root, snapshot)


def verify_allowlist(
    path: Path, records: list[dict[str, Any]], *, allow_symlinks: bool = False
) -> None:
    declared = {record["path"] for record in records}
    if len(declared) != len(records):
        raise RuntimeError(f"duplicate file in snapshot allowlist: {path}")
    if not allow_symlinks:
        symlinks = [item for item in path.rglob("*") if item.is_symlink()]
        if symlinks:
            raise RuntimeError(f"declared cache contains symlinks: {symlinks}")
    actual = {item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file()}
    if actual != declared:
        raise RuntimeError(
            f"snapshot differs from exact allowlist: {path}; "
            f"missing={sorted(declared - actual)}, extra={sorted(actual - declared)}"
        )
    for record in records:
        verify_file_record(
            safe_record_path(path, record["path"]), record, allow_symlink=allow_symlinks
        )


def _download_asset(record: dict[str, Any], destination: Path) -> None:
    url = record.get("url")
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError(f"external model asset requires an HTTPS URL: {url!r}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error: OSError | None = None
    for attempt in range(1, 6):
        temporary: Path | None = None
        try:
            request = urllib.request.Request(str(url), headers={"User-Agent": "docreconstruct/1"})
            with (
                urllib.request.urlopen(request, timeout=120) as response,
                tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=".asset-",
                    dir=destination.parent,
                    delete=False,
                ) as stream,
            ):
                temporary = Path(stream.name)
                written = 0
                expected = int(record["bytes"])
                while chunk := response.read(min(1024 * 1024, expected - written + 1)):
                    written += len(chunk)
                    if written > expected:
                        raise RuntimeError(f"pinned asset response exceeds {expected} bytes: {url}")
                    stream.write(chunk)
            verify_file_record(temporary, record)
            temporary.replace(destination)
            return
        except OSError as error:
            last_error = error
            if attempt < 5:
                time.sleep(attempt)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    raise RuntimeError(f"failed to download pinned asset after 5 attempts: {url}") from last_error


def materialize_url_assets(root: Path, records: list[dict[str, Any]], *, offline: bool) -> None:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise RuntimeError(f"unsafe external model asset directory: {root}")
    declared = {record["path"] for record in records}
    if len(declared) != len(records):
        raise RuntimeError("duplicate external asset path")
    actual = (
        {item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_file()}
        if root.is_dir()
        else set()
    )
    extras = actual - declared
    if extras:
        raise RuntimeError(f"undeclared external model assets are present: {sorted(extras)}")
    for record in records:
        destination = safe_record_path(root, record["path"])
        if destination.is_symlink():
            raise RuntimeError(f"external model asset must not be a symlink: {destination}")
        if destination.exists():
            verify_file_record(destination, record)
        elif offline:
            raise RuntimeError(f"pinned external model asset is missing offline: {destination}")
        else:
            _download_asset(record, destination)
    verify_allowlist(root, records)


def write_mineru_config(destination: Path, model_root: Path) -> None:
    payload = {
        "config_version": "1.3.2",
        "model-source": "local",
        "models-dir": {"pipeline": str(model_root.resolve()), "vlm": ""},
        "latex-delimiter-config": {
            "display": {"left": "$$", "right": "$$"},
            "inline": {"left": "$", "right": "$"},
        },
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(args: argparse.Namespace) -> None:
    from huggingface_hub import snapshot_download

    pins: dict[str, Any] = json.loads(args.pins.read_text(encoding="utf-8"))
    system = pins["systems"][args.system]
    hub = args.hf_home / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    realized = []
    mineru_root: Path | None = None
    for snapshot in system["huggingface_snapshots"]:
        repo = snapshot["repo"]
        revision = snapshot["revision"]
        records = snapshot.get("files", [])
        path = Path(
            snapshot_download(
                repo_id=repo,
                repo_type="model",
                revision=revision,
                cache_dir=hub,
                local_files_only=args.offline,
                allow_patterns=[record["path"] for record in records] or None,
            )
        )
        if path.name != revision or not any(item.is_file() for item in path.rglob("*")):
            raise RuntimeError(f"incomplete pinned snapshot for {repo}@{revision}: {path}")
        if records:
            verify_allowlist(path, records, allow_symlinks=True)
        if args.system == "mineru" and repo == "opendatalab/PDF-Extract-Kit-1.0":
            mineru_root = path
        aliases = declared_revision_aliases(snapshot)
        write_revision_aliases(hub / repository_cache_name(repo), snapshot)
        realized.append(
            {
                "repo": repo,
                "revision": revision,
                "revision_aliases": [
                    {"name": alias, "target_revision": revision} for alias in aliases
                ],
                "declared_files": len(records) if records else None,
                "declared_bytes": sum(record["bytes"] for record in records),
            }
        )
    rapidocr_assets = system.get("rapidocr_assets", [])
    if rapidocr_assets:
        if args.rapidocr_cache is None:
            raise RuntimeError("Docling RapidOCR assets require --rapidocr-cache")
        materialize_url_assets(args.rapidocr_cache, rapidocr_assets, offline=args.offline)
        realized.append(
            {
                "kind": "external-assets",
                "name": "rapidocr",
                "declared_files": len(rapidocr_assets),
                "declared_bytes": sum(record["bytes"] for record in rapidocr_assets),
            }
        )
    if args.system == "mineru":
        if args.mineru_config is None or mineru_root is None:
            raise RuntimeError("MinerU requires --mineru-config and its pinned pipeline snapshot")
        write_mineru_config(args.mineru_config, mineru_root)
    args.output.write_text(json.dumps(realized, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--system", choices=("docling", "mineru", "marker"), required=True)
    parser.add_argument("--hf-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mineru-config", type=Path)
    parser.add_argument("--rapidocr-cache", type=Path)
    parser.add_argument("--offline", action="store_true")
    main(parser.parse_args())
