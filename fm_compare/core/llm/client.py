"""
Gateway client — OpenAI-compatible chat completions through the corporate gateway.

This is the ONLY place in the application allowed to perform external LLM calls,
and it always targets ${GATEWAY_BASE_URL}/v1 — never an external provider URL.

Implements the contract from CLAUDE_CODE_GATEWAY_INSTRUCTION.md:
  - fresh Keycloak access token before each request (Authorization: Bearer ...)
  - X-Security-Prompt-Injection: false on every chat request (never true)
  - typed errors mapped from error.code / HTTP status
  - never logs bearer token, _security, or X-AISG-Masking-Map
  - optional OpenAI-style SSE streaming (Accept: text/event-stream)

Built on urllib (standard library) to keep the app dependency-free, matching
the rest of the codebase.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from fm_compare.core.llm.config import GatewayConfig
from fm_compare.core.llm.errors import (
    AuthenticationError,
    GatewayError,
    error_from_response,
)
from fm_compare.core.llm.token_provider import TokenProvider
from fm_compare.security import safe_logger as log


@dataclass(frozen=True)
class ChatMessage:
    """A single chat message. Put user/business data in role='user' only —
    the gateway does NOT mask role='system'."""

    role: str  # "system" | "user" | "assistant"
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ChatResult:
    """Result of a non-streaming chat completion."""

    content: str
    model: str
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None


class GatewayClient:
    """Performs chat completions and model listing via the gateway."""

    def __init__(
        self,
        config: GatewayConfig,
        token_provider: TokenProvider | None = None,
    ) -> None:
        self._config = config
        self._tokens = token_provider or TokenProvider(config)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        extra_body: dict | None = None,
    ) -> ChatResult:
        """
        Non-streaming chat completion.

        `tools` is passed through to the gateway top-level unchanged
        (e.g. openrouter:web_search). Do NOT put personal data in tools.
        """
        body = self._build_body(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            extra_body=extra_body,
            stream=False,
        )
        status, payload, headers = self._post_json(
            self._config.chat_completions_url, body
        )
        if status >= 400:
            raise error_from_response(status, payload)
        return self._parse_chat_result(payload, model or self._config.model)

    def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        extra_body: dict | None = None,
    ) -> Iterator[str]:
        """
        Streaming chat completion. Yields text fragments as they arrive.

        Reads OpenAI-style SSE: `data: {...}` lines carry fragments in
        choices[0].delta.content (legacy: choices[0].text); `data: [DONE]`
        ends the stream. No `_security` metadata is available while streaming.
        """
        body = self._build_body(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            extra_body=extra_body,
            stream=True,
        )
        yield from self._post_stream(self._config.chat_completions_url, body)

    def list_models(self) -> list[dict]:
        """GET /v1/models — role-aware list of models visible to this user."""
        status, payload, _ = self._get_json(self._config.models_url)
        if status >= 400:
            raise error_from_response(status, payload)
        return list((payload or {}).get("data", []))

    def health_check(self) -> bool:
        """GET /healthz — True if the gateway is reachable and healthy."""
        try:
            status, _, _ = self._get_json(self._config.health_url, authed=False)
            return 200 <= status < 300
        except GatewayError:
            return False

    # ------------------------------------------------------------------ #
    # Request building
    # ------------------------------------------------------------------ #

    def _build_body(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        tools: list[dict] | None,
        extra_body: dict | None,
        stream: bool,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model or self._config.model,
            "messages": [m.to_dict() for m in messages],
            "stream": stream,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            # Passed through to upstream unchanged by the gateway.
            body["tools"] = tools
        if extra_body:
            body.update(extra_body)
        return body

    def _security_headers(self) -> dict[str, str]:
        # Services calling the gateway must NOT enable prompt-injection check.
        return {"X-Security-Prompt-Injection": "false"}

    def _auth_header(self, *, force_refresh: bool = False) -> dict[str, str]:
        token = self._tokens.get_token(force_refresh=force_refresh)
        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------ #
    # HTTP — JSON (non-streaming)
    # ------------------------------------------------------------------ #

    def _post_json(
        self, url: str, body: dict
    ) -> tuple[int, dict | None, dict[str, str]]:
        """POST JSON with one automatic retry on 401 using a fresh token."""
        data = json.dumps(body).encode("utf-8")

        def _send(force_refresh: bool) -> tuple[int, dict | None, dict[str, str]]:
            headers = {
                "Content-Type": "application/json",
                **self._auth_header(force_refresh=force_refresh),
                **self._security_headers(),
            }
            return self._raw_request(url, data=data, headers=headers, method="POST")

        status, payload, resp_headers = _send(force_refresh=False)
        if status == 401:
            log.warning("Gateway returned 401; refreshing token and retrying once")
            self._tokens.invalidate()
            status, payload, resp_headers = _send(force_refresh=True)
        return status, payload, resp_headers

    def _get_json(
        self, url: str, *, authed: bool = True
    ) -> tuple[int, dict | None, dict[str, str]]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if authed:
            headers.update(self._auth_header())
        return self._raw_request(url, data=None, headers=headers, method="GET")

    def _raw_request(
        self,
        url: str,
        *,
        data: bytes | None,
        headers: dict[str, str],
        method: str,
    ) -> tuple[int, dict | None, dict[str, str]]:
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                status = resp.getcode()
                resp_headers = dict(resp.headers.items())
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                raw = e.read().decode("utf-8")
            except Exception:
                raw = ""
            resp_headers = dict(getattr(e, "headers", {}) or {})
        except urllib.error.URLError as e:
            raise GatewayError(f"Cannot reach gateway: {e.reason}") from None

        payload: dict | None
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = None
        return status, payload, resp_headers

    # ------------------------------------------------------------------ #
    # HTTP — streaming (SSE)
    # ------------------------------------------------------------------ #

    def _post_stream(self, url: str, body: dict) -> Iterator[str]:
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **self._auth_header(),
            **self._security_headers(),
        }
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(request, timeout=self._config.timeout_s)
        except urllib.error.HTTPError as e:
            try:
                raw = e.read().decode("utf-8")
                payload = json.loads(raw) if raw else None
            except Exception:
                payload = None
            raise error_from_response(e.code, payload) from None
        except urllib.error.URLError as e:
            raise GatewayError(f"Cannot reach gateway: {e.reason}") from None

        with resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if chunk == "[DONE]":
                    break
                fragment = self._extract_sse_fragment(chunk)
                if fragment:
                    yield fragment

    @staticmethod
    def _extract_sse_fragment(chunk: str) -> str:
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            return ""
        choices = obj.get("choices") or []
        if not choices:
            return ""
        choice = choices[0]
        delta = choice.get("delta") or {}
        # Standard chat streaming, then legacy completions fallback.
        return delta.get("content") or choice.get("text") or ""

    # ------------------------------------------------------------------ #
    # Response parsing
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_chat_result(payload: dict | None, model: str) -> ChatResult:
        payload = payload or {}
        choices = payload.get("choices") or []
        content = ""
        finish_reason = None
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content") or choices[0].get("text") or ""
            finish_reason = choices[0].get("finish_reason")
        # correlation_id may surface in _security; safe to keep for logs.
        security = payload.get("_security") or {}
        correlation_id = security.get("correlation_id")
        return ChatResult(
            content=content,
            model=payload.get("model") or model,
            finish_reason=finish_reason,
            usage=payload.get("usage") or {},
            correlation_id=correlation_id,
        )
