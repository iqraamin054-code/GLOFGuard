"""Separated baseline susceptibility and dynamic environmental indicators."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .schemas import TimeSeriesRecord
from .types import IndicatorResult, Lake


def level_for_score(score: float) -> str:
    if score < 33.0:
        return "Low"
    if score < 67.0:
        return "Medium"
    return "High"


class BaselineSusceptibilityIndex:
    """Label-free monitoring-priority index from static lake characteristics.

    It is not an event probability. Values are percentiles relative to the
    supplied lake inventory. The output helps prioritize monitoring only.
    """

    WEIGHTS = {
        "reference_area_km2": 0.50,
        "elevation_m": 0.20,
        "settlement_proximity": 0.30,
    }

    def __init__(self, lakes: list[Lake]) -> None:
        self.reference: dict[str, list[float]] = {
            "reference_area_km2": sorted(
                float(lake.reference_area_km2)
                for lake in lakes
                if lake.reference_area_km2 is not None
            ),
            "elevation_m": sorted(
                float(lake.elevation_m)
                for lake in lakes
                if lake.elevation_m is not None
            ),
            "distance_to_nearest_settlement_km": sorted(
                float(lake.distance_to_nearest_settlement_km)
                for lake in lakes
                if lake.distance_to_nearest_settlement_km is not None
            ),
        }

    @staticmethod
    def _percentile(values: list[float], value: float) -> float:
        if not values:
            return 0.5
        return bisect.bisect_right(values, value) / len(values)

    def score(self, lake: Lake) -> tuple[float | None, str | None, list[str]]:
        components: list[tuple[float, float]] = []
        missing: list[str] = []
        if lake.reference_area_km2 is None:
            missing.append("reference lake area")
        else:
            components.append(
                (
                    self.WEIGHTS["reference_area_km2"],
                    self._percentile(
                        self.reference["reference_area_km2"], lake.reference_area_km2
                    ),
                )
            )
        if lake.elevation_m is None:
            missing.append("elevation")
        else:
            components.append(
                (
                    self.WEIGHTS["elevation_m"],
                    self._percentile(self.reference["elevation_m"], lake.elevation_m),
                )
            )
        if lake.distance_to_nearest_settlement_km is None:
            missing.append("settlement distance")
        else:
            distance_percentile = self._percentile(
                self.reference["distance_to_nearest_settlement_km"],
                lake.distance_to_nearest_settlement_km,
            )
            components.append(
                (self.WEIGHTS["settlement_proximity"], 1.0 - distance_percentile)
            )
        total_weight = sum(weight for weight, _ in components)
        if total_weight == 0:
            return None, None, missing
        score = 100.0 * sum(weight * value for weight, value in components) / total_weight
        return round(score, 2), level_for_score(score), missing


@dataclass(frozen=True)
class FactorRule:
    field: str
    label: str
    weight: float
    stress_at: float
    absolute: bool = False


class DynamicEnvironmentalIndex:
    """Transparent conditions index, never a probability of a GLOF event."""

    RULES = (
        FactorRule("area_change_30d", "30-day lake-area increase", 0.18, 20.0),
        FactorRule("area_change_90d", "90-day lake-area increase", 0.08, 40.0),
        FactorRule("temperature_anomaly", "temperature anomaly", 0.12, 8.0),
        FactorRule("rainfall_anomaly", "rainfall anomaly", 0.10, 150.0),
        FactorRule("rainfall_last_24h", "rainfall in last 24 hours", 0.14, 100.0),
        FactorRule("rainfall_last_7d", "rainfall in last 7 days", 0.14, 300.0),
        FactorRule(
            "forecast_rainfall_next_72h", "forecast rainfall in next 72 hours", 0.24, 150.0
        ),
    )

    def __init__(self, model_version: str = "transparent-conditions-index-v0.1.0") -> None:
        self.model_version = model_version

    def score(
        self,
        record: TimeSeriesRecord,
        *,
        baseline: tuple[float | None, str | None, list[str]],
        freshness_penalty: float,
        source_mode: str,
    ) -> IndicatorResult:
        available: list[tuple[FactorRule, float, float]] = []
        missing: list[str] = []
        for rule in self.RULES:
            raw = getattr(record, rule.field)
            if raw is None or not math.isfinite(float(raw)):
                missing.append(rule.label)
                continue
            positive_value = abs(float(raw)) if rule.absolute else max(0.0, float(raw))
            normalized = min(1.0, positive_value / rule.stress_at)
            available.append((rule, float(raw), normalized))
        available_weight = sum(rule.weight for rule, _, _ in available)
        baseline_score, baseline_level, baseline_missing = baseline
        if available_weight == 0:
            warning = "Dynamic indicator unavailable: no recent environmental features."
            return IndicatorResult(
                baseline_susceptibility_score=baseline_score,
                baseline_susceptibility_level=baseline_level,
                risk_score=None,
                risk_level=None,
                confidence_level="Low",
                uncertainty_score=1.0,
                influential_factors=[],
                model_version=self.model_version,
                data_quality_warning=warning,
                model_available=False,
            )
        score = 100.0 * sum(
            rule.weight * normalized for rule, _, normalized in available
        ) / available_weight
        contributions = sorted(
            (
                {
                    "factor": rule.label,
                    "value": round(raw, 3),
                    "contribution": round(rule.weight * normalized, 4),
                }
                for rule, raw, normalized in available
            ),
            key=lambda item: item["contribution"],
            reverse=True,
        )[:3]
        completeness = available_weight / sum(rule.weight for rule in self.RULES)
        uncertainty = min(1.0, 1.0 - completeness + freshness_penalty)
        confidence = "High" if uncertainty <= 0.20 else "Medium" if uncertainty <= 0.45 else "Low"
        warnings: list[str] = []
        if missing:
            warnings.append("Missing dynamic inputs: " + ", ".join(missing))
        if baseline_missing:
            warnings.append("Missing baseline inputs: " + ", ".join(baseline_missing))
        if freshness_penalty > 0:
            warnings.append("One or more environmental sources are stale")
        if source_mode.upper() == "MOCK":
            confidence = "Low"
            uncertainty = 1.0
            warnings.append("MOCK DATA - result is for interface testing, not an observation")
        warnings.append(
            "Transparent research index; not calibrated as a flood-event probability"
        )
        return IndicatorResult(
            baseline_susceptibility_score=baseline_score,
            baseline_susceptibility_level=baseline_level,
            risk_score=round(score, 2),
            risk_level=level_for_score(score),
            confidence_level=confidence,
            uncertainty_score=round(uncertainty, 3),
            influential_factors=contributions,
            model_version=self.model_version,
            data_quality_warning="; ".join(warnings),
            model_available=True,
        )


class SavedValidatedModel:
    """Optional adapter for an explicitly validated saved preprocessor/model pair."""

    def __init__(self, preprocessor_path: Path, model_path: Path, version: str) -> None:
        import joblib

        if not preprocessor_path.exists() or not model_path.exists():
            raise FileNotFoundError("Both validated preprocessor and model artifacts are required")
        self.preprocessor = joblib.load(preprocessor_path)
        if model_path.suffix.lower() in {".keras", ".h5", ".hdf5"}:
            try:
                from tensorflow import keras
            except ImportError as exc:
                raise RuntimeError("TensorFlow is required to load a Keras model") from exc
            self.model = keras.models.load_model(model_path)
        else:
            self.model = joblib.load(model_path)
        self.version = version

    def probability(self, features: Mapping[str, Any]) -> float:
        import pandas as pd

        transformed = self.preprocessor.transform(pd.DataFrame([dict(features)]))
        if hasattr(self.model, "predict_proba"):
            return float(self.model.predict_proba(transformed)[0, 1])
        prediction = self.model.predict(transformed)
        return float(prediction[0][0] if getattr(prediction, "ndim", 1) > 1 else prediction[0])

    def influential_factors(self, features: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Return local linear contributions when the saved model exposes them."""
        import numpy as np
        import pandas as pd

        transformed = self.preprocessor.transform(pd.DataFrame([dict(features)]))
        values = transformed.toarray()[0] if hasattr(transformed, "toarray") else np.asarray(transformed)[0]
        if not hasattr(self.model, "coef_"):
            return []
        coefficients = np.asarray(self.model.coef_)[0]
        try:
            names = list(self.preprocessor.get_feature_names_out())
        except Exception:
            names = [f"transformed_feature_{index}" for index in range(len(values))]
        contributions = coefficients * values
        order = np.argsort(np.abs(contributions))[::-1][:3]
        return [
            {
                "factor": str(names[index]),
                "contribution": round(float(contributions[index]), 5),
                "explanation": "local linear log-odds contribution",
            }
            for index in order
        ]
