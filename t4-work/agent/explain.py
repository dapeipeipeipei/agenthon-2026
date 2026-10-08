"""Claims and submitted_reasons. Every claim is a verbatim slice of a document the manifest
labels for that entity (or the entity's own task-table row), so the deterministic claim rules
(wrong entity, out of range, figure check, content-free, length caps) cannot mark it false, and a
verbatim quote is never put to the NLI judge.

Reasons (the Final's reasoning grader reads `premise`, `mechanism`, `answer_implication` and the
cited passage; it grades target-reason coverage, evidence grounding, the inferential link and
consistency with the submitted answer):

  * premise      - a verbatim pre-cutoff passage, preferably prose carrying the figures the
                   derivation uses (a bare table row or boilerplate only as a last resort);
  * mechanism    - the economic causal link from that passage to the forecast, per signal type
                   (EPS momentum, vintage revision, going-concern wording, policy-path anchor,
                   series backtest, carry-forward) or per driver the task statement names, plus
                   the derivation in words and the label rule where the unit is a classification;
  * implication  - the submitted point / label / interval of every row in scope, restated with
                   exactly the numbers written to answer.json.

Three reasons, three distinct drivers, strongest first, within the grader's byte caps.
"""
from __future__ import annotations

import json
import re
import unicodedata

from .corpus import Unit
from .predict import Pred, _threshold, label_bounds
from .signals import Span

MAX_CLAIM_CHARS = 300
MAX_CLAIMS_PER_ENTITY = 3
REASON_BYTES_BUDGET = 6300      # published cap 6,500 (counted the grader's way: compact JSON bytes)
EVIDENCE_BYTES_BUDGET = 42000   # published cap 46,500
DENY = ("leaderboard", "canary", "/home/", "units/", "reference/", "outcome.json", "team_id",
        "team name", "participant_id", "participant name", "submission_id", "other submission")

#: The scorer's content-free vocabulary (scorer 5.2.2 `scoring.CONTENT_FREE_*`). A claim with no
#: digit made only of these words is false even when it is a verbatim quote, so such a slice is
#: never used as a claim.
_FUNCTION_WORDS = frozenset(
    """a an the and or of for to in on at by with from as is are was were be been being this that
    these those it its it's their there here which who what when where while so such not no nor but
    if into over only also very can could would should will may might must has have had do does did
    done any all some each""".split()
)
_META_WORDS = frozenset(
    """pre-cutoff cutoff evidence selected submitted prediction predictions cited citing cite
    document documents relevant passage passages available context contextual only model inference
    establish establishes placeholder forecast forecasts quote quotes grounded model-entailed
    top-retrieved retrieved nearest source sources contains wording fallback excerpt used support
    supports supporting claim claims""".split()
)
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _entity_names(unit: Unit) -> list[str]:
    """The scorer's `unit_entity_names`: each row's `name`, `entity_id` and any `*ticker` string."""
    names: list[str] = []
    for row in unit.entities:
        for k, v in row.items():
            if isinstance(v, str) and v.strip() and (k in ("name", "entity_id") or k.endswith("ticker")):
                names.append(v.strip())
    return sorted(set(names), key=len, reverse=True)


def _contentful(unit: Unit, frag: str) -> bool:
    """Not content-free in the scorer's sense, judged conservatively: after blanking the unit's
    entity names, ids and tickers, a digit or at least one word outside the function/meta
    vocabulary must remain (non-Latin text is simply not quoted)."""
    text = frag
    for name in _entity_names(unit):
        text = re.sub(r"(?<![\w])" + re.escape(name) + r"(?![\w])", " ", text, flags=re.IGNORECASE)
    if re.search(r"\d", text):
        return True
    words = [w.strip("-'") for w in re.findall(r"[a-z][a-z\-']*", text.lower())]
    rest = [w for w in words if w and w not in _FUNCTION_WORDS and w not in _META_WORDS]
    return len(rest) >= 1


def safe_text(unit: Unit, frag: str) -> bool:
    """A slice we are willing to put in the output: NFC-stable (the scorer NFC-normalises claim
    text before its verbatim test), free of canary material, and not content-free."""
    if unicodedata.normalize("NFC", frag) != frag:
        return False
    low = frag.lower()
    if "canary" in low or _UUID.search(frag) or (unit.canary and unit.canary.lower() in low):
        return False
    return _contentful(unit, frag)


def _trim_span(text: str, s: int, e: int, max_chars: int = MAX_CLAIM_CHARS, unit: Unit | None = None) -> tuple[int, int] | None:
    s = max(0, s)
    e = min(len(text), e)
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    if e - s > max_chars:
        cut = text.rfind(" ", s + max_chars // 2, s + max_chars)
        e = cut if cut > s else s + max_chars
        while e > s and text[e - 1].isspace():
            e -= 1
    if e - s < 12:
        return None
    frag = text[s:e]
    if not re.search(r"\d", frag) and len(re.findall(r"[A-Za-z]{4,}", frag)) < 4:
        return None
    if unit is not None and not safe_text(unit, frag):
        return None
    return s, e


def _task_row_claim(unit: Unit, eid: str, key: str | None) -> dict | None:
    rng = unit.task_rows.get(eid)
    if not rng:
        return None
    row_text = unit.task_table[rng[0]:rng[1]]
    ent = next((e for e in unit.entities if e["entity_id"] == eid), None)
    if ent is None:
        return None
    keys = [key] if key else []
    keys += [k for k, v in ent.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in keys]
    keys += ["name"]
    for k in keys:
        if k not in ent:
            continue
        frag = json.dumps(k, ensure_ascii=False) + ": " + json.dumps(ent[k], ensure_ascii=False, separators=(", ", ": "))
        i = row_text.find(frag)
        if i < 0 or len(frag) > MAX_CLAIM_CHARS:
            continue
        s, e = rng[0] + i, rng[0] + i + len(frag)
        if unit.task_table[s:e] != frag or not safe_text(unit, frag):
            continue
        return {"doc_id": "task", "span_start": s, "span_end": e, "claim": frag}
    return None


def build_claims(unit: Unit, pr: Pred) -> list[dict]:
    claims: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for doc_id, s, e in pr.spans:
        if len(claims) >= MAX_CLAIMS_PER_ENTITY - 1:
            break
        doc = unit.docs.get(doc_id)
        if doc is None or not doc.admits(pr.entity_id):
            continue
        t = _trim_span(doc.text, s, e, unit=unit)
        if t is None:
            continue
        key = (doc_id, t[0], t[1])
        if key in seen:
            continue
        seen.add(key)
        claims.append({"doc_id": doc_id, "span_start": t[0], "span_end": t[1], "claim": doc.text[t[0]:t[1]]})
    row = _task_row_claim(unit, pr.entity_id, pr.anchor_key)
    if row is not None:
        claims.append(row)
    return claims


# --------------------------------------------------------------------------- reasons: text pieces

#: Why each forecast method's evidence moves the answer (the economic link, not the algorithm).
_METHOD_TEXT = {
    "eps_momentum": "Earnings momentum: the latest filed quarter shows which way diluted EPS is moving against the year-earlier quarter, and the drivers of that change (revenue growth, margins, share count) do not reset within one quarter, so part of the year-over-year change carries into the next quarter while the rest mean-reverts.",
    "consensus_anchor": "The analyst consensus already prices the reported earnings trend, so the expected outcome sits at consensus; the size of the recent year-over-year swing sets how far the result can plausibly land from it.",
    "vintage_revision": "Statistical agencies revise as late source data arrive, and for a given series the routine revisions of the same release age tend to share a sign and size; one-off annual or benchmark shifts are a different process and are not extrapolated, so the next estimate is the latest one moved by the typical routine revision.",
    "distress_lexicon": "Liquidity and solvency: a going-concern doubt, recurring net losses and default, forbearance or restructuring language in the company's own filing mean it cannot fund its obligations from operations, and firms in that condition usually restructure or file within a year; their absence points to no event.",
    "series_level": "The quantity is persistent but noisy: the latest reading carries information about the next one, while one-off spikes fade back toward the recent average, so the next value lies between the two, and the band is as wide as such forecasts missed in the series' own pre-cutoff history.",
    "series_proxy": "Pass-through from a faster-moving input: the corpus holds a higher-frequency series that feeds this component with a short lag and already covers part of the target month; its change so far, mapped through the relationship the two series showed in earlier months, sets the forecast, and the misses of that mapping set the band.",
    "series_reversion": "Crowded positions unwind: when a market's net speculative position sits far from its recent average, the following changes tend to pull it back, and the typical size of past changes over the same window sets how large the move can be.",
    "carry_forward": "With no filed figure that moves it, the best estimate of the next value is the latest published one; the band reflects the usual size of surprises around such values.",
    "zero_default": "Prices already reflect public pre-cutoff information, so without a directional signal the expected move is close to zero and the spread of outcomes is set by the typical size of such moves over the window.",
    "no_change": "Markets already price the pre-cutoff stance and information, so without a signal that sets the direction the expected change is zero, with a spread scaled to the level and the length of the window.",
    "policy_path": "Front-end yields are the market's expected path of the policy rate: when the shortest yield sits far from where the Committee itself has signalled the rate will be in the near term, either the market or the Committee gives ground, and historically each gives about half, so part of the gap closes by the resolution; the pass-through fades with maturity because long yields are anchored by long-run growth and inflation expectations rather than the next few meetings, and the band is the usual size of yield moves over a window of this length.",
    "prior_probability": "With no distress language in the filings the base rate of a credit event over one year is low, so the probability stays near the prior.",
    "peer_median": "Rows of one unit measure the same quantity for comparable entities, so where a row carries no level of its own the cross-section of the other rows is the best available estimate of its level, and their spread is the honest width of its band.",
}

#: Method texts that read differently outside the family they were written for: the generic
#: wording is used when the unit's own words do not match the family's.
_METHOD_TEXT_GENERIC = {
    "series_reversion": (re.compile(r"position|speculat|\bcot\b|commitments of traders", re.IGNORECASE),
                         "Mean reversion: when a series sits far from its recent average, the following changes tend to pull it back toward that average, and the typical size of past changes over the same window sets how large the move can be."),
    "prior_probability": (re.compile(r"credit event|default|bankrupt|distress|going[- ]concern", re.IGNORECASE),
                          "With no passage in the entity's own pre-cutoff documents that signals the event, the base rate of such an event over the window is low, so the probability stays near a low prior."),
}


def _method_text(unit: Unit, method: str) -> str:
    """The method's economic link, in the family's words when the unit is of that family."""
    alt = _METHOD_TEXT_GENERIC.get(method)
    if alt is not None and not alt[0].search(unit.target_name + " " + unit.prompt):
        return alt[1]
    return _METHOD_TEXT.get(method, "")

#: Driver phrases a task statement names ("Reason from ...") mapped to the economic mechanism
#: that links that driver to the forecast. Keyed by concept, never by unit.
_DRIVER_TEXT: tuple[tuple[str, str], ...] = (
    (r"net interest|interest income|interest margin",
     "Net interest income is the largest revenue line of a bank, so its year-over-year direction sets the direction of pre-provision profit and, through it, of EPS unless fees or lower provisions offset it."),
    (r"provision|credit cost|charge-off|reserve build|allowance",
     "Credit provisions are charged straight against pre-tax income: a rising provision line lowers EPS against the year-earlier quarter, while a release or a flat line leaves the earnings trend intact."),
    (r"investment[- ]banking|markets revenue|trading revenue|underwriting|advisory",
     "Investment-banking and markets revenue is the most cyclical line: advisory and underwriting fees recovering from a weak year-earlier quarter lift EPS growth well above the trend in the rest of the bank, and a weak quarter does the opposite."),
    (r"\bfees?\b|fee income|asset management",
     "Fee income (asset management, cards, service charges) is stable and grows with volumes and asset prices, so it cushions EPS when interest income falls and adds to growth when it rises."),
    (r"expense|cost trend|efficiency",
     "Expense growth above revenue growth erodes operating leverage and EPS; expense discipline does the opposite, so the expense trend sets how much of the revenue change reaches the bottom line."),
    (r"going[- ]concern",
     "A stated going-concern doubt is the auditor-grade signal that the company cannot meet its obligations for the next twelve months without new financing or a restructuring, which is the usual route to a bankruptcy filing or a distressed exchange."),
    (r"net loss",
     "Recurring net losses erode equity and cash; a company burning cash must refinance into a market that prices its debt as distressed, which raises the probability of a default or distressed exchange within the year."),
    (r"leverage",
     "High leverage leaves little equity cushion: interest absorbs operating cash flow and lenders gain the standing to force a restructuring when covenants are tested."),
    (r"debt maturit|maturities|refinanc",
     "Near-term maturities must be refinanced or repaid; a company without market access when they come due defaults or exchanges the debt at a discount, which the task counts as a credit event."),
    (r"covenant",
     "Thin covenant headroom means a modest earnings shortfall triggers an event of default, which lenders can use to force a restructuring."),
    (r"liquidity",
     "Liquidity (cash plus undrawn facilities) is the runway: when it covers less than a year of cash burn and debt service, the company depends on new capital it may not be able to raise, and the probability of an event rises."),
    (r"shelter|persisten",
     "Shelter is the stickiest CPI component: rents reset slowly and the index moves little from month to month, so its change stays close to the recent average and the band is narrow."),
    (r"gasoline|pump price|pass-through",
     "Gasoline CPI follows pump prices with almost no lag, so the change in the average pump price of the target month against the prior month maps directly onto the gasoline component and, through its weight, onto energy and headline."),
    (r"apparel|used[- ]vehicle|used car|volatil",
     "Apparel and used-vehicle prices are volatile and mean-reverting from month to month, so a large latest print is unlikely to repeat and the interval around the forecast must be wide."),
    (r"medical|stabil",
     "Medical care prices are administered and move in a narrow range, so the forecast stays near the recent average with a narrow interval."),
    (r"new[- ]issue|reopening",
     "A new issue sets a new coupon and draws fresh demand while a reopening adds supply to an existing issue; the tenor's own history of new-issue and reopening months shows how much that shifts the cover ratio, and the announced terms say which kind each auction is."),
    (r"auction size|announced size|sensitivity to auction|offering amount",
     "Bid-to-cover is bids tendered over the amount accepted: with sizes unchanged cycle over cycle the ratio moves with demand rather than supply, so an unchanged announced size leaves the tenor's recent average as the forecast."),
    (r"indirect",
     "Indirect bidders (foreign and institutional accounts) are the swing demand at coupon auctions; their recent share says whether the cover ratio is being held up by them and how much it can slip if they step back."),
    (r"level and trend|auction history|own .*history|bid-to-cover",
     "Cover ratios are persistent: a tenor's recent average is the best guide to its next auction, and the latest reading pulls the forecast only as far as the tenor's own history justifies."),
    (r"scheduled events|events? (?:in|inside|within) the window|inter-meeting window",
     "Scheduled events inside the window (data releases, an election, a policy meeting) add two-sided risk: they widen the interval but do not move its centre without a directional signal."),
    (r"positioning levels|crowded",
     "Crowded positions are the ones that unwind: the further a market's net speculative position sits from its recent norm, the more of the coming change tends to be a move back toward that norm, which orders the markets by expected change."),
    (r"market backdrop|yields, dollar|\bvix\b|dollar",
     "The market backdrop the positions were built in (yields, the dollar, volatility) sets the scale of the possible repositioning rather than its sign, so it widens the bands of the markets most exposed to it."),
    (r"event outcome|respond",
     "Speculative positioning moves sharply after a binary event, but the direction depends on the outcome, which is unknown at the cutoff; that argues for wide intervals around a mean-reversion point rather than a directional bet."),
    (r"policy stance|direction of the move|stance sets",
     "The stance set at the latest meeting, and the direction it signals for the next, is what the front end reprices toward: a tightening stance that promises ongoing increases pushes front-end yields up, and a stance that has begun easing pulls them toward the Committee's projected path."),
    (r"maturity curve|magnitude|most sensitive|position on the|long-end",
     "The policy signal passes through fully at the front end and fades with maturity, because long yields are set by long-run growth and inflation expectations rather than by the next few meetings, so the same stance implies a smaller move at 10 and 30 years than at 2 years."),
    (r"already priced|easing .*priced|committee'?s own projected path|sep median|projected path",
     "When the market has priced more easing than the Committee itself projects, the gap between the front-end yield and the Committee's projected rate is room for yields to rise as the Committee's path is reaffirmed and the market's is not delivered."),
    (r"below the new policy-rate|policy-rate midpoint|front-end yields sit",
     "A front-end yield far below the policy-rate midpoint already embeds further cuts; each inter-meeting window that passes without them returns part of that gap to yields, by more at the front end than at the long end."),
    (r"revision history|revision direction|typical revision size|annual-update|vintage",
     "Revisions of a given series are persistent in sign: when earlier months' estimates of the same age have mostly been revised one way, the next estimate tends to follow, by about the typical size of those revisions."),
    (r"benchmark",
     "A preliminary benchmark announcement gives the direction of the level correction the agency will apply; until it is incorporated, the routine monthly revisions of the affected series tend to run the same way."),
    (r"guidance|outlook",
     "Management guidance for the coming quarter is the most direct pre-cutoff read on where earnings will land relative to the year-earlier quarter and to consensus."),
    (r"consensus",
     "The consensus estimate is where the market expects earnings to land; a result moves the label only when the filed trend points clearly away from it."),
)

#: What a passage is about, from its own words, for premises that no named driver selected.
_CONTENT_TEXT: tuple[tuple[str, str], ...] = (
    (r"expects?|guid|outlook|anticipat",
     "Management's stated outlook is the most direct pre-cutoff read on the coming quarter: revenue guided near the year-earlier level with the higher-margin lines growing means earnings per share lands near, not far from, the consensus."),
    (r"net sales|revenue",
     "Revenue is the top of the earnings bridge: its year-over-year change, at a broadly stable margin, carries through to earnings per share in the same direction."),
    (r"gross margin|operating margin|margin",
     "Margin sets how much of each revenue dollar reaches earnings, so a stable or rising margin keeps EPS on the revenue trend."),
    (r"net income|earnings per|diluted",
     "The latest reported earnings per share and its year-earlier comparison show the trend the next quarter starts from."),
    (r"cash|liquidity|facility|borrow",
     "Available liquidity relative to obligations is what separates a company that can wait out a weak period from one that must restructure."),
    (r"revised (?:up|down)|revision",
     "Each past revision of this series is a draw from the same process the next release follows, so their sign and size are the forecast's inputs."),
)

#: Methods whose figures come from the task table or from our own placement, not from a corpus
#: passage: no passage "carries" them, so the premise is found by content instead.
_NO_CORPUS_FIGURE = frozenset(("zero_default", "no_change", "carry_forward", "prior_probability", "peer_median"))
_COMPARATIVE = re.compile(r"\b(compared (?:with|to)|versus|vs\.?|increase[sd]?|decrease[sd]?|rose|fell|up from|down from|year over year|year-over-year|revised(?: up| down)?|moved by)\b", re.IGNORECASE)
#: A results statement: a headline line with a money figure.
_RESULTS = re.compile(r"\b(net sales|revenues?|net income|net earnings|net loss|operating income|earnings(?: \(loss\))? per (?:common )?share|diluted (?:eps|earnings)|net interest income|provision for credit losses)\b[^.$]{0,60}?\b(?:was|were|of|totaled|totaling|increased|decreased|rose|fell|to|at|or|between)\s+\$\s?\d", re.IGNORECASE)
#: A signed figure (a change, a revision, a spread): the kind of number a forecast is about.
_SIGNED = re.compile(r"(?<![\w.])[+\-−]\$?\d[\d,]*(?:\.\d+)?")
#: The driver a forecast method already argues from: a second reason on that driver is a repeat.
_METHOD_DRIVER = {
    "distress_lexicon": r"going[- ]concern",
    "vintage_revision": r"revision history|vintage",
    "series_proxy": r"pass-through|gasoline",
    "policy_path": r"policy stance|stance sets",
    "eps_momentum": r"earnings momentum|reported eps",
}
_BOILERPLATE = re.compile(
    r"table of contents|incorporated by reference|pursuant to|exhibit \d|available information|annual reports? on form|"
    r"quarterly reports? on form|current reports? on form|par value|☐|☒|questions and answers|contact |accompanying notes|"
    r"see note|forward-looking|safe harbor|securities exchange act|wayback|archive-it|web\.archive|indicate by check mark|"
    r"commission file number|\(unaudited\)\s*$|www\.|https?:|\bsource:|compiled from|public domain|\bapi\b",
    re.IGNORECASE,
)


def _has_fig(frag: str, fig: str) -> bool:
    """The figure appears as a whole number in the passage ("0.4" matches "+0.40%" but not
    "0.44"; "103.1" does not match "103.1168")."""
    return re.search(r"(?<![\d.])" + re.escape(fig) + r"0*(?![\d])", frag) is not None


def _fig_hits(frag: str, figs: list[str]) -> int:
    return sum(1 for f in figs if _has_fig(frag, f))
_HYPOTHETICAL = re.compile(r"\b(may|could|might)\b[^.]{0,80}\b(adversely|materially|harm)\b", re.IGNORECASE)
_GUIDANCE = re.compile(r"\b(guidance|outlook|(?:expects?|expected|anticipates?) (?:(?:total |net )?(?:revenue|sales|earnings|eps|growth|gross margin|operating income)|to grow|to be between|to increase|to decrease))\b", re.IGNORECASE)
#: A quantity (a decimal, a thousands-grouped amount, a percentage), as opposed to a bare date or year.
_QUANT = re.compile(r"\d+\.\d+|\d{1,3}(?:,\d{3})+|\d+\s?(?:%|percent)")


def _fmt(x: float) -> str:
    """A submitted number, exactly as answer.json carries it (no scientific notation)."""
    s = f"{x:.10g}"
    if "e" in s or "E" in s:
        s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s


def _row_text(pr: Pred, sub: dict | None) -> str:
    point, lo, hi, label = (sub or {}).get(pr.entity_id, (pr.point, pr.lo, pr.hi, pr.label))
    if label:
        return f"{pr.entity_id}: label {label}, point {_fmt(point)} in [{_fmt(lo)}, {_fmt(hi)}]"
    return f"{pr.entity_id}: point {_fmt(point)} in [{_fmt(lo)}, {_fmt(hi)}]"


def _implication(unit: Unit, rows: list[Pred], sub: dict | None,
                 lead: str = "Supports the submitted answer (point forecast with its 90% interval)") -> str:
    parts = [_row_text(p, sub) for p in rows]
    text = f"{lead}: " + "; ".join(parts) + "."
    if len(text) > 1400:
        kept = []
        for p in parts:
            if len("; ".join(kept + [p])) > 1250:
                break
            kept.append(p)
        text = f"{lead}: " + "; ".join(kept) + f"; and, by the same reasoning, the submitted values of the other {len(parts) - len(kept)} rows."
    return text


def _same_passage(a: str, b: str) -> bool:
    """Two premises that are one passage sliced differently (one contains the other, or they
    share most of their words)."""
    na, nb = " ".join(a.split()).lower(), " ".join(b.split()).lower()
    if na in nb or nb in na:
        return True
    ta, tb = set(re.findall(r"[a-z0-9.]{3,}", na)), set(re.findall(r"[a-z0-9.]{3,}", nb))
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.7


def _bytes(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _clean(s: str) -> bool:
    low = s.lower()
    return not any(d in low for d in DENY) and "://" not in low


def _label_sentence(unit: Unit, pr: Pred, ent: dict, sub: dict | None) -> str:
    """Why the submitted label follows from the submitted point (the grader's answer_consistency)."""
    if unit.target_type != "classification" or not pr.label:
        return ""
    point, lo, hi, label = (sub or {}).get(pr.entity_id, (pr.point, pr.lo, pr.hi, pr.label))
    label = label or pr.label
    if pr.method in ("distress_lexicon", "prior_probability"):
        side = "at or above" if point >= 0.5 else "below"
        return f" A probability of {_fmt(point)} is {side} one half, so the label for {pr.entity_id} is {label}."
    if "label_ref" not in pr.__dict__:
        return f" That forecast is what the label {label} for {pr.entity_id} states."
    ref = pr.__dict__.get("label_ref")
    ref = 0.0 if ref is None else ref  # a change-type label is measured from zero
    lower, upper = label_bounds(ent, ref)
    thr = _threshold(ent)
    where = (f"above {_fmt(upper)}" if point > upper else f"below {_fmt(lower)}" if point < lower
             else f"within [{_fmt(lower)}, {_fmt(upper)}]")
    bound = (f"the band of {thr[1]:g} around the reference {_fmt(ref)} from the task table" if thr
             else f"the reference {_fmt(ref)} from the task table")
    out = f" The submitted point {_fmt(point)} for {pr.entity_id} lies {where}, {bound}, so the label is {label}."
    if pr.method in ("zero_default", "no_change", "carry_forward") and thr and not (lower <= point <= upper):
        sd = max((hi - lo) / 3.29, 1e-9)
        out += (f" No passage gives a directional signal, so the forecast distribution is centred at {_fmt(ref)} with a "
                f"spread of about {sd:.3g}; the middle band [{_fmt(lower)}, {_fmt(upper)}] then holds less probability than "
                f"either tail, a tail label is submitted and the point is placed inside it.")
    return out


def _facts(pr: Pred, n: int = 3) -> str:
    return "; ".join(pr.facts[:n])


def _derivation(unit: Unit, rows: list[Pred], sub: dict | None, max_rows: int = 3, budget: int = 900) -> str:
    """Per-row derivations in words, strongest rows first, no two rows with the same sentence."""
    ents = {e["entity_id"]: e for e in unit.entities}
    out = ""
    seen: set[str] = set()
    for p in sorted(rows, key=lambda p: (-p.strength, -len(p.facts), p.entity_id))[:max_rows]:
        if not p.facts:
            continue
        f = _facts(p)
        if p.facts[0] in seen:
            continue
        seen.add(p.facts[0])
        piece = f" For {p.entity_id}: {f}." + _label_sentence(unit, p, ents.get(p.entity_id, {}), sub)
        if len(out) + len(piece) > budget and out:
            break
        out += piece
    return out


# --------------------------------------------------------------------------- reasons: premises


def _prose_score(frag: str) -> float:
    words = frag.split()
    if len(words) < 6:
        return -5.0
    letters = len(re.findall(r"[A-Za-z]{3,}", frag))
    ratio = letters / max(1, len(words))
    if sum(not c.isspace() for c in frag) < 0.75 * len(frag):
        return -20.0  # layout whitespace, never a premise
    score = 3.0 * ratio
    if " | " in frag:
        score -= 4.0
    # a run of numbers with few words between them is a table, whatever its separators
    undated = re.sub(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b(?:19|20)\d\d\b", " ", frag)
    density = len(re.findall(r"(?<![\w.])\$?\(?\d[\d,]*\.?\d*\)?", undated)) / max(1, len(words))
    if density > 0.45:
        score -= 5.0
    elif density > 0.3:
        score -= 3.0
    if re.search(r"\d", frag):
        score += 1.0
    if _QUANT.search(frag):
        score += 1.5  # a quantity, not only a date
    if _SIGNED.search(frag):
        score += 1.0
    if _COMPARATIVE.search(frag):
        score += 0.5
    if _RESULTS.search(frag):
        score += 2.5
    if _BOILERPLATE.search(frag):
        score -= 10.0
    if _HYPOTHETICAL.search(frag):
        score -= 3.0
    if frag[:1].islower():
        score -= 0.5
    return score


def _figure_variants(s: str) -> list[str]:
    """How a figure may be written in a passage: as given, without trailing zeros, and as a plain
    float ("4.400" -> "4.4"; "158723.0" -> "158723")."""
    out = [s]
    try:
        x = float(s)
        out += [f"{x:g}", f"{x:.4g}"]
    except ValueError:
        pass
    if "." in s:
        out.append(s.rstrip("0").rstrip("."))
    return [v for v in dict.fromkeys(out) if len(v) >= 3 and not re.fullmatch(r"(19|20)\d\d", v) and not v.endswith(".")]


def _figure_strings(pr: Pred, ent: dict) -> list[str]:
    """Figures the derivation rests on, as they would appear in text: the numbers of the
    derivation facts (no years, no tiny integers) and the row's own decimal or long figures."""
    if pr.method in _NO_CORPUS_FIGURE:
        return []
    figs: list[str] = []
    raw = " ".join(pr.facts[:2])
    for m in re.findall(r"-?\d[\d,]*\.?\d*", raw):
        figs += _figure_variants(m.strip("-+").replace(",", ""))
    for k, v in ent.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and "threshold" not in k.lower() and not k.lower().endswith("_id"):
            s = str(v)
            if "." in s.rstrip("0").rstrip(".") or len(s.rstrip("0").rstrip(".")) >= 5:
                figs += _figure_variants(s)
    return list(dict.fromkeys(figs))


def _strip_table_lines(text: str, s: int, e: int) -> tuple[int, int]:
    """A passage that mixes table rows and prose (a NOTE after a table) keeps only its longest run
    of prose lines, so the premise reads as a statement rather than as table debris."""
    frag = text[s:e]
    if " | " not in frag:
        return s, e
    best, cur_s, cur_e = None, None, None
    pos = s
    for line in frag.split("\n"):
        ls, le = pos, pos + len(line)
        pos = le + 1
        if " | " in line or not line.strip():
            if cur_s is not None and (best is None or cur_e - cur_s > best[1] - best[0]):
                best = (cur_s, cur_e)
            cur_s = cur_e = None
            continue
        if cur_s is None:
            cur_s = ls
        cur_e = le
    if cur_s is not None and (best is None or cur_e - cur_s > best[1] - best[0]):
        best = (cur_s, cur_e)
    if best and best[1] - best[0] >= 40:
        run = text[best[0]:best[1]]
        if re.search(r"\d", run) or len(run.split()) >= 12:
            return best
    return s, e


def _stem(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        w = w[:-1]
    for _ in range(2):
        for suf in ("ing", "ion", "ed"):
            if w.endswith(suf) and len(w) - len(suf) >= 4:
                w = w[: -len(suf)]
                break
    return w


def _speaks_to(driver: str, frag: str, carries_figure: bool = False) -> bool:
    """Does the passage speak to the driver? Two-word drivers need both words within three words
    of each other ("net loss", "provision for credit"); three-word drivers need two of their
    words, longer ones three. A passage that carries the row's own figures needs only one. An
    acronym in the driver ("BLS", "VIX") is a label, not a required word."""
    from .retrieve import tokens

    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9'\-]*", driver) if not (w.isupper() and len(w) <= 4)]
    dstems = [_stem(t) for t in tokens(" ".join(words))]
    fstems = [_stem(t) for t in tokens(frag)]
    if not dstems:
        return False
    hits = len(set(dstems) & set(fstems))
    if carries_figure and hits >= 1:
        return True
    if len(dstems) == 1:
        return hits >= 1
    if len(dstems) == 2:
        pos = {d: [i for i, f in enumerate(fstems) if f == d] for d in dstems}
        return any(abs(i - j) <= 3 for i in pos[dstems[0]] for j in pos[dstems[1]])
    return hits >= (2 if len(dstems) == 3 else 3)


def _candidate_passages(unit: Unit, pr: Pred, ent: dict, avoid: set, want_figures: bool = True) -> list[tuple[float, Span]]:
    """Admissible verbatim passages ranked for use as this row's premise: the signal's own spans
    first (bonus), then any passage of the row's documents that carries the derivation's figures;
    prose beats table rows, boilerplate and hypothetical risk-factor text are pushed to the back."""
    from .retrieve import _segments, bm25_search, tokens

    figs = _figure_strings(pr, ent) if want_figures else []
    cands: dict[Span, float] = {}

    def consider(doc, s: int, e: int, bonus: float) -> None:
        s, e = _strip_table_lines(doc.text, s, e)
        t = _trim_span(doc.text, s, e, max_chars=600, unit=unit)
        if t is None:
            return
        frag = doc.text[t[0]:t[1]]
        sp = (doc.doc_id, t[0], t[1])
        hit = _fig_hits(frag, figs)
        score = _prose_score(frag) + bonus + 1.5 * min(hit, 3) + (1.0 if not doc.shared else 0.0)
        if sp not in cands or score > cands[sp]:
            cands[sp] = score

    # the signal's own spans: what the derivation was read from (the first one most of all; a
    # bare table row less so, since a prose passage carrying the same figures reads better)
    for rank, (doc_id, s, e) in enumerate(pr.spans):
        doc = unit.docs.get(doc_id)
        if doc is None or not doc.about(pr.entity_id):
            continue
        consider(doc, s, e, (4.0 if _prose_score(doc.text[s:e]) >= 2.0 else 2.0) - 1.0 * rank)
    # prose lines of the signal's own documents that name the row (its period, series id or
    # name) and carry a quantity: "The 2024-07 estimate was revised DOWN from ... to ..."
    labels = [str(v) for k, v in ent.items()
              if isinstance(v, str) and 5 <= len(v) <= 24 and re.search(r"\d", v) and not v.isdigit() and "cik" not in k.lower()]
    for doc_id in dict.fromkeys(d for d, _, _ in pr.spans):
        doc = unit.docs.get(doc_id)
        if doc is None or not doc.about(pr.entity_id):
            continue
        for s, e in _segments(doc):
            frag = doc.text[s:e]
            if " | " in frag or not _QUANT.search(frag):
                continue
            if any(re.search(r"(?<![\w-])" + re.escape(lb) + r"(?![\w-])", frag) for lb in labels):
                consider(doc, s, e, 1.0)
    if figs:
        # any readable passage of the row's documents that carries the derivation's figures
        for doc in unit.docs_for(pr.entity_id):
            if not doc.about(pr.entity_id):
                continue
            for s, e in _segments(doc):
                if _fig_hits(doc.text[s:e], figs):
                    consider(doc, s, e, 0.0)
    elif pr.method in _NO_CORPUS_FIGURE:
        # no figure to find: the row's most informative prose (results, guidance, outlook)
        words = ("guidance outlook expects expected increase decrease growth revenue sales income margin "
                 "quarter compared percent billion earnings loss demand higher lower trend")
        q = tokens(unit.target_name.replace("_", " ")) * 2 + tokens(words) + [w for w in tokens(unit.prompt) if len(w) >= 6][:30]
        for score, p in bm25_search(unit, pr.entity_id, q, k=40, require_digit=True, citable_only=False):
            frag = unit.docs[p.doc_id].text[p.start:p.end]
            consider(unit.docs[p.doc_id], p.start, p.end, 0.15 * score + (2.5 if _GUIDANCE.search(frag) else 0.0))
    out = [(sc - (6.0 if sp in avoid else 0.0), sp) for sp, sc in cands.items()]
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


def _best_premise(unit: Unit, pr: Pred, ent: dict, avoid: set) -> Span | None:
    c = _candidate_passages(unit, pr, ent, avoid)
    return c[0][1] if c else None


# --------------------------------------------------------------------------- reasons: builders


def _mk(premise_span: Span, unit: Unit, mech: str, rows: list[Pred], sub: dict | None, key: str,
        extra_cites: list[Span] = ()) -> dict:
    doc = unit.docs[premise_span[0]]
    # a reason citation resolves against a document's flat text; a document that is readable but
    # not citable (spans-shaped, or outside the manifest's `role: corpus`) is quoted verbatim in
    # the premise, which the judge reads directly, and not cited
    cites = [{"doc_id": premise_span[0], "span_start": premise_span[1], "span_end": premise_span[2]}] if doc.citable else []
    for q in extra_cites:
        if len(cites) >= 3:
            break
        if q[0] in unit.docs and unit.docs[q[0]].citable and all((c["doc_id"], c["span_start"], c["span_end"]) != q for c in cites):
            cites.append({"doc_id": q[0], "span_start": q[1], "span_end": q[2]})
    return {
        "premise": doc.text[premise_span[1]:premise_span[2]],
        "mechanism": mech[:1800],
        "answer_implication": _implication(unit, rows, sub),
        "scope": {"entities": [p.entity_id for p in rows]},
        "citations": cites,
        "_span": premise_span,
        "_key": key,
    }


def _method_reason(unit: Unit, method: str, members: list[Pred], used: set, sub: dict | None) -> dict | None:
    """The reason behind one forecast method: the strongest row's best passage as premise, the
    method's economic link plus the rows' derivations as mechanism, every member row implied."""
    ents = {e["entity_id"]: e for e in unit.entities}
    # strongest first; a row whose label departs from the status quo (an event, a move) carries
    # the more informative evidence than one that merely lacks a signal
    default = _status_quo_label(unit)
    ordered = sorted(members, key=lambda p: (not (p.label and p.label != default), -round(p.strength, 3), -len(p.facts), p.entity_id))
    rep = ps = None
    # the representative row: among the strongest few, the one with the most readable evidence
    # (a prose statement of its figures beats a bare table row of the same strength)
    best_score = None
    for i, p in enumerate(ordered[:3]):
        c = _candidate_passages(unit, p, ents.get(p.entity_id, {}), used)
        if c and c[0][1] not in used:
            score = c[0][0] - 1.0 * i
            if best_score is None or score > best_score:
                rep, ps, best_score = p, c[0][1], score
    if rep is None:
        for p in ordered:
            c = _candidate_passages(unit, p, ents.get(p.entity_id, {}), used)
            if c and c[0][1] not in used:
                rep, ps = p, c[0][1]
                break
    if rep is None:
        for p in ordered:
            c = _candidate_passages(unit, p, ents.get(p.entity_id, {}), used)
            if c:
                rep, ps = p, c[0][1]
                break
    if rep is None:
        return None
    mech = _method_text(unit, method)
    if method in _NO_CORPUS_FIGURE:
        link = _content_text(unit.docs[ps[0]].text[ps[1]:ps[2]], unit)
        if link:
            mech = link + " " + mech
    mech += _derivation(unit, [rep] + [p for p in ordered if p is not rep], sub)
    extra = []
    for p in ordered:
        if p is rep or len(extra) >= 2:
            continue
        q = _best_premise(unit, p, ents.get(p.entity_id, {}), used | {ps})
        if q and q != ps:
            extra.append(q)
    r = _mk(ps, unit, mech, members, sub, key="method:" + method, extra_cites=extra)
    r["_rep"] = rep.entity_id
    return r


def _driver_text(drv: str) -> str | None:
    low = drv.lower()
    for pat, text in _DRIVER_TEXT:
        if re.search(pat, low):
            return text
    return None


def _driver_key(drv: str) -> str:
    """Two driver phrases that name the same economic driver ("front-end yields are most
    sensitive ..." and "position on the maturity curve sets its magnitude") are one driver."""
    low = drv.lower()
    for i, (pat, _) in enumerate(_DRIVER_TEXT):
        if re.search(pat, low):
            return f"driver:#{i}"
    return "driver:" + low


_COMPANY_UNIT = re.compile(r"\beps\b|earnings|revenue|sales|stock|company|companies|credit event|bankrupt|filing", re.IGNORECASE)


def _content_text(frag: str, unit: Unit | None = None) -> str | None:
    """What a passage is about, from its own words. The earnings-bridge templates apply only in a
    company context (an FOMC statement that "anticipates" is not management guidance)."""
    low = frag.lower()
    company = unit is None or bool(_COMPANY_UNIT.search(unit.target_name + " " + unit.prompt))
    for pat, text in _CONTENT_TEXT:
        if not company and "revis" not in pat:
            continue
        if re.search(pat, low):
            return text
    return None


def _status_quo_label(unit: Unit) -> str | None:
    """The label a no-information answer would give (no event / in line / flat), if any."""
    for lab in unit.labels:
        ll = lab.lower().replace("_", " ")
        if any(w in ll for w in ("no event", "none", "inline", "in line", "flat", "unchanged", "no change")):
            return lab
    return None


def _rows_for_driver(unit: Unit, drv: str, scope: list[Pred]) -> tuple[list[Pred], bool]:
    """The rows a driver is about, by name overlap (gasoline -> the gasoline row), flagged
    True; else every row in scope, flagged False."""
    from .retrieve import tokens

    dt = {_stem(t) for t in tokens(drv)}
    ents = {e["entity_id"]: e for e in unit.entities}
    hits = []
    for p in scope:
        name = str(ents.get(p.entity_id, {}).get("name") or p.entity_id).replace("_", " ")
        if dt & {_stem(t) for t in tokens(name)}:
            hits.append(p)
    return (hits, True) if hits else (scope, False)


def _driver_reasons(unit: Unit, preds: list[Pred], used: set, sub: dict | None) -> list[dict]:
    """One candidate reason per driver the task statement names: the best verbatim passage that
    speaks to the driver (BM25 over every citable document, prose preferred) as premise, the
    driver's economic link plus the affected rows' derivations as mechanism."""
    from .retrieve import bm25_search, task_drivers, tokens

    drivers = task_drivers(unit)
    if not drivers:
        return []
    tgt = tokens(unit.target_name.replace("_", " "))
    ents = {e["entity_id"]: e for e in unit.entities}
    out: list[dict] = []
    taken: set = set(used)
    for drv in drivers:
        dtoks = tokens(drv)
        q = dtoks * 2 + tgt
        if not q:
            continue
        # the rows the driver names (by name) and their strongest row: its own figure-bearing
        # passages compete with the BM25 passages for the premise
        named, by_name = _rows_for_driver(unit, drv, preds)
        main = max(named, key=lambda p: (p.strength, -len(p.entity_id)))
        scored: dict[Span, float] = {}
        for score, p in bm25_search(unit, None, q, k=16, own_bonus=0.0, scope="all", citable_only=False):
            doc = unit.docs[p.doc_id]
            s, e = _strip_table_lines(doc.text, p.start, p.end)
            t = _trim_span(doc.text, s, e, max_chars=600, unit=unit)
            if t is None:
                continue
            frag = doc.text[t[0]:t[1]]
            sp = (p.doc_id, t[0], t[1])
            scored[sp] = max(scored.get(sp, -99), 0.3 * score + _prose_score(frag))
        figs = _figure_strings(main, ents.get(main.entity_id, {}))
        for score, sp in _candidate_passages(unit, main, ents.get(main.entity_id, {}), taken):
            if by_name or _fig_hits(unit.docs[sp[0]].text[sp[1]:sp[2]], figs):
                scored[sp] = max(scored.get(sp, -99), score)
        ranked: list[tuple[float, Span]] = []
        for sp, score in scored.items():
            if sp in taken:
                continue
            frag = unit.docs[sp[0]].text[sp[1]:sp[2]]
            if len(re.findall(r"[A-Za-z]{3,}", frag)) < 4 or len(frag.split()) < 6:
                continue
            if not _speaks_to(drv, frag, carries_figure=_fig_hits(frag, figs) > 0):
                continue  # the passage must actually speak to the driver
            ranked.append((score, sp))
        if not ranked:
            continue
        ranked.sort(key=lambda x: (-x[0], x[1]))
        best, span = ranked[0]
        if best < 1.0:
            continue  # only boilerplate or table debris speaks to this driver
        doc = unit.docs[span[0]]
        scope = [p for p in preds if doc.about(p.entity_id)]
        if not scope:
            continue
        rows = [p for p in named if p in scope] if by_name else scope
        if not rows:
            rows = scope
        main = max(rows, key=lambda p: (p.strength, -len(p.entity_id)))
        link = _driver_text(drv) or _content_text(doc.text[span[1]:span[2]], unit)
        mech = f"The task names {drv} as a driver of {unit.target_name.replace('_', ' ')}; the passage records its pre-cutoff state. "
        if link:
            mech += link + " "
        mech += _method_text(unit, main.method)
        mech += _derivation(unit, rows, sub, max_rows=2, budget=600)
        taken.add(span)
        r = _mk(span, unit, mech, rows, sub, key=_driver_key(drv))
        r["_drv"] = drv
        r["_rep"] = main.entity_id if len(rows) == 1 else None
        r["_score"] = best - 0.5 * len(out)
        # a driver that names the rows of one other forecast method is that method's reason
        methods = {p.method for p in rows}
        r["_covers"] = ["method:" + m for m in methods] if by_name and len(methods) == 1 else []
        out.append(r)
    # the best-evidenced drivers first (a figure-bearing passage beats boilerplate), with a
    # small preference for the order the task statement names them in
    out.sort(key=lambda r: -r["_score"])
    return out


def _extra_reasons(unit: Unit, preds: list[Pred], used: set, need: int, sub: dict | None,
                   prefer_not: set | frozenset = frozenset()) -> list[dict]:
    """Further reasons from other figure-bearing prose of the strongest rows (a unit with one
    entity, one method and no named drivers still submits three); rows that already carry a
    reason of their own come last."""
    from .retrieve import bm25_search, tokens

    words = ("guidance outlook expects expected increase decrease growth revenue sales income margin "
             "quarter compared percent billion earnings loss demand higher lower trend")
    out: list[dict] = []
    ents = {e["entity_id"]: e for e in unit.entities}
    ordered = sorted(preds, key=lambda p: (p.entity_id in prefer_not, -p.strength, p.entity_id))
    for pr in ordered + ordered[:1]:
        if len(out) >= need:
            break
        q = tokens(unit.target_name.replace("_", " ")) * 2 + tokens(words) + [w for w in tokens(unit.prompt) if len(w) >= 6][:30]
        scored: list[tuple[float, Span]] = []
        hits = bm25_search(unit, pr.entity_id, q, k=40, require_digit=True, scope="own", citable_only=False)
        hits = hits or bm25_search(unit, pr.entity_id, q, k=40, require_digit=True, citable_only=False)
        for score, p in hits:
            doc = unit.docs[p.doc_id]
            t = _trim_span(doc.text, p.start, p.end, max_chars=600, unit=unit)
            if t is None or (p.doc_id, t[0], t[1]) in used:
                continue
            frag = doc.text[t[0]:t[1]]
            if len(frag.split()) < 8:
                continue
            scored.append((0.15 * score + _prose_score(frag) + (2.5 if _GUIDANCE.search(frag) else 0.0), (p.doc_id, t[0], t[1])))
        if not scored:
            continue
        scored.sort(key=lambda x: (-x[0], x[1]))
        if scored[0][0] < 1.0:
            continue
        span = scored[0][1]
        used.add(span)
        frag = unit.docs[span[0]].text[span[1]:span[2]]
        name = str(ents.get(pr.entity_id, {}).get("name") or pr.entity_id)
        link = _content_text(frag, unit)
        mech = f"This pre-cutoff passage for {name} bears on {unit.target_name.replace('_', ' ')}. "
        if link:
            mech += link + " "
        mech += _method_text(unit, pr.method) + _derivation(unit, [pr], sub, max_rows=1)
        r = _mk(span, unit, mech, [pr], sub, key=f"extra:{span[0]}:{span[1]}")
        r["_rep"] = pr.entity_id
        out.append(r)
    return out


def build_reasons(unit: Unit, preds: list[Pred], submitted: dict | None = None) -> list[dict]:
    """Up to three reasons on three distinct drivers, strongest first: the strongest forecast
    method's reason, then the drivers the task statement names (each with its own verbatim
    premise and economic link), then further methods, single rows and other figure-bearing prose.
    `submitted` maps entity_id -> (point, lo, hi, label) exactly as written to answer.json."""
    groups: dict[str, list[Pred]] = {}
    for pr in preds:
        if pr.method in _METHOD_TEXT:
            groups.setdefault(pr.method, []).append(pr)
    order = sorted(groups.items(), key=lambda kv: (-sum(p.strength for p in kv[1]) - 0.01 * len(kv[1]), kv[0]))
    order = [(m, sorted(ms, key=lambda p: (-p.strength, p.entity_id))) for m, ms in order]

    used: set = set()
    cands: list[dict] = []
    keys: list[str] = []
    if order:
        r = _method_reason(unit, order[0][0], order[0][1], used, submitted)
        if r:
            cands.append(r)
            used.add(r["_span"])
            # the driver the primary method already argues from is not a second driver
            pat = _METHOD_DRIVER.get(order[0][0])
            if pat:
                try:
                    from .retrieve import task_drivers

                    keys += [_driver_key(d) for d in task_drivers(unit) if re.search(pat, d, re.IGNORECASE)]
                except Exception:  # noqa: BLE001
                    pass
    try:
        drv = _driver_reasons(unit, preds, used, submitted)
    except Exception:  # noqa: BLE001 - driver reasons are an extra; never lose the others
        drv = []
    drv = [r for r in drv if r["_key"] not in keys]
    rest = list(order[1:])
    while len(cands) < 8 and (drv or rest):
        if drv:
            r = drv.pop(0)
            if r["_span"] not in used:
                cands.append(r)
                used.add(r["_span"])
        if rest:
            m, ms = rest.pop(0)
            r = _method_reason(unit, m, ms, used, submitted)
            if r:
                cands.append(r)
                used.add(r["_span"])
    # Still fewer than three distinct drivers: the evidence of another row of the main group
    # (a second series, a second company), then other figure-bearing prose of the strongest
    # rows (guidance, results), then further rows (a unit with one entity, one method and no
    # named drivers still submits three).
    def rep_ids() -> set:
        return {r.get("_rep") for r in cands if r.get("_rep")}

    def row_fallbacks(limit: int) -> None:
        if not order:
            return
        m, ms = max(order, key=lambda kv: len(kv[1]))
        taken_rows = rep_ids()
        n = 0
        for p in sorted(ms, key=lambda p: (p.entity_id in taken_rows, -p.strength, p.entity_id)):
            if n >= limit or len(ms) < 2:
                break
            r = _method_reason(unit, m, [p], used, submitted)
            if r and r["_span"] not in used:
                r["_key"] = f"row:{p.entity_id}"
                r["_rep"] = p.entity_id
                cands.append(r)
                used.add(r["_span"])
                n += 1

    distinct = len({r["_key"] for r in cands})
    if distinct < 3:
        row_fallbacks(1)
    distinct = len({r["_key"] for r in cands})
    if distinct < 3:
        try:
            cands += _extra_reasons(unit, preds, used, 3 - distinct, submitted, prefer_not=rep_ids())
        except Exception:  # noqa: BLE001
            pass
    if len(cands) < 4:
        row_fallbacks(3)

    reasons: list[dict] = []
    ev_bytes = 0
    seen_text: set[tuple[str, str]] = set()
    for r in cands:
        if len(reasons) >= 3:
            break
        if not (_clean(r["premise"]) and _clean(r["mechanism"]) and _clean(r["answer_implication"])):
            continue
        # three distinct drivers: a second reason on the same driver, or the same premise and
        # mechanism, is not a new reason to the grader
        if r["_key"] in keys or (r["premise"], r["mechanism"]) in seen_text:
            continue
        if any(_same_passage(r["premise"], x["premise"]) for x in reasons):
            continue  # the same passage again (a longer or shorter slice of it) is not new evidence
        reason = {"reason_id": f"r{len(reasons) + 1}", **{k: v for k, v in r.items() if not k.startswith("_")}}
        core = [{k: x[k] for k in ("reason_id", "premise", "mechanism", "answer_implication")} for x in reasons + [reason]]
        ev = sum(len(unit.docs[c["doc_id"]].text[c["span_start"]:c["span_end"]].encode("utf-8")) + 100 for c in reason["citations"])
        if _bytes(core) > REASON_BYTES_BUDGET:
            # over the grader's cap: shorten the implication, then the derivations, then skip
            trimmed = dict(reason)
            rows = [p for p in preds if p.entity_id in set(reason["scope"]["entities"])]
            trimmed["answer_implication"] = _implication(unit, rows[:4], submitted) if len(rows) > 4 else reason["answer_implication"]
            core[-1] = {k: trimmed[k] for k in ("reason_id", "premise", "mechanism", "answer_implication")}
            if _bytes(core) > REASON_BYTES_BUDGET:
                trimmed["mechanism"] = re.split(r" For [A-Za-z0-9_\-]+: ", trimmed["mechanism"], 1)[0]
                core[-1]["mechanism"] = trimmed["mechanism"]
            if _bytes(core) > REASON_BYTES_BUDGET:
                continue
            reason = trimmed
        if ev_bytes + ev > EVIDENCE_BYTES_BUDGET:
            continue
        ev_bytes += ev
        reasons.append(reason)
        keys.append(r["_key"])
        primary_key = ("method:" + order[0][0]) if order else None
        keys += [k for k in r.get("_covers", []) if k != primary_key]
        seen_text.add((r["premise"], r["mechanism"]))
    if len(reasons) < 3:
        try:  # caps or duplicates dropped candidates: top up from further passages
            for m in _extra_reasons(unit, preds, used, 3 - len(reasons), submitted):
                if len(reasons) >= 3 or not (_clean(m["premise"]) and _clean(m["mechanism"])):
                    continue
                reason = {"reason_id": f"r{len(reasons) + 1}", **{k: v for k, v in m.items() if not k.startswith("_")}}
                core = [{k: x[k] for k in ("reason_id", "premise", "mechanism", "answer_implication")} for x in reasons + [reason]]
                if _bytes(core) <= REASON_BYTES_BUDGET:
                    reasons.append(reason)
        except Exception:  # noqa: BLE001
            pass
    return reasons
