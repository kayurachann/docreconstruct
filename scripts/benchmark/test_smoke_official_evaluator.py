#!/usr/bin/env python3
"""POSIX regression for preserving a virtualenv interpreter symlink."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import venv
from pathlib import Path

from smoke_official_evaluator import nonresolving_absolute


@unittest.skipUnless(os.name == "posix", "POSIX venv symlink semantics are required")
class EvaluatorVenvPathTests(unittest.TestCase):
    def test_venv_only_import_survives_absolute_path_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv.EnvBuilder(with_pip=False, symlinks=True).create(root / "venv")
            python_path = root / "venv" / "bin" / "python"
            self.assertTrue(python_path.is_symlink())
            purelib = subprocess.run(
                [
                    str(python_path),
                    "-c",
                    "import sysconfig; print(sysconfig.get_paths()['purelib'])",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            ).stdout.strip()
            Path(purelib, "venv_only_probe.py").write_text(
                "VENVDATA = 'available'\n", encoding="utf-8"
            )
            executable = nonresolving_absolute(python_path)
            self.assertEqual(executable, python_path.absolute())
            completed = subprocess.run(
                [
                    str(executable),
                    "-c",
                    "import venv_only_probe; assert venv_only_probe.VENVDATA == 'available'",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=f"venv-only import failed: {completed.stderr[-500:]!r}",
            )


if __name__ == "__main__":
    unittest.main()
