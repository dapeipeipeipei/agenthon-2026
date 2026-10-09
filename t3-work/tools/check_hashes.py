"""Byte-level regression against the organizer's references, without the LFS parquet files.

Every public unit declares the sha256 of its reference trace.parquet and message_trace.parquet
(single units: events.json; batch units: batch.json per sub). Our engine reproduces the reference
files byte for byte, so comparing the hashes of what the image wrote with those declarations is a
stricter check than the semantic gate and needs no LFS download in CI. It also re-does the g1
sidecar arithmetic the scorer applies (n_events == rows, events_per_sec == n/wall within 5%,
scenario_id/seed bound to the scenario, trace_sha256 recomputed) and the output-folder allowlist.

Usage: python check_hashes.py --units <kit>/units --out-root <root> [--timing timing.json]
Exit 1 on any mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
import sys

import pyarrow.parquet as pq

ALLOWED_SINGLE = {"trace.parquet", "message_trace.parquet", "events.json", "profile.json"}
ALLOWED_SUB = {"trace.parquet", "message_trace.parquet", "events.json"}


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


ROWS_ONLY = False  # --rows-only: a different (content-identical) parquet writer; check row counts, not bytes


def rows(p: pathlib.Path) -> int:
    return int(pq.ParquetFile(p).metadata.num_rows)


def check_sidecar(ev: dict, out: pathlib.Path, scenario: dict, problems: list[str]) -> None:
    for k in ("scenario_id", "n_events", "wall_clock_sec", "events_per_sec", "seed", "trace_sha256"):
        if k not in ev:
            problems.append(f"events.json missing {k}")
    if problems:
        return
    if ev["scenario_id"] != scenario.get("scenario_id") or ev["seed"] != scenario.get("seed"):
        problems.append("events.json scenario_id/seed differ from the scenario")
    n, wc, eps = float(ev["n_events"]), float(ev["wall_clock_sec"]), float(ev["events_per_sec"])
    if n <= 0 or wc <= 0 or abs(eps - n / wc) / (n / wc) > 0.05:
        problems.append(f"events_per_sec inconsistent: {eps} vs {n}/{wc}")
    if int(ev["n_events"]) != rows(out / "trace.parquet"):
        problems.append("n_events != trace rows")
    if ev["trace_sha256"] != sha(out / "trace.parquet"):
        problems.append("trace_sha256 != sha256(trace.parquet)")


def check_single(unit: pathlib.Path, out: pathlib.Path) -> tuple[list[str], int]:
    problems: list[str] = []
    ref = json.loads((unit / "events.json").read_text(encoding="utf-8"))
    scenario = json.loads((unit / "scenario.json").read_text(encoding="utf-8"))
    extra = sorted(p.name for p in out.iterdir() if p.name not in ALLOWED_SINGLE)
    if extra:
        problems.append(f"files outside the output allowlist: {extra}")
    for fn, key in (("trace.parquet", "trace_sha256"), ("message_trace.parquet", "message_trace_sha256")):
        p = out / fn
        if not p.is_file():
            problems.append(f"missing {fn}")
        elif ROWS_ONLY:
            want = ref["n_events"] if fn == "trace.parquet" else ref.get("n_messages")
            if want is not None and rows(p) != int(want):
                problems.append(f"{fn}: {rows(p)} rows vs reference {want}")
        elif sha(p) != ref[key]:
            problems.append(f"{fn} sha256 differs from the reference ({rows(p)} rows vs {ref['n_events'] if fn == 'trace.parquet' else ref.get('n_messages')})")
    evp = out / "events.json"
    if not evp.is_file():
        problems.append("missing events.json")
        return problems, 0
    ev = json.loads(evp.read_text(encoding="utf-8"))
    check_sidecar(ev, out, scenario, problems)
    return problems, rows(out / "trace.parquet") if (out / "trace.parquet").is_file() else 0


def check_batch(unit: pathlib.Path, out: pathlib.Path) -> tuple[list[str], int]:
    problems: list[str] = []
    batch = json.loads((unit / "batch.json").read_text(encoding="utf-8"))
    declared = {s["sub"]: s for s in batch["subs"]}
    extra = sorted(p.name for p in out.iterdir() if p.name != "batch_events.json" and p.name not in declared)
    if extra:
        problems.append(f"entries outside the batch allowlist: {extra}")
    total = 0
    for sub, s in declared.items():
        d = out / sub
        if not d.is_dir():
            problems.append(f"missing sub dir {sub}")
            continue
        extra = sorted(p.name for p in d.iterdir() if p.name not in ALLOWED_SUB)
        if extra:
            problems.append(f"{sub}: files outside the allowlist: {extra}")
        for fn, key in (("trace.parquet", "reference_trace_sha256"), ("message_trace.parquet", "reference_message_sha256")):
            p = d / fn
            if not p.is_file():
                problems.append(f"{sub}: missing {fn}")
            elif ROWS_ONLY:
                if fn == "trace.parquet" and rows(p) != int(s["n_events"]):
                    problems.append(f"{sub}: {rows(p)} trace rows vs reference {s['n_events']}")
            elif sha(p) != s[key]:
                problems.append(f"{sub}: {fn} sha256 differs from the reference")
        evp = d / "events.json"
        if not evp.is_file():
            problems.append(f"{sub}: missing events.json")
            continue
        scenario = json.loads((unit / s["scenario_file"]).read_text(encoding="utf-8"))
        check_sidecar(json.loads(evp.read_text(encoding="utf-8")), d, scenario, problems)
        if (d / "trace.parquet").is_file():
            total += rows(d / "trace.parquet")
    bep = out / "batch_events.json"
    if not bep.is_file():
        problems.append("missing batch_events.json")
        return problems, total
    be = json.loads(bep.read_text(encoding="utf-8"))
    seen = [e.get("sub") for e in be.get("per_scenario", [])]
    if sorted(seen) != sorted(declared) or len(seen) != len(set(seen)):
        problems.append("batch_events per_scenario != declared subs")
    for k in ("total_events", "wall_clock_sec", "events_per_sec", "n_scenarios"):
        if k not in be:
            problems.append(f"batch_events missing {k}")
    if not problems:
        t, wc, eps = float(be["total_events"]), float(be["wall_clock_sec"]), float(be["events_per_sec"])
        if int(t) != total or wc <= 0 or abs(eps - t / wc) / (t / wc) > 0.05:
            problems.append("batch_events arithmetic inconsistent")
        for e in be["per_scenario"]:
            if int(e["n_events"]) != rows(out / e["sub"] / "trace.parquet"):
                problems.append(f"{e['sub']}: per_scenario n_events != rows")
    return problems, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", required=True, type=pathlib.Path)
    ap.add_argument("--out-root", required=True, type=pathlib.Path)
    ap.add_argument("--timing", type=pathlib.Path, default=None)
    ap.add_argument("--json", type=pathlib.Path, default=None)
    ap.add_argument("--rows-only", action="store_true",
                    help="outputs from a different parquet writer: check reference row counts instead of bytes "
                         "(pair with check_content.py for the values)")
    args = ap.parse_args()
    global ROWS_ONLY
    ROWS_ONLY = args.rows_only
    timing = json.loads(args.timing.read_text()) if args.timing and args.timing.is_file() else {}
    names = sorted(p.name for p in args.units.iterdir() if p.is_dir())
    ok = 0
    rates = []
    results = {}
    print(f"{'unit':34} {'result':6} {'rows':>8} {'wall_s':>7} {'ev/s':>8}  problems")
    for name in names:
        unit, out = args.units / name, args.out_root / name
        if not out.is_dir():
            problems, n = ["no output directory"], 0
        elif (unit / "batch.json").is_file():
            problems, n = check_batch(unit, out)
        else:
            problems, n = check_single(unit, out)
        wall = timing.get(name)
        rate = (n / wall) if (wall and not problems) else (0.0 if wall else None)
        if rate is not None:
            rates.append(rate)
        if not problems:
            ok += 1
        results[name] = {"ok": not problems, "rows": n, "wall_sec": wall, "events_per_sec": rate, "problems": problems}
        print(f"{name:34} {'OK' if not problems else 'FAIL':6} {n:8} {(f'{wall:.3f}' if wall else '-'):>7} "
              f"{(f'{rate:.0f}' if rate is not None else '-'):>8}  {'; '.join(problems)}")
    what = "row counts equal to" if ROWS_ONLY else "byte-identical to"
    print(f"\n{ok}/{len(names)} units {what} the references and sidecar-consistent")
    if rates:
        print(f"events/sec over {len(rates)} timed units: mean {statistics.mean(rates):.0f} "
              f"(ranked quantity)  median {statistics.median(rates):.0f}  min {min(rates):.0f}  max {max(rates):.0f}")
    if args.json:
        args.json.write_text(json.dumps(results, indent=1))
    return 0 if ok == len(names) else 1


if __name__ == "__main__":
    sys.exit(main())
