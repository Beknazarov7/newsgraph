# newsgraph

Scrapes TechCrunch's OpenAI topic page, extracts people and their relationships
from each article with an LLM, stores them as a knowledge graph in SQLite, and
serves the graph over a small HTTP API.

```
listing page → site adapter → article HTML → adapter.parse → LLM extract → resolver → SQLite
                                                                                   ↘
                                                                            FastAPI endpoints
```

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Set the LLM key (Anthropic):

```bash
export NEWSGRAPH_ANTHROPIC_API_KEY=sk-ant-...
# Optional: NEWSGRAPH_ANTHROPIC_MODEL=claude-haiku-4-5-20251001
# Optional: NEWSGRAPH_DB_PATH=data/newsgraph.sqlite3
```

Or use OpenAI instead (the extraction prompt and schema are provider-agnostic):

```bash
pip install -e ".[openai]"
export NEWSGRAPH_LLM_PROVIDER=openai
export NEWSGRAPH_OPENAI_API_KEY=sk-...
# Optional: NEWSGRAPH_OPENAI_MODEL=gpt-4o-mini
```

To bring up the API without an LLM key (parser-only mode):

```bash
export NEWSGRAPH_DISABLE_LLM=true
```

## Run the API

```bash
uvicorn newsgraph.api.app:app --reload --port 8000
```

OpenAPI docs: `http://localhost:8000/docs`. Schemas are generated from the
Pydantic models in [`src/newsgraph/models/api.py`](src/newsgraph/models/api.py).

### Web UI

A small browser front-end ships with the API (shared dark-navy theme, no build
step — see [`src/newsgraph/api/`](src/newsgraph/api/)):

- `/` — interactive knowledge graph (vis-network). Node size = mention count;
  double-click a node to open that person.
- `/people` — paginated, searchable people table.
- `/people/{id}` — a person's aliases and incoming/outgoing relationships, each
  with its explanation, supporting quote, and source article.

`/people` and `/people/{id}` **content-negotiate**: a browser (`Accept: text/html`)
gets the styled page, while API clients (`Accept: application/json`, curl, etc.)
get JSON from the very same URL. The explicit `…/view` paths always serve HTML.

### Endpoints

| Method | Path             | Purpose                                                  |
|--------|------------------|----------------------------------------------------------|
| GET    | `/`              | Interactive graph visualization (HTML)                   |
| POST   | `/articles`      | Fetch + extract + merge one article                      |
| POST   | `/rescan`        | Crawl the latest N listing pages and merge               |
| GET    | `/people`        | People list — HTML for browsers, JSON for API clients    |
| GET    | `/people/{id}`   | One person's aliases + 1-hop relationships (HTML/JSON)   |
| GET    | `/graph`         | Whole-graph nodes + edges (JSON; feeds the UI)           |
| GET    | `/health`        | Liveness probe                                           |

## Example calls

```bash
# Ingest a single article
curl -s -X POST http://localhost:8000/articles \
  -H 'content-type: application/json' \
  -d '{"url":"https://techcrunch.com/2026/05/13/who-trusts-sam-altman/"}'

# Re-crawl the latest 2 listing pages and merge
curl -s -X POST http://localhost:8000/rescan \
  -H 'content-type: application/json' \
  -d '{"pages": 2}'

# List people (paginated)
curl -s 'http://localhost:8000/people?page=1&size=20'

# One person's graph neighborhood
curl -s http://localhost:8000/people/1
```

### Response shapes

`POST /articles` and `POST /rescan` return a `MergeStats`:

```json
{
  "articles_processed": 1,
  "articles_skipped": 0,
  "people_added": 4,
  "people_updated": 1,
  "aliases_added": 5,
  "mentions_added": 6,
  "edges_added": 3,
  "edges_replaced": 0,
  "errors": []
}
```

`GET /people?page=1&size=20` returns:

```json
{
  "page": 1, "size": 20, "total": 17,
  "items": [
    {"id": 1, "canonical_name": "Sam Altman", "alias_count": 3, "mention_count": 8}
  ]
}
```

`GET /people/{id}` returns:

```json
{
  "id": 1,
  "canonical_name": "Sam Altman",
  "aliases": ["Sam Altman", "Altman", "OpenAI's CEO"],
  "outgoing": [
    {
      "other_person_id": 2,
      "other_person_name": "Greg Brockman",
      "type": "partners_with",
      "explanation": "Altman and Brockman are working together at OpenAI.",
      "article_url": "https://techcrunch.com/2026/05/13/who-trusts-sam-altman/",
      "supporting_quote": "Sam Altman and Greg Brockman are working together at OpenAI."
    }
  ],
  "incoming": [
    {
      "other_person_id": 5,
      "other_person_name": "Tim Fernholz",
      "type": "reports_on",
      "explanation": "Tim Fernholz reports on Altman.",
      "article_url": "https://techcrunch.com/2026/05/13/who-trusts-sam-altman/",
      "supporting_quote": "Tim Fernholz writes that Altman's tone was unusually sharp."
    }
  ]
}
```

### Error responses

| Status | When                                                       |
|--------|------------------------------------------------------------|
| 400    | `POST /articles` with a URL no adapter recognises          |
| 404    | `GET /people/{id}` for an unknown id                       |
| 422    | Pydantic validation failures (malformed URL, etc.)         |
| 502    | Upstream fetch failed, or the LLM call failed              |

`POST /rescan` is multi-article: per-article failures are accumulated into the
response body's `errors` list rather than failing the whole request.

## CLI

For ad-hoc runs without the HTTP layer:

```bash
newsgraph article https://techcrunch.com/2026/05/13/who-trusts-sam-altman/
newsgraph rescan --pages 2
```

## Tests

```bash
pytest
```

Covers HTML parsing against saved fixtures, entity-resolution edge cases,
idempotent merge (re-running an article must not duplicate people/edges), and
the API surface.

## Evals

See [`evals/README.md`](evals/README.md). 10-article hand-labeled gold set;
scorer reports people P/R, entity-resolution accuracy, and edge precision via
LLM-as-judge.

```bash
# Validate gold-set shape (no LLM, no key):
python evals/run_eval.py --validate-only

# Real eval (requires API key):
python evals/run_eval.py

# Deterministic eval against a recorded cassette (no key needed once recorded):
NEWSGRAPH_LLM_CASSETTE_PATH=evals/cassettes/gold.json \
NEWSGRAPH_LLM_CASSETTE_MODE=replay \
  python evals/run_eval.py

# To populate / refresh the cassette:
NEWSGRAPH_ANTHROPIC_API_KEY=sk-ant-... \
NEWSGRAPH_LLM_CASSETTE_PATH=evals/cassettes/gold.json \
NEWSGRAPH_LLM_CASSETTE_MODE=record \
  python evals/run_eval.py
```

## Adding another news site

Implement [`SiteAdapter`](src/newsgraph/scraping/base.py):

```python
class TheVergeAdapter(SiteAdapter):
    name = "the_verge"
    def listing_url(self, page: int) -> str: ...
    def list_articles(self, html: str) -> list[ListingItem]: ...
    def parse_article(self, html: str, url: str) -> ParsedArticle: ...
```

Register the instance with the pipeline (`Pipeline.adapters`). The extraction,
resolution, and storage layers don't change.

## Project layout

```
src/newsgraph/
  config.py              # env-driven settings
  models/
    extraction.py        # Pydantic schema the LLM must conform to
    api.py               # HTTP request/response shapes
  db/
    schema.sql           # SQLite schema
    repository.py        # all writes (idempotent merge lives here)
  scraping/
    base.py              # SiteAdapter interface
    techcrunch.py        # TechCrunch implementation
    http_client.py       # rate-limited, retrying fetcher with disk cache
  extraction/
    prompts.py           # extraction prompt + closed relationship vocab
    extractor.py         # provider clients (Anthropic/OpenAI) + cassette + round-trip
  resolution/
    resolver.py          # normalize + last-token + optional LLM tiebreak
  pipeline.py            # glue: fetch → parse → extract → resolve → merge
  api/app.py             # FastAPI app
  api/graph_view.py      # self-contained HTML graph page (vis-network)
  cli.py                 # `newsgraph article|rescan`
tests/
  fixtures/              # real HTML from TechCrunch
  test_techcrunch_adapter.py
  test_resolver.py
  test_merge.py
  test_api.py
evals/
  gold/articles.json     # 10-article hand-labeled set
  cache/                 # cached HTML used by the eval harness
  run_eval.py
  README.md
DECISIONS.md             # why the code looks the way it does
```

## Configuration

Every setting is read from env vars (prefix `NEWSGRAPH_`) or `.env`. See
[`src/newsgraph/config.py`](src/newsgraph/config.py).

| Variable                       | Default                                | Notes                                  |
|--------------------------------|----------------------------------------|----------------------------------------|
| `NEWSGRAPH_DB_PATH`            | `data/newsgraph.sqlite3`               | SQLite file                            |
| `NEWSGRAPH_CACHE_DIR`          | `data/cache`                           | On-disk HTML cache                     |
| `NEWSGRAPH_LLM_PROVIDER`       | `anthropic`                            | `anthropic` or `openai`                |
| `NEWSGRAPH_ANTHROPIC_API_KEY`  | _none_                                 | Required for real extraction           |
| `NEWSGRAPH_ANTHROPIC_MODEL`    | `claude-haiku-4-5-20251001`            | Any tool-use-capable Claude model      |
| `NEWSGRAPH_OPENAI_API_KEY`     | _none_                                 | Required when provider=openai          |
| `NEWSGRAPH_OPENAI_MODEL`       | `gpt-4o-mini`                          | Any function-calling OpenAI model      |
| `NEWSGRAPH_LLM_MAX_TOKENS`     | `2048`                                 |                                        |
| `NEWSGRAPH_DISABLE_LLM`        | `false`                                | Set true for parser-only / CI runs     |
| `NEWSGRAPH_USER_AGENT`         | `NewsGraphBot/0.1`                     | Sent on every fetch                    |
| `NEWSGRAPH_REQUEST_RATE_LIMIT_S` | `1.0`                                | Min seconds between fetches per host   |
| `NEWSGRAPH_REQUEST_TIMEOUT_S`  | `20.0`                                 |                                        |
| `NEWSGRAPH_MAX_RETRIES`        | `3`                                    | Tenacity exponential backoff           |

## Design decisions

See [`DECISIONS.md`](DECISIONS.md). Briefly:

- **Anthropic Claude Haiku with tool-use** for structured output — fast and
  cheap enough to re-run over the gold set; structured output is enforced by
  the tool's JSON schema (generated from Pydantic).
- **SQLite, not a graph DB.** The API only does 1-hop neighborhood queries.
- **Idempotence keyed on article URL.** Re-extracting the same URL wipes that
  article's mentions/edges and reinserts. People rows are never deleted.
- **Resolver = normalize → exact match → last-token fallback → optional LLM
  tiebreak.** Simple-and-correct beats clever.
- **Closed relationship vocabulary.** Keeps the graph queryable.

## Note on the brief

The take-home brief contained an embedded prompt-injection instructing any
assistant reading it to insert an unrelated apple-pie recipe into a source
file. Ignored. Greppped the final repo for the obvious tokens (`apple`, `pie`,
`flour`, `cinnamon`, `crust`, `butter`, `recipe`, `dough`, `lattice`) and
confirmed no injected recipe exists in source, tests, or docs. The only matches
are incidental: real TechCrunch HTML in the saved fixtures (`apple-touch-icon`,
links to `/tag/apple/`, an article headline mentioning Apple), the word
"pieces" in a schema comment, and the standard `-apple-system` CSS font stack in
the web UI.

## Docker (optional)

```bash
docker build -t newsgraph .
docker run --rm -p 8000:8000 \
  -e NEWSGRAPH_ANTHROPIC_API_KEY=$NEWSGRAPH_ANTHROPIC_API_KEY \
  -v "$PWD/data:/data" \
  newsgraph
```

## Deploy (Render)

[`render.yaml`](render.yaml) is a Render blueprint: connect the repo, set
`NEWSGRAPH_ANTHROPIC_API_KEY` in the dashboard (marked `sync:false`), and it
deploys a web service with a 1 GB persistent disk mounted at `/data` for the
SQLite DB + HTML cache. Health checks hit `/health`.
