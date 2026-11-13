"""GeminiProvider retry, error typing and output validation, with the SDK client mocked."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import errors
from pydantic import BaseModel

from autohub.assistant.llm import (
    FakeLLMProvider,
    GeminiProvider,
    LLMOutputError,
    LLMQuotaError,
    LLMRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
    parse_structured,
)


class Answer(BaseModel):
    name: str
    count: int


def response(text: str, finish: str = "STOP", *, thought: str | None = None) -> Any:
    parts = [SimpleNamespace(text=text, thought=False)]
    if thought:
        parts.insert(0, SimpleNamespace(text=thought, thought=True))
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(finish_reason=SimpleNamespace(value=finish), content=SimpleNamespace(parts=parts))
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=20, thoughts_token_count=5, total_token_count=125
        ),
        prompt_feedback=None,
    )


def api_error(code: int, status: str = "", details: list[dict[str, Any]] | None = None) -> errors.APIError:
    body = {"error": {"code": code, "status": status, "message": "boom", "details": details or []}}
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, body)


DAILY_QUOTA = [
    {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
    }
]
MINUTE_QUOTA = [
    {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}],
    },
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"},
]


class FakeModels:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def generate_content_stream(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return iter(outcome)


class FakeFiles:
    def __init__(self) -> None:
        self.uploaded: list[str] = []
        self.deleted: list[str] = []

    def upload(self, *, file: str, config: Any) -> Any:
        self.uploaded.append(file)
        return SimpleNamespace(
            name="files/abc", uri="https://files/abc", state=SimpleNamespace(name="ACTIVE")
        )

    def get(self, *, name: str) -> Any:  # pragma: no cover - only for PROCESSING
        return SimpleNamespace(name=name, uri="https://files/abc", state=SimpleNamespace(name="ACTIVE"))

    def delete(self, *, name: str) -> None:
        self.deleted.append(name)


def make(outcomes: list[Any], **kwargs: Any) -> tuple[GeminiProvider, FakeModels, list[float]]:
    models = FakeModels(outcomes)
    sleeps: list[float] = []
    client = SimpleNamespace(models=models, files=FakeFiles())
    provider = GeminiProvider(model="gemini-test", client=client, sleep=sleeps.append, **kwargs)
    return provider, models, sleeps


def call(provider: GeminiProvider, **kwargs: Any) -> Any:
    return provider.generate_structured(system="sys", prompt="p", schema=Answer, **kwargs)


def test_success_returns_validated_value_and_usage() -> None:
    provider, models, sleeps = make([response('{"name": "x", "count": 2}', thought="thinking...")])
    result = call(provider)
    assert result.value == Answer(name="x", count=2)
    assert result.raw_text == '{"name": "x", "count": 2}'  # thought parts excluded
    assert result.usage.input_tokens == 100 and result.usage.output_tokens == 25
    assert result.attempts == 1 and sleeps == []
    config = models.calls[0]["config"]
    assert config.response_mime_type == "application/json" and config.temperature == 0.0
    assert config.thinking_config.thinking_budget == 0


@pytest.mark.parametrize("code", [429, 500, 503])
def test_transient_errors_are_retried_with_backoff(code: int) -> None:
    provider, models, sleeps = make([api_error(code), api_error(code), response('{"name": "x", "count": 1}')])
    result = call(provider)
    assert result.attempts == 3 and len(models.calls) == 3
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] > 0


def test_server_retry_delay_is_honoured() -> None:
    provider, _, sleeps = make(
        [api_error(429, "RESOURCE_EXHAUSTED", MINUTE_QUOTA), response('{"name":"x","count":1}')]
    )
    call(provider)
    assert sleeps[0] >= 17.0


def test_daily_quota_raises_at_once_without_retry() -> None:
    provider, models, sleeps = make([api_error(429, "RESOURCE_EXHAUSTED", DAILY_QUOTA)])
    with pytest.raises(LLMQuotaError, match="daily quota"):
        call(provider)
    assert len(models.calls) == 1 and sleeps == []


def test_rate_limit_that_outlasts_retries_is_a_quota_error() -> None:
    provider, models, _ = make([api_error(429)] * 4, max_attempts=4)
    with pytest.raises(LLMQuotaError, match="rate limited"):
        call(provider)
    assert len(models.calls) == 4


def test_server_errors_that_outlast_retries_are_unavailable() -> None:
    provider, _, _ = make([api_error(503)] * 2, max_attempts=2)
    with pytest.raises(LLMUnavailableError):
        call(provider)


def test_timeout_is_typed() -> None:
    provider, models, _ = make([httpx.ReadTimeout("slow")] * 2, max_attempts=2)
    with pytest.raises(LLMTimeoutError):
        call(provider)
    assert len(models.calls) == 2


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_client_errors_are_not_retried(code: int) -> None:
    provider, models, sleeps = make([api_error(code)])
    with pytest.raises(LLMRequestError):
        call(provider)
    assert len(models.calls) == 1 and sleeps == []


def test_truncated_output_is_an_output_error_with_raw_text() -> None:
    provider, models, _ = make([response('{"name": "x", "cou', finish="MAX_TOKENS")])
    with pytest.raises(LLMOutputError, match="truncated") as info:
        call(provider)
    assert info.value.raw_text == '{"name": "x", "cou' and info.value.finish_reason == "MAX_TOKENS"
    assert len(models.calls) == 1  # never retried: the same prompt would truncate again


@pytest.mark.parametrize(
    ("text", "finish", "match"),
    [
        ("not json", "STOP", "does not match the schema"),
        ('{"name": "x"}', "STOP", "count"),
        ("", "STOP", "empty"),
        ('{"name": "x", "count": 1}', "SAFETY", "SAFETY"),
    ],
)
def test_unusable_output_is_rejected(text: str, finish: str, match: str) -> None:
    provider, _, _ = make([response(text, finish)])
    with pytest.raises(LLMOutputError, match=match):
        call(provider)


def test_no_candidates_is_an_output_error() -> None:
    blocked = SimpleNamespace(
        candidates=[], usage_metadata=None, prompt_feedback=SimpleNamespace(block_reason="SAFETY")
    )
    provider, _, _ = make([blocked])
    with pytest.raises(LLMOutputError, match="blocked"):
        call(provider)


def test_small_files_are_sent_inline(tmp_path: Path) -> None:
    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.7 small")
    provider, models, _ = make([response('{"name": "x", "count": 1}')])
    call(provider, files=[pdf])
    contents = models.calls[0]["contents"]
    assert contents[-1] == "p" and contents[0].inline_data.mime_type == "application/pdf"


def test_large_files_are_uploaded_and_deleted_afterwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autohub.assistant.llm as llm

    monkeypatch.setattr(llm, "INLINE_FILE_LIMIT_BYTES", 4)
    pdf = tmp_path / "big.pdf"
    pdf.write_bytes(b"%PDF-1.7 large")
    provider, _models, _ = make([api_error(400)])
    files = provider._client.files
    with pytest.raises(LLMRequestError):
        call(provider, files=[pdf])
    assert files.uploaded == [str(pdf)] and files.deleted == ["files/abc"]  # deleted even on failure


def test_stream_yields_chunks_and_retries_before_first_chunk() -> None:
    chunks = [SimpleNamespace(text="Hel"), SimpleNamespace(text=""), SimpleNamespace(text="lo")]
    provider, models, sleeps = make([api_error(503), chunks])
    assert "".join(provider.stream_text(system="s", prompt="p")) == "Hello"
    assert len(models.calls) == 2 and len(sleeps) == 1


def test_fake_provider_round_trip() -> None:
    fake = FakeLLMProvider([Answer(name="a", count=1), {"name": "b", "count": 2}, LLMQuotaError("q")])
    assert fake.generate_structured(system="s", prompt="p", schema=Answer).value.name == "a"
    assert fake.generate_structured(system="s", prompt="p", schema=Answer).value.count == 2
    with pytest.raises(LLMQuotaError):
        fake.generate_structured(system="s", prompt="p", schema=Answer)
    assert len(fake.calls) == 3


def test_parse_structured_accepts_unspecified_finish() -> None:
    assert parse_structured(Answer, '{"name":"x","count":3}', None).count == 3
