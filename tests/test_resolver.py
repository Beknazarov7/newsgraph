"""Entity resolution: normalization edge cases + within-article last-token
merging + cross-article merge."""
from __future__ import annotations

from newsgraph.db.repository import Repository
from newsgraph.models.extraction import PersonMention
from newsgraph.resolution.resolver import EntityResolver, normalize


def test_normalize_strips_honorifics_and_accents() -> None:
    assert normalize("Mr. Sam Altman") == "sam altman"
    assert normalize("Sam Altman, Jr.") == "sam altman"
    assert normalize("Léa Renard") == "lea renard"
    assert normalize("ALTMAN'S") == "altman"


def test_normalize_handles_empty_and_punctuation_only() -> None:
    assert normalize("") == ""
    assert normalize("???") == ""


def test_within_article_last_token_merge(tmp_db_path) -> None:
    repo = Repository(tmp_db_path)
    res = EntityResolver(repo)
    mentions = [
        PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman"),
        PersonMention(surface_form="Altman", canonical_hint=None),
    ]
    full = res.resolve(mentions[0], article_people=mentions)
    short = res.resolve(mentions[1], article_people=mentions)
    assert full.person_id == short.person_id, "short form should fold into full name"
    repo.close()


def test_role_descriptor_merges_via_canonical_hint(tmp_db_path) -> None:
    """The brief's three-way merge case: 'Sam Altman', 'Altman', and a role
    descriptor like "OpenAI's CEO" should all collapse into one person.

    The third form is interesting: surface_form is *longer in characters* than
    the canonical_hint, so a naive max-by-length tie-break would pick the role
    phrase as the lookup key and create a new row. We rely on canonical_hint
    being authoritative whenever the LLM sets it.
    """
    repo = Repository(tmp_db_path)
    repo.upsert_article("u1", "t1", None, "b1")
    repo.upsert_article("u2", "t2", None, "b2")

    r1 = EntityResolver(repo)
    sam = r1.resolve(
        PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman"),
        article_people=[PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman")],
    )

    r2 = EntityResolver(repo)
    role_mention = PersonMention(surface_form="OpenAI's CEO", canonical_hint="Sam Altman")
    role = r2.resolve(role_mention, article_people=[role_mention])

    assert role.person_id == sam.person_id, "role descriptor should merge via canonical_hint"
    # The surface form ("OpenAI's CEO") should be recorded as an alias so the
    # evaluator can see what was merged.
    aliases = {
        r["surface_form"]
        for r in repo._conn.execute(
            "SELECT surface_form FROM aliases WHERE person_id = ?", (sam.person_id,)
        )
    }
    assert "OpenAI's CEO" in aliases
    repo.close()


def test_cross_article_canonical_promotion(tmp_db_path) -> None:
    """First article only mentions 'Altman' → row created with short name.
    Second article gives the full 'Sam Altman' → the canonical_name should be
    promoted on the same row."""
    repo = Repository(tmp_db_path)
    repo.upsert_article("u1", "t1", None, "body1")
    repo.upsert_article("u2", "t2", None, "body2")

    res1 = EntityResolver(repo)
    short_mention = PersonMention(surface_form="Altman", canonical_hint=None)
    r1 = res1.resolve(short_mention, article_people=[short_mention])

    res2 = EntityResolver(repo)
    full_mention = PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman")
    r2 = res2.resolve(full_mention, article_people=[full_mention])

    assert r1.person_id == r2.person_id
    assert r2.canonical_name == "Sam Altman"
    repo.close()


def test_distinct_people_dont_merge(tmp_db_path) -> None:
    repo = Repository(tmp_db_path)
    res = EntityResolver(repo)
    mentions = [
        PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman"),
        PersonMention(surface_form="Greg Brockman", canonical_hint="Greg Brockman"),
    ]
    a = res.resolve(mentions[0], article_people=mentions)
    b = res.resolve(mentions[1], article_people=mentions)
    assert a.person_id != b.person_id
    repo.close()


def test_ambiguous_last_token_creates_new_when_no_llm(tmp_db_path) -> None:
    """If two people share a last token and there's no LLM disambiguator, the
    safe default is to create a NEW person rather than guess."""
    repo = Repository(tmp_db_path)
    res = EntityResolver(repo)
    # Seed two distinct Altmans in different articles.
    res.resolve(
        PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman"),
        article_people=[PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman")],
    )
    res2 = EntityResolver(repo)
    res2.resolve(
        PersonMention(surface_form="Jack Altman", canonical_hint="Jack Altman"),
        article_people=[PersonMention(surface_form="Jack Altman", canonical_hint="Jack Altman")],
    )
    # A bare "Altman" in a third article has nothing local to disambiguate
    # against → should create a new (unresolved) person, not silently pick one.
    res3 = EntityResolver(repo)
    r = res3.resolve(
        PersonMention(surface_form="Altman", canonical_hint=None),
        article_people=[PersonMention(surface_form="Altman", canonical_hint=None)],
    )
    assert r.is_new
    repo.close()


def test_ambiguous_last_token_uses_llm_disambiguator(tmp_db_path) -> None:
    """When an llm_disambiguate callable is provided, the resolver should call
    it on ambiguous cross-article last-token matches and merge into the chosen
    candidate."""
    repo = Repository(tmp_db_path)
    # Seed two distinct Altmans.
    seed1 = EntityResolver(repo)
    sam = seed1.resolve(
        PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman"),
        article_people=[PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman")],
    )
    seed2 = EntityResolver(repo)
    seed2.resolve(
        PersonMention(surface_form="Jack Altman", canonical_hint="Jack Altman"),
        article_people=[PersonMention(surface_form="Jack Altman", canonical_hint="Jack Altman")],
    )

    calls: list[tuple] = []

    def fake_disambiguate(article_context, candidates, surface_form):
        calls.append((article_context, tuple(candidates), surface_form))
        # Pick whichever candidate's name contains "Sam".
        for cid, name in candidates:
            if "Sam" in name:
                return cid
        return None

    res = EntityResolver(repo, llm_disambiguate=fake_disambiguate)
    r = res.resolve(
        PersonMention(surface_form="Altman", canonical_hint=None),
        article_people=[PersonMention(surface_form="Altman", canonical_hint=None)],
        article_context="The OpenAI CEO Altman said today...",
    )
    assert calls, "disambiguator should have been called"
    assert r.person_id == sam.person_id, "should merge into the Sam Altman row"
    assert not r.is_new
    repo.close()


def test_disambiguator_returning_none_falls_back_to_new(tmp_db_path) -> None:
    """If the disambiguator says 'none of these fit', we create a new row
    rather than guess."""
    repo = Repository(tmp_db_path)
    seed1 = EntityResolver(repo)
    seed1.resolve(
        PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman"),
        article_people=[PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman")],
    )
    seed2 = EntityResolver(repo)
    seed2.resolve(
        PersonMention(surface_form="Jack Altman", canonical_hint="Jack Altman"),
        article_people=[PersonMention(surface_form="Jack Altman", canonical_hint="Jack Altman")],
    )
    res = EntityResolver(repo, llm_disambiguate=lambda *_a, **_k: None)
    r = res.resolve(
        PersonMention(surface_form="Altman", canonical_hint=None),
        article_people=[PersonMention(surface_form="Altman", canonical_hint=None)],
    )
    assert r.is_new
