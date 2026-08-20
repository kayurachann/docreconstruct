#!/usr/bin/env python3
"""Negative smoke for package-byte provenance gates."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from compare_model_inventories import main


class PackageArchiveGateTests(unittest.TestCase):
    def test_changed_wheel_hash_with_same_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = root / "prepared.json"
            inference = root / "inference.json"
            environment = {
                "python": {"implementation": "CPython", "version": "3.11.13"},
                "platform": {"system": "Linux", "machine": "x86_64"},
                "packages": [{"name": "demo", "version": "1.0"}],
                "pip_install_inventory": [
                    {
                        "name": "demo",
                        "version": "1.0",
                        "requested": True,
                        "local_editable": False,
                        "archive_hashes": {"sha256": "a" * 64},
                    }
                ],
                "runtime_commands": [],
                "runtime_files": [],
            }
            prepared.write_text(
                json.dumps({"model_caches": [], "normalized_environment": environment}),
                encoding="utf-8",
            )
            changed = json.loads(json.dumps(environment))
            changed["pip_install_inventory"][0]["archive_hashes"]["sha256"] = "b" * 64
            inference.write_text(
                json.dumps({"model_caches": [], "normalized_environment": changed}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "Python/external runtime differs"):
                main(argparse.Namespace(prepared=prepared, inference=inference))


if __name__ == "__main__":
    unittest.main()
