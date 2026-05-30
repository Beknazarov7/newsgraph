"""Pipeline orchestration.

Pure glue: fetch → adapter.parse → extractor → resolver → repository.merge.
The pipeline is site-agnostic; the adapter is what changes between sites.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Settings, get_settings
from .db.repository import Repository
from .extraction.extractor import Extractor
from .models.api import MergeStats
from .models.extraction import ArticleExtraction, PersonMention
from .resolution.resolver import EntityResolver
from .scraping.base import SiteAdapter
from .scraping.http_client import HTTPClient
from .scraping.techcrunch import TechCrunchAdapter

logger = logging.getLogger(__name__)

# Cap the context window we hand to the disambiguator. Full article bodies
# are wasteful here — the first ~2k chars almost always contain the lead +
# the first named reference, which is what disambiguation needs.
_DISAMBIG_CONTEXT_CHARS = 2000

_DISAMBIG_SYSTEM = """You are resolving an ambiguous short reference in a news article.

Given the article context and a list of candidate people whose last name matches
the short reference, pick the candidate that the article is actually talking about.
If none of the candidates fit (e.g. the article refers to a different person who
happens to share a last name), return null.

Be conservative. A wrong merge corrupts the graph; "null" creates a separate
person row that future articles can still consolidate.
"""

_DISAMBIG_TOOL = {
    "name": "pick_person",
    "description": "Pick the candidate person_id that matches the short reference, or null.",
    "input_schema": {
        "type": "object",
        "properties": {
            "person_id": {
                "type": ["integer", "null"],
                "description": "ID of the chosen candidate, or null if none fit.",
            },
            "reason": {"type": "string"},
        },
        "required": ["person_id", "reason"],
    },
}


@dataclass
class Pipeline:
    """Holds the per-process collaborators. Construct one per request handler."""

    settings: Settings
    repo: Repository
    http: HTTPClient
    extractor: Extractor
    adapters: list[SiteAdapter]

    @classmethod
    def default(cls, settings: Settings | None = None, repo: Repository | None = None) -> "Pipeline":
        s = settings or get_settings()
        r = repo or Repository(s.db_path)
        http = HTTPClient(
            user_agent=s.user_agent,
            rate_limit_s=s.request_rate_limit_s,
            timeout_s=s.request_timeout_s,
            max_retries=s.max_retries,
            cache_dir=s.cache_dir,
        )
        extractor = Extractor(s)
        return cls(settings=s, repo=r, http=http, extractor=extractor, adapters=[TechCrunchAdapter()])

    def close(self) -> None:
        self.http.close()
        self.repo.close()

    # ---- adapter routing ----------------------------------------------------

    def _adapter_for(self, url: str) -> SiteAdapter | None:
        # Right now we only support TechCrunch; routing is a single hostname
        # check, but kept here so adding adapters is "register + match".
        if "techcrunch.com" in url:
            return next((a for a in self.adapters if a.name == "techcrunch"), None)
        return None

    # ---- single article -----------------------------------------------------

    def process_article(self, url: str, *, use_cache: bool = True) -> MergeStats:
        """Fetch, parse, extract, resolve, merge one article. Idempotent."""
        stats = MergeStats()
        adapter = self._adapter_for(url)
        if adapter is None:
            stats.errors.append(f"no adapter for url: {url}")
            return stats

        try:
            html = self.http.get(url, use_cache=use_cache)
        except Exception as e:
            stats.errors.append(f"fetch failed for {url}: {e}")
            return stats

        parsed = adapter.parse_article(html, url)
        if not parsed.body_text:
            stats.errors.append(f"empty body for {url}")
            return stats

        try:
            extraction = self.extractor.extract(
                url=url,
                title=parsed.title,
                authors=parsed.authors,
                body=parsed.body_text,
            )
        except Exception as e:
            stats.errors.append(f"extraction failed for {url}: {e}")
            return stats

        # Make sure byline authors are in the people list even if the LLM
        # forgot to add them.
        extraction = _ensure_authors(extraction, parsed.authors)

        # All writes for one article go in one transaction.
        with self.repo.transaction():
            stats.edges_replaced = self.repo.replace_article_facts(url)
            self.repo.upsert_article(
                url=url,
                title=parsed.title,
                published_at=parsed.published_at,
                body=parsed.body_text,
            )
            stats.articles_processed = 1
            self._merge_extraction(url, extraction, stats, article_context=parsed.body_text)
        return stats

    # ---- listing crawl ------------------------------------------------------

    def rescan(self, pages: int) -> MergeStats:
        total = MergeStats()
        adapter = next(a for a in self.adapters if a.name == "techcrunch")
        seen_urls: set[str] = set()
        for page in range(1, pages + 1):
            listing_url = adapter.listing_url(page)
            try:
                listing_html = self.http.get(listing_url, use_cache=False)
            except Exception as e:
                total.errors.append(f"listing page {page}: {e}")
                continue
            items = adapter.list_articles(listing_html)
            logger.info("rescan page %d: %d items", page, len(items))
            for item in items:
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url)
                article_stats = self.process_article(item.url)
                _accumulate(total, article_stats)
        return total

    # ---- merge --------------------------------------------------------------

    def _build_disambiguator(self):
        """Return a callable for EntityResolver, or None if LLM is disabled.

        Signature: (article_context, candidates, surface_form) -> person_id | None
        where candidates is list[(id, canonical_name)].
        """
        if self.settings.disable_llm:
            return None

        def _disambiguate(article_context: str, candidates: list, surface_form: str):
            if not candidates:
                return None
            ctx = (article_context or "")[:_DISAMBIG_CONTEXT_CHARS]
            cand_lines = "\n".join(f"  - id={cid}: {name}" for cid, name in candidates)
            user = (
                f"Short reference in the article: \"{surface_form}\"\n\n"
                f"Candidates (existing people whose last token matches):\n{cand_lines}\n\n"
                f"Article context (first {_DISAMBIG_CONTEXT_CHARS} chars):\n\"\"\"\n{ctx}\n\"\"\""
            )
            try:
                out = self.extractor.llm.call_tool(_DISAMBIG_SYSTEM, user, _DISAMBIG_TOOL)
            except Exception as e:
                logger.warning("disambiguator failed for %r: %s", surface_form, e)
                return None
            pid = out.get("person_id")
            if pid is None:
                return None
            valid_ids = {cid for cid, _ in candidates}
            if pid not in valid_ids:
                logger.warning(
                    "disambiguator returned out-of-set id=%s for %r (candidates=%s)",
                    pid, surface_form, sorted(valid_ids),
                )
                return None
            return pid

        return _disambiguate

    def _merge_extraction(
        self,
        url: str,
        extraction: ArticleExtraction,
        stats: MergeStats,
        *,
        article_context: str = "",
    ) -> None:
        resolver = EntityResolver(self.repo, llm_disambiguate=self._build_disambiguator())
        # First pass: resolve every person mentioned.
        resolved_by_surface: dict[str, int] = {}
        canonical_by_id: dict[int, str] = {}
        for mention in extraction.people:
            res = resolver.resolve(
                mention,
                article_people=extraction.people,
                article_context=article_context,
            )
            resolved_by_surface[mention.surface_form] = res.person_id
            canonical_by_id[res.person_id] = res.canonical_name
            if res.is_new:
                stats.people_added += 1
            elif res.promoted:
                stats.people_updated += 1
            if res.alias_added:
                stats.aliases_added += 1
            if self.repo.add_mention(
                person_id=res.person_id,
                article_url=url,
                surface_form=mention.surface_form,
                role_hint=mention.role,
                is_author=mention.is_author,
            ):
                stats.mentions_added += 1

        # Second pass: edges. Skip edges whose endpoints we couldn't resolve.
        for edge in extraction.relationships:
            sid = resolved_by_surface.get(edge.source)
            tid = resolved_by_surface.get(edge.target)
            if sid is None or tid is None or sid == tid:
                continue
            if self.repo.add_edge(
                source_id=sid,
                target_id=tid,
                type_=edge.type.value,
                explanation=edge.explanation,
                supporting_quote=edge.supporting_quote,
                article_url=url,
            ):
                stats.edges_added += 1


def _accumulate(total: MergeStats, delta: MergeStats) -> None:
    total.articles_processed += delta.articles_processed
    total.articles_skipped += delta.articles_skipped
    total.people_added += delta.people_added
    total.people_updated += delta.people_updated
    total.aliases_added += delta.aliases_added
    total.mentions_added += delta.mentions_added
    total.edges_added += delta.edges_added
    total.edges_replaced += delta.edges_replaced
    total.errors.extend(delta.errors)


def _ensure_authors(extraction: ArticleExtraction, authors: list[str]) -> ArticleExtraction:
    """Authors from the byline are ground truth; make sure they show up as
    is_author=True even if the LLM omitted or mis-flagged them."""
    if not authors:
        return extraction
    surfaces = {p.surface_form: p for p in extraction.people}
    changed = False
    for name in authors:
        if name in surfaces:
            if not surfaces[name].is_author:
                surfaces[name] = surfaces[name].model_copy(update={"is_author": True})
                changed = True
        else:
            surfaces[name] = PersonMention(
                surface_form=name,
                canonical_hint=name,
                role="author",
                is_author=True,
            )
            changed = True
    if not changed:
        return extraction
    return ArticleExtraction(
        people=list(surfaces.values()),
        relationships=extraction.relationships,
    )
