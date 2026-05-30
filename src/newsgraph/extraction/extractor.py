"""LLM-backed extractor.

We use Anthropic Claude with tool-use as the structured-output mechanism: the
model is offered a single tool whose JSON schema is generated from
`ArticleExtraction`, and forced to call it. This gives us a guarantee the
response parses; if the model returns malformed JSON the SDK retries.

The class exposes a single async-free `extract` method. Pipeline code calls
this once per article.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..config import Settings
from ..models.extraction import ArticleExtraction
from .prompts import EXTRACTION_SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

# Body sent to the LLM is capped at ~32k chars. TechCrunch features rarely run
# past 8k; this is a safety belt for the occasional deep-dive that would
# otherwise blow the context window.
_BODY_CHAR_CAP = 32_000


class LLMClient:
    """Anthropic Claude tool-use client.

    This is the default provider and the base class for the provider hierarchy:
    every concrete client exposes the same `call_tool(system, user, tool_schema)`
    contract and a `model` property (used as part of the cassette key). Kept
    separate from the extractor so it can be stubbed in tests / eval-without-LLM
    mode, and so swapping providers is a one-line change (see `build_llm_client`).
    """

    provider = "anthropic"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None  # lazy init so importing the module doesn't need a key

    @property
    def model(self) -> str:
        """The model id this client talks to. Part of the cassette key so a
        provider/model switch can't silently reuse another provider's cassette."""
        return self.settings.anthropic_model

    def _get_client(self):
        if self._client is None:
            if not self.settings.anthropic_api_key:
                raise RuntimeError(
                    "NEWSGRAPH_ANTHROPIC_API_KEY is not set. "
                    "Either set the env var or set NEWSGRAPH_DISABLE_LLM=true for parser-only runs."
                )
            # Imported lazily so test runs without the SDK installed still work
            # for the parser-only tests.
            import anthropic  # type: ignore

            self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        return self._client

    def call_tool(self, system: str, user: str, tool_schema: dict[str, Any]) -> dict[str, Any]:
        """Force a single tool call and return its `input` dict."""
        client = self._get_client()
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.settings.llm_max_tokens,
            system=system,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": tool_schema["name"]},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input)
        raise RuntimeError("LLM response contained no tool_use block")


class OpenAIClient(LLMClient):
    """OpenAI chat-completions client using function-calling for structured output.

    Drop-in alternative to the Anthropic client: same `call_tool` contract,
    same Pydantic-generated JSON schema (`tool_schema["input_schema"]` is passed
    straight through as the function's `parameters`). Selected via
    NEWSGRAPH_LLM_PROVIDER=openai. The SDK is an optional dependency, imported
    lazily so the default Anthropic install stays slim.
    """

    provider = "openai"

    @property
    def model(self) -> str:
        return self.settings.openai_model

    def _get_client(self):
        if self._client is None:
            # Validate the key before importing so a missing key surfaces a clear
            # error even in environments where the openai SDK isn't installed.
            if not self.settings.openai_api_key:
                raise RuntimeError(
                    "NEWSGRAPH_OPENAI_API_KEY is not set. "
                    "Set it (and install the 'openai' extra) or switch NEWSGRAPH_LLM_PROVIDER back to anthropic."
                )
            import openai  # type: ignore

            self._client = openai.OpenAI(api_key=self.settings.openai_api_key)
        return self._client

    def call_tool(self, system: str, user: str, tool_schema: dict[str, Any]) -> dict[str, Any]:
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self.model,
            max_tokens=self.settings.llm_max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": tool_schema["name"],
                        "description": tool_schema.get("description", ""),
                        "parameters": tool_schema["input_schema"],
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": tool_schema["name"]}},
        )
        message = resp.choices[0].message
        if not message.tool_calls:
            raise RuntimeError("OpenAI response contained no tool call")
        return json.loads(message.tool_calls[0].function.arguments)


def build_llm_client(settings: Settings) -> LLMClient:
    """Factory: pick the LLM client implementation from settings.llm_provider.

    The provider is the only thing that varies — prompt, schema, resolver, and
    storage are provider-agnostic.
    """
    provider = (settings.llm_provider or "anthropic").lower()
    if provider == "openai":
        return OpenAIClient(settings)
    if provider == "anthropic":
        return LLMClient(settings)
    raise ValueError(
        f"unknown NEWSGRAPH_LLM_PROVIDER {settings.llm_provider!r}; expected 'anthropic' or 'openai'"
    )


class CassetteLLMClient:
    """Records/replays a JSON cassette of (request -> response) around any inner
    `LLMClient`.

    Composition, not inheritance: it wraps whichever provider client
    `build_llm_client` produced, so cassettes work the same for Anthropic or
    OpenAI. Two modes:
      replay — cassette is the only source of truth; a miss raises.
      record — use the cassette where present, fall back to the real API and
               save the response back to the cassette on a miss.

    Keyed on sha256(model + system + user + tool_name) so prompt or model edits
    invalidate the cassette automatically.
    """

    def __init__(self, inner: LLMClient, cassette_path: Path, mode: str):
        if mode not in {"replay", "record"}:
            raise ValueError(f"unknown cassette mode {mode!r}; expected 'replay' or 'record'")
        self.inner = inner
        self.cassette_path = cassette_path
        self.mode = mode
        self._cassette: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if self.cassette_path.exists():
            return json.loads(self.cassette_path.read_text())
        return {}

    def _save(self) -> None:
        self.cassette_path.parent.mkdir(parents=True, exist_ok=True)
        self.cassette_path.write_text(json.dumps(self._cassette, indent=2, sort_keys=True))

    def _key(self, system: str, user: str, tool_schema: dict[str, Any]) -> str:
        h = hashlib.sha256()
        h.update(self.inner.model.encode("utf-8"))
        h.update(b"\x00")
        h.update(system.encode("utf-8"))
        h.update(b"\x00")
        h.update(user.encode("utf-8"))
        h.update(b"\x00")
        h.update(tool_schema["name"].encode("utf-8"))
        return h.hexdigest()

    def call_tool(self, system: str, user: str, tool_schema: dict[str, Any]) -> dict[str, Any]:
        key = self._key(system, user, tool_schema)
        entry = self._cassette.get(key)
        if entry is not None:
            return entry["response"]
        if self.mode == "replay":
            raise RuntimeError(
                f"cassette miss in replay mode (key={key[:12]}…) at {self.cassette_path}. "
                "Re-record with NEWSGRAPH_LLM_CASSETTE_MODE=record."
            )
        response = self.inner.call_tool(system, user, tool_schema)
        self._cassette[key] = {
            "tool": tool_schema["name"],
            "user_preview": user[:240],
            "response": response,
        }
        self._save()
        return response


def _build_tool_schema() -> dict[str, Any]:
    """Generate the tool's JSON schema from the Pydantic model."""
    schema = ArticleExtraction.model_json_schema()
    return {
        "name": "submit_extraction",
        "description": "Submit the people and relationships you extracted from the article.",
        "input_schema": schema,
    }


TOOL_SCHEMA = _build_tool_schema()


class Extractor:
    """Owns the prompt + the LLM round-trip + validation against the Pydantic
    schema."""

    def __init__(self, settings: Settings, llm: LLMClient | None = None):
        self.settings = settings
        if llm is not None:
            self.llm = llm
        elif settings.llm_cassette_path is not None:
            self.llm = CassetteLLMClient(
                build_llm_client(settings),
                settings.llm_cassette_path,
                settings.llm_cassette_mode,
            )
        else:
            self.llm = build_llm_client(settings)

    def extract(self, *, url: str, title: str, authors: list[str], body: str) -> ArticleExtraction:
        if self.settings.disable_llm:
            logger.info("LLM disabled — returning empty extraction for %s", url)
            return ArticleExtraction(people=[], relationships=[])

        if len(body) > _BODY_CHAR_CAP:
            logger.info(
                "truncating body for %s from %d to %d chars", url, len(body), _BODY_CHAR_CAP
            )
            body = body[:_BODY_CHAR_CAP]

        user_prompt = build_user_prompt(url=url, title=title, authors=authors, body=body)
        raw = self.llm.call_tool(EXTRACTION_SYSTEM_PROMPT, user_prompt, TOOL_SCHEMA)
        try:
            return ArticleExtraction.model_validate(raw)
        except ValidationError as e:
            logger.warning("LLM returned schema-invalid JSON for %s: %s", url, e)
            logger.debug("offending payload: %s", json.dumps(raw)[:500])
            # Bail to empty rather than try to repair; the per-article merge
            # handles "no extraction" gracefully.
            return ArticleExtraction(people=[], relationships=[])
