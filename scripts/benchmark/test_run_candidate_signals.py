#!/usr/bin/env python3
"""POSIX regression smoke for preserving candidate child signals."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "posix", "POSIX return-code semantics are required")
class CandidateSignalPropagationTests(unittest.TestCase):
    def assert_signal_propagates(self, child_signal: signal.Signals) -> None:
        child_code = f"import os, signal; os.kill(os.getpid(), signal.Signals({int(child_signal)}))"
        wrapper_code = (
            "import sys; from pathlib import Path; from run_candidate import run; "
            f"run([sys.executable, '-c', {child_code!r}], cwd=Path.cwd())"
        )
        completed = subprocess.run(
            [sys.executable, "-c", wrapper_code],
            cwd=Path(__file__).resolve().parent,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(
            completed.returncode,
            -int(child_signal),
            msg=(
                f"wrapper return code {completed.returncode}, expected {-int(child_signal)}; "
                f"stderr={completed.stderr[-500:]!r}"
            ),
        )

    def test_sigkill_propagates(self) -> None:
        self.assert_signal_propagates(signal.SIGKILL)

    def test_sigsegv_propagates(self) -> None:
        self.assert_signal_propagates(signal.SIGSEGV)


if __name__ == "__main__":
    unittest.main()
