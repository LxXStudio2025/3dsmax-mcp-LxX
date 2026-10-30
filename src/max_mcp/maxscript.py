"""Pure MAXScript value encoding shared by tool adapters.

No scene access occurs here. Strings and numeric values have one validation
path, so additions to the tool surface cannot silently bypass escaping.
"""

from __future__ import annotations

import math
from typing import Any


def mxs_str(value: str) -> str:
    """Render a Python string as a MAXScript string literal."""
    s = str(value)
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("\t", "\\t")
    return f'"{s}"'


def finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def mxs_lit(value: Any) -> str:
    """Render a Python value as a MAXScript literal."""
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        finite_number(value, "value")
        return repr(value)
    if isinstance(value, str):
        return mxs_str(value)
    if isinstance(value, (list, tuple)):
        numeric = all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
        )
        if numeric and len(value) == 3:
            return "[{}, {}, {}]".format(
                *[repr(finite_number(v, "vector component")) for v in value]
            )
        items = []
        for v in value:
            if isinstance(v, str):
                items.append(mxs_str(v))
            elif isinstance(v, bool):
                items.append("true" if v else "false")
            elif v is None:
                items.append("undefined")
            else:
                items.append(mxs_lit(v))
        return "#(" + ", ".join(items) + ")"
    return mxs_str(str(value))


def mxs_list(values: list[Any]) -> str:
    return "#(" + ", ".join(mxs_str(v) for v in values) + ")"
