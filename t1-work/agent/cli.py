"""`solve --task-dir /input --out /app/output`

Never crashes and never leaves an empty or invalid output folder: the whole solve is wrapped,
a POSIX alarm bounds the wall clock as a last line of defence, and `finalize` (stubs for missing
deliverables + output-tree sanitation) runs on every path, including the alarm and any exception.
Exit code is 0 in all cases except a usage error on the command line itself.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import tempfile
import time
import traceback
from pathlib import Path

VERB = "solve"
ALARM_GRACE_S = 45.0


class _Alarm(Exception):
    pass


def _on_alarm(_sig, _frm):  # pragma: no cover - POSIX only
    raise _Alarm()


def _scratch_dir() -> Path:
    base = os.environ.get("JP_SCRATCH") or os.environ.get("TMPDIR") or ("/tmp" if os.name == "posix" else tempfile.gettempdir())
    try:
        Path(base).mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="jp-", dir=base))
    except OSError:
        return Path(tempfile.mkdtemp(prefix="jp-"))


def solve(task_dir: Path, out_dir: Path) -> int:
    from .loop import FINAL_RESERVE_S, Solver, cfg

    t0 = time.monotonic()
    scratch = _scratch_dir()
    solver = Solver(task_dir, out_dir, scratch, t0)
    budget = cfg()["unit_budget"]
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(int(budget + ALARM_GRACE_S))
        # re-armed by the solver once the card's own timeout is known (it may be shorter)
        solver.arm_alarm = lambda b: signal.alarm(max(1, int(b + ALARM_GRACE_S)))
    try:
        solver.run()
    except _Alarm:
        solver.log("hard alarm: wall-clock budget exceeded; finalizing")
    except BaseException as exc:  # noqa: BLE001 - includes SystemExit/KeyboardInterrupt from inside
        solver.log(f"solver raised {type(exc).__name__}: {str(exc)[:300]}")
        traceback.print_exc()
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
        try:
            from .runner import kill_active

            kill_active()          # a generated script must not keep writing after we finalize
        except Exception:  # noqa: BLE001
            pass
        try:
            solver.finalize()
        except BaseException:  # noqa: BLE001
            traceback.print_exc()
            _last_resort(out_dir)
        try:
            import shutil

            shutil.rmtree(scratch, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
    return 0


def _last_resort(out_dir: Path) -> None:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if not any(p.is_file() for p in out_dir.rglob("*")):
            (out_dir / "results.json").write_text("{}\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="solve", description="Agenthon 2026 Track 1 agent (team Jin & Pei)")
    ap.add_argument("verb", nargs="?", default=VERB)
    ap.add_argument("--task-dir", default="/input")
    ap.add_argument("--out", default="/app/output")
    args = ap.parse_args(argv)
    if args.verb != VERB:
        print(f"unknown verb {args.verb!r}; this image implements only `{VERB}`", file=sys.stderr)
        return 2
    task_dir = Path(args.task_dir)
    out_dir = Path(args.out)
    try:
        return solve(task_dir, out_dir)
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        _last_resort(out_dir)
        return 0
