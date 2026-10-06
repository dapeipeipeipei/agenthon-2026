"""Run a forecaster over every T2 unit and check gates g0-g3 with the track scorer.

Usage (from agenthon/):
    .venv/Scripts/python t2-work/run_all_gates.py [--engine reference|engine] [--out-root DIR] [--only PATTERN]
        [--seed N] [--platform-env]

--engine reference  (default) the track's reference CLI (text-blind Gaussian random walk)
--engine engine     our engine: `python -m engine.forecast` run from t2-work/
--out-root          per-unit output root (default t2-work/out; use t2-work/out_engine_v1 for the engine)
Writes <out-root>/<unit>/ and a summary gates.json (reference) or gates_<engine>.json.

Besides the scorer's g0-g3 it checks the platform's output-folder rules (README "Output folder
rules", DEVELOPMENT-RUNTIME.md): exactly the three contract files, no links, no file > 64 MiB,
tree <= 64 MiB, meta <= 256 KiB, rationale non-blank and <= 1 MiB. --platform-env runs the
forecaster the way a restricted unit sees it: QFBENCH_NETWORK=restricted and no MODEL_* variables
(the engine must not need them).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TRACK = ROOT / "track2-forecasting-public"
UNITS = TRACK / "units"
PY = sys.executable


PARTICIPANT_FILES = ("forecast.parquet", "forecast_meta.json", "forecast_rationale.md")
MIB = 1024 * 1024


def card_asof(card: dict) -> str:
    """The as-of the harness passes: [forecast].asof, else [provenance].data_cutoff (cutoff.trusted_asof)."""
    return (card.get("forecast") or {}).get("asof") or card["provenance"]["data_cutoff"]


ENGINE_DIR = HERE  # directory holding the `engine` package (--engine-dir)


def forecast_cmd(engine: str, unit: Path, out_dir: Path, extra: list[str]) -> tuple[list[str], Path]:
    card = tomllib.loads((unit / "card.toml").read_text(encoding="utf-8"))
    asof = card_asof(card)
    module = "qfbench2_track_forecasting.cli" if engine == "reference" else "engine.forecast"
    cwd = TRACK if engine == "reference" else ENGINE_DIR
    return [PY, "-m", module, "--panels", str(unit), "--text", str(unit / "text"),
            "--asof", asof, "--out", str(out_dir / "forecast.parquet"), *extra], cwd


def run_forecaster(engine: str, unit: Path, out_dir: Path, extra: list[str], env: dict | None = None) -> None:
    cmd, cwd = forecast_cmd(engine, unit, out_dir, extra)
    subprocess.run(cmd, check=True, cwd=cwd, capture_output=True, text=True, env=env)


def output_tree_problems(out_dir: Path) -> list[str]:
    """The organizer output checker's rules that a local scorer run does not apply."""
    probs: list[str] = []
    names = sorted(p.name for p in out_dir.iterdir())
    if tuple(sorted(PARTICIPANT_FILES)) != tuple(names):
        probs.append(f"files {names} != {sorted(PARTICIPANT_FILES)}")
    total = 0
    for p in out_dir.rglob("*"):
        if p.is_symlink():
            probs.append(f"symlink {p.name}")
            continue
        if p.is_file():
            n = p.stat().st_size
            total += n
            if n > 64 * MIB:
                probs.append(f"{p.name} > 64 MiB")
    if total > 64 * MIB:
        probs.append("tree > 64 MiB")
    meta, rat = out_dir / "forecast_meta.json", out_dir / "forecast_rationale.md"
    if meta.exists() and meta.stat().st_size > 256 * 1024:
        probs.append("forecast_meta.json > 256 KiB")
    if rat.exists():
        if rat.stat().st_size > MIB:
            probs.append("forecast_rationale.md > 1 MiB")
        if not rat.read_text(encoding="utf-8", errors="replace").strip():
            probs.append("forecast_rationale.md blank")
    return probs


def score_gates(unit: Path, out_dir: Path) -> dict:
    r = subprocess.run(
        [PY, str(TRACK / "scoring" / "scoring.py"), "score",
         "--card", str(unit / "card.toml"), "--forecast", str(out_dir / "forecast.parquet")],
        cwd=TRACK, capture_output=True, text=True,
    )
    try:
        s = r.stdout
        return json.loads(s[s.index("{"): s.rindex("}") + 1])
    except Exception:
        return {"admissible": False, "gates": {}, "raw": (r.stdout + r.stderr)[-800:]}


def engine_info(out_dir: Path) -> dict:
    """Fallback flag / timing our engine records in forecast_meta.json (absent for the reference)."""
    try:
        meta = json.loads((out_dir / "forecast_meta.json").read_text(encoding="utf-8"))
        return meta.get("engine", {})
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["reference", "engine"], default="reference")
    ap.add_argument("--out-root", type=Path, default=None, help="default t2-work/out")
    ap.add_argument("--only", default="", help="substring filter on unit id")
    ap.add_argument("--engine-args", default="", help="extra CLI args passed to the forecaster, "
                    "e.g. \"--profile v2 --width 1.15\" (env such as ENGINE_PROFILE is inherited)")
    ap.add_argument("--engine-dir", type=Path, default=None,
                    help="directory containing the engine package (default t2-work/); for A/B of a snapshot")
    ap.add_argument("--seed", default=None, help="QFBENCH_SEED for the forecaster (default: unset, engine constant)")
    ap.add_argument("--platform-env", action="store_true",
                    help="QFBENCH_NETWORK=restricted and MODEL_ENDPOINT/MODEL_NAME/MODEL_TOKEN removed")
    ap.add_argument("--summary", type=Path, default=None,
                    help="summary json (default gates.json for reference, gates_<out-root name>.json otherwise)")
    args = ap.parse_args()
    out_root = (args.out_root or HERE / "out").resolve()
    extra = shlex.split(args.engine_args)
    summary = args.summary or HERE / ("gates.json" if args.engine == "reference" else f"gates_{out_root.name}.json")

    global ENGINE_DIR
    if args.engine_dir is not None:
        ENGINE_DIR = args.engine_dir.resolve()
    env = dict(os.environ)
    if args.seed is not None:
        env["QFBENCH_SEED"] = str(args.seed)
    if args.platform_env:
        env["QFBENCH_NETWORK"] = "restricted"
        for k in ("MODEL_ENDPOINT", "MODEL_NAME", "MODEL_TOKEN"):
            env.pop(k, None)

    units = sorted(p for p in UNITS.iterdir() if p.is_dir() and args.only in p.name)
    out_root.mkdir(parents=True, exist_ok=True)
    results = {}
    t0 = time.time()
    for i, unit in enumerate(units, 1):
        od = out_root / unit.name
        od.mkdir(exist_ok=True)
        for f in od.iterdir():
            f.unlink()
        t1 = time.time()
        try:
            run_forecaster(args.engine, unit, od, extra, env)
            res = score_gates(unit, od)
            probs = output_tree_problems(od)
            if probs:
                res["output_tree_problems"] = probs
                res["admissible"] = False
        except subprocess.CalledProcessError as e:
            res = {"admissible": False, "gates": {}, "raw": (e.stderr or "")[-800:]}
        res["elapsed_s"] = round(time.time() - t1, 2)
        info = engine_info(od)
        if info:
            res["fallback"] = bool(info.get("fallback"))
            res["fallback_reason"] = info.get("fallback_reason")
        results[unit.name] = res
        ok = "ok " if res.get("admissible") else "FAIL"
        gates = res.get("gates", {})
        first_fail = next((g for g, v in gates.items() if v != "pass"), "")
        fb = " FALLBACK" if res.get("fallback") else ""
        detail = res.get("detail", {}).get("code", "") if not res.get("admissible") else ""
        print(f"[{i:3d}/{len(units)}] {ok} {unit.name:45s} {res['elapsed_s']:5.2f}s{fb} {first_fail} {detail}")

    n_ok = sum(1 for r in results.values() if r.get("admissible"))
    n_fb = sum(1 for r in results.values() if r.get("fallback"))
    slowest = max(results.items(), key=lambda kv: kv[1].get("elapsed_s", 0.0)) if results else ("-", {})
    summary.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nadmissible {n_ok}/{len(units)}  fallbacks {n_fb}  in {time.time() - t0:.0f}s  -> {summary}")
    return 0 if n_ok == len(units) else 1


if __name__ == "__main__":
    raise SystemExit(main())
