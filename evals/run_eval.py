"""Evaluation harness.

Runs the pipeline against the gold set in `evals/gold/articles.json` and
reports:

  - People: precision / recall (vs gold canonical names, using normalized-name
    set comparison).
  - Entity resolution accuracy: of the people we extracted, what fraction land
    in the same DB row as another surface form from the same gold person?
  - Edges: precision (via an LLM judge) over predicted edges.

Run mode is controlled by env vars:
  NEWSGRAPH_ANTHROPIC_API_KEY=...   -> real extraction + LLM judge
  NEWSGRAPH_DISABLE_LLM=true        -> structural validation only
                                       (asserts gold set is well-formed)

Usage:
    python -m evals.run_eval
    python evals/run_eval.py --gold evals/gold/articles.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from newsgraph.config import Settings
from newsgraph.db.repository import Repository
from newsgraph.extraction.extractor import Extractor
from newsgraph.pipeline import Pipeline
from newsgraph.resolution.resolver import normalize
from newsgraph.scraping.http_client import HTTPClient
from newsgraph.scraping.techcrunch import TechCrunchAdapter


JUDGE_PROMPT = """You are evaluating a relationship-extraction system.

A predicted edge is CORRECT iff:
  1. The relationship type roughly matches what the article actually says.
  2. The supporting_quote really appears (or paraphrases something that appears)
     in the article body and supports the asserted relationship.
  3. The direction (source -> target) is the right way around.

Be strict but fair: a near-synonym for the type (e.g. "criticizes" vs "opposes")
is acceptable as long as it's defensible from the article. A wrong direction is
NOT acceptable. A fabricated quote is NOT acceptable.

Respond with the `judge_edge` tool, returning a single boolean `correct` field
and a one-sentence `reason`.
"""


JUDGE_TOOL = {
    "name": "judge_edge",
    "description": "Return whether the predicted edge is correct.",
    "input_schema": {
        "type": "object",
        "properties": {
            "correct": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["correct", "reason"],
    },
}


@dataclass
class ArticleResult:
    url: str
    predicted_people: list[str]
    gold_people: list[list[str]]  # each inner = aliases for one gold person
    tp_people: int = 0
    fp_people: int = 0
    fn_people: int = 0
    er_correct: int = 0
    er_total: int = 0
    edge_correct: int = 0
    edge_total: int = 0
    notes: list[str] = field(default_factory=list)


def _norm(s: str) -> str:
    return normalize(s)


def _gold_norms(person) -> set[str]:
    return {_norm(a) for a in person["aliases"] if _norm(a)}


def score_people(predicted_canonicals: list[str], gold: list[dict]) -> tuple[int, int, int, dict]:
    """Returns (tp, fp, fn, match_map) where match_map maps predicted_norm ->
    gold_canonical when matched."""
    gold_groups: list[set[str]] = [_gold_norms(p) for p in gold]
    gold_canonicals = [p["canonical"] for p in gold]
    matched_gold: set[int] = set()
    match_map: dict[str, str] = {}
    fp = 0
    for canonical in predicted_canonicals:
        n = _norm(canonical)
        hit = None
        for i, group in enumerate(gold_groups):
            if i in matched_gold:
                continue
            # A predicted canonical matches a gold person if its normalized form
            # equals any alias OR shares the last token AND tokens overlap.
            if n in group:
                hit = i
                break
            n_last = n.split()[-1] if n else ""
            if any(g.split()[-1] == n_last and len(g.split()) > 1 for g in group):
                hit = i
                break
        if hit is not None:
            matched_gold.add(hit)
            match_map[n] = gold_canonicals[hit]
        else:
            fp += 1
    tp = len(matched_gold)
    fn = len(gold) - tp
    return tp, fp, fn, match_map


def evaluate(gold_path: Path, use_llm: bool, judge_with_llm: bool) -> dict:
    gold = json.loads(gold_path.read_text())
    results: list[ArticleResult] = []

    tmpdir = Path(tempfile.mkdtemp(prefix="newsgraph-eval-"))
    settings = Settings(
        db_path=tmpdir / "eval.sqlite3",
        cache_dir=tmpdir / "cache",
        disable_llm=not use_llm,
    )
    repo = Repository(settings.db_path)
    http = HTTPClient(
        user_agent=settings.user_agent,
        rate_limit_s=settings.request_rate_limit_s,
        timeout_s=settings.request_timeout_s,
        max_retries=settings.max_retries,
        cache_dir=Path("evals/cache"),  # reuse the pre-cached fetches
    )
    extractor = Extractor(settings)
    pipeline = Pipeline(
        settings=settings,
        repo=repo,
        http=http,
        extractor=extractor,
        adapters=[TechCrunchAdapter()],
    )

    # Pre-seed pipeline cache with fixture HTMLs (avoid network if cached file present).
    for item in gold:
        if "fixture" in item:
            src = ROOT / item["fixture"]
            if src.exists():
                key = hashlib.sha1(item["url"].encode("utf-8")).hexdigest()
                tgt = http._cache_path(item["url"])  # type: ignore[attr-defined]
                if tgt is not None and not tgt.exists():
                    tgt.write_text(src.read_text())

    try:
        for item in gold:
            url = item["url"]
            stats = pipeline.process_article(url)
            ar = _score_article(repo, url, item, judge_with_llm=judge_with_llm and use_llm, judge=extractor.llm if use_llm else None)
            ar.notes.extend(stats.errors)
            results.append(ar)
    finally:
        pipeline.close()

    return _aggregate(results)


def _score_article(repo: Repository, url: str, gold_item: dict, *, judge_with_llm: bool, judge) -> ArticleResult:
    # All mentions for this article, joined with their person row.
    mentions = list(
        repo._conn.execute(  # noqa: SLF001 — tests reach in too
            """
            SELECT m.surface_form, m.person_id, p.canonical_name
              FROM mentions m JOIN people p ON p.id = m.person_id
             WHERE m.article_url = ?
            """,
            (url,),
        )
    )
    predicted_canonicals = sorted({r["canonical_name"] for r in mentions})
    tp, fp, fn, match_map = score_people(predicted_canonicals, gold_item["people"])

    # Entity-resolution score:
    # for each gold person, do all of their surface forms that the LLM emitted
    # map to a SINGLE person row?
    er_correct = 0
    er_total = 0
    surface_to_pid: dict[str, int] = {r["surface_form"]: r["person_id"] for r in mentions}
    for gp in gold_item["people"]:
        seen_pids: set[int] = set()
        for alias in gp["aliases"]:
            for surface, pid in surface_to_pid.items():
                if _norm(surface) == _norm(alias):
                    seen_pids.add(pid)
        if not seen_pids:
            continue
        er_total += 1
        if len(seen_pids) == 1:
            er_correct += 1

    # Edges: LLM judge for precision.
    edges = list(
        repo._conn.execute(
            """
            SELECT e.type, e.explanation, e.supporting_quote,
                   ps.canonical_name AS source_name,
                   pt.canonical_name AS target_name
              FROM edges e
              JOIN people ps ON ps.id = e.source_id
              JOIN people pt ON pt.id = e.target_id
             WHERE e.article_url = ?
            """,
            (url,),
        )
    )
    edge_total = len(edges)
    edge_correct = 0
    if judge_with_llm and judge is not None and edges:
        for e in edges:
            verdict = _judge_edge(judge, gold_item, dict(e))
            if verdict:
                edge_correct += 1

    return ArticleResult(
        url=url,
        predicted_people=predicted_canonicals,
        gold_people=[gp["aliases"] for gp in gold_item["people"]],
        tp_people=tp, fp_people=fp, fn_people=fn,
        er_correct=er_correct, er_total=er_total,
        edge_correct=edge_correct, edge_total=edge_total,
    )


def _judge_edge(llm, gold_item, edge: dict) -> bool:
    user = (
        f"Article: {gold_item['url']}\n"
        f"Predicted edge: {edge['source_name']} --[{edge['type']}]--> {edge['target_name']}\n"
        f"Explanation: {edge['explanation']}\n"
        f"Supporting quote: {edge['supporting_quote']}\n"
    )
    try:
        out = llm.call_tool(JUDGE_PROMPT, user, JUDGE_TOOL)
        return bool(out.get("correct"))
    except Exception:
        return False


def _aggregate(results: list[ArticleResult]) -> dict:
    tp = sum(r.tp_people for r in results)
    fp = sum(r.fp_people for r in results)
    fn = sum(r.fn_people for r in results)
    er_c = sum(r.er_correct for r in results)
    er_t = sum(r.er_total for r in results)
    eg_c = sum(r.edge_correct for r in results)
    eg_t = sum(r.edge_total for r in results)
    return {
        "articles": len(results),
        "people": {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if (tp + fp) else None,
            "recall":    tp / (tp + fn) if (tp + fn) else None,
        },
        "entity_resolution": {
            "correct": er_c, "total": er_t,
            "accuracy": er_c / er_t if er_t else None,
        },
        "edges": {
            "correct": eg_c, "total": eg_t,
            "precision": eg_c / eg_t if eg_t else None,
        },
        "per_article": [r.__dict__ for r in results],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--gold", default=str(ROOT / "evals/gold/articles.json"))
    p.add_argument("--no-llm-judge", action="store_true",
                   help="Skip the LLM-as-judge step (precision will report as 0/0)")
    p.add_argument("--validate-only", action="store_true",
                   help="Don't run the pipeline; just check gold-set well-formedness.")
    args = p.parse_args()

    if args.validate_only:
        out = _validate_only(Path(args.gold))
        print(json.dumps(out, indent=2))
        return

    settings = Settings()
    # Cassette replay is a valid LLM source even with no API key — that's the
    # whole point of recording one. Treat it as "LLM available".
    cassette_replay = (
        settings.llm_cassette_path is not None and settings.llm_cassette_mode == "replay"
    )
    use_llm = (not settings.disable_llm) and (
        bool(settings.anthropic_api_key) or cassette_replay
    )
    out = evaluate(Path(args.gold), use_llm=use_llm, judge_with_llm=not args.no_llm_judge)
    print(json.dumps(out, indent=2, default=str))


def _validate_only(path: Path) -> dict:
    """Sanity-check the gold set without running the pipeline. Useful in CI."""
    data = json.loads(path.read_text())
    problems: list[str] = []
    for item in data:
        canonicals = {p["canonical"] for p in item["people"]}
        for e in item.get("edges", []):
            if e["source"] not in canonicals:
                problems.append(f"{item['url']}: edge source {e['source']!r} not in people list")
            if e["target"] not in canonicals:
                problems.append(f"{item['url']}: edge target {e['target']!r} not in people list")
    return {
        "articles": len(data),
        "people_total": sum(len(i["people"]) for i in data),
        "edges_total": sum(len(i.get("edges", [])) for i in data),
        "problems": problems,
    }


if __name__ == "__main__":
    main()
