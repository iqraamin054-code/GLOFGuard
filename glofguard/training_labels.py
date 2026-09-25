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


def validate_verified_training_frame(frame: pd.DataFrame) -> dict[str, object]:
    """Validate an in-memory verified-label dataframe."""
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


def validate_verified_training_labels(path: str | Path | pd.DataFrame) -> dict[str, object]:
    """Validate a separately managed verified labels file or dataframe.

    The labels are only valid for model training when every row is explicitly marked
    as `verified` and the required target columns are present.
    """
    if isinstance(path, pd.DataFrame):
        return validate_verified_training_frame(path)

    label_file = Path(path)
    if not label_file.exists():
        return {
            "valid": False,
            "rows": 0,
            "errors": [f"Verified label file does not exist: {label_file}"],
        }

    frame = pd.read_csv(label_file)
    return validate_verified_training_frame(frame)


def merge_verified_training_labels(
    model_frame: pd.DataFrame,
    verified_frame: pd.DataFrame,
    *,
    key_column: str = "lake_id",
    date_column: str | None = "observation_date",
) -> pd.DataFrame:
    """Attach a verified outcome table to a model dataframe.

    The merge is intentionally strict: the verified labels must already pass the
    repository validation rules, and the target columns must not already exist in
    the model dataset under the same names.
    """
    result = validate_verified_training_frame(verified_frame)
    if not result["valid"]:
        raise ValueError("Verified labels failed validation: " + "; ".join(result["errors"]))

    if key_column not in model_frame.columns:
        raise ValueError(f"Model dataset is missing the join key column: {key_column}")
    if key_column not in verified_frame.columns:
        raise ValueError(f"Verified label table is missing the join key column: {key_column}")

    target_columns = [
        "glof_within_next_7_days",
        "glof_within_next_30_days",
        "label_provenance_status",
        "source_event_id",
        "source_event_name",
        "verification_date",
    ]
    for column in target_columns:
        if column in model_frame.columns:
            raise ValueError(
                f"Model dataset already contains target column {column}; this would overwrite an existing label."
            )

    merge_on = [key_column]
    if date_column is not None:
        if date_column not in model_frame.columns:
            raise ValueError(f"Model dataset is missing the date field: {date_column}")
        if date_column not in verified_frame.columns:
            raise ValueError(f"Verified label table is missing the date field: {date_column}")
        merge_on.append(date_column)

    right = verified_frame[
        list(dict.fromkeys([key_column] + ([date_column] if date_column else []) + target_columns))
    ].copy()
    merged = model_frame.merge(right, on=merge_on, how="left")
    return merged
