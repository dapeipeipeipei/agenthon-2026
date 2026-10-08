"""Write / fill / check the Track 1 submission descriptors (toolkit qfbench2-common v2.6.0).

    python t1-work/descriptor_tool.py write --digest sha256:<64 hex> [--name <candidate>]
        -> t1-work/submission/submission.json (+ .final.json), or submission.<candidate>.json
           (+ submission.<candidate>.final.json) when --name is given; sealed and validated
    python t1-work/descriptor_tool.py fill --descriptor PATH --digest sha256:<64 hex>
        -> changes only image.digest, reseals, validates
    python t1-work/descriptor_tool.py check --descriptor PATH
        -> SubmissionDescriptor.from_mapping + the track-specific semantic checks

Every descriptor carries the House row from Agenthon2026-public/docs/HOUSE-MODEL.md (the agent
calls the House model on every unit; README rule 9). team_id is a placeholder: `qfbench2
submission pack` (pack_all.py) derives and writes the real one from the Team Key. Nothing here
reads, asks for or prints the Team Key.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import qfbench2_common
from qfbench2_common.contracts.descriptor import SubmissionDescriptor, seal_descriptor_digest

HERE = pathlib.Path(__file__).resolve().parent
SUB = HERE / "submission"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PLACEHOLDER_ID = "team-" + "0" * 32
HOUSE_ROW = {
    "name": "nvidia/nemotron-3-super-120b-a12b",
    "version": "rl-030326-fp8",
    "revision": "rl-030326-fp8",
    "training_cutoff": "unpublished",
    "access": "api",
}
TWELVE = {"category", "competition_id", "descriptor_digest", "image", "image_access", "interface_version",
          "license", "models", "phase", "schema_version", "team_id", "track"}


def _schema_is_current() -> None:
    s = json.loads((pathlib.Path(qfbench2_common.__file__).parent / "schemas/submission.schema.json").read_text(encoding="utf-8"))
    if not ("image" in s["properties"] and s.get("additionalProperties") is False and len(s["required"]) == 12):
        sys.exit("FAIL: the installed qfbench2-common carries a stale descriptor schema; reinstall v2.6.0")


def descriptor(phase: str, digest: str) -> dict:
    d = {
        "schema_version": "1.1.0",
        "interface_version": "2.0",
        "competition_id": f"agenthon2026-coding-{phase}",
        "team_id": PLACEHOLDER_ID,
        "track": "coding",
        "phase": phase,
        "category": "api",
        "image": {"registry": "ghcr.io", "repository": "dapeipeipeipei/jinpei-t1", "digest": digest},
        "image_access": "public",
        "models": [dict(HOUSE_ROW)],
        "license": "Apache-2.0",
        "descriptor_digest": "sha256:" + "0" * 64,
    }
    return seal_descriptor_digest(d)


def semantic_problems(d: dict) -> list[str]:
    p = []
    if set(d) != TWELVE:
        p.append(f"keys must be exactly the twelve: extra={sorted(set(d) - TWELVE)} missing={sorted(TWELVE - set(d))}")
    for k, v in {"interface_version": "2.0", "track": "coding", "category": "api", "image_access": "public",
                 "schema_version": "1.1.0", "license": "Apache-2.0"}.items():
        if d.get(k) != v:
            p.append(f"{k}={d.get(k)!r}, expected {v!r}")
    if d.get("competition_id") != f"agenthon2026-coding-{d.get('phase')}":
        p.append(f"competition_id {d.get('competition_id')!r} does not match phase {d.get('phase')!r}")
    img = d.get("image") or {}
    if img.get("registry") != "ghcr.io" or img.get("repository") != "dapeipeipeipei/jinpei-t1":
        p.append(f"image registry/repository unexpected: {img}")
    if not DIGEST_RE.match(str(img.get("digest", ""))):
        p.append("image.digest is not sha256:<64 hex>")
    if img.get("digest") == "sha256:" + "0" * 64:
        p.append("image.digest is the placeholder (fill it from the CI digest)")
    if d.get("models") != [HOUSE_ROW]:
        p.append("models[] must be exactly the House row from HOUSE-MODEL.md")
    if seal_descriptor_digest(dict(d)).get("descriptor_digest") != d.get("descriptor_digest"):
        p.append("descriptor_digest is not the seal of this content")
    return p


def check(path: pathlib.Path) -> int:
    d = json.loads(path.read_text(encoding="utf-8"))
    try:
        SubmissionDescriptor.from_mapping(d)
    except Exception as exc:  # noqa: BLE001
        print(f"{path}: toolkit parser REJECTS: {exc}")
        return 1
    probs = semantic_problems(d)
    placeholder_only = probs == ["image.digest is the placeholder (fill it from the CI digest)"]
    for q in probs:
        print(f"{path}: {'note' if placeholder_only else 'FAIL'}: {q}")
    if not probs or placeholder_only:
        print(f"{path}: valid (toolkit parse ok, seal ok, phase={d['phase']}, digest {d['image']['digest'][:19]}...)")
        return 0
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--digest", required=True)
    w.add_argument("--name", default=None)
    f = sub.add_parser("fill")
    f.add_argument("--descriptor", required=True)
    f.add_argument("--digest", required=True)
    c = sub.add_parser("check")
    c.add_argument("--descriptor", required=True)
    a = ap.parse_args()
    _schema_is_current()
    if a.cmd == "write":
        if not DIGEST_RE.match(a.digest):
            sys.exit("digest must be sha256:<64 hex>")
        SUB.mkdir(exist_ok=True)
        rc = 0
        for phase, fn in (("dev", "submission.json"), ("final", "submission.final.json")):
            if a.name:
                fn = f"submission.{a.name}.json" if phase == "dev" else f"submission.{a.name}.final.json"
            p = SUB / fn
            p.write_text(json.dumps(descriptor(phase, a.digest), indent=2) + "\n", encoding="utf-8")
            rc |= check(p)
        return rc
    if a.cmd == "fill":
        p = pathlib.Path(a.descriptor)
        d = json.loads(p.read_text(encoding="utf-8"))
        if not DIGEST_RE.match(a.digest):
            sys.exit("digest must be sha256:<64 hex>")
        d["image"]["digest"] = a.digest
        d = seal_descriptor_digest(d)
        p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
        return check(p)
    return check(pathlib.Path(a.descriptor))


if __name__ == "__main__":
    sys.exit(main())
