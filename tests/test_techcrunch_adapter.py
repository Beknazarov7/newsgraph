"""Parser tests against saved HTML fixtures."""
from __future__ import annotations

from newsgraph.scraping.techcrunch import TechCrunchAdapter


def test_listing_url_pagination() -> None:
    a = TechCrunchAdapter()
    assert a.listing_url(1) == "https://techcrunch.com/tag/openai/"
    assert a.listing_url(2) == "https://techcrunch.com/tag/openai/page/2/"


def test_list_articles_filters_to_posts(listing_html: str) -> None:
    a = TechCrunchAdapter()
    items = a.list_articles(listing_html)
    assert len(items) > 10
    for it in items:
        # Article URLs are dated; podcasts/videos must be filtered out.
        assert "/podcast/" not in it.url
        assert "/video/" not in it.url
        assert "techcrunch.com/20" in it.url


def test_list_articles_dedupes(listing_html: str) -> None:
    a = TechCrunchAdapter()
    items = a.list_articles(listing_html)
    urls = [i.url for i in items]
    assert len(urls) == len(set(urls))


def test_parse_article_who_trusts(article_html_who_trusts: str) -> None:
    a = TechCrunchAdapter()
    art = a.parse_article(
        article_html_who_trusts, "https://techcrunch.com/2026/05/13/who-trusts-sam-altman/"
    )
    assert art.title == "Who trusts Sam Altman?"
    assert art.authors == ["Tim Fernholz"], art.authors
    assert art.published_at is not None and art.published_at.startswith("2026-05-13")
    # Body should include the opening sentence about Sam Altman.
    assert "Sam Altman" in art.body_text
    assert len(art.body_text) > 1000


def test_parse_article_robust_to_missing_meta(article_html_musk_texts: str) -> None:
    a = TechCrunchAdapter()
    art = a.parse_article(
        article_html_musk_texts,
        "https://techcrunch.com/2026/05/04/elon-musk-sent-ominous-texts-to-greg-brockman-sam-altman-after-asking-for-a-settlement-openai-claims/",
    )
    assert art.title
    assert art.body_text
    # Even if author parsing fails we should not raise.
    assert isinstance(art.authors, list)
