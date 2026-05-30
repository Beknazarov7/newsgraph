"""Site adapter interface.

A new site (e.g. The Verge) means: subclass `SiteAdapter`, implement two
methods, register the instance in the pipeline. The rest of the pipeline
doesn't care which site an article came from.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ListingItem:
    """One article card pulled off a listing page."""

    url: str
    title: str | None = None


@dataclass
class ParsedArticle:
    """Output of `SiteAdapter.parse_article`. Everything downstream consumes this."""

    url: str
    title: str
    published_at: str | None  # ISO-8601 if available
    authors: list[str]  # canonical-looking names, e.g. ["Maxwell Zeff"]
    body_text: str  # plain text of the article body, paragraphs joined by \n\n
    raw_html_len: int = 0  # for debugging / fixtures


class SiteAdapter(ABC):
    """Implement for each site. Statelessness keeps adapters trivially testable."""

    name: str

    @abstractmethod
    def listing_url(self, page: int) -> str:
        """URL of the Nth listing page (1-indexed)."""

    @abstractmethod
    def list_articles(self, html: str) -> list[ListingItem]:
        """Pull article URLs (and titles where cheap) off a listing page."""

    @abstractmethod
    def parse_article(self, html: str, url: str) -> ParsedArticle:
        """Turn an article HTML page into structured fields + body text.

        Should be robust to small DOM drift: missing meta tags, missing author
        block, etc. should degrade gracefully rather than raise.
        """
