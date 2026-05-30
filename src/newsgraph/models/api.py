"""API request/response shapes. Kept separate from extraction shapes on purpose:
the LLM contract changes more often than the HTTP contract.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl


# ---- requests ----------------------------------------------------------------


class ArticleRequest(BaseModel):
    url: HttpUrl


class RescanRequest(BaseModel):
    pages: int = Field(ge=1, le=20, description="Number of listing pages to crawl.")


# ---- responses ---------------------------------------------------------------


class MergeStats(BaseModel):
    """Returned from any merge operation (single article or rescan)."""

    articles_processed: int = 0
    articles_skipped: int = 0
    people_added: int = 0
    people_updated: int = 0
    aliases_added: int = 0
    mentions_added: int = 0
    edges_added: int = 0
    edges_replaced: int = 0
    errors: list[str] = Field(default_factory=list)


class PersonListItem(BaseModel):
    id: int
    canonical_name: str
    alias_count: int
    mention_count: int


class PersonPage(BaseModel):
    page: int
    size: int
    total: int
    items: list[PersonListItem]


class EdgeOut(BaseModel):
    other_person_id: int
    other_person_name: str
    type: str
    explanation: str
    article_url: str
    supporting_quote: str


class PersonDetail(BaseModel):
    id: int
    canonical_name: str
    aliases: list[str]
    outgoing: list[EdgeOut]
    incoming: list[EdgeOut]


# ---- whole-graph view --------------------------------------------------------


class GraphNode(BaseModel):
    """One person in the graph view. `mentions` drives node size in the UI."""

    id: int
    label: str
    mentions: int


class GraphEdge(BaseModel):
    """A directed relationship, collapsed across articles to a single
    (source, target, type) so the visualization isn't cluttered by per-article
    duplicate edges. `count` is how many article-level edges it stands for."""

    source: int
    target: int
    type: str
    count: int


class GraphData(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
