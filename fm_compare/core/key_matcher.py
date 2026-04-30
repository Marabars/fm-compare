"""
Business key matcher: exact → fuzzy → coordinate → not matched.
Never logs key values — only match statistics.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz, process

from fm_compare.core.excel_reader import SheetData
from fm_compare.core.business_dictionary import BusinessDictionary, SheetEntry
from fm_compare.core.models import MatchType, CellAddress
from fm_compare.security import safe_logger as log


FUZZY_THRESHOLD = 80       # minimum score for fuzzy match (0..100)
LABEL_COL_DEFAULT = 2      # column B as default label column


@dataclass
class KeyedRow:
    key: str
    label: str
    row: int
    col: int
    sheet: str
    addr: CellAddress


@dataclass
class MatchResult:
    key_v1: str
    key_v2: str
    row_v1: int | None
    row_v2: int | None
    col_v1: int | None
    col_v2: int | None
    match_type: MatchType
    confidence: float          # 0..1


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _build_synonyms_map(bd: BusinessDictionary) -> dict[str, str]:
    """Build map: normalized_synonym → canonical_name."""
    m: dict[str, str] = {}
    for canonical, syns in bd.synonyms.items():
        m[_normalize(canonical)] = canonical
        for syn in syns:
            m[_normalize(syn)] = canonical
    return m


def extract_keys(
    sd: SheetData,
    label_col: int = LABEL_COL_DEFAULT,
    min_row: int = 1,
) -> list[KeyedRow]:
    """
    Extract keyed rows from a sheet using label column.
    Key = normalized label text.
    """
    keys: list[KeyedRow] = []
    for row in range(min_row, sd.max_row + 1):
        cd = sd.cells.get((row, label_col))
        if cd is None or cd.value is None:
            continue
        raw_label = str(cd.value).strip()
        if not raw_label:
            continue
        key = _normalize(raw_label)
        keys.append(KeyedRow(
            key=key,
            label=raw_label,
            row=row,
            col=label_col,
            sheet=sd.name,
            addr=CellAddress(sheet=sd.name, row=row, col=label_col),
        ))
    log.debug(f"Extracted {len(keys)} keys from sheet")
    return keys


def match_sheets(
    keys_v1: list[KeyedRow],
    keys_v2: list[KeyedRow],
    bd: BusinessDictionary,
    global_abs: float | None = None,
    global_pct: float | None = None,
) -> list[MatchResult]:
    """
    Match keys from V1 to V2 using exact → fuzzy → coordinate fallback.
    """
    syn_map = _build_synonyms_map(bd)

    v2_by_key: dict[str, KeyedRow] = {}
    for kr in keys_v2:
        canonical = syn_map.get(kr.key, kr.key)
        v2_by_key[canonical] = kr

    v2_keys_list = list(v2_by_key.keys())
    matched_v2: set[str] = set()
    results: list[MatchResult] = []

    for kr1 in keys_v1:
        canonical1 = syn_map.get(kr1.key, kr1.key)

        # 1. Exact match
        if canonical1 in v2_by_key:
            kr2 = v2_by_key[canonical1]
            matched_v2.add(canonical1)
            results.append(MatchResult(
                key_v1=kr1.key, key_v2=kr2.key,
                row_v1=kr1.row, row_v2=kr2.row,
                col_v1=kr1.col, col_v2=kr2.col,
                match_type=MatchType.EXACT, confidence=1.0,
            ))
            continue

        # 2. Fuzzy match
        if v2_keys_list:
            best_match, score, _ = process.extractOne(
                canonical1, v2_keys_list, scorer=fuzz.token_sort_ratio
            )
            if score >= FUZZY_THRESHOLD:
                kr2 = v2_by_key[best_match]
                if best_match not in matched_v2:
                    matched_v2.add(best_match)
                    results.append(MatchResult(
                        key_v1=kr1.key, key_v2=kr2.key,
                        row_v1=kr1.row, row_v2=kr2.row,
                        col_v1=kr1.col, col_v2=kr2.col,
                        match_type=MatchType.FUZZY,
                        confidence=score / 100.0,
                    ))
                    continue

        # 3. Coordinate fallback: same row number
        kr2_coord = next(
            (kr for kr in keys_v2 if kr.row == kr1.row and kr.col == kr1.col),
            None
        )
        if kr2_coord is not None:
            results.append(MatchResult(
                key_v1=kr1.key, key_v2=kr2_coord.key,
                row_v1=kr1.row, row_v2=kr2_coord.row,
                col_v1=kr1.col, col_v2=kr2_coord.col,
                match_type=MatchType.COORDINATE, confidence=0.5,
            ))
            continue

        # 4. Not matched
        results.append(MatchResult(
            key_v1=kr1.key, key_v2="",
            row_v1=kr1.row, row_v2=None,
            col_v1=kr1.col, col_v2=None,
            match_type=MatchType.NOT_MATCHED, confidence=0.0,
        ))

    # Find new items (in V2 but not matched to anything in V1)
    for canonical2, kr2 in v2_by_key.items():
        if canonical2 not in matched_v2:
            results.append(MatchResult(
                key_v1="", key_v2=kr2.key,
                row_v1=None, row_v2=kr2.row,
                col_v1=None, col_v2=kr2.col,
                match_type=MatchType.NEW_ITEM, confidence=1.0,
            ))

    exact = sum(1 for r in results if r.match_type == MatchType.EXACT)
    fuzzy = sum(1 for r in results if r.match_type == MatchType.FUZZY)
    coord = sum(1 for r in results if r.match_type == MatchType.COORDINATE)
    not_m = sum(1 for r in results if r.match_type == MatchType.NOT_MATCHED)
    new_i = sum(1 for r in results if r.match_type == MatchType.NEW_ITEM)
    log.info(f"Match results: exact={exact} fuzzy={fuzzy} coord={coord} not_matched={not_m} new={new_i}")
    return results
