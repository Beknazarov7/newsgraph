"""Runtime configuration. Loaded from env vars; sensible defaults for local dev."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings. Read once at startup."""

    model_config = SettingsConfigDict(env_prefix="NEWSGRAPH_", env_file=".env", extra="ignore")

    db_path: Path = Path("data/newsgraph.sqlite3")
    cache_dir: Path = Path("data/cache")

    user_agent: str = (
        "Mozilla/5.0 (compatible; NewsGraphBot/0.1; +https://example.invalid/newsgraph)"
    )
    request_timeout_s: float = 20.0
    request_rate_limit_s: float = 1.0  # min seconds between requests to the same host
    max_retries: int = 3

    # Which LLM backend the extractor uses: "anthropic" (default) or "openai".
    # Prompt, schema, resolver, and storage are all provider-agnostic; only the
    # client implementation changes (see extraction/extractor.py:build_llm_client).
    llm_provider: str = "anthropic"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-haiku-4-5-20251001"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    llm_max_tokens: int = 2048
    # If set, the extractor never talks to the LLM and emits an empty extraction.
    # Useful for parser-only tests and CI environments without a key.
    disable_llm: bool = False

    # Optional LLM response cassette. When set, LLMClient looks up responses by
    # request hash in this JSON file instead of (or in addition to) calling the
    # API. Modes:
    #   "replay" — only use the cassette; raise on a miss (good for CI).
    #   "record" — use cassette where present; call the real API on a miss and
    #              save the response back. Used to populate the cassette once.
    llm_cassette_path: Path | None = None
    llm_cassette_mode: str = "record"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Memoized accessor. Tests can call `reset_settings()` to reload from env."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
