"""Cross-platform process isolation and resource sampling for benchmark commands."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol, cast

from ._common import EMPTY_SHA256, atomic_write, sha256_bytes, sha256_file
from .models import ProcessOutcome, SourceRunStatus

_OOM_PATTERN = re.compile(
    r"(?:out of (?:host |device )?memory|cuda out of memory|memoryerror|"
    r"cannot allocate memory|allocation failed|oom(?:-killer| killed)?)",
    re.IGNORECASE,
)
_CRASH_PATTERN = re.compile(
    r"(?:segmentation fault|access violation|core dumped|bus error|fatal signal)",
    re.IGNORECASE,
)
_POSIX_SIGKILL = 9  # POSIX reserves signal number 9 for unconditional termination.


class _KillProcessGroup(Protocol):
    def __call__(self, process_group_id: int, signal_number: int) -> None: ...


def expand_command(command: Sequence[str], replacements: Mapping[str, str]) -> list[str]:
    expanded: list[str] = []
    for argument in command:
        value = argument
        for key, replacement in replacements.items():
            value = value.replace("{" + key + "}", replacement)
        expanded.append(value)
    return expanded


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        with suppress(ProcessLookupError):
            raw_kill_process_group = vars(os).get("killpg")
            if raw_kill_process_group is None:
                process.kill()
            else:
                kill_process_group = cast(_KillProcessGroup, raw_kill_process_group)
                kill_process_group(process.pid, _POSIX_SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _log_summary(path: Path) -> tuple[str, int, str]:
    if not path.is_file():
        return EMPTY_SHA256, 0, ""
    size = path.stat().st_size
    digest = sha256_file(path)
    with path.open("rb") as stream:
        if size > 16384:
            stream.seek(-16384, os.SEEK_END)
        tail = stream.read().decode("utf-8", errors="replace")
    return digest, size, tail


def _linux_process_tree_rss_bytes(pid: int) -> int | None:
    """Best-effort current RSS sum for a Linux process tree."""

    pending = [pid]
    seen: set[int] = set()
    total = 0
    sampled = False
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        proc = Path("/proc") / str(current)
        try:
            status = (proc / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                fields = line.split()
                if len(fields) >= 2:
                    total += int(fields[1]) * 1024
                    sampled = True
                break
        try:
            children = (proc / "task" / str(current) / "children").read_text().split()
        except OSError:
            children = []
        pending.extend(int(child) for child in children if child.isdigit())
    return total if sampled else None


def _windows_process_peak_rss_bytes(pid: int) -> int | None:
    """Best-effort PeakWorkingSetSize using only the Windows standard library."""

    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    class _WindowsDllLoader(Protocol):
        def __call__(self, name: str, *, use_last_error: bool) -> ctypes.CDLL: ...

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    raw_loader = vars(ctypes).get("WinDLL")
    if raw_loader is None:
        return None
    load_windows_dll = cast(_WindowsDllLoader, raw_loader)
    kernel32 = load_windows_dll("kernel32", use_last_error=True)
    psapi = load_windows_dll("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return None
    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        success = psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.PeakWorkingSetSize) if success else None
    finally:
        kernel32.CloseHandle(handle)


def _process_rss_bytes(pid: int) -> int | None:
    if os.name == "nt":
        return _windows_process_peak_rss_bytes(pid)
    if Path("/proc").is_dir():
        return _linux_process_tree_rss_bytes(pid)
    return None


def run_process(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    stdout_path: Path,
    stderr_path: Path,
) -> ProcessOutcome:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(environment)
    creationflags = 0
    process_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        process_kwargs["start_new_session"] = True
    started = time.perf_counter()
    timed_out = False
    peak_rss_bytes: int | None = None
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            **process_kwargs,
        )
        deadline = started + timeout_seconds
        while True:
            sample = _process_rss_bytes(process.pid)
            if sample is not None:
                peak_rss_bytes = max(peak_rss_bytes or 0, sample)
            if process.poll() is not None:
                break
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                timed_out = True
                _terminate_process_tree(process)
                break
            time.sleep(min(0.05, remaining))
    duration = time.perf_counter() - started
    stdout_sha, stdout_bytes, _ = _log_summary(stdout_path)
    stderr_sha, stderr_bytes, stderr_tail = _log_summary(stderr_path)
    return ProcessOutcome(
        timed_out=timed_out,
        exit_code=process.returncode,
        duration_seconds=duration,
        stdout_sha256=stdout_sha,
        stdout_bytes=stdout_bytes,
        stderr_sha256=stderr_sha,
        stderr_bytes=stderr_bytes,
        stderr_tail=stderr_tail,
        peak_rss_bytes=peak_rss_bytes,
    )


def os_error_outcome(exc: OSError, stderr_path: Path) -> ProcessOutcome:
    payload = f"{type(exc).__name__}: {exc}".encode("utf-8", errors="replace")
    atomic_write(stderr_path, payload)
    return ProcessOutcome(
        timed_out=False,
        exit_code=None,
        duration_seconds=0.0,
        stdout_sha256=EMPTY_SHA256,
        stdout_bytes=0,
        stderr_sha256=sha256_bytes(payload),
        stderr_bytes=len(payload),
        stderr_tail=payload.decode("utf-8", errors="replace"),
        peak_rss_bytes=None,
    )


def process_failure_status(outcome: ProcessOutcome) -> SourceRunStatus:
    if outcome.timed_out:
        return SourceRunStatus.TIMEOUT
    exit_code = outcome.exit_code
    if exit_code in {137, -9} or (
        exit_code is not None and (exit_code & 0xFFFFFFFF) in {0xC0000017, 0xC000009A}
    ):
        return SourceRunStatus.OOM
    if _OOM_PATTERN.search(outcome.stderr_tail):
        return SourceRunStatus.OOM
    if exit_code is not None and (
        exit_code < 0
        or (os.name == "nt" and bool(exit_code & 0x80000000))
        or _CRASH_PATTERN.search(outcome.stderr_tail)
    ):
        return SourceRunStatus.CRASH
    return SourceRunStatus.NONZERO_EXIT
