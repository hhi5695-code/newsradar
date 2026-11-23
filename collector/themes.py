"""Theme labels and month grouping.

A theme is assigned from words that actually appear in the news excerpt.
The same dictionary key joins an existing theme later in that month.
Months do not share numbers or accumulated counts.
"""

from __future__ import annotations

import re
from typing import Any

REASON_MISSING = "이유 미확인"

# Earlier match in the text wins. Longer keyword wins on the same index.
# Latin keywords use boundaries so they do not fire inside other words.
THEMES: list[tuple[str, str, tuple[str, ...]]] = [
    ("humanoid", "휴머노이드", ("휴머노이드", "휴먼로봇")),
    ("robot", "로봇", ("로보틱스", "로봇")),
    ("hbm", "HBM", ("HBM",)),
    ("foundry", "파운드리", ("파운드리",)),
    ("semiconductor", "반도체", ("반도체", "메모리칩", "메모리 반도체", "웨이퍼", "D램", "DRAM", "낸드", "NAND")),
    ("ai", "AI", ("인공지능", "생성형AI", "생성형", "데이터센터", "온디바이스", "AI")),
    ("battery", "2차전지", ("2차전지", "이차전지", "전고체", "양극재", "음극재", "배터리", "리튬")),
    ("nuclear", "원전", ("소형모듈원자로", "원자력", "원전", "SMR")),
    ("defense", "방산", ("방위산업", "K방산", "무기체계", "방산", "방위")),
    ("ship", "조선", ("해양플랜트", "LNG선", "선박", "조선")),
    ("bio", "바이오", ("바이오시밀러", "기술이전", "세포치료", "식약처", "항암", "신약", "임상", "백신", "FDA", "바이오")),
    ("auto", "자동차", ("자율주행", "전기차", "완성차", "자동차")),
    ("power", "전력기기", ("해저케이블", "전력기기", "변압기", "초고압", "전선")),
    ("cosmetic", "화장품", ("K뷰티", "화장품", "뷰티")),
    ("ent", "엔터", ("엔터테인먼트", "콘서트", "음원", "엔터주")),
    ("game", "게임", ("게임 신작", "신작 게임", "게임주")),
    ("construction", "건설", ("재건축", "리모델링", "건설주", "건설사")),
    ("steel", "철강", ("철강", "후판")),
    ("chemical", "화학", ("석유화학", "정유", "화학주")),
    ("food", "식품", ("식품", "라면")),
    ("finance", "금융", ("금융주", "은행주", "증권주", "보험주")),
    ("crypto", "가상자산", ("암호화폐", "가상자산", "비트코인")),
    ("space", "우주항공", ("우주항공", "누리호", "위성")),
    ("drone", "드론", ("드론",)),
    ("quantum", "양자", ("양자암호", "양자컴", "양자기술")),
    ("display", "디스플레이", ("디스플레이", "폴더블", "OLED")),
    ("renewable", "신재생", ("태양광", "풍력", "수소")),
    ("reit", "리츠", ("리츠", "REITs")),
    ("ma", "인수합병", ("인수합병", "공개매수", "경영권", "최대주주", "지분인수")),
    ("earnings", "실적", ("어닝서프라이즈", "흑자전환", "영업이익", "실적", "매출")),
    ("contract", "공급계약", ("공급계약", "공급 계약", "납품계약", "수주")),
    ("policy", "정책", ("규제완화", "정책수혜", "국책과제")),
    ("capital", "증자·배당", ("무상증자", "유상증자", "자사주", "배당")),
]

LATIN = {"HBM", "DRAM", "NAND", "AI", "SMR", "FDA", "OLED", "REITs", "LNG"}

_EXTRA_CATALYSTS = (
    "계약",
    "공급",
    "납품",
    "인수",
    "합병",
    "지분",
    "투자",
    "협약",
    "MOU",
    "승인",
    "허가",
    "인증",
    "공시",
    "수출",
    "개발",
    "상용화",
    "특허",
    "수혜",
    "상장",
    "공모",
    "선정",
    "양산",
    "독점",
    "출시",
    "호재",
    "테마주",
    "관련주",
)
CATALYSTS = tuple(dict.fromkeys([keyword for _key, _name, keywords in THEMES for keyword in keywords] + list(_EXTRA_CATALYSTS)))

STOPWORDS = {
    "상한가",
    "급등",
    "급락",
    "강세",
    "약세",
    "마감",
    "주가",
    "종목",
    "관련",
    "소식",
    "따라",
    "대한",
    "위해",
    "오늘",
    "전일",
    "거래",
    "기록",
    "상승",
    "하락",
    "코스피",
    "코스닥",
    "증시",
    "장중",
    "시장",
    "투자",
    "심리",
    "자극",
    "영향",
    "발표",
    "체결",
    "계약",
    "공급",
    "본계약",
    "특징주",
    "마감시황",
    "불기둥",
    "치솟",
    "오른",
    "내렸",
    "퍼센트",
    "거래일",
    "시간",
    "외신",
    "종합",
    "속보",
    "단독",
    "기자",
    "뉴스",
    "한국",
    "국내",
    "글로벌",
    "미국",
    "중국",
    "이번",
    "지난",
    "올해",
    "내년",
    "최대",
    "규모",
    "예정",
    "전망",
    "기대",
    "가능",
    "관련주",
    "테마주",
    "수혜",
    "호재",
    "공시",
    "확인",
    "알려",
    "밝혔다",
    "전했다",
    "말했다",
}


def clip(text: str, limit: int = 52) -> str:
    text = text.strip(" ·…")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _latin_hit(text: str, keyword: str) -> int:
    match = re.search(rf"(?<![A-Za-z]){re.escape(keyword)}(?![A-Za-z])", text, flags=re.IGNORECASE)
    return match.start() if match else -1


def keyword_hits(text: str) -> list[tuple[int, int, str, str, str]]:
    """Return (position, -length, key, name, keyword) for keywords present in text."""
    hits = []
    for key, name, keywords in THEMES:
        for keyword in keywords:
            if keyword.isascii():
                pos = _latin_hit(text, keyword)
            else:
                pos = text.find(keyword)
            if pos >= 0:
                hits.append((pos, -len(keyword), key, name, keyword))
    hits.sort()
    return hits


def has_catalyst(text: str, stock_name: str = "") -> bool:
    haystack = strip_name(text, stock_name) if stock_name else text
    if keyword_hits(haystack):
        return True
    lowered = haystack.lower()
    for keyword in CATALYSTS:
        if keyword.isascii():
            if _latin_hit(haystack, keyword) >= 0:
                return True
        elif keyword in haystack or keyword.lower() in lowered:
            return True
    return False


def strip_name(text: str, name: str) -> str:
    if name:
        text = text.replace(name, " ")
    return re.sub(r"\s+", " ", text).strip()


def classify_text(text: str, stock_name: str) -> dict[str, Any] | None:
    """Dictionary theme from real text, or None when no keyword is present."""
    haystack = strip_name(text, stock_name)
    hits = keyword_hits(haystack)
    if not hits:
        return None
    _pos, _neg, key, name, keyword = hits[0]
    return {"type": "dict", "key": key, "name": name, "keyword": keyword}


def content_tokens(text: str, stock_name: str) -> list[str]:
    cleaned = strip_name(text, stock_name)
    parts = re.findall(r"[가-힣A-Za-z0-9]{2,}", cleaned)
    out = []
    seen = set()
    for part in parts:
        if part in STOPWORDS or part == stock_name:
            continue
        if part not in seen:
            seen.add(part)
            out.append(part)
    return out


def extract_reason(title: str, body: str, stock_name: str) -> tuple[str, bool]:
    """One short line copied from the title or body. Never invents wording."""
    title = (title or "").strip()
    body = (body or "").strip()
    title_core = re.sub(r"\s*\[[^\]]*\]\s*$", "", title).strip()
    title_core = re.sub(r"\s*\[[^\]]{0,40}$", "", title_core).strip()
    title_tokens = content_tokens(title_core, stock_name)
    if stock_name and stock_name in title_core and (has_catalyst(title_core, stock_name) or title_tokens):
        return clip(title_core), True
    if stock_name and body and stock_name in body and has_catalyst(body, stock_name):
        parts = re.split(r"(?<=[\.!?])\s+|(?=◆)", body)
        candidates = [part.strip() for part in parts if stock_name in part and has_catalyst(part)]
        if not candidates:
            candidates = [body]
        segment = min(candidates, key=len)
        if len(segment) > 52:
            anchor = stock_name
            hits = keyword_hits(strip_name(segment, stock_name))
            if hits:
                keyword = hits[0][4]
                pos = segment.find(keyword)
                if pos >= 0:
                    start = max(0, pos - 12)
                    start = segment.rfind(" ", 0, start + 1)
                    start = 0 if start < 0 else start
                    segment = segment[start : start + 52].strip()
        reason = clip(segment)
        source = title_core + "\n" + body
        bare = reason[:-1] if reason.endswith("…") else reason
        if bare and bare in source:
            return reason, True
        if reason.replace("…", "") and stock_name in reason:
            # Window may start mid-string; still require the kept characters to be real.
            probe = reason[:-1] if reason.endswith("…") else reason
            if probe[:12] and probe[:12] in source:
                return reason, True
    return REASON_MISSING, False


def hint_from_article(stock_name: str, title: str, body: str) -> dict[str, Any]:
    reason, found = extract_reason(title, body, stock_name)
    if not found:
        return {"type": "unknown", "reason": REASON_MISSING, "found": False, "keyword": None, "key": None, "name": "이유 미확인"}
    source = f"{title}\n{body}"
    classified = classify_text(reason + "\n" + source, stock_name)
    if classified:
        classified.update({"reason": reason, "found": True})
        return classified
    tokens = content_tokens(reason, stock_name)
    label = " ".join(tokens[:3]) if tokens else clip(reason, 16)
    label = clip(label, 18)
    return {
        "type": "custom",
        "key": "custom:" + "|".join(tokens[:4]) if tokens else "custom:" + label,
        "name": label or "개별이슈",
        "keyword": tokens[0] if tokens else None,
        "tokens": tokens[:6],
        "reason": reason,
        "found": True,
    }


def theme_size(stock_count: int, has_weekly240: bool, config: dict[str, Any]) -> str:
    large = int(config["largeMinCount"])
    medium = int(config["mediumCount"])
    if stock_count >= large and has_weekly240:
        return "대"
    if stock_count <= 1:
        return "소"
    if stock_count == medium or stock_count >= large:
        return "중"
    if stock_count > 1:
        return "중"
    return "소"


def _norm_reason(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _same_custom(existing: dict[str, Any], hint: dict[str, Any]) -> bool:
    """Same theme only when the catalyst key or the reason text is the same."""
    if hint.get("key") and existing.get("key") == hint.get("key"):
        return True
    new_reason = _norm_reason(hint.get("reason") or "")
    old_reason = _norm_reason(existing.get("reason") or "")
    return bool(new_reason and new_reason == old_reason)


def group_month(events: list[dict[str, Any]], year: int, month: int, config: dict[str, Any], trading_days: list[str]) -> dict[str, Any]:
    """Group one month of limit-up events into numbered themes."""
    ordered = sorted(events, key=lambda item: (item["date"], item["code"]))
    parent: dict[Any, Any] = {}

    def find(node: Any) -> Any:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: Any, right: Any) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for event in ordered:
        node = (event["code"], event["date"])
        find(node)
        hint = event.get("themeHint") or {"type": "unknown"}
        found = bool(event.get("reasonFound") and hint.get("found", True))
        if not found:
            continue
        if hint.get("type") == "dict" and hint.get("key"):
            union(node, ("key", hint["key"]))
        reason_key = _norm_reason(event.get("reason") or hint.get("reason") or "")
        if reason_key:
            union(node, ("reason", reason_key))
        elif hint.get("type") == "custom" and hint.get("key"):
            union(node, ("custom", hint["key"]))

    grouped_events: dict[Any, list[dict[str, Any]]] = {}
    for event in ordered:
        grouped_events.setdefault(find((event["code"], event["date"])), []).append(event)
    themes: list[dict[str, Any]] = []
    for rows in sorted(grouped_events.values(), key=lambda item: (item[0]["date"], item[0]["code"])):
        hint = next(
            (row.get("themeHint") or {} for row in rows if (row.get("themeHint") or {}).get("type") == "dict"),
            rows[0].get("themeHint") or {"type": "unknown"},
        )
        chosen = {
            "id": f"{month}-{len(themes) + 1}",
            "seq": len(themes) + 1,
            "name": hint.get("name") or "이유 미확인",
            "hintType": hint.get("type") or "unknown",
            "key": hint.get("key"),
            "tokens": list(hint.get("tokens") or []),
            "keywords": [],
            "stocks": {},
            "reason": rows[0].get("reason") or hint.get("reason") or "",
        }
        themes.append(chosen)
        for event in rows:
            row_hint = event.get("themeHint") or {}
            if row_hint.get("keyword") and row_hint["keyword"] not in chosen["keywords"]:
                chosen["keywords"].append(row_hint["keyword"])
            if row_hint.get("type") == "custom":
                for token in row_hint.get("tokens") or []:
                    if token not in chosen["tokens"]:
                        chosen["tokens"].append(token)
            code = event["code"]
            stock = chosen["stocks"].get(code)
            if stock is None:
                stock = {
                    "code": code,
                    "name": event["name"],
                    "market": event["market"],
                    "dates": [],
                    "aboveWeekly240": False,
                    "events": [],
                }
                chosen["stocks"][code] = stock
            if event["date"] not in stock["dates"]:
                stock["dates"].append(event["date"])
            if event.get("weekly240Signal"):
                stock["aboveWeekly240"] = True
            stock["events"].append(_public_event(event))

    public_themes = []
    for theme in themes:
        stocks = list(theme["stocks"].values())
        stocks.sort(key=lambda item: (item["dates"][0], item["code"]))
        has_signal = any(stock["aboveWeekly240"] for stock in stocks)
        count = len(stocks)
        public_themes.append(
            {
                "id": theme["id"],
                "seq": theme["seq"],
                "name": theme["name"],
                "hintType": theme["hintType"],
                "size": theme_size(count, has_signal, config),
                "stockCount": count,
                "eventCount": sum(len(stock["events"]) for stock in stocks),
                "hasWeekly240": has_signal,
                "keywords": theme["keywords"],
                "stocks": stocks,
            }
        )

    by_date: dict[str, list[dict[str, Any]]] = {}
    theme_of_stock_event: dict[tuple[str, str], dict[str, Any]] = {}
    for theme in public_themes:
        for stock in theme["stocks"]:
            for ev in stock["events"]:
                theme_of_stock_event[(stock["code"], ev["date"])] = theme
    for event in ordered:
        theme = theme_of_stock_event[(event["code"], event["date"])]
        row = {
            "code": event["code"],
            "name": event["name"],
            "market": event["market"],
            "close": event["close"],
            "changeRate": event["changeRate"],
            "reason": event.get("reason") or REASON_MISSING,
            "reasonFound": bool(event.get("reasonFound")),
            "source": event.get("source") or "",
            "sourceTitle": event.get("sourceTitle") or "",
            "sourceUrl": event.get("sourceUrl") or "",
            "themeId": theme["id"],
            "themeName": theme["name"],
            "themeSize": theme["size"],
            "aboveWeekly240": bool(event.get("weekly240Signal")),
        }
        by_date.setdefault(event["date"], []).append(row)
    for rows in by_date.values():
        rows.sort(key=lambda item: (-item["changeRate"], item["code"]))

    month_id = f"{year:04d}-{month:02d}"
    days = [day for day in trading_days if day.startswith(month_id)]
    return {
        "month": month_id,
        "year": year,
        "monthNumber": month,
        "themes": public_themes,
        "byDate": by_date,
        "tradingDays": days,
        "eventCount": len(ordered),
        "stockCount": len({event["code"] for event in ordered}),
        "themeCount": len(public_themes),
    }


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": event["date"],
        "close": event["close"],
        "prevClose": event["prevClose"],
        "changeRate": event["changeRate"],
        "reason": event.get("reason") or REASON_MISSING,
        "reasonFound": bool(event.get("reasonFound")),
        "source": event.get("source") or "",
        "sourceTitle": event.get("sourceTitle") or "",
        "sourceUrl": event.get("sourceUrl") or "",
        "aboveWeekly240": bool(event.get("weekly240Signal")),
        "weekly240Above": bool(event.get("weekly240Above")),
        "weekly240Breakout": bool(event.get("weekly240Breakout")),
    }
