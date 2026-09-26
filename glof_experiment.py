"""Reproducible experimental modelling workflow for the GLOF student project.

The labels used here are unverified geographic proxy labels. This module trains
comparison models for an audit; it deliberately does not save a final risk
classifier.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".matplotlib"))
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from pyproj import Geod
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_class_weight
import tensorflow as tf


RANDOM_SEED = 42
MATCH_RADIUS_KM = 5.0
MODEL_READY_PATH = ROOT / "glofguard_model_ready.csv"
ARTIFACT_DIR = ROOT / "artifacts"

CORE_FEATURES = [
    "area",
    "temperature",
    "rainfall",
    "elevation",
    "distance_to_nearest_settlement_km",
]
MODEL_VARIANTS = {
    "Model A": CORE_FEATURES
    + [
        "longitude",
        "latitude",
        "annual_area_change_km2_per_year",
        "growth_data_available",
    ],
    "Model B": CORE_FEATURES
    + ["annual_area_change_km2_per_year", "growth_data_available"],
    "Model C": CORE_FEATURES + ["longitude", "latitude"],
    "Model D": CORE_FEATURES
    + ["longitude", "latitude", "annual_area_change_km2_per_year"],
}
VARIANT_DESCRIPTIONS = {
    "Model A": "All non-redundant numeric features, including coordinates and growth",
    "Model B": "Model A without latitude and longitude",
    "Model C": "Core model without annual growth, using all samples",
    "Model D": "Experimental model using only rows with observed growth",
}
ALGORITHMS = ("Logistic Regression", "Random Forest", "ANN")


def set_global_seed(seed: int = RANDOM_SEED) -> None:
    """Set reproducible seeds for Python, NumPy, and TensorFlow."""
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    tf.keras.utils.set_random_seed(seed)
    try:
        tf.config.experimental.enable_op_determinism()
    except Exception:
        pass


def geodesic_distance_matrix_km(
    lake_latitudes: np.ndarray,
    lake_longitudes: np.ndarray,
    reference_latitudes: np.ndarray,
    reference_longitudes: np.ndarray,
) -> np.ndarray:
    """Compute vectorized WGS84 geodesic distances."""
    geod = Geod(ellps="WGS84")
    rows = len(lake_latitudes)
    columns = len(reference_latitudes)
    lake_lons = np.repeat(lake_longitudes.astype(float), columns)
    lake_lats = np.repeat(lake_latitudes.astype(float), columns)
    ref_lons = np.tile(reference_longitudes.astype(float), rows)
    ref_lats = np.tile(reference_latitudes.astype(float), rows)
    _, _, distances_m = geod.inv(lake_lons, lake_lats, ref_lons, ref_lats)
    return np.asarray(distances_m).reshape(rows, columns) / 1000.0


def prepare_model_ready(
    input_path: Path = ROOT / "glofguard_training_data.csv",
    hazard_path: Path = ROOT / "pakistan_hazardous_lakes_unique.csv",
    output_path: Path = MODEL_READY_PATH,
) -> pd.DataFrame:
    """Create the non-imputed model-ready CSV with auditable label metadata."""
    frame = pd.read_csv(input_path)
    hazards = pd.read_csv(hazard_path)
    required = {
        "sample_id",
        "area",
        "longitude",
        "latitude",
        "is_top200",
        "temperature",
        "rainfall",
        "label",
        "elevation",
        "distance_to_nearest_settlement_km",
        "nearest_settlement_name",
        "annual_area_change_km2_per_year",
        "growth_data_available",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Training CSV is missing columns: {', '.join(missing)}")

    model_ready = frame.drop(columns=["nearest_settlement_name"]).copy()
    model_ready["is_top200"] = (
        model_ready["is_top200"]
        .astype(str)
        .str.lower()
        .map({"true": 1, "false": 0, "1": 1, "0": 0})
        .astype("int8")
    )
    model_ready["growth_data_available"] = model_ready["growth_data_available"].astype("int8")

    distances = geodesic_distance_matrix_km(
        model_ready["latitude"].to_numpy(),
        model_ready["longitude"].to_numpy(),
        hazards["Lat_lake"].to_numpy(),
        hazards["Lon_lake"].to_numpy(),
    )
    nearest_indices = distances.argmin(axis=1)
    nearest_distances = distances[np.arange(len(model_ready)), nearest_indices]
    reconstructed = (nearest_distances <= MATCH_RADIUS_KM).astype(int)
    observed = model_ready["label"].to_numpy(dtype=int)
    if not np.array_equal(reconstructed, observed):
        raise RuntimeError(
            f"Geodesic proxy-label reconstruction has {(reconstructed != observed).sum()} mismatches"
        )

    nearest_names = hazards.iloc[nearest_indices]["Lake_name"].astype(str).str.strip().to_numpy()
    positive = observed == 1
    model_ready["label_source"] = (
        "pakistan_hazardous_lakes_unique.csv (upstream provenance unverified)"
    )
    model_ready["matched_event_id"] = pd.Series(pd.NA, index=model_ready.index, dtype="string")
    model_ready["matched_event_name"] = pd.Series(
        np.where(positive, nearest_names, pd.NA), index=model_ready.index, dtype="string"
    )
    model_ready["match_distance_km"] = nearest_distances
    model_ready["matched_reference_row"] = pd.Series(
        np.where(positive, nearest_indices + 1, pd.NA), index=model_ready.index, dtype="Int64"
    )
    model_ready["label_method"] = (
        "WGS84 geodesic distance to nearest reference coordinate <= 5 km"
    )
    model_ready["label_provenance_status"] = "experimental_proxy_unverified"
    model_ready["geographic_group_025deg"] = (
        np.floor(model_ready["latitude"] / 0.25).astype(int).astype(str)
        + "_"
        + np.floor(model_ready["longitude"] / 0.25).astype(int).astype(str)
    )

    # Deliberately do not impute or scale here. Those transformations are fit
    # using training rows only in each experiment.
    model_ready.to_csv(output_path, index=False)
    return model_ready


def audit_summary(frame: pd.DataFrame) -> dict[str, Any]:
    """Return key facts used in the executed notebook."""
    top = frame["is_top200"].astype(int)
    top_min = frame.loc[top == 1, "area"].min()
    reconstructed_top = (frame["area"] >= top_min).astype(int)
    return {
        "rows": int(len(frame)),
        "columns": int(len(frame.columns)),
        "missing_values": frame.isna().sum().astype(int).to_dict(),
        "duplicate_rows": int(frame.duplicated().sum()),
        "duplicate_coordinates": int(frame.duplicated(["latitude", "longitude"]).sum()),
        "label_counts": frame["label"].value_counts().sort_index().astype(int).to_dict(),
        "label_percentages": (
            frame["label"].value_counts(normalize=True).sort_index() * 100
        ).round(4).to_dict(),
        "growth_rows": int(frame["annual_area_change_km2_per_year"].notna().sum()),
        "is_top200_reconstructed_from_area_mismatches": int((top != reconstructed_top).sum()),
        "geographic_groups": int(frame["geographic_group_025deg"].nunique()),
    }


def choose_group_fold(
    positions: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    n_splits: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Choose the SGKF fold closest to the full class rate and target size."""
    splitter = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    candidates = []
    target_fraction = 1.0 / n_splits
    base_rate = float(y.mean())
    for train_local, test_local in splitter.split(np.zeros(len(y)), y, groups):
        test_y = y[test_local]
        if np.unique(test_y).size < 2 or np.unique(y[train_local]).size < 2:
            continue
        score = abs(len(test_local) / len(y) - target_fraction) + abs(float(test_y.mean()) - base_rate)
        candidates.append((score, train_local, test_local))
    if not candidates:
        raise RuntimeError("Could not create a group-aware split containing both classes")
    _, train_local, test_local = min(candidates, key=lambda item: item[0])
    return positions[train_local], positions[test_local]


def make_master_split(frame: pd.DataFrame, seed: int = RANDOM_SEED) -> pd.Series:
    """Create train/validation/test partitions with disjoint 0.25-degree groups."""
    positions = np.arange(len(frame))
    y = frame["label"].to_numpy(dtype=int)
    groups = frame["geographic_group_025deg"].to_numpy()
    train_validation, test = choose_group_fold(positions, y, groups, 5, seed)
    inner_train, validation = choose_group_fold(
        train_validation,
        y[train_validation],
        groups[train_validation],
        4,
        seed + 1,
    )
    partition = pd.Series("", index=frame.index, dtype="string")
    partition.iloc[inner_train] = "train"
    partition.iloc[validation] = "validation"
    partition.iloc[test] = "test"
    if (partition == "").any():
        raise RuntimeError("At least one row was not assigned to a partition")

    group_sets = {
        name: set(frame.loc[partition == name, "geographic_group_025deg"])
        for name in ("train", "validation", "test")
    }
    if (
        group_sets["train"] & group_sets["validation"]
        or group_sets["train"] & group_sets["test"]
        or group_sets["validation"] & group_sets["test"]
    ):
        raise RuntimeError("Geographic groups overlap between partitions")
    return partition


def split_summary(frame: pd.DataFrame, partition: pd.Series) -> pd.DataFrame:
    """Summarize row, class, and group counts for each partition."""
    records = []
    for name in ("train", "validation", "test"):
        subset = frame.loc[partition == name]
        records.append(
            {
                "partition": name,
                "rows": len(subset),
                "label_0": int((subset["label"] == 0).sum()),
                "label_1": int((subset["label"] == 1).sum()),
                "positive_percentage": float(subset["label"].mean() * 100),
                "geographic_groups": int(subset["geographic_group_025deg"].nunique()),
            }
        )
    return pd.DataFrame(records)


def build_preprocessor(features: list[str]) -> ColumnTransformer:
    """Build median-imputation and scaling learned only during fit."""
    numeric_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )
    return ColumnTransformer(
        [("numeric", numeric_pipeline, features)],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def class_weights_from_training(y: np.ndarray) -> dict[int, float]:
    classes = np.array([0, 1])
    values = compute_class_weight(class_weight="balanced", classes=classes, y=y)
    return {int(label): float(weight) for label, weight in zip(classes, values)}


def build_ann(input_dim: int, seed: int) -> tf.keras.Model:
    set_global_seed(seed)
    model = tf.keras.Sequential(
        [
            tf.keras.layers.Input(shape=(input_dim,)),
            tf.keras.layers.Dense(16, activation="relu"),
            tf.keras.layers.Dropout(0.30),
            tf.keras.layers.Dense(8, activation="relu"),
            tf.keras.layers.Dropout(0.20),
            tf.keras.layers.Dense(1, activation="sigmoid"),
        ]
    )
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
        loss="binary_crossentropy",
        metrics=[tf.keras.metrics.AUC(curve="PR", name="pr_auc")],
    )
    return model


def select_f1_threshold(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    """Choose an F1-maximizing threshold using validation data only."""
    precision, recall, thresholds = precision_recall_curve(y_true, probabilities)
    if len(thresholds) == 0:
        return 0.5
    denominator = precision[:-1] + recall[:-1]
    f1_values = np.divide(
        2 * precision[:-1] * recall[:-1],
        denominator,
        out=np.zeros_like(denominator),
        where=denominator > 0,
    )
    best = int(np.flatnonzero(f1_values == f1_values.max())[0])
    return float(thresholds[best])


def binary_metrics(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
) -> dict[str, Any]:
    predictions = (probabilities >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, predictions, labels=[0, 1]).ravel()
    return {
        "precision": float(precision_score(y_true, predictions, zero_division=0)),
        "recall": float(recall_score(y_true, predictions, zero_division=0)),
        "f1": float(f1_score(y_true, predictions, zero_division=0)),
        "pr_auc": float(average_precision_score(y_true, probabilities)),
        "roc_auc": float(roc_auc_score(y_true, probabilities)),
        "true_negatives": int(tn),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_positives": int(tp),
        "threshold": float(threshold),
    }


def fit_experiment(
    algorithm: str,
    features: list[str],
    train: pd.DataFrame,
    validation: pd.DataFrame,
    test: pd.DataFrame,
    seed: int,
) -> tuple[dict[str, Any], pd.DataFrame, ColumnTransformer]:
    """Fit one model without leaking validation/test transformations."""
    set_global_seed(seed)
    preprocessor = build_preprocessor(features)
    X_train = preprocessor.fit_transform(train[features])
    X_validation = preprocessor.transform(validation[features])
    X_test = preprocessor.transform(test[features])
    y_train = train["label"].to_numpy(dtype=int)
    y_validation = validation["label"].to_numpy(dtype=int)
    y_test = test["label"].to_numpy(dtype=int)
    weights = class_weights_from_training(y_train)

    if algorithm == "Logistic Regression":
        classifier = LogisticRegression(
            class_weight=weights,
            max_iter=2500,
            solver="lbfgs",
            random_state=seed,
        )
        classifier.fit(X_train, y_train)
        validation_probabilities = classifier.predict_proba(X_validation)[:, 1]
        test_probabilities = classifier.predict_proba(X_test)[:, 1]
        epochs_trained = None
    elif algorithm == "Random Forest":
        classifier = RandomForestClassifier(
            n_estimators=250,
            min_samples_leaf=2,
            class_weight=weights,
            n_jobs=-1,
            random_state=seed,
        )
        classifier.fit(X_train, y_train)
        validation_probabilities = classifier.predict_proba(X_validation)[:, 1]
        test_probabilities = classifier.predict_proba(X_test)[:, 1]
        epochs_trained = None
    elif algorithm == "ANN":
        tf.keras.backend.clear_session()
        classifier = build_ann(X_train.shape[1], seed)
        callback = tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=7, restore_best_weights=True
        )
        history = classifier.fit(
            X_train,
            y_train,
            validation_data=(X_validation, y_validation),
            epochs=60,
            batch_size=64,
            class_weight=weights,
            callbacks=[callback],
            verbose=0,
        )
        validation_probabilities = classifier.predict(X_validation, verbose=0).reshape(-1)
        test_probabilities = classifier.predict(X_test, verbose=0).reshape(-1)
        epochs_trained = len(history.history["loss"])
    else:
        raise ValueError(f"Unknown algorithm: {algorithm}")

    threshold = select_f1_threshold(y_validation, validation_probabilities)
    validation_result = binary_metrics(y_validation, validation_probabilities, threshold)
    test_result = binary_metrics(y_test, test_probabilities, threshold)
    numeric_pipeline = preprocessor.named_transformers_["numeric"]
    annual_median = None
    if "annual_area_change_km2_per_year" in features:
        annual_position = features.index("annual_area_change_km2_per_year")
        annual_median = float(numeric_pipeline.named_steps["imputer"].statistics_[annual_position])

    result = {
        **test_result,
        "validation_precision": validation_result["precision"],
        "validation_recall": validation_result["recall"],
        "validation_f1": validation_result["f1"],
        "validation_pr_auc": validation_result["pr_auc"],
        "validation_roc_auc": validation_result["roc_auc"],
        "train_rows": len(train),
        "validation_rows": len(validation),
        "test_rows": len(test),
        "train_positives": int(y_train.sum()),
        "validation_positives": int(y_validation.sum()),
        "test_positives": int(y_test.sum()),
        "class_weight_0": weights[0],
        "class_weight_1": weights[1],
        "annual_growth_training_median": annual_median,
        "epochs_trained": epochs_trained,
        "features": ";".join(features),
        "feature_count": len(features),
    }
    prediction_frame = pd.DataFrame(
        {
            "sample_id": test["sample_id"].to_numpy(),
            "y_true": y_test,
            "probability": test_probabilities,
            "prediction": (test_probabilities >= threshold).astype(int),
        }
    )
    return result, prediction_frame, preprocessor


def fit_dummy(
    features: list[str],
    train: pd.DataFrame,
    validation: pd.DataFrame,
    test: pd.DataFrame,
) -> tuple[dict[str, Any], pd.DataFrame, ColumnTransformer]:
    """Fit the required prior-probability DummyClassifier baseline."""
    preprocessor = build_preprocessor(features)
    X_train = preprocessor.fit_transform(train[features])
    X_validation = preprocessor.transform(validation[features])
    X_test = preprocessor.transform(test[features])
    y_train = train["label"].to_numpy(dtype=int)
    y_validation = validation["label"].to_numpy(dtype=int)
    y_test = test["label"].to_numpy(dtype=int)
    classifier = DummyClassifier(strategy="prior")
    classifier.fit(X_train, y_train)
    validation_probabilities = classifier.predict_proba(X_validation)[:, 1]
    test_probabilities = classifier.predict_proba(X_test)[:, 1]
    threshold = 0.5
    validation_result = binary_metrics(y_validation, validation_probabilities, threshold)
    result = {
        **binary_metrics(y_test, test_probabilities, threshold),
        "validation_precision": validation_result["precision"],
        "validation_recall": validation_result["recall"],
        "validation_f1": validation_result["f1"],
        "validation_pr_auc": validation_result["pr_auc"],
        "validation_roc_auc": validation_result["roc_auc"],
        "train_rows": len(train),
        "validation_rows": len(validation),
        "test_rows": len(test),
        "train_positives": int(y_train.sum()),
        "validation_positives": int(y_validation.sum()),
        "test_positives": int(y_test.sum()),
        "class_weight_0": None,
        "class_weight_1": None,
        "annual_growth_training_median": None,
        "epochs_trained": None,
        "features": ";".join(features),
        "feature_count": len(features),
    }
    predictions = pd.DataFrame(
        {
            "sample_id": test["sample_id"].to_numpy(),
            "y_true": y_test,
            "probability": test_probabilities,
            "prediction": (test_probabilities >= threshold).astype(int),
        }
    )
    return result, predictions, preprocessor


def variant_subset(frame: pd.DataFrame, variant: str) -> pd.DataFrame:
    """Return all rows except for Model D, which requires observed growth."""
    if variant == "Model D":
        return frame.loc[frame["annual_area_change_km2_per_year"].notna()].copy()
    return frame.copy()


def run_holdout_experiments(
    frame: pd.DataFrame,
    partition: pd.Series,
    seed: int = RANDOM_SEED,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, ColumnTransformer]]:
    """Run all requested models on one untouched geographic test partition."""
    metric_records: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    preprocessors: dict[str, ColumnTransformer] = {}

    for variant, features in MODEL_VARIANTS.items():
        subset = variant_subset(frame, variant)
        subset_partition = partition.loc[subset.index]
        train = subset.loc[subset_partition == "train"]
        validation = subset.loc[subset_partition == "validation"]
        test = subset.loc[subset_partition == "test"]
        for split_name, split_frame in {
            "train": train,
            "validation": validation,
            "test": test,
        }.items():
            if split_frame["label"].nunique() < 2:
                raise RuntimeError(f"{variant} {split_name} split does not contain both labels")

        for algorithm_index, algorithm in enumerate(ALGORITHMS):
            print(f"Training {variant} - {algorithm}")
            result, predictions, preprocessor = fit_experiment(
                algorithm,
                features,
                train,
                validation,
                test,
                seed + algorithm_index,
            )
            result.update(
                {
                    "variant": variant,
                    "variant_description": VARIANT_DESCRIPTIONS[variant],
                    "algorithm": algorithm,
                    "label_status": "experimental_proxy_unverified",
                }
            )
            predictions["variant"] = variant
            predictions["algorithm"] = algorithm
            metric_records.append(result)
            prediction_frames.append(predictions)
            preprocessors[f"{variant}|{algorithm}"] = preprocessor

    # One dummy baseline on the all-sample core feature set.
    subset = frame
    train = subset.loc[partition == "train"]
    validation = subset.loc[partition == "validation"]
    test = subset.loc[partition == "test"]
    result, predictions, preprocessor = fit_dummy(
        MODEL_VARIANTS["Model C"], train, validation, test
    )
    result.update(
        {
            "variant": "Model C",
            "variant_description": VARIANT_DESCRIPTIONS["Model C"],
            "algorithm": "Dummy",
            "label_status": "experimental_proxy_unverified",
        }
    )
    predictions["variant"] = "Model C"
    predictions["algorithm"] = "Dummy"
    metric_records.append(result)
    prediction_frames.append(predictions)
    preprocessors["Model C|Dummy"] = preprocessor

    metrics = pd.DataFrame(metric_records)
    predictions = pd.concat(prediction_frames, ignore_index=True)
    return metrics, predictions, preprocessors


def stability_fit_predict(
    algorithm: str,
    features: list[str],
    train: pd.DataFrame,
    test: pd.DataFrame,
    seed: int,
) -> np.ndarray:
    """Fit one outer geographic-CV fold and return test probabilities."""
    if algorithm == "ANN":
        positions = np.arange(len(train))
        inner_train_positions, validation_positions = choose_group_fold(
            positions,
            train["label"].to_numpy(dtype=int),
            train["geographic_group_025deg"].to_numpy(),
            3,
            seed + 100,
        )
        actual_train = train.iloc[inner_train_positions]
        validation = train.iloc[validation_positions]
    else:
        actual_train = train
        validation = None

    preprocessor = build_preprocessor(features)
    X_train = preprocessor.fit_transform(actual_train[features])
    X_test = preprocessor.transform(test[features])
    y_train = actual_train["label"].to_numpy(dtype=int)
    weights = class_weights_from_training(y_train)

    if algorithm == "Logistic Regression":
        classifier = LogisticRegression(
            class_weight=weights,
            max_iter=2500,
            solver="lbfgs",
            random_state=seed,
        )
        classifier.fit(X_train, y_train)
        return classifier.predict_proba(X_test)[:, 1]
    if algorithm == "Random Forest":
        classifier = RandomForestClassifier(
            n_estimators=180,
            min_samples_leaf=2,
            class_weight=weights,
            n_jobs=-1,
            random_state=seed,
        )
        classifier.fit(X_train, y_train)
        return classifier.predict_proba(X_test)[:, 1]
    if algorithm == "ANN":
        assert validation is not None
        X_validation = preprocessor.transform(validation[features])
        y_validation = validation["label"].to_numpy(dtype=int)
        tf.keras.backend.clear_session()
        classifier = build_ann(X_train.shape[1], seed)
        callback = tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=5, restore_best_weights=True
        )
        classifier.fit(
            X_train,
            y_train,
            validation_data=(X_validation, y_validation),
            epochs=45,
            batch_size=64,
            class_weight=weights,
            callbacks=[callback],
            verbose=0,
        )
        return classifier.predict(X_test, verbose=0).reshape(-1)
    raise ValueError(f"Unknown algorithm: {algorithm}")


def run_geographic_stability(
    frame: pd.DataFrame,
    seed: int = RANDOM_SEED,
    n_splits: int = 3,
) -> pd.DataFrame:
    """Measure threshold-free model stability across geographic folds."""
    records: list[dict[str, Any]] = []
    for variant, features in MODEL_VARIANTS.items():
        subset = variant_subset(frame, variant).reset_index(drop=True)
        splitter = StratifiedGroupKFold(
            n_splits=n_splits, shuffle=True, random_state=seed
        )
        for fold, (train_indices, test_indices) in enumerate(
            splitter.split(
                np.zeros(len(subset)),
                subset["label"],
                subset["geographic_group_025deg"],
            ),
            start=1,
        ):
            train = subset.iloc[train_indices]
            test = subset.iloc[test_indices]
            if train["label"].nunique() < 2 or test["label"].nunique() < 2:
                print(f"Skipping {variant} fold {fold}: only one label in a partition")
                continue
            for algorithm_index, algorithm in enumerate(ALGORITHMS):
                print(f"Stability {variant} fold {fold}/{n_splits} - {algorithm}")
                probabilities = stability_fit_predict(
                    algorithm,
                    features,
                    train,
                    test,
                    seed + fold * 10 + algorithm_index,
                )
                y_test = test["label"].to_numpy(dtype=int)
                records.append(
                    {
                        "variant": variant,
                        "algorithm": algorithm,
                        "fold": fold,
                        "rows": len(test),
                        "positives": int(y_test.sum()),
                        "pr_auc": float(average_precision_score(y_test, probabilities)),
                        "roc_auc": float(roc_auc_score(y_test, probabilities)),
                    }
                )
    return pd.DataFrame(records)


def add_stability_to_metrics(metrics: pd.DataFrame, stability: pd.DataFrame) -> pd.DataFrame:
    """Merge geographic-CV PR/ROC mean and variability into holdout metrics."""
    if stability.empty:
        return metrics
    aggregate = (
        stability.groupby(["variant", "algorithm"])
        .agg(
            geographic_cv_folds=("fold", "count"),
            geographic_cv_pr_auc_mean=("pr_auc", "mean"),
            geographic_cv_pr_auc_std=("pr_auc", "std"),
            geographic_cv_pr_auc_min=("pr_auc", "min"),
            geographic_cv_pr_auc_max=("pr_auc", "max"),
            geographic_cv_roc_auc_mean=("roc_auc", "mean"),
            geographic_cv_roc_auc_std=("roc_auc", "std"),
        )
        .reset_index()
    )
    return metrics.merge(aggregate, on=["variant", "algorithm"], how="left")


def save_confusion_plot(predictions: pd.DataFrame, output_path: Path) -> None:
    combinations = (
        predictions[["variant", "algorithm"]].drop_duplicates().sort_values(["variant", "algorithm"])
    )
    figure, axes = plt.subplots(4, 4, figsize=(14, 13))
    for axis, (_, item) in zip(axes.flat, combinations.iterrows()):
        subset = predictions.loc[
            (predictions["variant"] == item["variant"])
            & (predictions["algorithm"] == item["algorithm"])
        ]
        matrix = confusion_matrix(subset["y_true"], subset["prediction"], labels=[0, 1])
        sns.heatmap(matrix, annot=True, fmt="d", cmap="Blues", cbar=False, ax=axis)
        axis.set_title(f"{item['variant']} - {item['algorithm']}", fontsize=9)
        axis.set_xlabel("Predicted")
        axis.set_ylabel("Actual")
    for axis in axes.flat[len(combinations) :]:
        axis.axis("off")
    figure.suptitle("Geographic holdout confusion matrices (proxy labels)", fontsize=15)
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    figure.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(figure)


def save_precision_recall_plot(predictions: pd.DataFrame, output_path: Path) -> None:
    figure, axes = plt.subplots(2, 2, figsize=(13, 10))
    for axis, variant in zip(axes.flat, MODEL_VARIANTS):
        variant_data = predictions.loc[predictions["variant"] == variant]
        for algorithm, subset in variant_data.groupby("algorithm", sort=False):
            precision, recall, _ = precision_recall_curve(subset["y_true"], subset["probability"])
            score = average_precision_score(subset["y_true"], subset["probability"])
            axis.plot(recall, precision, label=f"{algorithm} (AP={score:.3f})")
        prevalence = variant_data.drop_duplicates("sample_id")["y_true"].mean()
        axis.axhline(prevalence, color="gray", linestyle="--", linewidth=1, label="Prevalence")
        axis.set_title(variant)
        axis.set_xlabel("Recall")
        axis.set_ylabel("Precision")
        axis.set_xlim(0, 1)
        axis.set_ylim(0, 1.02)
        axis.legend(fontsize=8)
        axis.grid(alpha=0.2)
    figure.suptitle("Precision-recall curves on disjoint geographic test groups", fontsize=15)
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    figure.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(figure)


def save_feature_distribution_plot(frame: pd.DataFrame, output_path: Path) -> None:
    features = [
        "area",
        "temperature",
        "rainfall",
        "elevation",
        "distance_to_nearest_settlement_km",
        "annual_area_change_km2_per_year",
        "longitude",
        "latitude",
        "growth_data_available",
    ]
    plotting = frame[[*features, "label"]].copy()
    plotting["log10_area"] = np.log10(plotting["area"].clip(lower=1e-10))
    display_features = ["log10_area", *features[1:]]
    figure, axes = plt.subplots(3, 3, figsize=(15, 12))
    for axis, feature in zip(axes.flat, display_features):
        sns.histplot(
            data=plotting,
            x=feature,
            hue="label",
            stat="density",
            common_norm=False,
            element="step",
            fill=False,
            bins=30,
            ax=axis,
        )
        axis.set_title(feature)
        axis.grid(alpha=0.15)
    figure.suptitle("Feature distributions by experimental proxy label", fontsize=15)
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    figure.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(figure)


def save_artifacts(
    frame: pd.DataFrame,
    partition: pd.Series,
    metrics: pd.DataFrame,
    predictions: pd.DataFrame,
    stability: pd.DataFrame,
    preprocessors: dict[str, ColumnTransformer],
    artifact_dir: Path = ARTIFACT_DIR,
) -> dict[str, Any]:
    """Save metrics, figures, split traceability, and one preprocessing object."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(ROOT / "model_metrics.csv", index=False)
    stability.to_csv(artifact_dir / "geographic_cv_stability.csv", index=False)
    predictions.to_csv(artifact_dir / "geographic_test_predictions.csv", index=False)

    split_assignments = frame[["sample_id", "geographic_group_025deg", "label"]].copy()
    split_assignments["partition"] = partition.to_numpy()
    split_assignments.to_csv(artifact_dir / "geographic_split_assignments.csv", index=False)

    save_confusion_plot(predictions, artifact_dir / "confusion_matrices.png")
    save_precision_recall_plot(predictions, artifact_dir / "precision_recall_curves.png")
    save_feature_distribution_plot(frame, artifact_dir / "feature_distributions.png")

    candidates = metrics.loc[metrics["algorithm"] != "Dummy"].copy()
    selected_row = candidates.sort_values(
        ["validation_pr_auc", "validation_recall", "validation_f1"], ascending=False
    ).iloc[0]
    selected_key = f"{selected_row['variant']}|{selected_row['algorithm']}"
    preprocessing_path = artifact_dir / "preprocessing_pipeline.joblib"
    joblib.dump(preprocessors[selected_key], preprocessing_path)
    metadata = {
        "selected_experimental_configuration": selected_key,
        "selection_basis": "validation PR-AUC, then validation recall and F1",
        "fit_scope": "training partition only",
        "features": selected_row["features"].split(";"),
        "label_status": "experimental_proxy_unverified",
        "classifier_saved": False,
        "classifier_not_saved_reason": (
            "The 53-row coordinate reference has no verifiable event IDs or upstream provenance; "
            "the 5 km expansion labels are experimental proxies."
        ),
    }
    (artifact_dir / "preprocessing_metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    return metadata


def run_workflow() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Run the complete notebook workflow."""
    set_global_seed()
    frame = prepare_model_ready()
    partition = make_master_split(frame)
    metrics, predictions, preprocessors = run_holdout_experiments(frame, partition)
    stability = run_geographic_stability(frame)
    metrics = add_stability_to_metrics(metrics, stability)
    metadata = save_artifacts(
        frame, partition, metrics, predictions, stability, preprocessors
    )
    return metrics, predictions, stability, metadata


if __name__ == "__main__":
    final_metrics, _, stability_records, preprocessing_metadata = run_workflow()
    print(final_metrics.to_string(index=False))
    print(f"stability_rows={len(stability_records)}")
    print(json.dumps(preprocessing_metadata, indent=2))
