"""Read one Track 1 unit the way the platform mounts it at /input.

What the platform gives us (track1-coding-public issue #28): the unit directory as published with
`checks/` removed -- `instruction.md`, `card.toml`, `manifest.json`, `environment/Dockerfile` and
everything under `environment/data/`. Nothing from the unit's Dockerfile runs, so the instruction's
`/app/...` paths describe an image that does not exist here; the Dockerfile's COPY lines give the
mapping from those paths to the real files under `environment/data/`.

This module is pure standard library plus optional pandas/pyarrow/openpyxl for file previews
(every preview is wrapped: a preview failure is never a unit failure).
"""
from __future__ import annotations

import io
import json
import os
import re
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

CANARY_LINE = re.compile(r"^.*(BENCHMARK DATA SHOULD NEVER APPEAR|canary GUID|canary_guid).*$", re.I | re.M)
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

#: Preview budgets (characters). The House model's context is large (the served window is not
#: published; a request that does not fit is refused before admission, so the loop retries with
#: `compact_unit`), and a typical unit prompt is 6-30k characters. Script / markdown / text inputs
#: are shown whole up to FULL_TEXT_CHARS: a "debug this template.py" unit cannot be solved from
#: its first 60 lines.
PREVIEW_FILE_CHARS = 1400
FULL_TEXT_CHARS = 20000
FULL_JSON_CHARS = 6000
PREVIEW_TOTAL_CHARS = 48000
COMPACT_FILE_CHARS = 300
MAX_LISTED_FILES = 80
META_FILES = {"instruction.md", "card.toml", "manifest.json", "README.md"}
META_DIRS = {"checks", "tests", "solution", "dev", "__pycache__"}


@dataclass
class DataFile:
    rel: str            # path relative to environment/data (posix)
    size: int
    preview: str = ""


@dataclass
class Unit:
    task_dir: Path
    unit_id: str
    instruction: str                    # instruction.md with canary lines removed
    instruction_raw: str
    card: dict
    timeout_sec: float
    canaries: list[str]                 # GUIDs that must never reach an output file
    data_dir: Path | None
    files: list[DataFile] = field(default_factory=list)
    dockerfile: str = ""
    mapping: dict[str, str] = field(default_factory=dict)   # instruction path -> real path (posix)
    notes: list[str] = field(default_factory=list)
    checks_text: str = ""               # the unit's own checker source when the platform ships it (it normally does not)
    checks_names: list[str] = field(default_factory=list)   # output file names quoted in that checker

    @property
    def data_dir_posix(self) -> str:
        return self.data_dir.as_posix() if self.data_dir else ""


# --------------------------------------------------------------------------- reading


def _read_text(p: Path, limit: int = 400_000) -> str:
    try:
        b = p.read_bytes()[:limit]
        return b.decode("utf-8", "replace")
    except OSError:
        return ""


def load_unit(task_dir: Path) -> Unit:
    task_dir = Path(task_dir)
    instr_raw = _read_text(task_dir / "instruction.md")
    card: dict = {}
    try:
        card = tomllib.loads(_read_text(task_dir / "card.toml"))
    except Exception:  # noqa: BLE001
        card = {}
    unit_id = str((card.get("task") or {}).get("id") or task_dir.name)
    try:
        timeout = float((card.get("agent") or {}).get("timeout_sec") or 1800.0)
    except (TypeError, ValueError):
        timeout = 1800.0
    canaries = set()
    g = (card.get("contamination") or {}).get("canary_guid")
    if isinstance(g, str) and UUID_RE.fullmatch(g.strip()):
        canaries.add(g.strip().lower())
    for m in UUID_RE.findall(instr_raw):
        canaries.add(m.lower())
    env_dir = task_dir / "environment"
    dockerfile = _read_text(env_dir / "Dockerfile")
    for m in UUID_RE.findall(dockerfile):
        canaries.add(m.lower())
    data_dir = _find_data_dir(task_dir)
    instruction = CANARY_LINE.sub("", instr_raw).strip()
    u = Unit(task_dir=task_dir, unit_id=unit_id, instruction=instruction, instruction_raw=instr_raw,
             card=card, timeout_sec=timeout, canaries=sorted(canaries), data_dir=data_dir, dockerfile=dockerfile)
    u.files = _list_files(data_dir, is_root=data_dir in (task_dir, env_dir)) if data_dir else []
    u.mapping = _copy_mapping(dockerfile, data_dir, u.files)
    _add_previews(u)
    _read_checks(u)
    return u


def _find_data_dir(task_dir: Path) -> Path | None:
    """`environment/data` as published; otherwise any other layout a roster might use (`data/`,
    or files beside instruction.md), so an unexpected Final layout still exposes its inputs."""
    for cand in (task_dir / "environment" / "data", task_dir / "data", task_dir / "environment"):
        if cand.is_dir() and any(p.is_file() and p.name not in META_FILES and p.name != "Dockerfile" for p in cand.rglob("*")):
            return cand
    others = [p for p in task_dir.rglob("*") if p.is_file() and p.name not in META_FILES
              and not (set(p.relative_to(task_dir).parts[:-1]) & META_DIRS)]
    return task_dir if others else None


def _list_files(data_dir: Path, is_root: bool = False) -> list[DataFile]:
    out: list[DataFile] = []
    for p in sorted(data_dir.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(data_dir)
        if is_root and (p.name in META_FILES and len(rel.parts) == 1 or p.name == "Dockerfile" or set(rel.parts[:-1]) & META_DIRS):
            continue                      # the unit's own metadata when the data sits beside instruction.md
        try:
            size = p.stat().st_size
        except OSError:
            size = -1
        out.append(DataFile(rel=rel.as_posix(), size=size))
    return out


def _read_checks(u: Unit) -> None:
    """The platform strips `checks/` from /input (issue #28), but the starter pack says to read it
    whenever it is present: it is the only machine-readable statement of the output contract."""
    checks = u.task_dir / "checks"
    if not checks.is_dir():
        return
    parts = []
    names: list[str] = []
    inputs = {f.rel.split("/")[-1] for f in u.files}
    for p in sorted(checks.glob("*.py")):
        txt = CANARY_LINE.sub("", _read_text(p, 200_000))
        for c in u.canaries:
            txt = txt.replace(c, "<redacted>")
        parts.append(f"--- checks/{p.name}\n{txt[:FULL_TEXT_CHARS]}")
        for m in re.finditer(r"""['"]([A-Za-z0-9_./-]+\.(?:json|csv|parquet|pqt|png|html|txt|tsv|xlsx))['"]""", txt):
            n = m.group(1)
            if "/" in n and not n.startswith(("/app/output/", "/output/")):
                continue
            n = n.split("/")[-1]
            if n in inputs or n in ("reward.json", "pytest_report.json", "expected.json", "checkpoints.json"):
                continue                  # the unit's own inputs / the scorer's artifacts are not deliverables
            if n not in names:
                names.append(n)
    u.checks_text = "\n".join(parts)[:FULL_TEXT_CHARS]
    u.checks_names = names


def compact_unit(u: Unit) -> Unit:
    """The same unit with previews cut to a few lines each: used when a request is refused for
    not fitting the model's context window."""
    import copy

    c = copy.copy(u)
    c.files = [DataFile(f.rel, f.size, f.preview[:COMPACT_FILE_CHARS]) for f in u.files]
    c.checks_text = u.checks_text[:3000]
    return c


# --------------------------------------------------------------------------- Dockerfile COPY mapping


def _copy_mapping(dockerfile: str, data_dir: Path | None, files: list[DataFile]) -> dict[str, str]:
    """Instruction-side path -> real path, from the unit Dockerfile's COPY lines.

    The build context of a unit Dockerfile is `environment/`, so `COPY data/x.csv /app/x.csv`
    means `/input/environment/data/x.csv` is what the instruction calls `/app/x.csv`.
    """
    mapping: dict[str, str] = {}
    if data_dir is None:
        return mapping
    real_root = data_dir.as_posix()
    names = {f.rel for f in files}
    for line in dockerfile.splitlines():
        s = line.strip()
        if not s.upper().startswith("COPY "):
            continue
        parts = [t for t in s[5:].split() if not t.startswith("--")]
        if len(parts) < 2:
            continue
        src, dst = parts[0], parts[-1]
        src = src.strip("\"'").lstrip("./")
        dst = dst.strip("\"'")
        if not src.startswith("data"):
            continue
        src_rel = src[4:].lstrip("/")          # path inside data/
        src_is_dir = src_rel == "" or src.endswith("/") or (data_dir / src_rel).is_dir()
        if src_is_dir:
            base = dst.rstrip("/") or "/"
            prefix = src_rel.rstrip("/")
            for rel in names:
                if prefix and not rel.startswith(prefix + "/"):
                    continue
                tail = rel[len(prefix) + 1:] if prefix else rel
                mapping[f"{base}/{tail}".replace("//", "/")] = f"{real_root}/{rel}"
        else:
            if src_rel not in names:
                continue
            if dst.endswith("/"):
                image_path = dst + Path(src_rel).name
            else:
                image_path = dst
            mapping[image_path] = f"{real_root}/{src_rel}"
    # Convention fallback: the instruction's `/app/data/<file>` is `environment/data/<file>`.
    for rel in names:
        mapping.setdefault(f"/app/data/{rel}", f"{real_root}/{rel}")
    return mapping


# --------------------------------------------------------------------------- previews


def _preview_bytes(p: Path, n: int) -> bytes:
    try:
        with open(p, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def _text_head(p: Path, chars: int, lines: int = 12) -> str:
    raw = _preview_bytes(p, chars * 3).decode("utf-8", "replace")
    out = []
    total = 0
    for ln in raw.splitlines()[:lines]:
        ln = ln[:300]
        out.append(ln)
        total += len(ln) + 1
        if total > chars:
            break
    return "\n".join(out)


def _count_lines(p: Path, cap_bytes: int = 64_000_000) -> int | None:
    try:
        if p.stat().st_size > cap_bytes:
            return None
        n = 0
        with open(p, "rb") as fh:
            for _ in fh:
                n += 1
        return n
    except OSError:
        return None


def _json_shape(obj, depth: int = 0, budget: int = 900) -> str:
    if depth > 3 or budget <= 0:
        return "..."
    if isinstance(obj, dict):
        items = list(obj.items())
        parts = []
        for k, v in items[:12]:
            parts.append(f"{json.dumps(str(k))}: {_json_shape(v, depth + 1, budget // 4)}")
        more = f", ...(+{len(items) - 12} keys)" if len(items) > 12 else ""
        return "{" + ", ".join(parts) + more + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        inner = _json_shape(obj[0], depth + 1, budget // 2)
        return f"[{inner}, ... ({len(obj)} items)]" if len(obj) > 1 else f"[{inner}]"
    if isinstance(obj, str):
        return json.dumps(obj[:60] + ("..." if len(obj) > 60 else ""))
    return json.dumps(obj)[:40]


def _preview_one(p: Path, rel: str) -> str:
    ext = p.suffix.lower()
    size = p.stat().st_size if p.exists() else 0
    try:
        if ext in (".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".cfg", ".ini") and size <= FULL_TEXT_CHARS:
            return "[complete file]\n" + _read_text(p, FULL_TEXT_CHARS + 10).rstrip()
        if ext in (".csv", ".tsv", ".txt", ".md"):
            n = _count_lines(p)
            head = _text_head(p, PREVIEW_FILE_CHARS, lines=8 if ext in (".csv", ".tsv") else 20)
            return f"[{'lines: ' + str(n) if n is not None else ''}]\n{head}"
        if ext == ".jsonl":
            n = _count_lines(p)
            first = _preview_bytes(p, 4000).decode("utf-8", "replace").splitlines()[:1]
            shape = ""
            if first:
                try:
                    shape = _json_shape(json.loads(first[0]))
                except Exception:  # noqa: BLE001
                    shape = first[0][:400]
            return f"[json lines: {n}] first record shape: {shape}"
        if ext == ".json":
            raw = _preview_bytes(p, 2_000_000)
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                return _text_head(p, PREVIEW_FILE_CHARS)
            if size <= FULL_JSON_CHARS:
                return "[complete file]\n" + raw.decode("utf-8", "replace").rstrip()
            return "shape: " + _json_shape(obj)
        if ext in (".parquet", ".pqt"):
            try:
                import pyarrow.parquet as pq

                pf = pq.ParquetFile(p)
                schema = pf.schema_arrow
                cols = ", ".join(f"{f.name}:{f.type}" for f in schema)
                rows = pf.metadata.num_rows
                head = pf.read_row_group(0).slice(0, 3).to_pandas().to_string(max_cols=14, max_colwidth=24)
                return f"[parquet rows: {rows}] columns: {cols}\nfirst rows:\n{head}"
            except Exception as exc:  # noqa: BLE001
                return f"[parquet; preview unavailable: {type(exc).__name__}]"
        if ext in (".xlsx", ".xlsm", ".xls"):
            try:
                import openpyxl

                wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
                parts = []
                for ws in wb.worksheets[:4]:
                    rows = []
                    for i, row in enumerate(ws.iter_rows(values_only=True)):
                        rows.append(", ".join("" if v is None else str(v)[:20] for v in row[:12]))
                        if i >= 4:
                            break
                    parts.append(f"sheet {ws.title!r} (dims {ws.dimensions}):\n" + "\n".join(rows))
                return "\n".join(parts)[:PREVIEW_FILE_CHARS]
            except Exception as exc:  # noqa: BLE001
                return f"[excel; preview unavailable: {type(exc).__name__}]"
        if ext == ".zip":
            try:
                with zipfile.ZipFile(p) as z:
                    names = z.namelist()
                    shown = ", ".join(names[:25]) + (f", ... (+{len(names) - 25})" if len(names) > 25 else "")
                    return f"[zip with {len(names)} members] {shown}"
            except Exception as exc:  # noqa: BLE001
                return f"[zip; unreadable: {type(exc).__name__}]"
        if ext in (".xml", ".html", ".htm"):
            return _text_head(p, 700, lines=10)
        if ext == ".py":
            return "[first lines of a longer script]\n" + _text_head(p, FULL_TEXT_CHARS // 2, lines=400)
        return _text_head(p, 400, lines=6)
    except Exception as exc:  # noqa: BLE001
        return f"[preview unavailable: {type(exc).__name__}]"


def _add_previews(u: Unit) -> None:
    if not u.data_dir:
        return
    total = 0
    for f in u.files[:MAX_LISTED_FILES]:
        if total >= PREVIEW_TOTAL_CHARS:
            f.preview = "[preview omitted: budget]"
            continue
        pv = _preview_one(u.data_dir / f.rel, f.rel)
        cap = FULL_TEXT_CHARS if pv.startswith(("[complete file]", "[first lines")) else PREVIEW_FILE_CHARS
        pv = pv[:min(cap, PREVIEW_TOTAL_CHARS - total)]
        # never show a canary to the model (it is in instruction.md too, which we strip)
        for c in u.canaries:
            pv = pv.replace(c, "<redacted>")
        f.preview = pv
        total += len(pv)


# --------------------------------------------------------------------------- rendering for prompts


def files_block(u: Unit) -> str:
    if not u.files:
        return "The unit ships no data files: every input is given in the instruction text.\n"
    lines = [f"Real data directory: {u.data_dir_posix}/  ({len(u.files)} files)"]
    for f in u.files[:MAX_LISTED_FILES]:
        lines.append(f"\n--- {u.data_dir_posix}/{f.rel}  ({f.size} bytes)")
        if f.preview:
            lines.append(f.preview)
    if len(u.files) > MAX_LISTED_FILES:
        lines.append(f"\n... and {len(u.files) - MAX_LISTED_FILES} more files")
    return "\n".join(lines) + "\n"


def mapping_block(u: Unit) -> str:
    """Only the non-trivial renames/relocations: a compact table the model can apply literally."""
    rows = []
    for image_path, real in sorted(u.mapping.items()):
        if image_path.startswith("/app/data/") and real.endswith(image_path[len("/app/data/"):]) and \
                image_path[len("/app/data/"):] == real[len(u.data_dir_posix) + 1:]:
            continue   # the plain /app/data/<file> convention is stated once below
        rows.append(f"  {image_path}  ->  {real}")
    out = []
    if u.data_dir:
        out.append(f"  /app/data/<file>  ->  {u.data_dir_posix}/<file>   (same file names)")
    out.extend(rows[:60])
    if not out:
        out.append("  (no data files)")
    return "\n".join(out) + "\n"


OUTPUT_EXTS = ("json", "csv", "parquet", "pqt", "png", "html", "htm", "txt", "tsv", "xlsx", "md", "svg", "pdf", "jpg", "jpeg")


def named_output_files(instruction: str, input_names: set[str]) -> list[str]:
    """Second-tier fallback: bare file names the task quotes (`summary.json`, **`greeks.csv`**) in
    or after its output section, minus the unit's own input files. 39 of the 86 public units name
    their deliverables only this way ("Save all results to /app/output/: 1. option_values.json ...")."""
    text = instruction
    m = re.search(r"^#+ .*(output|deliverable|required files|save)", text, re.I | re.M)
    if m:
        text = text[m.start():]
    found: list[str] = []
    pat = (r"`([A-Za-z0-9_][A-Za-z0-9_.-]*\.([A-Za-z0-9]{1,5}))`"            # `greeks.csv`
           r"|\*\*([A-Za-z0-9_][A-Za-z0-9_.-]*\.([A-Za-z0-9]{1,5}))\*\*"      # **greeks.csv**
           r"|^#+[^\n`]*?([A-Za-z0-9_][A-Za-z0-9_.-]*\.([A-Za-z0-9]{1,5}))[ \t\r]*$")   # ### 2. greeks.csv (CRLF-tolerant)
    for m in re.finditer(pat, text, re.M):
        name = m.group(1) or m.group(3) or m.group(5)
        ext = (m.group(2) or m.group(4) or m.group(6)).lower()
        if ext not in OUTPUT_EXTS or name in input_names or name.lower() in ("reward.json", "pytest_report.json", "reward.txt"):
            continue
        if name not in found:
            found.append(name)
    return found


def regex_deliverables(instruction: str) -> list[str]:
    """Output paths named in the instruction (fallback when the model's contract is unusable)."""
    found: list[str] = []
    for m in re.finditer(r"(?<![\w.])(/app/output|/output)/([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,8})", instruction):
        name = m.group(2).rstrip(".")
        base = Path(name).name.lower()
        if base in ("reward.json", "pytest_report.json", "reward.txt") or "<" in name or ">" in name:
            continue
        if name not in found:
            found.append(name)
    return found
