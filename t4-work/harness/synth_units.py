"""Synthetic Track 4 units for families the public set does NOT contain. Built from scratch
(task.json, card.toml, manifest.json with real digests, corpus/*.json), so each case exercises
the agent on a shape it has never seen rather than on a mutated public unit.

Used by `robustness.py` (which runs the agent on each unit and applies the official checks that
need no outcome: schema, roster, the scorer's claim rules, `check_submitted_reasons`, and the
scorer's own gates via `score_unit(require_outcome=False)`).

Each case is a `SynthCase(name, unit_dir, note, extra)`; `extra(answer) -> str | None` adds a
case-specific assertion (None = fine).
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CARD = """schema_version = "2.0"

[task]
id              = "{tid}"
track           = "analysis"
title           = "{title}"
split           = "public-dev"
family          = "{family}"
target_type     = "{ttype}"
prompt          = "{prompt}"
cutoff_date     = "{cutoff}"
resolution_date = "{resolution}"
adversarial     = false
faithfulness_rubric = "Every claim must cite a passage from a corpus document whose doc_date <= {cutoff}."

[metadata]
author_name              = "synthetic"
author_email             = "synthetic@example.org"
difficulty               = "medium"
category                 = "{family}"
tags                     = ["analysis", "synthetic"]
expert_time_estimate_min = 45.0
junior_time_estimate_min = 120.0

[provenance]
license             = "synthetic"
data_source         = "synthetic"
data_cutoff         = "{cutoff}"
public_release_date = "2026-10-08"
redistributable     = true
manifest            = "manifest.json"

[contamination]
canary_guid = "{canary}"

[scoring]
verifier            = "t4.faithful_analysis"
metric              = "analysis_composite"
admissibility_gates = ["g0_integrity", "g1_schema", "g2_cutoff_resource", "g3_domain_semantics"]

[scoring.params]
faithfulness_threshold = 0.80
interval_level         = 0.90
target_type            = "{ttype}"
composite_weights      = [0.7, 0.3]
tau_citation           = 0.5
nli_judge_ensemble     = ["cross-encoder/nli-deberta-v3-large", "MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli"]

[environment]
cpus    = 16
memory  = "128G"
gpu     = true
network = "restricted"

[agent]
timeout_sec = 600.0

[verifier]
timeout_sec = 600.0

[corpus]
pii_stripped      = true
manifest_required = true

[embargo]
cutoff_field   = "cutoff_date"
doc_date_field = "doc_date"
strict         = true
"""


@dataclass
class SynthCase:
    name: str
    unit: Path
    note: str
    extra: Callable[[dict], str | None] | None = None
    scorer: bool = True
    nli: bool = False  # include in the NLI subset


def _toml_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def write_unit(root: Path, name: str, task: dict, docs: list[dict], labels: dict[str, object], *,
               family: str = "synthetic_family", card: bool = True, task_bytes: bytes | None = None) -> Path:
    """Write a complete unit. `labels[doc_id]` is "shared" or a list of entity_ids (a missing
    key means an unlabelled document). `task_bytes` overrides the serialized task.json (odd
    encodings)."""
    unit = root / name
    if unit.exists():
        shutil.rmtree(unit)
    (unit / "corpus").mkdir(parents=True)
    (unit / "task.json").write_bytes(task_bytes if task_bytes is not None else json.dumps(task, ensure_ascii=False, indent=1).encode("utf-8"))
    for d in docs:
        raw = d.pop("_raw", None)
        p = unit / "corpus" / f"{d['doc_id']}.json"
        p.write_bytes(raw if raw is not None else json.dumps(d, ensure_ascii=False).encode("utf-8"))
    files = []
    for p in sorted((unit / "corpus").glob("*.json")):
        b = p.read_bytes()
        e = {"path": f"corpus/{p.name}", "role": "corpus", "source": "synthetic", "license": "synthetic",
             "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b), "split": "public-dev",
             "cutoff": task.get("cutoff_date", "")[:10], "redistributable": True, "pii_stripped": True}
        lab = labels.get(p.stem)
        if lab == "shared":
            e["shared"] = True
        elif isinstance(lab, list):
            e["entity_ids"] = lab
        files.append(e)
    (unit / "manifest.json").write_text(json.dumps({"manifest_version": "2.0", "unit_id": task["task_id"], "files": files}, indent=1), encoding="utf-8")
    if card:
        tt = task.get("target", {}).get("type") or task.get("target_type") or "regression"
        (unit / "card.toml").write_text(CARD.format(
            tid=task["task_id"], title=_toml_str(task.get("title", name)), family=family, ttype=tt,
            prompt=_toml_str(task.get("prompt", "")[:300]), cutoff=task["cutoff_date"][:10],
            resolution=task.get("resolution_date", task["cutoff_date"])[:10],
            canary=str(uuid.UUID(int=random.Random(name).getrandbits(128)))), encoding="utf-8")
    return unit


def _task(tid: str, target: dict, prompt: str, cutoff: str, resolution: str, entities: list[dict], **extra) -> dict:
    t = {"task_id": tid, "schema_version": "3", "family": extra.pop("family", "synthetic_family"), "target": target,
         "prompt": prompt, "cutoff_date": cutoff, "resolution_date": resolution, "interval_level": 0.9,
         "corpus_manifest": "manifest.json", "entities": entities, "notes": "synthetic unit for robustness checks"}
    t.update(extra)
    return t


def _table(header: list[str], rows: list[list]) -> str:
    out = [" | ".join(header)]
    for r in rows:
        out.append(" | ".join(str(x) for x in r))
    return "\n".join(out)


def _month(i: int, start=(2023, 10)) -> str:
    y, m = start
    m += i
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def _week(i: int, start=(2024, 10, 7)) -> str:
    import datetime as dt

    return (dt.date(*start) + dt.timedelta(days=7 * i)).isoformat()


# --------------------------------------------------------------------------- generic checks


def intervals_ok(ans: dict) -> str | None:
    for r in ans["entity_predictions"]:
        iv = r["interval"]
        p = r.get("point_forecast")
        if not (iv["lo"] <= iv["hi"]) or iv["hi"] - iv["lo"] <= 0:
            return f"{r['entity_id']}: degenerate interval"
        if p is not None and not (iv["lo"] <= p <= iv["hi"]):
            return f"{r['entity_id']}: point outside interval"
    return None


def three_reasons(ans: dict) -> str | None:
    rs = ans.get("submitted_reasons") or []
    if len(rs) != 3:
        return f"{len(rs)} reasons"
    for r in rs:
        for k in ("premise", "mechanism", "answer_implication"):
            if not isinstance(r.get(k), str) or len(r[k]) < 20:
                return f"reason {r.get('reason_id')}: short {k}"
    return None


def methods(ans: dict) -> set[str]:
    return set((ans.get("notes") or {}).get("methods", {}).values())


def labels_in(vocab: list[str]) -> Callable[[dict], str | None]:
    return lambda ans: None if all(r.get("label") in vocab for r in ans["entity_predictions"]) else "label outside vocabulary"


def combine(*checks: Callable[[dict], str | None]) -> Callable[[dict], str | None]:
    def run(ans: dict) -> str | None:
        msgs = [m for m in (c(ans) for c in checks) if m]
        return "; ".join(msgs) if msgs else None

    return run


# --------------------------------------------------------------------------- the cases


def case_rank_emfx(root: Path) -> SynthCase:
    """Ranking target (Spearman) on an EM FX cross-section; shared cross-section table."""
    rng = random.Random(1)
    ccy = [("BRL", "Brazilian real"), ("MXN", "Mexican peso"), ("ZAR", "South African rand"), ("TRY", "Turkish lira"),
           ("INR", "Indian rupee"), ("IDR", "Indonesian rupiah"), ("PLN", "Polish zloty"), ("HUF", "Hungarian forint")]
    rates = {"BRL": 14.25, "MXN": 9.0, "ZAR": 7.5, "TRY": 42.5, "INR": 6.25, "IDR": 5.75, "PLN": 5.75, "HUF": 6.5}
    ents = []
    rets = {}
    for code, nm in ccy:
        drift = rng.uniform(-0.8, 0.8)
        vol = rng.uniform(1.5, 5.0)
        rets[code] = [round(drift + rng.gauss(0, vol), 2) for _ in range(15)]
        ents.append({"entity_id": code, "name": f"{nm} (USD/{code})", "region": "EM", "policy_rate_pct": rates[code],
                     "trailing_1m_return_pct": rets[code][-1], "unit": "pct_return_vs_usd", "corpus_ref": "corpus/"})
    months = [_month(i, (2024, 1)) for i in range(15)]
    table = _table(["month"] + [c for c, _ in ccy], [[months[i]] + [f"{rets[c][i]:+.2f}" for c, _ in ccy] for i in range(15)])
    docs = [
        {"doc_id": "EMFX_MONTHLY_RETURNS_20250331", "doc_date": "2025-03-31", "source": "synthetic",
         "title": "Emerging-market currency monthly spot returns versus the U.S. dollar, 2024-01 to 2025-03",
         "text": "Emerging-market spot returns versus the U.S. dollar, percent per month (positive = the currency appreciated against the dollar). "
                 "Computed from end-of-month fixings; March 2025 is complete as of this document's date.\n\n" + table + "\n\n"
                 "NOTES:\n- TRY: the lira depreciated in 13 of the 15 months shown, with the central bank's policy rate at 42.5 percent.\n"
                 "- BRL: the real's monthly moves were the most volatile of the group, with a standard deviation above 4 percent.\n"},
        {"doc_id": "EM_MACRO_SNAPSHOT_20250331", "doc_date": "2025-03-31", "source": "synthetic",
         "title": "EM macro and positioning snapshot as of 2025-03-31",
         "text": "EM macro snapshot as of the 2025-03-31 close. The dollar index closed the quarter at 104.2, down 3.9 percent from the January high. "
                 "Carry: Brazil's Selic rate stands at 14.25 percent, Mexico's overnight rate at 9.00 percent, South Africa's repo rate at 7.50 percent, "
                 "Turkey's one-week repo rate at 42.50 percent, India's repo rate at 6.25 percent, Indonesia's BI rate at 5.75 percent, Poland's reference rate at 5.75 percent and Hungary's base rate at 6.50 percent. "
                 "Speculative positioning (CFTC) is net long the Mexican peso at 38,000 contracts and net short the Brazilian real at 12,000 contracts. "
                 "Oil (Brent) closed at 74.7 dollars per barrel, down 2.1 percent on the month; copper rose 4.4 percent to 9,850 dollars per tonne.\n"},
    ]
    task = _task("t4-synth-rank-emfx", {"name": "spot_return_pct_rank", "type": "ranking"},
                 "Using ONLY the frozen evidence corpus (monthly EM spot-return table and a macro snapshot as of the 2025-03-31 cutoff), predict the RANKING "
                 "of the eight emerging-market currencies by their spot return versus the U.S. dollar (in percent, positive = the currency appreciates) from the "
                 "2025-03-31 close to the 2025-04-30 close. Rank 1 = largest appreciation. For each currency provide a point_forecast of the spot return in percent "
                 "(the scorer ranks currencies by this value) and a 90% interval around it. Reason from carry (the policy-rate differential), the trailing "
                 "one-month move and mean reversion, and the dollar and commodity backdrop.",
                 "2025-03-31", "2025-04-30", ents, family="emfx_return_ranking")
    unit = write_unit(root, "synth_rank_emfx", task, docs, {d["doc_id"]: "shared" for d in docs}, family="emfx_return_ranking")

    def extra(ans):
        pts = [r["point_forecast"] for r in ans["entity_predictions"]]
        msg = []
        if len(set(pts)) < 2:
            msg.append("constant point forecasts (no ordering expressed)")
        if "rank" in ans["entity_predictions"][0] and sorted(r["rank"] for r in ans["entity_predictions"]) != list(range(1, 9)):
            msg.append("rank not a permutation")
        return "; ".join(msg) or None

    return SynthCase("rank: EM FX cross-section (Spearman)", unit, "ranking target; shared cross-section table; series signal expected",
                     combine(intervals_ok, three_reasons, extra), nli=True)


def case_reg_wide40(root: Path) -> SynthCase:
    """Regression with 40 entities (commodity contracts), one weekly settlement table each."""
    rng = random.Random(2)
    names = ["WTI crude", "Brent crude", "RBOB gasoline", "Heating oil", "Henry Hub natural gas", "Dutch TTF gas", "Gold", "Silver", "Platinum",
             "Palladium", "Copper", "Aluminium", "Zinc", "Nickel", "Lead", "Tin", "Iron ore", "Coking coal", "Thermal coal", "Corn", "Soybeans",
             "Soybean oil", "Soybean meal", "Wheat (SRW)", "Wheat (HRW)", "Oats", "Rough rice", "Cotton", "Sugar No. 11", "Coffee (arabica)",
             "Cocoa", "Orange juice", "Live cattle", "Feeder cattle", "Lean hogs", "Lumber", "Milk Class III", "Ethanol", "Carbon (EUA)", "Uranium (U3O8)"]
    ents, docs, labels = [], [], {}
    # 32 rows: the widest roster whose answer can still fit the reasoning grader's 3,000-byte
    # cap (about 77 bytes of JSON per row before any digit; 40 rows cannot fit at all)
    for i, nm in enumerate(names[:32]):
        eid = f"CMD{i:02d}"
        base = rng.choice([2.5, 25.0, 80.0, 450.0, 2400.0, 9500.0])
        vals = [base]
        for _ in range(25):
            vals.append(max(0.1, vals[-1] * (1 + rng.gauss(0, 0.025))))
        weeks = [_week(k, (2024, 9, 2)) for k in range(26)]
        rows = [[weeks[k], f"{vals[k]:.2f}"] for k in range(26)]
        docs.append({"doc_id": f"SETTLE_{eid}_20250228", "doc_date": "2025-02-28", "source": "synthetic",
                     "title": f"{nm} front-month weekly settlement prices, September 2024 to February 2025",
                     "text": f"{nm} front-month futures contract: weekly settlement price (Friday close), U.S. dollars per contract unit.\n"
                             f"Source: exchange settlement reports (synthetic). The latest settlement shown is {vals[-1]:.2f} on {weeks[-1]}.\n\n"
                             + _table(["week_ending", "settle_usd"], rows) + "\n\n"
                             f"NOTES:\n- {nm}: over the 26 weeks shown the settlement price ranged from {min(vals):.2f} to {max(vals):.2f}.\n"})
        labels[f"SETTLE_{eid}_20250228"] = [eid]
        ents.append({"entity_id": eid, "name": nm, "sector": "commodity", "latest_settle_usd": round(vals[-1], 2),
                     "latest_week_ending": weeks[-1], "unit": "usd", "corpus_ref": "corpus/"})
    task = _task("t4-synth-reg-wide40", {"name": "settle_usd_next", "type": "regression"},
                 "Using ONLY the frozen evidence corpus (each contract's weekly settlement table through the 2025-02-28 cutoff), predict for each of the 32 "
                 "commodity futures contracts in the table its front-month SETTLEMENT PRICE in U.S. dollars on the 2025-03-28 resolution date. "
                 "The latest settlement (latest_settle_usd) is given. For each contract provide a point_forecast in dollars and a 90% interval around it. "
                 "Reason from each contract's own recent level and trend and its volatility.",
                 "2025-02-28", "2025-03-28", ents, family="commodity_settle_level")
    unit = write_unit(root, "synth_reg_wide40", task, docs, labels, family="commodity_settle_level")

    def extra(ans):
        proj = [{"entity_id": r["entity_id"], **{k: r[k] for k in ("label", "point_forecast", "interval") if k in r}} for r in ans["entity_predictions"]]
        n = len(json.dumps(proj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")) - 2
        msg = []
        if n > 3000:
            msg.append(f"answer bytes {n} > 3000")
        if not methods(ans) <= {"series_level", "series_proxy"}:
            msg.append(f"methods {sorted(methods(ans))}")
        return "; ".join(msg) or None

    return SynthCase("regression: 32 commodity contracts (answer-byte cap)", unit, "32 rows, own series tables; must stay under the 3,000-byte answer cap",
                     combine(intervals_ok, three_reasons, extra))


def case_cls_2label_cb(root: Path) -> SynthCase:
    """Two-label vocabulary with semantics the agent has not seen; probability point forecast."""
    ents = [
        {"entity_id": "ECB", "name": "European Central Bank", "next_meeting": "2025-06-05", "current_policy_rate_pct": 2.25, "policy_rate_name": "deposit facility rate", "corpus_ref": "corpus/"},
        {"entity_id": "BOE", "name": "Bank of England", "next_meeting": "2025-06-19", "current_policy_rate_pct": 4.25, "policy_rate_name": "Bank Rate", "corpus_ref": "corpus/"},
        {"entity_id": "BOJ", "name": "Bank of Japan", "next_meeting": "2025-06-17", "current_policy_rate_pct": 0.5, "policy_rate_name": "uncollateralized overnight call rate", "corpus_ref": "corpus/"},
    ]
    docs = [
        {"doc_id": "ECB_DECISION_20250417", "doc_date": "2025-04-17", "source": "synthetic", "title": "ECB monetary policy decision, 17 April 2025",
         "text": "Monetary policy decisions. 17 April 2025.\n\nThe Governing Council today decided to lower the three key ECB interest rates by 25 basis points. "
                 "In particular, the decision to lower the deposit facility rate - the rate through which the Governing Council steers the monetary policy stance - "
                 "is based on its updated assessment of the inflation outlook, the dynamics of underlying inflation and the strength of monetary policy transmission.\n\n"
                 "The disinflation process is well on track. Both headline and core inflation declined in March. Services inflation has also eased markedly over recent months. "
                 "Most measures of underlying inflation suggest that inflation will settle at around the Governing Council's 2% medium-term target on a sustained basis. "
                 "Wage growth is moderating, and profits are partially buffering the impact of still elevated wage growth on inflation.\n\n"
                 "Accordingly, the interest rates on the deposit facility, the main refinancing operations and the marginal lending facility will be decreased to 2.25%, 2.40% and 2.65% respectively, with effect from 23 April 2025.\n\n"
                 "The Governing Council is determined to ensure that inflation stabilises sustainably at its 2% medium-term target. Especially in current conditions of exceptional uncertainty, "
                 "it will follow a data-dependent and meeting-by-meeting approach to determining the appropriate monetary policy stance. The Governing Council is not pre-committing to a particular rate path.\n"},
        {"doc_id": "BOE_MPC_SUMMARY_20250508", "doc_date": "2025-05-08", "source": "synthetic", "title": "Bank of England Monetary Policy Summary, May 2025",
         "text": "Monetary Policy Summary, May 2025.\n\nThe Bank of England's Monetary Policy Committee (MPC) sets monetary policy to meet the 2% inflation target, and in a way that helps to sustain growth and employment. "
                 "At its meeting ending on 7 May 2025, the MPC voted by a majority of 5-4 to reduce Bank Rate by 0.25 percentage points, to 4.25%. "
                 "Two members preferred to reduce Bank Rate by 0.5 percentage points, to 4%. Two members preferred to maintain Bank Rate at 4.5%.\n\n"
                 "Twelve-month CPI inflation fell to 2.6% in March from 2.8% in February. Inflation is expected to rise temporarily to 3.5% in the third quarter of 2025, "
                 "owing to regulated price increases, before falling back toward the target. GDP growth is expected to be weak at around 1% in 2025.\n\n"
                 "Monetary policy will need to continue to remain restrictive for sufficiently long until the risks to inflation returning sustainably to the 2% target in the medium term have dissipated further. "
                 "A gradual and careful approach to the further withdrawal of monetary policy restraint remains appropriate.\n"},
        {"doc_id": "BOJ_STATEMENT_20250501", "doc_date": "2025-05-01", "source": "synthetic", "title": "Bank of Japan Statement on Monetary Policy, May 1, 2025",
         "text": "Statement on Monetary Policy. May 1, 2025. Bank of Japan.\n\n1. At the Monetary Policy Meeting held today, the Policy Board of the Bank of Japan decided, by a unanimous vote, "
                 "to set the following guideline for money market operations for the intermeeting period: The Bank will encourage the uncollateralized overnight call rate to remain at around 0.5 percent.\n\n"
                 "2. Japan's economy has recovered moderately, although some weakness has been seen in part. The year-on-year rate of increase in the consumer price index (CPI, all items less fresh food) "
                 "has been in the range of 3.0-3.5 percent recently. Inflation expectations have risen moderately.\n\n"
                 "3. Given that real interest rates are at significantly low levels, if the outlook for economic activity and prices presented in the April Outlook Report will be realized, "
                 "the Bank will accordingly continue to raise the policy interest rate and adjust the degree of monetary accommodation. Uncertainties surrounding trade policies remain high.\n"},
    ]
    labels = {"ECB_DECISION_20250417": ["ECB"], "BOE_MPC_SUMMARY_20250508": ["BOE"], "BOJ_STATEMENT_20250501": ["BOJ"]}
    vocab = ["ease", "no_ease"]
    task = _task("t4-synth-cls-2label-cb",
                 {"name": "next_meeting_easing", "type": "classification", "labels": vocab,
                  "label_assertions": {"ease": "will lower its key policy rate at its next scheduled meeting",
                                       "no_ease": "will keep its key policy rate unchanged or raise it at its next scheduled meeting"}},
                 "Using ONLY the frozen evidence corpus (each central bank's latest policy statement available on or before the 2025-05-15 cutoff), predict for each central bank "
                 "whether it will EASE (lower its key policy rate) at its next scheduled meeting. For each central bank give a label (ease / no_ease), a point_forecast = your predicted "
                 "PROBABILITY of an easing move (0 to 1), and a 90% interval on that probability. Reason from the stated inflation outlook, the direction of the latest move and the forward guidance.",
                 "2025-05-15", "2025-06-20", ents, family="central_bank_easing")
    unit = write_unit(root, "synth_cls_2label_cb", task, docs, labels, family="central_bank_easing")

    def extra(ans):
        for r in ans["entity_predictions"]:
            p = r["point_forecast"]
            if not (0 <= p <= 1):
                return f"{r['entity_id']}: probability {p} outside [0, 1]"
            if (p >= 0.5) != (r["label"] == "ease"):
                return f"{r['entity_id']}: label {r['label']} inconsistent with probability {p}"
        return None

    return SynthCase("classification: 2-label (ease/no_ease), probability", unit, "unseen 2-label vocabulary; label must agree with the probability",
                     combine(intervals_ok, three_reasons, labels_in(vocab), extra), nli=True)


def case_cls_5label_rating(root: Path) -> SynthCase:
    """Five-label vocabulary (rating actions); no numeric prior column with an anchor word."""
    rng = random.Random(4)
    issuers = [("ACME", "Acme Industrial Holdings", "BBB-", "Negative", 4.2), ("BRAVO", "Bravo Retail Group", "BB+", "Stable", 3.1),
               ("CHARLIE", "Charlie Energy Partners", "B", "Negative", 6.8), ("DELTA", "Delta Telecom S.A.", "BBB", "Stable", 2.6),
               ("ECHO", "Echo Pharmaceuticals", "A-", "Positive", 1.4), ("FOXTROT", "Foxtrot Shipping Ltd.", "B-", "Negative", 7.9)]
    ents, docs, labels = [], [], {}
    for eid, nm, rat, outlook, lev in issuers:
        ents.append({"entity_id": eid, "name": nm, "current_rating": rat, "outlook": outlook, "net_leverage_x": lev, "corpus_ref": "corpus/"})
        did = f"RATING_{eid}_20250310"
        ebitda = rng.randint(150, 900)
        docs.append({"doc_id": did, "doc_date": "2025-03-10", "source": "synthetic", "title": f"Rating action commentary: {nm}",
                     "text": f"Rating Action Commentary. {nm}.\n\nThe agency has affirmed the long-term issuer rating of {nm} at '{rat}'. The outlook is {outlook}.\n\n"
                             f"Key rating drivers. Net leverage (net debt to EBITDA) was {lev:.1f}x at the end of 2024, against EBITDA of {ebitda} million dollars. "
                             f"Free cash flow was {'negative' if lev > 5 else 'positive'} at {rng.randint(10, 120)} million dollars. Liquidity is {'tight' if lev > 6 else 'adequate'}, "
                             f"with {rng.randint(50, 400)} million dollars of cash and an undrawn revolving facility of {rng.randint(100, 500)} million dollars.\n\n"
                             f"Rating sensitivities. A downgrade could result from net leverage sustained above {lev + 0.5:.1f}x or a weakening of liquidity. "
                             f"An upgrade could result from net leverage sustained below {max(0.5, lev - 1.0):.1f}x together with positive free cash flow.\n"})
        labels[did] = [eid]
    vocab = ["upgrade_2plus", "upgrade", "affirm", "downgrade", "downgrade_2plus"]
    task = _task("t4-synth-cls-5label-rating",
                 {"name": "rating_action_12m", "type": "classification", "labels": vocab,
                  "label_assertions": {"upgrade_2plus": "will have its long-term issuer rating raised by two or more notches within 12 months",
                                       "upgrade": "will have its long-term issuer rating raised by exactly one notch within 12 months",
                                       "affirm": "will keep its current long-term issuer rating for 12 months",
                                       "downgrade": "will have its long-term issuer rating lowered by exactly one notch within 12 months",
                                       "downgrade_2plus": "will have its long-term issuer rating lowered by two or more notches within 12 months"}},
                 "Using ONLY the frozen evidence corpus (the latest rating action commentary for each issuer, on or before the 2025-03-31 cutoff), predict for each issuer the "
                 "RATING ACTION over the 12 months after the cutoff: upgrade_2plus, upgrade, affirm, downgrade or downgrade_2plus. Also give a point_forecast = the predicted "
                 "notch change (positive = upgrade, negative = downgrade, 0 = affirmed) and a 90% interval on it. Reason from leverage, free cash flow, liquidity and the stated rating sensitivities.",
                 "2025-03-31", "2026-03-31", ents, family="rating_action")
    unit = write_unit(root, "synth_cls_5label_rating", task, docs, labels, family="rating_action")

    def extra(ans):
        notes = ans.get("notes") or {}
        if notes.get("house") == "on" and notes.get("labels"):
            # a (mock) model asserted labels: only label / point consistency is required
            bad = [(r["entity_id"], r["label"], r["point_forecast"]) for r in ans["entity_predictions"]
                   if (r["label"].startswith("upgrade") and r["point_forecast"] <= 0) or (r["label"].startswith("downgrade") and r["point_forecast"] >= 0)
                   or (r["label"] == "affirm" and abs(r["point_forecast"]) > 0.5)]
            return f"label and point disagree: {bad}" if bad else None
        # with no directional signal the naive-anchored answer is the status quo: the agent must
        # not default to the first label of the vocabulary ("upgrade_2plus")
        bad = [(r["entity_id"], r["label"], r["point_forecast"]) for r in ans["entity_predictions"]
               if r["label"] != "affirm" or abs(r["point_forecast"]) > 0.5]
        return f"not the status quo: {bad}" if bad else None

    return SynthCase("classification: 5-label rating action", unit, "unseen 5-label vocabulary; 'affirm' is the status quo",
                     combine(intervals_ok, three_reasons, labels_in(vocab), extra), nli=True)


def case_spans_only(root: Path) -> SynthCase:
    """Every corpus document is spans-shaped (no flat text): nothing is citable."""
    ents, docs, labels = [], [], {}
    for i, (eid, nm) in enumerate([("REIT_A", "Alpha Lodging Trust"), ("REIT_B", "Beta Hospitality REIT"), ("REIT_C", "Gamma Hotels Inc."), ("REIT_D", "Delta Resorts plc")]):
        occ = 66.0 + 3 * i
        ents.append({"entity_id": eid, "name": nm, "latest_reported_occupancy_pct": occ, "latest_quarter": "2024-12", "unit": "pct", "corpus_ref": "corpus/"})
        did = f"FILING_{eid}_20250220"
        # four differently worded releases (near-identical boilerplate would be deduplicated as
        # one passage by the reasons engine, which is right for boilerplate)
        wording = [
            [f"{nm} reported fourth-quarter results.", f"Occupancy was {occ:.1f}% in the fourth quarter of 2024, compared with {occ - 1.4:.1f}% in the fourth quarter of 2023.",
             f"Average daily rate rose 2.3% to {180 + 7 * i:.2f} dollars and RevPAR increased 4.1% year over year.", "Management expects first-quarter occupancy to be in line with the seasonal pattern of prior years."],
            [f"{nm} today announced results for the quarter ended December 31, 2024.", f"Portfolio occupancy reached {occ:.1f} percent, up 210 basis points from a year earlier, on stronger group bookings.",
             f"RevPAR grew 5.6 percent to {120 + 9 * i:.2f} dollars.", "The trust guided to first-quarter RevPAR growth of 2 to 4 percent, with occupancy expected to track the 2024 seasonal curve."],
            [f"Fourth quarter 2024 highlights for {nm}.", f"Same-store hotel occupancy of {occ:.1f}% versus {occ + 0.6:.1f}% a year ago, as renovation displacement at two resorts weighed on room nights.",
             f"Net income attributable to shareholders was {38 + 5 * i} million dollars; adjusted EBITDA was {95 + 11 * i} million dollars.", "For 2025 the company expects displacement to end by March and occupancy to recover toward its long-run average of about 70 percent."],
            [f"{nm}: quarterly update.", f"Occupancy for the quarter came in at {occ:.1f}%, with leisure transient demand offsetting softer corporate travel.",
             f"Total revenues were {210 + 20 * i:.1f} million dollars, an increase of 3.9% compared with the prior-year quarter.", "Management stated that forward bookings for the first quarter were pacing ahead of last year by 1.5 percentage points of occupancy."],
        ][i]
        spans = [{"text": s} for s in wording]
        docs.append({"doc_id": did, "doc_date": "2025-02-20", "source": "synthetic", "spans": spans})
        labels[did] = [eid]
    task = _task("t4-synth-spans-only", {"name": "occupancy_pct_next_quarter", "type": "regression"},
                 "Using ONLY the frozen evidence corpus (each trust's latest quarterly results release on or before the 2025-02-28 cutoff), predict for each lodging REIT its "
                 "portfolio OCCUPANCY RATE in percent for the first quarter of 2025 (reported after the cutoff). The latest reported occupancy is given. For each row provide a point_forecast "
                 "in percent and a 90% interval. Reason from the latest occupancy, its year-over-year change and management's outlook.",
                 "2025-02-28", "2025-05-05", ents, family="lodging_occupancy")
    unit = write_unit(root, "synth_spans_only", task, docs, labels, family="lodging_occupancy")

    def extra(ans):
        cited = {c["doc_id"] for r in ans["entity_predictions"] for c in r["claims"]}
        if cited != {"task"}:
            return f"cited a spans-only document: {sorted(cited - {'task'})}"
        return None

    return SynthCase("corpus of spans-format documents only", unit, "no document is citable; task-row claims; reasons must still be produced",
                     combine(intervals_ok, three_reasons, extra), nli=True)


def case_unicode_punct(root: Path) -> SynthCase:
    """Odd encodings and names with punctuation: NFD accents, zero-width joiners, CJK, emoji, CRLF, BOM, a lone surrogate."""
    import unicodedata

    vocab = ["outperform", "in_line", "underperform"]
    ents = [
        {"entity_id": "BRK.B", "name": "Berkshire Hathaway Inc. (Class B)", "consensus_revenue_growth_pct": 4.5, "threshold_pct": 2.0, "corpus_ref": "corpus/"},
        {"entity_id": "7203.T", "name": "トヨタ自動車株式会社 (Toyota Motor Corp.)", "consensus_revenue_growth_pct": 3.0, "threshold_pct": 2.0, "corpus_ref": "corpus/"},
        {"entity_id": "SOGN-PA", "name": unicodedata.normalize("NFD", "Société Générale S.A. — \"Groupe\" [Paris]"), "consensus_revenue_growth_pct": -1.5, "threshold_pct": 2.0, "corpus_ref": "corpus/"},
        {"entity_id": "AT_T", "name": "AT&T Inc. / Moody's rated; 3M-style name 50% off", "consensus_revenue_growth_pct": 0.8, "threshold_pct": 2.0, "corpus_ref": "corpus/"},
    ]
    docs = []
    labels = {}
    texts = {
        "BRK.B": "Berkshire Hathaway Inc. \u2014 quarterly report.\r\nRevenues of $93.4\u00a0billion for the quarter, an increase of 5.2% compared with $88.8 billion a year earlier.\r\n"
                 "Operating earnings were $11.2 billion. \U0001F4C8 Insurance underwriting improved; railroad revenue was flat.\r\nManagement expects revenue growth of 4\u20136% for the coming quarter.",
        "7203.T": "トヨタ自動車株式会社 決算短信。売上高は12兆3,456億円（前年同期比 +3.4%）。Consolidated revenue was \u00a512,345.6 billion, up 3.4% year on year; operating income rose 2.1% to \u00a51,234.5 billion.\n"
                  "Guidance: full-year revenue growth of 2\u20133%\u200b with vehicle sales of 10.5\u200bmillion units.",
        "SOGN-PA": unicodedata.normalize("NFD", "Soci\u00e9t\u00e9 G\u00e9n\u00e9rale S.A. r\u00e9sultats trimestriels. Revenues were \u20ac6.8 billion, down 1.1% versus \u20ac6.9 billion a year earlier. ")
                   + "Net income was \u20ac1.1 billion. The group expects revenue growth of about 0 to 2% next quarter.\n\ufeffAppendix: \ud83d\ude00 \udc80 lone surrogate test line with 7.5% figure.",
        "AT_T": "AT&T Inc. results. Revenues totaled $30.2 billion, up 0.9% year over year; Mobility service revenue grew 3.3% and Business Wireline revenue declined 9.9%.\n"
                "Free cash flow was $4.4 billion. The company reiterated 2025 guidance of low-single-digit revenue growth.",
    }
    for eid, t in texts.items():
        did = "DOC_" + eid.replace(".", "_").replace("-", "_") + "_20250131"
        raw = json.dumps({"doc_id": did, "doc_date": "2025-01-31", "source": "synthetic", "text": t}, ensure_ascii=False).encode("utf-8", "surrogatepass")
        if eid == "SOGN-PA":
            # ASCII-escaped JSON with a lone surrogate escape (a BOM on a corpus file is refused
            # by the scorer as an organizer fault, so it cannot occur in a valid unit)
            raw = json.dumps({"doc_id": did, "doc_date": "2025-01-31", "source": "synthetic", "text": t}, ensure_ascii=True).encode("ascii")
        docs.append({"doc_id": did, "_raw": raw})
        labels[did] = [eid]
    task = _task("t4-synth-unicode-punct",
                 {"name": "revenue_growth_vs_consensus", "type": "classification", "labels": vocab,
                  "label_assertions": {"outperform": "will report year-over-year revenue growth more than 2 percentage points above consensus",
                                       "in_line": "will report year-over-year revenue growth within 2 percentage points of consensus",
                                       "underperform": "will report year-over-year revenue growth more than 2 percentage points below consensus"}},
                 "Using ONLY the frozen evidence corpus (each company's latest results release on or before the 2025-02-14 cutoff), predict for each company whether its next-quarter "
                 "year-over-year REVENUE GROWTH (percent) will be above, in line with, or below the consensus (threshold: 2 percentage points). Also give a point_forecast of the revenue growth "
                 "in percent and a 90% interval. Reason from the latest reported growth and management's guidance.",
                 "2025-02-14", "2025-05-15", ents, family="revenue_vs_consensus")
    # CRLF task.json (a BOM on task.json is refused by the scorer as an organizer fault, so it
    # cannot occur in a valid unit; one corpus document carries a BOM instead)
    task_bytes = json.dumps(task, ensure_ascii=False, indent=1).encode("utf-8").replace(b"\n", b"\r\n")
    unit = write_unit(root, "synth_unicode_punct", task, docs, labels, family="revenue_vs_consensus", task_bytes=task_bytes)

    return SynthCase("unicode / punctuation names, odd encodings (BOM, CRLF, NFD, lone surrogate)", unit,
                     "names with punctuation and CJK; NFD text; a BOM document and a CRLF task; a lone surrogate in a document",
                     combine(intervals_ok, three_reasons, labels_in(vocab)), nli=True)


def case_prose_only(root: Path) -> SynthCase:
    """No history table anywhere: prose filings only, level target with a 'latest' column."""
    rng = random.Random(7)
    ents, docs, labels = [], [], {}
    for i, (eid, nm) in enumerate([("HST", "Host Hotels & Resorts"), ("PK", "Park Hotels & Resorts"), ("RHP", "Ryman Hospitality Properties"), ("APLE", "Apple Hospitality REIT"), ("SHO", "Sunstone Hotel Investors")]):
        occ = round(62 + 4 * i + rng.uniform(-1, 1), 1)
        ents.append({"entity_id": eid, "name": nm, "latest_reported_occupancy_pct": occ, "latest_quarter": "three months ended 2024-12-31", "unit": "pct", "corpus_ref": "corpus/"})
        did = f"EDGAR_{eid}_10K_20250225"
        docs.append({"doc_id": did, "doc_date": "2025-02-25", "source": "synthetic", "form_type": "10-K", "title": f"{nm} annual report excerpt",
                     "text": f"{nm}. Annual Report on Form 10-K, fiscal 2024. Management's discussion and analysis.\n\n"
                             f"Comparable hotel occupancy was {occ:.1f}% in the fourth quarter of 2024, compared with {occ - rng.uniform(0.5, 2.5):.1f}% in the fourth quarter of 2023. "
                             f"Average daily rate increased {rng.uniform(1, 4):.1f}% to ${rng.uniform(200, 340):.2f}, and comparable RevPAR increased {rng.uniform(2, 6):.1f}% to ${rng.uniform(140, 260):.2f}. "
                             f"Total revenues were ${rng.uniform(1.0, 1.6):.2f} billion for the year, an increase of {rng.uniform(2, 8):.1f}% compared with 2023.\n\n"
                             f"Outlook. For the first quarter of 2025 we expect comparable occupancy to follow the usual seasonal pattern, with group demand {rng.choice(['strong', 'solid', 'softer'])} "
                             f"and transient demand {rng.choice(['steady', 'improving', 'mixed'])}. We expect full-year comparable RevPAR growth of {rng.uniform(0.5, 3.0):.1f}% to {rng.uniform(3.0, 5.0):.1f}%.\n\n"
                             f"Liquidity. We had ${rng.uniform(0.3, 1.2):.2f} billion of cash and cash equivalents and ${rng.uniform(0.5, 1.5):.2f} billion available under our credit facility at December 31, 2024.\n"})
        labels[did] = [eid]
    task = _task("t4-synth-prose-only", {"name": "occupancy_pct_next_quarter", "type": "regression"},
                 "Using ONLY the frozen evidence corpus (each company's annual report excerpt on or before the 2025-02-28 cutoff), predict for each lodging REIT its comparable hotel OCCUPANCY "
                 "in percent for the first quarter of 2025 (reported after the cutoff). The latest reported quarterly occupancy is given. For each row provide a point_forecast in percent and a 90% interval. "
                 "Reason from the latest reported occupancy, its year-over-year change and management's stated outlook.",
                 "2025-02-28", "2025-05-05", ents, family="lodging_occupancy")
    unit = write_unit(root, "synth_prose_only", task, docs, labels, family="lodging_occupancy")

    def extra(ans):
        rows = {r["entity_id"]: r for r in ans["entity_predictions"]}
        for e in ents:
            p = rows[e["entity_id"]]["point_forecast"]
            if abs(p - e["latest_reported_occupancy_pct"]) > 10:
                return f"{e['entity_id']}: point {p} far from the latest {e['latest_reported_occupancy_pct']}"
        return None

    return SynthCase("regression: prose-only corpus, no history tables", unit, "carry-forward of the latest column with prose-grounded reasons",
                     combine(intervals_ok, three_reasons, extra), nli=True)


def case_extra_fields(root: Path) -> SynthCase:
    """task.json with extra fields, nested values, null/bool columns, timestamps with a time part,
    a doc whose doc_id field disagrees with its filename, and a date with a time suffix."""
    rng = random.Random(8)
    ents, docs, labels = [], [], {}
    for i, (eid, nm) in enumerate([("DAL", "Delta Air Lines"), ("UAL", "United Airlines Holdings"), ("AAL", "American Airlines Group"), ("LUV", "Southwest Airlines")]):
        lf = round(80 + 3 * i + rng.uniform(-1, 1), 1)
        ents.append({"entity_id": eid, "name": nm, "latest_load_factor_pct": lf, "latest_month": "2025-01", "unit": "pct", "notes": None, "active": True,
                     "tags": ["airline", "us"], "meta": {"hub": "ATL" if i == 0 else "ORD", "fleet": 900 + 50 * i}, "corpus_ref": "corpus/"})
        did = f"TRAFFIC_{eid}_20250210"
        months = [_month(k, (2024, 1)) for k in range(13)]
        vals = [round(lf + rng.gauss(0, 2.0), 1) for _ in range(13)]
        vals[-1] = lf
        docs.append({"doc_id": did if i else did + "_mismatch", "doc_date": "2025-02-10", "source": "synthetic", "title": f"{nm} monthly traffic release",
                     "text": f"{nm} monthly traffic results.\n\nSystem load factor, percent, by month:\n\n" + _table(["month", "load_factor_pct"], [[months[k], f"{vals[k]:.1f}"] for k in range(13)])
                             + f"\n\nJanuary 2025 load factor was {lf:.1f} percent, {'up' if vals[-1] >= vals[-2] else 'down'} from {vals[-2]:.1f} percent in December.\n",
                     "_raw": None})
        docs[-1]["_raw"] = json.dumps({k: v for k, v in docs[-1].items() if k != "_raw"}, ensure_ascii=False).encode("utf-8")
        docs[-1]["doc_id"] = did  # file name wins; the first document's doc_id field disagrees
        labels[did] = [eid]
    # (cutoff_date / doc_date with a time part are refused by the scorer as organizer faults, so
    # they cannot occur in a valid unit; the extra fields and odd column shapes can)
    task = _task("t4-synth-extra-fields", {"name": "load_factor_pct_next_month", "type": "regression", "unit": "pct", "direction": "higher_is_better", "extra_block": {"a": 1}},
                 "Using ONLY the frozen evidence corpus (each airline's monthly traffic releases through the 2025-02-14 cutoff), predict for each airline its system LOAD FACTOR in percent for "
                 "February 2025 (reported after the cutoff). The latest reported month is given. For each airline provide a point_forecast in percent and a 90% interval. Reason from each airline's own recent load-factor history.",
                 "2025-02-14", "2025-03-12", ents, family="airline_load_factor",
                 schema_version="4", hints={"use": "tables"}, version=2, reviewed=True, extra_list=[1, 2, 3])
    unit = write_unit(root, "synth_extra_fields", task, docs, labels, family="airline_load_factor")

    def extra(ans):
        if "series_level" not in methods(ans):
            return f"methods {sorted(methods(ans))}"
        # the document whose inner doc_id disagrees with its file name is never cited
        if any(c["doc_id"] == "TRAFFIC_DAL_20250210" for r in ans["entity_predictions"] for c in r["claims"]):
            return "cited a document whose doc_id field disagrees with its file name"
        for r in ans.get("submitted_reasons") or []:
            if any(c["doc_id"] == "TRAFFIC_DAL_20250210" for c in r.get("citations") or []):
                return "reason cites a document whose doc_id field disagrees with its file name"
        return None

    return SynthCase("task.json with extra fields / nested columns / doc_id mismatch", unit, "unknown fields and shapes must be ignored, not fatal",
                     combine(intervals_ok, three_reasons, extra))


def case_big_corpus(root: Path) -> SynthCase:
    """200 documents (150 entity-labelled, 50 shared), ~20 KB each."""
    rng = random.Random(9)
    ents, docs, labels = [], [], {}
    para = ("The company reported quarterly revenue of {rev:.1f} million dollars, {dir} {pct:.1f} percent from the prior-year quarter, and operating income of {oi:.1f} million dollars. "
            "Management cited {reason} as the main driver and reiterated its outlook for the year. Capital expenditure was {capex:.1f} million dollars and cash on hand stood at {cash:.1f} million dollars. ")
    for i in range(10):
        eid = f"CO{i:02d}"
        nm = f"Company {chr(65 + i)} Corp."
        base = rng.uniform(200, 900)
        ents.append({"entity_id": eid, "name": nm, "prior_year_q_revenue_musd": round(base, 1), "sector": "industrials", "corpus_ref": "corpus/"})
        for j in range(15):
            did = f"EDGAR_{eid}_DOC{j:02d}_2024{(j % 12) + 1:02d}15"
            body = "".join(para.format(rev=base * (1 + rng.gauss(0.03, 0.05)), dir=rng.choice(["up", "down"]), pct=rng.uniform(0.5, 12), oi=base * rng.uniform(0.05, 0.2),
                                       reason=rng.choice(["pricing", "volume growth", "cost control", "mix", "currency"]), capex=rng.uniform(5, 60), cash=rng.uniform(50, 600)) for _ in range(70))
            docs.append({"doc_id": did, "doc_date": f"2024-{(j % 12) + 1:02d}-15", "source": "synthetic", "title": f"{nm} filing {j}", "text": f"{nm}. Quarterly report excerpt {j}.\n\n" + body})
            labels[did] = [eid]
    for j in range(50):
        did = f"NEWS_MARKET_2024{(j % 12) + 1:02d}{(j % 28) + 1:02d}_{j:02d}"
        body = "".join(f"Industrial production rose {rng.uniform(0.1, 1.5):.1f} percent in the month and the purchasing managers index printed {rng.uniform(47, 56):.1f}. " for _ in range(120))
        docs.append({"doc_id": did, "doc_date": f"2024-{(j % 12) + 1:02d}-{(j % 28) + 1:02d}", "source": "synthetic", "title": f"Market note {j}", "text": "Market note.\n\n" + body})
        labels[did] = "shared"
    task = _task("t4-synth-big-corpus", {"name": "revenue_yoy_growth_pct", "type": "regression"},
                 "Using ONLY the frozen evidence corpus (each company's filings and market notes dated on or before the 2024-12-31 cutoff), predict for each company its year-over-year "
                 "REVENUE GROWTH in percent for the quarter ending 2025-03-31 (reported after the cutoff). prior_year_q_revenue_musd is given. For each company provide a point_forecast in percent "
                 "and a 90% interval. Reason from revenue trends, pricing and volume commentary and the industrial backdrop.",
                 "2024-12-31", "2025-05-15", ents, family="revenue_growth")
    unit = write_unit(root, "synth_big_corpus", task, docs, labels, family="revenue_growth")
    return SynthCase("200-document corpus (time)", unit, "150 entity documents + 50 shared, ~20 KB each", combine(intervals_ok, three_reasons))


def case_no_prior_column(root: Path) -> SynthCase:
    """Level target with NO numeric column in the task rows; history only in the corpus, and one
    row whose document carries no table at all."""
    rng = random.Random(10)
    ents, docs, labels = [], [], {}
    ports = [("PORT_LA", "Port of Los Angeles"), ("PORT_LB", "Port of Long Beach"), ("PORT_NYNJ", "Port of New York and New Jersey"), ("PORT_SAV", "Port of Savannah"), ("PORT_HOU", "Port of Houston")]
    for i, (eid, nm) in enumerate(ports):
        ents.append({"entity_id": eid, "name": nm, "coast": "west" if i < 2 else "east_gulf", "corpus_ref": "corpus/"})
        did = f"PORT_STATS_{eid}_20250115"
        months = [_month(k, (2024, 1)) for k in range(12)]
        base = rng.uniform(300, 900)
        vals = [round(base * (1 + rng.gauss(0, 0.06)), 1) for _ in range(12)]
        if i == 4:
            text = f"{nm} monthly container statistics.\n\nThe port handled {vals[-1]:.1f} thousand TEU in December 2024, {rng.uniform(2, 9):.1f} percent more than a year earlier. Loaded imports grew while empties fell.\n"
        else:
            text = f"{nm} monthly container statistics.\n\n" + _table(["month", "throughput_teu_thousands"], [[months[k], f"{vals[k]:.1f}"] for k in range(12)]) + \
                   f"\n\nNOTES:\n- {nm}: December 2024 throughput was {vals[-1]:.1f} thousand TEU.\n"
        docs.append({"doc_id": did, "doc_date": "2025-01-15", "source": "synthetic", "title": f"{nm} container statistics", "text": text})
        labels[did] = [eid]
    task = _task("t4-synth-no-prior", {"name": "throughput_teu_thousands_next_month", "type": "regression"},
                 "Using ONLY the frozen evidence corpus (each port's monthly container statistics through the 2025-01-31 cutoff), predict for each port its container THROUGHPUT in "
                 "thousands of TEU for January 2025 (reported after the cutoff). For each port provide a point_forecast in thousands of TEU and a 90% interval. Reason from each port's own monthly history.",
                 "2025-01-31", "2025-02-20", ents, family="port_throughput")
    unit = write_unit(root, "synth_no_prior", task, docs, labels, family="port_throughput")

    def extra(ans):
        rows = {r["entity_id"]: r for r in ans["entity_predictions"]}
        m = (ans.get("notes") or {}).get("methods", {})
        if any(m.get(e) not in ("series_level",) for e in ("PORT_LA", "PORT_LB", "PORT_NYNJ", "PORT_SAV")):
            return f"tabled rows not on the series path: {m}"
        if rows["PORT_HOU"]["point_forecast"] == 0:
            return "row without a table or a prior column predicted exactly 0 for a level target"
        return None

    return SynthCase("regression: no prior column in the rows", unit, "level target; anchors only in the corpus; one row with no table", combine(intervals_ok, three_reasons, extra), nli=True)


def _curve_unit(root: Path, name: str, tid: str, ccy: str, bank: str, decision_doc: dict, snapshot_intro: str, yields: dict[str, float],
                target_name: str, bond: str) -> Path:
    ents = []
    for eid, m in [("2Y", 2), ("5Y", 5), ("10Y", 10), ("30Y", 30)]:
        ents.append({"entity_id": f"{bond}{eid}", "name": f"{m}-year {bond} benchmark yield", "maturity_years": m, "start_yield_pct": yields[eid], "as_of": "2025-05-30", "unit": "bps_change", "corpus_ref": "corpus/"})
    days = ["2025-05-19", "2025-05-20", "2025-05-21", "2025-05-22", "2025-05-23", "2025-05-26", "2025-05-27", "2025-05-28", "2025-05-29", "2025-05-30"]
    rng = random.Random(tid)
    rows = []
    for d in days:
        rows.append([d] + [f"{yields[k] + rng.gauss(0, 0.04):.2f}" for k in ("2Y", "5Y", "10Y", "30Y")])
    rows[-1] = [days[-1]] + [f"{yields[k]:.2f}" for k in ("2Y", "5Y", "10Y", "30Y")]
    snap = {"doc_id": f"RATES_SNAPSHOT_{ccy}_20250530", "doc_date": "2025-05-30", "source": "synthetic", "title": f"{bond} yields and policy snapshot as of 2025-05-30",
            "text": snapshot_intro + "\n\n" + f"{bond} benchmark yields, percent, recent closes:\n" + _table(["date", "2Y", "5Y", "10Y", "30Y"], rows) + "\n"}
    task = _task(tid, {"name": target_name, "type": "regression"},
                 f"Using ONLY the frozen evidence corpus (the latest {bank} policy decision and a rates snapshot as of the 2025-05-30 cutoff), predict for each {bond} maturity in the table the CHANGE "
                 f"in its benchmark yield, in basis points, from the 2025-05-30 cutoff close to the 2025-07-15 resolution close. Each maturity's starting yield (start_yield_pct) is given. "
                 "For each maturity provide a point_forecast of the yield change in bps and a 90% interval around it. Reason from the policy stance set at the latest meeting and the direction it signals, "
                 "and from each maturity's position on the curve (front-end yields are most sensitive to the policy path).",
                 "2025-05-30", "2025-07-15", ents, family="sovereign_curve_cross_section")
    return write_unit(root, name, task, [decision_doc, snap], {decision_doc["doc_id"]: "shared", snap["doc_id"]: "shared"}, family="sovereign_curve_cross_section")


def case_ecb_bund(root: Path) -> SynthCase:
    ecb = {"doc_id": "ECB_DECISION_20250417", "doc_date": "2025-04-17", "source": "synthetic", "title": "ECB monetary policy decisions, 17 April 2025",
           "text": "Monetary policy decisions. 17 April 2025.\n\nThe Governing Council today decided to lower the three key ECB interest rates by 25 basis points. In particular, the decision to lower the deposit facility rate - "
                   "the rate through which the Governing Council steers the monetary policy stance - is based on its updated assessment of the inflation outlook, the dynamics of underlying inflation and the strength of monetary policy transmission.\n\n"
                   "The disinflation process is well on track. Both headline and core inflation declined in March. Services inflation has also eased markedly over recent months.\n\n"
                   "Accordingly, the interest rates on the deposit facility, the main refinancing operations and the marginal lending facility will be decreased to 2.25%, 2.40% and 2.65% respectively, with effect from 23 April 2025.\n\n"
                   "The Governing Council is determined to ensure that inflation stabilises sustainably at its 2% medium-term target. It will follow a data-dependent and meeting-by-meeting approach and is not pre-committing to a particular rate path.\n"}
    intro = ("Euro area rates snapshot as of the 2025-05-30 close. On 2025-04-17 the ECB Governing Council lowered the deposit facility rate by 25 basis points to 2.25 percent, the seventh cut of the cycle, "
             "citing a disinflation process that is well on track. Markets price about two further 25 basis point reductions by year-end. The 2-year Bund yield closed at 1.78 percent, well below the deposit facility rate, "
             "and the 2s10s Bund curve is positive at 76 basis points. The next Governing Council monetary policy meeting is on 2025-06-05, inside the window.")
    unit = _curve_unit(root, "synth_ecb_bund", "t4-synth-ecb-bund", "EUR", "ECB", ecb, intro, {"2Y": 1.78, "5Y": 2.05, "10Y": 2.54, "30Y": 3.02}, "bund_yield_change_bps", "Bund")

    def extra(ans):
        m = methods(ans)
        pts = {r["entity_id"]: r["point_forecast"] for r in ans["entity_predictions"]}
        if "policy_path" not in m:
            return f"no policy-path anchor from the ECB decision (methods {sorted(m)}, points {pts})"
        if not (abs(pts["Bund2Y"]) >= abs(pts["Bund30Y"])):
            return "front end should move at least as much as the long end"
        return None

    return SynthCase("policy path: ECB decision + Bund curve", unit, "non-US policy statement; deposit facility rate anchor", combine(intervals_ok, three_reasons, extra), nli=True)


def case_boe_gilt(root: Path) -> SynthCase:
    boe = {"doc_id": "BOE_MPC_SUMMARY_20250508", "doc_date": "2025-05-08", "source": "synthetic", "title": "Bank of England Monetary Policy Summary, May 2025",
           "text": "Monetary Policy Summary, May 2025.\n\nThe Bank of England's Monetary Policy Committee (MPC) sets monetary policy to meet the 2% inflation target. At its meeting ending on 7 May 2025, the MPC voted by a majority of 5-4 to reduce Bank Rate by 0.25 percentage points, to 4.25%. "
                   "Two members preferred to reduce Bank Rate by 0.5 percentage points, to 4%. Two members preferred to maintain Bank Rate at 4.5%.\n\n"
                   "Twelve-month CPI inflation fell to 2.6% in March from 2.8% in February. Inflation is expected to rise temporarily to 3.5% in the third quarter of 2025 before falling back toward the target.\n\n"
                   "A gradual and careful approach to the further withdrawal of monetary policy restraint remains appropriate. The Committee will continue to monitor closely the risks of inflation persistence.\n"}
    intro = ("UK rates snapshot as of the 2025-05-30 close. On 2025-05-08 the Monetary Policy Committee reduced Bank Rate by 0.25 percentage points to 4.25 percent on a 5-4 vote, the fourth cut of the cycle. "
             "Gilt yields rose over the month as April CPI inflation printed 3.5 percent. The 2-year gilt yield closed at 4.02 percent and the 10-year at 4.65 percent. The next MPC decision is on 2025-06-19.")
    unit = _curve_unit(root, "synth_boe_gilt", "t4-synth-boe-gilt", "GBP", "Bank of England", boe, intro, {"2Y": 4.02, "5Y": 4.12, "10Y": 4.65, "30Y": 5.3}, "gilt_yield_change_bps", "Gilt")
    return SynthCase("policy path: BoE decision + gilt curve", unit, "Bank Rate anchor with a percentage-point step",
                     combine(intervals_ok, three_reasons, lambda a: None if "policy_path" in methods(a) else f"no policy-path anchor (methods {sorted(methods(a))})"))


def case_boj_jgb(root: Path) -> SynthCase:
    boj = {"doc_id": "BOJ_STATEMENT_20250501", "doc_date": "2025-05-01", "source": "synthetic", "title": "Bank of Japan Statement on Monetary Policy, May 1, 2025",
           "text": "Statement on Monetary Policy. May 1, 2025. Bank of Japan.\n\n1. At the Monetary Policy Meeting held today, the Policy Board of the Bank of Japan decided, by a unanimous vote, to set the following guideline for money market operations for the intermeeting period: "
                   "The Bank will encourage the uncollateralized overnight call rate to remain at around 0.5 percent.\n\n"
                   "2. Japan's economy has recovered moderately, although some weakness has been seen in part. The year-on-year rate of increase in the CPI (all items less fresh food) has been in the range of 3.0-3.5 percent.\n\n"
                   "3. Given that real interest rates are at significantly low levels, if the outlook presented in the April Outlook Report will be realized, the Bank will accordingly continue to raise the policy interest rate and adjust the degree of monetary accommodation.\n"}
    intro = ("Japan rates snapshot as of the 2025-05-30 close. On 2025-05-01 the Bank of Japan kept the uncollateralized overnight call rate at around 0.5 percent by a unanimous vote, after raising it by 25 basis points in January, "
             "and repeated that it will continue to raise the policy interest rate if its outlook is realized. Super-long JGB yields rose sharply in May on weak auction demand; the 30-year JGB yield closed at 2.92 percent. The next meeting is on 2025-06-17.")
    unit = _curve_unit(root, "synth_boj_jgb", "t4-synth-boj-jgb", "JPY", "Bank of Japan", boj, intro, {"2Y": 0.74, "5Y": 1.0, "10Y": 1.5, "30Y": 2.92}, "jgb_yield_change_bps", "JGB")
    return SynthCase("policy path: BoJ hold at ~0.5% + JGB curve", unit, "'remain at around X percent' wording (hold); zero-rate band floor",
                     combine(intervals_ok, three_reasons, lambda a: None if "policy_path" in methods(a) else f"no policy-path anchor (methods {sorted(methods(a))})"))


def case_missing_entity_docs(root: Path) -> SynthCase:
    """Classification with known semantics but a roster where one entity has no document at all,
    one only an `entity_ids: []` document, and one only another entity's document."""
    vocab = ["raise", "maintain", "lower"]
    ents = [{"entity_id": e, "name": n, "prior_guidance_eps": g, "corpus_ref": "corpus/"} for e, n, g in
            [("NVDA", "NVIDIA Corporation", 0.85), ("INTC", "Intel Corporation", 0.12), ("AMD", "Advanced Micro Devices", 0.62), ("MU", "Micron Technology", 1.4)]]
    docs = [
        {"doc_id": "EDGAR_NVDA_8K_20250226", "doc_date": "2025-02-26", "source": "synthetic", "title": "NVIDIA results",
         "text": "NVIDIA Corporation reported record quarterly revenue of $39.3 billion, up 78% from a year ago. GAAP diluted earnings per share were $0.89, up 82% from a year ago. "
                 "Outlook for the first quarter of fiscal 2026: revenue is expected to be $43.0 billion, plus or minus 2%. GAAP gross margins are expected to be 70.6%.\n"},
        {"doc_id": "PEER_TSM_20250116", "doc_date": "2025-01-16", "source": "synthetic", "title": "Peer results (off roster)",
         "text": "Taiwan Semiconductor Manufacturing reported fourth-quarter revenue of $26.9 billion, up 37% year over year, and guided first-quarter revenue of $25.0 to $25.8 billion.\n"},
        {"doc_id": "EDGAR_MU_8K_20250320", "doc_date": "2025-03-20", "source": "synthetic", "title": "Micron results",
         "text": "Micron Technology reported revenue of $8.05 billion and GAAP diluted earnings per share of $1.41 for the second quarter of fiscal 2025. "
                 "Guidance for the third quarter: revenue of $8.80 billion plus or minus $200 million and GAAP diluted EPS of $1.37 plus or minus $0.10.\n"},
    ]
    labels = {"EDGAR_NVDA_8K_20250226": ["NVDA"], "PEER_TSM_20250116": [], "EDGAR_MU_8K_20250320": ["MU"]}
    task = _task("t4-synth-missing-docs",
                 {"name": "guidance_action", "type": "classification", "labels": vocab,
                  "label_assertions": {"raise": "will raise its quarterly EPS guidance at its next earnings release", "maintain": "will keep its quarterly EPS guidance unchanged at its next earnings release",
                                       "lower": "will lower its quarterly EPS guidance at its next earnings release"}},
                 "Using ONLY the frozen evidence corpus (filings on or before the 2025-03-31 cutoff), predict for each company whether it will RAISE, MAINTAIN or LOWER its EPS guidance at its next earnings release. "
                 "Also give a point_forecast of the guided EPS in dollars and a 90% interval. Reason from the latest results and outlook statements.",
                 "2025-03-31", "2025-06-01", ents, family="guidance_action")
    unit = write_unit(root, "synth_missing_docs", task, docs, labels, family="guidance_action")

    def extra(ans):
        for r in ans["entity_predictions"]:
            if r["entity_id"] in ("INTC", "AMD") and any(c["doc_id"] != "task" for c in r["claims"]):
                return f"{r['entity_id']} cited a document not labelled for it"
        return None

    return SynthCase("classification: rows without documents / off-roster documents", unit, "task-row claims only for document-less rows",
                     combine(intervals_ok, three_reasons, labels_in(vocab), extra), nli=True)


ALL_CASES = [case_rank_emfx, case_reg_wide40, case_cls_2label_cb, case_cls_5label_rating, case_spans_only, case_unicode_punct,
             case_prose_only, case_extra_fields, case_big_corpus, case_no_prior_column, case_ecb_bund, case_boe_gilt, case_boj_jgb,
             case_missing_entity_docs]


def build_all(root: Path) -> list[SynthCase]:
    root.mkdir(parents=True, exist_ok=True)
    return [f(root) for f in ALL_CASES]


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "out" / "_synth"
    for c in build_all(out):
        print(c.name, "->", c.unit)
