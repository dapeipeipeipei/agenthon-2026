"""Headroom analysis for Track 4: score answers against VERIFIED outcomes under several naive-rule
hypotheses, and compare with oracle answers. LOCAL DIAGNOSTICS ONLY (the agent never reads this).

    PYTHONUTF8=1 ../.venv/Scripts/python harness/headroom_eval.py --agent-root <dir containing t4-work/agent> --tag dev
    PYTHONUTF8=1 ../.venv/Scripts/python harness/headroom_eval.py --answers harness/out/ours --tag cur

The composite is re-implemented here from scorer 5.2.2 (`_composite`) with the toolkit's own
`predictive_quality` and `mean_interval_score`, so naive hypotheses can be swept quickly; the
faithfulness factor is taken as 1.0 (the Development board runs no NLI and our answers have 0
deterministic false claims). `--check` cross-checks one hypothesis against the official
`score_unit` (smoke judge) on scratch copies.

Truth comes from harness/out/_headroom/truth_verified.json (run headroom_truth.py first).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
T4 = WORK.parent / "track4-analysis-public"
UNITS = T4 / "units"
CACHE = HERE / "out" / "_headroom"
sys.path.insert(0, str(T4))

from qfbench2_common.scoring import faithfulness as F  # noqa: E402

TT = {  # target type, interval leg scored?
    "t4-EXAMPLE-eps-beat": ("classification", True),
    "t4-auction-btc-202411-us7": ("regression", True),
    "t4-cotpos-202411-us10": ("ranking", True),
    "t4-cpicomp-202410-us11": ("regression", True),
    "t4-credit-event-2023": ("classification", False),
    "t4-eps-growth-2024Q3-banks": ("regression", True),
    "t4-eps-yoy-2023Q2-mixed": ("classification", True),
    "t4-fomc-curve-20220728": ("regression", True),
    "t4-fomc-curve-20240918": ("regression", True),
    "t4-macrorev-20240930-us6": ("classification", True),
    "t4-postearn-20240201-megacap": ("classification", None),  # numeric y? see below
}
DEV10 = [u for u in TT if u != "t4-EXAMPLE-eps-beat"]


def load_truth() -> dict:
    return json.loads((CACHE / "truth_verified.json").read_text(encoding="utf-8"))


def task(unit: str) -> dict:
    return json.loads((UNITS / unit / "task.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------------
# naive-rule hypotheses: per unit, a function entity -> (label, point, lo, hi)
# --------------------------------------------------------------------------------------------
def _band(p: float, hw: float) -> tuple[float, float]:
    return p - hw, p + hw


def _auction_hist() -> dict[str, list[float]]:
    out = {}
    for f in (UNITS / "t4-auction-btc-202411-us7" / "corpus").glob("TDIRECT_AUCTIONS_*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        vals = []
        for line in d["text"].splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 7 and parts[0][:2] == "20":
                vals.append(float(parts[4]))
        out[f.stem.split("_")[2]] = vals
    return out


def naive_rows(unit: str, hyp: str) -> dict[str, tuple]:
    """hyp in {'carry','zero','mean'}; 'carry' = the author-guide naive (value one period earlier)."""
    t = task(unit)
    rows = {}
    for e in t["entities"]:
        eid = e["entity_id"]
        if unit == "t4-EXAMPLE-eps-beat":
            p = e["consensus_eps"]; rows[eid] = ("inline", p, *_band(p, 0.15 * p))
        elif unit == "t4-credit-event-2023":
            rows[eid] = ("no_event", 0.0, 0.0, 1.0)
        elif unit == "t4-eps-yoy-2023Q2-mixed":
            p = e["prior_year_q_eps"]
            lab = {"carry": "up", "zero": "down", "mean": "up"}[hyp]
            rows[eid] = (lab, p, *_band(p, 0.3 * abs(p) + 0.1))
        elif unit == "t4-postearn-20240201-megacap":
            rows[eid] = ("flat", 0.0, -5.0, 5.0)
        elif unit == "t4-macrorev-20240930-us6":
            p = e["latest_precutoff_estimate"]
            lab = {"carry": "down", "zero": "up", "mean": "down"}[hyp]
            rows[eid] = (lab, p, *_band(p, 0.005 * abs(p)))
        elif unit == "t4-cpicomp-202410-us11":
            p = e["latest_published_mom_pct"] if hyp != "zero" else 0.0
            rows[eid] = (None, p, *_band(p, 0.5 if "GAS" not in eid and "ENERGY" not in eid and "USED" not in eid and "APPAREL" not in eid else 2.0))
        elif unit.startswith("t4-fomc-curve"):
            rows[eid] = (None, 0.0, -50.0, 50.0)
        elif unit == "t4-eps-growth-2024Q3-banks":
            rows[eid] = (None, 0.0, -30.0, 30.0)
        elif unit == "t4-auction-btc-202411-us7":
            h = _auction_hist()[e["tenor"].split("-")[0] + "Y"]
            p = h[-1] if hyp != "mean" else statistics.fmean(h[-6:])
            rows[eid] = (None, p, *_band(p, 0.25))
        elif unit == "t4-cotpos-202411-us10":
            p = e["trailing_4wk_net_change_pct_oi"] if hyp == "carry" else 0.0
            rows[eid] = (None, p, *_band(p, 10.0))
    return rows


# --------------------------------------------------------------------------------------------
def composite(unit: str, ans_rows: dict, truth_rows: dict, naive: dict) -> dict:
    ttype, leg = TT[unit]
    ids = [e["entity_id"] for e in task(unit)["entities"]]
    tl = [truth_rows[i].get("label") or "" for i in ids]
    tv = [truth_rows[i].get("y") for i in ids]
    numeric = all(v is not None for v in tv)
    if unit == "t4-postearn-20240201-megacap":
        leg = numeric
    pl = [ans_rows[i].get("label") for i in ids]
    pv = [float(ans_rows[i].get("point_forecast", float("nan"))) for i in ids]
    nl = [naive[i][0] for i in ids]
    nv = [naive[i][1] for i in ids]
    tvv = [float(v) if v is not None else float("nan") for v in tv]
    q = F.predictive_quality(ttype, pl, tl, pv, tvv if numeric else [], naive_values=nv if ttype == "regression" else None)
    raw_q = q
    if ttype in ("classification", "ranking"):
        nq = F.predictive_quality(ttype, nl, tl, nv, tvv if numeric else [])
        anchor = max(nq, 0.5) if ttype == "ranking" else nq
        if q >= anchor:
            q = 1.0 if anchor >= 1 - 1e-12 else 0.5 + 0.5 * (q - anchor) / (1 - anchor)
        else:
            q = 0.5 * q / anchor if anchor > 0 else 0.5
    else:
        nq = None
    if not (numeric and leg):
        return {"pq": q, "raw_q": raw_q, "naive_q": nq, "iq": None, "score": q}
    lo = [ans_rows[i]["interval"]["lo"] for i in ids]
    hi = [ans_rows[i]["interval"]["hi"] for i in ids]
    nis = F.mean_interval_score([naive[i][2] for i in ids], [naive[i][3] for i in ids], tvv, 0.9)
    ois = F.mean_interval_score(lo, hi, tvv, 0.9)
    raw_iq = nis / (nis + ois)
    iq = min(raw_iq, max(0.5, q))
    cov = sum(1 for a, b, y in zip(lo, hi, tvv) if a <= y <= b) / len(ids)
    return {"pq": q, "raw_q": raw_q, "naive_q": nq, "iq": iq, "raw_iq": raw_iq, "cov": cov,
            "IS": ois, "naive_IS": nis, "score": 0.7 * q + 0.3 * iq}


def load_answers(root: Path) -> dict[str, dict]:
    out = {}
    for u in TT:
        p = root / u / "answer.json"
        if p.exists():
            a = json.loads(p.read_text(encoding="utf-8"))
            out[u] = {r["entity_id"]: r for r in a["entity_predictions"]}
    return out


def run_agent(agent_root: Path, out_root: Path) -> None:
    env = dict(os.environ, PYTHONUTF8="1", QFBENCH_SEED="0")
    env.pop("MODEL_ENDPOINT", None)
    for u in TT:
        out = out_root / u / "answer.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "agent", "analyze", "--task", str(UNITS / u / "task.json"),
               "--corpus", str(UNITS / u / "corpus") + os.sep, "--out", str(out)]
        subprocess.run(cmd, cwd=agent_root / "t4-work", env=env, capture_output=True, timeout=900)


def oracle_rows(unit: str, truth_rows: dict, width_rel: float = 0.0) -> dict:
    rows = {}
    for eid, t in truth_rows.items():
        y = t.get("y")
        p = float(y) if y is not None else 0.0
        hw = abs(p) * width_rel
        rows[eid] = {"label": t.get("label"), "point_forecast": p, "interval": {"lo": p - hw, "hi": p + hw}}
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent-root", default="")
    ap.add_argument("--answers", default="")
    ap.add_argument("--tag", default="x")
    args = ap.parse_args()
    truth = load_truth()
    if args.agent_root:
        root = CACHE / f"ans_{args.tag}"
        run_agent(Path(args.agent_root), root)
    else:
        root = Path(args.answers)
    answers = load_answers(root)
    report = {}
    for hyp in ("carry", "zero", "mean"):
        rows = {}
        for u in TT:
            if u not in answers:
                continue
            nv = naive_rows(u, hyp)
            rows[u] = composite(u, answers[u], truth[u], nv)
        report[hyp] = rows
    # print
    for hyp, rows in report.items():
        print(f"\n== naive hypothesis: {hyp}")
        print(f"{'unit':32s} {'score':>6s} {'pq':>6s} {'rawq':>6s} {'naiveq':>6s} {'iq':>6s} {'rawiq':>6s} {'cov':>5s}")
        for u, r in rows.items():
            f = lambda k: f"{r[k]:6.3f}" if isinstance(r.get(k), (int, float)) else "     -"  # noqa: E731
            print(f"{u:32s} {f('score')} {f('pq')} {f('raw_q')} {f('naive_q')} {f('iq')} {f('raw_iq')} {f('cov')[1:]}")
        d10 = [rows[u]["score"] for u in DEV10 if u in rows]
        a11 = [r["score"] for r in rows.values()]
        print(f"mean dev10 {statistics.fmean(d10):.4f} -> board {-0.27 + 1.27 * statistics.fmean(d10):.4f};"
              f" all11 {statistics.fmean(a11):.4f} -> board {-0.27 + 1.27 * statistics.fmean(a11):.4f}")
    (CACHE / f"report_{args.tag}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
