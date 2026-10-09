"""Build jpsim-native: the Python-free `simulate` / `simulate-batch` binary.

    python build_native.py [--out <path>] [--cxx g++] [--arrow] [--no-static]

Default (lean): the self-contained writer in jpparquet.h, a fully static executable (glibc,
libstdc++, no shared libraries at all: on Linux `ldd` says "not a dynamic executable"), sections
garbage-collected. The parquet files are content-identical to the reference's (not byte-identical).

--arrow: link the libarrow / libparquet shared libraries of the *installed* pyarrow 15.0.2 wheel
(the exact writer code the reference used), rpath = the pyarrow package directory; the files are
then byte-identical to the reference's. Linux only (the Windows wheel is MSVC-built).

Flags that matter for bit-exactness of the simulation (both variants): -O2, no fast-math, no FMA
contraction, baseline x86-64 ISA (the reference's numpy calls the same scalar libm functions).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cxx", default="g++")
    ap.add_argument("--out", default=None)
    ap.add_argument("--arrow", action="store_true", help="reference writer: link the pyarrow wheel's libarrow/libparquet")
    ap.add_argument("--no-static", action="store_true", help="lean variant linked dynamically (for LD_DEBUG comparisons)")
    args = ap.parse_args()
    here = pathlib.Path(__file__).resolve().parent
    win = sys.platform == "win32"
    out = pathlib.Path(args.out) if args.out else here / ("jpsim-native.exe" if win else "jpsim-native")
    common = [args.cxx, "-O2", "-std=c++17", "-fno-fast-math", "-ffp-contract=off", "-DJPK_NO_EXPORTS"]
    if args.arrow:
        import pyarrow as pa

        if pa.__version__ != "15.0.2":
            print(f"pyarrow {pa.__version__} != 15.0.2: the reference writer is 15.0.2", file=sys.stderr)
            return 1
        pa.create_library_symlinks()
        cmd = common + ["-DJPSIM_ARROW_WRITER", "-I" + pa.get_include(), "-o", str(out), str(here / "jpsim_native.cpp")]
        for d in pa.get_library_dirs():
            cmd += ["-L" + d, "-Wl,-rpath," + d]
        cmd += ["-lparquet", "-larrow", "-pthread"]
    else:
        cmd = common + ["-ffunction-sections", "-fdata-sections", "-o", str(out), str(here / "jpsim_native.cpp")]
        if args.no_static:
            cmd += ["-Wl,--gc-sections", "-Wl,-O1", "-Wl,--as-needed", "-pthread"]
        else:
            cmd += ["-static", "-Wl,--gc-sections", "-Wl,-O1", "-pthread"]
            if not win:
                # glibc's static libpthread needs the whole archive for std::thread (weak symbols)
                cmd += ["-Wl,--whole-archive", "-lpthread", "-Wl,--no-whole-archive"]
    print(" ".join(cmd))
    rc = subprocess.call(cmd)
    if rc == 0 and not win:
        os.chmod(out, 0o755)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
