"""Asset-id -> stress-direction table for the v3 event layer (no unit ids, no titles: asset ids only).

`stress_direction(asset, inflation_dominated)` returns +1 / -1 / 0 = the sign the target quantity
moves in when a risk-off / flight-to-quality shock hits, in the PANEL'S OWN quote convention:

  * factors (daily returns)      MKT HML SMB MOM BAB -> -1 (risk assets fall); QMJ -> +1 (quality is
                                 defensive and historically rallies in stress - judgment call)
  * FX quoted USD per currency   EUR GBP AUD NZD -> -1 (high-beta currency falls vs USD)
  * FX quoted currency per USD   CAD NOK SEK DKK + EM codes -> +1 (USD strengthens = quote rises)
  * funding currencies per USD   JPY CHF -> -1 (USD/JPY and USD/CHF FALL: carry unwinds)
  * UST yields                   -> -1 (flight to quality) unless the corpus is inflation/hike
                                 dominated, in which case +1 (hot-print / hiking shock)
  * macro monthly                NFP -> -1, UNRATE -> +1, price indices -> 0
  * anything else                -> 0 (symmetric)

Quote conventions were checked against the shipped g10_fx_daily panel (H.10 native): EUR 1.11,
GBP 1.24, AUD 0.62, NZD 0.61 are USD-per-unit; JPY 107, CHF 0.95, CAD 1.40, NOK 10.1, SEK 9.8,
DKK 6.75 are units-per-USD. The EM transfer panel (CNY 6.2-8.4) is units-per-USD as well.
"""

from __future__ import annotations

import re

#: (compiled pattern on the UPPER-CASED asset id, direction, reason). First match wins.
#: Ids are matched EXACTLY (anchored patterns): an id that is not in this table gets 0 = symmetric,
#: so a sealed card with a new asset id never receives a skew it was not calibrated for.
#: The bare ISO codes assume the H.10 quote convention of the shipped g10_fx_daily / em_transfer
#: panels (checked, see above). The six-letter pair spellings (EURUSD = USD per EUR, USDJPY = JPY
#: per USD) carry their own convention in the name, so they are mapped as well (2026-10-08).
_RISK_CCY = "EUR|GBP|AUD|NZD"
_FUND_CCY = "JPY|CHF"
_USD_LEG_CCY = "CAD|NOK|SEK|DKK"
_EM_CCY = ("CNY|CNH|BRL|INR|MXN|ZAR|TRY|KRW|IDR|RUB|PLN|HUF|CZK|CLP|COP|PEN|PHP|THB|MYR|"
           "TWD|SGD|HKD|ILS|ARS|EGP|NGN|VND|RON|SAR|AED|QAR")
_RULES: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"^(MKT|MKTRF|MKT_RF|MKT-RF|HML|SMB|MOM|UMD|BAB|RMW|CMA)$"), -1, "risk factor falls in stress"),
    (re.compile(r"^QMJ$"), +1, "quality factor is defensive, rallies in stress"),
    (re.compile(rf"^({_RISK_CCY})$"), -1, "USD-per-currency quote: high-beta currency falls"),
    (re.compile(rf"^({_FUND_CCY})$"), -1, "funding currency per USD: carry unwind, USD/xxx falls"),
    (re.compile(rf"^({_USD_LEG_CCY})$"), +1, "currency-per-USD quote: USD strengthens, quote rises"),
    # EM / other currencies: H.10 and most vendors quote these as units per USD.
    (re.compile(rf"^({_EM_CCY})$"), +1, "EM currency per USD: depreciates in stress, quote rises"),
    # Pair spellings: XXXUSD = USD per XXX (falls when XXX weakens); USDXXX = XXX per USD.
    (re.compile(rf"^({_RISK_CCY})[_/\- ]?USD$"), -1, "USD-per-currency pair: high-beta currency falls"),
    (re.compile(rf"^USD[_/\- ]?({_FUND_CCY})$"), -1, "funding-currency pair per USD: carry unwind, USD/xxx falls"),
    (re.compile(rf"^USD[_/\- ]?({_USD_LEG_CCY}|{_EM_CCY})$"), +1, "currency-per-USD pair: USD strengthens, quote rises"),
    (re.compile(r"^(NFP|PAYEMS|PAYROLL)"), -1, "payrolls fall in stress"),
    (re.compile(r"^(UNRATE|UNEMP)"), +1, "unemployment rises in stress"),
    (re.compile(r"^(CPI|PCE|PPI)"), 0, "price index: stress direction ambiguous"),
]
#: US Treasury tenor ids only (UST_2Y, DGS10, TSY-30Y, ...): other sovereign curves, swap rates and
#: anything merely starting with these letters (e.g. "USTECH") are NOT yields here -> symmetric.
_UST = re.compile(r"^(UST|DGS|TSY|USGG|TREAS)[_\- ]?\d{1,3}[_\- ]?(M|MO|Y|YR)?$")


def stress_direction(asset: str, inflation_dominated: bool = False) -> int:
    return classify(asset, inflation_dominated)[0]


def classify(asset: str, inflation_dominated: bool = False) -> tuple[int, str]:
    """(direction, one-line reason) for the rationale."""
    a = str(asset).strip().upper()
    if _UST.match(a):
        if inflation_dominated:
            return +1, "yield: inflation/hike-dominated corpus, hot-print shock is UP"
        return -1, "yield: flight to quality, stress is DOWN"
    for pat, d, why in _RULES:
        if pat.match(a):
            return d, why
    return 0, "no mapping for this asset id: symmetric"


def is_rate(asset: str) -> bool:
    """Government-yield target (UST tenors). Their stress direction is genuinely two-sided -- flight
    to quality (down) vs. an inflation/hawkish repricing (up) -- so v5 can treat their skew apart."""
    return bool(_UST.match(str(asset).strip().upper()))
