"""Split import warnings into issues someone has to look at and automatic repairs.

An automatic repair is something the importer already fixed with certainty (for example, a
";" inside a customer's notes that shifted the CSV columns). It is kept for traceability but
does not count as an incident nor raise notifications. If a repair does not fit, the importer
reports a real warning instead (an unreadable "Fecha de alta" after the notes, for instance).
"""

from __future__ import annotations

AUTO_REPAIRED_CODES = frozenset({"extra_separators"})


def is_repair(issue: dict) -> bool:
    return issue.get("code") in AUTO_REPAIRED_CODES


def split_issues(issues: list | None) -> tuple[list[dict], list[dict]]:
    """Return ``(warnings, repairs)``."""
    warnings, repairs = [], []
    for issue in issues or []:
        (repairs if is_repair(issue) else warnings).append(issue)
    return warnings, repairs


def with_repairs(payload: dict) -> dict:
    """Move automatic repairs of a preview/commit payload out of ``warnings``."""
    warnings, repairs = split_issues(payload.get("warnings"))
    return {**payload, "warnings": warnings, "repairs": repairs}
