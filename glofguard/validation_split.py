"""Leakage-resistant time-based and geographic model validation split."""

from __future__ import annotations

from datetime import date
from typing import Any


def geographic_group(latitude: float, longitude: float, cell_degrees: float = 0.25) -> str:
    return f"{int(latitude // cell_degrees)}_{int(longitude // cell_degrees)}"


def time_geographic_split(
    frame: Any,
    cutoff_date: date,
    *,
    date_column: str = "observation_date",
    group_column: str = "geographic_group",
) -> tuple[Any, Any, dict[str, int]]:
    """Return earlier training rows and later test rows with disjoint regions.

    Groups appearing after the cutoff belong exclusively to the test side.
    Earlier rows from those groups are excluded, preventing nearby lakes from
    leaking between partitions. Rows after the cutoff from training-only groups
    are likewise excluded so training is strictly earlier than testing.
    """
    import pandas as pd

    required = {date_column, group_column}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing split columns: {', '.join(sorted(missing))}")
    working = frame.copy()
    working[date_column] = pd.to_datetime(working[date_column], errors="raise").dt.date
    later = working[working[date_column] >= cutoff_date]
    test_groups = set(later[group_column].dropna().astype(str))
    if not test_groups:
        raise ValueError("No test rows exist on or after the cutoff date")
    train = working[
        (working[date_column] < cutoff_date)
        & (~working[group_column].astype(str).isin(test_groups))
    ].copy()
    test = later[later[group_column].astype(str).isin(test_groups)].copy()
    if train.empty or test.empty:
        raise ValueError("Time/geographic split produced an empty partition")
    if set(train[group_column].astype(str)) & set(test[group_column].astype(str)):
        raise AssertionError("Geographic groups overlap between train and test")
    if max(train[date_column]) >= min(test[date_column]):
        raise AssertionError("Training dates are not strictly earlier than test dates")
    audit = {
        "train_rows": len(train),
        "test_rows": len(test),
        "excluded_rows": len(working) - len(train) - len(test),
        "train_groups": train[group_column].nunique(),
        "test_groups": test[group_column].nunique(),
    }
    return train, test, audit

