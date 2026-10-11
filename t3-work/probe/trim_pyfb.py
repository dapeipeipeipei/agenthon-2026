"""Trim the relocated Python fallback (/opt/pyfb) to what the Python engine actually loads.

Run in the P3/P4 build stage with the build image's own python:
    python trim_pyfb.py <stage root> <synthetic_check.py>

1. Runs the relocated interpreter (JPSIM_KERNEL=py, -O, `python -X importtime`, LD_DEBUG=files)
   on every synthetic scenario the Python engine can run (incl. the kernel's fallback cases:
   extra keys, gbm oracle, gamma latency, no latency_config = scipy line-distance model) and on a
   two-scenario batch; records every imported module and every shared object loaded, plus the
   output hashes.
2. Deletes: tests / include / headers / Cython sources / type stubs; numpy, scipy and pyarrow
   sub-packages and extension modules / bundled libraries that were never imported or loaded;
   large unused stdlib packages and lib-dynload modules; every .pyc except the -O (.opt-1) ones;
   /opt/pyfb/lib system libraries no remaining ELF file needs. Strips the site-packages .so files
   and libpython (--strip-unneeded).
3. Re-runs everything and asserts byte-identical outputs and an unchanged module set.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(sys.argv[1])
PYFB = ROOT / "opt/pyfb"
SITE = PYFB / "lib/python3.11/site-packages"
STD = PYFB / "lib/python3.11"
LIB = PYFB / "lib"

spec = importlib.util.spec_from_file_location("synthetic_check", sys.argv[2])
syn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(syn)


def env():
    return {
        "LD_LIBRARY_PATH": "/opt/pyfb/lib", "PYTHONHOME": "/opt/pyfb", "PYTHONPATH": "/opt/jpsim",
        "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1", "HOME": "/tmp", "JPSIM_KERNEL": "py", "PATH": "/usr/bin:/bin",
        "LD_DEBUG": "files",
    }


def run_all(tag: str) -> tuple[set[str], set[str], dict[str, str]]:
    mods: set[str] = set()
    sos: set[str] = set()
    outs: dict[str, str] = {}
    work = pathlib.Path(tempfile.mkdtemp(prefix=f"trim-{tag}-"))
    jobs = []
    batch_dir = work / "batch"
    batch_dir.mkdir()
    k = 0
    for name, scen, _fb, can in syn.make_scenarios():
        if not can:
            continue
        cfg = work / f"{name}.json"
        cfg.write_text(json.dumps(scen))
        jobs.append((name, ["simulate", "--config", str(cfg), "--out", str(work / name / "trace.parquet")], work / name))
        if k < 2 and name in ("syn-extra-keys", "syn-no-latency-config"):
            sub = batch_dir / f"sub_{k:02d}"
            sub.mkdir()
            (sub / "scenario.json").write_text(json.dumps(scen))
            k += 1
    jobs.append(("batch", ["simulate-batch", "--batch-dir", str(batch_dir), "--out-dir", str(work / "batch-out")], work / "batch-out"))
    for name, args, out in jobs:
        out.mkdir(parents=True, exist_ok=True)
        cmd = ["/opt/pyfb/bin/python3.11", "-O", "-X", "importtime", "-m", "jpsim.cli"] + args
        p = subprocess.run(cmd, env=env(), capture_output=True, text=True)
        if p.returncode != 0:
            print(p.stderr[-4000:])
            raise SystemExit(f"[{tag}] {name}: rc={p.returncode}")
        for line in p.stderr.splitlines():
            m = re.match(r"import time:\s+\d+ \|\s+\d+ \|\s+(\S.*)$", line)
            if m:
                mods.add(m.group(1).strip())
            m = re.search(r"calling init: (\S+)", line)
            if m:
                sos.add(os.path.realpath(m.group(1)))
        for f in sorted(out.rglob("*.parquet")):
            outs[f"{name}/{f.relative_to(out)}"] = hashlib.sha256(f.read_bytes()).hexdigest()
    shutil.rmtree(work)
    # extension modules are dlopen'ed: "calling init" covers them; also record them by module file
    return mods, sos, outs


def du(p: pathlib.Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def rm(p: pathlib.Path):
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists() or p.is_symlink():
        p.unlink()


print(f"before: /opt/pyfb {du(PYFB) / 1e6:.1f} MB")
mods, sos, outs = run_all("before")
print(f"modules imported: {len(mods)}; shared objects initialised: {len(sos)}; outputs: {len(outs)}")
tops = {m.split(".")[0] for m in mods}

# --- 1. generic junk everywhere under site-packages
for pat in ("tests", "test", "include", "benchmarks", "doc", "docs"):
    for d in list(SITE.rglob(pat)):
        if d.is_dir() and ".".join(d.relative_to(SITE).parts) not in mods:
            rm(d)
for ext in ("*.pyi", "*.pxd", "*.pyx", "*.pxi", "*.h", "*.hpp", "*.cc", "*.cpp", "*.c", "*.f", "*.f90", "*.cmake", "*.a"):
    for f in list(SITE.rglob(ext)):
        rm(f)
for d in ("pyarrow/src", "pyarrow/vendored", "numpy/f2py", "numpy/distutils", "numpy/_pyinstaller", "numpy/array_api", "numpy/doc",
          "scipy/_lib/tests", "pyarrow/interchange"):
    if (SITE / d).exists() and d.replace("/", ".") not in mods:
        rm(SITE / d)

# --- 2. numpy / scipy / pyarrow sub-packages never imported
for pkg in ("numpy", "scipy", "pyarrow"):
    base = SITE / pkg
    for d in list(base.rglob("*")):
        if not d.is_dir() or d.name == "__pycache__" or not d.exists():
            continue
        if not (d / "__init__.py").exists():
            continue
        dotted = ".".join(d.relative_to(SITE).parts)
        if dotted not in mods:
            rm(d)
    # extension modules (.so) inside the package tree never loaded
    for f in list(base.rglob("*.so")):
        if os.path.realpath(f) not in sos:
            rm(f)
    for libdir in (SITE / f"{pkg}.libs",):
        if libdir.exists():
            for f in list(libdir.iterdir()):
                if os.path.realpath(f) not in sos:
                    rm(f)
# pyarrow bundles libarrow*.so.* next to its modules
for f in list((SITE / "pyarrow").glob("lib*.so*")):
    if os.path.realpath(f) not in sos:
        rm(f)
for d in SITE.iterdir():
    if d.is_dir() and d.name.split("-")[0] in ("pip", "setuptools", "wheel", "_distutils_hack"):
        rm(d)

# --- 3. stdlib: large packages the engine never imports
for d in ("test", "idlelib", "tkinter", "turtledemo", "lib2to3", "ensurepip", "pydoc_data", "sqlite3", "distutils",
          "venv", "curses", "dbm", "xmlrpc", "wsgiref", "msilib", "unittest/test", "ctypes/test", "tomllib/tests"):
    p = STD / d
    if p.exists() and d.split("/")[0] not in tops:
        rm(p)
for p in list(STD.glob("config-3.11*")):
    rm(p)
for f in list((STD / "lib-dynload").glob("*.so")):
    name = f.name.split(".")[0]
    if os.path.realpath(f) not in sos and name not in mods:
        if re.match(r"_(tkinter|sqlite3|curses|curses_panel|dbm|gdbm|codecs_\w+|testcapi|testbuffer|testimportmultiple|"
                    r"testinternalcapi|testmultiphase|testclinic|xxtestfuzz|ctypes_test|test\w*)$|xx|audioop|nis|ossaudiodev|spwd|readline", name):
            rm(f)

# --- 4. byte-code: only the -O variants are ever read (the launcher runs python -O)
for f in list(PYFB.rglob("*.pyc")) + list((ROOT / "opt/jpsim").rglob("*.pyc")):
    if ".opt-1." not in f.name:
        rm(f)

# --- 5. strip the site-packages ELF objects and libpython
elves = [f for f in SITE.rglob("*") if f.is_file() and (f.suffix == ".so" or ".so." in f.name)]
elves.append(LIB / "libpython3.11.so.1.0")
for f in elves:
    subprocess.run(["strip", "--strip-unneeded", str(f)], check=False, capture_output=True)

# --- 6. system libraries in /opt/pyfb/lib that no remaining ELF file needs
remaining = [f for f in PYFB.rglob("*") if f.is_file() and (f.suffix == ".so" or ".so." in f.name or f.name == "python3.11")]
need: set[str] = set()
for i in range(0, len(remaining), 50):
    p = subprocess.run(["ldd"] + [str(x) for x in remaining[i:i + 50]], capture_output=True, text=True,
                       env={"LD_LIBRARY_PATH": str(LIB), "PATH": "/usr/bin:/bin"})
    for m in re.finditer(r"=>\s+(\S+)", p.stdout):
        need.add(os.path.basename(m.group(1)))
for f in list(LIB.iterdir()):
    if f.is_file() and ".so" in f.name and f.name not in need and not f.name.startswith(("ld-linux", "libpython")):
        if os.path.realpath(f) not in sos:
            rm(f)

for d in list(PYFB.rglob("__pycache__")):
    if d.is_dir() and not any(d.iterdir()):
        d.rmdir()

print(f"after:  /opt/pyfb {du(PYFB) / 1e6:.1f} MB; /opt/jpsim {du(ROOT / 'opt/jpsim') / 1e6:.1f} MB")
mods2, sos2, outs2 = run_all("after")
missing = sorted(mods - mods2)
if outs2 != outs:
    raise SystemExit(f"outputs changed after trimming: {sorted(k for k in outs if outs.get(k) != outs2.get(k))}")
if missing:
    raise SystemExit(f"modules no longer imported after trimming: {missing[:20]}")
print(f"trim check ok: {len(outs)} outputs byte-identical, {len(mods2)} modules")
