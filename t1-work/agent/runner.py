"""Run a generated script in a subprocess with a hard timeout and resource limits.

The script is written to a scratch directory (/tmp in the container: 64 MiB tmpfs, noexec --
noexec does not matter because the interpreter reads the file). The working directory of the child
is the OUTPUT directory, so relative output paths still land where the checker looks, and the
child is told the output directory through OUT_DIR / OUTPUT_DIR.

Limits: wall time (kill the whole process tree on timeout), address space (RLIMIT_AS, so an
out-of-control allocation gets a MemoryError inside Python instead of an OOM kill of the
container -- an OOM kill scores `resource_oom` even when the main process exits 0, issue #30),
and the BLAS/OpenMP thread counts (256-PID container limit).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

TAIL_CHARS = 3500


@dataclass
class RunResult:
    returncode: int | None       # None = killed on timeout
    seconds: float
    stdout_tail: str
    stderr_tail: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def report(self) -> str:
        parts = []
        if self.timed_out:
            parts.append(f"TIMEOUT: the script was killed after {self.seconds:.0f} s (limit reached). It must run much faster.")
        else:
            parts.append(f"exit code: {self.returncode}  (wall time {self.seconds:.1f} s)")
        if self.stderr_tail.strip():
            parts.append("stderr (tail):\n" + self.stderr_tail.strip())
        if self.stdout_tail.strip():
            parts.append("stdout (tail):\n" + self.stdout_tail.strip()[-1200:])
        return "\n".join(parts)


def _preexec(mem_bytes: int):
    def fn() -> None:  # pragma: no cover - POSIX child
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        except Exception:  # noqa: BLE001
            pass
        try:
            os.setsid()
        except Exception:  # noqa: BLE001
            pass
    return fn


def _tail(b: bytes, n: int = TAIL_CHARS) -> str:
    t = b.decode("utf-8", "replace")
    return t[-n:]


def _clean_stderr(text: str, scratch: Path) -> str:
    """Shorter traceback: drop our scratch path prefix and noisy warnings."""
    text = text.replace(scratch.as_posix(), "").replace(str(scratch), "")
    lines = [ln for ln in text.splitlines() if not re.search(r"(FutureWarning|DeprecationWarning|UserWarning: )", ln)
             or "Error" in ln]
    return "\n".join(lines)


def run_script(code: str, scratch: Path, out_dir: Path, timeout: float, mem_gib: float = 40.0,
               threads: int = 8) -> RunResult:
    scratch.mkdir(parents=True, exist_ok=True)
    script = scratch / "solution.py"
    script.write_text(code, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MODEL_", "QFBENCH_"))}
    env.update({
        "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONHASHSEED": "0",
        "OUT_DIR": str(out_dir), "OUTPUT_DIR": str(out_dir),
        "OMP_NUM_THREADS": str(threads), "OPENBLAS_NUM_THREADS": str(threads), "MKL_NUM_THREADS": str(threads),
        "NUMBA_NUM_THREADS": str(threads), "NUMEXPR_NUM_THREADS": str(threads), "POLARS_MAX_THREADS": str(threads),
        "MPLBACKEND": "Agg",
        "HOME": env.get("HOME") or scratch.as_posix(),
        "MPLCONFIGDIR": env.get("MPLCONFIGDIR") or (scratch / "mpl").as_posix(),
        "NUMBA_CACHE_DIR": env.get("NUMBA_CACHE_DIR") or (scratch / "numba").as_posix(),
        "XDG_CACHE_HOME": env.get("XDG_CACHE_HOME") or (scratch / "xdg").as_posix(),
    })
    env.pop("http_proxy", None); env.pop("https_proxy", None)
    env.pop("HTTP_PROXY", None); env.pop("HTTPS_PROXY", None)
    kwargs: dict = {}
    if os.name == "posix":
        kwargs["preexec_fn"] = _preexec(int(mem_gib * (1 << 30)))
    else:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    t0 = time.monotonic()
    try:
        p = subprocess.Popen([sys.executable, "-B", "-I", str(script)], cwd=str(out_dir), env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return RunResult(returncode=-1, seconds=0.0, stdout_tail="", stderr_tail=f"could not start interpreter: {exc!r}")
    timed_out = False
    try:
        out, err = p.communicate(timeout=max(1.0, timeout))
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(p)
        try:
            out, err = p.communicate(timeout=10)
        except Exception:  # noqa: BLE001
            out, err = b"", b""
    secs = time.monotonic() - t0
    return RunResult(returncode=None if timed_out else p.returncode, seconds=secs,
                     stdout_tail=_tail(out or b""), stderr_tail=_clean_stderr(_tail(err or b""), scratch),
                     timed_out=timed_out)


def _kill_tree(p: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            import signal

            try:
                os.killpg(p.pid, signal.SIGKILL)
            except Exception:  # noqa: BLE001
                p.kill()
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=15)
            p.kill()
    except Exception:  # noqa: BLE001
        pass
