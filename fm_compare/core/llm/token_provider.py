"""
Keycloak access-token provider.

Obtains a fresh OIDC access token (realm AI-Gateway) via the password grant,
using KEYCLOAK_USERNAME / KEYCLOAK_PASSWORD. Refresh tokens are intentionally
NOT used or stored, per the gateway instruction.

The token is the credential for the *gateway* only — it is never forwarded to
the upstream LLM provider. It must never be written to application logs.

A short in-memory cache avoids re-fetching on every call within a single
comparison run, while still honouring the token's own expiry.

Static-token bypass (workaround while Keycloak is unreachable):
If GATEWAY_ACCESS_TOKEN is set in the environment / .env, it is used as-is
without contacting Keycloak. The token is assumed valid (no expiry check).
Useful when the VM cannot reach key.mr-group.ru (Servicepipe IP block) but
someone on the corporate network can obtain a token manually:

    curl -s -X POST https://key.mr-group.ru/realms/AI-Gateway/protocol/openid-connect/token \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -H "Origin: https://key.mr-group.ru" \
      -H "X-Requested-With: XMLHttpRequest" \
      -H "User-Agent: Mozilla/5.0 ..." \
      -d "client_id=ai-gateway&grant_type=password&username=USER&password=PASS"

Copy the access_token value to GATEWAY_ACCESS_TOKEN= in .env.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from fm_compare.core.llm.config import GatewayConfig
from fm_compare.core.llm.errors import AuthenticationError, GatewayError

# Re-fetch this many seconds before the token actually expires, to avoid
# using a token that expires mid-flight.
_EXPIRY_SKEW_S = 30
# Fallback lifetime if the token endpoint does not return expires_in.
_FALLBACK_TTL_S = 60


class TokenProvider:
    """Fetches and caches a Keycloak access token for the gateway."""

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        self._token: str | None = None
        self._expires_at: float = 0.0

    def get_token(self, *, force_refresh: bool = False) -> str:
        """
        Return a valid access token, fetching a new one if needed.

        Raises ConfigError (via validate) if credentials are missing,
        AuthenticationError if Keycloak rejects the credentials.
        """
        static = os.environ.get("GATEWAY_ACCESS_TOKEN", "").strip()
        if static:
            return static

        self._config.validate()
        now = time.monotonic()
        if not force_refresh and self._token and now < self._expires_at:
            return self._token
        return self._fetch()

    def invalidate(self) -> None:
        """Drop the cached token (e.g. after a 401 from the gateway)."""
        self._token = None
        self._expires_at = 0.0

    def _fetch(self) -> str:
        cfg = self._config
        data = urllib.parse.urlencode({
            "client_id": cfg.client_id,
            "username": cfg.username,
            "password": cfg.password,
            "grant_type": "password",
        }).encode("utf-8")

        request = urllib.request.Request(
            cfg.token_url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            with urllib.request.urlopen(request, timeout=cfg.timeout_s) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            # Do NOT include the request body (it contains the password).
            raise AuthenticationError(
                f"Keycloak token request failed (HTTP {e.code})",
                http_status=e.code,
            ) from None
        except urllib.error.URLError as e:
            raise GatewayError(
                f"Cannot reach Keycloak token endpoint: {e.reason}"
            ) from None

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise AuthenticationError(
                "Keycloak returned a non-JSON token response"
            ) from None

        token = payload.get("access_token")
        if not token:
            raise AuthenticationError("Keycloak response has no access_token")

        expires_in = payload.get("expires_in")
        ttl = int(expires_in) if isinstance(expires_in, (int, float)) else _FALLBACK_TTL_S
        self._token = token
        self._expires_at = time.monotonic() + max(0, ttl - _EXPIRY_SKEW_S)
        return token
