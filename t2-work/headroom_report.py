"""Tables for HEADROOM.md from out_headroom/headroom.pkl (headroom_decomp.py) and out_headroom/headroom_shapes.pkl (headroom_shapes.py).

    PYTHONUTF8=1 .venv/Scripts/python t2-work/headroom_report.py
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
D = pickle.loads((HERE / "out_headroom" / "headroom.pkl").read_bytes())
SH = pickle.loads((HERE / "out_headroom" / "headroom_shapes.pkl").read_bytes())
C = D["cards"]
X = {c["unit"]: c for c in SH["cards"]}
S, SC, PS, SHIFTS = D["scales"], list(D["scales_c"]), D["ps"], D["shifts"]
FAMS = ("F1", "F2", "F3", "F4")
ERAS = [(2003, 2009), (2010, 2014), (2015, 2018), (2019, 2021), (2022, 2024)]
FM = {f: np.array([c["family"] == f for c in C]) for f in FAMS}
VAL = np.array([c["split"] == "validation" for c in C])
YEAR = np.array([c["year"] for c in C])
Z = np.array([c["rms_z"] for c in C])


def row(name, v):
    v = np.asarray(v, float)
    return (f"| {name} | {v.mean():.3f} | " + " | ".join(f"{v[FM[f]].mean():.3f}" for f in FAMS)
            + f" | {v[VAL].mean():.3f} | {board(v[VAL].mean()):.2f} |")


def board(val65, x6=None):
    """Board estimate: 65 local validation cards + the 6 unscorable ones. x6 default: the 6 cards
    scale with the line's val65 the way they do for M0 (6.2 / 2.317) -- crude, for orientation."""
    x6 = val65 * 6.2 / 2.317 if x6 is None else x6
    return -(65 * val65 + 6 * x6) / 71


HDR = "| line | all 90 | F1 | F2 | F3 | F4 | val65 | board est. |\n|---|---|---|---|---|---|---|---|"


def best_idx(curves, mask):
    return int(np.argmin(curves[mask].mean(0)))


def main() -> None:
    comp = lambda key: np.array([c[key][3] for c in C])  # noqa: E731
    W = np.array([c["width"][:, 3] for c in C])
    VW = np.array([c["v4width"][:, 3] for c in C])
    print("## Lines\n")
    print(HDR)
    print(row("M0 exact (board reference)", comp("m0_exact")))
    print(row("M0 Gaussian clone, 2000 draws", comp("m0")))
    print(row("v4 10-06 rows (the image on the board)", comp("v4old")))
    print(row("v4 rev 3 (8250695)", comp("v4r3")))
    kg = int(np.argmin(W.mean(0)))
    print(row(f"M0 x one global width ({S[kg]:.2f}, hindsight)", W[:, kg]))
    fam_w = np.zeros(len(C))
    picks = {}
    for f in FAMS:
        k = best_idx(W, FM[f]); picks[f] = S[k]; fam_w[FM[f]] = W[FM[f], k]
    print(row("M0 x per-family width (hindsight: " + ", ".join(f"{f} {picks[f]:.2f}" for f in FAMS) + ")", fam_w))
    print(row("M0 x per-card width (oracle a)", W.min(1)))
    print(row("v4 rev 3 x per-card width (oracle a)", VW.min(1)))
    mag = np.array([X[c["unit"]]["mag2pt"][3] for c in C])
    print(row("sign-blind bound: +/-|y-m| two-point (magnitude+shape known)", mag))
    i1 = SC.index(1.0)
    print(row("sign known, M0 width (oracle b)", [c["sign_p1.0"][i1, 0, 3] for c in C]))
    print(row("sign known + per-card width", [c["sign_p1.0"][:, 0, 3].min() for c in C]))
    print(row("centre known, M0 width (oracle d)", comp("centre")))
    print(row("centre known + per-card width", [c["centre_w"][:, 3].min() for c in C]))

    # ---------------------------------------------------------------- decomposition
    print("\n## Decomposition (mean over 90 cards of the weighted parts; composite = m + j + t - clip)\n")
    print("| line | composite | marginal | joint | tail | clip removes | cards at 8 | F1 | F2 | F3 | F4 | top-10 share |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, get in (("M0", lambda c: c["m0_exact"]), ("v4 10-06", lambda c: c["v4old"]), ("v4 rev 3", lambda c: c["v4r3"]),
                      ("M0 per-card width", lambda c: c["width"][c["width"][:, 3].argmin()]),
                      ("v4 per-card width", lambda c: c["v4width"][c["v4width"][:, 3].argmin()])):
        P = np.array([get(c) for c in C])
        clip = P[:, :3].sum(1) - P[:, 3]
        print(f"| {name} | {P[:, 3].mean():.3f} | {P[:, 0].mean():.3f} | {P[:, 1].mean():.3f} | {P[:, 2].mean():.3f} | "
              f"{clip.mean():.3f} | {int(np.sum(P[:, 3] >= 7.999))} | "
              + " | ".join(f"{P[FM[f], 3].sum() / len(C):.3f}" for f in FAMS)
              + f" | {np.sort(P[:, 3])[-10:].sum() / P[:, 3].sum():.2f} |")
    print("\n(F1..F4 columns = that family's contribution to the 90-card mean.)\n")
    print("## Loss floor of a centred forecast vs the move size z (rms over cells of (y - m)/sd_M0)\n")
    print("| rms z | n | M0 | v4 rev 3 | per-card width oracle | oracle / z | sign-blind 2-pt bound | sign known + width |")
    print("|---|---|---|---|---|---|---|---|")
    sk = np.array([c["sign_p1.0"][:, 0, 3].min() for c in C])
    for lo, hi in ((0, .5), (.5, 1), (1, 1.5), (1.5, 2), (2, 3), (3, 5), (5, 99)):
        m = (Z >= lo) & (Z < hi)
        print(f"| {lo}-{hi if hi < 99 else 'inf'} | {m.sum()} | {comp('m0_exact')[m].mean():.2f} | {comp('v4r3')[m].mean():.2f} | "
              f"{W.min(1)[m].mean():.2f} | {np.mean(W.min(1)[m] / np.maximum(Z[m], 1e-9)):.2f} | {mag[m].mean():.2f} | {sk[m].mean():.2f} |")

    # ---------------------------------------------------------------- -1.34
    print("\n## What -1.34 needs (board = -(65 x val65 + 6 x x6) / 71)\n")
    print("| mean on the 6 unscorable cards (x6) | val65 needed for -1.34 |\n|---|---|")
    for x6 in (8.0, 6.2, 5.86, 4.0, 2.0):
        print(f"| {x6} | {(1.34 * 71 - 6 * x6) / 65:.3f} |")
    print("\n(6.2 = M0's implied mean on them from its -2.6412 row; 5.86 = v4 10-06 implied from our -2.35.)")

    # ---------------------------------------------------------------- direction signal
    print("\n## Direction signal of accuracy p (M0 centre, per-family width fixed at its no-signal hindsight value)\n")
    sfam = {}
    for f in FAMS:
        base = np.array([[c["sign_p0.5"][k, 0, 3] for k in range(len(SC))] for c in np.array(C)[FM[f]]])
        sfam[f] = int(np.argmin(base.mean(0)))
    print("per-family width used: " + ", ".join(f"{f} {SC[sfam[f]]}" for f in FAMS) + "\n")
    print("| use of the signal | p* break-even (all) | F1 | F2 | F3 | F4 | gain at p=0.6 | p=0.7 | p=0.8 | p=1 | val65 at p=0.7 | val65 at p=1 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    B = np.array([c["sign_p0.5"][sfam[c["family"]], 0, 3] for c in C])

    def exp_loss(R, Wr, p):
        return p * R + (1 - p) * Wr

    for d in SHIFTS:
        R = np.array([c[f"shift_{d}"][sfam[c["family"]], 0, 3] for c in C])
        Wr = np.array([c[f"shift_{d}"][sfam[c["family"]], 1, 3] for c in C])
        pstar = lambda m: (Wr[m] - B[m]).sum() / max((Wr[m] - R[m]).sum(), 1e-9)  # noqa: E731
        allm = np.ones(len(C), bool)
        g = {p: (B - exp_loss(R, Wr, p)).mean() for p in (0.6, 0.7, 0.8, 1.0)}
        print(f"| centre shift {d} sd toward the call | {pstar(allm):.2f} | " + " | ".join(f"{pstar(FM[f]):.2f}" for f in FAMS)
              + " | " + " | ".join(f"{g[p]:+.3f}" for p in (0.6, 0.7, 0.8, 1.0))
              + f" | {exp_loss(R, Wr, 0.7)[VAL].mean():.3f} | {R[VAL].mean():.3f} |")
    # calibrated mixture: forecast puts weight p on the called side (M0 conditioned on sign)
    E_p = {}
    for p in PS:
        R = np.array([c[f"sign_p{p}"][sfam[c["family"]], 0, 3] for c in C])
        Wr = np.array([c[f"sign_p{p}"][sfam[c["family"]], 1, 3] for c in C])
        E_p[p] = exp_loss(R, Wr, p)
    pst = next((p for p in PS[1:] if E_p[p].mean() < B.mean()), None)
    pfam = {f: next((p for p in PS[1:] if E_p[p][FM[f]].mean() < B[FM[f]].mean()), None) for f in FAMS}
    print("| calibrated split: weight p on the called side | " + (f"{pst}" if pst else ">1") + " | "
          + " | ".join(str(pfam[f]) for f in FAMS) + " | "
          + " | ".join(f"{(B - E_p[p]).mean():+.3f}" for p in (0.6, 0.7, 0.8, 1.0))
          + f" | {E_p[0.7][VAL].mean():.3f} | {E_p[1.0][VAL].mean():.3f} |")
    print(f"\nno-signal baseline at these widths: all {B.mean():.3f}, val65 {B[VAL].mean():.3f}")
    print("\ncalibrated split, expected loss by accuracy p (all / val65): "
          + "; ".join(f"{p}: {E_p[p].mean():.3f}/{E_p[p][VAL].mean():.3f}" for p in PS))

    # ---------------------------------------------------------------- magnitude signal
    print("\n## Magnitude signal: 'large move' (rms z >= 2) vs not, M0 centre, one width per bucket\n")
    big = Z >= 2
    kb, ks = best_idx(W, big), best_idx(W, ~big)
    print(f"bucket widths (hindsight, pooled): large {S[kb]:.2f}, small {S[ks]:.2f}; {big.sum()} large cards "
          f"({', '.join(f'{f} {int(big[FM[f]].sum())}/{int(FM[f].sum())}' for f in FAMS)})\n")
    print("| accuracy p | all | val65 | vs per-family width (no signal) |\n|---|---|---|---|")
    for p in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        right = np.where(big, W[:, kb], W[:, ks])
        wrong = np.where(big, W[:, ks], W[:, kb])
        e = p * right + (1 - p) * wrong
        print(f"| {p} | {e.mean():.3f} | {e[VAL].mean():.3f} | {e.mean() - fam_w.mean():+.3f} |")
    # per family x bucket
    e2 = np.zeros(len(C))
    for f in FAMS:
        for m in (FM[f] & big, FM[f] & ~big):
            if m.sum():
                e2[m] = W[m, best_idx(W, m)]
    print(f"\nperfect 2-bucket signal with per-family x bucket widths: all {e2.mean():.3f}, val65 {e2[VAL].mean():.3f}"
          f" (per-card oracle {W.min(1).mean():.3f})")

    # ---------------------------------------------------------------- honest shapes
    print("\n## Honest shape: lognormal scale mixture s0 x exp(sig g) per family; leave-one-era-out\n")
    s0, sigs = SH["s0"], SH["sigs"]
    for centre in ("mix", "mix_v4"):
        M = np.array([X[c["unit"]][centre][:, :, 3] for c in C])  # cards x s0 x sig
        for label, sig_ok in (("fixed width (sig=0)", [0]), ("width + scale mixture", list(range(len(sigs))))):
            ins = np.zeros(len(C)); ho = np.zeros(len(C)); ch = {}
            for f in FAMS:
                sub = M[FM[f]][:, :, sig_ok].mean(0)
                a, b = np.unravel_index(np.argmin(sub), sub.shape)
                ch[f] = (s0[a], sigs[sig_ok[b]])
                ins[FM[f]] = M[FM[f]][:, a, sig_ok[b]]
                for lo, hi in ERAS:
                    te = FM[f] & (YEAR >= lo) & (YEAR <= hi)
                    tr = FM[f] & ~((YEAR >= lo) & (YEAR <= hi))
                    if te.sum() == 0:
                        continue
                    sub = M[tr][:, :, sig_ok].mean(0)
                    a2, b2 = np.unravel_index(np.argmin(sub), sub.shape)
                    ho[te] = M[te][:, a2, sig_ok[b2]]
            fwd_tr, fwd_te = YEAR <= 2018, YEAR >= 2019
            fw = np.zeros(len(C))
            for f in FAMS:
                sub = M[FM[f] & fwd_tr][:, :, sig_ok].mean(0)
                a2, b2 = np.unravel_index(np.argmin(sub), sub.shape)
                fw[FM[f]] = M[FM[f]][:, a2, sig_ok[b2]]
            print(f"- centre {'M0' if centre == 'mix' else 'v4 rev 3'}, {label}: in-sample {ins.mean():.3f} (val65 {ins[VAL].mean():.3f}), "
                  f"held-out eras {ho.mean():.3f}, forward>=2019 {fw[fwd_te].mean():.3f}; picks "
                  + ", ".join(f"{f} s0 {ch[f][0]} sig {ch[f][1]}" for f in FAMS)
                  + "; per family held-out " + ", ".join(f"{f} {ho[FM[f]].mean():.3f}" for f in FAMS))
    print(f"\n(v4 rev 3 forward>=2019: {comp('v4r3')[YEAR >= 2019].mean():.3f}; M0 forward {comp('m0_exact')[YEAR >= 2019].mean():.3f})")

    # ---------------------------------------------------------------- heaviest cards
    print("\n## Heaviest cards (M0 composite), v4 rev 3 and bounds\n")
    print("| card | split | rms z | M0 | v4 10-06 | v4 rev 3 | per-card width | sign-blind 2pt | sign known + width |")
    print("|---|---|---|---|---|---|---|---|---|")
    for i in np.argsort(-comp("m0_exact"))[:20]:
        c = C[i]
        print(f"| {c['unit']} | {c['split'][:3]} | {c['rms_z']:.1f} | {c['m0_exact'][3]:.2f} | {c['v4old'][3]:.2f} | {c['v4r3'][3]:.2f} | "
              f"{W[i].min():.2f} | {mag[i]:.2f} | {sk[i]:.2f} |")


if __name__ == "__main__":
    main()
