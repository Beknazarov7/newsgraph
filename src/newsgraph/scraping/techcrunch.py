"""TechCrunch adapter.

Selectors verified against real HTML saved in tests/fixtures/. Listing pages
use `.loop-card` blocks; article pages use the `wp-block-tc23-*` block
hierarchy. We avoid scraping anything that's not a "post-type-post" card to
exclude podcasts and videos, neither of which has prose to extract from.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import ListingItem, ParsedArticle, SiteAdapter

LISTING_BASE = "https://techcrunch.com/tag/openai/"
# Article URLs look like https://techcrunch.com/YYYY/MM/DD/slug/
_ARTICLE_RE = re.compile(r"^https?://techcrunch\.com/\d{4}/\d{2}/\d{2}/[^/]+/?$")


class TechCrunchAdapter(SiteAdapter):
    name = "techcrunch"

    def listing_url(self, page: int) -> str:
        if page <= 1:
            return LISTING_BASE
        return f"{LISTING_BASE}page/{page}/"

    def list_articles(self, html: str) -> list[ListingItem]:
        soup = BeautifulSoup(html, "lxml")
        items: list[ListingItem] = []
        seen: set[str] = set()
        # `loop-card--post-type-post` excludes podcasts/videos that share the
        # same class root. Belt-and-suspenders: also URL-shape filter.
        for card in soup.select(".loop-card.loop-card--post-type-post"):
            a = card.select_one("a.loop-card__title-link")
            if not a:
                continue
            href_attr = a.get("href")
            if not isinstance(href_attr, str) or not _ARTICLE_RE.match(href_attr):
                continue
            href = href_attr.split("#")[0].split("?")[0]
            if href in seen:
                continue
            seen.add(href)
            items.append(ListingItem(url=href, title=a.get_text(strip=True) or None))
        return items

    def parse_article(self, html: str, url: str) -> ParsedArticle:
        soup = BeautifulSoup(html, "lxml")

        # ---- title ----------------------------------------------------------
        title_el = soup.select_one("h1.article-hero__title") or soup.select_one(
            "h1.wp-block-post-title"
        )
        if title_el is None:
            og = soup.select_one('meta[property="og:title"]')
            og_val = og.get("content") if og else None
            title = og_val.split(" | TechCrunch")[0] if isinstance(og_val, str) else ""
        else:
            title = title_el.get_text(strip=True)

        # ---- published_at ---------------------------------------------------
        published_at: str | None = None
        time_el = soup.select_one("time[datetime]")
        if time_el is not None:
            dt = time_el.get("datetime")
            if isinstance(dt, str):
                published_at = dt
        if not published_at:
            meta = soup.select_one('meta[property="article:published_time"]')
            if meta is not None:
                mc = meta.get("content")
                if isinstance(mc, str):
                    published_at = mc

        # ---- authors --------------------------------------------------------
        authors: list[str] = []
        for card in soup.select(".wp-block-tc23-author-card"):
            link = card.select_one(".wp-block-tc23-author-card-name a[href*='/author/']")
            if link is None:
                continue
            name = link.get_text(strip=True)
            # The same author block also contains a "View Bio" link pointing at
            # /author/; we picked the one inside the *-name wrapper which holds
            # the real display name.
            if name and name.lower() != "view bio" and name not in authors:
                authors.append(name)
        if not authors:
            # Fallback: TC pages without the modern author-card block expose
            # a single `meta[name=author]` whose value joins co-authors with
            # ", ". Split on comma to recover the individual names.
            for m in soup.select('meta[name="author"]'):
                content = m.get("content")
                if not isinstance(content, str):
                    continue
                for name in (n.strip() for n in content.split(",")):
                    if name and name not in authors:
                        authors.append(name)

        # ---- body text ------------------------------------------------------
        body_el = soup.select_one("div.entry-content.wp-block-post-content") or soup.select_one(
            ".entry-content"
        )
        paragraphs: list[str] = []
        if body_el is not None:
            # Drop UI islands that appear inside `entry-content` on TC pages
            # (newsletter promos, related-article boxes). They're wrapped in
            # blocks with `wp-block-techcrunch-*` or `is-style-newsletter-cta`.
            for junk in body_el.select(
                ".wp-block-techcrunch-newsletter-cta, .wp-block-techcrunch-related-articles, "
                ".wp-block-techcrunch-storyline, figure, aside, .ad-unit"
            ):
                junk.decompose()
            for p in body_el.find_all(["p", "h2", "h3", "li"]):
                text = p.get_text(" ", strip=True)
                if text:
                    paragraphs.append(text)

        body_text = "\n\n".join(paragraphs)

        return ParsedArticle(
            url=url,
            title=title or "",
            published_at=published_at,
            authors=authors,
            body_text=body_text,
            raw_html_len=len(html),
        )

    def absolute_url(self, href: str) -> str:
        return urljoin(LISTING_BASE, href)
