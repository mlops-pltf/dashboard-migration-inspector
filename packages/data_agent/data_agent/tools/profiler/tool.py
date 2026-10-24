"""Tools: key_profile, orphan_profile, column_profile."""

from __future__ import annotations

from strands import tool

from . import profile


@tool
def key_profile(source: str, keys: list[str]) -> dict:
    """Is this key unique? Rows, distinct keys, null keys, duplicated keys with samples. A non-unique key on the
    'one' side of a join multiplies the other side's rows.

    Args:
        source: schema.table, or a SELECT (e.g. a Freeform SQL source).
        keys: Key column(s).
    """
    return profile.key_profile(source, keys)


@tool
def orphan_profile(left: str, right: str, left_keys: list[str], right_keys: list[str] | None = None) -> dict:
    """How many rows on each side find no partner across a join (what INNER drops and LEFT/RIGHT keep).

    Args:
        left: schema.table or SELECT on the left of the join.
        right: schema.table or SELECT on the right.
        left_keys: Join columns on the left.
        right_keys: Join columns on the right (default: same names).
    """
    return profile.orphan_profile(left, right, left_keys, right_keys)


@tool
def column_profile(source: str, column: str, top: int = 10) -> dict:
    """Nulls, distinct count, min, max and most frequent values of one column.

    Args:
        source: schema.table or SELECT.
        column: Column name.
        top: How many frequent values to return (at most 50).
    """
    return profile.column_profile(source, column, top)
