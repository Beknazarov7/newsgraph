-- Knowledge-graph schema.
--
-- Identity model:
--   people       — canonical entities. One row per unique person across articles.
--   aliases      — every surface form we've seen, pointing back to a person.
--                  Normalized form is unique per person; the same normalized
--                  form CAN point to two people in the rare ambiguous case,
--                  in which case the resolver makes the call at insert time.
--   mentions     — (person, article) link with the surface form used in that
--                  article. Powers the "mention count" stat.
--   articles     — what we've already scraped. URL is the primary key.
--   edges        — directed relationships, with article-level provenance.
--
-- Why the (source_id, target_id, type, article_url) uniqueness on edges:
-- the same edge can legitimately appear in multiple articles (e.g. Altman LEADS
-- OpenAI is asserted in dozens of pieces). We dedupe within an article but
-- preserve provenance across articles.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS articles (
    url            TEXT PRIMARY KEY,
    title          TEXT,
    published_at   TEXT,
    fetched_at     TEXT NOT NULL,
    content_hash   TEXT
);

CREATE TABLE IF NOT EXISTS people (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name   TEXT NOT NULL,
    normalized_name  TEXT NOT NULL UNIQUE,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id      INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    surface_form   TEXT NOT NULL,
    normalized     TEXT NOT NULL,
    UNIQUE (person_id, normalized)
);
CREATE INDEX IF NOT EXISTS idx_aliases_normalized ON aliases(normalized);

CREATE TABLE IF NOT EXISTS mentions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id      INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    article_url    TEXT NOT NULL REFERENCES articles(url) ON DELETE CASCADE,
    surface_form   TEXT NOT NULL,
    role_hint      TEXT,
    is_author      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (person_id, article_url, surface_form)
);
CREATE INDEX IF NOT EXISTS idx_mentions_person ON mentions(person_id);
CREATE INDEX IF NOT EXISTS idx_mentions_article ON mentions(article_url);

CREATE TABLE IF NOT EXISTS edges (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id         INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    target_id         INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    type              TEXT NOT NULL,
    explanation       TEXT NOT NULL,
    supporting_quote  TEXT NOT NULL,
    article_url       TEXT NOT NULL REFERENCES articles(url) ON DELETE CASCADE,
    created_at        TEXT NOT NULL,
    UNIQUE (source_id, target_id, type, article_url)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_article ON edges(article_url);
