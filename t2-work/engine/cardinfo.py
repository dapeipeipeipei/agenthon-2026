"""Task-statement facts the v4 engine may condition on, read from the unit's own card files.

Only fields that are printed on the card the solver is handed (forecast_card.md shows
"Family T2-F4"): the unit id, the card family (CATEGORIES.md: every task belongs to exactly one
family F1-F4), the target type, and, on monthly cards, the observation month of each target
(forecast_spec.json `targets.observation_periods`, docs/MONTHLY-HORIZONS.md). Nothing here is an
outcome, a date of a future event or a title keyword.

Never raises: a missing or unreadable card yields {} and the engine uses its family-agnostic
defaults.
"""

from __future__ import annotations

import json
import pathlib
import re
import tomllib
from typing import Any



#: A family token is F1-F4 on its own: "T2-F4", "t2-f4", "F4", "T2F4" and "T2-F4-REGIME" count;
#: "ETF1", "SF1" or "F10" do not (a sealed unit id is not promised to carry the family at all,
#: and a letter-digit pair inside another word must not be read as one).
_FAMILY_TOKEN = re.compile(r"(?<![A-Z0-9])F([1-4])(?![0-9])")


def _family(*cands: Any) -> str | None:
    """First F1-F4 token among the candidates; lists (tags) are scanned element by element."""
    for c in cands:
        if not c:
            continue
        items = c if isinstance(c, (list, tuple)) else [c]
        for item in items:
            if not item or isinstance(item, (dict, list, tuple)):
                continue
            m = _FAMILY_TOKEN.search(str(item).upper())
            if m:
                return f"F{m.group(1)}"
    return None


def from_card(card: dict[str, Any] | None, spec: dict[str, Any] | None) -> dict[str, Any]:
    """The same facts, from an already-parsed card / spec (the CLI's own copies). Never raises."""
    try:
        return _facts(card if isinstance(card, dict) else {}, spec if isinstance(spec, dict) else {})
    except Exception:  # noqa: BLE001 - optional context only
        return {}


def read(text_dir: pathlib.Path | str | None) -> dict[str, Any]:
    try:
        return _read(pathlib.Path(text_dir)) if text_dir is not None else {}
    except Exception:  # noqa: BLE001 - optional context only
        return {}


def _read(text_dir: pathlib.Path) -> dict[str, Any]:
    card: dict[str, Any] = {}
    spec: dict[str, Any] = {}
    for d in (text_dir.parent, text_dir):
        p = d / "card.toml"
        if not card and p.is_file():
            try:
                card = tomllib.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                card = {}
        q = d / "forecast_spec.json"
        if not spec and q.is_file():
            try:
                spec = json.loads(q.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                spec = {}
    if not card and not spec:
        return {}
    return _facts(card, spec)


def _facts(card: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    task = card.get("task", {}) if isinstance(card.get("task"), dict) else {}
    meta = card.get("metadata", {}) if isinstance(card.get("metadata"), dict) else {}
    unit_id = str(task.get("id") or spec.get("card_id") or "")
    # Family, in order of trust: [metadata].category (on every card, T2#20), the spec's
    # card_family, [metadata].family, the tags list, and last the unit id (practice ids carry it;
    # sealed ids may not). An id-only token is bounded by _FAMILY_TOKEN. None -> default row.
    family = _family(meta.get("category"), spec.get("card_family"), meta.get("family"), meta.get("tags"), unit_id)
    tgt = spec.get("targets") if isinstance(spec.get("targets"), dict) else {}
    periods = tgt.get("observation_periods")
    if not (isinstance(periods, list) and all(isinstance(x, str) for x in periods)):
        periods = None
    ctg = card.get("targets") if isinstance(card.get("targets"), dict) else {}
    return {"unit_id": unit_id, "family": family, "observation_periods": periods,
            "horizons": [int(h) for h in (tgt.get("horizons") or ctg.get("horizons") or [])]}
