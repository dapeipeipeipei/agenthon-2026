"""Pack Agenthon submission zips into ../agenthon-submissions/.

Run from the repo root:
    .venv\\Scripts\\python pack_all.py                  # the four default zips (T2/T4 dev + final)
    .venv\\Scripts\\python pack_all.py t2-v5a t2-v5b    # only the named candidate jobs
    .venv\\Scripts\\python pack_all.py t2-dev t4-final   # any default job by its short name
    .venv\\Scripts\\python pack_all.py path/to/descriptor.json=name.zip   # explicit descriptor -> zip
    add `--key-file <path>` to read the key from a file instead of the hidden prompt

A short name `t2-<x>` that is not a default job means the T2 candidate descriptor
t2-work/submission/submission.<x>.json and is packed to t2-<phase>-<x>.zip (phase read from the
descriptor), e.g. t2-v5a -> t2-dev-v5a.zip. Every descriptor is checked to exist before the key is
asked for.

The Team Key is typed at a hidden prompt; it is never printed, logged or written to disk.
The zips hold no key (team-claim.json carries a proof bound to the descriptor).
"""
import getpass
import json
import pathlib
import sys

from qfbench2_common.team_claim import pack_submission, validate_team_key

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT.parent / "agenthon-submissions"
TEAM = 299
DEFAULT_JOBS = {
    "t2-dev.zip": ROOT / "t2-work/submission/submission.json",
    "t2-final.zip": ROOT / "t2-work/submission/submission.final.json",
    "t4-dev.zip": ROOT / "t4-work/submission/submission.json",
    "t4-final.zip": ROOT / "t4-work/submission/submission.final.json",
}


def resolve(arg: str) -> tuple[str, pathlib.Path]:
    if "=" in arg:
        src, name = arg.split("=", 1)
        p = pathlib.Path(src)
        return name, (p if p.is_absolute() else ROOT / p)
    if f"{arg}.zip" in DEFAULT_JOBS:
        return f"{arg}.zip", DEFAULT_JOBS[f"{arg}.zip"]
    if arg.startswith("t2-"):
        x = arg[3:]
        src = ROOT / f"t2-work/submission/submission.{x}.json"
        phase = json.loads(src.read_text(encoding="utf-8")).get("phase", "dev") if src.is_file() else "dev"
        return f"t2-{phase}-{x}.zip", src
    sys.exit(f"unknown job {arg!r}: use t2-<candidate>, a default job ({', '.join(n[:-4] for n in DEFAULT_JOBS)}) "
             "or descriptor.json=name.zip")


args = sys.argv[1:]
key_file = None
if "--key-file" in args:  # Windows cannot satisfy the toolkit's chmod-600 check on --team-key-file
    i = args.index("--key-file")
    key_file = pathlib.Path(args[i + 1])
    del args[i:i + 2]
jobs = dict(resolve(a) for a in args) if args else dict(DEFAULT_JOBS)
missing = [str(p) for p in jobs.values() if not p.is_file()]
if missing:
    sys.exit("descriptor(s) not found: " + ", ".join(missing))

raw = key_file.read_text(encoding="utf-8-sig") if key_file else getpass.getpass("Team Key (hidden): ")
key = validate_team_key(raw.strip())
del raw
OUT.mkdir(exist_ok=True)
for name, src in jobs.items():
    d = json.loads(src.read_text(encoding="utf-8"))
    d.pop("team_id", None)  # placeholder; pack derives and seals the real one
    tid = pack_submission(d, TEAM, key, OUT / name)
    try:
        shown = src.relative_to(ROOT)
    except ValueError:
        shown = src
    print(f"{name}: team_id {tid}  <- {shown}  (image {d['image']['digest'][:19]}...)")
del key
print(f"\ndone -> {OUT}")
