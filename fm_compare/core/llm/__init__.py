"""
LLM integration layer.

All external LLM access in this application MUST go through the corporate
`gateway` (OpenAI-compatible API). Direct calls to api.openai.com,
api.anthropic.com, openrouter.ai or any external provider are NOT allowed.

See CLAUDE_CODE_GATEWAY_INSTRUCTION.md for the contract this layer implements.

Public surface:
    GatewayConfig        — connection settings, loaded from .env / environment
    GatewayClient        — performs chat completions through the gateway
    ChatMessage          — single role/content message
    ChatResult           — assistant reply + usage metadata
    GatewayError + subclasses — typed errors mapped from gateway error codes
"""
from fm_compare.core.llm.config import GatewayConfig
from fm_compare.core.llm.client import GatewayClient, ChatMessage, ChatResult
from fm_compare.core.llm.errors import (
    GatewayError,
    AuthenticationError,
    AuthorizationError,
    ModelAccessDenied,
    PromptInjectionBlocked,
    ValidationError,
    QuotaExceeded,
    UpstreamError,
    ConfigError,
)

__all__ = [
    "GatewayConfig",
    "GatewayClient",
    "ChatMessage",
    "ChatResult",
    "GatewayError",
    "AuthenticationError",
    "AuthorizationError",
    "ModelAccessDenied",
    "PromptInjectionBlocked",
    "ValidationError",
    "QuotaExceeded",
    "UpstreamError",
    "ConfigError",
]
