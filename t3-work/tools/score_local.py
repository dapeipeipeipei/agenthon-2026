"""Score Track 3 output trees with the OFFICIAL public scorer, without Docker.

The platform driver calls ``qfbench2_track_simulation.scoring.build_developer_verifier(ctx).run(ctx)``
per unit with ``ctx = {"unit_dir": <organizer unit>, "output_dir": <what the image wrote>}``. This
script does exactly that for every unit found under an output root, so a tree produced by any
runner (local process, CI container, ...) gets the same g0-g3 verdict the Development board gives.

Usage:
    python t3-work/tools/score_local.py --units <kit>/units --out-root <root> [--timing timing.json]

Layout expected under --out-root: one directory per unit name, holding what the image wrote to
/output. If --timing is given it maps unit -> host wall-clock seconds (whole-process/container
wall) and the script prints a host events/sec table and the roster mean, the ranked quantity.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import tomllib

import pyarrow.parquet as pq

from qfbench2_track_simulation.scoring import build_developer_verifier


def count_rows(out_dir: pathlib.Path, unit_dir: pathlib.Path) -> int:
    if (unit_dir / "batch.json").is_file():
        subs = json.loads((unit_dir / "batch.json").read_text())["subs"]
        return sum(
            pq.ParquetFile(out_dir / s["sub"] / "trace.parquet").metadata.num_rows for s in subs
        )
    return int(pq.ParquetFile(out_dir / "trace.parquet").metadata.num_rows)


def family(unit_dir: pathlib.Path) -> str:
    card = tomllib.loads((unit_dir / "card.toml").read_text(encoding="utf-8"))
    return str(card.get("task", {}).get("scenario_family", "?"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", required=True, type=pathlib.Path)
    ap.add_argument("--out-root", required=True, type=pathlib.Path)
    ap.add_argument("--timing", type=pathlib.Path, default=None,
                    help="JSON {unit: wall_sec} measured by the runner (whole process/container)")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--json", type=pathlib.Path, default=None, help="write per-unit results here")
    args = ap.parse_args()

    timing = json.loads(args.timing.read_text()) if args.timing else {}
    names = sorted(p.name for p in args.out_root.iterdir() if p.is_dir())
    if args.only:
        names = [n for n in names if n in set(args.only)]
    results: dict[str, dict] = {}
    n_pass = 0
    rates: list[float] = []
    print(f"{'unit':34} {'family':27} {'verdict':8} {'rows':>8} {'wall_s':>8} {'ev/s':>9}  detail")
    for name in names:
        unit_dir = args.units / name
        out_dir = args.out_root / name
        if not unit_dir.is_dir():
            print(f"{name:34} (no such unit in kit, skipped)")
            continue
        ctx = {"unit_dir": str(unit_dir), "output_dir": str(out_dir)}
        try:
            verdict = build_developer_verifier(ctx).run(ctx)
            admissible = bool(verdict.admissible)
            detail = "" if admissible else json.dumps(verdict.detail, default=str)[:300]
            labels = [str(getattr(l, "value", l)) for l in verdict.labels]
        except Exception as exc:  # noqa: BLE001 - organizer-fault style exceptions
            admissible, detail, labels = False, f"EXC {type(exc).__name__}: {exc}"[:300], []
        try:
            rows = count_rows(out_dir, unit_dir)
        except Exception:  # noqa: BLE001
            rows = -1
        wall = timing.get(name)
        rate = (rows / wall) if (wall and rows > 0 and admissible) else (0.0 if wall else None)
        if admissible:
            n_pass += 1
        if rate is not None:
            rates.append(rate)
        results[name] = {"admissible": admissible, "labels": labels, "rows": rows,
                         "wall_sec": wall, "events_per_sec": rate, "detail": detail}
        print(f"{name:34} {family(unit_dir):27} {'PASS' if admissible else 'FAIL':8} {rows:8} "
              f"{(f'{wall:.3f}' if wall else '-'):>8} {(f'{rate:.0f}' if rate is not None else '-'):>9}  "
              f"{','.join(labels)} {detail}")
    print(f"\n{n_pass}/{len(names)} admissible")
    if rates:
        print(f"host events/sec: mean {statistics.mean(rates):.0f}  median {statistics.median(rates):.0f}  "
              f"min {min(rates):.0f}  max {max(rates):.0f}  (n={len(rates)}; mean is the ranked quantity)")
    if args.json:
        args.json.write_text(json.dumps(results, indent=1))
    return 0 if n_pass == len(names) else 1


if __name__ == "__main__":
    sys.exit(main())
