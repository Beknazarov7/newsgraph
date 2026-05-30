"""FastAPI application.

Endpoints:
  GET  /            — interactive graph visualization (HTML)
  POST /articles    — process a single article
  POST /rescan      — re-crawl the latest N listing pages
  GET  /people      — paginated list (HTML for browsers, JSON for API clients)
  GET  /people/{id} — aliases + outgoing/incoming relationships (HTML or JSON)
  GET  /graph       — whole-graph nodes + edges (JSON, feeds the visualization)
  GET  /graph/view  — interactive graph visualization (HTML)
  GET  /health

The /people routes content-negotiate: a browser (Accept: text/html) gets the
styled page; everything else gets JSON from the same URL. See _wants_html.
"""
from __future__ import annotations

from typing import Callable, Generator

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from ..config import Settings, get_settings
from ..db.repository import Repository
from ..models.api import (
    ArticleRequest,
    GraphData,
    MergeStats,
    PersonDetail,
    PersonPage,
    RescanRequest,
)
from ..pipeline import Pipeline
from .graph_view import GRAPH_HTML
from .people_view import PEOPLE_LIST_HTML, PERSON_DETAIL_HTML


def _wants_html(request: Request) -> bool:
    """True when the caller is a browser (asks for text/html). API clients
    (curl, httpx, fetch with Accept: application/json) fall through to JSON, so
    the same URLs stay a clean REST contract."""
    return "text/html" in request.headers.get("accept", "")


def _classify_error(err: str) -> int | None:
    """Map a pipeline-error string to an HTTP status code, or None if the
    operation should still report 200 with the error in the response body
    (the rescan case, where some articles can fail and others succeed)."""
    low = err.lower()
    if low.startswith("no adapter"):
        return 400
    if low.startswith("fetch failed"):
        return 502
    if low.startswith("empty body"):
        return 422
    if low.startswith("extraction failed"):
        return 502
    return None


def create_app(
    settings: Settings | None = None,
    pipeline_factory: Callable[[], Pipeline] | None = None,
) -> FastAPI:
    """Application factory.

    Tests pass a `settings` override and optionally a `pipeline_factory` to
    inject a stubbed Pipeline (so the LLM and HTTP layers don't need to be
    reachable).
    """
    s = settings or get_settings()
    build_pipeline = pipeline_factory or (lambda: Pipeline.default(s))
    app = FastAPI(
        title="newsgraph",
        version="0.1.0",
        summary="Scrape TechCrunch, extract people + relationships, query as a graph.",
    )

    def get_pipeline() -> Generator[Pipeline, None, None]:
        """Per-request pipeline. Built fresh so each request owns its DB
        connection (sqlite3 isn't thread-safe by default)."""
        pl = build_pipeline()
        try:
            yield pl
        finally:
            pl.close()

    def get_repo() -> Generator[Repository, None, None]:
        repo = Repository(s.db_path)
        try:
            yield repo
        finally:
            repo.close()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/",
        response_class=HTMLResponse,
        include_in_schema=False,
        summary="Interactive knowledge-graph visualization.",
    )
    @app.get("/graph/view", response_class=HTMLResponse, include_in_schema=False)
    def graph_view() -> HTMLResponse:
        return HTMLResponse(GRAPH_HTML)

    @app.get(
        "/graph",
        response_model=GraphData,
        summary="Whole-graph nodes + edges (feeds the visualization).",
    )
    def graph(
        limit_nodes: int = Query(200, ge=1, le=1000),
        repo: Repository = Depends(get_repo),
    ) -> GraphData:
        return repo.get_graph(limit_nodes=limit_nodes)

    @app.post(
        "/articles",
        response_model=MergeStats,
        summary="Fetch, parse, extract, and merge one article.",
    )
    def post_article(
        req: ArticleRequest,
        pipeline: Pipeline = Depends(get_pipeline),
    ) -> MergeStats:
        stats = pipeline.process_article(str(req.url))
        # Single-article ingestion: if nothing got processed AND we have an
        # error we can classify, surface it as a real HTTP status. (Rescan
        # accumulates errors per-article so we don't escalate there.)
        if stats.articles_processed == 0 and stats.errors:
            status = _classify_error(stats.errors[0])
            if status is not None:
                raise HTTPException(status_code=status, detail=stats.errors[0])
        return stats

    @app.post(
        "/rescan",
        response_model=MergeStats,
        summary="Re-crawl the latest N listing pages and merge new content.",
    )
    def post_rescan(
        req: RescanRequest,
        pipeline: Pipeline = Depends(get_pipeline),
    ) -> MergeStats:
        return pipeline.rescan(req.pages)

    @app.get("/people", response_model=PersonPage)
    def list_people(
        request: Request,
        page: int = Query(1, ge=1),
        size: int = Query(50, ge=1, le=200),
        repo: Repository = Depends(get_repo),
    ):
        # Browsers get the styled page; API clients get JSON (same URL).
        if _wants_html(request):
            return HTMLResponse(PEOPLE_LIST_HTML)
        return repo.list_people(page=page, size=size)

    @app.get("/people/view", response_class=HTMLResponse, include_in_schema=False)
    def people_list_view() -> HTMLResponse:
        return HTMLResponse(PEOPLE_LIST_HTML)

    # Declared before /people/{person_id} so "view" isn't captured as an id.
    @app.get("/people/{person_id}/view", response_class=HTMLResponse, include_in_schema=False)
    def person_detail_view(person_id: int) -> HTMLResponse:
        return HTMLResponse(PERSON_DETAIL_HTML)

    @app.get("/people/{person_id}", response_model=PersonDetail)
    def get_person(
        person_id: int,
        request: Request,
        repo: Repository = Depends(get_repo),
    ):
        if _wants_html(request):
            # The shell is the same for any id; its JS fetches the JSON and
            # renders a "not found" message on a 404.
            return HTMLResponse(PERSON_DETAIL_HTML)
        detail = repo.get_person_detail(person_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="person not found")
        return detail

    return app


# Module-level instance for `uvicorn newsgraph.api.app:app`.
app = create_app()
