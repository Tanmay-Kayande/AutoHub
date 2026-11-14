"""LLM provider seam: protocol, Gemini implementation and a fake for tests.

Callers depend on :class:`LLMProvider` only. :class:`GeminiProvider` owns every
provider-specific concern:

- one request timeout, and the SDK's own retry switched off so retries happen
  in exactly one place;
- retry with exponential backoff for rate limits (429), 5xx, timeouts and
  connection errors, honouring the server's ``RetryInfo`` delay (capped);
- a **daily** quota error is raised as :class:`LLMQuotaError` at once — retrying
  it would only burn time until the next day;
- output that was cut short (``MAX_TOKENS``), blocked, empty, not JSON, or not
  valid against the schema raises :class:`LLMOutputError` with the raw text, so
  the caller can record it.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from autohub.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_MAX_OUTPUT_TOKENS = 8192
MAX_RETRY_DELAY_SECONDS = 60.0
INLINE_FILE_LIMIT_BYTES = 18 * 1024 * 1024  # request limit is 20 MB including the prompt
FILE_ACTIVE_TIMEOUT_SECONDS = 120.0
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")


# ---------------------------------------------------------------- errors
class LLMError(Exception):
    """Base class. ``str(exc)`` is safe to log and store (no secrets)."""

    error_type = "llm_error"


class LLMQuotaError(LLMError):
    """Daily quota exhausted, or still rate-limited after every retry."""

    error_type = "quota"


class LLMTimeoutError(LLMError):
    error_type = "timeout"


class LLMUnavailableError(LLMError):
    """5xx or connection failures that outlasted the retries."""

    error_type = "unavailable"


class LLMRequestError(LLMError):
    """The request itself was refused (bad request, auth, model not found)."""

    error_type = "request"


class LLMOutputError(LLMError):
    """The model answered, but the answer is not usable."""

    error_type = "invalid_output"

    def __init__(self, message: str, *, raw_text: str = "", finish_reason: str | None = None) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.finish_reason = finish_reason


# ---------------------------------------------------------------- results
@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0  # includes thinking tokens, which are billed as output
    total_tokens: int = 0


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    value: T
    raw_text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    latency_ms: int = 0
    attempts: int = 1


class LLMProvider(Protocol):
    name: str
    model: str

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        files: Sequence[Path] = (),
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> LLMResult[T]: ...

    def stream_text(
        self, *, system: str, prompt: str, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    ) -> Iterator[str]: ...


def parse_structured[T: BaseModel](schema: type[T], raw_text: str, finish_reason: str | None) -> T:
    """Validate model text against ``schema``; raise :class:`LLMOutputError` otherwise."""
    if finish_reason == "MAX_TOKENS":
        raise LLMOutputError(
            "output was truncated (MAX_TOKENS)", raw_text=raw_text, finish_reason=finish_reason
        )
    if finish_reason not in (None, "STOP", "FINISH_REASON_UNSPECIFIED"):
        raise LLMOutputError(
            f"generation stopped early ({finish_reason})", raw_text=raw_text, finish_reason=finish_reason
        )
    if not raw_text.strip():
        raise LLMOutputError("empty response", raw_text=raw_text, finish_reason=finish_reason)
    try:
        return schema.model_validate_json(raw_text)
    except ValidationError as exc:
        summary = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()[:5]
        )
        raise LLMOutputError(
            f"output does not match the schema ({exc.error_count()} errors: {summary})",
            raw_text=raw_text,
            finish_reason=finish_reason,
        ) from exc


# ---------------------------------------------------------------- Gemini
def _quota_details(details: Any) -> tuple[bool, float | None]:
    """From a 429 error body: (is a daily quota, server-suggested retry delay in seconds)."""
    daily = False
    delay: float | None = None
    body = details.get("error", details) if isinstance(details, dict) else {}
    for item in body.get("details", []) if isinstance(body, dict) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            for violation in item.get("violations", []):
                quota_id = str(violation.get("quotaId", "")) if isinstance(violation, dict) else ""
                if "PerDay" in quota_id:
                    daily = True
        elif kind.endswith("RetryInfo"):
            match = _DURATION.match(str(item.get("retryDelay", "")))
            if match:
                delay = float(match.group(1))
    return daily, delay


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str,
        timeout_seconds: float = 180.0,
        max_attempts: int = 4,
        thinking_budget: int | None = 0,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``thinking_budget=0`` turns thinking off (cheapest); ``None`` leaves the model default."""
        self.model = model
        self._max_attempts = max_attempts
        self._thinking_budget = thinking_budget
        self._sleep = sleep
        self._clock = clock
        if client is None:
            from google import genai
            from google.genai import types

            if not api_key:
                raise LLMRequestError("Gemini API key is not configured")
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=int(timeout_seconds * 1000), retry_options=types.HttpRetryOptions(attempts=1)
                ),
            )
        self._client = client

    # ------------------------------------------------------------------ public
    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        files: Sequence[Path] = (),
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> LLMResult[T]:
        from google.genai import types

        uploaded: list[Any] = []
        try:
            parts: list[Any] = []
            for path in files:
                part, remote = self._file_part(path)
                parts.append(part)
                if remote is not None:
                    uploaded.append(remote)
            parts.append(prompt)
            config = types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.0,
                max_output_tokens=max_output_tokens,
                response_mime_type="application/json",
                response_json_schema=schema.model_json_schema(),
                thinking_config=self._thinking(),
            )
            started = self._clock()
            response, attempts = self.call_with_retry(
                lambda: self._client.models.generate_content(model=self.model, contents=parts, config=config)
            )
            latency_ms = int((self._clock() - started) * 1000)
        finally:
            for remote in uploaded:
                self._delete_file(remote)

        raw_text, finish_reason = self._text_and_finish(response)
        value = parse_structured(schema, raw_text, finish_reason)
        usage = self._usage(response)
        logger.info(
            "llm_call model=%s attempts=%s latency_ms=%s input_tokens=%s output_tokens=%s",
            self.model,
            attempts,
            latency_ms,
            usage.input_tokens,
            usage.output_tokens,
        )
        return LLMResult(
            value=value,
            raw_text=raw_text,
            model=self.model,
            usage=usage,
            finish_reason=finish_reason,
            latency_ms=latency_ms,
            attempts=attempts,
        )

    def stream_text(
        self, *, system: str, prompt: str, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    ) -> Iterator[str]:
        """Yield text chunks. Retries apply only until the first chunk arrives."""
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.3,
            max_output_tokens=max_output_tokens,
            thinking_config=self._thinking(),
        )

        def open_stream() -> tuple[Any, Any]:
            stream = iter(
                self._client.models.generate_content_stream(
                    model=self.model, contents=[prompt], config=config
                )
            )
            return stream, next(stream, None)

        (stream, first), _ = self.call_with_retry(open_stream)
        chunk = first
        while chunk is not None:
            text = getattr(chunk, "text", None)
            if text:
                yield str(text)
            try:
                chunk = next(stream, None)
            except Exception as exc:
                raise self._translate(exc) from exc

    # ------------------------------------------------------------------ internals
    def _thinking(self) -> Any:
        from google.genai import types

        # Only Gemini 2.5 accepts a thinking budget; 3.x models reject it (HTTP 400).
        if self._thinking_budget is None or not self.model.startswith("gemini-2.5"):
            return None
        return types.ThinkingConfig(thinking_budget=self._thinking_budget)

    def _file_part(self, path: Path) -> tuple[Any, Any]:
        from google.genai import types

        size = path.stat().st_size
        if size <= INLINE_FILE_LIMIT_BYTES:
            return types.Part.from_bytes(data=path.read_bytes(), mime_type="application/pdf"), None
        remote, _ = self.call_with_retry(
            lambda: self._client.files.upload(file=str(path), config={"mime_type": "application/pdf"})
        )
        deadline = self._clock() + FILE_ACTIVE_TIMEOUT_SECONDS
        while str(getattr(remote.state, "name", remote.state)) == "PROCESSING":
            if self._clock() > deadline:
                self._delete_file(remote)
                raise LLMTimeoutError(
                    f"uploaded file {path.name} still processing after {FILE_ACTIVE_TIMEOUT_SECONDS:.0f} s"
                )
            self._sleep(2.0)
            remote = self._client.files.get(name=remote.name)
        if str(getattr(remote.state, "name", remote.state)) != "ACTIVE":
            self._delete_file(remote)
            raise LLMRequestError(f"uploaded file {path.name} was not accepted (state {remote.state})")
        return types.Part.from_uri(file_uri=remote.uri, mime_type="application/pdf"), remote

    def _delete_file(self, remote: Any) -> None:
        try:
            self._client.files.delete(name=remote.name)
        except Exception as exc:  # uploads expire after 48 h anyway
            logger.warning("llm_file_delete_failed name=%s error=%s", getattr(remote, "name", "?"), exc)

    def call_with_retry(self, operation: Callable[[], Any]) -> tuple[Any, int]:
        """Run ``operation`` with retry. Returns (result, attempts used)."""
        last: LLMError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return operation(), attempt
            except Exception as exc:
                error = self._translate(exc)
                retry, delay = self._retry_plan(exc, error, attempt)
                if not retry:
                    raise error from exc
                last = error
                if attempt < self._max_attempts:
                    logger.warning(
                        "llm_retry model=%s attempt=%s delay=%.1f reason=%s",
                        self.model,
                        attempt,
                        delay,
                        error,
                    )
                    self._sleep(delay)
        assert last is not None
        raise last

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(MAX_RETRY_DELAY_SECONDS, float(2**attempt) + random.uniform(0, 1))

    def _retry_plan(self, exc: Exception, error: LLMError, attempt: int) -> tuple[bool, float]:
        if isinstance(error, (LLMRequestError, LLMOutputError)):
            return False, 0.0
        if isinstance(error, LLMQuotaError):
            daily, server_delay = _quota_details(getattr(exc, "details", None))
            if daily:
                return False, 0.0
            delay = max(self._backoff(attempt), server_delay or 0.0)
            return True, min(delay, MAX_RETRY_DELAY_SECONDS)
        return True, self._backoff(attempt)

    def _translate(self, exc: Exception) -> LLMError:
        if isinstance(exc, LLMError):
            return exc
        from google.genai import errors

        if isinstance(exc, errors.APIError):
            code = int(exc.code or 0)
            text = f"Gemini HTTP {code} {exc.status or ''}: {exc.message or ''}".strip()
            if code == 429:
                daily, _ = _quota_details(exc.details)
                return LLMQuotaError(("daily quota exhausted - " if daily else "rate limited - ") + text)
            if code in _RETRYABLE_STATUS or code >= 500:
                return LLMUnavailableError(text)
            return LLMRequestError(text)
        if isinstance(exc, httpx.TimeoutException):
            return LLMTimeoutError(f"Gemini request timed out ({type(exc).__name__})")
        if isinstance(exc, httpx.TransportError):
            return LLMUnavailableError(f"Gemini connection failed ({type(exc).__name__}: {exc})")
        return LLMError(f"unexpected Gemini client error ({type(exc).__name__}: {exc})")

    @staticmethod
    def _text_and_finish(response: Any) -> tuple[str, str | None]:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            feedback = getattr(response, "prompt_feedback", None)
            reason = getattr(feedback, "block_reason", None)
            raise LLMOutputError(
                f"no candidates returned (prompt blocked: {reason})" if reason else "no candidates"
            )
        candidate = candidates[0]
        finish = getattr(candidate, "finish_reason", None)
        finish_reason = None if finish is None else str(getattr(finish, "value", finish))
        content = getattr(candidate, "content", None)
        texts = [
            str(part.text)
            for part in (getattr(content, "parts", None) or [])
            if getattr(part, "text", None) and not getattr(part, "thought", False)
        ]
        return "".join(texts), finish_reason

    @staticmethod
    def _usage(response: Any) -> Usage:
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return Usage()
        prompt = int(getattr(meta, "prompt_token_count", 0) or 0)
        output = int(getattr(meta, "candidates_token_count", 0) or 0) + int(
            getattr(meta, "thoughts_token_count", 0) or 0
        )
        total = int(getattr(meta, "total_token_count", 0) or 0) or prompt + output
        return Usage(input_tokens=prompt, output_tokens=output, total_tokens=total)


# ---------------------------------------------------------------- fake
@dataclass
class FakeCall:
    system: str
    prompt: str
    schema: type[BaseModel] | None
    files: tuple[Path, ...]


class FakeLLMProvider:
    """Returns queued answers in order. An answer may be a model instance, a dict,
    raw JSON text, or an exception to raise. ``stream`` answers are strings."""

    name = "fake"

    def __init__(self, answers: Sequence[Any] = (), *, model: str = "fake-model") -> None:
        self.model = model
        self.answers: list[Any] = list(answers)
        self.calls: list[FakeCall] = []

    def _next(self) -> Any:
        if not self.answers:
            raise AssertionError("FakeLLMProvider has no queued answer left")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        files: Sequence[Path] = (),
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> LLMResult[T]:
        self.calls.append(FakeCall(system, prompt, schema, tuple(files)))
        answer = self._next()
        if isinstance(answer, BaseModel):
            raw = answer.model_dump_json()
        elif isinstance(answer, str):
            raw = answer
        else:
            import json

            raw = json.dumps(answer)
        value = parse_structured(schema, raw, "STOP")
        return LLMResult(
            value=value, raw_text=raw, model=self.model, usage=Usage(10, 20, 30), finish_reason="STOP"
        )

    def stream_text(
        self, *, system: str, prompt: str, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    ) -> Iterator[str]:
        self.calls.append(FakeCall(system, prompt, None, ()))
        answer = self._next()
        yield from (answer if isinstance(answer, list) else [str(answer)])


def get_llm_provider(settings: Settings | None = None, *, model: str | None = None) -> LLMProvider:
    """The provider selected by ``LLM_PROVIDER``. Demands the API key only here."""
    settings = settings or get_settings()
    if settings.llm_provider == "gemini":
        return GeminiProvider(
            api_key=settings.require_gemini_key(),
            model=model or settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_attempts=settings.llm_max_attempts,
        )
    if settings.llm_provider == "fake":
        return FakeLLMProvider()
    raise LLMRequestError(f"unknown LLM_PROVIDER {settings.llm_provider!r}")
