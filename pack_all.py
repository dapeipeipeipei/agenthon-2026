"""Pack the T2/T4 dev+final submission zips into ../agenthon-submissions/.

Run from the repo root:   .venv\\Scripts\\python pack_all.py
The Team Key is typed at a hidden prompt; it is never printed, logged or written to disk.
The zips hold no key (team-claim.json carries a proof bound to the descriptor).
"""
import getpass
import json
import pathlib

from qfbench2_common.team_claim import pack_submission, validate_team_key

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT.parent / "agenthon-submissions"
TEAM = 299
JOBS = {
    "t2-dev.zip": ROOT / "t2-work/submission/submission.json",
    "t2-final.zip": ROOT / "t2-work/submission/submission.final.json",
    "t4-dev.zip": ROOT / "t4-work/submission/submission.json",
    "t4-final.zip": ROOT / "t4-work/submission/submission.final.json",
}

key = validate_team_key(getpass.getpass("Team Key (hidden): ").strip())
OUT.mkdir(exist_ok=True)
for name, src in JOBS.items():
    d = json.loads(src.read_text(encoding="utf-8"))
    d.pop("team_id", None)  # placeholder; pack derives and seals the real one
    tid = pack_submission(d, TEAM, key, OUT / name)
    print(f"{name}: team_id {tid}  <- {src.relative_to(ROOT)}")
del key
print(f"\ndone -> {OUT}")
