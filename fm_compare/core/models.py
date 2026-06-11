"""
Core data models — pure dataclasses, no business data in logs.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MatchType(str, Enum):
    EXACT = "exact"
    FUZZY = "fuzzy"
    COORDINATE = "coordinate"
    NOT_MATCHED = "not_matched"
    NEW_ITEM = "new_item"
    DELETED_ITEM = "deleted_item"


class ChangeType(str, Enum):
    VALUE = "value"
    FORMULA = "formula"
    HIDDEN_ROW = "hidden_row"
    COMMENT = "comment"
    NEW_ITEM = "new_item"
    DELETED_ITEM = "deleted_item"
    TIMING_SHIFT = "timing_shift"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class CompareMode(str, Enum):
    FULL = "full"
    QUICK = "quick"


@dataclass
class CellAddress:
    sheet: str
    row: int
    col: int

    def __str__(self) -> str:
        from openpyxl.utils import get_column_letter
        return f"{self.sheet}!{get_column_letter(self.col)}{self.row}"


@dataclass
class WorkbookInfo:
    """Metadata about a loaded workbook — no sensitive data."""
    sheet_names: list[str] = field(default_factory=list)
    visible_sheets: list[str] = field(default_factory=list)
    hidden_sheets: list[str] = field(default_factory=list)
    file_size_mb: float = 0.0
    has_external_links: bool = False


@dataclass
class PreCheckResult:
    ok: bool = True
    blocking_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class BusinessKey:
    sheet: str
    key: str
    label: str
    address: CellAddress | None = None


@dataclass
class DiffRow:
    business_key_v1: str
    business_key_v2: str
    match_type: MatchType
    match_confidence: float          # 0..1
    addr_v1: CellAddress | None
    addr_v2: CellAddress | None
    value_v1: Any
    value_v2: Any
    delta: Any
    delta_pct: float | None
    change_type: ChangeType
    formula_v1: str | None
    formula_v2: str | None
    is_material: bool
    sign_changed: bool
    kpi_group: str | None
    kpi_level: int | None            # 1 or 2
    note: str = ""


@dataclass
class FormulaChange:
    addr_v1: CellAddress
    addr_v2: CellAddress
    formula_v1: str
    formula_v2: str
    value_v1: Any
    value_v2: Any
    logic_changed: bool              # formula changed but value same
    affected_kpi: list[str] = field(default_factory=list)
    dependency_partial: bool = False


@dataclass
class TimingShift:
    business_key: str
    sheet: str
    kpi_group: str
    periods_shifted: int             # positive = shifted right
    amount_shifted: float
    addr_v1: CellAddress | None
    addr_v2: CellAddress | None


@dataclass
class Warning:
    severity: Severity
    category: str
    message: str
    related_sheet: str = ""
    related_kpi: str = ""
    related_cell: str = ""        # Excel notation, e.g. "P&L!E42" — empty if sheet-level only
    manual_check_required: bool = False


@dataclass
class KPIResolution:
    """Per-KPI address resolution — auto-detected or user-confirmed."""
    kpi_name: str
    kpi_group: str
    kpi_level: int
    search_pattern: str
    # V1
    sheet_v1: str = ""
    row_v1: int | None = None
    col_v1: int | None = None
    label_v1: str = ""          # matched row label text
    addr_v1: str = ""           # Excel notation "Sheet!E42"
    # V2
    sheet_v2: str = ""
    row_v2: int | None = None
    col_v2: int | None = None
    label_v2: str = ""
    addr_v2: str = ""
    unit_v1: str = ""           # unit detected from V1 sheet (e.g. "тыс. руб.")
    unit_v2: str = ""           # unit detected from V2 sheet
    # V3 — optional (Stage 4: three-version comparison)
    sheet_v3: str = ""
    row_v3: int | None = None
    col_v3: int | None = None
    label_v3: str = ""
    addr_v3: str = ""
    unit_v3: str = ""
    source: str = "auto"        # "auto" | "manual"


@dataclass
class KPIValue:
    kpi_name: str
    kpi_group: str
    kpi_level: int                   # 1 = director, 2 = analyst
    unit: str
    value_v1: Any
    value_v2: Any
    delta: Any
    delta_pct: float | None
    impact: str                      # "positive" | "negative" | "neutral"
    note: str = ""
    addr_v1: CellAddress | None = None
    addr_v2: CellAddress | None = None


@dataclass
class CompareResult:
    mode: CompareMode
    pre_check_v1: PreCheckResult = field(default_factory=PreCheckResult)
    pre_check_v2: PreCheckResult = field(default_factory=PreCheckResult)
    kpi_values: list[KPIValue] = field(default_factory=list)
    diff_rows: list[DiffRow] = field(default_factory=list)
    formula_changes: list[FormulaChange] = field(default_factory=list)
    timing_shifts: list[TimingShift] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    comment_changes: list[dict] = field(default_factory=list)
    hidden_row_changes: list[dict] = field(default_factory=list)
    raw_diff_rows: list[dict] = field(default_factory=list)   # Full mode only
    summary_blocks: list[dict] = field(default_factory=list)
    run_settings: dict = field(default_factory=dict)
    dependency_graph: dict = field(default_factory=dict)
    workbook_v1: "WorkbookInfo | None" = None
    workbook_v2: "WorkbookInfo | None" = None


# ── Article hierarchy (Stage 1a) ────────────────────────────────────────────

@dataclass
class ArticleNode:
    """A single article (line item) within a sheet's hierarchy tree."""
    code: str | None                       # e.g. "5400" if present, else None
    label: str                             # row label text
    addr: CellAddress | None               # location of the row label
    value: Any = None                      # numeric value in the chosen value column
    level: int = 0                         # nesting depth (0 = top)
    source: str = "code"                   # "code" | "indent" | "formula" | "llm"
    children: list["ArticleNode"] = field(default_factory=list)


@dataclass
class ArticleTree:
    """Hierarchy of articles detected on a single sheet."""
    sheet: str
    roots: list[ArticleNode] = field(default_factory=list)
    # Flat index by code for quick lookup (only nodes that have a code).
    by_code: dict[str, ArticleNode] = field(default_factory=dict)


# ── Cross-sheet / intra-sheet discrepancies (Stage 1b) ──────────────────────

@dataclass
class Discrepancy:
    """A detected inconsistency: parent≠sum(children), or same article
    carrying different totals on two sheets."""
    kind: str                              # "sum_mismatch" | "cross_sheet"
    article: str                           # code or label of the article
    sheet_a: str
    value_a: Any
    sheet_b: str | None = None             # None for intra-sheet sum_mismatch
    value_b: Any = None
    delta: Any = None
    delta_pct: float | None = None
    severity: Severity = Severity.MEDIUM
    message: str = ""
    addr_a: CellAddress | None = None
    addr_b: CellAddress | None = None


# ── Sensitivity analysis (Stage 2) ──────────────────────────────────────────

@dataclass
class SensitivityInput:
    """One variable input parameter to sweep in sensitivity analysis."""
    name: str                              # display name, e.g. "Цена реализации"
    addr: CellAddress                      # cell to patch in the workbook
    unit: str = ""                         # "руб./кв.м", "%", etc.
    base_value: float = 0.0               # current (baseline) value read from file
    values: list[float] = field(default_factory=list)  # values to test


@dataclass
class SensitivityScenario:
    """Result of one recalculated scenario."""
    inputs: dict[str, float]              # input name → value used in this scenario
    kpi_values: dict[str, float | None]   # KPI name → computed value
    label: str = ""                        # short human-readable identifier


@dataclass
class SensitivityResult:
    """Aggregated results of a full sensitivity run."""
    base_scenario: SensitivityScenario    # run with all inputs at base_value
    scenarios: list[SensitivityScenario]  # one-at-a-time variations
    input_names: list[str]                # ordered input parameter names
    kpi_names: list[str]                  # ordered KPI names
