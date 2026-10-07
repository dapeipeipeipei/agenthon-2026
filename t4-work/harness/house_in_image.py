"""Run the BUILT image against the mock House server (good mode), the way the platform injects the
route: MODEL_ENDPOINT / MODEL_NAME / MODEL_TOKEN in the environment, platform container limits
(read-only root, uid 65534, pids/nofile caps, tmpfs /tmp) except that the network reaches the
host's mock. The image's own T4_USE_HOUSE decides the mode: an image built with T4_USE_HOUSE=1
(candidate B) must use the House and still pass every official check; one built with 0
(candidate A) must ignore the injected variables.

Usage (Linux CI):  python t4-work/harness/house_in_image.py --image t4-local:ci
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
T4 = HERE.parent.parent / "track4-analysis-public"
sys.path.insert(0, str(T4))
sys.path.insert(0, str(HERE))
import mock_house as mh  # noqa: E402
from run_local import scratch_unit, schema_errors  # noqa: E402

UNITS = ("t4-credit-event-2023", "t4-cpicomp-202410-us11", "t4-postearn-20240201-megacap", "t4-fomc-curve-20240918")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out-root", default=str(HERE / "out" / "_house_image"))
    args = ap.parse_args()
    env_line = subprocess.run(["docker", "image", "inspect", args.image, "--format", "{{json .Config.Env}}"],
                              capture_output=True, text=True, check=True).stdout
    house_on = "T4_USE_HOUSE=1" in json.loads(env_line)
    srv = ThreadingHTTPServer(("0.0.0.0", 0), mh.H)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    mh.MODE["m"] = "good"
    from baselines.guardrails_example.citation_rail import check_claim_rules, check_submitted_reasons, load_corpus

    out_root = Path(args.out_root).resolve()
    allok = True
    for name in UNITS:
        unit = scratch_unit(T4 / "units" / name, out_root / "_units")
        outdir = out_root / name
        outdir.mkdir(parents=True, exist_ok=True)
        outdir.chmod(0o777)
        ans_path = outdir / "answer.json"
        if ans_path.exists():
            ans_path.unlink()
        mh.SEEN.clear()
        cmd = [
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "--read-only", "--user", "65534:65534", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--pids-limit", "256", "--ulimit", "nofile=1024:1024", "--ulimit", "nproc=256:256",
            "--network=host",
            "-v", f"{unit}:/input:ro", "-v", f"{outdir}:/output",
            "-e", "QFBENCH_SEED=0", "-e", "QFBENCH_NETWORK=restricted",
            "-e", f"MODEL_ENDPOINT=http://127.0.0.1:{port}", "-e", "MODEL_NAME=house", "-e", "MODEL_TOKEN=test-token",
            args.image, "analyze", "--task", "/input/task.json", "--corpus", "/input/corpus/", "--out", "/output/answer.json",
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        why = []
        if p.returncode != 0 or not ans_path.exists():
            why.append(f"rc={p.returncode} {p.stderr[-300:]}")
        else:
            ans = json.loads(ans_path.read_text(encoding="utf-8"))
            notes = ans.get("notes", {})
            if schema_errors(ans):
                why.append("schema")
            if notes.get("fallback"):
                why.append("fallback")
            fs = [f.code for f in check_claim_rules(ans, unit, token_counter=None) if f.code != "claim_tokens_unchecked"]
            if fs:
                why.append(f"claims {sorted(set(fs))}")
            task = json.loads((unit / "task.json").read_text(encoding="utf-8"))
            if check_submitted_reasons(ans, load_corpus(unit / "corpus"), task["cutoff_date"]):
                why.append("reasons")
            if house_on and not (notes.get("house") == "on" and notes.get("points") and notes.get("reasons") and mh.SEEN):
                why.append(f"House not used: {notes.get('house')} points={notes.get('points')} requests={len(mh.SEEN)}")
            if not house_on and (notes.get("house") != "off" or mh.SEEN):
                why.append(f"House used by a House-off image ({len(mh.SEEN)} requests)")
            extra = sorted(x.name for x in outdir.iterdir() if x.name != "answer.json")
            if extra:
                why.append(f"extra output files {extra}")
        allok &= not why
        print(f"{'PASS' if not why else 'FAIL'}  in-image house={'on ' if house_on else 'off'} {name:32s} requests={len(mh.SEEN)}"
              + ("  " + "; ".join(why) if why else ""), flush=True)
    srv.shutdown()
    print(f"HOUSE IN IMAGE: {'PASS' if allok else 'FAIL'}")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
