"""
LLM enrichment of the rule-based executive summary.

Takes the summary blocks produced by summary_generator.generate_summary and
asks the gateway LLM to write a short executive overview on top. This is
strictly additive and best-effort:

  - If the gateway is not configured or unreachable, the original blocks are
    returned unchanged (graceful fallback — the app never depends on the LLM).
  - On any GatewayError the original blocks are returned and a warning is logged
    (without the prompt/response content).

Security (per CLAUDE_CODE_GATEWAY_INSTRUCTION.md):
  - Business text goes in role="user" only; role="system" carries no financial
    data (gateway does not mask system messages).
  - The client already sends X-Security-Prompt-Injection: false.
  - We never log the prompt, the response, or any gateway secret — only the
    fact of the call, the model, correlation_id and usage.
"""
from __future__ import annotations

from fm_compare.core.llm.config import GatewayConfig
from fm_compare.core.llm.client import GatewayClient, ChatMessage
from fm_compare.core.llm.errors import GatewayError
from fm_compare.security import safe_logger as log

_SYSTEM_PROMPT = (
    "Ты — финансовый аналитик. По структурированным результатам сравнения двух "
    "версий финансовой модели напиши краткое управленческое резюме на русском "
    "языке: 3–6 предложений, по делу, без воды. Не выдумывай цифры, опирайся "
    "только на переданные данные. Не добавляй приветствий и заключений."
)

# Cap how much we send upstream — keeps requests small and predictable.
_MAX_BLOCKS = 12
_MAX_ITEMS_PER_BLOCK = 8


def _blocks_to_user_text(blocks: list[dict]) -> str:
    lines: list[str] = []
    for block in blocks[:_MAX_BLOCKS]:
        title = block.get("title", "")
        text = block.get("text", "")
        lines.append(f"## {title}")
        if text:
            lines.append(text)
        for item in (block.get("items") or [])[:_MAX_ITEMS_PER_BLOCK]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).strip()


def enhance_summary(
    blocks: list[dict],
    cfg: GatewayConfig | None = None,
) -> list[dict]:
    """
    Return summary blocks with an LLM overview prepended, or the original
    blocks unchanged if the LLM is unavailable.
    """
    if not blocks:
        return blocks

    cfg = cfg or GatewayConfig.from_env()
    if not cfg.is_configured:
        log.info("LLM summary skipped: gateway not configured (fallback to rule-based)")
        return blocks

    client = GatewayClient(cfg)
    if not client.health_check():
        log.warning("LLM summary skipped: gateway health check failed (fallback)")
        return blocks

    user_text = _blocks_to_user_text(blocks)
    if not user_text:
        return blocks

    try:
        result = client.chat(
            [
                ChatMessage("system", _SYSTEM_PROMPT),
                ChatMessage("user", user_text),
            ],
            temperature=0.2,
            max_tokens=600,
        )
    except GatewayError as e:
        # Log only safe metadata — never the prompt or response.
        log.warning(
            f"LLM summary failed: {type(e).__name__} "
            f"code={getattr(e, 'code', None)} cid={getattr(e, 'correlation_id', None)}"
        )
        return blocks

    overview = (result.content or "").strip()
    if not overview:
        return blocks

    log.info(
        f"LLM summary generated: model={result.model} cid={result.correlation_id} "
        f"usage={result.usage.get('total_tokens') if result.usage else None}"
    )
    overview_block = {
        "type": "llm_overview",
        "title": "Управленческое резюме (AI)",
        "text": overview,
        "items": [],
    }
    return [overview_block, *blocks]
