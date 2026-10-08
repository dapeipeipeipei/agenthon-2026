"""Run the agent over public Track 1 units against the mock House, from source or inside the
built image with the Development platform's container settings, and judge what the platform
judges before any checker runs: exit 0, an acceptable output tree, the deliverables present.

    python harness/run_units.py                          # all units, source agent, mock mode good
    python harness/run_units.py --units t1-zero-coupon-bootstrapping t1-EXAMPLE-bs-greeks-pde
    python harness/run_units.py --mode flaky --units ... # robustness modes of mock_house.py
    python harness/run_units.py --offline                # no MODEL_* at all (stub path)
    python harness/run_units.py --docker-image jinpei-t1:ci --out-root t1-ci-out   # Linux CI

Writes <out-root>/<unit>/ (the output tree the agent left, untouched) and <out-root>/results.json.
Exit status 1 when any unit crashed, left an unacceptable tree or wrote none of its deliverables.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
ROOT = WORK.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORK))
import mock_house as mh  # noqa: E402
from agent.unit import regex_deliverables  # noqa: E402

def _default_kit() -> Path:
    """The upstream kit is gitignored and lives in the main checkout; worktrees look there."""
    for cand in (Path(os.environ.get("JP_KIT", "")), ROOT / "track1-coding-public", ROOT.parent / "agenthon" / "track1-coding-public"):
        if cand and (cand / "units").is_dir():
            return cand
    return ROOT / "track1-coding-public"


DEFAULT_KIT = _default_kit()


def tree_problems(out: Path) -> list[str]:
    """The organizers' output-folder rules (README), re-checked independently of the agent."""
    probs = []
    files = 0
    nodes = 0
    total = 0
    names: dict[str, str] = {}
    if not out.is_dir():
        return ["no output directory"]
    for root, dirs, fs in os.walk(out):
        depth = len(Path(root).relative_to(out).parts)
        nodes += len(dirs) + len(fs)
        if depth >= 8:
            probs.append(f"folder nested too deep: {root}")
        for d in dirs:
            if (Path(root) / d).is_symlink():
                probs.append(f"symlink dir {d}")
        for f in fs:
            p = Path(root) / f
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode):
                probs.append(f"symlink {f}")
                continue
            if not stat.S_ISREG(st.st_mode):
                probs.append(f"special file {f}")
                continue
            if st.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                probs.append(f"setuid/setgid/sticky bit on {f}")
            if os.name == "posix" and st.st_nlink > 1:
                probs.append(f"hard link {f}")
            if st.st_size > 64 * 2**20:
                probs.append(f"{f} over 64 MiB")
            if "\\" in f or any(ord(c) < 32 for c in f):
                probs.append(f"bad name {f!r}")
            key = p.relative_to(out).as_posix().casefold()
            if key in names:
                probs.append(f"case-duplicate {f}")
            names[key] = f
            files += 1
            total += st.st_size
    if files == 0:
        probs.append("no files at all")
    if files > 256:
        probs.append(f"{files} files > 256")
    if nodes > 4096:
        probs.append(f"{nodes} nodes > 4096")
    if total > 64 * 2**20:
        probs.append(f"total {total} > 64 MiB")
    return probs


def docker_cmd(image: str, unit: Path, out: Path, env: dict, network: str) -> list[str]:
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "--read-only", "--user", "65534:65534", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--pids-limit", "256", "--ulimit", "nofile=1024:1024", "--ulimit", "nproc=256:256",
        "--cpus", "4", "--memory", "12g", "--memory-swap", "12g",
    ]
    if network == "none":
        cmd += ["--network=none"]
    else:
        cmd += ["--add-host=host.docker.internal:host-gateway"]
    cmd += ["-v", f"{unit}:/input:ro", "-v", f"{out}:/app/output", "-v", f"{out}:/output",
            "-e", "QFBENCH_SEED=0", "-e", f"QFBENCH_NETWORK={'none' if network == 'none' else 'restricted'}"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [image, "solve", "--task-dir", "/input", "--out", "/app/output"]
    return cmd


def run_one(unit: Path, out: Path, env: dict, image: str | None, timeout: float) -> tuple[int, float, str, str]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    base = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    base.update({"PYTHONUTF8": "1", "NO_PROXY": "127.0.0.1,localhost,host.docker.internal", "no_proxy": "127.0.0.1,localhost,host.docker.internal"})
    for k in ("MODEL_ENDPOINT", "MODEL_TOKEN", "MODEL_NAME"):
        base.pop(k, None)
    t0 = time.time()
    if image:
        os.chmod(out, 0o777)
        cmd = docker_cmd(image, unit, out, env, "none" if not env.get("MODEL_ENDPOINT") else "host-gateway")
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 120, env=base)
    else:
        base.update(env)
        p = subprocess.run([sys.executable, "-B", "-m", "agent", "solve", "--task-dir", str(unit), "--out", str(out)],
                           cwd=str(WORK), env=base, capture_output=True, text=True, timeout=timeout + 120)
    return p.returncode, time.time() - t0, p.stdout, p.stderr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", default=str(DEFAULT_KIT))
    ap.add_argument("--units", nargs="*", default=None)
    ap.add_argument("--out-root", default=str(HERE / "out" / "units"))
    ap.add_argument("--mode", default="good", help="mock_house mode; 'down' = endpoint set but nothing listening")
    ap.add_argument("--offline", action="store_true", help="no MODEL_* variables at all")
    ap.add_argument("--docker-image", default=None)
    ap.add_argument("--budget", type=float, default=300.0, help="JP_UNIT_BUDGET_SEC for the agent")
    ap.add_argument("--agent-env", nargs="*", default=[], help="extra KEY=VALUE for the agent")
    ap.add_argument("--mock-host", default=None, help="bind address of the mock (default 127.0.0.1; 0.0.0.0 for docker)")
    args = ap.parse_args()

    kit = Path(args.kit)
    units = sorted(p for p in (kit / "units").iterdir() if p.is_dir() and (p / "instruction.md").exists())
    if args.units:
        want = set(args.units)
        units = [u for u in units if u.name in want]
        missing = want - {u.name for u in units}
        if missing:
            sys.exit(f"unknown units: {sorted(missing)}")
    out_root = Path(args.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    env = {"JP_UNIT_BUDGET_SEC": str(args.budget)}
    for kv in args.agent_env:
        k, _, v = kv.partition("=")
        env[k] = v
    srv = None
    if not args.offline:
        host = args.mock_host or ("0.0.0.0" if args.docker_image else "127.0.0.1")
        if args.mode != "down":
            mh.MODE["m"] = args.mode
            srv = mh.start(0, host)
            port = srv.server_address[1]
        else:
            port = 9   # discard port: nothing listens
        reach = "host.docker.internal" if args.docker_image else "127.0.0.1"
        env.update({"MODEL_ENDPOINT": f"http://{reach}:{port}", "MODEL_TOKEN": mh.TOKEN, "MODEL_NAME": "mock-house"})

    results = []
    bad = 0
    print(f"{'unit':52s} {'rc':>3s} {'secs':>6s} {'req':>3s} {'files':>5s}  verdict")
    for u in units:
        out = out_root / u.name
        mh.SEEN.clear()
        rc, secs, so, se = run_one(u, out, env, args.docker_image, args.budget)
        summary = None
        m = re.search(r"^JP_SUMMARY (\{.*\})$", so, re.M)
        if m:
            try:
                summary = json.loads(m.group(1))
            except Exception:  # noqa: BLE001
                summary = None
        probs = tree_problems(out)
        # deliverables the task text names (the floor the organizers' conformance.sh checks)
        want = regex_deliverables((u / "instruction.md").read_text(encoding="utf-8", errors="replace"))
        have = [n for n in want if (out / n).is_file()]
        reqs = summary.get("requests") if summary else len(mh.SEEN)
        n_files = sum(1 for _r, _d, fs in os.walk(out) for _ in fs)
        verdict = []
        if rc != 0:
            verdict.append(f"CRASH rc={rc}")
        if probs:
            verdict.append("TREE: " + "; ".join(probs[:3]))
        if want and not have:
            verdict.append(f"NONE of {want[:3]} written")
        if reqs is not None and reqs > 25:
            verdict.append(f"{reqs} requests > 25")
        if summary and summary.get("final_problems"):
            verdict.append("unresolved: " + str(summary["final_problems"][0])[:70].replace("\n", " "))
        ok = not any(v.startswith(("CRASH", "TREE", "NONE")) or "requests" in v for v in verdict)
        bad += 0 if ok else 1
        (out_root / f"{u.name}.log").write_text(so[-20000:] + "\n--- stderr ---\n" + se[-8000:], encoding="utf-8")
        results.append({"unit": u.name, "rc": rc, "seconds": round(secs, 1), "requests": reqs, "files": n_files,
                        "ok": ok, "verdict": verdict, "deliverables_named": want, "deliverables_written": have,
                        "summary": summary})
        print(f"{u.name:52s} {rc:3d} {secs:6.1f} {str(reqs):>3s} {n_files:5d}  {'ok' if ok else 'FAIL'} {' | '.join(verdict)}", flush=True)
    (out_root / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    n = len(results)
    print(f"\nunits {n}   ok {n - bad}   failed {bad}   mode={args.mode if not args.offline else 'offline'}"
          f"   image={args.docker_image or 'source'}   total {sum(r['seconds'] for r in results):.0f}s")
    if srv:
        srv.shutdown()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
