"""API endpoint tests. Pipe data into the DB directly so we don't need the LLM."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from newsgraph.api.app import create_app
from newsgraph.config import Settings
from newsgraph.db.repository import Repository
from newsgraph.models.extraction import (
    ArticleExtraction,
    PersonMention,
    RelationType,
    Relationship,
)
from newsgraph.pipeline import Pipeline
from newsgraph.scraping.base import ParsedArticle, SiteAdapter


@pytest.fixture
def client_and_repo(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "api.sqlite3",
        cache_dir=tmp_path / "cache",
        disable_llm=True,
    )
    app = create_app(settings)
    repo = Repository(settings.db_path)
    # Seed
    repo.upsert_article(
        url="https://example.com/a", title="A", published_at="2026-05-13T12:00:00Z", body="b"
    )
    pid_sam = repo.insert_person("Sam Altman", "sam altman")
    repo.add_alias(pid_sam, "Sam Altman", "sam altman")
    repo.add_alias(pid_sam, "Altman", "altman")
    pid_greg = repo.insert_person("Greg Brockman", "greg brockman")
    repo.add_alias(pid_greg, "Greg Brockman", "greg brockman")
    repo.add_mention(pid_sam, "https://example.com/a", "Sam Altman", "CEO", False)
    repo.add_mention(pid_greg, "https://example.com/a", "Greg Brockman", "President", False)
    repo.add_edge(
        pid_sam,
        pid_greg,
        "partners_with",
        "They work together.",
        "Sam Altman and Greg Brockman work together.",
        "https://example.com/a",
    )
    repo.close()

    return TestClient(app), settings, pid_sam, pid_greg


def test_health(client_and_repo) -> None:
    client, *_ = client_and_repo
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_people_list_pagination(client_and_repo) -> None:
    client, *_ = client_and_repo
    r = client.get("/people?page=1&size=10")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["page"] == 1
    names = {p["canonical_name"] for p in body["items"]}
    assert names == {"Sam Altman", "Greg Brockman"}
    # alias_count is populated.
    sam = next(p for p in body["items"] if p["canonical_name"] == "Sam Altman")
    assert sam["alias_count"] == 2
    assert sam["mention_count"] == 1


def test_people_detail(client_and_repo) -> None:
    client, _settings, sam, greg = client_and_repo
    r = client.get(f"/people/{sam}")
    assert r.status_code == 200
    body = r.json()
    assert body["canonical_name"] == "Sam Altman"
    assert set(body["aliases"]) == {"Sam Altman", "Altman"}
    assert len(body["outgoing"]) == 1
    out = body["outgoing"][0]
    assert out["other_person_id"] == greg
    assert out["type"] == "partners_with"
    assert "Altman" in out["supporting_quote"]
    assert body["incoming"] == []
    # Other side of the edge.
    r2 = client.get(f"/people/{greg}")
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["incoming"]) == 1
    assert body2["incoming"][0]["other_person_id"] == sam


def test_person_not_found(client_and_repo) -> None:
    client, *_ = client_and_repo
    r = client.get("/people/99999")
    assert r.status_code == 404


def test_post_article_unknown_host_returns_400(client_and_repo) -> None:
    """An otherwise-valid URL with no registered adapter is a client error."""
    client, *_ = client_and_repo
    r = client.post("/articles", json={"url": "https://example.com/no-adapter"})
    assert r.status_code == 400
    assert "no adapter" in r.json()["detail"].lower()


def test_post_article_malformed_url_returns_422(client_and_repo) -> None:
    """Pydantic rejects garbage before we ever reach the pipeline."""
    client, *_ = client_and_repo
    r = client.post("/articles", json={"url": "not-a-url"})
    assert r.status_code == 422


def test_openapi_docs_load(client_and_repo) -> None:
    """FastAPI auto-generates an OpenAPI spec; make sure it's wired up."""
    client, *_ = client_and_repo
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert {"/articles", "/rescan", "/people", "/people/{person_id}", "/graph"} <= set(
        paths.keys()
    )


# ---- graph view -------------------------------------------------------------


def test_graph_json(client_and_repo) -> None:
    """GET /graph returns the seeded people as nodes and the single edge,
    collapsed to one (source, target, type) row."""
    client, _settings, sam, greg = client_and_repo
    r = client.get("/graph")
    assert r.status_code == 200
    body = r.json()
    node_ids = {n["id"] for n in body["nodes"]}
    assert node_ids == {sam, greg}
    # Node payload carries the label + mention count that drives sizing.
    sam_node = next(n for n in body["nodes"] if n["id"] == sam)
    assert sam_node["label"] == "Sam Altman"
    assert sam_node["mentions"] == 1
    assert len(body["edges"]) == 1
    edge = body["edges"][0]
    assert edge["source"] == sam
    assert edge["target"] == greg
    assert edge["type"] == "partners_with"
    assert edge["count"] == 1


def test_graph_json_excludes_dangling_edges(tmp_path: Path) -> None:
    """An edge is only returned when BOTH endpoints are in the node set."""
    settings = Settings(db_path=tmp_path / "g.sqlite3", cache_dir=tmp_path / "c", disable_llm=True)
    repo = Repository(settings.db_path)
    a = repo.insert_person("A", "a")
    b = repo.insert_person("B", "b")
    repo.add_mention(a, "https://x/1", "A", None, False)
    repo.add_mention(b, "https://x/1", "B", None, False)
    repo.add_edge(a, b, "partners_with", "x", "q", "https://x/1")
    repo.close()

    client = TestClient(create_app(settings))
    # Only request 1 node → the edge's other endpoint is missing → no edges.
    body = client.get("/graph?limit_nodes=1").json()
    assert len(body["nodes"]) == 1
    assert body["edges"] == []


def test_graph_view_html(client_and_repo) -> None:
    """The root path and /graph/view serve the interactive HTML page."""
    client, *_ = client_and_repo
    for path in ("/", "/graph/view"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "vis-network" in r.text
        assert "/graph" in r.text


# ---- people pages: HTML for browsers, JSON for API clients ------------------

_HTML = {"accept": "text/html,application/xhtml+xml"}
_JSON = {"accept": "application/json"}


def test_people_serves_html_to_browsers(client_and_repo) -> None:
    """A browser (Accept: text/html) gets the styled list page at /people."""
    client, *_ = client_and_repo
    r = client.get("/people", headers=_HTML)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "news" in r.text and "People" in r.text


def test_people_serves_json_to_api_clients(client_and_repo) -> None:
    """The same URL returns JSON when the caller asks for it — REST contract
    is preserved under content negotiation."""
    client, *_ = client_and_repo
    r = client.get("/people?page=1&size=10", headers=_JSON)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["total"] == 2


def test_person_detail_serves_html_to_browsers(client_and_repo) -> None:
    client, _settings, sam, _greg = client_and_repo
    r = client.get(f"/people/{sam}", headers=_HTML)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # The detail shell reads the id from the path in JS.
    assert "/people/" in r.text


def test_person_detail_json_unaffected(client_and_repo) -> None:
    """JSON detail (and its 404) still work for API clients."""
    client, _settings, sam, _greg = client_and_repo
    ok = client.get(f"/people/{sam}", headers=_JSON)
    assert ok.status_code == 200
    assert ok.json()["canonical_name"] == "Sam Altman"
    missing = client.get("/people/99999", headers=_JSON)
    assert missing.status_code == 404


def test_people_explicit_view_routes(client_and_repo) -> None:
    """/people/view and /people/{id}/view always serve HTML, and 'view' is not
    mistaken for a person id."""
    client, _settings, sam, _greg = client_and_repo
    r1 = client.get("/people/view")
    assert r1.status_code == 200
    assert r1.headers["content-type"].startswith("text/html")
    r2 = client.get(f"/people/{sam}/view")
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("text/html")


# ---- happy-path POST /articles ----------------------------------------------


class _StubAdapter(SiteAdapter):
    name = "stub"

    def __init__(self, parsed: ParsedArticle):
        self.parsed = parsed

    def listing_url(self, page: int) -> str:  # pragma: no cover — unused here
        return f"stub://listing/{page}"

    def list_articles(self, html: str):  # pragma: no cover — unused here
        return []

    def parse_article(self, html: str, url: str) -> ParsedArticle:
        return self.parsed


class _StubHTTP:
    def get(self, url: str, *, use_cache: bool = True) -> str:
        return "<html><body>stub</body></html>"

    def close(self) -> None:
        pass


class _StubExtractor:
    """Skips the LLM entirely and returns a canned extraction."""

    def __init__(self, payload: ArticleExtraction):
        self.payload = payload
        self.llm = None  # disambiguator path is unused here

    def extract(self, *, url, title, authors, body):
        return self.payload


def test_post_article_happy_path(tmp_path: Path) -> None:
    """End-to-end POST /articles: fetch (stub) → parse (stub) → extract (stub)
    → resolve → store → readable via GET /people/{id}."""
    url = "stub://articles/happy-path"
    parsed = ParsedArticle(
        url=url,
        title="Altman partners with Brockman",
        published_at="2026-05-13T12:00:00Z",
        authors=["Tim Fernholz"],
        body_text="Sam Altman and Greg Brockman are working together at OpenAI.",
    )
    extraction = ArticleExtraction(
        people=[
            PersonMention(surface_form="Sam Altman", canonical_hint="Sam Altman", role="CEO"),
            PersonMention(surface_form="Greg Brockman", canonical_hint="Greg Brockman"),
            PersonMention(surface_form="Tim Fernholz", canonical_hint="Tim Fernholz", is_author=True),
        ],
        relationships=[
            Relationship(
                source="Sam Altman",
                target="Greg Brockman",
                type=RelationType.PARTNERS_WITH,
                explanation="Altman and Brockman are working together.",
                supporting_quote="Sam Altman and Greg Brockman are working together at OpenAI.",
            ),
        ],
    )

    settings = Settings(
        db_path=tmp_path / "happy.sqlite3",
        cache_dir=tmp_path / "cache",
        disable_llm=False,
    )

    def factory() -> Pipeline:
        repo = Repository(settings.db_path)
        adapter = _StubAdapter(parsed)
        pl = Pipeline(
            settings=settings,
            repo=repo,
            http=_StubHTTP(),  # type: ignore[arg-type]
            extractor=_StubExtractor(extraction),  # type: ignore[arg-type]
            adapters=[adapter],
        )
        pl._adapter_for = lambda _u: adapter  # type: ignore[assignment]
        return pl

    app = create_app(settings, pipeline_factory=factory)
    client = TestClient(app)

    r = client.post("/articles", json={"url": "https://stub.example/a"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["articles_processed"] == 1
    assert body["people_added"] == 3
    assert body["edges_added"] == 1
    assert body["errors"] == []

    # Idempotent: a second POST replaces facts but yields the same people count.
    r2 = client.post("/articles", json={"url": "https://stub.example/a"})
    assert r2.status_code == 200
    assert r2.json()["people_added"] == 0
    assert r2.json()["edges_replaced"] == 1

    # Now look the edge up through GET /people/{id} and verify provenance flows.
    listing = client.get("/people?page=1&size=50").json()
    sam = next(p for p in listing["items"] if p["canonical_name"] == "Sam Altman")
    detail = client.get(f"/people/{sam['id']}").json()
    assert detail["canonical_name"] == "Sam Altman"
    assert detail["aliases"] == ["Sam Altman"]
    assert len(detail["outgoing"]) == 1
    out = detail["outgoing"][0]
    assert out["type"] == "partners_with"
    assert out["other_person_name"] == "Greg Brockman"
    assert "Brockman" in out["supporting_quote"]
    assert out["article_url"] == "https://stub.example/a"

    # And the edge surfaces in the graph view too.
    graph = client.get("/graph").json()
    assert any(e["type"] == "partners_with" for e in graph["edges"])


# ---- POST /articles error mapping -------------------------------------------


def _app_with_adapter(tmp_path: Path, adapter: SiteAdapter, http) -> TestClient:
    """Build an app whose pipeline routes everything to `adapter` over `http`."""
    settings = Settings(
        db_path=tmp_path / "err.sqlite3", cache_dir=tmp_path / "cache", disable_llm=True
    )

    def factory() -> Pipeline:
        pl = Pipeline(
            settings=settings,
            repo=Repository(settings.db_path),
            http=http,  # type: ignore[arg-type]
            extractor=_StubExtractor(ArticleExtraction(people=[], relationships=[])),  # type: ignore[arg-type]
            adapters=[adapter],
        )
        pl._adapter_for = lambda _u: adapter  # type: ignore[assignment]
        return pl

    return TestClient(create_app(settings, pipeline_factory=factory))


def test_post_article_empty_body_returns_422(tmp_path: Path) -> None:
    """A parsed article with no body text is unprocessable, not a server error."""
    parsed = ParsedArticle(
        url="stub://empty", title="t", published_at=None, authors=[], body_text=""
    )
    client = _app_with_adapter(tmp_path, _StubAdapter(parsed), _StubHTTP())
    r = client.post("/articles", json={"url": "https://stub.example/empty"})
    assert r.status_code == 422
    assert "empty body" in r.json()["detail"].lower()


def test_post_article_fetch_failure_returns_502(tmp_path: Path) -> None:
    """Upstream fetch failures surface as 502 Bad Gateway."""

    class _BoomHTTP:
        def get(self, url: str, *, use_cache: bool = True) -> str:
            raise RuntimeError("connection reset")

        def close(self) -> None:
            pass

    parsed = ParsedArticle(
        url="stub://x", title="t", published_at=None, authors=[], body_text="x"
    )
    client = _app_with_adapter(tmp_path, _StubAdapter(parsed), _BoomHTTP())
    r = client.post("/articles", json={"url": "https://stub.example/x"})
    assert r.status_code == 502
    assert "fetch failed" in r.json()["detail"].lower()


def test_rescan_accumulates_per_article_errors(tmp_path: Path) -> None:
    """A single bad article must not fail the whole multi-article rescan: the
    error lands in the response body and the request is still 200."""
    from newsgraph.scraping.base import ListingItem

    good = ParsedArticle(
        url="stub://good", title="t", published_at=None, authors=[], body_text="body"
    )

    class _ListingAdapter(SiteAdapter):
        name = "techcrunch"  # rescan() looks the adapter up by this name

        def listing_url(self, page: int) -> str:
            return f"stub://listing/{page}"

        def list_articles(self, html: str):
            return [ListingItem(url="stub://good"), ListingItem(url="stub://bad")]

        def parse_article(self, html: str, url: str) -> ParsedArticle:
            return good

    class _SelectiveHTTP:
        def get(self, url: str, *, use_cache: bool = True) -> str:
            if url == "stub://bad":
                raise RuntimeError("404 not found")
            return "<html><body>stub</body></html>"

        def close(self) -> None:
            pass

    client = _app_with_adapter(tmp_path, _ListingAdapter(), _SelectiveHTTP())
    r = client.post("/rescan", json={"pages": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["articles_processed"] == 1  # the good one
    assert len(body["errors"]) == 1
    assert "stub://bad" in body["errors"][0]
