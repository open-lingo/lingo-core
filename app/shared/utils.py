"""Cross-cutting helpers shared by multiple modules.

Keep this lean — domain-specific helpers belong next to their domain.
"""

from typing import Any


def earliest_due_date(payload: dict[str, Any]) -> str:
    """Pick the earliest dueDate across modalities for indexing.

    The SRS card payload may hold per-modality sub-states (`recognition`,
    `production`) each with their own `dueDate`, plus a legacy top-level
    `dueDate` on pre-modal payloads. Return the lexicographically smallest
    ISO date (YYYY-MM-DD) found, or empty string if none.
    """
    candidates: list[str] = []
    for modality_key in ("recognition", "production"):
        m = payload.get(modality_key)
        if isinstance(m, dict) and isinstance(m.get("dueDate"), str):
            candidates.append(m["dueDate"])
    top = payload.get("dueDate")
    if isinstance(top, str):
        candidates.append(top)
    return min(candidates) if candidates else ""
