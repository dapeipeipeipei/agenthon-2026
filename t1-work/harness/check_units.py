"""Run each public unit's own checker (checks/test_outputs.py, what the platform's g3 runs) against
an output folder produced by run_units.py, and report the local pass rate.

The checkers hardcode the platform's paths (/app/output, /output, /input, /app/data,
/tests/reference_data, /logs/verifier, and the unit Dockerfile's COPY targets such as
/app/curve_data.json). This script presents them: on Linux with symlinks (sudo when not root), on
Windows with directory junctions and file copies at the drive root (C:\\app, C:\\input, ...).

    python harness/check_units.py --out-root harness/out/units [--units ...]

Writes <out-root>/checks.json; prints a table and `local pass K/N`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
ROOT = WORK.parent
def _default_kit() -> Path:
    for cand in (Path(os.environ.get("JP_KIT", "")), ROOT / "track1-coding-public", ROOT.parent / "agenthon" / "track1-coding-public"):
        if cand and (cand / "units").is_dir():
            return cand
    return ROOT / "track1-coding-public"


DEFAULT_KIT = _default_kit()
WIN = os.name == "nt"
SHIM_ROOTS = ["/app", "/input", "/output", "/tests", "/logs"]


def _sudo() -> list[str]:
    if WIN or (hasattr(os, "geteuid") and os.geteuid() == 0):
        return []
    return ["sudo", "-n"]


def _sh(cmd: list[str]) -> None:
    subprocess.run(cmd, check=False, capture_output=True)


def _drive(p: str) -> str:
    return (os.path.splitdrive(os.getcwd())[0] + p.replace("/", "\\")) if WIN else p


def _rm(path: str) -> None:
    p = _drive(path)
    if WIN:
        if os.path.islink(p) or (os.path.isdir(p) and _is_junction(p)):
            _sh(["cmd", "/c", "rmdir", p])
        elif os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
    else:
        _sh(_sudo() + ["rm", "-rf", p])


def _is_junction(p: str) -> bool:
    try:
        return bool(os.lstat(p).st_file_attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except (OSError, AttributeError):
        return False


def _link_dir(target: Path, path: str) -> None:
    _rm(path)
    p = _drive(path)
    if WIN:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        _sh(["cmd", "/c", "mklink", "/J", p, str(target)])
    else:
        _sh(_sudo() + ["mkdir", "-p", os.path.dirname(p)])
        _sh(_sudo() + ["ln", "-sfn", str(target), p])


def _place_file(target: Path, path: str) -> None:
    _rm(path)
    p = _drive(path)
    if WIN:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copy2(target, p)
    else:
        _sh(_sudo() + ["mkdir", "-p", os.path.dirname(p)])
        _sh(_sudo() + ["ln", "-sfn", str(target), p])


def copy_targets(unit: Path) -> list[tuple[Path, str, bool]]:
    """(real path, image path, is_dir) for every COPY line of the unit Dockerfile."""
    df = unit / "environment" / "Dockerfile"
    data = unit / "environment" / "data"
    out = []
    if not df.exists() or not data.is_dir():
        return out
    for line in df.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s.upper().startswith("COPY "):
            continue
        parts = [t for t in s[5:].split() if not t.startswith("--")]
        if len(parts) < 2:
            continue
        src, dst = parts[0].strip("\"'").lstrip("./"), parts[-1].strip("\"'")
        if not src.startswith("data"):
            continue
        rel = src[4:].lstrip("/")
        srcp = data / rel if rel else data
        if srcp.is_dir():
            if dst.rstrip("/") in ("/app", ""):
                for f in sorted(srcp.rglob("*")):
                    if f.is_file():
                        out.append((f, "/app/" + f.relative_to(srcp).as_posix(), False))
            else:
                out.append((srcp, dst.rstrip("/"), True))
        elif srcp.is_file():
            out.append((srcp, dst + srcp.name if dst.endswith("/") else dst, False))
    return out


def _clear_root(path: str) -> None:
    """Remove a shim root without ever recursing into a junction/symlink target."""
    p = _drive(path)
    if not os.path.lexists(p):
        return
    if WIN:
        _clear_win(p)
    else:
        _sh(_sudo() + ["rm", "-rf", p])   # rm -rf never follows symlinks


def _clear_win(p: str) -> None:
    """Delete a shim tree on Windows. A junction or symlink is removed as an entry (rmdir on the
    link itself) and NEVER entered: os.walk does not recognise junctions, and following one would
    delete the kit or the output tree it points at."""
    if _is_junction(p) or os.path.islink(p):
        _sh(["cmd", "/c", "rmdir", p])
        return
    if os.path.isdir(p):
        with os.scandir(p) as it:
            entries = list(it)
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                _clear_win(e.path)
            else:
                try:
                    os.remove(e.path)
                except OSError:
                    pass
        try:
            os.rmdir(p)
        except OSError:
            pass
    else:
        try:
            os.remove(p)
        except OSError:
            pass


def setup_shims(unit: Path, out: Path, logs: Path, appdata: Path) -> None:
    """Present the platform's paths. /app/data is a scratch directory holding a link/copy of
    every data file plus the Dockerfile's renamed copies, so nothing is ever written into the kit."""
    for r in SHIM_ROOTS:
        _clear_root(r)
    _link_dir(unit, "/input")
    _link_dir(out, "/output")
    _link_dir(out, "/app/output")
    _link_dir(logs, "/logs/verifier")
    data = unit / "environment" / "data"
    if data.is_dir():
        for f in sorted(data.rglob("*")):
            if f.is_file():
                dst = appdata / f.relative_to(data)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if WIN:
                    shutil.copy2(f, dst)
                else:
                    os.symlink(f, dst)
    _link_dir(appdata, "/app/data")
    ref = unit / "checks" / "reference_data"
    if ref.is_dir():
        _link_dir(ref, "/tests/reference_data")
    for real, image_path, is_dir in copy_targets(unit):
        if image_path.startswith("/app/output") or image_path.rstrip("/") == "/app/data":
            continue
        if image_path.startswith("/app/data/"):
            dst = appdata / image_path[len("/app/data/"):]
            if dst.exists() or dst.is_symlink():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if is_dir:
                if WIN:
                    shutil.copytree(real, dst)
                else:
                    os.symlink(real, dst)
            elif WIN:
                shutil.copy2(real, dst)
            else:
                os.symlink(real, dst)
            continue
        if is_dir:
            _link_dir(real, image_path)
        else:
            _place_file(real, image_path)


def teardown() -> None:
    for r in SHIM_ROOTS:
        _clear_root(r)


def run_checker(unit: Path, out: Path) -> dict:
    checks = unit / "checks"
    test = checks / "test_outputs.py"
    if not test.exists():
        return {"status": "no-checker"}
    with tempfile.TemporaryDirectory(prefix="jp-chk-") as td:
        logs = Path(td) / "verifier"
        logs.mkdir()
        appdata = Path(td) / "appdata"
        appdata.mkdir()
        setup_shims(unit, out, logs, appdata)
        rep = Path(td) / "report.json"
        env = dict(os.environ, OUTPUT_DIR="/app/output", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
        env.pop("PYTHONPATH", None)
        cmd = [sys.executable, "-m", "pytest", str(test), "-q", "--tb=line", "--no-header", "-p", "no:cacheprovider",
               "--timeout=270", "--json-report", f"--json-report-file={rep}"]
        try:
            p = subprocess.run(cmd, cwd=str(checks), env=env, capture_output=True, text=True, timeout=330)
            rc, tail = p.returncode, (p.stdout + p.stderr)[-1500:]
        except subprocess.TimeoutExpired:
            rc, tail = 124, "checker timeout"
        try:
            r = json.loads(rep.read_text(encoding="utf-8"))
            s = r.get("summary", {})
            passed, failed, errors, total = s.get("passed", 0), s.get("failed", 0), s.get("error", 0), s.get("total", 0)
            fails = [t["nodeid"].split("::")[-1] for t in r.get("tests", []) if t.get("outcome") in ("failed", "error")][:8]
        except Exception:  # noqa: BLE001
            passed = failed = errors = total = 0
            fails = ["no report"]
        teardown()
        return {"status": "pass" if rc == 0 else "fail", "rc": rc, "passed": passed, "failed": failed, "errors": errors,
                "total": total, "failing": fails, "tail": tail}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", default=str(DEFAULT_KIT))
    ap.add_argument("--out-root", default=str(HERE / "out" / "units"))
    ap.add_argument("--units", nargs="*", default=None)
    args = ap.parse_args()
    kit = Path(args.kit)
    out_root = Path(args.out_root).resolve()
    units = sorted(p for p in (kit / "units").iterdir() if p.is_dir() and (p / "checks").is_dir())
    if args.units:
        units = [u for u in units if u.name in set(args.units)]
    rows = []
    npass = 0
    print(f"{'unit':52s} {'result':6s} {'tests':>9s}  failing")
    for u in units:
        out = out_root / u.name
        if not out.is_dir():
            rows.append({"unit": u.name, "status": "no-output"})
            print(f"{u.name:52s} {'n/a':6s}")
            continue
        r = run_checker(u, out)
        r["unit"] = u.name
        rows.append(r)
        npass += r.get("status") == "pass"
        print(f"{u.name:52s} {r.get('status', '?'):6s} {str(r.get('passed', 0)) + '/' + str(r.get('total', 0)):>9s}  "
              f"{', '.join(r.get('failing', []))[:90]}", flush=True)
    (out_root / "checks.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\nlocal pass {npass}/{len(units)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
