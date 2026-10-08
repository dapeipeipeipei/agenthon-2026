"""Family-agnostic prediction: anchors, pooled series models, EPS momentum, vintage revisions,
distress lexicon, and label mapping from label assertions. Deterministic; no model calls.

Design rule: when the evidence is thin, stay close to the "carry it forward" anchor, because the
score is anchored to a naive rule (matching it scores 0.5; worse falls below). Every model that
moves away from the anchor is shrunk and fitted only on pre-cutoff data inside the unit.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
import statistics
from dataclasses import dataclass, field

from .corpus import Unit, parse_date
from .signals import (
    Span,
    best_series,
    distress_signal,
    eps_signal,
    finite,
    keyword_spans,
    notes_span,
    policy_path_signal,
    proxy_signal,
    vintage_signal,
)
from .tables import Series, norm_tokens

Z90 = 1.645

_ANCHOR_PRIORITY = ("latest", "consensus", "prior", "last", "previous", "current", "start")
_DOWN = ("down", "lower", "decrease", "decline", "fall", "negative", "miss", "below", "less", "cut", "worse",
         "underperform", "downgrade", "weaken", "deteriorate", "reduce", "contract", "loss", "drop", "sell",
         "short", "bearish", "tighten")
_UP = ("up", "higher", "increase", "rise", "positive", "beat", "above", "greater", "raise", "better",
       "outperform", "upgrade", "strengthen", "improve", "expand", "gain", "hike", "buy", "long", "bullish",
       "widen", "accelerate")
_MIDDLE = ("inline", "in line", "flat", "within", "between", "unchanged", "neutral", "hold", "maintain",
           "no change", "steady", "stable", "same", "meet", "met", "in-line")
_NO_EVENT = ("no_event", "no event", "none", "no_default", "no default", "survive", "solvent")


@dataclass
class Pred:
    entity_id: str
    point: float
    lo: float
    hi: float
    label: str | None = None
    method: str = "fallback"
    spans: list[Span] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)   # short human-readable derivation pieces
    anchor_key: str | None = None
    strength: float = 0.0                              # how far the method moved from its anchor


# --------------------------------------------------------------------------- small helpers


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def row_anchor(entity: dict) -> tuple[str, float] | None:
    best = None
    for k, v in entity.items():
        x = _num(v)
        if x is None:
            continue
        kl = k.lower()
        if "threshold" in kl or kl in ("cik",) or kl.endswith("_id"):
            continue
        for rank, word in enumerate(_ANCHOR_PRIORITY):
            if word in kl:
                if best is None or rank < best[0]:
                    best = (rank, k, x)
                break
    return (best[1], best[2]) if best else None


def is_change_target(unit: Unit) -> bool:
    name = unit.target_name.lower()
    if re.search(r"change|growth|delta|revision|return|move", name):
        return True
    return bool(re.search(r"\bthe CHANGE\b|\bCHANGE in\b", unit.prompt))


def wants_probability(unit: Unit) -> bool:
    p = unit.prompt.lower()
    return "probability" in p and "point_forecast" in p


def _label_semantics(unit: Unit) -> dict[str, str]:
    """label -> 'up' | 'down' | 'middle' | 'noevent' | 'event' | '?'"""
    out = {}
    for lab in unit.labels:
        text = (lab + " " + unit.label_assertions.get(lab, "")).lower().replace("_", " ")
        ll = lab.lower().replace("_", " ")
        if any(w in ll for w in ("no event", "none", "no default")) or (ll.startswith("no ") and "event" in ll):
            out[lab] = "noevent"
        elif any(re.search(rf"\b{w}\b", ll) for w in _MIDDLE):
            out[lab] = "middle"
        elif any(re.search(rf"\b{w}\b", ll) for w in _UP):
            out[lab] = "up"
        elif any(re.search(rf"\b{w}\b", ll) for w in _DOWN):
            out[lab] = "down"
        elif "event" in ll or "default" in ll or "bankrupt" in ll:
            out[lab] = "event"
        elif any(re.search(rf"\b{w}\b", text) for w in ("within", "between")):
            out[lab] = "middle"
        elif any(re.search(rf"\b{w}\b", text) for w in ("above", "higher", "greater", "up")):
            out[lab] = "up"
        elif any(re.search(rf"\b{w}\b", text) for w in ("below", "lower", "less", "down")):
            out[lab] = "down"
        else:
            out[lab] = "?"
    return out


def _threshold(entity: dict) -> tuple[str, float] | None:
    for k, v in entity.items():
        x = _num(v)
        if x is not None and "threshold" in k.lower():
            return k, x
    return None


def _date_steps(keys: list[str]) -> float | None:
    ds = [parse_date(k + ("-01" if re.fullmatch(r"\d{4}-\d{2}", k) else "")) for k in keys]
    ds = [d for d in ds if d is not None]
    if len(ds) < 3:
        return None
    gaps = [(b - a).days for a, b in zip(ds, ds[1:]) if (b - a).days > 0]
    return statistics.median(gaps) if gaps else None


def _last_date(keys: list[str]) -> _dt.date | None:
    for k in reversed(keys):
        d = parse_date(k + ("-01" if re.fullmatch(r"\d{4}-\d{2}", k) else ""))
        if d:
            return d
    return None


#: 90% band half-width in robust standard deviations (prefer wide: the interval score is far
#: flatter above the 1.645 sigma optimum than below it).
VINTAGE_HW = 1.8
#: Below this many age-matched revisions, their spread is floored at the spread of all routine
#: revisions in the table (three observations say little about the tail).
SMALL_SAMPLE = 5
#: A series' own backtest residual is floored at this share of the unit-pooled residual (both in
#: units of each series' own scale): one quiet year in one row is not evidence of a quiet future.
POOLED_SD_FLOOR = 0.75

#: Policy-path anchor: the share of the gap between the near-term policy anchor and the front-end
#: yield expected to close by the resolution (markets and the committee each half right), and the
#: maturity (years) beyond which the pass-through decays as sqrt(M0 / maturity).
PATH_SHARE = 0.5
PATH_M0 = 5.0
#: The gap is a repricing of the next few meetings: beyond this it is not a policy-path gap but a
#: regime difference (or a mis-read), so the signal is capped there (bp).
PATH_GAP_CAP = 150.0
#: Absolute floor of a basis-point fallback band's half-width over a 30-day window (scaled by
#: the square root of the window): binds only when the level-proportional band is narrower, i.e.
#: in zero-rate contexts (a level below about 0.6%).
ZLB_FLOOR_BP_30D = 15.0
#: The policy-path rule is about sovereign yields (the expected policy path is their main driver);
#: a basis-point target that is not one (mortgage rates, credit spreads, swap spreads) does not
#: get it, however similar the corpus.
_SOVEREIGN = re.compile(r"\b(yield|yields|treasury|treasuries|sovereign|government bond|gilt|bund|jgb|policy rate|curve)\b", re.IGNORECASE)
_NOT_SOVEREIGN = re.compile(r"\b(mortgage|credit spread|cds|swap spread|corporate|municipal)\b", re.IGNORECASE)


def _is_sovereign_yield(unit: Unit) -> bool:
    text = unit.target_name.replace("_", " ") + " " + unit.prompt
    return bool(_SOVEREIGN.search(text)) and not _NOT_SOVEREIGN.search(unit.target_name.replace("_", " ") + " " + unit.prompt[:400])


def _maturity_years(ent: dict) -> float | None:
    for k, v in ent.items():
        x = _num(v)
        if x is not None and x > 0 and any(w in k.lower() for w in ("maturity", "tenor_years", "years")):
            return x
    for k in ("name", "tenor", "entity_id"):
        m = re.search(r"(\d+(?:\.\d+)?)\s*-?\s*(?:Year|Yr|Y)\b", str(ent.get(k) or ""), re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def _yield_level(ent: dict) -> float | None:
    for k, v in ent.items():
        x = _num(v)
        kl = k.lower()
        if x is not None and ("yield" in kl or "rate" in kl) and ("pct" in kl or "percent" in kl):
            return x
    return None


def _is_bps(unit: Unit) -> bool:
    text = (unit.target_name + " " + " ".join(str(e.get("unit", "")) for e in unit.entities) + " " + unit.prompt).lower()
    return "bps" in text or "basis point" in text


def _robust_center(xs: list[float]) -> float:
    """Median for small samples; mean of the values within 3 MADs of the median otherwise."""
    xs = [x for x in xs if finite(x)]
    if not xs:
        return 0.0
    med = statistics.median(xs)
    if len(xs) < 5:
        return med
    mad = statistics.median(abs(x - med) for x in xs)
    if mad <= 0:
        return med
    kept = [x for x in xs if abs(x - med) <= 3.0 * 1.4826 * mad]
    return statistics.fmean(kept) if kept else med


def _robust_sd(xs: list[float]) -> float:
    xs = [x for x in xs if finite(x)]
    if len(xs) < 2:
        return 0.0
    med = statistics.median(xs)
    mad = 1.4826 * statistics.median(abs(x - med) for x in xs)
    return max(mad, _rms([x - med for x in xs]) * 0.75) if len(xs) >= 5 else _rms([x - med for x in xs])


def _release_steps(ent: dict, gap_days: float | None) -> int:
    """How many releases lie between the latest pre-cutoff vintage and the resolving release
    (both read from the entity row when it names them), at the table's release spacing."""
    vint = res = None
    for k, val in ent.items():
        kl = k.lower()
        d = parse_date(val) if isinstance(val, str) else None
        if d is None:
            continue
        if "vintage" in kl and vint is None:
            vint = d
        elif ("resolv" in kl or "release" in kl) and res is None:
            res = d
    if not (vint and res and gap_days and res > vint):
        return 1
    return int(min(6, max(1, round((res - vint).days / gap_days))))


def _rms(xs: list[float]) -> float:
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else 0.0


def _robust_scale(vals: list[float]) -> float:
    d = [abs(b - a) for a, b in zip(vals, vals[1:])]
    s = statistics.median(d) if d else 0.0
    if s <= 0:
        s = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    return s if s > 0 else max(abs(vals[-1]) * 0.05, 1e-6)


# --------------------------------------------------------------------------- pooled series models

LEVEL_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
WINDOW = 6


WINDOW_GRID = (3, 6, 12)


def _level_pred(vals: list[float], w: float, window: int = WINDOW) -> float:
    tail = vals[-window:]
    return w * vals[-1] + (1 - w) * (sum(tail) / len(tail))


def fit_level_weight(series: dict[str, Series]) -> tuple[float, int]:
    """(weight on the last value, trailing-mean window), chosen by one-step backtest pooled over
    the unit's entities (pre-cutoff data only, scale-free). Ties go to (0.5, 6)."""
    if not series:
        return 0.5, WINDOW
    grid = sorted(((w, k) for w in LEVEL_GRID for k in WINDOW_GRID), key=lambda x: (abs(x[0] - 0.5), abs(x[1] - WINDOW)))
    best, best_loss = (0.5, WINDOW), None
    for w, k in grid:
        loss, n = 0.0, 0
        for s in series.values():
            v = s.values
            sc = _robust_scale(v)
            for t in range(3, len(v)):
                loss += abs(v[t] - _level_pred(v[:t], w, k)) / sc
                n += 1
        if n == 0:
            return 0.5, WINDOW
        loss /= n
        if best_loss is None or loss < best_loss - 1e-9:
            best, best_loss = (w, k), loss
    return best


def level_residual_sd(vals: list[float], w: float, window: int = WINDOW) -> float:
    errs = [vals[t] - _level_pred(vals[:t], w, window) for t in range(3, len(vals))]
    if len(errs) < 2:
        return _robust_scale(vals) * 1.5
    return _rms(errs)


def fit_reversion(series: dict[str, Series], h: int) -> float:
    """kappa in E[v(t+h) - v(t)] = -kappa * (v(t) - trailing mean), pooled and scale-free."""
    sxy = sxx = 0.0
    for s in series.values():
        v = s.values
        sc = _robust_scale(v)
        for t in range(4, len(v) - h):
            m = sum(v[: t + 1]) / (t + 1)
            x = (v[t] - m) / sc
            y = (v[t + h] - v[t]) / sc
            sxy += x * y
            sxx += x * x
    if sxx <= 0:
        return 0.0
    return max(0.0, min(0.8, -sxy / sxx))


def h_change_sd(vals: list[float], h: int) -> float:
    ch = [vals[t + h] - vals[t] for t in range(len(vals) - h)]
    if len(ch) >= 3:
        return _rms(ch)
    return _robust_scale(vals) * math.sqrt(max(h, 1)) * 1.5


# --------------------------------------------------------------------------- fallback scales


def fallback_halfwidth(unit: Unit, entity: dict, point: float, anchor: tuple[str, float] | None) -> float:
    h = unit.horizon_days
    unit_field = " ".join(str(entity.get(k, "")) for k in ("unit", "units", "currency")).lower()
    tname = unit.target_name.lower()
    p = unit.prompt.lower()
    if "bps" in unit_field or "bps" in tname or "basis point" in p:
        level = None
        for k, v in entity.items():
            x = _num(v)
            if x is not None and ("yield" in k.lower() or "rate" in k.lower()) and "pct" in k.lower():
                level = x
        level = level if level and level > 0 else 3.0
        sd = 100.0 * level * 0.30 * math.sqrt(max(h, 1) / 365.0)
        # the level-proportional band collapses at the zero lower bound (a 0.1% yield still
        # moves by tens of basis points over weeks on term premium and lift-off expectations):
        # an absolute floor that grows with the window, 15 bp per 30 days
        floor = max(10.0, ZLB_FLOOR_BP_30D * math.sqrt(max(h, 1) / 30.0))
        return max(Z90 * sd * 1.75, floor)
    if "return" in tname or "abnormal return" in p:
        # an earnings release inside the window moves single stocks two to three times a normal
        # day's range: use the wider event scale (the interval score punishes misses 20x)
        event_sd = 6.5 if re.search(r"earnings|results release|report", p) else 5.0
        return Z90 * event_sd * math.sqrt(max(h, 1) / 2.0)
    if anchor is not None and anchor[1] != 0:
        return max(abs(anchor[1]) * 0.15, 1e-3)
    if point != 0:
        return abs(point) * 0.5
    return 1.0


# --------------------------------------------------------------------------- main


def predict_unit(unit: Unit, cache: dict | None = None) -> list[Pred]:
    cache = {} if cache is None else cache
    ttype = unit.target_type or "regression"
    change = is_change_target(unit)
    eps_family = bool(re.search(r"\beps\b|earnings per share", (unit.target_name + " " + unit.prompt).lower()))
    sem = _label_semantics(unit) if unit.labels else {}
    eventish = "noevent" in sem.values() or "event" in sem.values()

    # ---- per-entity evidence
    series: dict[str, Series] = {}
    vint: dict = {}
    eps: dict = {}
    dist: dict = {}
    for ent in unit.entities:
        eid = ent["entity_id"]
        try:
            if eventish:
                d = distress_signal(unit, ent)
                if d:
                    dist[eid] = d
            if eps_family:
                e = eps_signal(unit, ent)
                if e:
                    eps[eid] = e
            v = vintage_signal(unit, ent, cache)
            if v:
                vint[eid] = v
            elif eid not in eps:
                s = best_series(unit, ent, cache)
                if s is not None and len(s.values) >= 4:
                    series[eid] = s
                    try:
                        s.__dict__["proxy"] = proxy_signal(unit, ent, s, cache)
                    except Exception:  # noqa: BLE001
                        s.__dict__["proxy"] = None
        except Exception:  # noqa: BLE001 - one entity's evidence never sinks the unit
            continue

    # ---- pooled fits (pre-cutoff history only)
    w_level = fit_level_weight(series)
    steps = {}
    for eid, s in series.items():
        step = _date_steps(s.keys)
        last = _last_date(s.keys)
        if step and last and unit.resolution:
            steps[eid] = max(1, int(round((unit.resolution - last).days / step)))
        else:
            steps[eid] = 1
    h_common = int(statistics.median(steps.values())) if steps else 1
    kappa = fit_reversion(series, h_common) if change else 0.0
    # unit-pooled level-model residual, in units of each series' own scale (the floor for a row's band)
    ratios = []
    for s in series.values():
        sc = _robust_scale(s.values)
        if sc > 0 and len(s.values) >= 5:
            ratios.append(level_residual_sd(s.values, *w_level) / sc)
    pooled_ratio = _rms(ratios) if len(ratios) >= 2 else 0.0

    # policy-path anchor for a yield-change cross-section: one stance per unit, read from the
    # pre-cutoff statements, applied through each row's maturity
    path = None
    if change and _is_bps(unit) and _is_sovereign_yield(unit):
        try:
            pp = policy_path_signal(unit)
            fronts = [(m, _yield_level(e)) for e in unit.entities for m in [_maturity_years(e)] if m is not None and _yield_level(e) is not None]
            if pp is not None and fronts:
                m0, y0 = min(fronts)
                path = (pp, m0, y0)
        except Exception:  # noqa: BLE001
            path = None

    preds: list[Pred] = []
    for ent in unit.entities:
        eid = ent["entity_id"]
        anchor = row_anchor(ent)
        pr = Pred(entity_id=eid, point=0.0, lo=-1.0, hi=1.0, anchor_key=anchor[0] if anchor else None)
        try:
            _fill(pr, unit, ent, ttype, change, anchor, series.get(eid), vint.get(eid), eps.get(eid),
                  dist.get(eid), w_level, kappa, steps.get(eid, h_common), sem, pooled_ratio, path)
        except Exception:  # noqa: BLE001
            pr.method = "fallback"
            base = anchor[1] if (anchor and not change) else 0.0
            hw = fallback_halfwidth(unit, ent, base, anchor)
            pr.point, pr.lo, pr.hi = base, base - hw, base + hw
        _sanitize(pr)
        if not any(unit.docs.get(d) is not None and unit.docs[d].admits(eid) for d, _, _ in pr.spans):
            try:
                pr.spans = pr.spans + keyword_spans(unit, ent, k=2)
            except Exception:  # noqa: BLE001
                pass
        preds.append(pr)

    if ttype == "classification":
        for pr in preds:
            if unit.labels and pr.label not in unit.labels:
                pr.label = _default_label(unit, sem)
            elif not unit.labels and not (isinstance(pr.label, str) and pr.label):
                pr.label = fallback_label(unit)
        ents = {e["entity_id"]: e for e in unit.entities}
        for pr in preds:
            try:
                make_consistent(pr, ents[pr.entity_id], sem)
            except Exception:  # noqa: BLE001
                pass
    return preds


def make_consistent(pr: Pred, ent: dict, sem: dict[str, str]) -> None:
    """Point and label must tell the same story (the reasoning judge reads both, and the point is
    what the interval leg is centred on): when the label says 'up' (or 'down' / 'middle') relative
    to its reference but the point sits elsewhere, move the point to the label's side - to the
    edge plus half a forecast sd, the conditional location of the label's region - and keep the
    band around it. A no-op where the label has no numeric reference."""
    if "label_ref" not in pr.__dict__ or not pr.label:
        return
    s = sem.get(pr.label)
    lower, upper = label_bounds(ent, pr.__dict__["label_ref"])
    sd = max((pr.hi - pr.lo) / (2 * Z90), 1e-9)
    p = pr.point
    if s == "up" and not p > upper:
        p = upper + 0.5 * sd
    elif s == "down" and not p < lower:
        p = lower - 0.5 * sd
    elif s == "middle" and not (lower <= p <= upper):
        p = min(max(p, lower), upper)
    else:
        return
    pr.point = p
    pr.lo, pr.hi = min(pr.lo, p), max(pr.hi, p)
    pr.facts.append(f"the point is placed on the {pr.label} side of the label boundary ({p:.4g}) so that value and label tell the same story")


def fallback_label(unit: Unit) -> str:
    """A classification unit with no declared vocabulary still needs a non-empty label on every
    row (the scorer refuses a row without one). Use a label the task itself names."""
    keys = [k for k in unit.label_assertions if k]
    if keys:
        return keys[0]
    m = re.search(r"[Ll]abel\s+['\"]([A-Za-z0-9_\- ]{1,40})['\"]", unit.prompt)
    return m.group(1) if m else "no_change"


def _sanitize(pr: Pred) -> None:
    if not finite(pr.point):
        pr.point = 0.0
    lo, hi = pr.lo, pr.hi
    if not finite(lo) or not finite(hi):
        lo, hi = pr.point - 1.0, pr.point + 1.0
    lo, hi = min(lo, hi), max(lo, hi)
    if hi - lo <= 0:
        pad = max(abs(pr.point) * 0.05, 1e-3)
        lo, hi = pr.point - pad, pr.point + pad
    lo = min(lo, pr.point)
    hi = max(hi, pr.point)
    pr.lo, pr.hi = lo, hi


def _default_label(unit: Unit, sem: dict[str, str]) -> str:
    for want in ("noevent", "middle", "up"):
        for lab, s in sem.items():
            if s == want:
                return lab
    return unit.labels[0]


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def label_bounds(ent: dict, ref: float | None) -> tuple[float, float]:
    """(lower, upper) edges of the middle region around `ref` for this row's threshold (equal
    when there is no threshold): above `upper` is 'up', below `lower` is 'down'."""
    thr = _threshold(ent)
    base = ref if ref is not None else 0.0
    if thr is None:
        return base, base
    k, t = thr
    relative = ref is not None and ref != 0 and t < 1 and "abn" not in k.lower()
    width = t * abs(ref) if relative else t
    return base - width, base + width


def _label_from_value(unit: Unit, ent: dict, sem: dict[str, str], value: float, ref: float | None,
                      sd: float | None = None, pr: "Pred | None" = None) -> str | None:
    """Map a numeric forecast to a label. With a three-way vocabulary (up / middle / down around a
    threshold) and a forecast spread `sd`, pick the label with the highest probability under a
    normal forecast distribution (ties: up, then middle), so a narrow middle band is not chosen
    by default when the forecast is very uncertain."""
    ups = [l for l, s in sem.items() if s == "up"]
    downs = [l for l, s in sem.items() if s == "down"]
    mids = [l for l, s in sem.items() if s == "middle"]
    if not ups or not downs:
        return None
    if pr is not None:
        pr.__dict__["label_ref"] = ref  # what the label is measured against (consistency pass)
    thr = _threshold(ent)
    if mids and thr is not None and sd is not None and sd > 0:
        k, t = thr
        relative = ref is not None and ref != 0 and t < 1 and "abn" not in k.lower()
        base = ref if ref is not None else 0.0
        width = t * abs(ref) if relative else t
        z_hi = (base + width - value) / sd
        z_lo = (base - width - value) / sd
        p_up, p_dn = 1.0 - _phi(z_hi), _phi(z_lo)
        p_mid = max(0.0, 1.0 - p_up - p_dn)
        best = max((p_up + 1e-9, ups[0]), (p_mid, mids[0]), (p_dn - 1e-9, downs[0]))
        return best[1]
    if mids and thr is not None:
        k, t = thr
        if ref is not None and ref != 0 and t < 1 and "abn" not in k.lower():
            rel = (value - ref) / abs(ref)
            if rel > t:
                return ups[0]
            if rel < -t:
                return downs[0]
            return mids[0]
        base = ref if ref is not None else 0.0
        if value - base > t:
            return ups[0]
        if value - base < -t:
            return downs[0]
        return mids[0]
    base = ref if ref is not None else 0.0
    if value > base:
        return ups[0]
    if value < base:
        return downs[0]
    return mids[0] if mids else ups[0]


def _fill(pr: Pred, unit: Unit, ent: dict, ttype: str, change: bool, anchor, s: Series | None, v, e, d,
          w_level: tuple[float, int], kappa: float, h: int, sem: dict, pooled_ratio: float = 0.0,
          path: tuple | None = None) -> None:
    eid = ent["entity_id"]
    name = str(ent.get("name") or eid)
    prob = ttype == "classification" and wants_probability(unit)

    # 1) EPS momentum from the latest filing
    if e is not None:
        prior = None
        for k, val in ent.items():
            x = _num(val)
            if x is not None and "eps" in k.lower() and ("prior" in k.lower() or "year" in k.lower()):
                prior = x
        cons = _num(ent.get("consensus_eps"))
        delta = e.current - e.prior_year
        pr.spans.append(e.span)
        pr.facts.append(f"the latest filed quarter's diluted EPS was {e.current:g} against {e.prior_year:g} a year earlier, "
                        f"a year-over-year change of {delta:+.2f}")
        if prior is not None:
            eps_hat = prior + 0.5 * delta
            sd = 0.35 * abs(delta) + 0.08 * abs(prior) + 0.02
            pr.facts.append(f"half of that change is expected to persist into the target quarter, so the forecast EPS is the "
                            f"prior-year quarter's {prior:g} (task table) plus 0.5 x ({e.current:g} - {e.prior_year:g}) = {eps_hat:.3f}")
            pr.method = "eps_momentum"
            pr.strength = abs(eps_hat - prior) / (abs(prior) + 0.05)
            if ttype in ("regression", "ranking") and change and "eps" not in (ent.get("unit") or "").lower():
                base = abs(prior) if abs(prior) > 0.05 else 0.05
                g = (eps_hat - prior) / base * 100.0
                g_lo = ((eps_hat - Z90 * sd) - prior) / base * 100.0
                g_hi = ((eps_hat + Z90 * sd) - prior) / base * 100.0
                pr.point, pr.lo, pr.hi = g, g_lo, g_hi
                pr.facts.append(f"relative to the prior-year quarter's {prior:g} that is year-over-year EPS growth of {g:.1f}%")
            else:
                pr.point, pr.lo, pr.hi = eps_hat, eps_hat - Z90 * sd, eps_hat + Z90 * sd
                if ttype == "classification":
                    pr.label = _label_from_value(unit, ent, sem, eps_hat, prior, sd, pr=pr)
            return
        if cons is not None:
            # consensus already prices the trend: keep it, use the trend only for the band
            sd = 0.06 * abs(cons) + 0.1 * abs(delta) + 0.02
            pr.point, pr.lo, pr.hi = cons, cons - Z90 * sd, cons + Z90 * sd
            pr.method = "consensus_anchor"
            if ttype == "classification":
                pr.label = _label_from_value(unit, ent, sem, cons, cons, sd, pr=pr)
            return

    # 2) vintage revisions
    if v is not None:
        base = anchor[1] if anchor else v.values[-1]
        steps = _release_steps(ent, v.vintage_gap_days)
        # expected cumulative revision over the releases up to the resolving one: for each age,
        # the robust centre of past revisions of that age (jump transitions already removed),
        # else the robust centre of all routine revisions
        pooled = [r for r in v.revisions if finite(r)]
        exp_rev, used_n, matched_ages = 0.0, 0, 0
        for k in range(steps):
            age = (v.next_age + k) if v.next_age else None
            rs = [r for r in v.by_age.get(age, []) if finite(r)] if age else []
            if len(rs) >= 2:
                matched_ages += 1
            else:
                rs = pooled
            exp_rev += _robust_center(rs)
            used_n += len(rs)
        revs = [r for r in (v.age_matched or pooled)]
        nz = [r for r in revs if r != 0]
        c = _robust_center(revs) if revs else 0.0
        spread = max(_robust_sd([r - c for r in revs]), _rms([r - c for r in revs])) if len(revs) >= 2 else abs(base) * 0.002
        if len(revs) < SMALL_SAMPLE and len(pooled) >= 2:
            cp = _robust_center(pooled)
            spread = max(spread, _robust_sd([r - cp for r in pooled]), _rms([r - cp for r in pooled]))
        sd = max(spread * math.sqrt(steps), abs(exp_rev) * 0.5, abs(base) * 1e-4, 1e-3)
        point = base + exp_rev
        pr.point, pr.lo, pr.hi = point, point - VINTAGE_HW * sd, point + VINTAGE_HW * sd
        pr.method = "vintage_revision"
        pr.strength = abs(exp_rev) / (sd + 1e-9)
        up_share = (sum(1 for r in nz if r > 0) / len(nz)) if nz else 0.5
        scope = (f"revisions of other reference months at the same release age (revision number {v.next_age})" if v.age_matched
                 else "routine revisions")
        pr.facts.append(f"the vintage table shows {len(revs)} past {scope}, typically {c:+.4g} (outlier-robust average) with "
                        f"{up_share:.0%} of the non-zero ones upward, so the latest estimate of {base:g} is expected to move by "
                        f"{exp_rev:+.4g} to {point:.6g}")
        if steps > 1:
            pr.facts.append(f"the resolving release is about {steps} releases after the latest pre-cutoff estimate, so the revisions of {steps} release ages are added up")
        if v.dropped_jumps:
            pr.facts.append(f"{v.dropped_jumps} one-off level shift(s) in the table (annual or benchmark revisions) were left out of the averages")
        pr.spans += [(v.doc_id, v.row_span[0], v.row_span[1])]
        if ttype == "classification":
            direction = exp_rev
            if direction == 0 and nz:
                direction = 1 if up_share >= 0.5 else -1
            pr.label = _label_from_value(unit, ent, sem, base + direction, base, sd, pr=pr) if direction != 0 else None
        return

    # 3) distress lexicon (event classification)
    if d is not None and ttype == "classification":
        p = 1.0 / (1.0 + math.exp(-d.score))
        p = min(max(p, 0.02), 0.95)
        ev = [l for l, s_ in sem.items() if s_ == "event"]
        ne = [l for l, s_ in sem.items() if s_ == "noevent"]
        if ev and ne:
            pr.label = ev[0] if p >= 0.5 else ne[0]
        pr.point, pr.lo, pr.hi = p, max(0.0, p - 0.35), min(1.0, p + 0.35)
        pr.method = "distress_lexicon"
        pr.strength = abs(p - 0.5)
        if d.going_concern:
            pr.facts.append("the company's own filing states substantial doubt about its ability to continue as a going concern")
        pr.facts.append(f"the density of distress language in its own filings (going-concern doubt, net losses, default, forbearance, "
                        f"restructuring, delisting, covenant waivers) scores {d.score:+.2f}, which maps to a probability of {p:.2f}")
        if d.span:
            pr.spans.append(d.span)
        return

    # 4) pooled series model
    if s is not None:
        vals = s.values
        if change:
            dev = vals[-1] - statistics.fmean(vals)
            point = -kappa * dev
            sd = h_change_sd(vals, h)
            pr.method = "series_reversion"
            pr.facts.append(f"{s.label} stands at {vals[-1]:g} against its {len(vals)}-period average of {statistics.fmean(vals):.4g} "
                            f"(a deviation of {dev:+.4g}); in this unit's own history about {kappa:.0%} of such a deviation reverses "
                            f"over {h} reports, giving an expected change of {point:+.3g}")
        else:
            wl, win = w_level
            point = _level_pred(vals, wl, win)
            sd = level_residual_sd(vals, wl, win)
            if pooled_ratio > 0:
                sd = max(sd, POOLED_SD_FLOOR * pooled_ratio * _robust_scale(vals))
            pr.method = "series_level"
            tail = vals[-win:]
            lean = ("entirely on that average" if wl == 0 else "entirely on the latest reading" if wl == 1
                    else f"{wl:.0%} on the latest reading and {1 - wl:.0%} on that average")
            pr.facts.append(f"{s.label}: latest {vals[-1]:g}, average of the last {len(tail)} readings {statistics.fmean(tail):.4g}; "
                            f"the pre-cutoff history of all rows in this unit is forecast best by leaning {lean}, giving {point:.4g}")
            px = s.__dict__.get("proxy")
            if px is not None:
                # a higher-frequency series in the corpus already covers part of the target month
                w = min(1.0, max(0.0, (px.r - 0.6) / 0.3))
                point = w * px.point + (1 - w) * point
                sd = math.sqrt(w * px.resid_sd ** 2 + (1 - w) * sd ** 2)
                pr.method = "series_proxy"
                pr.facts.insert(0, f"the {px.label} series moved {px.x_target:+.2f}% in the target month so far ({px.weeks_in_target} observations); "
                                   f"over {px.n} earlier months this component tracked it as {px.a:+.2f} + {px.b:.2f} x that change (correlation {px.r:.2f}), "
                                   f"which implies {px.point:+.3g} for the target month")
                pr.spans.insert(0, px.span)
        hw = Z90 * sd * 1.1
        pr.point, pr.lo, pr.hi = point, point - hw, point + hw
        base = 0.0 if change else (anchor[1] if anchor else vals[-1])
        pr.strength = abs(point - base) / (sd + 1e-9)
        pr.spans.append((s.doc_id, s.row_spans[-1][0], s.row_spans[-1][1]))
        ns = notes_span(unit, s.doc_id, norm_tokens(str(ent.get("name") or "")) | norm_tokens(s.label),
                        names=(str(ent.get("name") or ""), s.label))
        if ns:
            pr.spans.append(ns)
        if ttype == "classification":
            pr.label = _label_from_value(unit, ent, sem, point, base, sd, pr=pr)
        if prob:
            pr.point = min(max(pr.point, 0.0), 1.0)
        return

    # 5) anchors / priors
    if prob:
        p = 0.1
        pr.point, pr.lo, pr.hi = p, 0.0, 0.5
        pr.method = "prior_probability"
        return
    if change and path is not None and _maturity_years(ent) is not None:
        pp, m0, y0 = path
        m = _maturity_years(ent)
        gap_bp = (pp.anchor - y0) * 100.0
        gap_bp = max(-PATH_GAP_CAP, min(PATH_GAP_CAP, gap_bp))
        beta = min(1.0, math.sqrt(PATH_M0 / m)) if m > 0 else 1.0
        point = PATH_SHARE * gap_bp * beta
        pr.method = "policy_path"
        hw = fallback_halfwidth(unit, ent, point, anchor)
        pr.point, pr.lo, pr.hi = point, point - hw, point + hw
        pr.strength = abs(point) / (hw / Z90 + 1e-9)
        what = ("the Committee's projected year-end policy rate" if pp.anchor_kind == "sep"
                else "the current target-range midpoint moved one more step in the signalled direction" if pp.anchor_kind == "step"
                else "the current target-range midpoint (no further move signalled)")
        pr.facts.append(f"near-term policy anchor {pp.anchor:.3f}% ({what}; target-range midpoint {pp.midpoint:.3f}%, last step {pp.step * 100:+.0f} bp) "
                        f"sits {gap_bp:+.0f} bp from the {m0:g}-year yield of {y0:g}%")
        pr.facts.append(f"half of that gap is expected to close by the resolution, passed through to the {m:g}-year point at a factor of {beta:.2f}, "
                        f"an expected change of {point:+.1f} bp")
        pr.spans.append(pp.span)
        if pp.sep_span:
            pr.spans.append(pp.sep_span)
        if ttype == "classification":
            pr.label = _label_from_value(unit, ent, sem, point, 0.0, hw / Z90, pr=pr)
        return
    if change:
        point = 0.0
        pr.method = "no_change"
        pr.facts.append("no pre-cutoff passage gives a directional signal, so the expected change is zero")
    elif anchor is not None:
        point = anchor[1]
        pr.method = "carry_forward"
        pr.facts.append(f"no filed figure moves the estimate off the task table's {anchor[0].replace('_', ' ')} of {anchor[1]:g}, which is carried forward")
    else:
        point = 0.0
        pr.method = "zero_default"
    hw = fallback_halfwidth(unit, ent, point, anchor)
    pr.point, pr.lo, pr.hi = point, point - hw, point + hw
    if ttype == "classification":
        ref = anchor[1] if anchor else None
        pr.label = _label_from_value(unit, ent, sem, point, ref if not change else 0.0, hw / Z90, pr=pr)
