"""Weekly and monthly candles, moving averages, and chart payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def iso_date(value: str) -> str:
    raw = value.replace("-", "")
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def ymd(value: str) -> str:
    return value.replace("-", "")


def week_key(value: str) -> tuple[int, int]:
    dt = datetime.strptime(ymd(value), "%Y%m%d")
    year, week, _day = dt.isocalendar()
    return year, week


def month_key(value: str) -> str:
    return ymd(value)[:6]


def collapse(bucket: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "date": bucket[-1]["date"],
        "open": bucket[0]["open"],
        "high": max(bar["high"] for bar in bucket),
        "low": min(bar["low"] for bar in bucket),
        "close": bucket[-1]["close"],
        "volume": sum(bar["volume"] for bar in bucket),
    }


def candles(bars: list[dict[str, Any]], keyfn) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current = None
    bucket: list[dict[str, Any]] = []
    for bar in bars:
        key = keyfn(bar["date"])
        if current is None:
            current = key
        if key != current:
            if bucket:
                groups.append(collapse(bucket))
            bucket = [bar]
            current = key
        else:
            bucket.append(bar)
    if bucket:
        groups.append(collapse(bucket))
    return groups


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        return out
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= period:
            total -= values[index - period]
        if index >= period - 1:
            out[index] = total / period
    return out


def weekly_signal(bars: list[dict[str, Any]], asof: str, period: int = 240) -> dict[str, Any]:
    """Point-in-time weekly 240 MA state using sessions on or before asof."""
    limit = ymd(asof)
    use = [bar for bar in bars if ymd(bar["date"]) <= limit and bar["close"] > 0]
    weeks = candles(use, week_key)
    closes = [week["close"] for week in weeks]
    averages = sma(closes, period)
    if not weeks or averages[-1] is None:
        return {
            "above": False,
            "breakout": False,
            "signal": False,
            "ready": False,
            "ma": None,
            "close": weeks[-1]["close"] if weeks else None,
            "bars": len(weeks),
        }
    last = weeks[-1]
    above = last["close"] > averages[-1]
    breakout = False
    if len(weeks) >= 2 and averages[-2] is not None:
        previous = weeks[-2]
        breakout = previous["close"] <= averages[-2] and last["high"] >= averages[-1]
    return {
        "above": above,
        "breakout": breakout,
        "signal": above or breakout,
        "ready": True,
        "ma": round(averages[-1], 2),
        "close": last["close"],
        "bars": len(weeks),
    }


def _public_bar(bar: dict[str, Any], average: float | None = None) -> dict[str, Any]:
    payload = {
        "d": iso_date(bar["date"]),
        "o": bar["open"],
        "h": bar["high"],
        "l": bar["low"],
        "c": bar["close"],
        "v": bar["volume"],
    }
    if average is not None:
        payload["ma"] = round(average, 2)
    return payload


def chart_payload(code: str, name: str, market: str, bars: list[dict[str, Any]], updated: str, weekly_period: int, monthly_period: int) -> dict[str, Any]:
    ordered = sorted(bars, key=lambda bar: bar["date"])
    daily = [_public_bar(bar) for bar in ordered[-520:]]
    weeks = candles(ordered, week_key)
    week_ma = sma([bar["close"] for bar in weeks], weekly_period)
    weekly = [
        _public_bar(bar, week_ma[index])
        for index, bar in enumerate(weeks[-400:], start=max(0, len(weeks) - 400))
    ]
    months = candles(ordered, month_key)
    month_ma = sma([bar["close"] for bar in months], monthly_period)
    monthly = [
        _public_bar(bar, month_ma[index])
        for index, bar in enumerate(months[-180:], start=max(0, len(months) - 180))
    ]
    return {
        "code": code,
        "name": name,
        "market": market,
        "updated": updated,
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
    }
