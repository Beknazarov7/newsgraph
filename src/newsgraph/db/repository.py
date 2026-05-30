"""SQLite repository.

All write paths go through here so the idempotence rules (described in
DECISIONS.md) live in one file. The repository is intentionally not async:
SQLite is in-process and the API is fronted by FastAPI which runs blocking
endpoints on a thread pool.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from ..models.api import (
    EdgeOut,
    GraphData,
    GraphEdge,
    GraphNode,
    MergeStats,
    PersonDetail,
    PersonListItem,
    PersonPage,
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class Repository:
    """Thin wrapper over a sqlite3 connection. Not thread-safe across instances;
    each request handler should construct its own."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # timeout=30: under a concurrent POST /articles + POST /rescan the
        # default 5s wait for the file lock can spuriously raise OperationalError.
        self._conn = sqlite3.connect(
            db_path, isolation_level=None, timeout=30.0
        )  # autocommit
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with open(SCHEMA_PATH) as f:
            self._conn.executescript(f.read())

    def close(self) -> None:
        self._conn.close()

    # ---- context manager for a single logical transaction --------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Wrap a series of writes in a single transaction."""
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # ---- articles -----------------------------------------------------------

    def upsert_article(self, url: str, title: str | None, published_at: str | None, body: str) -> bool:
        """Insert or update the article row. Returns True if it's new content
        (either new URL or content hash changed)."""
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        row = self._conn.execute(
            "SELECT content_hash FROM articles WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO articles(url, title, published_at, fetched_at, content_hash) VALUES (?, ?, ?, ?, ?)",
                (url, title, published_at, _now_iso(), content_hash),
            )
            return True
        # Always update the fetched_at + title in case they changed.
        self._conn.execute(
            "UPDATE articles SET title = ?, published_at = ?, fetched_at = ?, content_hash = ? WHERE url = ?",
            (title, published_at, _now_iso(), content_hash, url),
        )
        return row["content_hash"] != content_hash

    def article_exists(self, url: str) -> bool:
        return (
            self._conn.execute("SELECT 1 FROM articles WHERE url = ?", (url,)).fetchone()
            is not None
        )

    # ---- people / aliases ---------------------------------------------------

    def find_person_by_normalized(self, normalized: str) -> int | None:
        """Lookup by alias's normalized form. Returns the person_id or None."""
        row = self._conn.execute(
            "SELECT person_id FROM aliases WHERE normalized = ? LIMIT 1",
            (normalized,),
        ).fetchone()
        return row["person_id"] if row else None

    def candidates_by_last_token(self, last_token: str) -> list[tuple[int, str]]:
        """For 'Altman', return every (person_id, canonical_name) where the
        canonical name's last token normalizes to `last_token`.

        Used by the entity resolver for short surface forms.
        """
        rows = self._conn.execute(
            """
            SELECT id, canonical_name FROM people
             WHERE normalized_name LIKE ? || ' %' ESCAPE '\\'
                OR normalized_name = ?
                OR normalized_name LIKE '% ' || ?
            """,
            (last_token, last_token, last_token),
        ).fetchall()
        return [(r["id"], r["canonical_name"]) for r in rows]

    def get_person_canonical(self, person_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT canonical_name FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        return row["canonical_name"] if row else None

    def insert_person(self, canonical_name: str, normalized_name: str) -> int:
        now = _now_iso()
        cur = self._conn.execute(
            "INSERT INTO people(canonical_name, normalized_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (canonical_name, normalized_name, now, now),
        )
        return cur.lastrowid

    def maybe_promote_canonical(self, person_id: int, candidate_canonical: str, candidate_normalized: str) -> bool:
        """If the candidate looks like a fuller/better canonical (longer, more
        tokens), promote it. Returns True if the row was updated."""
        row = self._conn.execute(
            "SELECT canonical_name, normalized_name FROM people WHERE id = ?",
            (person_id,),
        ).fetchone()
        if row is None:
            return False
        current = row["canonical_name"]
        if (
            len(candidate_canonical) > len(current)
            and len(candidate_normalized.split()) > len(row["normalized_name"].split())
        ):
            self._conn.execute(
                "UPDATE people SET canonical_name = ?, normalized_name = ?, updated_at = ? WHERE id = ?",
                (candidate_canonical, candidate_normalized, _now_iso(), person_id),
            )
            return True
        return False

    def add_alias(self, person_id: int, surface_form: str, normalized: str) -> bool:
        """Returns True if a new alias row was inserted."""
        try:
            self._conn.execute(
                "INSERT INTO aliases(person_id, surface_form, normalized) VALUES (?, ?, ?)",
                (person_id, surface_form, normalized),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    # ---- mentions + edges (per-article) -------------------------------------

    def replace_article_facts(self, article_url: str) -> int:
        """Delete every mention/edge tied to this article URL. Returns the
        number of edges removed (used to report `edges_replaced`)."""
        edges = self._conn.execute(
            "SELECT COUNT(*) AS n FROM edges WHERE article_url = ?", (article_url,)
        ).fetchone()["n"]
        self._conn.execute("DELETE FROM mentions WHERE article_url = ?", (article_url,))
        self._conn.execute("DELETE FROM edges WHERE article_url = ?", (article_url,))
        return edges

    def add_mention(
        self,
        person_id: int,
        article_url: str,
        surface_form: str,
        role_hint: str | None,
        is_author: bool,
    ) -> bool:
        try:
            self._conn.execute(
                "INSERT INTO mentions(person_id, article_url, surface_form, role_hint, is_author) VALUES (?, ?, ?, ?, ?)",
                (person_id, article_url, surface_form, role_hint, 1 if is_author else 0),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def add_edge(
        self,
        source_id: int,
        target_id: int,
        type_: str,
        explanation: str,
        supporting_quote: str,
        article_url: str,
    ) -> bool:
        try:
            self._conn.execute(
                """INSERT INTO edges
                   (source_id, target_id, type, explanation, supporting_quote, article_url, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (source_id, target_id, type_, explanation, supporting_quote, article_url, _now_iso()),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    # ---- API queries --------------------------------------------------------

    def list_people(self, page: int, size: int) -> PersonPage:
        total = self._conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]
        offset = (page - 1) * size
        rows = self._conn.execute(
            """
            SELECT p.id, p.canonical_name,
                   (SELECT COUNT(*) FROM aliases a WHERE a.person_id = p.id) AS alias_count,
                   (SELECT COUNT(*) FROM mentions m WHERE m.person_id = p.id) AS mention_count
              FROM people p
             ORDER BY mention_count DESC, p.id
             LIMIT ? OFFSET ?
            """,
            (size, offset),
        ).fetchall()
        items = [
            PersonListItem(
                id=r["id"],
                canonical_name=r["canonical_name"],
                alias_count=r["alias_count"],
                mention_count=r["mention_count"],
            )
            for r in rows
        ]
        return PersonPage(page=page, size=size, total=total, items=items)

    def get_person_detail(self, person_id: int) -> PersonDetail | None:
        row = self._conn.execute(
            "SELECT id, canonical_name FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        if row is None:
            return None
        aliases = [
            r["surface_form"]
            for r in self._conn.execute(
                "SELECT surface_form FROM aliases WHERE person_id = ? ORDER BY surface_form",
                (person_id,),
            )
        ]
        outgoing = [
            EdgeOut(
                other_person_id=r["target_id"],
                other_person_name=r["target_name"],
                type=r["type"],
                explanation=r["explanation"],
                article_url=r["article_url"],
                supporting_quote=r["supporting_quote"],
            )
            for r in self._conn.execute(
                """
                SELECT e.target_id, e.type, e.explanation, e.article_url, e.supporting_quote,
                       p.canonical_name AS target_name
                  FROM edges e JOIN people p ON p.id = e.target_id
                 WHERE e.source_id = ?
                 ORDER BY e.created_at
                """,
                (person_id,),
            )
        ]
        incoming = [
            EdgeOut(
                other_person_id=r["source_id"],
                other_person_name=r["source_name"],
                type=r["type"],
                explanation=r["explanation"],
                article_url=r["article_url"],
                supporting_quote=r["supporting_quote"],
            )
            for r in self._conn.execute(
                """
                SELECT e.source_id, e.type, e.explanation, e.article_url, e.supporting_quote,
                       p.canonical_name AS source_name
                  FROM edges e JOIN people p ON p.id = e.source_id
                 WHERE e.target_id = ?
                 ORDER BY e.created_at
                """,
                (person_id,),
            )
        ]
        return PersonDetail(
            id=row["id"],
            canonical_name=row["canonical_name"],
            aliases=aliases,
            outgoing=outgoing,
            incoming=incoming,
        )

    def get_graph(self, limit_nodes: int = 200) -> GraphData:
        """Return the top-N most-mentioned people and every edge whose endpoints
        are both in that set, collapsed to one row per (source, target, type).

        Bounding to the busiest people keeps the visualization legible and the
        payload finite even as the graph grows.
        """
        node_rows = self._conn.execute(
            """
            SELECT p.id, p.canonical_name,
                   (SELECT COUNT(*) FROM mentions m WHERE m.person_id = p.id) AS mention_count
              FROM people p
             ORDER BY mention_count DESC, p.id
             LIMIT ?
            """,
            (limit_nodes,),
        ).fetchall()
        nodes = [
            GraphNode(id=r["id"], label=r["canonical_name"], mentions=r["mention_count"])
            for r in node_rows
        ]
        ids = {r["id"] for r in node_rows}
        if not ids:
            return GraphData(nodes=[], edges=[])

        placeholders = ",".join("?" for _ in ids)
        params = list(ids) + list(ids)
        edge_rows = self._conn.execute(
            f"""
            SELECT source_id, target_id, type, COUNT(*) AS n
              FROM edges
             WHERE source_id IN ({placeholders})
               AND target_id IN ({placeholders})
             GROUP BY source_id, target_id, type
             ORDER BY n DESC
            """,
            params,
        ).fetchall()
        edges = [
            GraphEdge(
                source=r["source_id"],
                target=r["target_id"],
                type=r["type"],
                count=r["n"],
            )
            for r in edge_rows
        ]
        return GraphData(nodes=nodes, edges=edges)


def empty_stats() -> MergeStats:
    return MergeStats()
