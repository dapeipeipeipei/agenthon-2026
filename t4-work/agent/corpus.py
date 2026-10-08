"""Unit loading: task, manifest entity labels, corpus documents, embargo.

Everything here is defensive. A document that cannot be read, has no date, is dated after the
cutoff, or is not labelled for any entity is never cited. Post-cutoff documents are dropped
before any other module sees them, so they cannot influence a prediction either.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Hard per-document read cap (bytes). Public units top out near 1 MB.
MAX_DOC_BYTES = 40 * 1024 * 1024
#: Hard cap on the number of corpus files considered.
MAX_DOCS = 2000

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
#: The scorer's doc_id grammar (one path component); an id outside it can never resolve.
DOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TARGET_TYPES = ("classification", "regression", "ranking")


def parse_date(value: Any) -> _dt.date | None:
    if not isinstance(value, str):
        return None
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


@dataclass
class Doc:
    doc_id: str
    text: str
    doc_date: _dt.date
    meta: dict
    shared: bool = False
    entity_ids: tuple[str, ...] | None = None
    #: Whether a claim may cite this document at all. The scorer resolves only manifest entries
    #: with `role: corpus` and reads a citation's offsets into the document's FLAT `text` string
    #: (`scoring._span_text`), so a `spans`-only document is readable evidence but never cited.
    citable: bool = True

    def about(self, entity_id: str) -> bool:
        """The manifest labels this document for the entity, or marks it shared."""
        return self.shared or (self.entity_ids is not None and entity_id in self.entity_ids)

    def admits(self, entity_id: str) -> bool:
        """May a claim for `entity_id` cite this document (the scorer's entity check + resolution)?"""
        return self.citable and self.about(entity_id)


@dataclass
class Unit:
    task: dict
    task_id: str
    target_type: str | None
    target_name: str
    labels: list[str]
    label_assertions: dict
    prompt: str
    cutoff: _dt.date | None
    resolution: _dt.date | None
    interval_level: float
    entities: list[dict]
    docs: dict[str, Doc] = field(default_factory=dict)
    dropped_post_cutoff: int = 0
    task_table: str = ""
    task_rows: dict[str, tuple[int, int]] = field(default_factory=dict)
    canary: str = ""

    @property
    def horizon_days(self) -> int:
        if self.cutoff and self.resolution and self.resolution > self.cutoff:
            return (self.resolution - self.cutoff).days
        return 30

    def docs_for(self, entity_id: str, *, own_first: bool = True) -> list[Doc]:
        """Documents this entity may cite, its own (entity-labelled) ones first, newest first."""
        own = [d for d in self.docs.values() if not d.shared and d.about(entity_id)]
        shared = [d for d in self.docs.values() if d.shared]
        key = lambda d: (d.doc_date, d.doc_id)  # noqa: E731
        own.sort(key=key, reverse=True)
        shared.sort(key=key, reverse=True)
        return own + shared if own_first else shared + own


def task_table_text(task: dict) -> tuple[str, dict[str, tuple[int, int]]]:
    """Exactly the scorer's task table: one compact json.dumps line per entities row."""
    lines: list[str] = []
    ranges: dict[str, tuple[int, int]] = {}
    offset = 0
    for row in task.get("entities") or []:
        if not isinstance(row, dict) or not isinstance(row.get("entity_id"), str):
            continue
        line = json.dumps(row, ensure_ascii=False, separators=(", ", ": "))
        ranges[row["entity_id"]] = (offset, offset + len(line))
        lines.append(line)
        offset += len(line) + 1
    return "\n".join(lines), ranges


def _read_json(path: Path) -> Any:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_DOC_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 - unreadable is simply absent
        return None


def _doc_text(raw: Any) -> str:
    """The scorer's `_doc_text`: a raw string, a flat `text`, or `spans[].text` joined by spaces."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        text = raw.get("text")
        if isinstance(text, str):
            return text
        spans = raw.get("spans")
        if isinstance(spans, list):
            parts = [s.get("text") for s in spans if isinstance(s, dict) and isinstance(s.get("text"), str)]
            return " ".join(parts)
    return ""


def _manifest_labels(unit_dir: Path, corpus_dir: Path, task: dict) -> dict[str, tuple[tuple[str, ...] | None, bool, bool]]:
    """doc_id -> (entity_ids, shared, resolvable) from the FIRST readable manifest, in the
    scorer's order. `resolvable` mirrors the scorer's `CorpusIndex`: only `role: corpus` entries
    at exactly `corpus/<doc_id>.json` with a well-formed, NFC doc_id exist for a citation."""
    candidates = [unit_dir / "manifest.json", unit_dir / "corpus" / "manifest.json"]
    rel = task.get("corpus_manifest")
    if isinstance(rel, str) and rel and ".." not in rel:
        candidates.append(unit_dir / rel)
    candidates.append(corpus_dir / "manifest.json")
    for cand in candidates:
        man = _read_json(cand)
        if not isinstance(man, dict) or not isinstance(man.get("files"), list):
            continue
        out: dict[str, tuple[tuple[str, ...] | None, bool, bool]] = {}
        for entry in man["files"]:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if not isinstance(path, str) or not path.startswith("corpus/") or not path.endswith(".json"):
                continue
            stem = path[len("corpus/"):-len(".json")]
            if "/" in stem or stem == "manifest":
                continue
            shared = entry.get("shared") is True
            ids = entry.get("entity_ids")
            ent = tuple(str(x) for x in ids) if isinstance(ids, list) else None
            if shared and ent:
                ent = None  # malformed label: treat as unlabelled (never cited)
                shared = False
            resolvable = (
                entry.get("role") == "corpus"
                and bool(DOC_ID_RE.match(stem))
                and unicodedata.normalize("NFC", path) == path
                and stem not in out  # two entries for one doc_id: never cite it
            )
            out[stem] = (ent, shared, resolvable)
        return out
    return {}


def card_params(unit_dir: Path | None) -> dict:
    """The few card.toml values the agent uses, if the card is readable: the trusted target type
    (`[scoring.params].target_type`, else `[task].target_type`) and the canary GUID.

    The scorer takes the target type from the card (or the signed plan it mirrors), not from
    task.json, so the card is the better authority when both are present."""
    if unit_dir is None:
        return {}
    try:
        import tomllib

        path = unit_dir / "card.toml"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
            return {}
        card = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 - a missing or odd card is simply absent
        return {}
    out: dict = {}
    sc = card.get("scoring") if isinstance(card.get("scoring"), dict) else {}
    params = sc.get("params") if isinstance(sc.get("params"), dict) else {}
    task_sec = card.get("task") if isinstance(card.get("task"), dict) else {}
    for tt in (params.get("target_type"), task_sec.get("target_type")):
        if tt in TARGET_TYPES:
            out["target_type"] = tt
            break
    contam = card.get("contamination") if isinstance(card.get("contamination"), dict) else {}
    if isinstance(contam.get("canary_guid"), str) and contam["canary_guid"].strip():
        out["canary"] = contam["canary_guid"].strip()
    return out


def resolve_target_type(task: dict, unit_dir: Path | None) -> str | None:
    """Card first (the scorer's authority), then `target.type`, then a flat `target_type`; a
    label vocabulary exists only on classification units, so it implies classification."""
    card = card_params(unit_dir)
    if card.get("target_type"):
        return card["target_type"]
    target = task.get("target") if isinstance(task.get("target"), dict) else {}
    for cand in (target.get("type"), task.get("target_type")):
        if cand in TARGET_TYPES:
            return cand
    if any(isinstance(x, str) and x for x in (target.get("labels") or [])):
        return "classification"
    return None


def load_unit(task_path: Path, corpus_dir: Path) -> Unit:
    task = json.loads(task_path.read_text(encoding="utf-8-sig"))
    if not isinstance(task, dict):
        raise ValueError("task.json is not an object")
    target = task.get("target") if isinstance(task.get("target"), dict) else {}
    unit_dir = task_path.resolve().parent
    target_type = resolve_target_type(task, unit_dir)
    labels = list(dict.fromkeys(x for x in (target.get("labels") or []) if isinstance(x, str) and x))
    la = target.get("label_assertions") if isinstance(target.get("label_assertions"), dict) else {}
    level = task.get("interval_level", 0.9)
    try:
        level = float(level)
    except (TypeError, ValueError):
        level = 0.9
    entities = [e for e in (task.get("entities") or []) if isinstance(e, dict) and isinstance(e.get("entity_id"), str)]
    unit = Unit(
        task=task,
        task_id=str(task.get("task_id", task_path.parent.name)),
        target_type=target_type,
        target_name=str(target.get("name") or task.get("target_name") or ""),
        labels=labels,
        label_assertions={str(k): str(v) for k, v in la.items()},
        prompt=str(task.get("prompt") or ""),
        cutoff=parse_date(task.get("cutoff_date")),
        resolution=parse_date(task.get("resolution_date")),
        interval_level=level,
        entities=entities,
    )
    unit.task_table, unit.task_rows = task_table_text(task)
    unit.canary = card_params(unit_dir).get("canary", "")

    labels_by_doc = _manifest_labels(unit_dir, corpus_dir, task)
    try:
        names = sorted(os.listdir(corpus_dir))[:MAX_DOCS]
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".json") or name == "manifest.json":
            continue
        doc_id = name[: -len(".json")]
        raw = _read_json(corpus_dir / name)
        if raw is None:
            continue
        date = parse_date(raw.get("doc_date")) if isinstance(raw, dict) else None
        if date is None:
            continue  # undated: never usable
        if unit.cutoff is not None and date > unit.cutoff:
            unit.dropped_post_cutoff += 1
            continue  # embargo, applied before anything reads the text
        if unit.cutoff is None:
            continue  # no cutoff means no document can be shown to be safe
        text = _doc_text(raw)
        if not text:
            continue
        ent, shared, resolvable = labels_by_doc.get(doc_id, (None, False, False))
        flat = isinstance(raw, dict) and isinstance(raw.get("text"), str)
        meta = {k: v for k, v in raw.items() if k not in ("text", "spans")} if isinstance(raw, dict) else {}
        # the scorer resolves a citation by file name; a document whose own `doc_id` field says
        # otherwise is read but never cited (two resolvers could disagree about it)
        inner = raw.get("doc_id") if isinstance(raw, dict) else None
        consistent = not isinstance(inner, str) or inner == doc_id
        unit.docs[doc_id] = Doc(doc_id=doc_id, text=text, doc_date=date, meta=meta, shared=shared,
                                entity_ids=ent, citable=bool(resolvable and flat and consistent))
    return unit
