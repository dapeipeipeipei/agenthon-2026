"""Pipe-table and series extraction from corpus text (no layout assumptions beyond `a | b | c`)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NUM_RE = re.compile(r"^\(?\s*([+\-−]?)\s*\$?\s*([0-9][0-9,]*\.?[0-9]*|\.[0-9]+)\s*\)?\s*(%|bp|bps)?$", re.IGNORECASE)
_DATEISH = re.compile(r"(\d{4}-\d{2}(-\d{2})?)")
#: Longest history kept per series (most recent observations).
MAX_SERIES = 240


def parse_number(cell: str) -> float | None:
    s = cell.strip().replace("−", "-")
    if not s or s in ("--", "-", "—", "n/a", "NA", "na"):
        return None
    m = _NUM_RE.match(s)
    if not m:
        return None
    sign, body = m.group(1), m.group(2).replace(",", "")
    try:
        v = float(body)
    except ValueError:
        return None
    if sign in ("-", "−") or (s.startswith("(") and s.endswith(")")):
        v = -v
    return v


@dataclass
class Table:
    doc_id: str
    header: list[str]
    rows: list[list[str]]
    row_spans: list[tuple[int, int]]  # char offsets of each data row line in the doc text
    header_span: tuple[int, int]
    title_span: tuple[int, int] | None = None
    numeric: dict[int, list[float | None]] = field(default_factory=dict)

    def column(self, j: int) -> list[float | None]:
        if j not in self.numeric:
            self.numeric[j] = [parse_number(r[j]) if j < len(r) else None for r in self.rows]
        return self.numeric[j]

    def key_column(self) -> list[str]:
        return [r[0].strip() if r else "" for r in self.rows]


def find_tables(doc_id: str, text: str, max_tables: int = 50) -> list[Table]:
    tables: list[Table] = []
    pos = 0
    lines: list[tuple[int, int, str]] = []
    for raw in text.split("\n"):
        lines.append((pos, pos + len(raw), raw))
        pos += len(raw) + 1
    i = 0
    n = len(lines)
    while i < n and len(tables) < max_tables:
        s, e, line = lines[i]
        if " | " not in line:
            i += 1
            continue
        ncell = line.count(" | ") + 1
        j = i + 1
        while j < n and lines[j][2].count(" | ") + 1 == ncell and " | " in lines[j][2]:
            j += 1
        if j - i >= 3 and ncell >= 2:
            header = [c.strip() for c in line.split(" | ")]
            rows = [[c.strip() for c in lines[k][2].split(" | ")] for k in range(i + 1, j)]
            spans = [(lines[k][0], lines[k][1]) for k in range(i + 1, j)]
            title = None
            # nearest non-empty line above the header (a caption), for citations
            k = i - 1
            while k >= 0 and not lines[k][2].strip():
                k -= 1
            if k >= 0 and len(lines[k][2]) <= 600:
                title = (lines[k][0], lines[k][1])
            tables.append(Table(doc_id, header, rows, spans, (s, e), title))
        i = max(j, i + 1)
    return tables


def norm_tokens(s: str) -> set[str]:
    s = s.lower().replace("%", " pct ").replace("$", " usd ")
    toks = set(re.findall(r"[a-z]+|\d+", s))
    stop = {"the", "of", "and", "a", "in", "for", "to", "as", "by", "on", "all", "less", "type", "types"}
    return {t for t in toks if t not in stop and len(t) > 1}


def is_dateish(s: str) -> bool:
    return bool(_DATEISH.search(s))


@dataclass
class Series:
    """One numeric column in time order (oldest -> newest)."""
    doc_id: str
    label: str
    keys: list[str]
    values: list[float]
    row_spans: list[tuple[int, int]]
    header_span: tuple[int, int]
    title_span: tuple[int, int] | None
    score: float = 0.0
    kind: str = "column"  # "column" | "vintage"


def column_series(t: Table, j: int) -> Series | None:
    col = t.column(j)
    keys = t.key_column()
    vals, ks, spans = [], [], []
    for v, k, sp in zip(col, keys, t.row_spans):
        if v is not None:
            vals.append(v)
            ks.append(k)
            spans.append(sp)
    if len(vals) < 3:
        return None
    # time order: if keys look like dates and are descending, reverse
    if all(is_dateish(k) for k in ks) and ks[0] > ks[-1]:
        vals, ks, spans = vals[::-1], ks[::-1], spans[::-1]
    # bound the work of the pooled backtests (quadratic in series length) on very long tables
    vals, ks, spans = vals[-MAX_SERIES:], ks[-MAX_SERIES:], spans[-MAX_SERIES:]
    return Series(t.doc_id, t.header[j], ks, vals, spans, t.header_span, t.title_span)


def vintage_columns(t: Table) -> list[int]:
    """Indices of header cells that look like vintage dates (as_of_YYYY-MM-DD etc.)."""
    return [j for j, h in enumerate(t.header) if j > 0 and is_dateish(h)]
