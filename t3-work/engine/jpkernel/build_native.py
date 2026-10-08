"""Build jpsim-native (Linux only): the Python-free `simulate` / `simulate-batch` binary.

    python build_native.py [--out /opt/jpsim/bin/jpsim-native] [--cxx g++]

Links against the libarrow / libparquet shared libraries of the *installed* pyarrow wheel (the
exact writer code the reference used, 15.0.2), with an rpath pointing at the pyarrow package
directory so no copy of the libraries is needed. The headers come from ``pyarrow.get_include()``.
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
    args = ap.parse_args()
    import pyarrow as pa

    if pa.__version__ != "15.0.2":
        print(f"pyarrow {pa.__version__} != 15.0.2: the reference writer is 15.0.2", file=sys.stderr)
        return 1
    pa.create_library_symlinks()
    inc = pa.get_include()
    libdirs = pa.get_library_dirs()
    here = pathlib.Path(__file__).resolve().parent
    out = pathlib.Path(args.out) if args.out else here / "jpsim-native"
    cmd = [args.cxx, "-O2", "-std=c++17", "-fno-fast-math", "-ffp-contract=off", "-DJPK_NO_EXPORTS",
           "-I" + inc, "-o", str(out), str(here / "jpsim_native.cpp")]
    for d in libdirs:
        cmd += ["-L" + d, "-Wl,-rpath," + d]
    cmd += ["-lparquet", "-larrow"]
    print(" ".join(cmd))
    rc = subprocess.call(cmd)
    if rc == 0:
        os.chmod(out, 0o755)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
