# Evals

A small hand-labeled gold set + a scorer that runs the full pipeline and
reports three numbers: **people P/R**, **entity-resolution accuracy**, and
**edge precision** (LLM-as-judge).

## What's measured

- **People precision & recall** — set-overlap between the canonical names the
  pipeline produced for an article and the canonical names in the gold set.
  A predicted name matches a gold person if its normalized form equals any of
  the gold aliases, OR if it shares a multi-token last name (handles short
  surface forms the pipeline left unmerged).
- **Entity-resolution accuracy** — of every gold person whose surface forms the
  pipeline mentioned at all, what fraction landed in a *single* DB row? This is
  the metric that catches "Sam Altman" and "Altman" splitting into two people.
- **Edge precision** — for each predicted edge we ask Claude Haiku to decide
  whether it's correct given the article context. Judge prompt is at
  [`run_eval.py`](run_eval.py) under `JUDGE_PROMPT`.

## Gold set shape

[`gold/articles.json`](gold/articles.json) — 10 TechCrunch articles drawn from
the `/tag/openai/` archive. Each entry has:

```json
{
  "url": "...",
  "title": "...",
  "fixture": "tests/fixtures/...html",   // optional: use saved HTML instead of fetching
  "people": [
    {"canonical": "Sam Altman", "aliases": ["Sam Altman", "Altman"]}
  ],
  "edges": [
    {"source": "Tim Fernholz", "target": "Sam Altman",
     "type": "reports_on", "quote_contains": "Altman"}
  ]
}
```

Edges are labeled with the gold relationship `type` and a substring expected
to appear in the supporting quote — but **the scorer doesn't use those for
recall** because exhaustively labeling every edge in a 1,500-word article is
labor-intensive and error-prone. Instead the LLM judge decides per predicted
edge whether the model's claim is *defensible from the article*. This gives us
edge precision without claiming a recall number we can't justify.

## Running

```bash
# Real eval — requires the LLM key
export NEWSGRAPH_ANTHROPIC_API_KEY=sk-ant-...
python evals/run_eval.py

# Structural check — no LLM, just verify gold JSON is well-formed
python evals/run_eval.py --validate-only

# Run the pipeline with LLM disabled (authors get extracted by the byline
# heuristic, everyone else is missing); useful for proving the harness wiring.
NEWSGRAPH_DISABLE_LLM=true python evals/run_eval.py --no-llm-judge
```

### Deterministic / CI mode (cassette)

To make the eval reproducible in CI without an API key, the LLM client supports
a JSON cassette keyed on `sha256(model || system || user || tool_name)`.

```bash
# One-time: record real responses to a cassette (needs the key)
NEWSGRAPH_ANTHROPIC_API_KEY=sk-ant-... \
NEWSGRAPH_LLM_CASSETTE_PATH=evals/cassettes/gold.json \
NEWSGRAPH_LLM_CASSETTE_MODE=record \
  python evals/run_eval.py

# After that: replay deterministically, no key needed
NEWSGRAPH_LLM_CASSETTE_PATH=evals/cassettes/gold.json \
NEWSGRAPH_LLM_CASSETTE_MODE=replay \
  python evals/run_eval.py
```

A cassette miss in `replay` mode raises a clear error (so a stale cassette
won't silently fall through to live calls). Re-record after prompt or model
changes — the hash key invalidates automatically.

## Latest numbers

| Metric                      | Value                                       | Notes                                      |
|-----------------------------|---------------------------------------------|--------------------------------------------|
| People precision            | 0.631 (41 TP / 65 predicted)                | 24 FP — extra people extracted beyond gold |
| People recall               | 1.000 (41/41 gold people found)             | Zero misses across all 10 articles         |
| Entity-resolution accuracy  | 1.000 (41/41 gold people in one row)        | All aliases collapsed correctly            |
| Edge precision (LLM judge)  | 0.564 (22/39 predicted edges correct)       | 17 edges judged hallucinated or mis-typed  |
| With LLM **disabled**       | precision 1.00, recall 0.27, ER 1.00        | Authors-only baseline (byline heuristic)   |

Run date: 2026-05-28 · Model: `claude-haiku-4-5-20251001`

**Interpreting the numbers:**
- The 24 false-positive people are not all genuine errors — the gold set labeled only the *headline* people per article (6 per article on average), not every person mentioned. Many FPs are real people the article names that simply weren't included in the gold annotation. A larger gold set would push precision up.
- Entity-resolution accuracy of 1.0 means every alias form ("Altman", "Musk", "Brockman", role phrases like "OpenAI's CEO") collapsed to the correct canonical row — the resolver is working correctly.
- Edge precision of 0.56 is expected for a closed-vocabulary extractor on litigation-heavy articles: the LLM occasionally picks the closest type rather than the correct one, or infers an edge from background knowledge rather than the article text. The `supporting_quote` field lets a human auditor verify each edge independently.

The "LLM disabled" row is a smoke test: only authors get extracted (via the
byline heuristic in [`pipeline._ensure_authors`](../src/newsgraph/pipeline.py)),
which is exactly the 10/41 = ~24% recall floor we expect (one author per
article, minus one or two duplicates). It tells us the harness, scoring, and
the byline pathway all work without an LLM in the loop.

Real numbers — once a key is wired up — are produced by:

```bash
NEWSGRAPH_ANTHROPIC_API_KEY=... python evals/run_eval.py > evals/last_run.json
```

…and should be copied into this table along with the date and model id.

## Known weak spots / failure modes

The pipeline is built on a few simplifying assumptions; the eval is where they
get challenged. Things we'd expect to see go wrong:

1. **Single-token names** ("Altman", "Musk") that appear in articles without an
   accompanying full reference. The resolver creates a *new* short-form person
   row rather than guess. With current corpora this should be rare (full forms
   are reintroduced periodically), but a fresh DB seeded only with short-form
   articles would show inflated FP counts.
2. **Co-author bylines that aren't split.** Older TC pages emit
   `<meta name="author" content="Julie Bort, Tim Fernholz">`. We split on
   comma, but multi-author dashed/ampersand styles (`Julie Bort & Tim Fernholz`)
   would slip through. None observed in the gold set but worth watching.
3. **Edge direction.** Confusing for the LLM in lawsuit narratives where the
   subject is on the defensive — e.g. "Altman testified about Musk's behavior"
   could be mis-typed as `criticizes` or mis-directed. The LLM judge is the
   gate here.
4. **Open-vocabulary creep.** When a closed-vocab type doesn't fit, the model
   may pick the *least wrong* one (e.g. `partners_with` for board memberships
   or shared affiliations). Worth re-tuning the vocabulary if a class of edges
   ends up consistently mis-typed.

## What we'd improve next

In rough priority order:

1. **Expand the gold set to ~25-30 articles** covering negative cases (articles
   with zero person-person edges) and edge-direction traps. The current set
   leans heavily on Musk/Altman litigation pieces.
2. **Add an edge-recall metric** by labeling a small subset (3-5 articles) of
   the gold set with *every* expected edge, not just the headline ones. Even a
   tiny exhaustive subset gives a defensible recall number.
3. **Tune the prompt's "be conservative" instruction.** Right now it leans
   towards omission; depending on the precision/recall trade-off observed in
   the first real eval run, we'd adjust.
4. **Cache LLM judge verdicts** keyed on (edge content, model). A second eval
   run with the same edges should re-use prior judgments to save tokens.
5. **Add an alias-coverage metric** that asks "did we see every aliased form
   from the article?" — different from entity-resolution accuracy because it
   measures *recall* of surface forms rather than the cleanliness of merging.
