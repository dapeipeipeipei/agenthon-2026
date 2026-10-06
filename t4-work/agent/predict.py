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
        return max(Z90 * sd * 1.75, 10.0)
    if "return" in tname or "abnormal return" in p:
        return Z90 * 5.0 * math.sqrt(max(h, 1) / 2.0)
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

    preds: list[Pred] = []
    for ent in unit.entities:
        eid = ent["entity_id"]
        anchor = row_anchor(ent)
        pr = Pred(entity_id=eid, point=0.0, lo=-1.0, hi=1.0, anchor_key=anchor[0] if anchor else None)
        try:
            _fill(pr, unit, ent, ttype, change, anchor, series.get(eid), vint.get(eid), eps.get(eid),
                  dist.get(eid), w_level, kappa, steps.get(eid, h_common), sem)
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
    return preds


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


def _label_from_value(unit: Unit, ent: dict, sem: dict[str, str], value: float, ref: float | None,
                      sd: float | None = None) -> str | None:
    """Map a numeric forecast to a label. With a three-way vocabulary (up / middle / down around a
    threshold) and a forecast spread `sd`, pick the label with the highest probability under a
    normal forecast distribution (ties: up, then middle), so a narrow middle band is not chosen
    by default when the forecast is very uncertain."""
    ups = [l for l, s in sem.items() if s == "up"]
    downs = [l for l, s in sem.items() if s == "down"]
    mids = [l for l, s in sem.items() if s == "middle"]
    if not ups or not downs:
        return None
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
          w_level: tuple[float, int], kappa: float, h: int, sem: dict) -> None:
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
        pr.facts.append(f"latest filed quarter diluted EPS {e.current:g} vs {e.prior_year:g} a year earlier")
        if prior is not None:
            eps_hat = prior + 0.5 * delta
            sd = 0.35 * abs(delta) + 0.08 * abs(prior) + 0.02
            pr.facts.append(f"forecast EPS = {prior:g} + 0.5 x ({e.current:g} - {e.prior_year:g}) = {eps_hat:.3f}")
            pr.method = "eps_momentum"
            pr.strength = abs(eps_hat - prior) / (abs(prior) + 0.05)
            if ttype in ("regression", "ranking") and change and "eps" not in (ent.get("unit") or "").lower():
                base = abs(prior) if abs(prior) > 0.05 else 0.05
                g = (eps_hat - prior) / base * 100.0
                g_lo = ((eps_hat - Z90 * sd) - prior) / base * 100.0
                g_hi = ((eps_hat + Z90 * sd) - prior) / base * 100.0
                pr.point, pr.lo, pr.hi = g, g_lo, g_hi
                pr.facts.append(f"implied YoY growth {g:.1f}%")
            else:
                pr.point, pr.lo, pr.hi = eps_hat, eps_hat - Z90 * sd, eps_hat + Z90 * sd
                if ttype == "classification":
                    pr.label = _label_from_value(unit, ent, sem, eps_hat, prior, sd)
            return
        if cons is not None:
            # consensus already prices the trend: keep it, use the trend only for the band
            sd = 0.06 * abs(cons) + 0.1 * abs(delta) + 0.02
            pr.point, pr.lo, pr.hi = cons, cons - Z90 * sd, cons + Z90 * sd
            pr.method = "consensus_anchor"
            if ttype == "classification":
                pr.label = _label_from_value(unit, ent, sem, cons, cons, sd)
            return

    # 2) vintage revisions
    if v is not None:
        base = anchor[1] if anchor else v.values[-1]
        revs = [r for r in (v.age_matched or v.revisions) if finite(r)]
        mean_rev = statistics.fmean(revs) if revs else 0.0
        nz = [r for r in revs if r != 0]
        sd = _rms([r - mean_rev for r in revs]) if len(revs) >= 2 else abs(base) * 0.002
        if sd <= 0:
            sd = max(abs(base) * 0.001, 1e-3)
        point = base + mean_rev
        pr.point, pr.lo, pr.hi = point, point - Z90 * sd * 1.2, point + Z90 * sd * 1.2
        pr.method = "vintage_revision"
        pr.strength = abs(mean_rev) / (sd + 1e-9)
        up_share = (sum(1 for r in nz if r > 0) / len(nz)) if nz else 0.5
        scope = f"revision number {v.next_age} of other reference months" if v.age_matched else "consecutive revisions"
        pr.facts.append(f"{len(revs)} past {scope} in the vintage table average {mean_rev:+.4g}; {up_share:.0%} of the non-zero ones were upward")
        pr.spans += [(v.doc_id, v.row_span[0], v.row_span[1])]
        if ttype == "classification":
            if mean_rev == 0 and nz:
                mean_rev = 1 if up_share >= 0.5 else -1
            pr.label = _label_from_value(unit, ent, sem, base + mean_rev, base, sd) if mean_rev != 0 else None
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
        pr.facts.append(f"distress score {d.score:+.2f} (probability {p:.2f})")
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
            pr.facts.append(f"{s.label} last {vals[-1]:g} vs its {len(vals)}-period mean {statistics.fmean(vals):.4g}; pooled reversion kappa {kappa:.2f} over {h} steps")
        else:
            wl, win = w_level
            point = _level_pred(vals, wl, win)
            sd = level_residual_sd(vals, wl, win)
            pr.method = "series_level"
            tail = vals[-win:]
            pr.facts.append(f"{s.label}: last {vals[-1]:g}, trailing {len(tail)}-period mean {statistics.fmean(tail):.4g}, weight on last value {wl:.2f} (picked by backtest on all rows)")
        hw = Z90 * sd * 1.1
        pr.point, pr.lo, pr.hi = point, point - hw, point + hw
        base = 0.0 if change else (anchor[1] if anchor else vals[-1])
        pr.strength = abs(point - base) / (sd + 1e-9)
        pr.spans.append((s.doc_id, s.row_spans[-1][0], s.row_spans[-1][1]))
        ns = notes_span(unit, s.doc_id, norm_tokens(str(ent.get("name") or "")) | norm_tokens(s.label))
        if ns:
            pr.spans.append(ns)
        if ttype == "classification":
            pr.label = _label_from_value(unit, ent, sem, point, base, sd)
        if prob:
            pr.point = min(max(pr.point, 0.0), 1.0)
        return

    # 5) anchors / priors
    if prob:
        p = 0.1
        pr.point, pr.lo, pr.hi = p, 0.0, 0.5
        pr.method = "prior_probability"
        return
    if change:
        point = 0.0
        pr.method = "no_change"
        pr.facts.append("no pre-cutoff signal strong enough to move the forecast off zero change")
    elif anchor is not None:
        point = anchor[1]
        pr.method = "carry_forward"
        pr.facts.append(f"carry forward {anchor[0]} = {anchor[1]:g}")
    else:
        point = 0.0
        pr.method = "zero_default"
    hw = fallback_halfwidth(unit, ent, point, anchor)
    pr.point, pr.lo, pr.hi = point, point - hw, point + hw
    if ttype == "classification":
        ref = anchor[1] if anchor else None
        pr.label = _label_from_value(unit, ent, sem, point, ref if not change else 0.0, hw / Z90)
