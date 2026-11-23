#!/usr/bin/env python3
"""Offline checks for the limit-up price, filters, themes, and moving average."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from charts import weekly_signal
from limits import exclusion_reason, ipo_lower, ipo_upper, is_limit_up, limit_up_status, upper_limit
from themes import detail_cutoff, extract_reason, group_month, hint_from_article, issue_summary, theme_size

CONFIG = {
    "largeMinCount": 3,
    "mediumCount": 2,
    "weeklyMaPeriod": 240,
    "monthlyMaPeriod": 120,
}


def test_upper_limits() -> None:
    assert upper_limit(9940) == 12920
    assert upper_limit(16990) == 22050
    assert upper_limit(15550) == 20200
    assert upper_limit(10000) == 13000
    assert upper_limit(4400) == 5720
    assert upper_limit(1234) == 1604


def test_ipo_band_and_halt() -> None:
    assert ipo_lower(8000) == 4800
    assert ipo_upper(8000) == 32000
    assert limit_up_status(8000, 4800, 4800, 100, "LOWER_LIMIT", True, -40) == "none"
    assert limit_up_status(8000, 32000, 32000, 100, "UPPER_LIMIT", True, 300) == "limit"
    assert limit_up_status(4400, 5720, 5720, 10, "UPPER_LIMIT", False, 30) == "limit"
    assert limit_up_status(4400, 5720, 5700, 10, "", False, 29) == "touch"
    assert limit_up_status(4400, 5720, 5720, 0, "UPPER_LIMIT", False, 30) == "halt"


def _event(code, day, reason, found=True, key=None, theme_name=None, signal=False, industry="", industry_code="", article=""):
    hint_type = "dict" if key else ("unknown" if not found else "custom")
    return {
        "code": code,
        "name": code,
        "market": "KOSDAQ",
        "date": day,
        "close": 1000,
        "prevClose": 800,
        "changeRate": 30.0,
        "reason": reason,
        "reasonFound": found,
        "source": "테스트" if found else "",
        "sourceTitle": reason if found else "",
        "sourceUrl": "https://example.test/a" if found else "",
        "articleTitle": article,
        "articleBody": "",
        "weekly240Signal": signal,
        "industry": industry,
        "industryCode": industry_code,
        "themeHint": {
            "type": hint_type,
            "key": key,
            "name": theme_name or reason,
            "keyword": theme_name,
            "reason": reason,
            "found": found,
            "tokens": [],
        },
    }


def test_unknowns_are_not_merged() -> None:
    def unknown(code, day):
        return {
            "code": code,
            "name": code,
            "market": "KOSDAQ",
            "date": day,
            "close": 1000,
            "prevClose": 800,
            "changeRate": 30.0,
            "reason": "이유 미확인",
            "reasonFound": False,
            "source": "",
            "sourceTitle": "",
            "sourceUrl": "",
            "weekly240Signal": False,
            "themeHint": {"type": "unknown", "reason": "이유 미확인", "found": False, "name": "이유 미확인", "key": None},
        }

    grouped = group_month(
        [unknown("000001", "2026-01-05"), unknown("000002", "2026-01-05")],
        2026,
        1,
        CONFIG,
        ["2026-01-05"],
    )
    assert len(grouped["themes"]) == 2
    assert {theme["name"] for theme in grouped["themes"]} == {"이유 미확인"}


def test_listing_day_can_be_ordinary_limit() -> None:
    offer = 13630
    close = upper_limit(offer)
    assert limit_up_status(offer, close, close, 10, "UPPER_LIMIT", True, 29.93) == "limit"
    assert limit_up_status(8000, 32000, 32000, 10, "UPPER_LIMIT", True, 300) == "limit"


def test_title_without_dictionary_is_still_a_reason() -> None:
    title = "[특징주] 형지글로벌·형지I&C, 李 대통령 방중 기대감에 강세"
    reason, found = extract_reason(title, "형지글로벌은 상한가를 기록했다.", "형지글로벌")
    assert found
    assert "방중" in reason
    assert reason.replace("…", "") in title or reason.replace("…", "")[:12] in title


def test_similar_wording_and_sector_share_a_theme() -> None:
    grouped = group_month(
        [
            _event("000001", "2026-03-03", "HBM 수요 확대"),
            _event("000002", "2026-03-18", "파운드리 증설 소식"),
            _event("000003", "2026-03-04", "휴머노이드 양산"),
            _event("000004", "2026-03-19", "산업용 로봇 공급"),
            _event("000005", "2026-03-05", "방중 수행단 일정"),
            _event("000006", "2026-03-06", "방중 기대 속 강세"),
            _event("000007", "2026-03-07", "삼성 기대감"),
            _event("000008", "2026-03-07", "삼성 발표"),
        ],
        2026,
        3,
        CONFIG,
        ["2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-07", "2026-03-18", "2026-03-19"],
    )
    names = {theme["name"]: theme["stockCount"] for theme in grouped["themes"]}
    assert names["AI반도체"] == 2
    assert names["로봇"] == 2
    assert any(theme["stockCount"] == 2 and theme["name"] == "방중" for theme in grouped["themes"])
    assert sum(1 for theme in grouped["themes"] if "삼성" in theme["name"]) == 2


def test_missing_news_is_inferred_not_rewritten() -> None:
    grouped = group_month(
        [
            _event("000001", "2026-04-01", "양극재 공급", industry="화학", industry_code="272"),
            _event("000002", "2026-04-01", "이유 미확인", found=False, industry="화학", industry_code="272"),
            _event("000003", "2026-04-01", "이유 미확인", found=False, industry="제약", industry_code="261"),
            _event("000004", "2026-04-02", "이유 미확인", found=False, industry="제약", industry_code="261"),
            _event("000005", "2026-04-03", "이유 미확인", found=False),
        ],
        2026,
        4,
        CONFIG,
        ["2026-04-01", "2026-04-02", "2026-04-03"],
        history={"000009": {"key": "ship", "name": "조선"}},
    )
    by_name = {theme["name"]: theme for theme in grouped["themes"]}
    battery = by_name["2차전지"]
    assert battery["stockCount"] == 2
    inferred = next(stock for stock in battery["stocks"] if stock["code"] == "000002")
    assert inferred["events"][0]["inferred"] is True
    assert inferred["events"][0]["reason"] == "이유 미확인"
    assert inferred["events"][0]["reasonFound"] is False
    known = next(stock for stock in battery["stocks"] if stock["code"] == "000001")
    assert known["events"][0]["inferred"] is False
    assert known["events"][0]["reason"] == "양극재 공급"
    bio = by_name["바이오"]
    assert bio["stockCount"] == 2
    assert bio["inferredStocks"] == 2
    assert all(event["reason"] == "이유 미확인" for stock in bio["stocks"] for event in stock["events"])
    leftovers = [theme for theme in grouped["themes"] if theme["name"] == "이유 미확인"]
    assert len(leftovers) == 1
    assert leftovers[0]["stockCount"] == 1
    assert leftovers[0]["stocks"][0]["events"][0]["inferred"] is False

    later = group_month(
        [_event("000001", "2026-05-04", "이유 미확인", found=False)],
        2026,
        5,
        CONFIG,
        ["2026-05-04"],
        history=grouped["groundedByCode"],
    )
    assert later["themes"][0]["name"] == "2차전지"
    assert later["themes"][0]["stocks"][0]["events"][0]["inferred"] is True
    assert later["themes"][0]["stocks"][0]["events"][0]["reason"] == "이유 미확인"
    assert later["inferredStocks"] == 1


def test_detail_window_keeps_only_recent_reasons() -> None:
    assert detail_cutoff("2026-11-23") == "2026-05-23"
    assert detail_cutoff("2026-11-24") == "2026-05-24"
    assert detail_cutoff("2026-08-31") == "2026-02-28"
    grouped = group_month(
        [
            _event("000001", "2026-05-20", "갑돌 관련 로봇 공급", industry="전자장비와기기", industry_code="282"),
            _event("000002", "2026-05-26", "산업용 로봇 공급 계약", industry="전자장비와기기", industry_code="282"),
            _event("000003", "2026-05-27", "휴머노이드 양산 발표", industry="전자장비와기기", industry_code="282"),
            _event("000004", "2026-05-27", "이유 미확인", found=False, industry="전자장비와기기", industry_code="282"),
        ],
        2026,
        5,
        CONFIG,
        ["2026-05-20", "2026-05-26", "2026-05-27"],
        detail_from="2026-05-24",
    )
    assert len(grouped["themes"]) == 1
    theme = grouped["themes"][0]
    assert theme["name"] == "로봇"
    assert theme["id"] == "5-1"
    assert theme["stockCount"] == 4
    assert "갑돌" not in theme["issueSummary"]
    assert "산업용 로봇 공급 계약" in theme["issueSummary"]
    assert "휴머노이드 양산 발표" in theme["issueSummary"]
    assert "만들어진" not in theme["issueSummary"]
    by_code = {stock["code"]: stock for stock in theme["stocks"]}
    assert by_code["000001"]["events"][0]["detail"] is False
    assert by_code["000001"]["events"][0]["reason"] == ""
    assert by_code["000001"]["events"][0]["reasonSummary"] == ""
    assert by_code["000001"]["events"][0]["sourceTitle"] == ""
    assert by_code["000002"]["events"][0]["detail"] is True
    assert by_code["000002"]["events"][0]["reasonSummary"] == "산업용 로봇 공급 계약"
    unknown = by_code["000004"]["events"][0]
    assert unknown["inferred"] is True
    assert unknown["reasonSummary"] == "이유 미확인"
    old_only = group_month(
        [_event("000008", "2026-01-05", "항암 임상")],
        2026,
        1,
        CONFIG,
        ["2026-01-05"],
        detail_from="2026-05-24",
    )
    assert old_only["themes"][0]["issueSummary"] == ""
    assert old_only["themes"][0]["stocks"][0]["events"][0]["reason"] == ""
    assert old_only["themes"][0]["name"] == "바이오"
    bare = group_month(
        [_event("000009", "2026-06-01", "이유 미확인", found=False, industry="제약", industry_code="261")],
        2026,
        6,
        CONFIG,
        ["2026-06-01"],
        detail_from="2026-05-24",
    )
    assert bare["themes"][0]["issueSummary"] == "바이오 뉴스 근거 부족"
    assert issue_summary("방산", []) == "방산 뉴스 근거 부족"


def test_same_theme_key_is_not_split() -> None:
    def event(code, day, key, reason):
        return {
            "code": code,
            "name": code,
            "market": "KOSDAQ",
            "date": day,
            "close": 1000,
            "prevClose": 800,
            "changeRate": 30.0,
            "reason": reason,
            "reasonFound": True,
            "source": "테스트",
            "sourceTitle": reason,
            "sourceUrl": "https://example.test/a",
            "weekly240Signal": False,
            "themeHint": {"type": "dict", "key": key, "name": "바이오", "keyword": "바이오", "reason": reason, "found": True},
        }

    grouped = group_month(
        [
            event("000001", "2026-05-04", "bio", "항암 임상"),
            event("000002", "2026-05-20", "bio", "다른 문장의 바이오 소식"),
        ],
        2026,
        5,
        CONFIG,
        ["2026-05-04", "2026-05-20"],
    )
    assert len(grouped["themes"]) == 1


def test_official_base() -> None:
    assert is_limit_up(4400, 5720, 5720)
    assert is_limit_up(7400, 9620, 9620)
    assert is_limit_up(21200, 27550, 27550)
    assert not is_limit_up(4390, 5720, 5720)


def test_exclusions() -> None:
    assert exclusion_reason("삼성전자우") == "preferred"
    assert exclusion_reason("현대차2우B") == "preferred"
    assert exclusion_reason("유안타증권우") == "preferred"
    assert exclusion_reason("KODEX 200ETF") == "etf-etn"
    assert exclusion_reason("삼성 레버리지 ETN") == "etf-etn"
    assert exclusion_reason("하이제6호스팩") == "spac"
    assert exclusion_reason("삼성스팩7호") == "spac"
    assert exclusion_reason("삼성전자") is None
    assert exclusion_reason("우리금융지주") is None
    assert exclusion_reason("우진아이엔에스") is None
    assert exclusion_reason("대우건설") is None


def test_reason_is_copied() -> None:
    title = "휴림로봇 주가 ‘불기둥’...한화로보틱스와 로봇 공급 계약"
    body = "휴림로봇이 한화로보틱스와 산업용 로봇 공급 본계약을 맺었다는 소식에 상한가로 치솟았다."
    reason, found = extract_reason(title, body, "휴림로봇")
    assert found
    assert reason.replace("…", "") in title or reason.replace("…", "") in body
    hint = hint_from_article("휴림로봇", title, body)
    assert hint["found"] is True
    assert hint["key"] == "robot"
    bare, found_bare = extract_reason("휴림로봇 상한가 마감", "휴림로봇이 상한가로 장을 마쳤다.", "휴림로봇")
    assert found_bare is False
    assert bare == "이유 미확인"


def test_month_numbers_and_sizes() -> None:
    def event(code, name, day, key, theme_name, signal):
        return {
            "code": code,
            "name": name,
            "market": "KOSDAQ",
            "date": day,
            "close": 1000,
            "prevClose": 800,
            "changeRate": 25.0,
            "reason": theme_name,
            "reasonFound": True,
            "source": "테스트",
            "sourceTitle": theme_name,
            "sourceUrl": "",
            "weekly240Signal": signal,
            "weekly240Above": signal,
            "weekly240Breakout": False,
            "themeHint": {"type": "dict", "key": key, "name": theme_name, "keyword": theme_name, "reason": theme_name, "found": True},
        }

    january = [
        event("000001", "가나다", "2026-01-05", "robot", "로봇", False),
        event("000002", "라마바", "2026-01-06", "robot", "로봇", True),
        event("000003", "사아자", "2026-01-07", "robot", "로봇", False),
        event("000004", "차카타", "2026-01-07", "bio", "바이오", False),
    ]
    grouped = group_month(january, 2026, 1, CONFIG, ["2026-01-05", "2026-01-06", "2026-01-07"])
    assert [theme["id"] for theme in grouped["themes"]] == ["1-1", "1-2"]
    robot = grouped["themes"][0]
    assert robot["stockCount"] == 3
    assert robot["size"] == "대"
    assert grouped["themes"][1]["size"] == "소"
    february = [event("000009", "파하", "2026-02-02", "robot", "로봇", True)]
    next_month = group_month(february, 2026, 2, CONFIG, ["2026-02-02"])
    assert next_month["themes"][0]["id"] == "2-1"
    assert theme_size(2, True, CONFIG) == "중"
    assert theme_size(4, False, CONFIG) == "중"
    assert theme_size(1, True, CONFIG) == "소"


def test_vault_roundtrip() -> None:
    import base64

    from vault import decrypt_bytes, derive_key, encrypt_bytes, require_password

    header = {
        "alg": "AES-256-GCM",
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": 210_000,
        "salt": base64.b64encode(b"0123456789abcdef").decode("ascii"),
    }
    key = derive_key("unit-test-only", header)
    blob = encrypt_bytes('{"code":"005930"}'.encode(), key)
    assert decrypt_bytes(blob, key) == b'{"code":"005930"}'
    try:
        decrypt_bytes(blob, derive_key("wrong-password", header))
    except Exception:
        pass
    else:
        raise AssertionError("wrong password opened the payload")
    import os

    os.environ.pop("SITE_PASSWORD", None)
    try:
        require_password()
    except SystemExit as exc:
        assert "SITE_PASSWORD" in str(exc)
    else:
        raise AssertionError("missing password did not stop the collector")


def test_weekly_signal() -> None:
    start = date(2016, 1, 4)
    bars = []
    for index in range(250):
        day = start + timedelta(days=7 * index)
        price = 1000 + index
        bars.append({"date": day.strftime("%Y%m%d"), "open": price, "high": price + 5, "low": price - 5, "close": price, "volume": 1})
    signal = weekly_signal(bars, bars[-1]["date"], 240)
    assert signal["ready"] is True
    assert signal["above"] is True
    flat = []
    for index in range(240):
        day = start + timedelta(days=7 * index)
        price = 1000 if index < 239 else 5000
        flat.append({"date": day.strftime("%Y%m%d"), "open": price, "high": price, "low": price, "close": price, "volume": 1})
    jumped = weekly_signal(flat, flat[-1]["date"], 240)
    assert jumped["signal"] is True
    short = weekly_signal(bars[:30], bars[29]["date"], 240)
    assert short["ready"] is False
    assert short["signal"] is False


if __name__ == "__main__":
    test_upper_limits()
    test_official_base()
    test_ipo_band_and_halt()
    test_listing_day_can_be_ordinary_limit()
    test_title_without_dictionary_is_still_a_reason()
    test_same_theme_key_is_not_split()
    test_detail_window_keeps_only_recent_reasons()
    test_similar_wording_and_sector_share_a_theme()
    test_missing_news_is_inferred_not_rewritten()
    test_unknowns_are_not_merged()
    test_exclusions()
    test_reason_is_copied()
    test_month_numbers_and_sizes()
    test_weekly_signal()
    test_vault_roundtrip()
    print("ok")
