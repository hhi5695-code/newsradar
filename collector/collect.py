#!/usr/bin/env python3
"""Collect KOSPI/KOSDAQ limit-up stocks from Naver Finance and encrypt the site data.

pykrx cannot be used here: KRX's site now requires a login and the public
endpoint comes back empty. Stock candles come from Naver's price API, whose
change field is the exchange base price. The KOSPI calendar still uses
siseJson. The universe and today's official upper-limit list come from Naver's
public market APIs. News lines come from Naver Finance news search. Nothing is
invented. Market files are written only as AES-GCM ciphertext.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site" / "data"
CACHE = Path(__file__).resolve().parent / ".cache"
CONFIG_PATH = SITE / "config.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from charts import chart_payload, iso_date, weekly_signal  # noqa: E402
from limits import exclusion_reason, is_limit_up, limit_up_status  # noqa: E402
from themes import REASON_MISSING, group_month, has_catalyst, hint_from_article  # noqa: E402
from vault import derive_key, purge_plaintext, read_json, require_password, write_json  # noqa: E402

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; sanghanga-radar/1.0; +https://github.com/hhi5695-code/newsradar)"


def kst_today() -> str:
    return datetime.now(KST).date().isoformat()


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def http_get(url: str, referer: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": referer,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001 - retry network failures
            last_error = exc
            status = getattr(exc, "code", None)
            if status in {400, 404}:
                raise
            time.sleep(0.6 * (2**attempt))
    assert last_error is not None
    raise last_error


def get_json(url: str, referer: str):
    return json.loads(http_get(url, referer).decode("utf-8"))


def parse_sise(payload: bytes) -> list[dict]:
    text = payload.decode("utf-8", "replace").strip()
    if not text or text in {"[]", "null"}:
        return []
    try:
        data = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return []
    rows = []
    for row in data:
        if not row or not str(row[0]).isdigit() or row[4] in (None, ""):
            continue
        try:
            rows.append(
                {
                    "date": str(row[0]),
                    "open": int(round(float(row[1]))),
                    "high": int(round(float(row[2]))),
                    "low": int(round(float(row[3]))),
                    "close": int(round(float(row[4]))),
                    "volume": int(float(row[5] or 0)),
                }
            )
        except (TypeError, ValueError):
            continue
    return rows


def fetch_bars(code: str, start: str, end: str) -> list[dict]:
    """Index history from Naver siseJson. Stock candles use the price API instead."""
    url = (
        "https://api.finance.naver.com/siseJson.naver"
        f"?symbol={urllib.parse.quote(code)}&requestType=1&startTime={start}&endTime={end}&timeframe=day"
    )
    return parse_sise(http_get(url, "https://finance.naver.com/"))


def parse_int(value) -> int | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("+", "").strip()
    if text in {"", "-", "N/A", "None"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def fetch_price_bars(code: str, start: str, end: str) -> list[dict]:
    """Daily candles from Naver's price list.

    The official base price is close minus compareToPreviousClosePrice.
    That base is what the exchange uses for the 30% limit. The previous
    candle's close can differ after a corporate action, so it is not used.
    """
    start_iso = iso_date(start)
    end_iso = iso_date(end)
    rows: list[dict] = []
    page = 1
    while page <= 90:
        url = (
            "https://m.stock.naver.com/api/stock/"
            f"{urllib.parse.quote(code)}/price?pageSize=60&page={page}"
        )
        batch = get_json(url, "https://m.stock.naver.com/")
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        oldest = str(batch[-1].get("localTradedAt") or "")
        if len(batch) < 60 or oldest < start_iso:
            break
        page += 1
    by_date: dict[str, dict] = {}
    for row in rows:
        day = str(row.get("localTradedAt") or "")
        if not day or day < start_iso or day > end_iso:
            continue
        close = parse_int(row.get("closePrice"))
        high = parse_int(row.get("highPrice"))
        low = parse_int(row.get("lowPrice"))
        opened = parse_int(row.get("openPrice"))
        change = parse_int(row.get("compareToPreviousClosePrice"))
        if None in {close, high, low, opened} or change is None:
            continue
        try:
            rate = float(str(row.get("fluctuationsRatio")).replace(",", "").replace("+", ""))
        except (TypeError, ValueError):
            rate = None
        by_date[day.replace("-", "")] = {
            "date": day.replace("-", ""),
            "open": opened,
            "high": high,
            "low": low,
            "close": close,
            "volume": parse_int(row.get("accumulatedTradingVolume")) or 0,
            "change": change,
            "base": close - change,
            "rate": rate,
            "flag": ((row.get("compareToPreviousPrice") or {}).get("name") or ""),
        }
    return [by_date[key] for key in sorted(by_date)]


def cached_bars(kind: str, code: str, start: str, end: str) -> list[dict]:
    path = CACHE / kind / f"{code}.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        bars = payload.get("bars") or [] if isinstance(payload, dict) else []
        covered = isinstance(payload, dict) and payload.get("start", "99999999") <= start and payload.get("end", "") >= end
        usable = bool(bars) and (code == "KOSPI" or "base" in bars[0])
        if covered and usable:
            return bars
    bars = fetch_bars(code, start, end) if code == "KOSPI" else fetch_price_bars(code, start, end)
    if bars:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"start": start, "end": end, "bars": bars}, ensure_ascii=False),
            encoding="utf-8",
        )
    return bars


def fetch_trading_days(start: str, end: str) -> list[str]:
    bars = fetch_bars("KOSPI", start.replace("-", ""), end.replace("-", ""))
    return [iso_date(bar["date"]) for bar in bars]


def fetch_universe() -> tuple[list[dict], dict[str, int]]:
    stocks: list[dict] = []
    seen: set[str] = set()
    skipped: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    for market, sosok in (("KOSPI", "0"), ("KOSDAQ", "1")):
        first = get_json(
            f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page=1&pageSize=100",
            "https://m.stock.naver.com/",
        )
        total = int(first.get("totalCount") or 0)
        pages = max(1, (total + 99) // 100)
        batches = [first]
        for page in range(2, pages + 1):
            batches.append(
                get_json(
                    f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100",
                    "https://m.stock.naver.com/",
                )
            )
        for batch in batches:
            for row in batch.get("stocks") or []:
                if row.get("stockEndType") != "stock":
                    skipped["not-stock"] = skipped.get("not-stock", 0) + 1
                    continue
                if str(row.get("sosok")) != sosok:
                    skipped["other-market"] = skipped.get("other-market", 0) + 1
                    continue
                name = (row.get("stockName") or "").strip()
                code = (row.get("itemCode") or "").strip()
                if not code or not name or code in seen:
                    continue
                reason = exclusion_reason(name)
                if reason:
                    skipped[reason] = skipped.get(reason, 0) + 1
                    bucket = samples.setdefault(reason, [])
                    if len(bucket) < 30:
                        bucket.append(name)
                    continue
                seen.add(code)
                stocks.append({"code": code, "name": name, "market": market})
    stocks.sort(key=lambda item: (item["market"], item["code"]))
    for reason, names in samples.items():
        print(f"제외 {reason}: {', '.join(names)}", flush=True)
    return stocks, skipped


def fetch_official_limitups() -> list[dict]:
    rows: list[dict] = []
    start = 0
    while start < 400:
        url = (
            "https://stock.naver.com/api/domestic/market/stock/default"
            f"?tradeType=KRX&marketType=ALL&orderType=up&startIdx={start}&pageSize=100"
        )
        try:
            batch = get_json(url, "https://stock.naver.com/")
        except urllib.error.HTTPError:
            batch = []
        if not isinstance(batch, list) or not batch:
            if start == 0:
                break
            # The list API sometimes rejects offsets other than single rows.
            one = get_json(url.replace("pageSize=100", "pageSize=1"), "https://stock.naver.com/")
            if not isinstance(one, list) or not one:
                break
            batch = one
            step = 1
        else:
            step = len(batch)
        rows.extend(batch)
        last = batch[-1]
        if str(last.get("nowPrice")) != str(last.get("upperLimitPrice")):
            break
        start += step
    official = []
    for row in rows:
        if row.get("type") not in (None, "ST"):
            continue
        if str(row.get("nowPrice")) != str(row.get("upperLimitPrice")):
            continue
        if str(row.get("sosok")) not in {"0", "1"}:
            continue
        name = row.get("itemname") or ""
        if exclusion_reason(name):
            continue
        official.append(
            {
                "code": row["itemcode"],
                "name": name,
                "market": "KOSPI" if str(row.get("sosok")) == "0" else "KOSDAQ",
                "close": int(str(row["nowPrice"]).replace(",", "")),
                "rate": float(row.get("prevChangeRate") or 0),
            }
        )
    return official


def check_today(end: str) -> None:
    official = fetch_official_limitups()
    print(f"오늘 네이버 상한가(제외 후) {len(official)}종목", flush=True)
    matched = 0
    missed = []
    for row in official:
        bars = fetch_price_bars(row["code"], end.replace("-", ""), end.replace("-", ""))
        today = next((bar for bar in bars if iso_date(bar["date"]) == end), None)
        if not today or not today.get("base"):
            missed.append((row, "시세 부족"))
            continue
        ok = is_limit_up(int(today["base"]), today["high"], today["close"]) and today["close"] == row["close"]
        if ok:
            matched += 1
        else:
            missed.append((row, f"계산 종가 {today['close']} 기준가 {today['base']} 날짜 {today['date']}"))
    print(f"공식 상한가와 가격제한 계산 일치 {matched}/{len(official)}", flush=True)
    for row, why in missed:
        print(f"  불일치 {row['name']} {row['code']} 네이버 {row['close']} (+{row['rate']}%) — {why}", flush=True)
    if official and matched / len(official) < 0.8:
        raise SystemExit("상한가 계산이 오늘 네이버 상한가와 너무 달라서 수집을 멈췄습니다.")


def clean_html(text: str) -> str:
    import html as html_lib

    value = html_lib.unescape(text or "")
    value = value.replace("<b>", "").replace("</b>", "")
    value = value.replace("\xa0", " ")
    return " ".join(value.split())


def _in_reason_window(stamp: str, day: str, prev: str) -> bool:
    """Article must fall on the limit-up date, or after the previous close."""
    if len(stamp) < 8:
        return False
    stamp_day = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
    if stamp_day == day:
        return True
    clock = stamp[8:12] if len(stamp) >= 12 else ""
    return stamp_day == prev and (not clock or clock >= "1530")


def search_news(query: str, start: str, end: str) -> list[dict]:
    url = (
        "https://stock.naver.com/api/domestic/news/search?query="
        + urllib.parse.quote(query)
        + f"&page=1&pageSize=15&startDate={start}&endDate={end}"
    )
    try:
        payload = get_json(url, "https://stock.naver.com/")
    except Exception:
        return []
    return payload.get("items") or []


def attach_news(event: dict) -> dict:
    code = event["code"]
    date = event["date"]
    path = CACHE / "news" / f"{code}_{date}.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        event.update(saved)
        if event.get("articleTitle") or event.get("articleBody"):
            refresh_reason(event)
        return event
    name = event["name"]
    day = date
    prev = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).date().isoformat()
    items: list[dict] = []
    for query in (f"{name} 상한가", name):
        found = search_news(query, prev, day)
        if not found:
            continue
        items = found
        blob = " ".join(clean_html(item.get("title", "")) + clean_html(item.get("body", "")) for item in found)
        if name in blob and "상한가" in query:
            break
    best = None
    best_score = -1
    for item in items:
        title = clean_html(item.get("title", ""))
        body = clean_html(item.get("body", ""))
        text = f"{title} {body}"
        if name not in text:
            continue
        stamp = re.sub(r"\D", "", str(item.get("datetime") or ""))
        if not _in_reason_window(stamp, day, prev):
            continue
        score = 5 if stamp[:8] == day.replace("-", "") else 2
        if name in title:
            score += 4
        if "상한가" in text:
            score += 2
        if has_catalyst(text, name):
            score += 3
        if "마감시황" in title:
            score -= 2
        if score > best_score:
            best_score = score
            best = (item, title, body)
    if best is None:
        saved = {
            "source": "",
            "sourceTitle": "",
            "sourceUrl": "",
            "articleTitle": "",
            "articleBody": "",
            "reason": REASON_MISSING,
            "reasonFound": False,
        }
    else:
        item, title, body = best
        office = str(item.get("officeId") or "")
        article = str(item.get("articleId") or "")
        url = f"https://n.news.naver.com/mnews/article/{office}/{article}" if office and article else ""
        hint = hint_from_article(name, title, body)
        saved = {
            "source": item.get("officeName") or "",
            "sourceTitle": title,
            "sourceUrl": url,
            "articleTitle": title,
            "articleBody": body[:700],
            "reason": hint["reason"],
            "reasonFound": bool(hint["found"]),
            "themeHint": hint,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    event.update(saved)
    return event


def refresh_reason(event: dict) -> dict:
    title = event.get("articleTitle") or ""
    body = event.get("articleBody") or ""
    if not title and not body:
        event["themeHint"] = {
            "type": "unknown",
            "reason": REASON_MISSING,
            "found": False,
            "name": "이유 미확인",
            "keyword": None,
            "key": None,
        }
        event["reason"] = REASON_MISSING
        event["reasonFound"] = False
        return event
    hint = hint_from_article(event["name"], title, body)
    event["themeHint"] = hint
    event["reason"] = hint["reason"]
    event["reasonFound"] = bool(hint["found"])
    return event


_LISTING_DATES: dict[str, str] | None = None


def listing_dates() -> dict[str, str]:
    global _LISTING_DATES
    if _LISTING_DATES is not None:
        return _LISTING_DATES
    path = CACHE / "listing_dates.json"
    if not path.exists():
        _LISTING_DATES = {}
        return _LISTING_DATES
    try:
        _LISTING_DATES = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _LISTING_DATES = {}
    return _LISTING_DATES


def detect_for_stock(stock: dict, trading_days: list[str], start: str, end: str) -> list[dict]:
    del trading_days  # each daily row already carries the exchange base price
    bars = cached_bars("short", stock["code"], start.replace("-", ""), end.replace("-", ""))
    listed = listing_dates().get(stock["code"], "")
    events = []
    for bar in bars:
        day = iso_date(bar["date"])
        if day < start or day > end:
            continue
        base = int(bar.get("base") or 0)
        rate = bar.get("rate")
        status = limit_up_status(
            base,
            bar["high"],
            bar["close"],
            bar.get("volume"),
            bar.get("flag") or "",
            listed == day,
            None if rate is None else float(rate),
        )
        if status not in {"limit", "flag-only"}:
            continue
        events.append(
            {
                "code": stock["code"],
                "name": stock["name"],
                "market": stock["market"],
                "date": day,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "prevClose": base,
                "changeRate": round(float(rate), 2),
            }
        )
    return events


def apply_signals(events: list[dict], end: str, period: int) -> None:
    codes = sorted({event["code"] for event in events})
    print(f"주봉 240선·차트용 장기 시세 {len(codes)}종목", flush=True)
    long_bars: dict[str, list[dict]] = {}
    failed: list[str] = []

    def load(code: str):
        return code, cached_bars("long", code, "20140101", end.replace("-", ""))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(load, code) for code in codes]
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                code, bars = future.result()
                long_bars[code] = bars
            except Exception as exc:  # noqa: BLE001
                failed.append(str(exc))
            if done % 50 == 0 or done == len(codes):
                print(f"  장기 시세 {done}/{len(codes)}", flush=True)
    if failed:
        print(f"장기 시세 실패 {len(failed)}건", flush=True)
    for event in events:
        bars = long_bars.get(event["code"]) or []
        signal = weekly_signal(bars, event["date"], period)
        event["weekly240Above"] = signal["above"]
        event["weekly240Breakout"] = signal["breakout"]
        event["weekly240Signal"] = signal["signal"]
        event["weekly240Ready"] = signal["ready"]
        event["weeklyMa240"] = signal["ma"]
        event["weeklyClose"] = signal["close"]
        event["_bars"] = bars


def enc_path(*parts: str) -> Path:
    return SITE / "enc" / Path(*parts)


def write_outputs(events: list[dict], trading_days: list[str], config: dict, collected: list[str], key: bytes) -> None:
    stored = []
    for event in events:
        event.pop("_bars", None)
        refresh_reason(event)
        stored.append({field: value for field, value in event.items() if field != "_bars"})
    purge_plaintext()
    write_json(enc_path("events.bin"), stored, key)
    months = sorted({day[:7] for day in collected})
    summaries = []
    by_month: dict[str, list[dict]] = {}
    for event in stored:
        by_month.setdefault(event["date"][:7], []).append(event)
    for month_id in months:
        year, month = int(month_id[:4]), int(month_id[5:7])
        month_events = by_month.get(month_id, [])
        payload = group_month(month_events, year, month, config, [day for day in trading_days if day[:7] == month_id])
        write_json(enc_path("months", f"{month_id}.bin"), payload, key)
        summaries.append(
            {
                "id": month_id,
                "events": payload["eventCount"],
                "stocks": payload["stockCount"],
                "themes": payload["themeCount"],
                "reasonsFound": sum(1 for event in month_events if event.get("reasonFound")),
            }
        )
        print(
            f"{month_id} 상한가 {payload['eventCount']}건 · 종목 {payload['stockCount']} · 테마 {payload['themeCount']}",
            flush=True,
        )
    latest = collected[-1] if collected else None
    manifest = {
        "updatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "네이버금융",
        "startDate": config["startDate"],
        "latestTradingDay": latest,
        "collectedDates": collected,
        "months": summaries,
        "config": {
            "largeMinCount": config["largeMinCount"],
            "mediumCount": config["mediumCount"],
            "weeklyMaPeriod": config["weeklyMaPeriod"],
            "monthlyMaPeriod": config["monthlyMaPeriod"],
        },
    }
    write_json(enc_path("manifest.bin"), manifest, key)


def write_charts(events: list[dict], updated: str, config: dict, key: bytes) -> None:
    seen = set()
    for event in events:
        if event["code"] in seen:
            continue
        seen.add(event["code"])
        bars = event.get("_bars") or []
        if not bars:
            cache = CACHE / "long" / f"{event['code']}.json"
            if cache.exists():
                payload = json.loads(cache.read_text(encoding="utf-8"))
                bars = payload.get("bars") if isinstance(payload, dict) else payload
        if not bars:
            continue
        chart = chart_payload(
            event["code"],
            event["name"],
            event["market"],
            bars,
            updated,
            int(config["weeklyMaPeriod"]),
            int(config["monthlyMaPeriod"]),
        )
        write_json(enc_path("charts", f"{event['code']}.bin"), chart, key)
    print(f"차트 {len(seen)}개", flush=True)


def load_existing_events(dates: set[str], key: bytes) -> list[dict]:
    path = enc_path("events.bin")
    if not path.exists():
        return []
    events = read_json(path, key)
    return [event for event in events if event.get("date") not in dates]


def collected_dates_on_disk(key: bytes) -> list[str]:
    path = enc_path("manifest.bin")
    if not path.exists():
        return []
    manifest = read_json(path, key)
    return list(manifest.get("collectedDates") or [])


def run_collect(dates: list[str], trading_days: list[str], config: dict, workers: int, key: bytes) -> None:
    start = dates[0]
    end = dates[-1]
    print("종목 목록 수집", flush=True)
    universe, skipped = fetch_universe()
    print(f"대상 종목 {len(universe)} · 제외 {skipped}", flush=True)
    if len(universe) < 1200:
        raise SystemExit("종목 목록이 너무 짧습니다. 목록 API를 확인해야 합니다.")
    universe_path = CACHE / "universe.json"
    universe_path.parent.mkdir(parents=True, exist_ok=True)
    universe_path.write_text(json.dumps(universe, ensure_ascii=False), encoding="utf-8")

    print(f"일봉 수집 {start}~{end} ({len(dates)} 거래일, 종목 {len(universe)})", flush=True)
    found: list[dict] = []
    failures: list[str] = []

    def work(stock: dict):
        return detect_for_stock(stock, trading_days, start, end)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, stock): stock for stock in universe}
        done = 0
        for future in as_completed(futures):
            done += 1
            stock = futures[future]
            try:
                found.extend(future.result())
            except Exception:
                failures.append(stock["code"])
            if done % 100 == 0 or done == len(universe):
                print(f"  일봉 {done}/{len(universe)} · 상한가 {len(found)} · 실패 {len(failures)}", flush=True)
    if failures:
        print(f"실패 종목 재시도 {len(failures)}", flush=True)
        retry = []
        by_code = {stock["code"]: stock for stock in universe}
        for code in failures:
            try:
                found.extend(work(by_code[code]))
            except Exception:
                retry.append(code)
                traceback.print_exc()
        failures = retry
        print(f"재시도 후 남은 실패 {len(failures)}", flush=True)

    date_set = set(dates)
    found = [event for event in found if event["date"] in date_set]
    print(f"신규 구간 상한가 {len(found)}건", flush=True)
    print("뉴스에서 상한가 이유 수집", flush=True)
    with ThreadPoolExecutor(max_workers=max(2, min(5, workers))) as pool:
        futures = [pool.submit(attach_news, event) for event in found]
        done = 0
        for future in as_completed(futures):
            done += 1
            future.result()
            if done % 50 == 0 or done == len(found):
                print(f"  뉴스 {done}/{len(found)}", flush=True)

    previous = load_existing_events(date_set, key)
    everyone = previous + found
    apply_signals(found, end, int(config["weeklyMaPeriod"]))
    all_collected = sorted(set(collected_dates_on_disk(key)) | set(dates))
    # Keep only trading days that are within the published range and were requested
    # or already stored. Days we intentionally collected with zero names still count.
    trading_set = set(trading_days)
    all_collected = [day for day in all_collected if day in trading_set and day >= config["startDate"]]
    # Dates in this run must be recorded even when a day had no limit-up.
    for day in dates:
        if day not in all_collected:
            all_collected.append(day)
    all_collected = sorted(set(all_collected))
    write_charts(found, all_collected[-1], config, key)
    for event in everyone:
        event.pop("_bars", None)
    write_outputs(everyone, trading_days, config, all_collected, key)
    if failures:
        (CACHE / "bar_failures.txt").write_text("\n".join(failures), encoding="utf-8")
        print("일부 종목 시세를 받지 못했습니다. collector/.cache/bar_failures.txt", flush=True)


def rebuild(config: dict, key: bytes) -> None:
    trading = fetch_trading_days("20251201", kst_today().replace("-", ""))
    dates = collected_dates_on_disk(key)
    events = load_existing_events(set(), key)
    if not events and not dates:
        raise SystemExit("재구성할 암호화된 원본이 없습니다.")
    end = dates[-1] if dates else kst_today()
    apply_signals(events, end, int(config["weeklyMaPeriod"]))
    write_charts(events, end, config, key)
    for event in events:
        event.pop("_bars", None)
    write_outputs(events, trading, config, dates, key)
    print("테마와 크기를 현재 설정으로 다시 암호화해 저장했습니다.", flush=True)


def resolve_dates(args, config: dict, key: bytes) -> tuple[list[str], list[str]]:
    today = kst_today()
    trading = fetch_trading_days("20251201", today.replace("-", ""))
    trading = [day for day in trading if day <= today]
    if not trading:
        raise SystemExit("거래일 달력을 가져오지 못했습니다.")
    start = config["startDate"]
    in_range = [day for day in trading if day >= start]
    if args.date:
        day = args.date
        if day not in trading:
            if datetime.strptime(day, "%Y-%m-%d").weekday() >= 5 or day < trading[-1]:
                print(f"{day} 은 휴장일이라 수집하지 않습니다.")
                return [], trading
            raise SystemExit(f"{day} 시세가 아직 없습니다.")
        return [day], trading
    have_dates = collected_dates_on_disk(key)
    if args.backfill or not have_dates:
        have = set() if args.force else set(have_dates)
        missing = [day for day in in_range if day not in have]
        return missing, trading
    # Scheduled / default run: today, if it is a session and not collected.
    if today not in trading:
        if datetime.strptime(today, "%Y-%m-%d").weekday() >= 5 or today < trading[-1]:
            print(f"{today} 은 휴장일이라 수집하지 않습니다.")
            return [], trading
        raise SystemExit(f"{today} 시세가 아직 없습니다.")
    if today in have_dates and not args.force:
        print(f"{today} 데이터는 이미 있습니다.")
        return [], trading
    return [today], trading


def main() -> None:
    parser = argparse.ArgumentParser(description="상한가 수집기")
    parser.add_argument("--backfill", action="store_true", help="2026-01-02부터 오늘까지 빠진 거래일을 채웁니다")
    parser.add_argument("--date", default="", help="특정 거래일만 수집 (YYYY-MM-DD)")
    parser.add_argument("--rebuild", action="store_true", help="저장된 원본으로 테마·크기를 다시 계산합니다")
    parser.add_argument("--force", action="store_true", help="이미 있는 날짜도 다시 받습니다")
    parser.add_argument("--universe-only", action="store_true")
    parser.add_argument("--check-today", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    config = load_config()
    CACHE.mkdir(parents=True, exist_ok=True)
    if args.universe_only:
        stocks, skipped = fetch_universe()
        print(f"대상 {len(stocks)} 제외 {skipped}")
        (CACHE / "universe.json").parent.mkdir(parents=True, exist_ok=True)
        (CACHE / "universe.json").write_text(json.dumps(stocks, ensure_ascii=False), encoding="utf-8")
        print("예시", ", ".join(stock["name"] for stock in stocks[:8]))
        return
    if args.check_today:
        check_today(kst_today())
        return
    password = require_password()
    key = derive_key(password)
    if args.rebuild:
        rebuild(config, key)
        return
    dates, trading = resolve_dates(args, config, key)
    if not dates:
        return
    if kst_today() in dates:
        check_today(kst_today())
    run_collect(dates, trading, config, args.workers, key)


if __name__ == "__main__":
    main()
