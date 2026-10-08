"""Run a Track 3 image on public units with the platform's container settings; time like the Final.

For each unit: `docker create` (platform flags), `docker start -a`, `docker inspect` for the
daemon's own `State.StartedAt` / `State.FinishedAt` window (the Final's timed window; creating,
inspecting and removing the container are outside it), `docker rm`. The unit's inputs are staged
the way the organizer's runner stages them (only scenario.json, or only scenarios/), the output
directory is bind-mounted at /output, and the container runs as 65534:65534 with a read-only root
filesystem and a 64 MiB /tmp, so anything that only works as root or with a writable rootfs
fails here first.

Usage:
    python ci_run_units.py --image <img> --units <kit>/units --out-root <root> [--only ...]
                           [--repeat N] [--timeout 300]
Writes <root>/<unit>/... and <root>/timing.json {unit: median window seconds}; exit 1 on any
non-zero container exit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

PLATFORM_FLAGS = [
    "--network=none", "--cpus=4", "--memory=16g", "--memory-swap=16g", "--pids-limit=256",
    "--read-only", "--user", "65534:65534", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
    "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
    "-e", "QFBENCH_NETWORK=none", "-e", "QFBENCH_SEED=0",
]


def _parse_ts(s: str) -> float:
    s = s.rstrip("Z")
    head, _, frac = s.partition(".")
    base = dt.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    frac = (frac + "000000000")[:9]
    return base.timestamp() + int(frac) / 1e9


def run_once(image: str, unit: pathlib.Path, out: pathlib.Path, timeout: float) -> tuple[int, float, str]:
    batch = (unit / "batch.json").is_file()
    out = out.resolve()  # docker -v needs absolute host paths
    staging = pathlib.Path(tempfile.mkdtemp(prefix="t3in_")).resolve()
    try:
        if batch:
            shutil.copytree(unit / "scenarios", staging / "scenarios")
            verb = ["simulate-batch", "--batch-dir", "/input/scenarios", "--out-dir", "/output"]
        else:
            shutil.copy(unit / "scenario.json", staging / "scenario.json")
            verb = ["simulate", "--config", "/input/scenario.json", "--out", "/output/trace.parquet"]
        staging.chmod(0o755)
        for p in staging.rglob("*"):
            p.chmod(0o755 if p.is_dir() else 0o644)
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        out.chmod(0o777)
        cid = subprocess.run(
            ["docker", "create", *PLATFORM_FLAGS, "-v", f"{staging}:/input:ro", "-v", f"{out}:/output", image, *verb],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        try:
            proc = subprocess.run(["docker", "start", "-a", cid], capture_output=True, text=True, timeout=timeout)
            rc = proc.returncode
            tail = proc.stderr[-1500:]
            info = json.loads(subprocess.run(["docker", "inspect", cid], check=True, capture_output=True, text=True).stdout)[0]
            st, fi = info["State"]["StartedAt"], info["State"]["FinishedAt"]
            window = _parse_ts(fi) - _parse_ts(st)
            if info["State"].get("OOMKilled"):
                tail += " [OOMKilled]"
                rc = rc or 137
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", cid], capture_output=True)
            rc, window, tail = -1, timeout, "timeout"
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
        return rc, window, tail
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--units", required=True, type=pathlib.Path)
    ap.add_argument("--out-root", required=True, type=pathlib.Path)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()
    names = sorted(p.name for p in args.units.iterdir() if p.is_dir())
    if args.only:
        names = [n for n in names if n in set(args.only)]
    args.out_root.mkdir(parents=True, exist_ok=True)
    timing: dict[str, float] = {}
    failures = []
    t_all = time.perf_counter()
    for name in names:
        windows = []
        for _ in range(args.repeat):
            rc, window, tail = run_once(args.image, args.units / name, args.out_root / name, args.timeout)
            if rc != 0:
                failures.append(name)
                print(f"{name:34} FAILED rc={rc} window={window:.2f}s {tail}", flush=True)
                break
            windows.append(window)
        if windows:
            timing[name] = statistics.median(windows)
            print(f"{name:34} window {timing[name]:7.3f}s  ({' '.join(f'{w:.2f}' for w in windows)})", flush=True)
        (args.out_root / "timing.json").write_text(json.dumps(timing, indent=1, sort_keys=True))
    print(f"\n{len(names) - len(failures)}/{len(names)} ran in {time.perf_counter() - t_all:.0f}s; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
