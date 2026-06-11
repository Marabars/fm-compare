"""
Cross-sheet consistency check (Stage 1b).

The same article often appears on several sheets of a model (e.g. a cost item
on COST, Себ20%НДС, CF). If its headline total differs between sheets beyond a
tolerance, that's a data-integrity problem the analyst must see BEFORE trusting
the comparison — exactly the ТЗ example: 200 тыс on sheet 1 vs 223 тыс on sheet 3.

This is deterministic (numbers computed in code). Results are surfaced as
Discrepancy items; they never block the analysis (per product decision), and
the tolerance is configurable.
"""
from __future__ import annotations

import re
from typing import Iterable

from fm_compare.core.excel_reader import WorkbookData, SheetData
from fm_compare.core.hierarchy import build_hierarchy
from fm_compare.core.models import ArticleNode, ArticleTree, Discrepancy, Severity
from fm_compare.core.utils import is_numeric
from fm_compare.security import safe_logger as log

_DEFAULT_REL_TOL = 0.01      # 1%
_DEFAULT_ABS_TOL = 1000.0    # ignore sub-1000 currency-unit noise

_WS_RE = re.compile(r"\s+")
_CODE_PREFIX_RE = re.compile(r"^\s*\d{3,5}[_\s).]*")


def _norm_label(label: str) -> str:
    """Normalize a label for cross-sheet matching: drop leading code, lower,
    collapse whitespace, strip punctuation tails."""
    s = _CODE_PREFIX_RE.sub("", label or "")
    s = _WS_RE.sub(" ", s).strip().lower()
    s = s.strip(" .:–—-")
    return s


def _iter_nodes(tree: ArticleTree) -> Iterable[ArticleNode]:
    stack = list(tree.roots)
    seen: set[int] = set()
    while stack:
        n = stack.pop()
        if id(n) in seen:
            continue
        seen.add(id(n))
        yield n
        stack.extend(n.children)


def _article_key(node: ArticleNode) -> str | None:
    """Stable cross-sheet key for an article: prefer code, else normalized label."""
    if node.code:
        return f"code:{node.code}"
    lbl = _norm_label(node.label)
    return f"label:{lbl}" if len(lbl) >= 4 else None


def cross_sheet_check(
    wb: WorkbookData,
    sheets: list[str] | None = None,
    dep_graph: dict[str, list[str]] | None = None,
    rel_tol: float = _DEFAULT_REL_TOL,
    abs_tol: float = _DEFAULT_ABS_TOL,
) -> list[Discrepancy]:
    """
    Compare the headline value of each article across the sheets it appears on.

    Returns one Discrepancy per article whose values differ between two sheets
    beyond tolerance (worst pair reported). Does not raise.
    """
    target_sheets = sheets or list(wb.sheets.keys())

    # article_key -> list of (sheet, node)
    index: dict[str, list[tuple[str, ArticleNode]]] = {}
    for sheet in target_sheets:
        sd = wb.sheets.get(sheet)
        if sd is None:
            continue
        tree = build_hierarchy(sd, dep_graph=dep_graph)
        for node in _iter_nodes(tree):
            if not is_numeric(node.value):
                continue
            key = _article_key(node)
            if key is None:
                continue
            index.setdefault(key, []).append((sheet, node))

    out: list[Discrepancy] = []
    for key, occurrences in index.items():
        if len(occurrences) < 2:
            continue
        # Compare each sheet's value; report the widest disagreement.
        worst: tuple[float, tuple[str, ArticleNode], tuple[str, ArticleNode]] | None = None
        for i in range(len(occurrences)):
            for j in range(i + 1, len(occurrences)):
                sa, na = occurrences[i]
                sb, nb = occurrences[j]
                if sa == sb:
                    continue
                va, vb = float(na.value), float(nb.value)
                diff = abs(va - vb)
                denom = max(abs(va), abs(vb), 1.0)
                if diff <= abs_tol or diff / denom <= rel_tol:
                    continue
                if worst is None or diff > worst[0]:
                    worst = (diff, (sa, na), (sb, nb))
        if worst is None:
            continue
        diff, (sa, na), (sb, nb) = worst
        va, vb = float(na.value), float(nb.value)
        denom = max(abs(va), abs(vb), 1.0)
        pct = (va - vb) / denom * 100.0
        article = na.code or na.label
        out.append(Discrepancy(
            kind="cross_sheet",
            article=article,
            sheet_a=sa, value_a=va,
            sheet_b=sb, value_b=vb,
            delta=va - vb,
            delta_pct=pct,
            severity=Severity.HIGH if diff / denom > 0.05 else Severity.MEDIUM,
            message=(
                f"Статья «{article}» расходится между листами: "
                f"{sa}={va:,.0f} и {sb}={vb:,.0f} (Δ {va - vb:,.0f})"
            ),
            addr_a=na.addr, addr_b=nb.addr,
        ))

    out.sort(key=lambda d: abs(d.delta) if is_numeric(d.delta) else 0, reverse=True)
    log.info(f"Cross-sheet check: {len(out)} discrepancies across {len(target_sheets)} sheets")
    return out
