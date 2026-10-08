"""Build jpsim/fastsim.pyx into an extension module next to its source.

    python t3-work/tools/build_fastsim.py [--engine-dir t3-work/engine] [--cxx g++] [--keep-cpp]

Windows dev box: mingw-w64 g++ linking against python311.lib (the CPython 3.11 of the venv that runs
this script). Linux (Docker build stage): g++ from the builder image. Flags are the same on both:
-O2, no fast-math, no FMA contraction (bit-exact float arithmetic is the whole point).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import sysconfig


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine-dir", default=str(pathlib.Path(__file__).resolve().parents[1] / "engine"))
    ap.add_argument("--cxx", default=os.environ.get("CXX", "g++"))
    ap.add_argument("--keep-cpp", action="store_true")
    ap.add_argument("--opt", default="-O2")
    args = ap.parse_args()

    import numpy as np

    engine = pathlib.Path(args.engine_dir).resolve()
    pyx = engine / "jpsim" / "fastsim.pyx"
    cpp = pyx.with_suffix(".cpp")
    ext = sysconfig.get_config_var("EXT_SUFFIX")
    out = pyx.with_name("fastsim" + ext)
    subprocess.check_call([sys.executable, "-m", "cython", "-3", "--cplus", "-o", str(cpp), str(pyx)])
    inc = sysconfig.get_paths()["include"]
    cmd = [args.cxx, args.opt, "-shared", "-fPIC", "-fwrapv", "-fno-strict-aliasing", "-ffp-contract=off",
           "-std=c++17", "-w", "-DNPY_NO_DEPRECATED_API=NPY_1_7_API_VERSION",
           "-I", inc, "-I", np.get_include(), str(cpp), "-o", str(out)]
    if sys.platform == "win32":
        libs = os.path.join(sysconfig.get_config_var("installed_base"), "libs")
        cmd += ["-DMS_WIN64", "-L", libs, f"-lpython{sys.version_info.major}{sys.version_info.minor}"]
        cmd += ["-static"]  # mingw runtime (libstdc++, libgcc, winpthread) linked in; python311.lib stays an import lib
    print(" ".join(cmd), flush=True)
    subprocess.check_call(cmd)
    if not args.keep_cpp:
        cpp.unlink()
    print("built", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
