"""Fill the image digest into submission.json and check a packed submission.zip.

Used by pack.sh; runs under t2-work/pack/.venv-docker (toolkit qfbench2-common v2.6.0).

    python descriptor_tool.py fill --descriptor PATH --registry ghcr.io --repository OWNER/NAME \
        --digest sha256:<64 hex> [--dry-run]
    python descriptor_tool.py check --descriptor PATH
    python descriptor_tool.py verify-zip --zip PATH --digest sha256:<64 hex> [--repository OWNER/NAME]

`fill` changes only the three values under "image" (and reseals descriptor_digest); every other
key, and the key order, stay exactly as the descriptor's author wrote them. It never touches
team_id: `qfbench2 submission pack` derives and writes that from the Team Key.

team_id and the toolkit: `pack` REFUSES a descriptor whose team_id differs from the derived one.
A draft may therefore omit team_id or carry a placeholder; `pack-input` writes the temporary copy
`pack` reads, with a placeholder team_id left out (a real-looking `team-<32 hex>` is kept, so the
toolkit can still catch a wrong one).

Nothing here reads, asks for or prints the Team Key or the claim proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import zipfile

import qfbench2_common
from qfbench2_common.contracts.descriptor import SubmissionDescriptor, seal_descriptor_digest

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TWELVE = {"category", "competition_id", "descriptor_digest", "image", "image_access",
          "interface_version", "license", "models", "phase", "schema_version", "team_id", "track"}


def _schema_is_current() -> None:
    """The stale-toolkit guard from SUBMISSION-DESCRIPTOR.md, verbatim in spirit."""
    s = json.loads((pathlib.Path(qfbench2_common.__file__).parent
                    / "schemas/submission.schema.json").read_text(encoding="utf-8"))
    ok = "image" in s["properties"] and s.get("additionalProperties") is False and len(s["required"]) == 12
    if not ok:
        sys.exit("FAIL: installed qfbench2-common carries a stale descriptor schema; run make_venv.sh --recreate")


DERIVED_ID = re.compile(r"team-[0-9a-f]{32}")
PLACEHOLDER_ID = "team-" + "0" * 32


def _semantic_checks(d: dict) -> list[str]:
    """Things the schema/parser do NOT enforce for this track (see SUBMISSION-DESCRIPTOR.md)."""
    problems = []
    keys = set(d) | {"team_id"}  # team_id may be absent from a draft: pack derives it
    if keys != TWELVE:
        extra, missing = sorted(keys - TWELVE), sorted(TWELVE - keys)
        problems.append(f"top-level keys must be exactly the twelve (extra={extra}, missing={missing})")
    expect = {"interface_version": "2.0", "track": "forecasting", "category": "api",
              "image_access": "public"}
    for k, v in expect.items():
        if d.get(k) != v:
            problems.append(f"{k} is {d.get(k)!r}, expected {v!r}")
    cid, phase = d.get("competition_id", ""), d.get("phase", "")
    if cid != f"agenthon2026-forecasting-{phase}":
        problems.append(f"competition_id {cid!r} does not match phase {phase!r} "
                        f"(expected 'agenthon2026-forecasting-{phase}')")
    if not isinstance(d.get("models"), list):
        problems.append("models must be a list ([] for a model-free engine)")
    return problems


def _with_team_id(d: dict) -> dict:
    return d if "team_id" in d else {**d, "team_id": PLACEHOLDER_ID}


def _parse(d: dict) -> SubmissionDescriptor:
    return SubmissionDescriptor.from_mapping(seal_descriptor_digest(_with_team_id(d)))


def _team_id_note(d: dict) -> None:
    tid = d.get("team_id")
    if tid is None:
        print("team_id: absent (pack derives it from the team number and Team Key)")
    elif DERIVED_ID.fullmatch(str(tid)):
        print(f"team_id: {tid} (pack refuses it unless it is the id derived from your number+key)")
    else:
        print(f"team_id: {tid!r} is a placeholder; pack.sh gives the toolkit a copy without it")


def cmd_fill(a: argparse.Namespace) -> int:
    _schema_is_current()
    if not DIGEST_RE.match(a.digest):
        sys.exit(f"FAIL: digest {a.digest!r} is not sha256:<64 lowercase hex>")
    path = pathlib.Path(a.descriptor)
    raw_text = path.read_text(encoding="utf-8")
    d = json.loads(raw_text)  # dict keeps the author's key order
    if not isinstance(d.get("image"), dict):
        sys.exit("FAIL: descriptor has no 'image' object; fix the draft first (image must be an object)")
    before = dict(d["image"])
    d["image"]["registry"] = a.registry
    d["image"]["repository"] = a.repository
    d["image"]["digest"] = a.digest
    sealed = seal_descriptor_digest(d)
    parsed = _parse(d)  # raises on any contract violation
    problems = _semantic_checks(sealed)
    for p in problems:
        print(f"FAIL: {p}")
    if problems:
        return 1
    print(f"image before: {before}")
    print(f"image after : {parsed.image_reference()}")
    print(f"descriptor_digest: {sealed['descriptor_digest']}")
    _team_id_note(d)
    if a.dry_run:
        print("dry run: file not written")
        return 0
    indent = 2
    m = re.search(r"\n( +)\"", raw_text)
    if m:
        indent = len(m.group(1))
    path.write_text(json.dumps(sealed, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    _schema_is_current()
    d = json.loads(pathlib.Path(a.descriptor).read_text(encoding="utf-8"))
    parsed = _parse(d)
    problems = _semantic_checks(d)
    for p in problems:
        print(f"FAIL: {p}")
    print(f"image: {parsed.image_reference()}  models: {len(parsed.models)}  license: {parsed.license}")
    _team_id_note(d)
    return 1 if problems else 0


def cmd_pack_input(a: argparse.Namespace) -> int:
    """Write the copy `qfbench2 submission pack` reads: identical, minus a placeholder team_id."""
    d = json.loads(pathlib.Path(a.descriptor).read_text(encoding="utf-8"))
    tid = d.get("team_id")
    if tid is not None and not DERIVED_ID.fullmatch(str(tid)):
        d = {k: v for k, v in d.items() if k != "team_id"}
        print(f"note: placeholder team_id {tid!r} left out of the copy given to pack")
    pathlib.Path(a.out).write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def cmd_verify_zip(a: argparse.Namespace) -> int:
    _schema_is_current()
    fails: list[str] = []
    with zipfile.ZipFile(a.zip) as z:
        names = sorted(z.namelist())
        if names != ["submission.json", "team-claim.json"]:
            fails.append(f"zip members are {names}, expected exactly submission.json + team-claim.json")
        desc_bytes = z.read("submission.json")
        claim = json.loads(z.read("team-claim.json"))
    d = json.loads(desc_bytes)
    try:
        parsed = SubmissionDescriptor.from_mapping(d)  # also verifies descriptor_digest as sealed
    except Exception as e:  # noqa: BLE001 - report, do not crash
        fails.append(f"descriptor in zip does not parse: {e}")
        parsed = None
    fails += _semantic_checks(d)
    if parsed is not None:
        if parsed.image_digest != a.digest:
            fails.append(f"image.digest in zip {parsed.image_digest} != pushed digest {a.digest}")
        if a.repository and parsed.repository != a.repository:
            fails.append(f"image.repository in zip {parsed.repository} != {a.repository}")
        if not re.fullmatch(r"team-[0-9a-f]{32}", parsed.team_id):
            fails.append(f"team_id {parsed.team_id!r} is not a derived id (team-<32 hex>)")
    if set(claim) != {"schema_version", "site_team_id", "descriptor_sha256", "proof"}:
        fails.append(f"team-claim.json keys are {sorted(claim)}")
    if claim.get("schema_version") != "2.0":
        fails.append(f"team-claim schema_version {claim.get('schema_version')!r} != '2.0'")
    want = hashlib.sha256(desc_bytes).hexdigest()
    got = str(claim.get("descriptor_sha256", ""))
    if got.removeprefix("sha256:") != want:
        fails.append("team-claim descriptor_sha256 does not match the submission.json bytes in the zip")
    if not re.fullmatch(r"[0-9a-f]{64}", str(claim.get("proof", ""))):
        fails.append("team-claim proof is not 64 hex characters")
    for f in fails:
        print(f"FAIL: {f}")
    if not fails and parsed is not None:
        print(f"OK  {a.zip}")
        print(f"    image      {parsed.image_reference()}")
        print(f"    team_id    {parsed.team_id}  (site team number {claim.get('site_team_id')})")
        print(f"    competition {parsed.competition_id}  phase {parsed.phase}  category {parsed.category}")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fill")
    f.add_argument("--descriptor", required=True)
    f.add_argument("--registry", default="ghcr.io")
    f.add_argument("--repository", required=True)
    f.add_argument("--digest", required=True)
    f.add_argument("--dry-run", action="store_true")
    f.set_defaults(func=cmd_fill)
    c = sub.add_parser("check")
    c.add_argument("--descriptor", required=True)
    c.set_defaults(func=cmd_check)
    pi = sub.add_parser("pack-input")
    pi.add_argument("--descriptor", required=True)
    pi.add_argument("--out", required=True)
    pi.set_defaults(func=cmd_pack_input)
    v = sub.add_parser("verify-zip")
    v.add_argument("--zip", required=True)
    v.add_argument("--digest", required=True)
    v.add_argument("--repository", default="")
    v.set_defaults(func=cmd_verify_zip)
    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
