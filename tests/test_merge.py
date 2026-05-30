"""Idempotent merge: running the same extraction twice must not duplicate
people, aliases, or edges."""
from __future__ import annotations

from pathlib import Path

import pytest

from newsgraph.config import Settings
from newsgraph.db.repository import Repository
from newsgraph.extraction.extractor import Extractor, LLMClient
from newsgraph.models.extraction import (
    ArticleExtraction,
    PersonMention,
    RelationType,
    Relationship,
)
from newsgraph.pipeline import Pipeline
from newsgraph.scraping.base import ParsedArticle, SiteAdapter
from newsgraph.scraping.http_client import HTTPClient


class StubAdapter(SiteAdapter):
    """In-process site adapter — returns canned articles by URL."""

    name = "stub"

    def __init__(self, articles: dict[str, ParsedArticle]):
        self.articles = articles

    def listing_url(self, page: int) -> str:
        return f"stub://listing/{page}"

    def list_articles(self, html: str):
        return []

    def parse_article(self, html: str, url: str) -> ParsedArticle:
        return self.articles[url]


class StubExtractor(Extractor):
    """Returns canned extractions by URL. No LLM involved."""

    def __init__(self, payloads: dict[str, ArticleExtraction]):
        # Bypass the parent's __init__ — we don't need a real LLMClient.
        self.settings = Settings(disable_llm=False)  # not used
        self.llm = None  # type: ignore[assignment]
        self.payloads = payloads

    def extract(self, *, url: str, title: str, authors: list[str], body: str):
        return self.payloads[url]


class StubHTTP:
    """HTTPClient stand-in that hands the parser a non-empty HTML blob."""

    def get(self, url: str, *, use_cache: bool = True) -> str:
        return "<html><body>stub</body></html>"

    def close(self) -> None:  # pragma: no cover
        pass


def _make_pipeline(tmp_path: Path, articles, payloads) -> Pipeline:
    settings = Settings(db_path=tmp_path / "db.sqlite3", cache_dir=tmp_path / "cache", disable_llm=True)
    repo = Repository(settings.db_path)
    adapter = StubAdapter(articles)
    extractor = StubExtractor(payloads)
    http = StubHTTP()  # type: ignore[assignment]
    # Stub the adapter routing by replacing the adapters list.
    pl = Pipeline(settings=settings, repo=repo, http=http, extractor=extractor, adapters=[adapter])  # type: ignore[arg-type]
    # Override _adapter_for since URL won't be techcrunch.
    pl._adapter_for = lambda url: adapter  # type: ignore[assignment]
    return pl


@pytest.fixture
def stub_article() -> tuple[str, ParsedArticle, ArticleExtraction]:
    url = "stub://articles/1"
    article = ParsedArticle(
        url=url,
        title="Altman partners with Brockman again",
        published_at="2026-05-13T12:00:00Z",
        authors=["Tim Fernholz"],
        body_text="Sam Altman and Greg Brockman are working together at OpenAI. Altman leads the company.",
    )
    extraction = ArticleExtraction(
        people=[
            PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman", role="CEO of OpenAI"),
            PersonMention(surface_form="Greg Brockman", canonical_hint="Greg Brockman", role="President"),
            PersonMention(surface_form="Tim Fernholz", canonical_hint="Tim Fernholz", is_author=True),
            # Short form in the body — resolver should fold this into Sam Altman.
            PersonMention(surface_form="Altman", canonical_hint="Sam Altman"),
        ],
        relationships=[
            Relationship(
                source="Sam Altman",
                target="Greg Brockman",
                type=RelationType.PARTNERS_WITH,
                explanation="Altman and Brockman are working together at OpenAI.",
                supporting_quote="Sam Altman and Greg Brockman are working together at OpenAI.",
            ),
            Relationship(
                source="Tim Fernholz",
                target="Sam Altman",
                type=RelationType.REPORTS_ON,
                explanation="Tim Fernholz is the author reporting on Sam Altman.",
                supporting_quote="(byline)",
            ),
        ],
    )
    return url, article, extraction


def test_merge_inserts_expected_rows(tmp_path: Path, stub_article) -> None:
    url, article, extraction = stub_article
    pl = _make_pipeline(tmp_path, {url: article}, {url: extraction})
    stats = pl.process_article(url)
    # 3 distinct people; the "Altman" surface form folds into Sam Altman.
    assert stats.people_added == 3
    assert stats.edges_added == 2
    assert stats.mentions_added >= 3
    pl.close()


def test_merge_is_idempotent(tmp_path: Path, stub_article) -> None:
    url, article, extraction = stub_article
    pl = _make_pipeline(tmp_path, {url: article}, {url: extraction})
    first = pl.process_article(url)
    second = pl.process_article(url)

    # Second run: no new people, no new edges. Edges were replaced (deleted +
    # reinserted) — that count is non-zero.
    assert first.people_added == 3
    assert second.people_added == 0
    assert second.edges_added == 2  # re-inserted
    assert second.edges_replaced == 2  # the old ones we wiped

    # Verify only 3 people exist regardless of how many times we ran.
    pl.process_article(url)
    page = pl.repo.list_people(page=1, size=50)
    assert page.total == 3
    pl.close()


def test_merge_handles_author_not_in_extraction(tmp_path: Path) -> None:
    """If the LLM forgets the author, `_ensure_authors` should still record them."""
    url = "stub://articles/no-author"
    article = ParsedArticle(
        url=url, title="t", published_at=None, authors=["Alice Reporter"],
        body_text="Alice Reporter wrote this. Sam Altman is mentioned.",
    )
    extraction = ArticleExtraction(
        people=[PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman")],
        relationships=[],
    )
    pl = _make_pipeline(tmp_path, {url: article}, {url: extraction})
    stats = pl.process_article(url)
    # 2 people: Sam Altman + Alice Reporter (added by _ensure_authors).
    assert stats.people_added == 2
    page = pl.repo.list_people(page=1, size=50)
    names = {p.canonical_name for p in page.items}
    assert "Alice Reporter" in names
    pl.close()
