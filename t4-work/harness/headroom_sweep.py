"""Sweeps over our Development answers against verified outcomes (LOCAL DIAGNOSTICS ONLY):
interval width multiplier k, point shrinkage toward the naive (carry) point, and oracle swaps.
    PYTHONUTF8=1 ../.venv/Scripts/python harness/headroom_sweep.py [--tag dev]
"""
from __future__ import annotations

import argparse
import copy
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from headroom_eval import CACHE, DEV10, TT, composite, load_answers, load_truth, naive_rows, task  # noqa: E402
from qfbench2_common.scoring import faithfulness as F  # noqa: E402


def scaled(rows: dict, k: float) -> dict:
    out = copy.deepcopy(rows)
    for r in out.values():
        p = float(r.get("point_forecast", 0.0)); iv = r["interval"]
        lo, hi = iv["lo"], iv["hi"]
        r["interval"] = {"lo": p - k * (p - lo), "hi": p + k * (hi - p)}
    return out


def shrunk(unit: str, rows: dict, w: float) -> dict:
    nv = naive_rows(unit, "carry")
    out = copy.deepcopy(rows)
    for eid, r in out.items():
        p = float(r.get("point_forecast", 0.0)); n = nv[eid][1]
        q = w * p + (1 - w) * n
        iv = r["interval"]
        r["point_forecast"] = q
        r["interval"] = {"lo": iv["lo"] + (q - p), "hi": iv["hi"] + (q - p)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", default="dev"); a = ap.parse_args()
    truth = load_truth(); A = load_answers(CACHE / f"ans_{a.tag}")
    numeric = [u for u in TT if all(t.get("y") is not None for t in truth[u].values())]
    print("== E1 interval width multiplier k: mean IS(k)/IS(1) and coverage, per numeric unit")
    ks = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
    print(f"{'unit':32s} " + " ".join(f"k={k:<5}" for k in ks))
    for u in numeric:
        ids = [e["entity_id"] for e in task(u)["entities"]]
        y = [float(truth[u][i]["y"]) for i in ids]
        base = None; cells = []
        for k in ks:
            r = scaled(A[u], k)
            IS = F.mean_interval_score([r[i]["interval"]["lo"] for i in ids], [r[i]["interval"]["hi"] for i in ids], y, 0.9)
            cov = sum(r[i]["interval"]["lo"] <= yy <= r[i]["interval"]["hi"] for i, yy in zip(ids, y)) / len(ids)
            base = IS if k == 1.0 else base
            cells.append((IS, cov))
        base = cells[ks.index(1.0)][0]
        print(f"{u:32s} " + " ".join(f"{IS / base:4.2f}/{cov:.1f}" for IS, cov in cells))
    print("\n== E2 point shrinkage toward carry naive: score (carry hypothesis) for w in 0..1.2")
    ws = [0.0, 0.25, 0.5, 0.75, 1.0, 1.2]
    for u in [u for u in TT if TT[u][0] == "regression"]:
        nv = naive_rows(u, "carry")
        print(f"{u:32s} " + " ".join(f"w={w}:{composite(u, shrunk(u, A[u], w), truth[u], nv)['pq']:.3f}" for w in ws))
    print("\n== E3 oracle swaps (carry hypothesis): ours / oracle-label-or-point (our band recentred) / full oracle")
    tot = {"ours": [], "pt": []}
    for u in TT:
        nv = naive_rows(u, "carry")
        ours = composite(u, A[u], truth[u], nv)["score"]
        orc = copy.deepcopy(A[u])
        for eid, r in orc.items():
            t = truth[u][eid]
            if t.get("label"):
                r["label"] = t["label"]
            if t.get("y") is not None:
                p = float(r.get("point_forecast", 0.0)); y = float(t["y"])
                r["point_forecast"] = y
                r["interval"] = {"lo": r["interval"]["lo"] + y - p, "hi": r["interval"]["hi"] + y - p}
        s2 = composite(u, orc, truth[u], nv)
        if u in DEV10:
            tot["ours"].append(ours); tot["pt"].append(s2["score"])
        print(f"{u:32s} ours {ours:.3f}  perfect-point/label+our-band {s2['score']:.3f} (iq {s2['iq'] if s2['iq'] is None else round(s2['iq'],3)})  full-oracle 1.000")
    print(f"dev10 mean: ours {statistics.fmean(tot['ours']):.3f}  perfect-points-our-bands {statistics.fmean(tot['pt']):.3f}")


if __name__ == "__main__":
    main()
