"""
Agent layer: orchestrates deterministic engine facts + LLM reasoning.

The LLM never invents numbers. It is used to:
  - validate/correct deterministic KPI auto-detection (kpi_validator)
  - explain deltas and give recommendations (analyst, Stage 3)

Every LLM call degrades gracefully: if the gateway is unavailable, the
deterministic result is returned unchanged.
"""
