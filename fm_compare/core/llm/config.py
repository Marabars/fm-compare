"""
Gateway connection configuration.

Loaded from environment variables (and an optional local .env file).
Secrets live only in the local .env / environment — never in the repo,
never in settings.json, never in logs.

Naming follows CLAUDE_CODE_GATEWAY_INSTRUCTION.md:
    GATEWAY_BASE_URL
    KEYCLOAK_TOKEN_URL
    KEYCLOAK_CLIENT_ID
    KEYCLOAK_USERNAME
    KEYCLOAK_PASSWORD
    LLM_MODEL              — default model id to use (must exist in /v1/models)

The user-supplied token field for the OpenAI SDK shape is called
ACCESS_TOKEN / GATEWAY_ACCESS_TOKEN, never OPENAI_API_KEY.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fm_compare.core.llm.errors import ConfigError

# Defaults from the instruction. Overridable via environment.
_DEFAULT_BASE_URL = "http://BA-SRV-AI-APP01.mr-group.ru:8080"
_DEFAULT_TOKEN_URL = (
    "https://key.mr-group.ru/realms/AI-Gateway/protocol/openid-connect/token"
)
_DEFAULT_CLIENT_ID = "ai-gateway"
_DEFAULT_MODEL = "openai/gpt-5.5"

# Default request timeout (seconds). Generous — LLM calls can be slow.
DEFAULT_TIMEOUT_S = 120.0


def _load_dotenv(path: Path) -> dict[str, str]:
    """
    Minimal .env parser (no external dependency).

    Supports `KEY=value` lines, `#` comments, blank lines, optional surrounding
    quotes. Does NOT override variables already present in os.environ.
    """
    values: dict[str, str] = {}
    try:
        if not path.exists():
            return values
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    except Exception:
        # A malformed .env must not crash the app — treat as absent.
        return {}
    return values


def _find_dotenv() -> Path | None:
    """Look for a .env (or .gateway.env) walking up from CWD."""
    names = (".gateway.env", ".env")
    for base in (Path.cwd(), *Path.cwd().parents):
        for name in names:
            candidate = base / name
            if candidate.exists():
                return candidate
    return None


@dataclass(frozen=True)
class GatewayConfig:
    """Immutable connection settings for the gateway."""

    base_url: str
    token_url: str
    client_id: str
    username: str
    password: str
    model: str
    timeout_s: float = DEFAULT_TIMEOUT_S

    # --- derived endpoints ---

    @property
    def api_base(self) -> str:
        """OpenAI-style base URL, ends with /v1 (for SDK-style clients)."""
        return f"{self.base_url.rstrip('/')}/v1"

    @property
    def chat_completions_url(self) -> str:
        return f"{self.api_base}/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.api_base}/models"

    @property
    def health_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/healthz"

    # --- construction ---

    @classmethod
    def from_env(
        cls,
        *,
        load_dotenv: bool = True,
        dotenv_path: str | os.PathLike | None = None,
    ) -> "GatewayConfig":
        """
        Build config from environment, optionally merging a local .env file.

        Real os.environ values take precedence over .env values.
        Raises ConfigError if required credentials are missing.
        """
        dotenv_values: dict[str, str] = {}
        if load_dotenv:
            path = Path(dotenv_path) if dotenv_path else _find_dotenv()
            if path is not None:
                dotenv_values = _load_dotenv(path)

        def _get(key: str, default: str | None = None) -> str | None:
            return os.environ.get(key, dotenv_values.get(key, default))

        base_url = _get("GATEWAY_BASE_URL", _DEFAULT_BASE_URL) or _DEFAULT_BASE_URL
        token_url = _get("KEYCLOAK_TOKEN_URL", _DEFAULT_TOKEN_URL) or _DEFAULT_TOKEN_URL
        client_id = _get("KEYCLOAK_CLIENT_ID", _DEFAULT_CLIENT_ID) or _DEFAULT_CLIENT_ID
        username = _get("KEYCLOAK_USERNAME") or ""
        password = _get("KEYCLOAK_PASSWORD") or ""
        model = _get("LLM_MODEL", _DEFAULT_MODEL) or _DEFAULT_MODEL

        timeout_raw = _get("LLM_TIMEOUT_S")
        try:
            timeout_s = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_S
        except ValueError:
            timeout_s = DEFAULT_TIMEOUT_S

        cfg = cls(
            base_url=base_url,
            token_url=token_url,
            client_id=client_id,
            username=username,
            password=password,
            model=model,
            timeout_s=timeout_s,
        )
        return cfg

    def validate(self) -> None:
        """Raise ConfigError if anything required for a real call is missing."""
        if not self.base_url:
            raise ConfigError("GATEWAY_BASE_URL is not configured")
        if not self.token_url:
            raise ConfigError("KEYCLOAK_TOKEN_URL is not configured")
        if not self.client_id:
            raise ConfigError("KEYCLOAK_CLIENT_ID is not configured")
        if not self.username or not self.password:
            raise ConfigError(
                "KEYCLOAK_USERNAME / KEYCLOAK_PASSWORD are not configured. "
                "Set them in your local .env (never commit it)."
            )
        if not self.model:
            raise ConfigError("LLM_MODEL is not configured")

    @property
    def is_configured(self) -> bool:
        """True when a real gateway call could be attempted (no network check)."""
        try:
            self.validate()
            return True
        except ConfigError:
            return False
