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
_RULES: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"^(MKT|MKTRF|MKT_RF|HML|SMB|MOM|UMD|BAB|RMW|CMA)$"), -1, "risk factor falls in stress"),
    (re.compile(r"^QMJ$"), +1, "quality factor is defensive, rallies in stress"),
    (re.compile(r"^(EUR|GBP|AUD|NZD)$"), -1, "USD-per-currency quote: high-beta currency falls"),
    (re.compile(r"^(JPY|CHF)$"), -1, "funding currency per USD: carry unwind, USD/xxx falls"),
    (re.compile(r"^(CAD|NOK|SEK|DKK)$"), +1, "currency-per-USD quote: USD strengthens, quote rises"),
    # EM / other currencies: H.10 and most vendors quote these as units per USD.
    (re.compile(r"^(CNY|CNH|BRL|INR|MXN|ZAR|TRY|KRW|IDR|RUB|PLN|HUF|CZK|CLP|COP|PEN|PHP|THB|MYR|"
                r"TWD|SGD|HKD|ILS|ARS|EGP|NGN|VND|RON|SAR|AED|QAR)$"), +1,
     "EM currency per USD: depreciates in stress, quote rises"),
    (re.compile(r"^(NFP|PAYEMS|PAYROLL)"), -1, "payrolls fall in stress"),
    (re.compile(r"^(UNRATE|UNEMP)"), +1, "unemployment rises in stress"),
    (re.compile(r"^(CPI|PCE|PPI)"), 0, "price index: stress direction ambiguous"),
]
_UST = re.compile(r"^(UST|DGS|TSY|USGG|TREAS)")


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


# ----------------------------------------------------------------------------- v6: asset class
#: Asset class of a target id (v6 per-(family, asset class) rows). Asset ids only; the declared
#: panel is a fallback for ids this table does not know (cardinfo "panels"), and anything still
#: unknown or a card that mixes classes gets None = the family row.
_CLASS_RULES: list[tuple[re.Pattern[str], str]] = [
    (_UST, "rates"),
    (re.compile(r"^(MKT|MKTRF|MKT_RF|HML|SMB|MOM|UMD|BAB|RMW|CMA|QMJ|STR|LTR)$"), "factors"),
    (re.compile(r"^(EUR|GBP|AUD|NZD|JPY|CHF|CAD|NOK|SEK|DKK|CNY|CNH|BRL|INR|MXN|ZAR|TRY|KRW|IDR|RUB|PLN|"
                r"HUF|CZK|CLP|COP|PEN|PHP|THB|MYR|TWD|SGD|HKD|ILS|ARS|EGP|NGN|VND|RON|SAR|AED|QAR)$"), "fx"),
    (re.compile(r"^(NFP|PAYEMS|PAYROLL|UNRATE|UNEMP|CPI|PCE|PPI|CORE|INDPRO|RETAIL|GDP|ICSA|HOUST)"), "macro"),
]
PANEL_CLASS = {"rates_daily": "rates", "g10_fx_daily": "fx", "em_transfer_early": "fx",
               "factors_daily": "factors", "macro_monthly": "macro"}


def asset_class(asset: str) -> str | None:
    a = str(asset).strip().upper()
    for pat, c in _CLASS_RULES:
        if pat.match(a):
            return c
    return None


def card_class(assets: list[str], panels: list[str] | None = None) -> str | None:
    """One class for the whole card, or None (mixed classes / unknown ids without a single known
    declared panel). Ids decide; the declared panel only fills ids the table does not know."""
    fill = next((PANEL_CLASS[p] for p in (panels or []) if p in PANEL_CLASS), None)   # target panel first
    cls = {asset_class(a) or fill for a in assets}
    return cls.pop() if len(cls) == 1 and None not in cls else None
