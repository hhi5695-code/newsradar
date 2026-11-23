"""KRX price-limit math and listing filters.

Upper limit follows the exchange rule: 30% of the base price, truncated to the
base price's tick, then truncated again to the resulting price's tick.
Tick table is the unified KRX table in effect since 2023-01-25.
"""

from __future__ import annotations

import re

TICKS = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
)


def tick_size(price: int) -> int:
    for limit, tick in TICKS:
        if price < limit:
            return tick
    return 1000


def upper_limit(base: int) -> int:
    """Return the daily upper limit for an integer base price (usually prior close)."""
    if base <= 0:
        raise ValueError("base price must be positive")
    raw_width = (base * 30) // 100
    base_tick = tick_size(base)
    width = (raw_width // base_tick) * base_tick
    candidate = base + width
    result_tick = tick_size(candidate)
    return (candidate // result_tick) * result_tick


def is_limit_up(prev_close: int, high: int, close: int) -> bool:
    """True when the session closed exactly at the calculated upper limit."""
    if prev_close <= 0 or close <= prev_close or high != close:
        return False
    return close == upper_limit(prev_close)


def _band_price(base: int, numer: int, denom: int) -> int:
    raw = (base * numer) // denom
    if raw <= 0:
        return 0
    tick = tick_size(raw)
    return (raw // tick) * tick


def ipo_upper(offer: int) -> int:
    """First-day upper price: 400% of the offer, truncated to the quote tick.

    Since 2023-06-26 a new KOSPI/KOSDAQ listing trades between 60% and 400%
    of the public offer price. The offer price is the session's base price.
    """
    return _band_price(offer, 4, 1)


def ipo_lower(offer: int) -> int:
    return _band_price(offer, 60, 100)


def is_ipo_limit_up(offer: int, high: int, close: int) -> bool:
    if offer <= 0 or high != close or close <= offer:
        return False
    return close == ipo_upper(offer)


def limit_up_status(
    base: int,
    high: int,
    close: int,
    volume: int | None,
    flag: str,
    listed_today: bool,
    rate: float | None,
) -> str:
    """Classify one session.

    limit: closed on the upper price (30% rule, or 400% of the offer on the
    listing day). flag-only: Naver marked 상한 but the tick formula does not
    match; still a limit-up, and the audit lists it. touch: the high printed
    the upper price and the close did not. halt: no volume.
    """
    if volume is not None and volume <= 0:
        return "halt"
    if base <= 0 or close <= 0:
        return "none"
    # Listing day can be either the ordinary 30% limit or the 400% offer band.
    on_limit = high == close and (is_limit_up(base, high, close) or (listed_today and is_ipo_limit_up(base, high, close)))
    if on_limit:
        return "limit"
    upper_prices = {upper_limit(base)}
    if listed_today:
        upper_prices.add(ipo_upper(base))
    if high in upper_prices and close != high:
        return "touch"
    if flag == "UPPER_LIMIT" and high == close and (rate is None or rate >= 28 or listed_today):
        return "flag-only"
    return "none"


def change_rate(prev_close: int, close: int) -> float:
    return round((close - prev_close) / prev_close * 100, 2)


_PREF_NUM = re.compile(r"\d우[A-Z]?$")
_PREF_LETTER = re.compile(r"우[B-Z]$")


def exclusion_reason(name: str) -> str | None:
    """Return why a listing is excluded, or None to keep it.

    Excludes ETF/ETN, SPACs, and preferred shares only.
    """
    if not name:
        return "empty"
    upper = name.upper()
    compact = name.replace(" ", "")
    if "ETF" in upper or "ETN" in upper:
        return "etf-etn"
    if "스팩" in compact or "기업인수목적" in compact:
        return "spac"
    if compact.endswith("우선주") or _PREF_NUM.search(compact) or _PREF_LETTER.search(compact) or compact.endswith("우"):
        return "preferred"
    return None
