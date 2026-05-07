"""Shared small utilities used across core modules."""
from __future__ import annotations
from typing import Any


def is_numeric(v: Any) -> bool:
    """True for int/float but NOT bool — booleans are int subclasses in Python."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)
