"""
Conversational chat turn helpers for the FM Compare web interface.

Wires together the dashboard KPI context with the gateway LLM client to power
a streaming chat endpoint. Design principles:

  - Business/financial data MUST travel in role="user" only. role="system"
    carries only behavioral instructions; the gateway does NOT mask system
    messages.
  - The client already sends X-Security-Prompt-Injection: false automatically.
  - Never log prompt content, bearer tokens, or _security fields.
  - Graceful fallback: if the gateway is not configured or unreachable, yields
    a polite unavailability message instead of raising.
"""
from __future__ import annotations

from typing import Iterator

from fm_compare.core.llm.client import GatewayClient, ChatMessage
from fm_compare.core.llm.config import GatewayConfig
from fm_compare.core.llm.errors import GatewayError
from fm_compare.security import safe_logger as log

# Instructions only — NO financial data here (gateway does not mask system role).
_SYSTEM_PROMPT = (
    "Ты — ИИ-агент для анализа финансовых моделей. "
    "Отвечай по-русски. "
    "Опирайся только на данные из сообщений пользователя. "
    "Не выдумывай цифры."
)

# Cap KPI rows forwarded to the LLM to keep token usage predictable.
_MAX_KPI_ROWS = 20
# Cap history depth to prevent hitting model context limits in long conversations.
_MAX_HISTORY_TURNS = 10


def build_dashboard_context(dashboard: dict) -> str:
    """
    Serialize dashboard KPI data to a markdown table for the LLM user context.

    Formats columns: Показатель | Группа | Ед. | V2 | V1 | Δ | Δ%
    When has_v3 is True, appends: V3 | Δ(V2-V3)

    Limited to the first _MAX_KPI_ROWS rows to keep token usage manageable.
    Returns an empty string when the dashboard is empty or has no KPIs.
    """
    if not dashboard:
        return ""

    kpis: list[dict] = dashboard.get("kpis") or []
    if not kpis:
        return ""

    has_v3: bool = bool(dashboard.get("has_v3"))
    date_v1: str = (dashboard.get("date_v1") or "V1")[:10]
    date_v2: str = (dashboard.get("date_v2") or "V2")[:10]
    date_v3: str = (dashboard.get("date_v3") or "V3")[:10]

    lines: list[str] = []

    if has_v3:
        lines.append(
            f"| Показатель | Группа | Ед. | {date_v2} | {date_v1} | Δ | Δ% "
            f"| {date_v3} | Δ(V2-V3) |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
    else:
        lines.append(
            f"| Показатель | Группа | Ед. | {date_v2} | {date_v1} | Δ | Δ% |"
        )
        lines.append("|---|---|---|---|---|---|---|")

    def _fmt(val) -> str:
        if val is None:
            return ""
        if isinstance(val, float):
            return f"{val:,.2f}"
        return str(val)

    for row in kpis[:_MAX_KPI_ROWS]:
        name = row.get("kpi_name") or ""
        group = row.get("kpi_group") or ""
        unit = row.get("unit") or ""
        v1 = _fmt(row.get("value_v1"))
        v2 = _fmt(row.get("value_v2"))
        delta = _fmt(row.get("delta"))
        delta_pct = _fmt(row.get("delta_pct"))

        if has_v3:
            v3 = _fmt(row.get("value_v3"))
            d_v2_v3 = _fmt(row.get("delta_v2_v3"))
            lines.append(
                f"| {name} | {group} | {unit} | {v2} | {v1} | {delta} | {delta_pct} "
                f"| {v3} | {d_v2_v3} |"
            )
        else:
            lines.append(
                f"| {name} | {group} | {unit} | {v2} | {v1} | {delta} | {delta_pct} |"
            )

    total = len(kpis)
    if total > _MAX_KPI_ROWS:
        lines.append(f"\n_(Показаны первые {_MAX_KPI_ROWS} из {total} строк)_")

    return "\n".join(lines)


def stream_chat_turn(
    history: list[dict],
    dashboard: dict,
    cfg: GatewayConfig | None = None,
) -> Iterator[str]:
    """
    Stream one chat turn, yielding text fragments from the gateway LLM.

    `history` is a list of {"role": "user"|"assistant", "content": "..."} dicts
    that INCLUDES the new user message as the last item.

    The dashboard context is prepended to the FIRST user message so that
    financial data always travels in role="user" (per gateway security rules).

    Returns a single-item iterator with a polite unavailability message if:
      - the gateway is not configured (credentials missing)
      - the gateway health check fails
    Never raises; all GatewayError subclasses are caught and converted to a
    user-visible Russian-language fallback message.
    """
    cfg = cfg or GatewayConfig.from_env()

    if not cfg.is_configured:
        log.info("Chat stream skipped: gateway not configured")
        yield "Языковая модель недоступна: шлюз не настроен."
        return

    client = GatewayClient(cfg)
    if not client.health_check():
        log.warning("Chat stream skipped: gateway health check failed")
        yield "Языковая модель временно недоступна. Попробуйте позже."
        return

    # System message: instructions only, no financial data.
    messages: list[ChatMessage] = [ChatMessage("system", _SYSTEM_PROMPT)]

    # Inject dashboard context into the first user message so it stays in
    # the user role (gateway security rule: system role is never masked).
    context_text = build_dashboard_context(dashboard)
    first_user_injected = False

    # Trim history to avoid exceeding model context limits.
    trimmed_history = history[-_MAX_HISTORY_TURNS:]

    for turn in trimmed_history:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role == "user" and not first_user_injected and context_text:
            content = f"Данные финансовой модели:\n\n{context_text}\n\n---\n\n{content}"
            first_user_injected = True
        messages.append(ChatMessage(role, content))

    try:
        yield from client.stream_chat(messages, temperature=0.3)
    except GatewayError as e:
        # Log only safe metadata — never prompt content or secrets.
        log.warning(
            f"Chat stream failed: {type(e).__name__} "
            f"code={getattr(e, 'code', None)} "
            f"cid={getattr(e, 'correlation_id', None)}"
        )
        yield f"Ошибка при обращении к языковой модели: {type(e).__name__}."
