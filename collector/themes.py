"""Theme labels and month grouping.

Themes follow the issue, not the exact sentence. The same sector or the same
theme words (2차전지, 바이오, 원전, 로봇, AI반도체, 방산, and the rest of the
dictionary) share one number for the month. A stock with no news sentence
stays `이유 미확인`. Its theme can still be inferred from its industry, from
limit-ups in that industry on the same day or nearby days, or from the stock's
own recent theme. That assignment is marked inferred. The reason sentence is
never written in.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

REASON_MISSING = "이유 미확인"

# Longer keyword wins. A sector theme beats a generic catalyst (실적, 계약).
# Latin keywords use boundaries so they do not fire inside other words.
THEMES: list[tuple[str, str, tuple[str, ...]]] = [
    ("robot", "로봇", ("휴머노이드", "휴먼로봇", "협동로봇", "로보틱스", "액추에이터", "감속기", "로봇")),
    ("ai_semi", "AI반도체", ("AI반도체", "AI 반도체", "온디바이스", "파운드리", "메모리 반도체", "메모리칩", "소부장", "엔비디아", "웨이퍼", "반도체", "HBM", "GPU", "DRAM", "NAND", "D램", "낸드")),
    ("ai", "AI", ("인공지능", "생성형AI", "생성형", "챗봇", "LLM", "AI")),
    ("battery", "2차전지", ("2차전지", "이차전지", "2차 전지", "이차 전지", "전고체", "양극재", "음극재", "전해질", "폐배터리", "배터리", "리튬")),
    ("nuclear", "원전", ("소형모듈원자로", "원자력발전", "원자력", "핵융합", "원전", "SMR")),
    ("defense", "방산", ("방위산업", "무기체계", "K방산", "방산", "방위", "천무")),
    ("ship", "조선", ("해양플랜트", "조선소", "LNG선", "선박", "조선")),
    ("bio", "바이오", ("바이오시밀러", "세포치료", "비만치료", "기술이전", "기술수출", "식약처", "제약사", "바이오텍", "항암", "신약", "임상", "백신", "제약", "바이오", "FDA")),
    ("auto", "자동차", ("자율주행", "하이브리드", "전기차", "완성차", "자동차")),
    ("power", "전력기기", ("해저케이블", "전력설비", "전력기기", "변압기", "초고압", "전선")),
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
    ("quantum", "양자", ("양자암호", "양자컴", "양자기술", "양자")),
    ("display", "디스플레이", ("디스플레이", "폴더블", "OLED")),
    ("renewable", "신재생", ("태양광", "풍력", "수소")),
    ("reit", "리츠", ("리츠", "REITs")),
    ("ma", "인수합병", ("인수합병", "공개매수", "경영권", "최대주주", "지분인수")),
    ("earnings", "실적", ("어닝서프라이즈", "흑자전환", "영업이익", "실적", "매출")),
    ("contract", "공급계약", ("공급계약", "공급 계약", "납품계약", "수주")),
    ("policy", "정책", ("규제완화", "정책수혜", "국책과제")),
    ("capital", "증자·배당", ("무상증자", "유상증자", "자사주", "배당")),
]

THEME_NAMES = {key: name for key, name, _keywords in THEMES}
CATALYST_KEYS = {"ma", "earnings", "contract", "policy", "capital"}
# When both are present, keep the more specific sector.
PREFERRED = {
    "ai_semi": {"ai"},
    "battery": {"auto"},
    "nuclear": {"power"},
}
LEGACY_KEYS = {
    "humanoid": "robot",
    "hbm": "ai_semi",
    "foundry": "ai_semi",
    "semiconductor": "ai_semi",
}

# Naver industry name -> theme key. Ambiguous groups (화학, 기계, 전기장비) are
# omitted so a battery name is not labeled 화학, and a chemical name is not
# labeled 2차전지, unless a same-industry limit-up already has that theme.
INDUSTRY_THEMES = {
    "반도체와반도체장비": "ai_semi",
    "제약": "bio",
    "생물공학": "bio",
    "생명과학도구및서비스": "bio",
    "건강관리장비와용품": "bio",
    "건강관리업체및서비스": "bio",
    "건강관리기술": "bio",
    "우주항공과국방": "defense",
    "조선": "ship",
    "화장품": "cosmetic",
    "게임엔터테인먼트": "game",
    "방송과엔터테인먼트": "ent",
    "자동차": "auto",
    "자동차부품": "auto",
    "철강": "steel",
    "은행": "finance",
    "증권": "finance",
    "생명보험": "finance",
    "손해보험": "finance",
    "카드": "finance",
    "기타금융": "finance",
    "건설": "construction",
    "건축자재": "construction",
    "건축제품": "construction",
    "디스플레이패널": "display",
    "디스플레이장비및부품": "display",
    "식품": "food",
    "음료": "food",
    "전기유틸리티": "power",
    "복합유틸리티": "power",
    "부동산": "reit",
}

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

# Short words that show up in unrelated headlines and must not glue themes together.
GENERIC = STOPWORDS | {
    "삼성",
    "현대",
    "기대감",
    "수혜주",
    "상한",
    "급등세",
    "특징",
    "재료",
    "모멘텀",
    "신고가",
    "거래량",
    "외국인",
    "기관",
    "매수",
    "매도",
    "연속",
    "주주",
    "지분",
    "인수",
    "합병",
    "배당",
    "증자",
    "매출",
    "영업",
    "이익",
    "흑자",
    "적자",
    "수주",
    "납품",
    "협약",
    "양산",
    "특허",
    "개발",
    "생산",
    "수출",
    "일본",
    "유럽",
    "증권",
    "테마",
    "이슈",
    "일정",
    "발표",
    "수행",
    "방문",
    "증가",
    "확대",
    "축소",
    "돌파",
    "달성",
    "선정",
    "관련",
    "상승세",
    "강세세",
    "상한가",
    "이유",
    "미확인",
}


def clip(text: str, limit: int = 52) -> str:
    text = text.strip(" ·…")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def canonical_key(key: str | None) -> str | None:
    if not key:
        return None
    key = LEGACY_KEYS.get(key, key)
    if key in THEME_NAMES:
        return key
    return None


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


def _choose_hit(hits: list[tuple[int, int, str, str, str]], sector_only: bool = False):
    if sector_only:
        hits = [hit for hit in hits if hit[2] not in CATALYST_KEYS]
    if not hits:
        return None
    keys = {hit[2] for hit in hits}
    dropped: set[str] = set()
    for winner, losers in PREFERRED.items():
        if winner in keys:
            dropped |= losers & keys
    sector = [hit for hit in hits if hit[2] not in CATALYST_KEYS and hit[2] not in dropped]
    pool = sector or [hit for hit in hits if hit[2] not in dropped]
    if not pool:
        pool = hits
    pool.sort(key=lambda hit: (hit[1], hit[0]))
    return pool[0]


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


def classify_text(text: str, stock_name: str, sector_only: bool = False) -> dict[str, Any] | None:
    """Dictionary theme from real text, or None when no keyword is present."""
    haystack = strip_name(text, stock_name)
    chosen = _choose_hit(keyword_hits(haystack), sector_only=sector_only)
    if not chosen:
        return None
    _pos, _neg, key, name, keyword = chosen
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


def distinctive_tokens(text: str, stock_name: str) -> list[str]:
    out = []
    for token in content_tokens(text, stock_name):
        if token in GENERIC or token.isdigit() or len(token) < 2:
            continue
        out.append(token)
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


def _evidence_text(event: dict[str, Any]) -> str:
    hint = event.get("themeHint") or {}
    parts = []
    for value in (event.get("reason"), event.get("sourceTitle"), event.get("articleTitle"), hint.get("reason")):
        if value and value != REASON_MISSING:
            parts.append(str(value))
    return "\n".join(parts)


def _resolve_grounded(event: dict[str, Any]) -> dict[str, Any] | None:
    """Theme taken from a real sentence. Missing news returns None."""
    if not event.get("reasonFound"):
        return None
    text = _evidence_text(event)
    name = event.get("name") or ""
    classified = classify_text(text, name) if text else None
    if classified:
        classified["tokens"] = distinctive_tokens(text, name)
        return classified
    hint = event.get("themeHint") or {}
    key = canonical_key(hint.get("key"))
    if hint.get("type") == "dict" and key:
        return {"type": "dict", "key": key, "name": THEME_NAMES[key], "keyword": hint.get("keyword"), "tokens": []}
    tokens = distinctive_tokens(text, name)
    if not tokens:
        tokens = [token for token in (hint.get("tokens") or []) if token not in GENERIC and not str(token).isdigit()]
    if tokens:
        return {"type": "custom", "key": None, "name": None, "keyword": tokens[0], "tokens": tokens[:6]}
    reason = event.get("reason") or ""
    if reason and reason != REASON_MISSING:
        return {"type": "custom", "key": None, "name": clip(reason, 18), "keyword": None, "tokens": []}
    return None


def _day_distance(left: str, right: str, day_index: dict[str, int]) -> int:
    if left in day_index and right in day_index:
        return abs(day_index[left] - day_index[right])
    try:
        return abs((date.fromisoformat(left) - date.fromisoformat(right)).days)
    except ValueError:
        return 99


def _infer_target(
    event: dict[str, Any],
    grounded: list[dict[str, Any]],
    by_code: dict[str, list[dict[str, Any]]],
    day_index: dict[str, int],
    history: dict[str, dict[str, str]],
) -> tuple[str, Any] | None:
    """Pick a theme for a stock whose reason sentence is missing.

    The return is ('family', key), ('event', peer), or ('industry', name).
    None leaves the stock on its own. No reason sentence is created.
    """
    name = event.get("name") or ""
    article = "\n".join(
        part
        for part in (event.get("articleTitle"), event.get("sourceTitle"), event.get("articleBody"))
        if part
    )
    if article:
        hit = classify_text(article, name, sector_only=True)
        if hit:
            return ("family", hit["key"])
    own = by_code.get(event["code"]) or []
    if own:
        nearest = min(own, key=lambda item: (_day_distance(event["date"], item["date"], day_index), item["date"]))
        return ("event", nearest)
    previous = history.get(event["code"]) or {}
    previous_key = canonical_key(previous.get("key"))
    if previous_key:
        return ("family", previous_key)
    industry_code = str(event.get("industryCode") or "")
    industry_name = str(event.get("industry") or "")
    peers: list[tuple[int, dict[str, Any]]] = []
    if industry_code:
        for other in grounded:
            if other["code"] == event["code"] or str(other.get("industryCode") or "") != industry_code:
                continue
            distance = _day_distance(event["date"], other["date"], day_index)
            if distance <= 3:
                peers.append((distance, other))
    if peers:
        scores: dict[str, int] = defaultdict(int)
        for distance, other in peers:
            key = (other.get("_resolved") or {}).get("key")
            if key:
                scores[key] += 4 - min(distance, 3)
        if scores:
            ranked = sorted(scores, key=lambda key: (scores[key], key), reverse=True)
            if len(ranked) == 1 or scores[ranked[0]] > scores[ranked[1]]:
                return ("family", ranked[0])
        nearest = min(peers, key=lambda item: (item[0], item[1]["date"], item[1]["code"]))[1]
        return ("event", nearest)
    if industry_code:
        scores = defaultdict(int)
        for other in grounded:
            if str(other.get("industryCode") or "") != industry_code:
                continue
            key = (other.get("_resolved") or {}).get("key")
            if key:
                scores[key] += 1
        if scores:
            ranked = sorted(scores, key=lambda key: (scores[key], key), reverse=True)
            # One far-away limit-up is not enough to relabel the whole industry.
            if scores[ranked[0]] >= 2 and (len(ranked) == 1 or scores[ranked[0]] > scores[ranked[1]]):
                return ("family", ranked[0])
    mapped = INDUSTRY_THEMES.get(industry_name)
    if mapped:
        return ("family", mapped)
    if industry_name:
        return ("industry", industry_name)
    return None


def detail_cutoff(run_date: str) -> str:
    """First day of the detail window: the run date minus six calendar months."""
    year, month, day = (int(part) for part in run_date.split("-"))
    month -= 6
    if month <= 0:
        month += 12
        year -= 1
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(day, last_day)).isoformat()


def _as_sentence(text: str) -> str:
    """Keep a sourced line as one sentence. Do not add words."""
    cleaned = " ".join(text.split()).strip(" ·")
    if not cleaned:
        return ""
    if cleaned[-1] in ".!?…":
        return cleaned
    if cleaned.endswith("다"):
        return cleaned + "."
    return cleaned + "."


def issue_summary(name: str, reasons: list[str]) -> str:
    """One to three sentences copied from sourced reasons. No new facts."""
    counts: Counter[str] = Counter()
    order: list[str] = []
    for reason in reasons:
        cleaned = " ".join((reason or "").split()).strip()
        if not cleaned or cleaned == REASON_MISSING:
            continue
        if cleaned not in counts:
            order.append(cleaned)
        counts[cleaned] += 1
    if not order:
        return f"{name} 뉴스 근거 부족"
    ranked = sorted(order, key=lambda text: (-counts[text], order.index(text)))[:3]
    return " ".join(sentence for sentence in (_as_sentence(text) for text in ranked) if sentence)


def _in_detail(day: str, detail_from: str) -> bool:
    return not detail_from or day >= detail_from


def group_month(
    events: list[dict[str, Any]],
    year: int,
    month: int,
    config: dict[str, Any],
    trading_days: list[str],
    history: dict[str, dict[str, str]] | None = None,
    detail_from: str = "",
) -> dict[str, Any]:
    """Group one month. Similar themes share a number. Missing news can be inferred."""
    ordered = sorted(events, key=lambda item: (item["date"], item["code"]))
    history = history or {}
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
        event["inferred"] = False
        event["_resolved"] = _resolve_grounded(event)
        event.pop("_inferKey", None)
        event.pop("_inferName", None)
        node = (event["code"], event["date"])
        find(node)
        resolved = event["_resolved"]
        if not resolved:
            continue
        if resolved.get("key") in THEME_NAMES:
            union(node, ("family", resolved["key"]))
        reason_key = _norm_reason(event.get("reason") or "")
        if reason_key and reason_key != _norm_reason(REASON_MISSING):
            union(node, ("reason", reason_key))
        elif resolved.get("name"):
            union(node, ("custom-name", resolved["name"]))

    token_owner: dict[str, Any] = {}
    for event in ordered:
        resolved = event.get("_resolved") or {}
        if not resolved or resolved.get("key"):
            continue
        node = (event["code"], event["date"])
        for token in resolved.get("tokens") or []:
            if token in token_owner:
                union(node, token_owner[token])
            else:
                token_owner[token] = node

    grounded = [event for event in ordered if event.get("_resolved")]
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in grounded:
        by_code[event["code"]].append(event)
    day_index = {day: index for index, day in enumerate(trading_days)}

    for event in ordered:
        if event.get("_resolved"):
            continue
        target = _infer_target(event, grounded, by_code, day_index, history)
        if target is None:
            continue
        node = (event["code"], event["date"])
        kind, value = target
        event["inferred"] = True
        if kind == "family":
            event["_inferKey"] = value
            event["_inferName"] = THEME_NAMES[value]
            union(node, ("family", value))
        elif kind == "event":
            union(node, (value["code"], value["date"]))
        else:
            event["_inferKey"] = "industry:" + str(value)
            event["_inferName"] = str(value)
            union(node, ("industry", value))

    grouped_events: dict[Any, list[dict[str, Any]]] = {}
    for event in ordered:
        grouped_events.setdefault(find((event["code"], event["date"])), []).append(event)

    themes: list[dict[str, Any]] = []
    for rows in sorted(grouped_events.values(), key=lambda item: (item[0]["date"], item[0]["code"])):
        name, key, hint_type = _cluster_label(rows)
        sourced = [
            row.get("reason") or ""
            for row in rows
            if _in_detail(row["date"], detail_from) and row.get("reasonFound") and row.get("reason") not in {None, "", REASON_MISSING}
        ]
        in_window = any(_in_detail(row["date"], detail_from) for row in rows)
        chosen = {
            "id": f"{month}-{len(themes) + 1}",
            "seq": len(themes) + 1,
            "name": name,
            "hintType": hint_type,
            "key": key,
            "tokens": [],
            "keywords": [],
            "stocks": {},
            "reason": next((row.get("reason") for row in rows if row.get("reasonFound")), ""),
            "issueSummary": issue_summary(name, sourced) if in_window else "",
        }
        themes.append(chosen)
        for event in rows:
            resolved = event.get("_resolved") or {}
            keyword = resolved.get("keyword")
            if keyword and keyword not in chosen["keywords"]:
                chosen["keywords"].append(keyword)
            for token in resolved.get("tokens") or []:
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
            event["_detail"] = _in_detail(event["date"], detail_from)
            stock["events"].append(_public_event(event))

    public_themes = []
    for theme in themes:
        stocks = list(theme["stocks"].values())
        stocks.sort(key=lambda item: (item["dates"][0], item["code"]))
        has_signal = any(stock["aboveWeekly240"] for stock in stocks)
        count = len(stocks)
        inferred_stocks = sum(1 for stock in stocks if any(event.get("inferred") for event in stock["events"]))
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
                "inferredStocks": inferred_stocks,
                "issueSummary": theme["issueSummary"],
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
        detail = _in_detail(event["date"], detail_from)
        sourced_reason = event.get("reason") or REASON_MISSING
        row = {
            "code": event["code"],
            "name": event["name"],
            "market": event["market"],
            "close": event["close"],
            "changeRate": event["changeRate"],
            "detail": detail,
            "reason": sourced_reason if detail else "",
            "reasonSummary": sourced_reason if detail else "",
            "reasonFound": bool(event.get("reasonFound")) if detail else False,
            "inferred": bool(event.get("inferred")),
            "source": event.get("source") or "",
            "sourceTitle": (event.get("sourceTitle") or "") if detail else "",
            "sourceUrl": event.get("sourceUrl") or "",
            "themeId": theme["id"],
            "themeName": theme["name"],
            "themeSize": theme["size"],
            "issueSummary": theme["issueSummary"] if detail else "",
            "aboveWeekly240": bool(event.get("weekly240Signal")),
        }
        by_date.setdefault(event["date"], []).append(row)
    for rows in by_date.values():
        rows.sort(key=lambda item: (-item["changeRate"], item["code"]))

    grounded_by_code: dict[str, dict[str, str]] = {}
    for event in ordered:
        resolved = event.get("_resolved") or {}
        if resolved.get("key"):
            grounded_by_code[event["code"]] = {"key": resolved["key"], "name": resolved["name"]}
    for event in ordered:
        event.pop("_resolved", None)
        event.pop("_inferKey", None)
        event.pop("_inferName", None)
        event.pop("_detail", None)

    month_id = f"{year:04d}-{month:02d}"
    days = [day for day in trading_days if day.startswith(month_id)]
    inferred_codes = {event["code"] for event in ordered if event.get("inferred")}
    unknown_codes = {event["code"] for event in ordered if not event.get("reasonFound")}
    theme_name_of = {
        (stock["code"], event["date"]): theme["name"]
        for theme in public_themes
        for stock in theme["stocks"]
        for event in stock["events"]
    }
    leftover_codes = {
        event["code"]
        for event in ordered
        if not event.get("reasonFound") and theme_name_of.get((event["code"], event["date"])) == REASON_MISSING
    }
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
        "inferredStocks": len(inferred_codes),
        "unknownStocks": len(unknown_codes),
        "leftoverStocks": len(leftover_codes),
        "groundedByCode": grounded_by_code,
        "detailFrom": detail_from,
    }


def _cluster_label(rows: list[dict[str, Any]]) -> tuple[str, str | None, str]:
    for row in rows:
        resolved = row.get("_resolved") or {}
        if resolved.get("key") in THEME_NAMES and resolved.get("name"):
            return resolved["name"], resolved["key"], "dict"
    for row in rows:
        key = row.get("_inferKey")
        if key in THEME_NAMES and row.get("_inferName"):
            return row["_inferName"], key, "inferred"
    tokens: list[str] = []
    custom_name = None
    for row in rows:
        resolved = row.get("_resolved") or {}
        tokens.extend(resolved.get("tokens") or [])
        if resolved.get("name") and not custom_name:
            custom_name = resolved["name"]
    if tokens:
        name = Counter(tokens).most_common(1)[0][0]
        return name, "custom:" + name, "custom"
    if custom_name:
        return custom_name, None, "custom"
    for row in rows:
        if row.get("_inferName"):
            return row["_inferName"], row.get("_inferKey"), "inferred"
    return REASON_MISSING, None, "unknown"


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    detail = bool(event.get("_detail", True))
    reason = event.get("reason") or ""
    if detail and not reason:
        reason = REASON_MISSING
    return {
        "date": event["date"],
        "close": event["close"],
        "prevClose": event["prevClose"],
        "changeRate": event["changeRate"],
        "detail": detail,
        "reason": reason if detail else "",
        "reasonSummary": reason if detail else "",
        "reasonFound": bool(event.get("reasonFound")) if detail else False,
        "inferred": bool(event.get("inferred")),
        "source": event.get("source") or "",
        "sourceTitle": (event.get("sourceTitle") or "") if detail else "",
        "sourceUrl": event.get("sourceUrl") or "",
        "aboveWeekly240": bool(event.get("weekly240Signal")),
        "weekly240Above": bool(event.get("weekly240Above")),
        "weekly240Breakout": bool(event.get("weekly240Breakout")),
    }
