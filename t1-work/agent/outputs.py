"""Deliverables: the contract, checks on what was written, stubs, previews for the review call,
path rewriting of generated code, and the final sanitation of the output tree against the
organizers' output-folder rules (README "Output folder rules").
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import shutil
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .unit import UUID_RE, Unit, regex_deliverables

MAX_FILES = 256
MAX_NODES = 4096
MAX_BYTES = 64 * (1 << 20)
MAX_DEPTH = 7


@dataclass
class Deliverable:
    name: str                   # path relative to the output directory (posix)
    fmt: str = "other"          # json|csv|parquet|png|html|txt|other
    spec: str = ""

    @property
    def ext(self) -> str:
        return Path(self.name).suffix.lower().lstrip(".")


@dataclass
class Contract:
    deliverables: list[Deliverable] = field(default_factory=list)
    plan: dict | None = None
    source: str = "none"        # model | regex | none


# --------------------------------------------------------------------------- contract


def _norm_out_name(p: str, out_dir: str) -> str | None:
    p = str(p).strip().replace("\\", "/")
    for prefix in (out_dir.rstrip("/") + "/", "/app/output/", "/output/", "./", "output/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
            break
    if p.startswith("/"):
        return None
    p = p.strip("/")
    if not p or p in ("reward.json", "pytest_report.json", "reward.txt") or ".." in p.split("/"):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./ -]*", p):
        return None
    return p


def contract_from_model(obj: dict | None, u: Unit, out_dir: str) -> Contract:
    c = Contract()
    if isinstance(obj, dict):
        for d in obj.get("deliverables") or []:
            if not isinstance(d, dict):
                continue
            name = _norm_out_name(str(d.get("path") or d.get("name") or ""), out_dir)
            if not name or any(x.name == name for x in c.deliverables):
                continue
            fmt = str(d.get("format") or Path(name).suffix.lstrip(".") or "other").lower()
            c.deliverables.append(Deliverable(name=name, fmt=fmt, spec=str(d.get("spec") or "")[:400]))
        plan = {k: obj.get(k) for k in ("plan", "pitfalls") if obj.get(k)}
        c.plan = plan or None
        c.source = "model" if c.deliverables else "none"
    # the task text itself is the second source: names it mentions that the model missed
    for name in regex_deliverables(u.instruction):
        if not any(x.name == name for x in c.deliverables):
            c.deliverables.append(Deliverable(name=name, fmt=Path(name).suffix.lstrip(".").lower() or "other", spec="(named in the task text)"))
            if c.source == "none":
                c.source = "regex"
    return c


# --------------------------------------------------------------------------- checks on written files


def _json_bad_numbers(obj, depth: int = 0) -> bool:
    if depth > 50:
        return False
    if isinstance(obj, float):
        return not math.isfinite(obj)
    if isinstance(obj, dict):
        return any(_json_bad_numbers(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return any(_json_bad_numbers(v, depth + 1) for v in obj)
    return False


def check_deliverables(out_dir: Path, deliverables: list[Deliverable]) -> list[str]:
    """Problems (empty list = everything we can verify without the checker is fine)."""
    problems: list[str] = []
    for d in deliverables:
        p = out_dir / d.name
        if not p.is_file():
            problems.append(f"deliverable NOT WRITTEN: {d.name}")
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        if size == 0:
            problems.append(f"deliverable is EMPTY (0 bytes): {d.name}")
            continue
        ext = d.ext
        try:
            if ext == "json":
                raw = p.read_text(encoding="utf-8", errors="replace")
                obj = json.loads(raw)
                if _json_bad_numbers(obj):
                    problems.append(f"{d.name}: contains NaN/Infinity values (not valid JSON numbers); write null or a finite number")
                if obj in ({}, []):
                    problems.append(f"{d.name}: is an empty JSON object/array")
            elif ext in ("csv", "tsv"):
                with open(p, encoding="utf-8", errors="replace", newline="") as fh:
                    head = fh.readline()
                    second = fh.readline()
                if not head.strip():
                    problems.append(f"{d.name}: empty header line")
                elif not second.strip():
                    problems.append(f"{d.name}: has a header but no data rows")
            elif ext in ("parquet", "pqt"):
                import pyarrow.parquet as pq

                md = pq.ParquetFile(p).metadata
                if md.num_rows == 0:
                    problems.append(f"{d.name}: parquet file has zero rows")
            elif ext == "png":
                with open(p, "rb") as fh:
                    if fh.read(8) != b"\x89PNG\r\n\x1a\n":
                        problems.append(f"{d.name}: not a PNG file")
            elif ext in ("html", "htm"):
                if size < 40:
                    problems.append(f"{d.name}: html file is nearly empty")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{d.name}: could not be parsed as {ext}: {type(exc).__name__}: {str(exc)[:160]}")
    return problems


# --------------------------------------------------------------------------- stubs


def write_stub(out_dir: Path, d: Deliverable) -> None:
    """A syntactically valid file of the right type, so the output folder is never empty or
    unparsable. It will not pass the checker; it keeps the unit from being `no_output`."""
    p = out_dir / d.name
    p.parent.mkdir(parents=True, exist_ok=True)
    ext = d.ext
    try:
        if ext == "json":
            p.write_text("{}\n", encoding="utf-8")
        elif ext in ("csv", "tsv"):
            cols = re.findall(r"`([A-Za-z0-9_]+)`", d.spec)[:40]
            sep = "\t" if ext == "tsv" else ","
            p.write_text(sep.join(cols) + "\n" if cols else "value\n0\n", encoding="utf-8")
        elif ext in ("parquet", "pqt"):
            import pyarrow as pa
            import pyarrow.parquet as pq

            pq.write_table(pa.table({"value": pa.array([0.0])}), p)
        elif ext == "png":
            p.write_bytes(_PNG_1x1)
        elif ext in ("html", "htm"):
            p.write_text("<html><body></body></html>\n", encoding="utf-8")
        else:
            p.write_text("\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        try:
            p.write_text("\n", encoding="utf-8")
        except OSError:
            pass


_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c636000020000050001"
    "0d0a2db40000000049454e44ae426082"
)


# --------------------------------------------------------------------------- previews for the review call


def preview_outputs(out_dir: Path, deliverables: list[Deliverable], budget: int = 9000) -> str:
    parts = []
    per = max(800, budget // max(1, len(deliverables)))
    for d in deliverables:
        p = out_dir / d.name
        if not p.is_file():
            parts.append(f"--- {d.name}: MISSING")
            continue
        size = p.stat().st_size
        ext = d.ext
        body = ""
        try:
            if ext == "json":
                raw = p.read_text(encoding="utf-8", errors="replace")
                body = raw if len(raw) <= per else _json_abbrev(raw, per)
            elif ext in ("csv", "tsv"):
                with open(p, encoding="utf-8", errors="replace") as fh:
                    lines = []
                    n = 0
                    for ln in fh:
                        n += 1
                        if len(lines) < 6:
                            lines.append(ln.rstrip("\n")[:300])
                body = f"[rows incl. header: {n}]\n" + "\n".join(lines)
            elif ext in ("parquet", "pqt"):
                import pyarrow.parquet as pq

                pf = pq.ParquetFile(p)
                cols = ", ".join(f"{f.name}:{f.type}" for f in pf.schema_arrow)
                head = pf.read_row_group(0).slice(0, 4).to_pandas().to_string(max_cols=14, max_colwidth=22)
                body = f"[rows: {pf.metadata.num_rows}] columns: {cols}\n{head}"
            elif ext == "png":
                body = "[PNG image]"
            else:
                body = p.read_text(encoding="utf-8", errors="replace")[:per]
        except Exception as exc:  # noqa: BLE001
            body = f"[unreadable: {type(exc).__name__}: {str(exc)[:120]}]"
        parts.append(f"--- {d.name} ({size} bytes)\n{body[:per]}")
    return "\n".join(parts)


def _json_abbrev(raw: str, per: int) -> str:
    try:
        obj = json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw[:per]

    def ab(o, depth=0):
        if isinstance(o, dict):
            items = list(o.items())
            d = {k: ab(v, depth + 1) for k, v in items[:25]}
            if len(items) > 25:
                d["...<%d more keys>" % (len(items) - 25)] = "..."
            return d
        if isinstance(o, list):
            if len(o) > 6:
                return [ab(x, depth + 1) for x in o[:4]] + [f"...<{len(o) - 4} more items>"]
            return [ab(x, depth + 1) for x in o]
        if isinstance(o, str) and len(o) > 120:
            return o[:120] + "..."
        return o

    s = json.dumps(ab(obj), indent=1)
    return s if len(s) <= per else s[:per] + "\n...(truncated)"


# --------------------------------------------------------------------------- code path rewriting


def rewrite_paths(code: str, u: Unit, out_dir: str) -> str:
    """Deterministic safety net: the task's image paths -> the real paths, longest first, and the
    task's output directory spellings -> the real output directory."""
    real_out = out_dir.rstrip("/")
    repl: list[tuple[str, str]] = []
    for image_path, real in u.mapping.items():
        repl.append((image_path, real))
    repl.sort(key=lambda t: -len(t[0]))
    for a, b in repl:
        code = code.replace(a, b)
    if u.data_dir:
        code = code.replace("/app/data/", u.data_dir_posix + "/")
        code = re.sub(r"(?<![\w/])/app/data(?=['\"\s)])", u.data_dir_posix, code)
    if real_out not in ("/app/output", "/output"):
        code = code.replace("/app/output", real_out)
        code = re.sub(r"(?<![\w/])/output(?=[/'\"\s)])", real_out, code)
    elif real_out == "/app/output":
        code = re.sub(r"(?<![\w/])/output(?=[/'\"\s)])", real_out, code)
    return code


# --------------------------------------------------------------------------- output-tree sanitation


def sanitize_tree(out_dir: Path, canaries: list[str], keep_names: set[str] | None = None, log: list[str] | None = None) -> None:
    """Make the output tree acceptable to the organizers' checker, whatever the script left.

    Removes links and special files, oversize files, files beyond the count limit (deliverables
    are kept first), names that are not NFC / contain control characters / backslashes / a drive
    prefix, directories nested too deep; clears mode bits; scrubs canary GUIDs from text files;
    and guarantees at least one regular file.
    """
    log = log if log is not None else []
    keep_names = keep_names or set()
    if not out_dir.is_dir():
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
    entries: list[tuple[Path, int, int]] = []     # (path, depth, size)
    for root, dirs, files in os.walk(out_dir, topdown=True):
        rel_depth = len(Path(root).relative_to(out_dir).parts)
        if rel_depth >= MAX_DEPTH:
            for d in list(dirs):
                _rm(Path(root) / d, log, "too deep")
            dirs[:] = []
        for d in list(dirs):
            dp = Path(root) / d
            if dp.is_symlink() or _bad_name(d):
                _rm(dp, log, "link or bad dir name")
                dirs.remove(d)
        for f in files:
            fp = Path(root) / f
            try:
                st = fp.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                _rm(fp, log, "link/special")
                continue
            if _bad_name(f) or (rel_depth == 0 and re.match(r"^[A-Za-z]:", f)):
                _rm(fp, log, "bad name")
                continue
            if st.st_size > MAX_BYTES:
                _rm(fp, log, "over 64 MiB")
                continue
            if os.name == "posix" and st.st_nlink > 1:
                _rm(fp, log, "hard link")
                continue
            entries.append((fp, rel_depth, st.st_size))
    # case-insensitive / NFC duplicates
    seen: dict[str, Path] = {}
    for fp, _, _ in list(entries):
        key = unicodedata.normalize("NFC", fp.relative_to(out_dir).as_posix()).casefold()
        if key in seen:
            _rm(fp, log, "case/NFC duplicate")
            entries = [e for e in entries if e[0] != fp]
        else:
            seen[key] = fp
    # count and total size: keep deliverables first, then the rest by name
    entries.sort(key=lambda e: (0 if e[0].relative_to(out_dir).as_posix() in keep_names else 1, e[0].as_posix()))
    total = 0
    kept: list[Path] = []
    for i, (fp, _, size) in enumerate(entries):
        if i >= MAX_FILES or total + size > MAX_BYTES:
            _rm(fp, log, "count/size limit")
            continue
        total += size
        kept.append(fp)
    # canary scrub + mode bits
    for fp in kept:
        try:
            os.chmod(fp, 0o644)
        except OSError:
            pass
        if canaries and fp.suffix.lower() in (".json", ".csv", ".txt", ".md", ".html", ".htm", ".tsv", ".py", ".xml", ".yaml", ".yml"):
            _scrub(fp, canaries, log)
    for root, dirs, _files in os.walk(out_dir):
        for d in dirs:
            try:
                os.chmod(Path(root) / d, 0o755)
            except OSError:
                pass
    # prune empty directories (they count toward the node limit)
    for root, dirs, files in os.walk(out_dir, topdown=False):
        if Path(root) != out_dir and not dirs and not files:
            try:
                os.rmdir(root)
            except OSError:
                pass
    if not kept:
        try:
            (out_dir / "results.json").write_text("{}\n", encoding="utf-8")
            log.append("empty tree: wrote results.json stub")
        except OSError:
            pass


def _bad_name(name: str) -> bool:
    if "\\" in name or any(ord(ch) < 32 or ch == "\x7f" for ch in name):
        return True
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return unicodedata.normalize("NFC", name) != name


def _rm(p: Path, log: list[str], why: str) -> None:
    try:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink()
        log.append(f"removed {p.name}: {why}")
    except OSError:
        pass


def _scrub(fp: Path, canaries: list[str], log: list[str]) -> None:
    try:
        raw = fp.read_bytes()
    except OSError:
        return
    low = raw.lower()
    if not any(c.encode() in low for c in canaries):
        return
    text = raw.decode("utf-8", "replace")
    for c in canaries:
        text = re.sub(re.escape(c), "00000000-0000-0000-0000-000000000000", text, flags=re.I)
    try:
        fp.write_text(text, encoding="utf-8")
        log.append(f"scrubbed canary from {fp.name}")
    except OSError:
        pass


def tree_summary(out_dir: Path) -> dict:
    n = 0
    total = 0
    for root, _d, files in os.walk(out_dir):
        for f in files:
            n += 1
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return {"files": n, "bytes": total}
