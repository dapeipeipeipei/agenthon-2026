"""Run every public Track 4 unit through our agent and the official minimal baseline, then check
each answer with the official tools:

  * JSON-schema validation against the toolkit's analysis.schema.json
  * `check_claim_rules` (the scorer's own deterministic claim verdicts, with the judge tokenizer
    when it is installed) and `claim_penalty_preview` (the faithfulness factor)
  * `check_submitted_reasons` (shape, caps, deny list)
  * the official scorer (`score_unit`, smoke judge) on a scratch copy of the unit, with an
    APPROXIMATE outcome and a GUESSED naive rule from approx_truth.py where we have one

Usage (from t4-work/):  PYTHONUTF8=1 ../.venv/Scripts/python harness/run_local.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
ROOT = WORK.parent
T4 = ROOT / "track4-analysis-public"
UNITS = T4 / "units"
sys.path.insert(0, str(T4))
sys.path.insert(0, str(HERE))

import winshim  # noqa: E402,F401  (Windows: emulate O_DIRECTORY/dir_fd for the scorer)
from approx_truth import NAIVE, TRUTH  # noqa: E402

PY = sys.executable


DOCKER_IMAGE: str | None = None


def docker_cmd(image: str, unit: Path, outdir: Path) -> list[str]:
    """The Development platform's container settings (DEVELOPMENT-RUNTIME.md), network off."""
    return [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "--read-only", "--user", "65534:65534", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--pids-limit", "256", "--ulimit", "nofile=1024:1024", "--ulimit", "nproc=256:256",
        "--cpus", "2", "--memory", "4g", "--memory-swap", "4g", "--network=none",
        "-v", f"{unit}:/input:ro", "-v", f"{outdir}:/output",
        "-e", "QFBENCH_SEED=0", "-e", "QFBENCH_NETWORK=none",
        image, "analyze", "--task", "/input/task.json", "--corpus", "/input/corpus/", "--out", "/output/answer.json",
    ]


def run_agent(kind: str, unit: Path, out: Path) -> tuple[int, float, str]:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    env = dict(os.environ, PYTHONUTF8="1", QFBENCH_SEED="0")
    env.pop("MODEL_ENDPOINT", None)
    if kind == "ours" and DOCKER_IMAGE:
        os.chmod(out.parent, 0o777)
        cmd = docker_cmd(DOCKER_IMAGE, unit, out.parent)
        t0 = time.time()
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)
        extra = sorted(x.name for x in out.parent.iterdir() if x.name != "answer.json")
        err = (p.stderr or "")[-400:] + (f" extra output files: {extra}" if extra else "")
        return p.returncode, time.time() - t0, err
    if kind == "ours":
        cmd = [PY, "-m", "agent", "analyze", "--task", str(unit / "task.json"), "--corpus", str(unit / "corpus") + os.sep, "--out", str(out)]
        cwd = WORK
    else:
        cmd = [PY, "baselines/baseline_agent.py", "analyze", "--task", str(unit / "task.json"), "--corpus", str(unit / "corpus"), "--out", str(out)]
        cwd = T4
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
    return p.returncode, time.time() - t0, (p.stderr or "")[-400:]


def schema_errors(ans: dict) -> int:
    import importlib.resources as res

    import jsonschema

    schema = json.loads((res.files("qfbench2_common") / "schemas" / "analysis.schema.json").read_text(encoding="utf-8"))
    return len(list(jsonschema.Draft202012Validator(schema).iter_errors(ans)))


def _undo_crlf(unit_dir: Path) -> None:
    """A Windows checkout (core.autocrlf) rewrites LF as CRLF, which breaks the manifest sha256
    the scorer verifies. Restore LF where that makes the digest match again."""
    import hashlib

    for man_path in (unit_dir / "manifest.json", unit_dir / "corpus" / "manifest.json"):
        if not man_path.exists():
            continue
        man = json.loads(man_path.read_text(encoding="utf-8"))
        for f in man.get("files", []):
            p = unit_dir / f["path"]
            if not p.is_file():
                continue
            raw = p.read_bytes()
            want = f.get("sha256", "").split(":")[-1]
            if hashlib.sha256(raw).hexdigest() != want:
                fixed = raw.replace(b"\r\n", b"\n")
                if hashlib.sha256(fixed).hexdigest() == want:
                    p.write_bytes(fixed)


def scratch_unit(unit: Path, scratch: Path) -> Path:
    dst = scratch / unit.name
    if not dst.exists():
        shutil.copytree(unit, dst)
        _undo_crlf(dst)
    truth = TRUTH.get(unit.name)
    ref = dst / "reference"
    if truth and not ref.exists():
        ref.mkdir()
        rows = []
        for eid, (lab, y) in truth.items():
            row = {"entity_id": eid}
            if lab is not None:
                row["true_label"] = lab
            if y is not None:
                row["y"] = y
            rows.append(row)
        (ref / "outcome.json").write_text(json.dumps({"outcomes": rows}), encoding="utf-8")
        task = json.loads((unit / "task.json").read_text(encoding="utf-8"))
        nv = NAIVE[unit.name]
        preds = []
        for ent in task["entities"]:
            src = nv.get("point")
            p = float(ent[src]) if isinstance(src, str) else float(src or 0.0)
            hw = nv.get("hw_abs") or abs(p) * nv.get("hw_rel", 0.1) or 1.0
            row = {"entity_id": ent["entity_id"], "point_forecast": p,
                   "interval": {"level": 0.9, "lo": p - hw, "hi": p + hw},
                   "claims": [{"doc_id": "task", "span_start": 0, "span_end": 1, "claim": "x"}]}
            if nv.get("label"):
                row["label"] = nv["label"]
            preds.append(row)
        (ref / "naive_answer.json").write_text(json.dumps({"task_id": task["task_id"], "entity_predictions": preds}), encoding="utf-8")
    return dst


def official_score(unit_copy: Path, answer_path: Path) -> dict:
    from qfbench2_track_analysis.judge_factory import build_smoke_judge
    from qfbench2_track_analysis.scoring import score_unit

    outdir = answer_path.parent
    judge, prov = build_smoke_judge()
    ctx = {"unit_dir": unit_copy, "output_dir": outdir, "_enforce_faithfulness": False}
    o = score_unit(ctx, judge=judge, judge_provenance=prov, require_outcome=False)
    d = o.diagnostics or {}
    return {
        "state": o.state,
        "score": o.score if o.state == "participant_success" else None,
        "failure": o.failure_code,
        "pq": d.get("predictive_quality"),
        "iq": d.get("interval_quality"),
        "raw_pq": d.get("raw_predictive_quality"),
        "naive_pq": d.get("naive_predictive_quality"),
        "cov": d.get("interval_coverage"),
        "factor": d.get("faithfulness_factor"),
    }


def raw_metrics(unit: Path, ans: dict) -> dict:
    truth = TRUTH.get(unit.name)
    if not truth:
        return {}
    rows = {r["entity_id"]: r for r in ans["entity_predictions"]}
    out = {}
    labs = [(rows[e].get("label"), t[0]) for e, t in truth.items() if t[0] is not None]
    if labs:
        out["acc"] = sum(a == b for a, b in labs) / len(labs)
    ys = [(rows[e].get("point_forecast"), t[1], rows[e]["interval"]) for e, t in truth.items() if t[1] is not None]
    if ys and all(p is not None for p, _, _ in ys):
        out["mae"] = statistics.fmean(abs(p - y) for p, y, _ in ys)
        out["cov"] = statistics.fmean(1.0 if iv["lo"] <= y <= iv["hi"] else 0.0 for _, y, iv in ys)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-root", default=str(HERE / "out"))
    ap.add_argument("--agents", default="ours,baseline")
    ap.add_argument("--units", default="")
    ap.add_argument("--docker-image", default="", help="run OUR agent inside this image (platform settings)")
    ap.add_argument("--gate", action="store_true", help="exit 1 unless every unit of ours is clean")
    args = ap.parse_args()
    global DOCKER_IMAGE
    DOCKER_IMAGE = args.docker_image or None
    out_root = Path(args.out_root).resolve()  # docker -v needs an absolute host path
    scratch = out_root / "_scratch_units"
    scratch.mkdir(parents=True, exist_ok=True)

    from baselines.guardrails_example.citation_rail import (
        check_claim_rules,
        check_submitted_reasons,
        claim_penalty_preview,
        judge_token_counter,
        load_corpus,
    )

    counter = judge_token_counter()
    units = sorted(p for p in UNITS.iterdir() if (p / "task.json").exists())
    if args.units:
        keep = set(args.units.split(","))
        units = [u for u in units if u.name in keep]
    results = []
    for kind in args.agents.split(","):
        for unit in units:
            out = out_root / kind / unit.name / "answer.json"
            rc, secs, err = run_agent(kind, unit, out)
            sunit = scratch_unit(unit, scratch)
            rec = {"agent": kind, "unit": unit.name, "rc": rc, "secs": round(secs, 1)}
            if not out.exists():
                rec["error"] = "no answer.json " + err
                results.append(rec)
                continue
            ans = json.loads(out.read_text(encoding="utf-8"))
            rec["fallback"] = bool((ans.get("notes") or {}).get("fallback"))
            rec["schema_errors"] = schema_errors(ans)
            try:
                findings = check_claim_rules(ans, sunit, token_counter=counter)
                rec["claim_findings"] = sorted({f.code for f in findings if f.code != "claim_tokens_unchecked"})
                pen = claim_penalty_preview(ans, sunit, token_counter=counter)
                rec["claims"], rec["false_claims"], rec["factor"] = pen["claims"], pen["false"], pen["factor"]
            except Exception as exc:  # noqa: BLE001
                rec["claim_check_error"] = repr(exc)[:200]
            task = json.loads((unit / "task.json").read_text(encoding="utf-8"))
            rf = check_submitted_reasons(ans, load_corpus(unit / "corpus"), task["cutoff_date"])
            rec["n_reasons"] = len(ans.get("submitted_reasons") or [])
            rec["reason_findings"] = sorted({f.code for f in rf})
            try:
                rec.update({f"off_{k}": v for k, v in official_score(sunit, out).items()})
            except Exception as exc:  # noqa: BLE001
                rec["score_error"] = repr(exc)[:300]
            rec.update(raw_metrics(unit, ans))
            results.append(rec)
            print(json.dumps(rec, default=str))
    (out_root / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")

    # summary table
    print("\n=== summary (approximate truth, guessed naive rule; relative comparison only) ===")
    by = {}
    for r in results:
        by.setdefault(r["unit"], {})[r["agent"]] = r
    hdr = f"{'unit':34s} {'agent':9s} {'state':20s} {'score':>6s} {'pq':>5s} {'iq':>5s} {'acc':>5s} {'mae':>8s} {'cov':>5s} {'false':>5s} {'reasons':>7s}"
    print(hdr)
    tot = {}
    for u in sorted(by):
        for a in args.agents.split(","):
            r = by[u].get(a)
            if not r:
                continue
            f = lambda k, w=5, p=2: (f"{r[k]:{w}.{p}f}" if isinstance(r.get(k), (int, float)) else " " * (w - 1) + "-")  # noqa: E731
            print(f"{u:34s} {a:9s} {str(r.get('off_state')):20s} {f('off_score', 6, 3)} {f('off_pq')} {f('off_iq')} {f('acc')} {f('mae', 8, 3)} {f('cov')} {str(r.get('false_claims')):>5s} {r.get('n_reasons', 0):>7d}")
            if isinstance(r.get("off_score"), (int, float)):
                tot.setdefault(a, []).append(r["off_score"])
    for a, xs in tot.items():
        print(f"mean analysis score over {len(xs)} locally-scorable units, {a}: {statistics.fmean(xs):.3f}  (leaderboard scale {-0.27 + 1.27 * statistics.fmean(xs):.3f})")

    # admissibility gate for our agent: every unit answered, schema-valid, no fallback, no false
    # claim, not refused by the scorer, reasons clean
    bad = []
    for r in results:
        if r["agent"] != "ours":
            continue
        why = []
        if r.get("rc") != 0 or r.get("error"):
            why.append(f"rc={r.get('rc')} {r.get('error', '')}")
        if r.get("schema_errors"):
            why.append("schema")
        if r.get("fallback"):
            why.append("fallback")
        if r.get("false_claims") not in (0,) or r.get("claim_check_error"):
            why.append(f"false_claims={r.get('false_claims')} {r.get('claim_check_error', '')}")
        if r.get("score_error") or r.get("off_state") not in ("participant_success", "unrankable"):
            why.append(f"scorer: {r.get('off_state')} {r.get('off_failure')} {r.get('score_error', '')}")
        if r.get("reason_findings"):
            why.append(f"reasons: {r['reason_findings']}")
        if why:
            bad.append(f"{r['unit']}: {'; '.join(why)}")
    print(f"\nGATE (ours): {'PASS' if not bad else 'FAIL'} on {sum(1 for r in results if r['agent'] == 'ours')} units")
    for b in bad:
        print("  " + b)
    return 1 if (args.gate and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
