"""
Typed gateway errors.

The gateway returns errors in a single shape:

    {"error": {"message": "...", "type": "...", "code": "...",
               "details": {"correlation_id": "..."}}}

We map `error.code` (and HTTP status as a fallback) to specific exception
classes so callers can react by meaning instead of parsing strings.

`correlation_id` is safe to log and surface for investigation; the bearer
token, KEYCLOAK_PASSWORD, `_security.mappings` and `X-AISG-Masking-Map`
are NOT — never put them in an exception message.
"""
from __future__ import annotations


class GatewayError(Exception):
    """Base class for all gateway-related failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.correlation_id = correlation_id

    def __str__(self) -> str:
        parts = [self.message]
        if self.code:
            parts.append(f"code={self.code}")
        if self.http_status is not None:
            parts.append(f"http={self.http_status}")
        if self.correlation_id:
            parts.append(f"correlation_id={self.correlation_id}")
        return " ".join(parts)


class ConfigError(GatewayError):
    """Local misconfiguration — missing base URL, credentials, etc.
    Raised before any network call."""


class AuthenticationError(GatewayError):
    """401 — no bearer token or token invalid."""


class AuthorizationError(GatewayError):
    """403 authorization_error — token valid but role/group missing."""


class ModelAccessDenied(GatewayError):
    """403 model_access_denied — model not available to current role."""


class PromptInjectionBlocked(GatewayError):
    """403 prompt_injection_blocked — request blocked by classifier."""


class ValidationError(GatewayError):
    """422 validation_error — bad payload or header."""


class QuotaExceeded(GatewayError):
    """429 quota_exceeded — role quota exceeded."""


class UpstreamError(GatewayError):
    """502 upstream_error — external LLM provider failed."""


# Map gateway error.code -> exception class. Preferred over HTTP status,
# because two distinct 403 codes carry different meaning.
_CODE_MAP: dict[str, type[GatewayError]] = {
    "authentication_error": AuthenticationError,
    "authorization_error": AuthorizationError,
    "model_access_denied": ModelAccessDenied,
    "prompt_injection_blocked": PromptInjectionBlocked,
    "validation_error": ValidationError,
    "quota_exceeded": QuotaExceeded,
    "upstream_error": UpstreamError,
}

# Fallback when error.code is absent — map by HTTP status only.
_STATUS_MAP: dict[int, type[GatewayError]] = {
    401: AuthenticationError,
    403: AuthorizationError,
    422: ValidationError,
    429: QuotaExceeded,
    502: UpstreamError,
}


def error_from_response(
    http_status: int,
    payload: dict | None,
) -> GatewayError:
    """
    Build a typed error from a gateway error response.

    `payload` is the parsed JSON body (or None when the body was not JSON).
    Never include the request body or any secret in the resulting message.
    """
    err = (payload or {}).get("error") or {}
    message = err.get("message") or f"Gateway request failed (HTTP {http_status})"
    code = err.get("code")
    correlation_id = (err.get("details") or {}).get("correlation_id")

    exc_cls: type[GatewayError]
    if code and code in _CODE_MAP:
        exc_cls = _CODE_MAP[code]
    else:
        exc_cls = _STATUS_MAP.get(http_status, GatewayError)

    return exc_cls(
        message,
        code=code,
        http_status=http_status,
        correlation_id=correlation_id,
    )
