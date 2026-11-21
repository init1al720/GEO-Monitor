"""分析器：把一段回答文本变成「谁被提到、第几个、什么情绪」。

全是纯函数，不碰数据库，所以可以脱离线上库跑单测。
判定口径见 docs/04-supabase-contract.md 第 6 节。

v0.1 只做规则匹配，不调 LLM：快、免费、结果可回溯。
mentions.verified_by 字段留了 'llm' 的位置，将来加一层复核不用改表。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

# 情绪窗口：命中词前后各取多少字。太长会把别家的评价算进来，40 字大致是一句话
_SENTIMENT_WINDOW = 40

_POSITIVE = (
    "领先",
    "首选",
    "推荐",
    "优秀",
    "好评",
    "口碑",
    "优势",
    "值得",
    "可靠",
    "专业",
    "稳定",
    "性价比",
    "头部",
    "第一梯队",
    "主流",
    "强劲",
    "出色",
)

_NEGATIVE = (
    "落后",
    "劣势",
    "较差",
    "差评",
    "投诉",
    "负面",
    "争议",
    "缺陷",
    "不足",
    "风险",
    "不稳定",
    "昂贵",
    "偏贵",
    "难用",
    "复杂",
    "处罚",
    "抄袭",
    "下滑",
    "不建议",
)

# 常见信源：回答里往往只写中文名不写链接，靠关键词兜住
_SOURCE_KEYWORDS: dict[str, str] = {
    "知乎": "zhihu.com",
    "小红书": "xiaohongshu.com",
    "百度百科": "baike.baidu.com",
    "维基百科": "zh.wikipedia.org",
    "微信公众号": "mp.weixin.qq.com",
    "抖音": "douyin.com",
    "B站": "bilibili.com",
    "哔哩哔哩": "bilibili.com",
    "CSDN": "csdn.net",
    "掘金": "juejin.cn",
    "简书": "jianshu.com",
    "36氪": "36kr.com",
    "搜狐": "sohu.com",
    "新浪": "sina.com.cn",
    "网易": "163.com",
    "腾讯网": "qq.com",
    "澎湃": "thepaper.cn",
}

_URL_RE = re.compile(r"https?://[^\s<>\"'）)】，。；、]+", re.IGNORECASE)


@dataclass(frozen=True)
class Detection:
    """一次品牌提及的判定结果，字段与 mentions 表对应。"""

    brand: str
    is_own: bool
    matched_term: str
    position: int
    sentiment: str
    occurrences: int
    first_index: int


def normalize(text: str) -> str:
    """全角转半角、英文转小写。

    只做长度不变的变换，因为后面要靠字符下标判定「谁先出现」，
    一旦删空白或补字符，位次就会错位。
    """
    return unicodedata.normalize("NFKC", text).lower()


def _term_pattern(term: str) -> re.Pattern[str]:
    """英文/数字结尾的词加边界，避免 RED 命中 CREDIT。中文不需要边界。"""
    escaped = re.escape(term)
    if term[:1].isascii() and term[:1].isalnum():
        escaped = rf"(?<![a-z0-9]){escaped}"
    if term[-1:].isascii() and term[-1:].isalnum():
        escaped = rf"{escaped}(?![a-z0-9])"
    return re.compile(escaped)


def find_term(text: str, term: str) -> list[int]:
    """返回 term 在 text 中所有出现的起始下标（text 需已 normalize）。"""
    if not term:
        return []
    return [m.start() for m in _term_pattern(normalize(term)).finditer(text)]


def judge_sentiment(text: str, index: int, window: int = _SENTIMENT_WINDOW) -> str:
    """取命中词前后 window 个字符，按正负词数量判情绪。平局算中性。"""
    start = max(0, index - window)
    end = min(len(text), index + window)
    segment = text[start:end]
    pos = sum(1 for w in _POSITIVE if w in segment)
    neg = sum(1 for w in _NEGATIVE if w in segment)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def analyze(
    answer: str,
    *,
    own_brand: str,
    own_terms: list[str] | None = None,
    competitors: list[str] | None = None,
    window: int = _SENTIMENT_WINDOW,
) -> list[Detection]:
    """识别回答里的全部品牌提及，并按「第一次出现的先后」给位次。

    位次规则：谁的回答里第一次出现在最前面，谁就是第 1 位。
    同一回答里提到多次只算一次提及，但 occurrences 会记下来。
    """
    if not answer or not answer.strip():
        return []

    text = normalize(answer)
    subjects: list[tuple[str, bool, list[str]]] = []

    own_terms_clean = [t for t in (own_terms or []) if t]
    if own_brand and own_brand not in own_terms_clean:
        own_terms_clean.insert(0, own_brand)
    if own_terms_clean:
        subjects.append((own_brand, True, own_terms_clean))

    for name in competitors or []:
        if name and name != own_brand:
            subjects.append((name, False, [name]))

    hits: list[Detection] = []
    for brand, is_own, terms in subjects:
        best_index: int | None = None
        best_term = ""
        total = 0
        for term in terms:
            positions = find_term(text, term)
            if not positions:
                continue
            total += len(positions)
            if best_index is None or positions[0] < best_index:
                best_index = positions[0]
                best_term = term
        if best_index is None:
            continue
        hits.append(
            Detection(
                brand=brand,
                is_own=is_own,
                matched_term=best_term,
                position=0,  # 占位，下面按先后重排
                sentiment=judge_sentiment(text, best_index, window),
                occurrences=total,
                first_index=best_index,
            )
        )

    # 位次：按首次出现下标升序，1 开始；同下标时自家优先
    ordered = sorted(hits, key=lambda d: (d.first_index, not d.is_own))
    return [
        Detection(
            brand=d.brand,
            is_own=d.is_own,
            matched_term=d.matched_term,
            position=idx,
            sentiment=d.sentiment,
            occurrences=d.occurrences,
            first_index=d.first_index,
        )
        for idx, d in enumerate(ordered, start=1)
    ]


def extract_domains(answer: str) -> list[str]:
    """抽取回答引用的域名：显式链接 + 常见信源关键词。去重保序。"""
    if not answer:
        return []

    found: list[str] = []
    for match in _URL_RE.finditer(answer):
        host = urlparse(match.group(0)).hostname or ""
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            found.append(host)

    for keyword, domain in _SOURCE_KEYWORDS.items():
        if keyword in answer:
            found.append(domain)

    return list(dict.fromkeys(found))


def own_domain_hit(domains: list[str], own_domains: list[str]) -> bool:
    """回答是否引用了自家域名（含子域）。"""
    for d in domains:
        for own in own_domains:
            own = own.lower().lstrip(".")
            if d == own or d.endswith("." + own):
                return True
    return False
