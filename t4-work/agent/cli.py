"""`analyze --task /input/task.json --corpus /input/corpus/ --out /output/answer.json`

Never crashes: any failure in the main path writes a schema-valid minimal answer built from
task.json alone, and the process exits 0. A hard alarm (POSIX) bounds the wall clock well inside
the 600 s unit limit.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

VERB = "analyze"
SOFT_BUDGET_S = float(os.environ.get("T4_SOFT_BUDGET_S", "420"))
HARD_ALARM_S = int(os.environ.get("T4_HARD_ALARM_S", "520"))


class _Timeout(Exception):
    pass


def _alarm(_sig, _frm):  # pragma: no cover - POSIX only
    raise _Timeout()


def run(task_path: Path, corpus_dir: Path, out_path: Path) -> dict:
    from .answer import build_answer, minimal_answer, validate, write_answer
    from .corpus import load_unit
    from .house import enhance
    from .predict import predict_unit

    t0 = time.monotonic()
    deadline = t0 + SOFT_BUDGET_S
    task = None
    try:
        task = json.loads(task_path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        task = None
    try:
        unit = load_unit(task_path, corpus_dir)
        preds = predict_unit(unit)
        notes = {"seed": os.environ.get("QFBENCH_SEED", "")}
        try:
            notes.update(enhance(unit, preds, deadline))
        except Exception:  # noqa: BLE001
            notes["house"] = "error"
        ans = build_answer(unit, preds, extra_notes=notes)
        errs = validate(ans, unit)
        if errs:
            # retry without reasons (the only optional block), then fall back
            ans = build_answer(unit, preds, reasons=False, extra_notes=notes)
            errs = validate(ans, unit)
        if errs:
            raise ValueError("self-check failed: " + "; ".join(errs[:5]))
        write_answer(ans, out_path)
        return ans
    except BaseException as exc:  # noqa: BLE001 - includes the alarm
        if isinstance(exc, KeyboardInterrupt):
            raise
        if task is None:
            raise
        ans = minimal_answer(task, note=f"{type(exc).__name__}: {exc}")
        write_answer(ans, out_path)
        return ans


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analyze", description="Agenthon 2026 Track 4 analyst")
    parser.add_argument("verb", nargs="?", default=VERB, choices=[VERB])
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(HARD_ALARM_S)
    try:
        run(args.task, args.corpus, args.out)
    except Exception as exc:  # noqa: BLE001 - last resort: still exit 0 with whatever we can
        print(f"analyze: unrecoverable error: {exc}", file=sys.stderr)
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
    print(f"analyze: wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
