"""
Article hierarchy detection (Stage 1a).

Builds a tree of articles (line items) for a sheet, so the app understands that
e.g. 5400 = 5401 + 5402 + … . Detection is deterministic and hybrid:

  1. Code prefix — codes like 5000 / 5400 / 5401 nest by their digit prefix
     (5000 ⊃ 5400 ⊃ 5401). Codes are read from column A or the start of the
     label ("5400_СМР"). This is the strong signal on COST / Себ* sheets.
  2. Formula SUM — if a row's formula is =SUM(range)/=A+B over other article
     rows, those rows are recorded as its children (uses the dependency graph
     produced by formula_differ).
  3. Indentation — on label-only sheets (RESUME) nesting comes from how deep
     the label sits across the first few columns.

Numbers are always computed here in code; the LLM never invents hierarchy
(an optional LLM pass for ambiguous label-only sheets lives in
core/agent/hierarchy_llm.py and only runs when the gateway is available).
"""
from __future__ import annotations

import re
from typing import Any

from fm_compare.core.excel_reader import SheetData, CellData
from fm_compare.core.models import CellAddress, ArticleNode, ArticleTree, Discrepancy, Severity
from fm_compare.core.utils import is_numeric
from fm_compare.security import safe_logger as log

# Article code: 3–5 digits, optionally followed by _name. Captured from the
# label column or a dedicated code column.
_CODE_RE = re.compile(r"^\s*(\d{3,5})(?:[_\s).]|$)")

# How close parent and sum(children) must be to be considered consistent.
_DEFAULT_REL_TOL = 0.01      # 1%
_DEFAULT_ABS_TOL = 1.0       # 1 currency unit


def _extract_code(*texts: Any) -> str | None:
    """Return the first article code found in the given cell texts."""
    for t in texts:
        if t is None:
            continue
        m = _CODE_RE.match(str(t))
        if m:
            return m.group(1)
    return None


def _label_col_indent(sd: SheetData, row: int, max_probe_col: int = 6) -> tuple[int, str]:
    """
    Find the first non-empty text cell in the leading columns of a row and
    return (column_index, text). The column index doubles as an indent level
    for label-only sheets.
    """
    for col in range(1, max_probe_col + 1):
        cd = sd.cells.get((row, col))
        if cd is not None and cd.value is not None:
            s = str(cd.value).strip()
            if s and not is_numeric(cd.value):
                return col, s
    return 0, ""


# Header words that mark the headline "total" column on financial sheets.
_TOTAL_HEADER_RE = re.compile(
    r"бюджет|итог|всего|total|сумма", re.IGNORECASE | re.UNICODE
)
_HEADER_PROBE_ROWS = 8       # how many top rows to scan for header text


def _column_header_text(sd: SheetData, col: int) -> str:
    """Concatenate non-numeric header texts of a column from the top rows."""
    parts: list[str] = []
    for row in range(1, _HEADER_PROBE_ROWS + 1):
        cd = sd.cells.get((row, col))
        if cd is not None and cd.value is not None and not is_numeric(cd.value):
            parts.append(str(cd.value))
    return " ".join(parts)


def _numeric_density(sd: SheetData, col: int) -> int:
    return sum(
        1 for row in range(1, min(sd.max_row + 1, 300))
        if sd.cells.get((row, col)) and is_numeric(sd.cells[(row, col)].value)
    )


def _pick_value_column(sd: SheetData, label_col: int) -> int | None:
    """
    Choose the column that holds each article's headline (total) value.

    Strategy: among reasonably dense numeric columns, prefer the LEFT-most one
    whose header says Бюджет/Итого/Всего/Total (financial sheets like COST keep
    the all-periods total in the first such column, with per-period columns to
    the right). Fall back to the first dense numeric column after the label.
    """
    col_cap = min(sd.max_col + 1, 80)

    dense_cols = [
        col for col in range(label_col + 1, col_cap)
        if _numeric_density(sd, col) >= 4
    ]
    if not dense_cols:
        # last resort: first numeric cell anywhere right of the label
        for col in range(label_col + 1, col_cap):
            for row in range(1, min(sd.max_row + 1, 300)):
                cd = sd.cells.get((row, col))
                if cd is not None and is_numeric(cd.value):
                    return col
        return None

    # Prefer the left-most dense column with a "total" header.
    for col in dense_cols:
        if _TOTAL_HEADER_RE.search(_column_header_text(sd, col)):
            return col

    # Otherwise the first dense numeric column.
    return dense_cols[0]


def _code_parent(code: str, known: set[str]) -> str | None:
    """
    Find the closest ancestor code by the trailing-zero scheme: 5401 is a child
    of 5400 if present, else 5000. The parent must be a *strict prefix* of the
    child (same leading digits), so 5401→5400→5000 but never 5990→5990-swallows-
    unrelated-59xx beyond its own prefix.
    """
    n = len(code)
    # Coarsen one trailing digit at a time: 5401 -> 5400 -> 5000.
    for keep in range(n - 1, 0, -1):
        cand = code[:keep] + "0" * (n - keep)
        if cand == code:
            continue
        if cand in known:
            return cand
    return None


def build_hierarchy(
    sd: SheetData,
    dep_graph: dict[str, list[str]] | None = None,
    label_col_hint: int = 2,
) -> ArticleTree:
    """
    Build an ArticleTree for one sheet.

    dep_graph: optional cell_id -> [source cell_ids] map from formula_differ;
    used to record SUM-based parent→child links. cell_id format: "Sheet!R{r}C{c}".
    """
    tree = ArticleTree(sheet=sd.name)

    value_col = _pick_value_column(sd, label_col_hint)

    # 1) Gather candidate article rows: any row with a leading text label.
    rows: list[tuple[int, int, str, str | None, Any, CellData | None]] = []
    for row in range(1, min(sd.max_row + 1, 5000)):
        col, label = _label_col_indent(sd, row)
        if not label:
            continue
        # code may be in column A even if label sits in B/C
        col_a = sd.cells.get((row, 1))
        code = _extract_code(col_a.value if col_a else None, label)
        val = None
        cd = None
        if value_col is not None:
            cd = sd.cells.get((row, value_col))
            if cd is not None and is_numeric(cd.value):
                val = cd.value
        rows.append((row, col, label, code, val, cd))

    nodes: dict[int, ArticleNode] = {}
    for row, col, label, code, val, cd in rows:
        addr = cd.address if cd is not None else CellAddress(sd.name, row, col)
        node = ArticleNode(
            code=code, label=label, addr=addr, value=val,
            level=0, source="code" if code else "indent",
        )
        nodes[row] = node
        if code:
            tree.by_code[code] = node

    # 2) Link by code prefix where codes exist.
    known_codes = set(tree.by_code.keys())
    linked_rows: set[int] = set()
    for row, _col, _label, code, _val, _cd in rows:
        if not code:
            continue
        parent_code = _code_parent(code, known_codes)
        if parent_code and parent_code != code:
            parent = tree.by_code[parent_code]
            parent.children.append(nodes[row])
            nodes[row].level = parent.level + 1
            linked_rows.add(row)

    # 3) Indentation fallback for rows without a code link: attach to the
    #    nearest preceding row with a smaller indent column.
    stack: list[tuple[int, ArticleNode]] = []  # (indent_col, node)
    for row, col, label, code, _val, _cd in rows:
        node = nodes[row]
        if row in linked_rows:
            # already placed by code; reset stack baseline to it
            stack = [(col, node)]
            continue
        while stack and stack[-1][0] >= col:
            stack.pop()
        if stack:
            parent = stack[-1][1]
            parent.children.append(node)
            node.level = parent.level + 1
            node.source = "indent"
        else:
            tree.roots.append(node)
            node.level = 0
        stack.append((col, node))

    # Roots = code-rooted nodes never used as a child + indentation roots.
    child_ids = {id(ch) for n in nodes.values() for ch in n.children}
    for node in nodes.values():
        if id(node) not in child_ids and node not in tree.roots:
            tree.roots.append(node)

    # 4) Record SUM-based links as a cross-check signal (does not re-parent;
    #    used later by validate_tree / analyst).
    if dep_graph:
        _annotate_formula_children(tree, sd, nodes, dep_graph)

    log.info(f"Hierarchy built: sheet rows={len(rows)} coded={len(tree.by_code)} roots={len(tree.roots)}")
    return tree


def _annotate_formula_children(
    tree: ArticleTree,
    sd: SheetData,
    nodes: dict[int, ArticleNode],
    dep_graph: dict[str, list[str]],
) -> None:
    """Mark nodes whose value comes from a SUM over other article rows."""
    cellid_re = re.compile(r"^(?P<sheet>.+)!R(?P<r>\d+)C(?P<c>\d+)$")
    for cell_id, sources in dep_graph.items():
        m = cellid_re.match(cell_id)
        if not m or m.group("sheet") != sd.name:
            continue
        row = int(m.group("r"))
        node = nodes.get(row)
        if node is not None and sources:
            node.source = node.source if node.source != "indent" else "formula"


def validate_tree(
    tree: ArticleTree,
    rel_tol: float = _DEFAULT_REL_TOL,
    abs_tol: float = _DEFAULT_ABS_TOL,
) -> list[Discrepancy]:
    """
    Check parent value == sum(children values) for every node that has both a
    numeric value and numeric children. Returns discrepancies (does not raise).
    """
    out: list[Discrepancy] = []

    def _check(node: ArticleNode) -> None:
        child_vals = [c.value for c in node.children if is_numeric(c.value)]
        if is_numeric(node.value) and child_vals:
            s = sum(float(v) for v in child_vals)
            parent = float(node.value)
            diff = parent - s
            denom = abs(parent) if abs(parent) > 1e-9 else 1.0
            if abs(diff) > abs_tol and abs(diff) / denom > rel_tol:
                out.append(Discrepancy(
                    kind="sum_mismatch",
                    article=node.code or node.label,
                    sheet_a=tree.sheet,
                    value_a=parent,
                    value_b=s,
                    delta=diff,
                    delta_pct=(diff / denom) * 100.0,
                    severity=Severity.HIGH if abs(diff) / denom > 0.05 else Severity.MEDIUM,
                    message=(
                        f"Сумма дочерних статей ({s:,.0f}) не сходится с "
                        f"родительской «{node.code or node.label}» ({parent:,.0f})"
                    ),
                    addr_a=node.addr,
                ))
        for c in node.children:
            _check(c)

    for root in tree.roots:
        _check(root)
    if out:
        log.info(f"Hierarchy validation: {len(out)} sum mismatches on sheet")
    return out
