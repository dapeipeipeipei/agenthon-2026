"""Candidate workspaces and cross-candidate agreement (v3).

Every candidate script runs in its own sub-directory of the output directory (`_jp_c<i>`), so the
candidates never overwrite each other and their results can be compared. The chosen candidate is
copied ("promoted") into the output directory itself as soon as it is known -- an early draft that
later choices replace -- and every `_jp_*` workspace is removed before the output tree is finalised.

Agreement: the numbers a candidate wrote are flattened into leaves (JSON paths; CSV/parquet
columns, row by row up to a cap) and two candidates agree on a leaf when the values match within a
relative 1e-3 (absolute 1e-9) -- loose enough for different but equivalent numerics, tight enough
to separate a different formula or convention. Independent samples from the same model that agree
are far more likely to be right than a lone outlier; where they disagree, the judge call sees
exactly which values differ.
"""
from __future__ import annotations

import csv
import json
import math
import os
import shutil
from pathlib import Path

WORK_PREFIX = "_jp_"
MAX_LEAVES_PER_FILE = 4000
MAX_ROWS = 3000


# --------------------------------------------------------------------------- workspaces


def workspace(out_dir: Path, name: str) -> Path:
    d = out_dir / f"{WORK_PREFIX}{name}"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def clear_workspaces(out_dir: Path) -> None:
    try:
        for p in out_dir.iterdir():
            if p.name.startswith(WORK_PREFIX):
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    try:
                        p.unlink()
                    except OSError:
                        pass
    except OSError:
        pass


def promote(src: Path, out_dir: Path) -> int:
    """Make the output directory hold exactly what `src` holds (workspaces untouched). Returns the
    number of files copied. Links and special files in `src` are not copied."""
    for p in list(out_dir.iterdir()):
        if p.name.startswith(WORK_PREFIX):
            continue
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
        except OSError:
            pass
    n = 0
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink() and not d.startswith(WORK_PREFIX)]
        for f in files:
            sp = Path(root) / f
            if sp.is_symlink() or not sp.is_file():
                continue
            dp = out_dir / rel / f
            try:
                dp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(sp, dp)
                n += 1
            except OSError:
                pass
    return n


# --------------------------------------------------------------------------- fingerprints


def _num(x):
    if isinstance(x, bool):
        return float(x)
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        s = x.strip()
        if not s or len(s) > 40:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _flatten_json(obj, prefix: str, out: dict, depth: int = 0) -> None:
    if len(out) >= MAX_LEAVES_PER_FILE or depth > 30:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten_json(v, f"{prefix}.{k}", out, depth + 1)
    elif isinstance(obj, list):
        step = max(1, len(obj) // 500)
        for i in range(0, len(obj), step):
            _flatten_json(obj[i], f"{prefix}[{i}]", out, depth + 1)
        out[f"{prefix}#len"] = float(len(obj))
    else:
        v = _num(obj)
        out[prefix] = v if v is not None else (None if obj is None else str(obj)[:80])


def _flatten_table(header: list[str], rows: list[list], prefix: str, out: dict, nrows: int) -> None:
    out[f"{prefix}#rows"] = float(nrows)
    out[f"{prefix}#cols"] = "|".join(header)[:400]
    if not rows:
        return
    per_col = max(1, MAX_LEAVES_PER_FILE // max(1, len(header)))
    step = max(1, len(rows) // per_col)
    for j, col in enumerate(header[:200]):
        for i in range(0, len(rows), step):
            r = rows[i]
            if j < len(r):
                v = _num(r[j])
                out[f"{prefix}:{col}[{i}]"] = v if v is not None else str(r[j])[:60]


def fingerprint(d: Path, names: list[str]) -> dict:
    """Leaves of every deliverable in `d` (missing / unreadable files give a marker leaf)."""
    out: dict = {}
    for name in names:
        p = d / name
        if not p.is_file():
            out[f"{name}#missing"] = 1.0
            continue
        ext = p.suffix.lower()
        try:
            if ext == ".json":
                _flatten_json(json.loads(p.read_text(encoding="utf-8", errors="replace")), name, out)
            elif ext in (".csv", ".tsv"):
                with open(p, encoding="utf-8", errors="replace", newline="") as fh:
                    rd = csv.reader(fh, delimiter="\t" if ext == ".tsv" else ",")
                    header = next(rd, [])
                    rows = []
                    n = 0
                    for r in rd:
                        n += 1
                        if len(rows) < MAX_ROWS:
                            rows.append(r)
                _flatten_table(header, rows, name, out, n)
            elif ext in (".parquet", ".pqt"):
                import pyarrow.parquet as pq

                pf = pq.ParquetFile(p)
                t = pf.read_row_group(0) if pf.num_row_groups else None
                header = [f.name for f in pf.schema_arrow]
                rows = []
                if t is not None:
                    cols = [t.column(i).to_pylist()[:MAX_ROWS] for i in range(t.num_columns)]
                    rows = [list(r) for r in zip(*cols)] if cols else []
                _flatten_table(header, rows, name, out, pf.metadata.num_rows)
            else:
                out[f"{name}#size_class"] = float(min(9, int(math.log10(max(1, p.stat().st_size)))))
        except Exception:  # noqa: BLE001
            out[f"{name}#unreadable"] = 1.0
    return out


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        if math.isinf(a) or math.isinf(b):
            return a == b
        return abs(a - b) <= 1e-9 + 1e-3 * max(abs(a), abs(b))
    return a == b


def agreement(fa: dict, fb: dict) -> float:
    """Share of the union of leaves on which both candidates hold the same value (0..1)."""
    keys = set(fa) | set(fb)
    if not keys:
        return 0.0
    same = sum(1 for k in keys if k in fa and k in fb and _same(fa[k], fb[k]))
    return same / len(keys)


def diff_table(fps: dict[str, dict], limit: int = 40) -> str:
    """The leaves on which the candidates disagree, numeric ones first, as text for the judge."""
    labels = list(fps)
    keys = sorted(set().union(*[set(f) for f in fps.values()]))
    rows = []
    for k in keys:
        vals = [fps[l].get(k, "<absent>") for l in labels]
        if all(_same(vals[0], v) for v in vals[1:]):
            continue
        rows.append((0 if all(isinstance(v, float) for v in vals) else 1, k, vals))
    rows.sort(key=lambda r: (r[0], r[1]))
    n_diff = len(rows)
    lines = [f"{n_diff} of {len(keys)} output values differ between candidates " + ", ".join(labels) + "."]
    for _, k, vals in rows[:limit]:
        lines.append(f"- {k}: " + " | ".join(f"{l}={_fmt(v)}" for l, v in zip(labels, vals)))
    if n_diff > limit:
        lines.append(f"... and {n_diff - limit} more")
    return "\n".join(lines)


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.10g}"
    return repr(v)[:60]
