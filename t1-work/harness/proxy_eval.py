"""Local proxy evaluation: run the agent (from source) on public units against a local model behind
`ollama_shim.py`, then grade every unit with its own official checker (`check_units.py`).

    python harness/proxy_eval.py --variant v3 --units-file harness/proxy_subset30.txt \
        --endpoint http://127.0.0.1:11435 --model qwen3:8b --parallel 2 \
        --agent-env JP_UNIT_BUDGET_SEC=900 ...

Writes harness/out/proxy/<tag>/<unit>/ (output trees), <unit>.log, results.json, checks.json and
summary.json (per-unit pass/fail, requests, seconds). `--check-only` re-grades an existing run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import check_units as cu  # noqa: E402
import run_units as ru  # noqa: E402

# Time: the local model is ~10x slower per request than the House (other teams report 14-24
# requests in a 183-258 s median unit on the platform), so on the platform the 510 s budget is
# rarely binding for v3. The proxy therefore removes time pressure (budget up to the card's
# timeout - 60 s) and measures answer quality; the GPU is the shared bottleneck either way.
SCALED_DEFAULTS = {
    "JP_UNIT_BUDGET_SEC": "4000", "JP_SOFT_BUDGET_SEC": "3000", "JP_CARD_MARGIN_SEC": "-5000",
    "JP_CALL_TIMEOUT_SEC": "1800", "JP_THINK_TIMEOUT_SEC": "1800", "JP_RUN_TIMEOUT_SEC": "180",
}

VARIANTS = {
    "v1": {"JP_CANDIDATES": "1", "JP_THINK": "0", "JP_JUDGE": "0"},
    "v3": {},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, help="tag for the run directory (and a preset name if known)")
    ap.add_argument("--units-file", default=None)
    ap.add_argument("--units", nargs="*", default=None)
    ap.add_argument("--endpoint", default="http://127.0.0.1:11435")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--agent-env", nargs="*", default=[])
    ap.add_argument("--out-root", default=None)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    kit = ru.DEFAULT_KIT
    all_units = sorted(p for p in (kit / "units").iterdir() if p.is_dir() and (p / "instruction.md").exists())
    want = None
    if args.units_file:
        want = [ln.strip() for ln in Path(args.units_file).read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    if args.units:
        want = args.units
    units = [u for u in all_units if want is None or u.name in set(want)]
    tag = f"{args.variant}__{args.model.replace(':', '-').replace('/', '-')}"
    out_root = Path(args.out_root or HERE / "out" / "proxy" / tag).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    env = dict(SCALED_DEFAULTS)
    env.update(VARIANTS.get(args.variant, {}))
    for kv in args.agent_env:
        k, _, v = kv.partition("=")
        env[k] = v
    env.update({"MODEL_ENDPOINT": args.endpoint, "MODEL_TOKEN": "local", "MODEL_NAME": args.model})
    budget = float(env["JP_UNIT_BUDGET_SEC"])

    results: dict[str, dict] = {}
    if args.check_only and (out_root / "results.json").exists():
        results = {r["unit"]: r for r in json.loads((out_root / "results.json").read_text(encoding="utf-8"))}
    else:
        t_all = time.time()

        def one(u: Path) -> dict:
            out = out_root / u.name
            rc, secs, so, se = ru.run_one(u, out, env, None, budget)
            m = re.search(r"^JP_SUMMARY (\{.*\})$", so, re.M)
            summary = None
            if m:
                try:
                    summary = json.loads(m.group(1))
                except Exception:  # noqa: BLE001
                    pass
            (out_root / f"{u.name}.log").write_text(so[-40000:] + "\n--- stderr ---\n" + se[-8000:], encoding="utf-8")
            probs = ru.tree_problems(out)
            r = {"unit": u.name, "rc": rc, "seconds": round(secs, 1), "requests": summary.get("requests") if summary else None,
                 "chosen": summary.get("chosen") if summary else None, "tree_ok": not probs, "summary": summary}
            print(f"[{time.time() - t_all:6.0f}s] ran {u.name:50s} rc={rc} {secs:5.0f}s req={r['requests']} chosen={r['chosen']}", flush=True)
            return r

        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
            for r in ex.map(one, units):
                results[r["unit"]] = r
        (out_root / "results.json").write_text(json.dumps(list(results.values()), indent=1), encoding="utf-8")

    # the checkers' path shims (C:\app, C:\input, ...) are global: one grading at a time
    lock = Path(os.environ.get("TEMP", "/tmp")) / "jp_check_units.lock"
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            time.sleep(5)
    try:
        return grade(units, results, out_root, tag, env)
    finally:
        lock.rmdir()


def grade(units, results, out_root, tag, env) -> int:
    rows = []
    npass = 0
    for u in units:
        if u.name not in results:
            continue
        c = cu.run_checker(u, out_root / u.name) if (u / "checks").is_dir() else {"status": "no-checker"}
        r = results[u.name]
        row = {"unit": u.name, "pass": c.get("status") == "pass", "tests": f"{c.get('passed', 0)}/{c.get('total', 0)}",
               "seconds": r.get("seconds"), "requests": r.get("requests"), "chosen": r.get("chosen"),
               "failing": c.get("failing", [])[:4]}
        npass += row["pass"]
        rows.append(row)
        print(f"{u.name:52s} {'PASS' if row['pass'] else 'fail':4s} {row['tests']:>7s} {row['seconds']:>6}s req={row['requests']}", flush=True)
    secs = [r["seconds"] for r in rows if r["seconds"]]
    reqs = [r["requests"] for r in rows if r["requests"] is not None]
    summ = {"tag": tag, "env": {k: v for k, v in env.items() if k != "MODEL_TOKEN"}, "units": len(rows), "pass": npass,
            "partial_tests": sum(int(r["tests"].split("/")[0]) for r in rows),
            "mean_seconds": round(sum(secs) / max(1, len(secs)), 1), "mean_requests": round(sum(reqs) / max(1, len(reqs)), 1),
            "rows": rows}
    (out_root / "summary.json").write_text(json.dumps(summ, indent=1), encoding="utf-8")
    print(f"\n{tag}: pass {npass}/{len(rows)}  tests passed {summ['partial_tests']}  mean {summ['mean_seconds']}s  mean req {summ['mean_requests']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
