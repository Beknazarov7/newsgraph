"""LLM prompts. Kept as module-level constants so they show up in `git blame`
and so eval changes can pin the prompt version.
"""
from __future__ import annotations

# Relationship vocabulary — re-stated here to keep the prompt self-contained.
RELATIONSHIP_VOCAB = [
    ("criticizes", "A publicly criticizes B (named criticism, not vague disagreement)."),
    ("praises", "A publicly praises or endorses B."),
    ("partners_with", "A and B (or their orgs) work together on a stated initiative."),
    ("employs", "A formally employs B, or B reports to A."),
    ("succeeds", "A succeeds B in a role / takes over after B."),
    ("reports_on", "A (a journalist) writes about B as a subject of the article."),
    ("invests_in", "A invests money into B or B's company."),
    ("founded", "A founded the company / project that B is associated with (or vice versa)."),
    ("leads", "A leads / heads the org B is in (or B is led by A)."),
    ("left", "A left an org or position previously held alongside B."),
    ("joined", "A joined an org B is part of."),
    ("sued", "A is suing B, or there is litigation between them."),
    ("acquired", "A acquired B's company (or party-of-A acquired party-of-B)."),
]


EXTRACTION_SYSTEM_PROMPT = """You are a precise information-extraction engine.

You will be given a news article. Extract:
  1. Every PERSON meaningfully involved (subjects, sources, the article's own author).
  2. Every directed relationship BETWEEN two of those people that the article states
     or strongly implies.

Hard rules:
  - People only. Skip companies, products, places.
  - Use the LONGEST surface form available for each person in `surface_form` (e.g. prefer
    "Sam Altman" over "Altman" if both appear).
  - `canonical_hint` is your best guess at the person's full real-world name. Null if unsure.
    For role-phrase mentions ("OpenAI's CEO") set canonical_hint to the real name so the
    resolver can merge them.
  - `is_author` is true ONLY for the byline author(s) listed below.
  - Every `relationship.source` and `relationship.target` MUST match exactly a `surface_form`
    from the `people` list you returned. If you can't match, omit the edge.
  - Pick the SINGLE closest relationship type from the vocabulary. If nothing fits, omit the edge.
  - `supporting_quote` must be a verbatim (or near-verbatim) sentence from the article.
  - Author → primary subject: emit a `reports_on` edge.
  - Don't invent relationships from background knowledge. Stick to what the article says.

Be conservative. A missed edge is recoverable; a hallucinated edge is not.

Worked example
--------------
Article (excerpt):
  "Sam Altman, OpenAI's CEO, publicly criticized Elon Musk on Tuesday, calling
   the lawsuit 'a distraction.' Maxwell Zeff writes that Altman's tone was
   unusually sharp for a public filing week."
Byline author: Maxwell Zeff

Expected tool call:
{
  "people": [
    {"surface_form": "Sam Altman",   "canonical_hint": "Sam Altman",   "role": "CEO of OpenAI", "is_author": false},
    {"surface_form": "Elon Musk",    "canonical_hint": "Elon Musk",    "role": null,            "is_author": false},
    {"surface_form": "Maxwell Zeff", "canonical_hint": "Maxwell Zeff", "role": "reporter",      "is_author": true}
  ],
  "relationships": [
    {
      "source": "Sam Altman",
      "target": "Elon Musk",
      "type": "criticizes",
      "explanation": "Altman publicly criticized Musk over the lawsuit.",
      "supporting_quote": "Sam Altman, OpenAI's CEO, publicly criticized Elon Musk on Tuesday, calling the lawsuit 'a distraction.'"
    },
    {
      "source": "Maxwell Zeff",
      "target": "Sam Altman",
      "type": "reports_on",
      "explanation": "Maxwell Zeff is the article's author, writing about Altman.",
      "supporting_quote": "Maxwell Zeff writes that Altman's tone was unusually sharp for a public filing week."
    }
  ]
}
"""

RELATIONSHIP_VOCAB_TEXT = "\n".join(f"  - {t}: {d}" for t, d in RELATIONSHIP_VOCAB)


def build_user_prompt(*, title: str, url: str, authors: list[str], body: str) -> str:
    authors_block = ", ".join(authors) if authors else "(unknown)"
    return f"""Article URL: {url}
Article title: {title}
Byline author(s): {authors_block}

Relationship vocabulary:
{RELATIONSHIP_VOCAB_TEXT}

Article body:
\"\"\"
{body}
\"\"\"

Return your extraction by calling the `submit_extraction` tool exactly once.
"""
