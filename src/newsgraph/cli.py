"""Tiny CLI for ad-hoc runs without going through HTTP. Keeps the README
example sane and is handy for the eval script."""
from __future__ import annotations

import argparse
import json
import logging

from .pipeline import Pipeline


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser("newsgraph")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_article = sub.add_parser("article", help="Process one article URL")
    p_article.add_argument("url")

    p_rescan = sub.add_parser("rescan", help="Crawl N listing pages")
    p_rescan.add_argument("--pages", type=int, default=1)

    args = p.parse_args()
    pipeline = Pipeline.default()
    try:
        if args.cmd == "article":
            stats = pipeline.process_article(args.url)
        else:
            stats = pipeline.rescan(args.pages)
        print(json.dumps(stats.model_dump(), indent=2))
    finally:
        pipeline.close()


if __name__ == "__main__":
    main()
