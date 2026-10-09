"""Content-level regression: every parquet an image wrote reads back as the same table.

``jpsim.parquet_lite`` writes the Track 3 tables without pyarrow, so its bytes differ from the
organizer's references (which are pyarrow's dictionary-encoded pages with statistics). What the
scorer reads is the table, so this tool compares, per unit and per file, what pyarrow (and pandas
through pyarrow) read back from our file and from a reference file:

* Arrow schema equal, metadata included (field names, types, the ``pandas`` block);
* table values equal (``Table.equals``, chunk layout ignored), row count equal;
* pandas round trip: same dtypes (``Int64`` nullable columns included) and ``DataFrame.equals``.

The reference root is either the kit's ``units/`` directory (local: LFS files present) or another
output root written by the byte-identical pyarrow path (CI: the baseline image's output).

Usage: python check_content.py --units <kit>/units --out-root <root> [--ref-root <root>] [--only ...]
Exit 1 on any difference.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import pyarrow as pa
import pyarrow.parquet as pq


def ref_files(unit: pathlib.Path, ref_root: pathlib.Path | None, units_root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """(relative output path, reference file) pairs for one unit."""
    pairs: list[tuple[str, pathlib.Path]] = []
    if (unit / "batch.json").is_file():
        b = json.loads((unit / "batch.json").read_text())
        for s in b["subs"]:
            sub = s["sub"]
            for name in ("trace.parquet", "message_trace.parquet"):
                rel = f"{sub}/{name}"
                if ref_root is not None:
                    pairs.append((rel, ref_root / unit.name / rel))
                else:
                    cands = [unit / "checks" / sub / name, unit / "scenarios" / sub / name, unit / sub / name]
                    hit = next((c for c in cands if c.is_file()), None)
                    if hit is None:
                        hit = next(iter(unit.rglob(f"{sub}/{name}")), cands[0])
                    pairs.append((rel, hit))
    else:
        for name in ("trace.parquet", "message_trace.parquet"):
            pairs.append((name, (ref_root / unit.name / name) if ref_root is not None else unit / name))
    return pairs


def compare(ours: pathlib.Path, ref: pathlib.Path) -> list[str]:
    problems: list[str] = []
    if not ours.is_file():
        return [f"missing {ours.name}"]
    if not ref.is_file():
        return [f"reference missing: {ref}"]
    a, b = pq.read_table(ours), pq.read_table(ref)
    if not a.schema.equals(b.schema, check_metadata=True):
        problems.append(f"schema differs:\n ours: {a.schema}\n ref:  {b.schema}")
    if a.num_rows != b.num_rows:
        problems.append(f"rows {a.num_rows} != {b.num_rows}")
    elif not a.equals(b):
        for name in b.column_names:
            if name in a.column_names and not a[name].equals(b[name]):
                problems.append(f"column {name} differs")
        if not problems:
            problems.append("table differs")
    try:
        da, db = a.to_pandas(), b.to_pandas()
        if list(da.dtypes.astype(str)) != list(db.dtypes.astype(str)):
            problems.append(f"pandas dtypes differ: {list(da.dtypes.astype(str))} vs {list(db.dtypes.astype(str))}")
        elif not da.equals(db):
            problems.append("pandas frames differ")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"pandas round trip failed: {exc}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", required=True, type=pathlib.Path)
    ap.add_argument("--out-root", required=True, type=pathlib.Path)
    ap.add_argument("--ref-root", type=pathlib.Path, default=None,
                    help="another output root (e.g. the baseline image's); default: the kit's reference files")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    names = sorted(p.name for p in args.units.iterdir() if p.is_dir())
    if args.only:
        names = [n for n in names if n in set(args.only)]
    bad = 0
    checked = 0
    for name in names:
        unit = args.units / name
        unit_problems: list[str] = []
        for rel, ref in ref_files(unit, args.ref_root, args.units):
            probs = compare(args.out_root / name / rel, ref)
            checked += 1
            unit_problems += [f"{rel}: {p}" for p in probs]
        if unit_problems:
            bad += 1
            print(f"{name:34} DIFF")
            for p in unit_problems:
                print("    " + p)
        else:
            print(f"{name:34} same table")
    print(f"\n{len(names) - bad}/{len(names)} units read back identical ({checked} files)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
