"""Utilities for separately managed, verified training labels.

This module enforces the repository rule that outcome targets must not live in the
canonical live-data schema. Verified labels are kept in a separate table or CSV and
must be explicitly marked as authoritative before they can be used for training.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_VERIFIED_LABEL_COLUMNS = {
    "lake_id",
    "observation_date",
    "glof_within_next_7_days",
    "glof_within_next_30_days",
    "label_provenance_status",
    "source_event_id",
    "source_event_name",
    "verification_date",
}


def validate_verified_training_labels(path: str | Path) -> dict[str, object]:
    """Validate a separately managed verified labels file.

    The labels are only valid for model training when every row is explicitly marked
    as `verified` and the required target columns are present.
    """
    label_file = Path(path)
    if not label_file.exists():
        return {
            "valid": False,
            "rows": 0,
            "errors": [f"Verified label file does not exist: {label_file}"],
        }

    frame = pd.read_csv(label_file)
    missing = sorted(REQUIRED_VERIFIED_LABEL_COLUMNS - set(frame.columns))
    if missing:
        return {
            "valid": False,
            "rows": int(len(frame)),
            "errors": [f"Missing required columns: {', '.join(missing)}"],
        }

    errors: list[str] = []
    status = frame["label_provenance_status"].astype(str).str.lower()
    if not status.eq("verified").all():
        errors.append("Every training label row must have label_provenance_status=verified")

    for column in ["glof_within_next_7_days", "glof_within_next_30_days"]:
        values = pd.to_numeric(frame[column], errors="coerce")
        if not values.notna().all():
            errors.append(f"Column {column} contains non-numeric values")
        if not ((values.fillna(-1) >= 0) & (values.fillna(-1) <= 1)).all():
            errors.append(f"Column {column} must be binary 0/1 values")

    if frame.empty:
        errors.append("Verified label file is empty")

    return {
        "valid": not errors,
        "rows": int(len(frame)),
        "errors": errors,
    }
