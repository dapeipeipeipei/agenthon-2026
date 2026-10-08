"""Write the sealed Track 3 descriptors for a pushed image digest.

    python t3-work/tools/make_descriptors.py sha256:<64 hex> [--suffix <x>]

Writes t3-work/submission/submission[.<x>].json (phase dev) and submission[.<x>].final.json
(phase final), both validated with the toolkit parser and sealed with seal_descriptor_digest.
team_id is the placeholder pack_all.py replaces with the derived id at pack time.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re

from qfbench2_common.contracts.descriptor import SubmissionDescriptor, seal_descriptor_digest

HERE = pathlib.Path(__file__).resolve().parent.parent


def build(digest: str, phase: str) -> dict:
    d = {
        "schema_version": "1.1.0",
        "interface_version": "2.0",
        "competition_id": f"agenthon2026-simulation-{phase}",
        "team_id": "team-00000000000000000000000000000000",
        "track": "simulation",
        "phase": phase,
        "category": "simulator",
        "image": {"registry": "ghcr.io", "repository": "dapeipeipeipei/jinpei-t3", "digest": digest},
        "image_access": "public",
        "models": [],
        "license": "BSD-3-Clause",
        "descriptor_digest": "sha256:" + "0" * 64,
    }
    sealed = seal_descriptor_digest(d)
    SubmissionDescriptor.from_mapping(sealed)  # raises on any contract violation
    return sealed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("digest")
    ap.add_argument("--suffix", default="")
    args = ap.parse_args()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.digest):
        raise SystemExit("digest must be sha256:<64 hex>")
    out = HERE / "submission"
    out.mkdir(exist_ok=True)
    sfx = f".{args.suffix}" if args.suffix else ""
    for phase, name in (("dev", f"submission{sfx}.json"), ("final", f"submission{sfx}.final.json")):
        p = out / name
        p.write_text(json.dumps(build(args.digest, phase), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {p.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
