"""Build a model table from REAL observations and an exhaustive event ledger."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from glofguard.training_labels import (
    build_verified_training_labels,
    merge_verified_training_labels,
    validate_verified_training_frame,
)


def build_training_data(
    model_input: Path,
    observations: Path,
    events: Path,
    labels_output: Path,
    training_output: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build and write the verified labels and fully labeled training table."""
    model_frame = pd.read_csv(model_input)
    observations_frame = pd.read_csv(observations)
    event_frame = pd.read_csv(events)

    required_observation_keys = {"lake_id", "observation_date"}
    if not required_observation_keys <= set(observations_frame.columns):
        missing = sorted(required_observation_keys - set(observations_frame.columns))
        raise ValueError(f"Observations are missing required columns: {', '.join(missing)}")
    required_event_columns = {"lake_id", "event_date", "event_id", "event_name"}
    if not required_event_columns <= set(event_frame.columns):
        missing = sorted(required_event_columns - set(event_frame.columns))
        raise ValueError(f"Events are missing required columns: {', '.join(missing)}")

    observation_keys = observations_frame[["lake_id", "observation_date"]].copy()
    observation_keys["lake_id"] = observation_keys["lake_id"].astype(str)
    observation_keys["observation_date"] = pd.to_datetime(
        observation_keys["observation_date"], errors="raise"
    ).dt.strftime("%Y-%m-%d")
    if observation_keys.duplicated().any():
        raise ValueError("Observations contain duplicate lake_id/observation_date keys")

    event_frame["lake_id"] = event_frame["lake_id"].astype(str)
    event_frame["event_date"] = pd.to_datetime(event_frame["event_date"], errors="raise")
    if event_frame[["event_id", "event_date"]].isna().any().any():
        raise ValueError("Events must have non-null event_id and event_date values")
    if event_frame["event_id"].astype(str).duplicated().any():
        raise ValueError("Events contain duplicate event_id values")

    labels = build_verified_training_labels(observations_frame, event_frame)
    validation = validate_verified_training_frame(labels)
    if not validation["valid"]:
        raise ValueError("Generated labels failed validation: " + "; ".join(validation["errors"]))

    merged = merge_verified_training_labels(model_frame, labels)
    required_label_columns = [
        "glof_within_next_7_days",
        "glof_within_next_30_days",
        "label_provenance_status",
        "verification_date",
    ]
    if merged[required_label_columns].isna().any().any():
        raise ValueError("Model input contains rows without verified labels")

    labels_output.parent.mkdir(parents=True, exist_ok=True)
    training_output.parent.mkdir(parents=True, exist_ok=True)
    labels.to_csv(labels_output, index=False)
    merged.to_csv(training_output, index=False)
    return labels, merged


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a training table from REAL observations and verified historical events."
    )
    parser.add_argument("--model-input", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--labels-output", type=Path, required=True)
    parser.add_argument("--training-output", type=Path, required=True)
    parser.add_argument(
        "--event-ledger-complete",
        action="store_true",
        help="Required acknowledgement that absent events are verified negatives.",
    )
    args = parser.parse_args()
    if not args.event_ledger_complete:
        parser.error(
            "--event-ledger-complete is required; an incomplete event list cannot create verified negatives"
        )
    labels, merged = build_training_data(
        args.model_input,
        args.observations,
        args.events,
        args.labels_output,
        args.training_output,
    )
    print(f"Wrote {len(labels)} verified labels and {len(merged)} training rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())