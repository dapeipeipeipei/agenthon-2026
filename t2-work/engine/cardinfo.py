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



def _family(*cands: Any) -> str | None:
    for c in cands:
        if not c:
            continue
        m = re.search(r"F([1-4])(?![0-9])", str(c).upper())
        if m:
            return f"F{m.group(1)}"
    return None


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
    task = card.get("task", {}) if isinstance(card, dict) else {}
    meta = card.get("metadata", {}) if isinstance(card, dict) else {}
    unit_id = str(task.get("id") or spec.get("card_id") or "")
    family = _family(meta.get("category"), spec.get("card_family"), unit_id)
    tgt = spec.get("targets") or {}
    periods = tgt.get("observation_periods")
    if not (isinstance(periods, list) and all(isinstance(x, str) for x in periods)):
        periods = None
    return {"unit_id": unit_id, "family": family, "observation_periods": periods, "panels": _panels(card, spec, meta),
            "horizons": [int(h) for h in (tgt.get("horizons") or card.get("targets", {}).get("horizons") or [])]}


def _panels(card: dict[str, Any], spec: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    """Declared panel ids, target panel first (v6 asset-class fallback only): metadata.asset_panel,
    [panels].panel_ids, the spec's panels[].panel_id, then the metadata tags. Never raises."""
    out: list[str] = []
    try:
        pan = card.get("panels")
        cands = [meta.get("asset_panel")]
        cands += list(pan.get("panel_ids") or []) if isinstance(pan, dict) else []
        cands += [x.get("panel_id") for x in (spec.get("panels") or []) if isinstance(x, dict)]
        tags = meta.get("tags")
        cands += list(tags) if isinstance(tags, list) else []
        for c in cands:
            if isinstance(c, str) and c and c not in out:
                out.append(c)
    except Exception:  # noqa: BLE001
        pass
    return out
