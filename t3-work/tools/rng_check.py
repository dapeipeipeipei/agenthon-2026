"""Bit-exact check of jpkernel's numpy-legacy RNG against numpy itself.

    python rng_check.py [--n 1000000] [--seeds 0 1 12345 4294967295]

For every generator the kernel uses (and the parameters the Track 3 units use), draws n values
from a fresh RandomState(seed) in numpy and in the kernel and requires identical bits.
"""

from __future__ import annotations

import argparse
import math
import sys

import numpy as np

sys.path.insert(0, __file__.rsplit("tools", 1)[0] + "engine")
from jpsim import kernel  # noqa: E402


def py_round_vec(x: np.ndarray) -> np.ndarray:
    return np.asarray([float(round(float(v))) for v in x.tolist()], dtype=np.float64)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1_000_000)
    ap.add_argument("--seeds", type=int, nargs="*", default=[0, 1, 12345, 1806084051, 4294967295])
    args = ap.parse_args()
    if not kernel.available():
        print("kernel not available:", kernel.load_error())
        return 2
    n = args.n
    cases = [
        ("next_u32", 0, 0.0, 0.0, lambda rs: rs.randint(0, 2**32, size=n, dtype=np.uint64).astype(np.float64)),
        ("random_double", 1, 0.0, 0.0, lambda rs: rs.random_sample(n)),
        ("normal(10,2)", 2, 10.0, 2.0, lambda rs: np.asarray([rs.normal(10.0, 2.0) for _ in range(n)])),
        ("normal(100000,31.62)", 2, 100000.0, math.sqrt(1000.0),
         lambda rs: np.asarray([rs.normal(100000, math.sqrt(1000.0)) for _ in range(n)])),
        ("lognormal(ln500,0.3)", 3, float(np.log(500.0)), 0.3,
         lambda rs: np.asarray([rs.lognormal(mean=float(np.log(500.0)), sigma=0.3) for _ in range(n)])),
        ("uniform(100,2000)", 4, 100.0, 2000.0, lambda rs: np.asarray([rs.uniform(100.0, 2000.0) for _ in range(n)])),
        ("exponential(2e9)", 5, 2e9, 0.0, lambda rs: np.asarray([rs.exponential(scale=2e9) for _ in range(n)])),
        ("exponential(3.6e17)", 5, 1.0 / 2.77778e-18, 0.0,
         lambda rs: np.asarray([rs.exponential(scale=1.0 / 2.77778e-18) for _ in range(n)])),
        ("pareto(1.5)", 6, 1.5, 0.0, lambda rs: np.asarray([rs.pareto(1.5) for _ in range(n)])),
        ("randint(0,2)", 7, 2.0, 0.0, lambda rs: np.asarray([float(rs.randint(0, 2)) for _ in range(n)])),
        ("randint(0,6)", 7, 6.0, 0.0, lambda rs: np.asarray([float(rs.randint(0, 6)) for _ in range(n)])),
        ("randint(0,11)", 7, 11.0, 0.0, lambda rs: np.asarray([float(rs.randint(0, 11)) for _ in range(n)])),
        ("seed draw randint(0,2**32,uint64)", 8, 0.0, 0.0,
         lambda rs: np.asarray([float(rs.randint(low=0, high=2**32, dtype="uint64")) for _ in range(n)])),
        ("round(normal(11,2))", 9, 11.0, 2.0,
         lambda rs: np.asarray([float(round(rs.normal(11.0, 2.0))) for _ in range(n)])),
    ]
    bad = 0
    for seed in args.seeds:
        for name, kind, p1, p2, ref_fn in cases:
            rs = np.random.RandomState(seed=np.uint64(seed))
            ref = np.asarray(ref_fn(rs), dtype=np.float64)
            ours = kernel.rng_test(seed, kind, p1, p2, n)
            same = np.array_equal(ref.view(np.int64), ours.view(np.int64))
            if not same:
                bad += 1
                i = int(np.argmax(ref.view(np.int64) != ours.view(np.int64)))
                print(f"MISMATCH seed={seed} {name}: first at {i}: ref={ref[i]!r} ours={ours[i]!r}")
            else:
                print(f"ok seed={seed} {name}")
    # the mixed-generator sequence a NoiseTrader draws: normal, randint(0,2), randint(0,6) per act
    for seed in args.seeds:
        rs = np.random.RandomState(seed=np.uint64(seed))
        ref = []
        for _ in range(n // 10):
            ref.append(float(round(rs.normal(10.0, 2.0))))
            ref.append(float(rs.randint(0, 2)))
            ref.append(float(rs.randint(0, 6)))
        ours = kernel.rng_test(seed, 100, 10.0, 2.0, len(ref))
        if not np.array_equal(np.asarray(ref).view(np.int64), ours.view(np.int64)):
            bad += 1
            print(f"MISMATCH seed={seed} noise-trader sequence")
        else:
            print(f"ok seed={seed} noise-trader sequence")
    print("FAILURES:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
