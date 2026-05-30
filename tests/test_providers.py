"""LLM provider selection + cassette composition.

These exercise the provider abstraction without ever hitting a network: we only
check which client `build_llm_client` returns, that a missing key raises a clear
error, and that the cassette wrapper keys on the *active* provider's model.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from newsgraph.config import Settings
from newsgraph.extraction.extractor import (
    CassetteLLMClient,
    LLMClient,
    OpenAIClient,
    build_llm_client,
)


def test_default_provider_is_anthropic() -> None:
    client = build_llm_client(Settings(disable_llm=True))
    assert isinstance(client, LLMClient)
    assert client.provider == "anthropic"
    assert client.model == "claude-haiku-4-5-20251001"


def test_openai_provider_selected() -> None:
    client = build_llm_client(Settings(llm_provider="openai", openai_model="gpt-4o-mini"))
    assert isinstance(client, OpenAIClient)
    assert client.provider == "openai"
    assert client.model == "gpt-4o-mini"


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="unknown"):
        build_llm_client(Settings(llm_provider="llama"))


def test_openai_missing_key_raises_clear_error() -> None:
    """Key is validated before the SDK import, so the message is useful even if
    the optional `openai` package isn't installed."""
    client = OpenAIClient(Settings(llm_provider="openai", openai_api_key=None))
    with pytest.raises(RuntimeError, match="NEWSGRAPH_OPENAI_API_KEY"):
        client._get_client()


def test_cassette_keys_on_active_provider_model(tmp_path: Path) -> None:
    """The cassette key includes the inner client's model, so an Anthropic and
    an OpenAI run never collide in the same cassette file."""
    schema = {"name": "submit_extraction", "input_schema": {"type": "object"}}
    anthropic_cass = CassetteLLMClient(
        LLMClient(Settings()), tmp_path / "a.json", "record"
    )
    openai_cass = CassetteLLMClient(
        OpenAIClient(Settings(llm_provider="openai")), tmp_path / "o.json", "record"
    )
    k_anthropic = anthropic_cass._key("sys", "user", schema)
    k_openai = openai_cass._key("sys", "user", schema)
    assert k_anthropic != k_openai


def test_cassette_replay_miss_raises(tmp_path: Path) -> None:
    cass = CassetteLLMClient(LLMClient(Settings()), tmp_path / "empty.json", "replay")
    with pytest.raises(RuntimeError, match="cassette miss"):
        cass.call_tool("sys", "user", {"name": "t", "input_schema": {}})
