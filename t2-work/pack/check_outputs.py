"""Check per-unit output folders the way the platform does, after the container has exited.

    python check_outputs.py --out-root DIR [--timings TSV] [--compare DIR] [--summary JSON] [--only SUBSTR]

DIR holds one sub-folder per unit (named by unit id) with what the container left in /output.
For each unit:

1. Output-tree rules (track2 README "Output folder rules"), applied through the toolkit's own
   policy function `qfbench2_common.contracts.artifact_tree.validate_listing` (node types, depth,
   per-file size, sparse ratio, case/NFC collisions, setuid/setgid/sticky) on an os.lstat walk
   that never follows links, with the README's 64 MiB total and 4,096 files+folders bounds and the
   "no files at all" / "C:name" rules added. (The toolkit's own walker, sanitize.walk_nofollow,
   is POSIX-only, so the walk is done here; the verdict is the toolkit's.)
2. Gates g0-g3 with the OFFICIAL track scorer, `track2-forecasting-public/scoring/scoring.py score`
   against the unit's card.toml (the same call t2-work/run_all_gates.py makes).
3. Optional --compare DIR: numeric comparison of forecast.parquet against DIR/<unit>/forecast.parquet
   (max |value difference|, and the largest 5/50/95 % quantile shift per cell in units of that
   cell's standard deviation).

Exit status 0 only if every unit passes 1 and 2.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import time

os.environ.setdefault("PYTHONUTF8", "1")

from qfbench2_common.contracts.artifact_tree import (  # noqa: E402
    NodeObservation, TreeLimits, classify_node, validate_listing,
)

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TRACK = ROOT / "track2-forecasting-public"
UNITS = TRACK / "units"

MIB = 1024 * 1024
README_LIMITS = TreeLimits(max_files=256, max_depth=8, max_file_bytes=64 * MIB,
                           max_total_bytes=64 * MIB, max_sparse_ratio=64.0)
MAX_NODES = 4096
REQUIRED = ("forecast.parquet", "forecast_meta.json", "forecast_rationale.md")


def tree_check(out_dir: pathlib.Path) -> tuple[bool, list[str], dict]:
    problems: list[str] = []
    obs: list[NodeObservation] = []
    n_nodes = 0
    for dirpath, dirnames, filenames in os.walk(out_dir, followlinks=False):
        for name in dirnames + filenames:
            full = pathlib.Path(dirpath, name)
            st = os.lstat(full)
            n_nodes += 1
            rel = full.relative_to(out_dir).as_posix()
            if "/" not in rel and re.match(r"^[A-Za-z]:", rel):
                problems.append("top-level name starts with a drive letter and colon")
            alloc = getattr(st, "st_blocks", None)
            allocated = alloc * 512 if alloc is not None else st.st_size
            obs.append(NodeObservation(
                path=rel, node_type=classify_node(st.st_mode, st.st_nlink),
                size_bytes=st.st_size, allocated_bytes=allocated, nlink=st.st_nlink,
                mode_bits=stat.S_IMODE(st.st_mode),
                readable=stat.S_ISDIR(st.st_mode) or os.access(full, os.R_OK)))
    v = validate_listing(obs, README_LIMITS)
    if not v.ok:
        problems += [f"tree rejection {r['code']} x{r['count']}" for r in v.rejection_rows()]
    if not v.accepted:
        problems.append("no files at all")
    if n_nodes > MAX_NODES:
        problems.append(f"{n_nodes} files+folders > {MAX_NODES}")
    for req in REQUIRED:
        if req not in v.accepted:
            problems.append(f"missing {req}")
    rat = out_dir / "forecast_rationale.md"
    if rat.is_file() and not rat.read_text(encoding="utf-8", errors="replace").strip():
        problems.append("forecast_rationale.md is blank")
    total = sum(o.size_bytes for o in obs if o.node_type.value == "regular")
    return not problems, problems, {"files": len(v.accepted), "nodes": n_nodes, "bytes": total}


def gates(unit: str, out_dir: pathlib.Path) -> dict:
    r = subprocess.run(
        [sys.executable, str(TRACK / "scoring" / "scoring.py"), "score",
         "--card", str(UNITS / unit / "card.toml"), "--forecast", str(out_dir / "forecast.parquet")],
        cwd=TRACK, capture_output=True, text=True, encoding="utf-8", errors="replace")
    s = r.stdout
    try:
        return json.loads(s[s.index("{"): s.rindex("}") + 1])
    except ValueError:
        return {"admissible": False, "gates": {}, "raw": (r.stdout + r.stderr)[-600:]}


def compare(a: pathlib.Path, b: pathlib.Path) -> dict:
    import numpy as np
    import pandas as pd
    fa, fb = pd.read_parquet(a), pd.read_parquet(b)
    key = ["asset", "horizon", "draw"]
    fa = fa.sort_values(key).reset_index(drop=True)
    fb = fb.sort_values(key).reset_index(drop=True)
    if len(fa) != len(fb) or not (fa[key].astype(str).values == fb[key].astype(str).values).all():
        return {"same_shape": False}
    va, vb = fa["value"].to_numpy(float), fb["value"].to_numpy(float)
    worst_q = 0.0
    for _, g in fa.assign(vb=vb).groupby(["asset", "horizon"]):
        sd = float(np.std(g["value"])) or 1.0
        qa = np.quantile(g["value"], [0.05, 0.5, 0.95])
        qb = np.quantile(g["vb"], [0.05, 0.5, 0.95])
        worst_q = max(worst_q, float(np.max(np.abs(qa - qb))) / sd)
    return {"same_shape": True, "max_abs_diff": float(np.max(np.abs(va - vb))),
            "identical": bool(np.array_equal(va, vb)), "max_quantile_shift_sd": worst_q}


def read_timings(path: pathlib.Path | None) -> dict[str, dict]:
    if not path or not path.is_file():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 4:
            out[parts[0]] = {"asof": parts[1], "exit_code": int(parts[2]), "seconds": float(parts[3])}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-root", type=pathlib.Path, required=True)
    ap.add_argument("--timings", type=pathlib.Path, default=None)
    ap.add_argument("--compare", type=pathlib.Path, default=None)
    ap.add_argument("--summary", type=pathlib.Path, default=None)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    a.out_root = a.out_root.resolve()  # the scorer runs with cwd=TRACK
    if a.compare is not None:
        a.compare = a.compare.resolve()

    timings = read_timings(a.timings)
    units = sorted(p.name for p in a.out_root.iterdir() if p.is_dir() and a.only in p.name)
    if not units:
        print(f"no unit folders under {a.out_root}")
        return 1
    results, n_ok, t0 = {}, 0, time.time()
    for unit in units:
        od = a.out_root / unit
        res: dict = {}
        if unit in timings:
            res.update(timings[unit])
        tree_ok, problems, stats_ = tree_check(od)
        res.update(tree_ok=tree_ok, tree_problems=problems, **stats_)
        g = gates(unit, od) if (od / "forecast.parquet").is_file() else {"admissible": False, "gates": {}}
        res["admissible"] = bool(g.get("admissible"))
        res["gates"] = g.get("gates", {})
        if not res["admissible"]:
            res["gate_detail"] = g.get("detail") or g.get("failure_labels") or g.get("raw")
        try:
            meta = json.loads((od / "forecast_meta.json").read_text(encoding="utf-8"))
            eng = meta.get("engine", {})
            res["fallback"] = bool(eng.get("fallback"))
            res["profile"] = eng.get("profile")
        except (OSError, ValueError):
            pass
        if a.compare is not None:
            other = a.compare / unit / "forecast.parquet"
            res["compare"] = compare(od / "forecast.parquet", other) if other.is_file() else {"missing": True}
        ok = tree_ok and res["admissible"] and res.get("exit_code", 0) == 0
        res["ok"] = ok
        n_ok += ok
        results[unit] = res
        first_fail = next((k for k, v in res["gates"].items() if v != "pass"), "")
        cmp_txt = ""
        if "compare" in res and res["compare"].get("same_shape"):
            c = res["compare"]
            cmp_txt = " identical" if c["identical"] else \
                f" maxdiff={c['max_abs_diff']:.3g} qshift={c['max_quantile_shift_sd']:.3g}sd"
        secs = f"{res['seconds']:7.2f}s" if "seconds" in res else ""
        print(f"{'ok  ' if ok else 'FAIL'} {unit:45s} {secs} {res['bytes']:>8d}B"
              f"{' FALLBACK' if res.get('fallback') else ''} {first_fail}"
              f"{' ' + '; '.join(problems) if problems else ''}{cmp_txt}")

    secs = [r["seconds"] for r in results.values() if "seconds" in r]
    print(f"\nunits ok {n_ok}/{len(units)}  (tree rules + official gates g0-g3)"
          + (f"  run time per unit: total {sum(secs):.1f}s, max {max(secs):.1f}s, mean {sum(secs)/len(secs):.1f}s"
             if secs else "")
          + f"  check took {time.time() - t0:.0f}s")
    if a.compare is not None:
        cs = [r["compare"] for r in results.values() if r.get("compare", {}).get("same_shape")]
        if cs:
            print(f"compare vs {a.compare}: {sum(c['identical'] for c in cs)}/{len(cs)} identical, "
                  f"max |diff| {max(c['max_abs_diff'] for c in cs):.3g}, "
                  f"max quantile shift {max(c['max_quantile_shift_sd'] for c in cs):.3g} sd")
    if a.summary:
        a.summary.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"summary -> {a.summary}")
    return 0 if n_ok == len(units) else 1


if __name__ == "__main__":
    raise SystemExit(main())
