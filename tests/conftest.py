"""Test fixtures shared by all suites."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `src/` importable without an editable install (works in CI sandboxes).
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def article_html_who_trusts() -> str:
    return (FIXTURES / "techcrunch_article_who_trusts_sam_altman.html").read_text()


@pytest.fixture
def article_html_musk_texts() -> str:
    return (FIXTURES / "techcrunch_article_musk_brockman_texts.html").read_text()


@pytest.fixture
def article_html_brockman_product() -> str:
    return (FIXTURES / "techcrunch_article_brockman_product.html").read_text()


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "techcrunch_tag_openai.html").read_text()


@pytest.fixture
def tmp_db_path(tmp_path) -> Path:
    return tmp_path / "test.sqlite3"


@pytest.fixture
def test_settings(tmp_path, monkeypatch):
    """A Settings instance pointed at a temp dir, with the LLM disabled."""
    from newsgraph.config import Settings, reset_settings

    monkeypatch.setenv("NEWSGRAPH_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("NEWSGRAPH_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("NEWSGRAPH_DISABLE_LLM", "true")
    reset_settings()
    yield Settings()
    reset_settings()
