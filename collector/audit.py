#!/usr/bin/env python3
"""Full limit-up audit from 2026-01-02.

Two independent calculations are required for every trading day:

* Naver Finance daily prices (mobile price API). The base price is close minus
  the exchange change, and Naver's own UPPER_LIMIT flag is kept.
* FinanceDataReader daily bars (``NAVER:`` reader, which is the fchart series).
  A row is a candidate when the change from the previous close is at least
  about 29.5% and the close equals the 30% upper limit of that previous close.

Where FinanceData's KRX daily snapshot exists (2026-03-08 onward) it is a third
check. Snapshots whose volume does not match Naver are treated as intraday and
are not used to override the close.

Edge cases: trading halts (no volume) are not limit-ups; the listing day uses
the 400% offer-price band instead of 30%; KONEX, preferred shares, SPACs, ETFs
and ETNs are excluded. Sources that disagree are listed. A limit-up is kept
when the Naver official base (or the listing-day band) says so.

The stock-level report is written under collector/.cache/audit/, which is not
committed. If SITE_PASSWORD is set, the same report is encrypted to
audit/report.bin.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import traceback
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import (  # noqa: E402
    attach_news,
    fetch_price_bars,
    fetch_trading_days,
    fetch_universe,
    kst_today,
    load_config,
)
from limits import exclusion_reason, is_limit_up, limit_up_status  # noqa: E402
from themes import REASON_MISSING, group_month  # noqa: E402

START = "2026-01-02"
KRX_CACHE_FROM = "2026-03-08"
AUDIT = Path(__file__).resolve().parent / ".cache" / "audit"
UA = "Mozilla/5.0 (compatible; sanghanga-audit/1.0)"


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://github.com/"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _ymd(day: str) -> str:
    return day.replace("-", "")


def load_listing_table() -> dict[str, str]:
    path = AUDIT / "listing_dates.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    raw = _get(
        "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/listing/desc/2026-11-23.csv"
    ).decode("utf-8-sig")
    dates = {}
    for row in csv.DictReader(io.StringIO(raw)):
        code = (row.get("Code") or "").strip()
        listed = (row.get("ListingDate") or "")[:10]
        if code and listed:
            dates[code] = listed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dates, ensure_ascii=False), encoding="utf-8")
    cache = Path(__file__).resolve().parent / ".cache" / "listing_dates.json"
    cache.write_text(json.dumps(dates, ensure_ascii=False), encoding="utf-8")
    return dates


def load_delisted(listing_dates: dict[str, str]) -> list[dict]:
    raw = _get(
        "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/listing/delisting/2026-11-23.csv"
    ).decode("utf-8-sig")
    added = []
    for row in csv.DictReader(io.StringIO(raw)):
        if row.get("SecuGroup") != "주권":
            continue
        if row.get("Market") not in {"KOSPI", "KOSDAQ"}:
            continue
        if (row.get("DelistingDate") or "") < START:
            continue
        if "우선주" in (row.get("Kind") or ""):
            continue
        code = (row.get("Symbol") or "").strip()
        name = (row.get("Name") or "").strip()
        if not code or exclusion_reason(name):
            continue
        if not (len(code) == 6):
            continue
        added.append({"code": code, "name": name, "market": row["Market"], "delisted": row.get("DelistingDate") or ""})
        listed = (row.get("ListingDate") or "")[:10]
        if listed:
            listing_dates.setdefault(code, listed)
    return added


def universe() -> list[dict]:
    cache = Path(__file__).resolve().parent / ".cache" / "universe.json"
    if cache.exists():
        stocks = json.loads(cache.read_text(encoding="utf-8"))
    else:
        stocks, _skipped = fetch_universe()
    listing = load_listing_table()
    seen = {row["code"] for row in stocks}
    for row in load_delisted(listing):
        if row["code"] not in seen:
            stocks.append(row)
            seen.add(row["code"])
    # Refresh the shared listing-date cache after delisted names are merged.
    shared = Path(__file__).resolve().parent / ".cache" / "listing_dates.json"
    shared.write_text(json.dumps(listing, ensure_ascii=False), encoding="utf-8")
    return stocks, listing


def naver_bars(code: str) -> list[dict]:
    path = AUDIT / "naver" / f"{code}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    bars = fetch_price_bars(code, "20251201", _ymd(kst_today()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bars, ensure_ascii=False), encoding="utf-8")
    return bars


def fdr_bars(code: str) -> list[dict]:
    path = AUDIT / "fdr" / f"{code}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    import FinanceDataReader as fdr

    frame = fdr.DataReader(f"NAVER:{code}", "2025-12-01", kst_today())
    rows = []
    if frame is not None and len(frame):
        for stamp, row in frame.iterrows():
            rows.append(
                {
                    "date": stamp.strftime("%Y-%m-%d"),
                    "open": int(row["Open"]),
                    "high": int(row["High"]),
                    "low": int(row["Low"]),
                    "close": int(row["Close"]),
                    "volume": int(row["Volume"]),
                }
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def krx_day(day: str) -> list[dict]:
    path = AUDIT / "krx" / f"{day}.csv"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            _get(
                "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/listing/krx/"
                + day
                + ".csv"
            )
        )
    rows = []
    for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
        if row.get("MarketId") not in {"STK", "KSQ"}:
            continue
        name = row.get("Name") or ""
        dept = row.get("Dept") or ""
        if "SPAC" in dept.upper() or "스팩" in dept:
            continue
        if exclusion_reason(name):
            continue
        rows.append(row)
    return rows


def _int(value) -> int:
    try:
        return int(str(value).replace(",", "").split(".")[0])
    except (TypeError, ValueError):
        return 0


def _float(value) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def scan_prices(stocks: list[dict], listing: dict[str, str], workers: int) -> dict:
    naver_hits = {}
    fdr_hits = {}
    naver_days = set()
    coverage = defaultdict(int)
    failures = {"naver": [], "fdr": []}
    touches = []
    halts = []

    def work(stock: dict) -> dict:
        code = stock["code"]
        out = {"naver": [], "fdr": [], "naver_error": "", "fdr_error": ""}
        try:
            out["naver"] = naver_bars(code)
        except Exception as exc:  # noqa: BLE001
            out["naver_error"] = f"{type(exc).__name__}: {exc}"
        try:
            out["fdr"] = fdr_bars(code)
        except Exception as exc:  # noqa: BLE001
            out["fdr_error"] = f"{type(exc).__name__}: {exc}"
        return out

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, stock): stock for stock in stocks}
        for future in as_completed(futures):
            stock = futures[future]
            done += 1
            try:
                result = future.result()
            except Exception:
                traceback.print_exc()
                failures["naver"].append(stock["code"])
                continue
            if result["naver_error"]:
                failures["naver"].append(stock["code"])
            if result["fdr_error"]:
                failures["fdr"].append(stock["code"])
            listed = listing.get(stock["code"], "")
            for bar in result["naver"]:
                day = f"{bar['date'][:4]}-{bar['date'][4:6]}-{bar['date'][6:8]}" if "-" not in bar["date"] else bar["date"]
                if day < START:
                    continue
                coverage[day] += 1
                status = limit_up_status(
                    int(bar.get("base") or 0),
                    int(bar["high"]),
                    int(bar["close"]),
                    int(bar.get("volume") or 0),
                    bar.get("flag") or "",
                    listed == day,
                    None if bar.get("rate") is None else float(bar["rate"]),
                )
                record = {
                    "date": day,
                    "code": stock["code"],
                    "name": stock["name"],
                    "market": stock["market"],
                    "close": int(bar["close"]),
                    "high": int(bar["high"]),
                    "base": int(bar.get("base") or 0),
                    "rate": bar.get("rate"),
                    "volume": int(bar.get("volume") or 0),
                    "status": status,
                    "flag": bar.get("flag") or "",
                    "listedToday": listed == day,
                }
                if status in {"limit", "flag-only"}:
                    naver_hits[(day, stock["code"])] = record
                elif status == "touch":
                    touches.append(record)
                elif status == "halt" and bar.get("flag") == "UPPER_LIMIT":
                    halts.append(record)
                naver_days.add((day, stock["code"]))
            previous = None
            for bar in result["fdr"]:
                day = bar["date"]
                if previous and day >= START:
                    prev_close = int(previous["close"])
                    rate = (bar["close"] - prev_close) / prev_close * 100 if prev_close else 0
                    if rate >= 29.5 and is_limit_up(prev_close, bar["high"], bar["close"]) and bar["volume"] > 0:
                        fdr_hits[(day, stock["code"])] = {
                            "date": day,
                            "code": stock["code"],
                            "name": stock["name"],
                            "market": stock["market"],
                            "close": bar["close"],
                            "high": bar["high"],
                            "base": prev_close,
                            "rate": round(rate, 2),
                            "volume": bar["volume"],
                        }
                previous = bar
            if done % 100 == 0 or done == len(stocks):
                print(
                    f"  시세 {done}/{len(stocks)} · 네이버 상한 {len(naver_hits)} · FDR 상한 {len(fdr_hits)}",
                    flush=True,
                )
    return {
        "naver": naver_hits,
        "fdr": fdr_hits,
        "coverage": coverage,
        "failures": failures,
        "touches": touches,
        "halts": halts,
        "naverDays": naver_days,
    }


def scan_krx(days: list[str], listing: dict[str, str], workers: int) -> dict:
    hits = {}
    stale = []
    missing_files = []

    def work(day: str):
        if day < KRX_CACHE_FROM:
            return day, None, "before-cache"
        try:
            return day, krx_day(day), ""
        except Exception as exc:  # noqa: BLE001
            return day, None, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for day, rows, error in pool.map(work, days):
            if error == "before-cache":
                continue
            if error or rows is None:
                missing_files.append({"date": day, "error": error})
                continue
            samsung = next((row for row in rows if row.get("Code") == "005930"), None)
            if samsung and _int(samsung.get("Volume")) <= 0:
                continue
            for row in rows:
                code = row.get("Code") or ""
                close = _int(row.get("Close"))
                high = _int(row.get("High"))
                change = _int(row.get("Changes"))
                volume = _int(row.get("Volume"))
                base = close - change
                listed = listing.get(code, "") == day
                status = limit_up_status(base, high, close, volume, "", listed, _float(row.get("ChagesRatio")))
                if status not in {"limit", "flag-only"}:
                    continue
                hits[(day, code)] = {
                    "date": day,
                    "code": code,
                    "name": row.get("Name") or "",
                    "market": "KOSPI" if row.get("MarketId") == "STK" else "KOSDAQ",
                    "close": close,
                    "high": high,
                    "base": base,
                    "rate": _float(row.get("ChagesRatio")),
                    "volume": volume,
                    "status": status,
                }
    print(f"  KRX 스냅샷 상한 {len(hits)} · 파일 없음 {len(missing_files)}", flush=True)
    return {"hits": hits, "stale": stale, "missing": missing_files}


def reconcile(scanned: dict, krx: dict, names: dict[str, dict]) -> dict:
    naver = scanned["naver"]
    fdr = scanned["fdr"]
    krx_hits = krx["hits"]
    naver_days = scanned["naverDays"]
    failed = set(scanned["failures"]["naver"])
    kept = {}
    fixed = []
    open_mismatches = []
    for key in sorted(set(naver) | set(fdr) | set(krx_hits)):
        day, code = key
        left = naver.get(key)
        mid = fdr.get(key)
        right = krx_hits.get(key)
        stock = names.get(code, {})
        name = (left or mid or right or {}).get("name") or stock.get("name") or code
        market = (left or mid or right or {}).get("market") or stock.get("market") or ""
        naver_seen = code not in failed and (day, code) in naver_days
        if left:
            record = dict(left)
            record["name"] = name
            record["market"] = market
            record["prevClose"] = record.get("base")
            record["changeRate"] = record.get("rate") or 0
            notes = []
            if not mid:
                notes.append("FDR 전일종가 계산은 상한가가 아님")
                fixed.append({**_brief(record), "kind": "naver-only", "note": notes[-1]})
            if right and int(right["close"]) != int(left["close"]):
                same_volume = left["volume"] and right["volume"] and abs(left["volume"] - right["volume"]) / max(left["volume"], right["volume"]) <= 0.02
                if same_volume:
                    notes.append(f"KRX 스냅샷 종가 {right['close']} · 네이버 종가 {left['close']}")
                    open_mismatches.append({**_brief(record), "kind": "krx-close", "krxClose": right["close"], "note": notes[-1]})
            elif right is None and day >= KRX_CACHE_FROM and not mid:
                pass
            if left.get("status") == "flag-only":
                notes.append("네이버는 상한으로 표시했지만 호가단위 공식과 불일치")
                open_mismatches.append({**_brief(record), "kind": "flag-only", "note": notes[-1]})
            record["notes"] = notes
            kept[key] = record
            continue
        if mid and not left and naver_seen:
            fixed.append(
                {
                    "date": day,
                    "code": code,
                    "name": name,
                    "market": market,
                    "close": mid["close"],
                    "kind": "fdr-only",
                    "note": "FDR 전일종가만 상한가. 거래소 기준가와 달라 제외",
                }
            )
            continue
        if mid and not left and not naver_seen:
            record = dict(mid)
            record["name"] = name
            record["market"] = market
            record["prevClose"] = record.get("base")
            record["changeRate"] = record.get("rate") or 0
            record["status"] = "fdr-gap"
            record["notes"] = ["네이버 시세가 없어 FDR 상한가를 유지"]
            kept[key] = record
            open_mismatches.append({**_brief(record), "kind": "naver-missing", "note": record["notes"][0]})
            continue
        if right and not left and naver_seen:
            fixed.append(
                {
                    "date": day,
                    "code": code,
                    "name": name,
                    "market": market,
                    "close": right["close"],
                    "kind": "krx-only",
                    "note": "KRX 스냅샷만 상한가. 네이버 종가와 달라 제외",
                }
            )
            continue
        if right and not left and not naver_seen:
            record = dict(right)
            record["prevClose"] = record.get("base")
            record["changeRate"] = record.get("rate") or 0
            record["status"] = "krx-gap"
            record["notes"] = ["네이버 시세가 없어 KRX 스냅샷 상한가를 유지"]
            kept[key] = record
            open_mismatches.append({**_brief(record), "kind": "naver-missing", "note": record["notes"][0]})
    return {"kept": kept, "fixed": fixed, "open": open_mismatches}


def _brief(record: dict) -> dict:
    return {
        "date": record["date"],
        "code": record["code"],
        "name": record["name"],
        "market": record.get("market") or "",
        "close": record.get("close"),
        "rate": record.get("rate"),
    }


def theme_violations(events: list[dict], config: dict, trading_days: list[str]) -> list[str]:
    problems = []
    by_month = defaultdict(list)
    for event in events:
        by_month[event["date"][:7]].append(event)
    for month_id, rows in sorted(by_month.items()):
        year, month = int(month_id[:4]), int(month_id[5:7])
        grouped = group_month(rows, year, month, config, [day for day in trading_days if day[:7] == month_id])
        theme_of = {}
        for theme in grouped["themes"]:
            for stock in theme["stocks"]:
                for event in stock["events"]:
                    theme_of[(stock["code"], event["date"])] = theme["id"]
        originals = {(row["code"], row["date"]): row for row in rows}
        seen_reason = {}
        seen_key = {}
        for ident, theme_id in theme_of.items():
            row = originals[ident]
            hint = row.get("themeHint") or {}
            if row.get("reasonFound") and hint.get("type") == "dict" and hint.get("key"):
                previous = seen_key.get(hint["key"])
                if previous and previous != theme_id:
                    problems.append(f"{month_id} 같은 테마 키 {hint['key']}가 {previous}와 {theme_id}로 갈라짐")
                seen_key[hint["key"]] = theme_id
            if row.get("reasonFound"):
                text = "".join((row.get("reason") or "").split())
                previous = seen_reason.get(text)
                if text and previous and previous != theme_id:
                    problems.append(f"{month_id} 같은 이유가 {previous}와 {theme_id}로 갈라짐")
                if text:
                    seen_reason[text] = theme_id
        keys_in_theme = defaultdict(set)
        for ident, theme_id in theme_of.items():
            row = originals[ident]
            hint = row.get("themeHint") or {}
            if row.get("reasonFound") and hint.get("type") == "dict" and hint.get("key"):
                keys_in_theme[theme_id].add(hint["key"])
        for theme_id, keys in keys_in_theme.items():
            if len(keys) > 1:
                problems.append(f"{month_id} {theme_id}에 서로 다른 테마 키가 섞임: {', '.join(sorted(keys))}")
        unknown_events = [ident for ident, row in originals.items() if not row.get("reasonFound")]
        unknown_ids = {theme_of[ident] for ident in unknown_events}
        if unknown_events and len(unknown_ids) != len(unknown_events):
            problems.append(f"{month_id} 이유 미확인 {len(unknown_events)}건이 테마 {len(unknown_ids)}개로 합쳐짐")
    return problems


def render_report(payload: dict) -> str:
    lines = [
        "# 상한가 전수조사",
        "",
        f"기간: {START} ~ {payload['end']} (거래일 {payload['tradingDays']}일)",
        "",
        "기준은 두 가지다. 네이버 일별시세의 거래소 기준가(종가에서 등락을 뺀 값)와 상한 표시, 그리고 FinanceDataReader 일봉의 전일 종가 대비 등락이 약 29.5% 이상이면서 종가가 그 전일 종가의 상한가와 같은지다. 2026-03-08부터는 KRX 일별 스냅샷 종가도 대조했다.",
        "",
        "상장 첫날은 공모가의 400%를 상한으로 본다. 거래량 0은 거래정지로 보고 상한가에서 뺀다. 코넥스, 우선주, 스팩, ETF, ETN은 제외한다. 고가만 상한가이고 종가는 아닌 경우는 상한가로 세지 않는다.",
        "",
        "이유는 그날(또는 전 거래일 15:30 이후)의 기사·공시에서 문장을 그대로 가져온다. 문장을 만들지 않는다. 근거가 없으면 이유 미확인이며, 그 종목은 서로 테마로 합치지 않는다.",
        "",
        "## 월별 상한가",
        "",
        "| 월 | 건수 | 종목 수 | 이유 미확인 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in payload["months"]:
        lines.append(f"| {row['month']} | {row['events']} | {row['stocks']} | {row['unknown']} |")
    lines += [
        "",
        f"합계 {payload['events']}건, 이유 미확인 {payload['unknown']}건.",
        "",
        f"불일치로 고쳐 뺀 건 {payload['fixedCount']}건 (FDR만 상한 {payload['fixedFdr']}건, KRX 스냅샷만 상한 {payload['fixedKrx']}건).",
        f"네이버만 상한이라 유지한 건 {payload['naverOnly']}건. 상한 표시와 호가 공식이 어긋난 건 {payload['flagOnly']}건. 거래량은 같은데 KRX 종가가 다른 건 {payload['krxClose']}건.",
        "",
        "## 시세가 비는 날",
        "",
    ]
    if payload["thinDays"]:
        for row in payload["thinDays"]:
            lines.append(f"- {row['date']}: 네이버 시세 {row['covered']}종목 / 대상 {payload['universe']}")
    else:
        lines.append("거래일마다 대상 종목의 80% 이상에서 네이버 시세를 받았다.")
    lines += ["", "## 테마", "", payload["themeNote"], ""]
    if payload["themeProblems"]:
        lines.append("테마 점검에서 남은 문제:")
        for problem in payload["themeProblems"]:
            lines.append(f"- {problem}")
    else:
        lines.append("같은 달에서 같은 이유·같은 테마 키는 한 번호로 모이고, 다른 키는 섞이지 않았다. 이유 미확인은 종목마다 따로 두었다.")
    lines += [
        "",
        "## 수집 실패",
        "",
        f"네이버 시세를 못 받은 종목 {len(payload['naverFailures'])}개, FDR 시세를 못 받은 종목 {len(payload['fdrFailures'])}개.",
        "",
        "고가만 상한가인 경우와 거래정지 상한 표시는 상한가 건수에 넣지 않았다.",
        f"고가 상한 {payload['touches']}건, 거래정지인데 상한 표시 {payload['halts']}건.",
        "",
    ]
    return "\n".join(lines) + "\n"


def maybe_publish(report: str) -> str:
    import os

    password = os.environ.get("SITE_PASSWORD", "").strip()
    if not password:
        return "SITE_PASSWORD 가 없어 종목이 적힌 보고서는 저장소에 넣지 않았다."
    from vault import derive_key, write_json

    target = ROOT / "audit" / "report.bin"
    write_json(target, {"report": report}, derive_key(password))
    return f"암호화된 보고서를 {target.relative_to(ROOT)} 에 저장했다."


def main() -> None:
    parser = argparse.ArgumentParser(description="상한가 전수조사")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--codes", default="", help="쉼표로 구분한 종목코드만 조사")
    parser.add_argument("--skip-news", action="store_true")
    args = parser.parse_args()
    AUDIT.mkdir(parents=True, exist_ok=True)
    end = kst_today()
    print("거래일 달력", flush=True)
    trading = [day for day in fetch_trading_days("20251201", _ymd(end)) if START <= day <= end]
    print(f"거래일 {len(trading)}일 {trading[0]}~{trading[-1]}", flush=True)
    stocks, listing = universe()
    if args.codes:
        wanted = {code.strip() for code in args.codes.split(",") if code.strip()}
        stocks = [stock for stock in stocks if stock["code"] in wanted]
    names = {stock["code"]: stock for stock in stocks}
    print(f"대상 {len(stocks)}종목", flush=True)
    print("네이버·FDR 시세", flush=True)
    scanned = scan_prices(stocks, listing, args.workers)
    print("KRX 스냅샷", flush=True)
    krx = scan_krx(trading, listing, min(8, args.workers))
    result = reconcile(scanned, krx, names)
    events = [result["kept"][key] for key in sorted(result["kept"])]
    print(f"확정 상한가 {len(events)}건 · 뉴스", flush=True)
    if not args.skip_news:
        with ThreadPoolExecutor(max_workers=max(2, min(6, args.workers))) as pool:
            futures = [pool.submit(attach_news, event) for event in events]
            done = 0
            for future in as_completed(futures):
                future.result()
                done += 1
                if done % 50 == 0 or done == len(events):
                    print(f"  뉴스 {done}/{len(events)}", flush=True)
    else:
        for event in events:
            event["reason"] = REASON_MISSING
            event["reasonFound"] = False
            event["sourceUrl"] = ""
    config = load_config()
    problems = theme_violations(events, config, trading) if events else []
    months = []
    by_month = defaultdict(list)
    for event in events:
        by_month[event["date"][:7]].append(event)
    for month_id in sorted({day[:7] for day in trading}):
        rows = by_month.get(month_id, [])
        months.append(
            {
                "month": month_id,
                "events": len(rows),
                "stocks": len({row["code"] for row in rows}),
                "unknown": sum(1 for row in rows if not row.get("reasonFound")),
            }
        )
    universe_count = len(stocks)
    thin = []
    for day in trading:
        covered = scanned["coverage"].get(day, 0)
        if universe_count and covered / universe_count < 0.8:
            thin.append({"date": day, "covered": covered})
    fixed = result["fixed"]
    payload = {
        "end": end,
        "tradingDays": len(trading),
        "universe": universe_count,
        "events": len(events),
        "unknown": sum(1 for row in events if not row.get("reasonFound")),
        "months": months,
        "fixedCount": sum(1 for row in fixed if row["kind"] in {"fdr-only", "krx-only"}),
        "fixedFdr": sum(1 for row in fixed if row["kind"] == "fdr-only"),
        "fixedKrx": sum(1 for row in fixed if row["kind"] == "krx-only"),
        "naverOnly": sum(1 for row in fixed if row["kind"] == "naver-only"),
        "flagOnly": sum(1 for row in result["open"] if row["kind"] == "flag-only"),
        "krxClose": sum(1 for row in result["open"] if row["kind"] == "krx-close"),
        "thinDays": thin,
        "themeProblems": problems,
        "themeNote": "테마는 기사에 나온 같은 이유끼리만 한 달 안에서 묶는다. 다른 이유는 합치지 않는다.",
        "naverFailures": scanned["failures"]["naver"],
        "fdrFailures": scanned["failures"]["fdr"],
        "touches": len(scanned["touches"]),
        "halts": len(scanned["halts"]),
        "krxMissing": krx["missing"],
    }
    report = render_report(payload)
    (AUDIT / "report.md").write_text(report, encoding="utf-8")
    (AUDIT / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (AUDIT / "events.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    (AUDIT / "fixed.json").write_text(json.dumps(fixed, ensure_ascii=False), encoding="utf-8")
    (AUDIT / "open.json").write_text(json.dumps(result["open"], ensure_ascii=False), encoding="utf-8")
    print(report)
    print(maybe_publish(report), flush=True)


if __name__ == "__main__":
    main()
