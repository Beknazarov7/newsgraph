"""Pydantic models the LLM is forced to fill in via structured output.

These shapes are the contract between `extraction.extractor` and the rest of
the pipeline. Anything the LLM returns that doesn't conform is rejected; we
don't try to repair partial JSON.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RelationType(str, Enum):
    """Closed vocabulary. The LLM is told to pick the closest one or skip the edge.

    Why closed: an open vocabulary turns the graph into a free-text mess and
    makes the API less useful. If a new type is genuinely needed (say, `fired`)
    we add it here and re-run the extractor.
    """

    CRITICIZES = "criticizes"
    PRAISES = "praises"
    PARTNERS_WITH = "partners_with"
    EMPLOYS = "employs"
    SUCCEEDS = "succeeds"
    REPORTS_ON = "reports_on"
    INVESTS_IN = "invests_in"
    FOUNDED = "founded"
    LEADS = "leads"
    LEFT = "left"
    JOINED = "joined"
    SUED = "sued"
    ACQUIRED = "acquired"


class PersonMention(BaseModel):
    """One person as they appear in one article."""

    surface_form: str = Field(
        description="Exactly how the name appears in the article (the longest form available).",
    )
    canonical_hint: str | None = Field(
        default=None,
        description="Your best guess at the person's full canonical name, or null if unsure.",
    )
    role: str | None = Field(
        default=None,
        description="The person's role as conveyed by the article, e.g. 'CEO of OpenAI'. Null if not stated.",
    )
    is_author: bool = Field(
        default=False,
        description="True only for the byline author(s) of this article.",
    )


class Relationship(BaseModel):
    """A directed, typed edge between two people."""

    source: str = Field(description="Must equal a `surface_form` from the `people` list.")
    target: str = Field(description="Must equal a `surface_form` from the `people` list.")
    type: RelationType
    explanation: str = Field(
        description="One sentence in natural language explaining the edge.",
    )
    supporting_quote: str = Field(
        description="A verbatim sentence (or near-verbatim) from the article that supports this edge.",
    )


class ArticleExtraction(BaseModel):
    """Top-level structured output for one article."""

    people: list[PersonMention] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
