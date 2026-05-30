"""Entity resolution.

Per DECISIONS.md: normalize → exact-match → last-token within-article match →
LLM tiebreak only on collision. Keep it simple.

The resolver works in two phases:
  1. Within an article, the LLM has already given us a `canonical_hint` for each
     person. We treat that hint as authoritative for inner-article identity:
     two PersonMentions with the same `canonical_hint` collapse.
  2. Across articles, we use the normalized form to merge with existing people.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..db.repository import Repository
from ..models.extraction import PersonMention

_HONORIFICS = {
    "mr", "mrs", "ms", "mx", "dr", "prof", "sir", "lord", "lady", "rev", "fr",
}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def normalize(name: str) -> str:
    """Lowercase, strip accents, drop honorifics/suffixes, collapse whitespace."""
    if not name:
        return ""
    # Strip accents
    nfkd = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = s.lower()
    # Drop possessives ("altman's" → "altman")
    s = re.sub(r"['’]s\b", "", s)
    # Replace any non-alphanumeric with space
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = [
        t for t in s.split()
        if t not in _HONORIFICS and t not in _SUFFIXES and not (len(t) == 1)
    ]
    return " ".join(tokens)


@dataclass
class ResolvedPerson:
    """The output of resolving one PersonMention against the existing DB +
    article-local context."""

    person_id: int
    canonical_name: str
    is_new: bool  # we inserted a new people row
    promoted: bool  # we updated an existing canonical to a longer form
    alias_added: bool


class EntityResolver:
    """Stateful for one article — caches resolutions so the same surface form
    inside one article maps to the same person_id."""

    def __init__(self, repo: Repository, llm_disambiguate=None):
        self.repo = repo
        # llm_disambiguate is a callable taking (article_context, candidates,
        # surface_form) → person_id | None. Optional; if None we fall back to
        # "pick the most-mentioned candidate" which is good enough in practice.
        self.llm_disambiguate = llm_disambiguate
        self._article_cache: dict[str, ResolvedPerson] = {}

    def reset(self) -> None:
        self._article_cache.clear()

    def resolve(
        self,
        mention: PersonMention,
        *,
        article_people: list[PersonMention],
        article_context: str = "",
    ) -> ResolvedPerson:
        """Resolve one PersonMention to a person_id, inserting if needed."""
        surface = mention.surface_form.strip()
        hint = (mention.canonical_hint or "").strip()
        # canonical_hint is the LLM's claim about the real-world identity — trust
        # it as the lookup/canonical key when present (so "OpenAI's CEO" with hint
        # "Sam Altman" merges into Sam Altman instead of becoming a new row).
        # Surface form is what we record as the alias the article actually used.
        best = hint if hint else surface
        normalized_best = normalize(best)
        normalized_surface = normalize(surface)

        if not normalized_best:
            # Garbage in; treat as a unique-by-surface row to avoid silent loss.
            normalized_best = normalized_surface or f"unknown:{surface}"

        cache_key = normalized_best
        if cache_key in self._article_cache:
            cached = self._article_cache[cache_key]
            # We may still want to add the surface as an alias if it differs.
            alias_added = False
            if normalized_surface and normalized_surface != normalized_best:
                alias_added = self.repo.add_alias(
                    cached.person_id, surface, normalized_surface
                )
            return ResolvedPerson(
                person_id=cached.person_id,
                canonical_name=cached.canonical_name,
                is_new=False,
                promoted=False,
                alias_added=alias_added,
            )

        person_id: int | None = self.repo.find_person_by_normalized(normalized_best)

        # Reverse-promotion: when resolving a FULL name (multi-token), check
        # if a prior article created an unresolved short-form row for the same
        # last token (e.g. an earlier article only said "Altman"). If exactly
        # one such single-token candidate exists, merge into it. We require the
        # existing row to be single-token to keep this safe — we never collapse
        # two existing full names this way.
        if person_id is None and len(normalized_best.split()) > 1:
            last_token = normalized_best.split()[-1]
            candidates = [
                (cid, cname)
                for cid, cname in self.repo.candidates_by_last_token(last_token)
                if cname and len(normalize(cname).split()) == 1
            ]
            if len(candidates) == 1:
                person_id = candidates[0][0]

        # Last-token fallback: short forms like "Altman" only.
        if person_id is None and len(normalized_best.split()) == 1:
            last_token = normalized_best
            # Within-article match: does another mention in this article have
            # a longer name ending in this last token?
            siblings = [
                p for p in article_people
                if p.surface_form != mention.surface_form
                and last_token in normalize(p.canonical_hint or p.surface_form).split()
            ]
            if siblings:
                # Resolve the first sibling first (recursively), then alias to it.
                target = self.resolve(
                    siblings[0],
                    article_people=article_people,
                    article_context=article_context,
                )
                alias_added = self.repo.add_alias(
                    target.person_id, surface, normalized_surface
                )
                resolved = ResolvedPerson(
                    person_id=target.person_id,
                    canonical_name=target.canonical_name,
                    is_new=False,
                    promoted=False,
                    alias_added=alias_added,
                )
                self._article_cache[cache_key] = resolved
                return resolved

            # Cross-article last-token match against existing people.
            candidates = self.repo.candidates_by_last_token(last_token)
            if len(candidates) == 1:
                person_id = candidates[0][0]
            elif len(candidates) > 1:
                # Ambiguous. Use the LLM disambiguator if available; otherwise
                # we conservatively create a NEW person rather than guess.
                if self.llm_disambiguate is not None:
                    person_id = self.llm_disambiguate(
                        article_context, candidates, surface
                    )

        promoted = False
        is_new = False
        alias_added = False

        if person_id is None:
            person_id = self.repo.insert_person(best, normalized_best)
            is_new = True
            self.repo.add_alias(person_id, best, normalized_best)
            if normalized_surface and normalized_surface != normalized_best:
                alias_added = self.repo.add_alias(
                    person_id, surface, normalized_surface
                ) or alias_added
            canonical_name = best
        else:
            promoted = self.repo.maybe_promote_canonical(
                person_id, best, normalized_best
            )
            if self.repo.add_alias(person_id, best, normalized_best):
                alias_added = True
            if normalized_surface and normalized_surface != normalized_best:
                if self.repo.add_alias(person_id, surface, normalized_surface):
                    alias_added = True
            canonical_name = self.repo.get_person_canonical(person_id) or best

        resolved = ResolvedPerson(
            person_id=person_id,
            canonical_name=canonical_name,
            is_new=is_new,
            promoted=promoted,
            alias_added=alias_added,
        )
        self._article_cache[cache_key] = resolved
        return resolved
