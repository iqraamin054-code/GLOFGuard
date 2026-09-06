"""Data, feature, prediction, freshness, and outcome monitoring."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .storage import Repository


MONITORED_FEATURES = [
    "area_current_km2",
    "area_change_30d",
    "area_change_90d",
    "temperature_current",
    "temperature_anomaly",
    "rainfall_anomaly",
    "rainfall_last_24h",
    "rainfall_last_7d",
    "forecast_rainfall_next_72h",
]


def population_stability_index(reference: pd.Series, current: pd.Series) -> float | None:
    reference = pd.to_numeric(reference, errors="coerce").dropna()
    current = pd.to_numeric(current, errors="coerce").dropna()
    if len(reference) < 20 or len(current) < 20:
        return None
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, 11)))
    if len(edges) < 3:
        return 0.0
    edges[0] = -np.inf
    edges[-1] = np.inf
    reference_counts, _ = np.histogram(reference, bins=edges)
    current_counts, _ = np.histogram(current, bins=edges)
    reference_rate = np.maximum(reference_counts / reference_counts.sum(), 1e-6)
    current_rate = np.maximum(current_counts / current_counts.sum(), 1e-6)
    return float(np.sum((current_rate - reference_rate) * np.log(current_rate / reference_rate)))


def evaluate_verified_targets(
    records: pd.DataFrame, labels_path: Path | None
) -> dict[str, Any]:
    """Evaluate only a separately supplied, explicitly verified target table."""
    if labels_path is None or not labels_path.exists():
        return {
            "status": "unavailable",
            "reason": "No verified dated outcome-label table was supplied",
            "calibration": None,
            "false_positives": None,
            "false_negatives": None,
        }
    if records.empty or not {"lake_id", "observation_date"}.issubset(records.columns):
        return {
            "status": "unavailable",
            "reason": "No prediction rows are available for verified-label evaluation",
            "calibration": None,
            "false_positives": None,
            "false_negatives": None,
        }
    labels = pd.read_csv(labels_path)
    required = {
        "lake_id",
        "observation_date",
        "glof_within_next_7_days",
        "label_provenance_status",
    }
    missing = required - set(labels.columns)
    if missing:
        raise ValueError(
            "Verified label table is missing: " + ", ".join(sorted(missing))
        )
    if not labels["label_provenance_status"].astype(str).str.lower().eq("verified").all():
        raise ValueError("Every monitoring label must have label_provenance_status=verified")
    joined = records.merge(
        labels[list(required)], on=["lake_id", "observation_date"], how="inner"
    ).dropna(subset=["risk_score"])
    if joined.empty:
        return {
            "status": "unavailable",
            "reason": "No prediction rows match verified label dates",
            "calibration": None,
            "false_positives": None,
            "false_negatives": None,
        }
    actual = joined["glof_within_next_7_days"].astype(int)
    predicted_high = joined["risk_level"].eq("High")
    false_positives = int(((actual == 0) & predicted_high).sum())
    false_negatives = int(((actual == 1) & ~predicted_high).sum())
    # A Brier score is only meaningful when scores are probabilities. The
    # default transparent conditions index is explicitly not a probability.
    probabilistic = joined["model_version"].astype(str).str.contains(
        "calibrated-probability", case=False
    ).all()
    calibration = None
    if probabilistic:
        probabilities = joined["risk_score"].astype(float) / 100.0
        calibration = {"brier_score": float(np.mean((probabilities - actual) ** 2))}
    return {
        "status": "evaluated",
        "rows": len(joined),
        "calibration": calibration,
        "calibration_note": (
            None if probabilistic else "Not applicable to a non-probabilistic conditions index"
        ),
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }


def build_monitoring_snapshot(
    repository: Repository,
    *,
    verified_labels_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    rows = repository.records()
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    if not rows:
        return {
            "created_at": current_time.isoformat(),
            "status": "no_predictions",
            "missing_data_rate": {},
            "feature_drift_psi": {},
            "prediction_distribution_drift_psi": None,
            "high_risk_predictions": 0,
            "data_freshness": {},
            "outcome_performance": evaluate_verified_targets(
                pd.DataFrame(), verified_labels_path
            ),
        }
    frame = pd.DataFrame(rows)
    frame["observation_date"] = pd.to_datetime(frame["observation_date"])
    latest = (
        frame.sort_values(["observation_date", "prediction_timestamp"])
        .groupby("lake_id", as_index=False)
        .tail(1)
    )
    missing_rates = {
        column: float(latest[column].isna().mean())
        for column in MONITORED_FEATURES
        if column in latest.columns
    }

    ordered_dates = sorted(frame["observation_date"].dropna().unique())
    feature_drift: dict[str, float | None] = {}
    prediction_drift = None
    if len(ordered_dates) >= 2:
        midpoint = ordered_dates[len(ordered_dates) // 2]
        reference = frame[frame["observation_date"] < midpoint]
        current = frame[frame["observation_date"] >= midpoint]
        for column in MONITORED_FEATURES:
            if column in frame:
                feature_drift[column] = population_stability_index(
                    reference[column], current[column]
                )
        prediction_drift = population_stability_index(
            reference.get("risk_score", pd.Series(dtype=float)),
            current.get("risk_score", pd.Series(dtype=float)),
        )

    freshness: dict[str, Any] = {}
    for field in (
        "satellite_observation_date",
        "weather_observation_date",
        "forecast_creation_time",
    ):
        parsed = pd.to_datetime(latest[field], errors="coerce", utc=True)
        ages = (pd.Timestamp(current_time) - parsed).dt.total_seconds() / 3600.0
        valid = ages.dropna()
        freshness[field] = {
            "missing_rate": float(parsed.isna().mean()),
            "maximum_age_hours": float(valid.max()) if not valid.empty else None,
            "median_age_hours": float(valid.median()) if not valid.empty else None,
        }
    snapshot = {
        "created_at": current_time.isoformat(),
        "status": "ok",
        "records": len(frame),
        "lakes": int(latest["lake_id"].nunique()),
        "mock_records": int(frame["source_mode"].astype(str).str.upper().eq("MOCK").sum()),
        "missing_data_rate": missing_rates,
        "feature_drift_psi": feature_drift,
        "prediction_distribution_drift_psi": prediction_drift,
        "high_risk_predictions": int(latest["risk_level"].eq("High").sum()),
        "prediction_distribution": latest["risk_level"].value_counts(dropna=False).to_dict(),
        "data_freshness": freshness,
        "unresolved_ingestion_failures": repository.failure_summary(),
        "outcome_performance": evaluate_verified_targets(frame, verified_labels_path),
        "retraining_guidance": (
            "Review for retraining every 3-6 months, when new verified GLOF events are "
            "added, or when significant feature/prediction drift is detected. Use both "
            "time-based and geographically disjoint validation."
        ),
    }
    repository.store_monitoring_snapshot(snapshot)
    return snapshot
