"""分析器单测：纯函数，不依赖数据库。"""

from __future__ import annotations

from app.services.analyzer import (
    analyze,
    extract_domains,
    find_term,
    judge_sentiment,
    own_domain_hit,
)


def test_own_brand_first_gets_position_one() -> None:
    text = "推荐用智标枪，广联达功能更全但价格高。"
    hits = analyze(text, own_brand="智标枪", competitors=["广联达"])
    assert [h.brand for h in hits] == ["智标枪", "广联达"]
    assert hits[0].position == 1
    assert hits[0].is_own is True
    assert hits[1].position == 2


def test_competitor_mentioned_first_shifts_position() -> None:
    text = "广联达是老牌产品，智标枪这两年增长很快。"
    hits = analyze(text, own_brand="智标枪", competitors=["广联达"])
    assert hits[0].brand == "广联达"
    assert hits[0].position == 1
    own = next(h for h in hits if h.is_own)
    assert own.position == 2


def test_no_mention_returns_empty() -> None:
    hits = analyze("这个问题我无法回答。", own_brand="智标枪", competitors=["广联达"])
    assert hits == []


def test_alias_is_recorded_as_matched_term() -> None:
    text = "RED 这个产品的模板挺多。"
    hits = analyze(text, own_brand="小红书", own_terms=["小红书", "RED"])
    assert len(hits) == 1
    assert hits[0].matched_term == "RED"
    assert hits[0].brand == "小红书"


def test_ascii_alias_does_not_match_inside_word() -> None:
    # CREDIT 里含 RED，不能算提及
    assert find_term("this is a credit card", "RED") == []
    assert find_term("i use red daily", "RED") == [6]


def test_occurrences_counted_but_position_uses_first() -> None:
    text = "智标枪不错，智标枪的模板也好用。"
    hits = analyze(text, own_brand="智标枪")
    assert len(hits) == 1
    assert hits[0].occurrences == 2
    assert hits[0].position == 1


def test_sentiment_positive_and_negative() -> None:
    positive = "智标枪口碑不错，排版校验很细致，值得推荐。"
    negative = "智标枪价格偏贵，稳定性较差，不建议小团队用。"
    assert judge_sentiment(positive, positive.find("智标枪")) == "positive"
    assert judge_sentiment(negative, negative.find("智标枪")) == "negative"


def test_sentiment_neutral_when_no_signal() -> None:
    text = "智标枪是一个产品。"
    assert judge_sentiment(text, text.find("智标枪")) == "neutral"


def test_extract_domains_from_urls_and_keywords() -> None:
    text = (
        "参考 https://www.zhihu.com/question/1 和 https://baike.baidu.com/item/x ，"
        "另外小红书上也有讨论。"
    )
    assert extract_domains(text) == [
        "zhihu.com",
        "baike.baidu.com",
        "xiaohongshu.com",
    ]


def test_extract_domains_deduplicates() -> None:
    text = "知乎上有答案 https://zhihu.com/a 和 https://www.zhihu.com/b"
    assert extract_domains(text) == ["zhihu.com"]


def test_own_domain_hit_matches_subdomain() -> None:
    assert own_domain_hit(["www.xiaohongshu.com"], ["xiaohongshu.com"]) is True
    assert own_domain_hit(["zhihu.com"], ["xiaohongshu.com"]) is False
