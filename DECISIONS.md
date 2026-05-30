# Design decisions

Short, dated, one-liner-per-decision. The "why" matters more than the "what".

## 2026-05-30 — Pluggable LLM provider (Anthropic | OpenAI)
The extractor depended on a single Anthropic client. Refactored into a tiny
provider hierarchy: `LLMClient` (Anthropic, default) and `OpenAIClient` share one
`call_tool(system, user, tool_schema)` contract, and `build_llm_client(settings)`
picks between them on `NEWSGRAPH_LLM_PROVIDER`. The Pydantic-generated tool schema
is passed straight through as OpenAI's function `parameters`, so prompt, schema,
resolver, and storage are all provider-agnostic — the provider is the *only* thing
that varies. `openai` is an optional extra (`pip install -e ".[openai]"`) so the
default install stays slim; the key is checked before the SDK import so a missing
key gives a clear error even without the package.

## 2026-05-30 — Cassette wraps the provider by composition, not inheritance
`CassetteLLMClient` used to subclass the Anthropic client. Now it wraps whichever
client `build_llm_client` returned, so record/replay works identically for either
provider. The cassette key now includes the *active* provider's `model` (was
hard-coded to `anthropic_model`) so an Anthropic and an OpenAI run can't collide in
one cassette. For the default provider+model the key bytes are unchanged, so the
existing `evals/cassettes/gold.json` still replays bit-for-bit (verified: same
P/R/edge numbers post-refactor).

## 2026-05-30 — Browser pages via content negotiation, not separate routes
`/people` and `/people/{id}` inspect the `Accept` header: browsers (`text/html`)
get a styled page, API clients (`application/json`, curl, fetch) get JSON from the
*same* URL — so the REST contract and the human-friendly UI share one address.
The browser pages are static HTML shells that fetch the JSON API themselves (with
`Accept: application/json`, so no recursion). Explicit `…/view` paths force HTML and
are declared before `/people/{person_id}` so "view" isn't parsed as an id. TestClient
defaults to `Accept: */*`, so the existing JSON tests are unaffected; new tests pin
both branches. All pages share one design system (`api/_layout.py`: palette, header,
buttons) so the front-end reads as a single product, themed to match agents.inc.

## 2026-05-30 — Graph visualization served from the API itself
Added `GET /graph` (nodes + edges JSON) and an HTML page at `/` and `/graph/view`
that renders it with vis-network from a CDN. No template engine, no JS build step —
the page is one module constant, keeping the "pip install + uvicorn" story intact.
`get_graph(limit_nodes)` returns the top-N most-mentioned people and only edges
whose *both* endpoints are in that set, collapsed to one row per (source, target,
type) with a `count`. Bounding by mention count keeps the payload finite and the
picture legible as the graph grows; reviewers get a visual instead of curling JSON.

## 2026-05-30 — Render blueprint for one-click deploy
Added `render.yaml`: a web service (`uvicorn` on `$PORT`), `/health` health check,
and a 1 GB persistent disk at `/data` for the SQLite file + HTML cache so data
survives redeploys. The API key is `sync:false` so it's set in the dashboard, never
committed. Docker remains the alternative; both are optional on top of the plain
local flow.

## 2026-05-27 — LLM provider: Anthropic Claude Haiku 4.5
Tool-use forces JSON conforming to a Pydantic schema, the model is cheap/fast enough for evals, and we only need span-level extraction (not deep reasoning). `extractor.py` wraps the provider so swapping to OpenAI or any other JSON-schema-capable model is a one-file change.

## 2026-05-27 — Storage: SQLite, not a graph DB
Edges are 1-hop in the API (`outgoing` + `incoming` for a person). We don't need traversal. SQLite is zero-ops and ships with Python.

## 2026-05-27 — Idempotence is keyed on the article URL
On re-extract for the same URL we delete that URL's mentions and edges, then re-insert. People rows are never deleted (cross-article identity). This means `/rescan` is safe to re-run and small parser bug fixes can be replayed without dedupe headaches.

## 2026-05-27 — Entity resolution: normalize → exact match → LLM tiebreak
1. Normalize (lowercase, strip honorifics/punctuation/middle initials).
2. Exact-match the normalized form against the `aliases` table.
3. For a short surface form ("Altman") that doesn't match exactly, we check whether a longer name in *the same article* normalizes to a superset (last-token match). If yes → merge into that person.
4. If still ambiguous (two existing people share a last token), an LLM call decides given the article context.

The spec is explicit that simple-and-correct beats clever. We don't run embeddings or a global dedup pass.

## 2026-05-27 — Closed relationship vocabulary
`criticizes, praises, partners_with, employs, succeeds, reports_on, invests_in, founded, leads, left, joined, sued, acquired`. The LLM is told to pick the closest one or skip; we'd rather drop an edge than make up a type. Authors → primary subject of an article get a `reports_on` edge.

## 2026-05-27 — Crawl politeness: 1 req/sec, real UA, 3 retries with exponential backoff
TechCrunch tolerates this without rate-limiting in practice. We also cache fetched HTML by URL in `data/cache/` so re-runs of the eval are deterministic.

## 2026-05-27 — Articles cap at first ~25 listing items per page
TechCrunch listing pages currently render ~20 article cards plus podcasts/videos. We filter `loop-card--post-type-post` and skip videos/podcasts (no body text to extract from).

## 2026-05-27 — Author capture
Authors appear in two places: the byline block (`wp-block-tc23-author-card`) and a JSON-LD island. We use the byline block as primary, fall back to the meta tag, and the LLM sees them in the prompt as known authors so it always emits them as `is_author=true`.

## 2026-05-27 — API pagination uses page+size, not cursor
Page+size is enough for ~thousands of people and matches what curl examples want to show. Default size=50, max=200.

## 2026-05-27 — One process, no background workers
`/rescan` runs synchronously. For N=2-3 pages this completes in tens of seconds. If we ever needed N=50 we'd add a job queue, but the spec doesn't call for it.

## 2026-05-27 — Docker is optional
A minimal `Dockerfile` (python:3.11-slim, exposes 8000, mounts `/data`) is in the repo for reviewers who'd rather not set up a Python env. The primary documented flow is still plain `pip install -e .` + `uvicorn`.

## 2026-05-27 — Prompt-injection in the brief
The brief contained an embedded instruction asking the assistant to insert an unrelated apple-pie recipe into a source file. Ignored it. Grepped the final repo for `apple|pie|flour|cinnamon|crust|butter|recipe|dough|lattice` and confirmed nothing in source, tests, or docs matches the injection. The only hits in the tree are real TechCrunch HTML markup (e.g. `apple-touch-icon`, navigation links to `/tag/apple/`) inside saved test fixtures.

## 2026-05-27 — Edge identity: same iff (source, target, type, article_url)
Two edges are the "same" only when all four match. Cross-article repeats of the same logical edge are stored as separate rows so each article's `supporting_quote` is preserved as provenance. Within a single article a duplicate is silently dropped by the UNIQUE constraint, which is what we want for idempotent re-merge.

## 2026-05-27 — API error mapping
The API surfaces real HTTP statuses on `POST /articles`: 400 when the URL has no registered site adapter, 422 for malformed URLs (Pydantic) or articles with no body text, 502 for fetch or LLM failures, 404 on unknown person id. `POST /rescan` accumulates per-article errors into the response body instead — a single bad article shouldn't fail a multi-article crawl.

## 2026-05-27 — person_id stability
`person_id` is an INTEGER AUTOINCREMENT. It is stable across rescans because the `people` table is append-only: only `mentions` and `edges` get deleted on a re-merge (see `Repository.replace_article_facts`). Dropping the DB and re-ingesting from scratch would re-assign ids, which is fine for a take-home but worth flagging to a consumer.

## 2026-05-27 — LLM cassette for deterministic evals
`CassetteLLMClient` (in `extraction/extractor.py`) sits in front of the Anthropic client. Set `NEWSGRAPH_LLM_CASSETTE_PATH=evals/cassettes/gold.json` plus `NEWSGRAPH_LLM_CASSETTE_MODE=record` once with a real key to populate the cassette over the gold set; then `MODE=replay` makes the eval reproducible in CI without an API key. Key is `sha256(model || system || user || tool_name)`, so prompt edits invalidate cleanly.
