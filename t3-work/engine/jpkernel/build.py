"""Build the jpkernel shared library next to this file.

    python build.py [--cxx g++]

Linux: libjpkernel.so (used by the Docker image, built in the image's build stage).
Windows: jpkernel.dll (MinGW-w64 g++; the C runtime's log/exp/pow are resolved from ucrtbase.dll
at run time, see jpkernel.cpp).

Flags matter for bit-exactness: no fast-math, no FMA contraction, the baseline x86-64 ISA (the
reference's numpy uses the same scalar libm calls; a fused multiply-add would change the last bit).
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cxx", default="g++")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    here = pathlib.Path(__file__).resolve().parent
    src = here / "jpkernel.cpp"
    win = sys.platform == "win32"
    out = pathlib.Path(args.out) if args.out else here / ("jpkernel.dll" if win else "libjpkernel.so")
    cmd = [args.cxx, "-O2", "-std=c++17", "-shared", "-fPIC", "-fno-fast-math", "-ffp-contract=off",
           "-fvisibility=hidden", "-o", str(out), str(src)]
    if win:
        cmd += ["-static", "-Wl,--exclude-all-symbols"]
    else:
        cmd += ["-Wl,--as-needed"]
    print(" ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
