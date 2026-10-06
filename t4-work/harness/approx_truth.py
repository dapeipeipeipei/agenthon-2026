"""APPROXIMATE outcomes for 8 of the 11 public practice units, for LOCAL DIAGNOSTICS ONLY.

Where these numbers come from: they are recalled public facts (reported GAAP EPS, bankruptcy
filings, published yields and CPI prints), written down from memory by the assistant on
2026-10-06 and NOT checked against a data source. They may be wrong in detail. The organizers'
real outcome files are not published. Nothing in the agent reads this file; it only feeds the
local scorer so we can see roughly whether a change helps.

Units with no row here (auction bid-to-cover, COT positioning, macro revisions) are not scored
locally because the outcomes are not known with enough confidence.

`naive` is OUR GUESS at each unit's declared naive rule (organizer material, unpublished): carry the
prior value forward (or zero change) with a fixed band; classification naive = a constant
status-quo label. The anchored scores below move with that guess, so read them as relative
(agent vs. baseline under the same assumptions), never as a leaderboard estimate.
"""

TRUTH = {
    "t4-EXAMPLE-eps-beat": {  # Apple FQ2-2024 diluted EPS 1.53 vs 1.50 consensus -> inline
        "AAPL": ("inline", 1.53),
    },
    "t4-credit-event-2023": {  # Chapter 11: BBBY 2023-04, YELL 2023-08, RAD 2023-10, WE 2023-11
        "BBBY": ("credit_event", None), "RAD": ("credit_event", None), "WE": ("credit_event", None),
        "YELL": ("credit_event", None), "BBY": ("no_event", None), "M": ("no_event", None),
        "ODFL": ("no_event", None), "WBA": ("no_event", None),
    },
    "t4-eps-yoy-2023Q2-mixed": {  # June-2023 quarter GAAP diluted EPS
        "AMD": ("down", 0.02), "AMGN": ("down", 2.25), "DOW": ("down", 0.27),
        "HON": ("up", 2.23), "IBM": ("up", 1.72), "TMO": ("down", 2.84),
    },
    "t4-eps-growth-2024Q3-banks": {  # Q3-2024 diluted EPS vs prior_year_q_eps, in percent
        "JPM": (None, (4.37 / 4.33 - 1) * 100), "BAC": (None, (0.81 / 0.90 - 1) * 100),
        "C": (None, (1.51 / 1.63 - 1) * 100), "GS": (None, (8.40 / 5.47 - 1) * 100),
        "MS": (None, (1.88 / 1.38 - 1) * 100), "PNC": (None, (3.49 / 3.60 - 1) * 100),
        "USB": (None, (1.03 / 0.91 - 1) * 100), "WFC": (None, (1.42 / 1.48 - 1) * 100),
    },
    "t4-postearn-20240201-megacap": {  # 2024-02-02 one-day return minus SPY (+1.07%)
        "AAPL": ("negative_reaction", -1.6), "AMZN": ("positive_reaction", 6.8),
        "META": ("positive_reaction", 19.2),
    },
    "t4-fomc-curve-20220728": {  # DGS* 2022-09-20 minus 2022-07-28, bps (approximate)
        "UST2Y": (None, 110.0), "UST3Y": (None, 112.0), "UST5Y": (None, 104.0),
        "UST7Y": (None, 97.0), "UST10Y": (None, 89.0), "UST30Y": (None, 58.0),
    },
    "t4-fomc-curve-20240918": {  # DGS* 2024-11-06 minus 2024-09-19, bps (approximate)
        "UST2Y": (None, 67.0), "UST3Y": (None, 78.0), "UST5Y": (None, 79.0),
        "UST7Y": (None, 77.0), "UST10Y": (None, 71.0), "UST30Y": (None, 56.0),
    },
    "t4-cpicomp-202410-us11": {  # October-2024 CPI first print, SA m/m % (approximate)
        "CPI_ALLITEMS": (None, 0.24), "CPI_CORE": (None, 0.28), "CPI_SHELTER": (None, 0.4),
        "CPI_ENERGY": (None, 0.0), "CPI_GASOLINE": (None, -0.9), "CPI_FOOD": (None, 0.2),
        "CPI_NEWVEH": (None, 0.0), "CPI_USEDCARS": (None, 2.7), "CPI_APPAREL": (None, -1.5),
        "CPI_MEDICAL": (None, 0.3), "CPI_TRANSPSVC": (None, 0.3),
    },
}

# Guessed naive rules: (point source, half-width rule, label)
NAIVE = {
    "t4-EXAMPLE-eps-beat": {"point": "consensus_eps", "hw_rel": 0.10, "label": "inline"},
    "t4-credit-event-2023": {"point": None, "hw_abs": 0.5, "label": "no_event"},
    "t4-eps-yoy-2023Q2-mixed": {"point": "prior_year_q_eps", "hw_rel": 0.30, "label": "up"},
    "t4-eps-growth-2024Q3-banks": {"point": 0.0, "hw_abs": 30.0},
    "t4-postearn-20240201-megacap": {"point": 0.0, "hw_abs": 5.0, "label": "flat"},
    "t4-fomc-curve-20220728": {"point": 0.0, "hw_abs": 50.0},
    "t4-fomc-curve-20240918": {"point": 0.0, "hw_abs": 50.0},
    "t4-cpicomp-202410-us11": {"point": "latest_published_mom_pct", "hw_abs": 0.5},
}
