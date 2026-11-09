
# =========================
# IMPORT LIBRARIES
# =========================

from datetime import datetime
from pathlib import Path
import os
import json

import joblib
import numpy as np
import pandas as pd

from flask import Flask, jsonify, request, Response


# =========================
# FILE PATHS
# =========================

BASE_DIR = Path(__file__).resolve().parent

MODEL_PATH = BASE_DIR / "artifacts" / "final_model.joblib"

DATA_PATH = (
    BASE_DIR
    / "data"
    / "final"
    / "glofguard_training_data.csv"
)

HTML_PATH = (
    BASE_DIR
    / "templates"
    / "GLOFGuard_Project.html"
)


# =========================
# DEFAULT MODEL FEATURES
# =========================

DEFAULT_FEATURES = [
    "area",
    "temperature",
    "rainfall",
    "elevation",
    "distance_to_nearest_settlement_km",
    "annual_area_change_km2_per_year",
    "growth_data_available",
]


# =========================
# RISK THRESHOLDS
# =========================

HIGH_PCT = 0.95
MED_PCT = 0.80


# =========================
# CREATE FLASK APP
# =========================

app = Flask(__name__)


# =========================
# LOAD ML MODEL
# =========================

def load_model():

    """Load the existing saved ML model."""

    if not MODEL_PATH.exists():

        raise FileNotFoundError(
            f"Model file not found: {MODEL_PATH}"
        )

    obj = joblib.load(MODEL_PATH)

    if isinstance(obj, dict):

        pipeline = obj["pipeline"]

        features = obj.get(
            "features",
            DEFAULT_FEATURES
        )

    else:

        pipeline = obj
        features = DEFAULT_FEATURES

    return pipeline, features


# Load model once when Flask starts
MODEL, FEATURES = load_model()


# =========================
# CLEAN VALUES
# =========================

def clean_number(value):

    if value is None or pd.isna(value):
        return None

    return float(value)


def normalize_id(value):

    """Normalize IDs so CSV and HTML IDs can be matched."""

    if value is None or pd.isna(value):
        return None

    try:
        return int(float(value))

    except (ValueError, TypeError):
        return str(value)


# =========================
# READ ORIGINAL UI DATA
# =========================

def get_original_ui_data():

    """
    Read the existing embedded data from HTML.

    This preserves the original dashboard data
    instead of creating new lake records.
    """

    if not HTML_PATH.exists():

        raise FileNotFoundError(
            f"HTML file not found: {HTML_PATH}"
        )

    html = HTML_PATH.read_text(
        encoding="utf-8"
    )

    start_marker = "const D="
    end_marker = ",APP=false;"

    start = html.find(start_marker)

    if start == -1:

        raise ValueError(
            "Could not find const D= in HTML."
        )

    start += len(start_marker)

    end = html.find(
        end_marker,
        start
    )

    if end == -1:

        raise ValueError(
            "Could not find ,APP=false; in HTML."
        )

    original_data = json.loads(
        html[start:end]
    )

    return html, original_data


# =========================
# BUILD DASHBOARD DATA
# =========================

def build_dashboard_data():

    """
    Generate new ML predictions while preserving
    the original dashboard records and alert logic.
    """

    if not DATA_PATH.exists():

        raise FileNotFoundError(
            f"Dataset not found: {DATA_PATH}"
        )

    # =========================
    # READ FINAL DATASET
    # =========================

    df = pd.read_csv(DATA_PATH).copy()

    # Check required model features
    missing = [
        column
        for column in FEATURES
        if column not in df.columns
    ]

    if missing:

        raise ValueError(
            f"Dataset is missing model features: {missing}"
        )

    # =========================
    # MAKE ML PREDICTIONS
    # =========================

    probabilities = MODEL.predict_proba(
        df[FEATURES]
    )[:, 1]

    df["score"] = probabilities

    # Calculate percentile
    df["rank_pct"] = df["score"].rank(pct=True)

    # Calculate ranking
    df["rank"] = (
        df["score"]
        .rank(
            ascending=False,
            method="first"
        )
        .astype(int)
    )

    # =========================
    # ASSIGN ML RISK BANDS
    # =========================

    df["band"] = np.where(
        df["rank_pct"] >= HIGH_PCT,
        "High",
        np.where(
            df["rank_pct"] >= MED_PCT,
            "Medium",
            "Low"
        )
    )

    # Original UI risk tier indexes
    band_index = {
        "Low": 0,
        "Medium": 1,
        "High": 2
    }

    # =========================
    # READ ORIGINAL DASHBOARD DATA
    # =========================

    _, original_data = get_original_ui_data()

    original_rows = original_data["L"]

    # Check record counts
    if len(df) != len(original_rows):

        raise ValueError(
            "The final CSV and original dashboard "
            "do not contain the same number of lakes. "
            f"CSV: {len(df)}, HTML: {len(original_rows)}"
        )

    # =========================
    # MATCH LAKES BY ID
    # =========================

    original_id_map = {}

    for original_row in original_rows:

        lake_id = normalize_id(original_row[0])

        if lake_id in original_id_map:

            raise ValueError(
                f"Duplicate lake ID in original HTML: {lake_id}"
            )

        original_id_map[lake_id] = original_row

    # Check CSV IDs
    if "sample_id" in df.columns:

        csv_ids = df["sample_id"].apply(normalize_id)

        if csv_ids.isna().any():

            raise ValueError(
                "Missing sample_id values found in CSV."
            )

        if csv_ids.duplicated().any():

            raise ValueError(
                "Duplicate sample_id values found in CSV."
            )

        # Check whether all CSV IDs exist in original HTML
        missing_ids = [
            lake_id
            for lake_id in csv_ids
            if lake_id not in original_id_map
        ]

        if missing_ids:

            raise ValueError(
                "Some CSV lake IDs were not found "
                f"in original HTML. Examples: {missing_ids[:5]}"
            )

        # =========================
        # UPDATE ML VALUES ONLY
        # =========================

        for position, (_, row) in enumerate(df.iterrows()):

            lake_id = csv_ids.iloc[position]

            original_row = original_id_map[lake_id]

            # 3 - ML risk score
            original_row[3] = round(
                float(row["score"]) * 100,
                1
            )

            # 4 - ML risk tier
            original_row[4] = band_index[
                row["band"]
            ]

            # 15 - ML percentile
            original_row[15] = round(
                float(row["rank_pct"]),
                4
            )

            # IMPORTANT:
            # Do NOT change original_row[5].
            # It contains the original alert classification.

    else:

        # If sample_id is unavailable,
        # use original CSV row order.
        # This requires both datasets to have identical ordering.

        for position, (_, row) in enumerate(df.iterrows()):

            original_row = original_rows[position]

            # 3 - ML risk score
            original_row[3] = round(
                float(row["score"]) * 100,
                1
            )

            # 4 - ML risk tier
            original_row[4] = band_index[
                row["band"]
            ]

            # 15 - ML percentile
            original_row[15] = round(
                float(row["rank_pct"]),
                4
            )

            # Keep original_row[5] unchanged.

    # =========================
    # FINAL DASHBOARD PAYLOAD
    # =========================

    # Preserve ALL original dashboard data.
    # Only ML fields inside the lake rows are updated.

    payload = original_data

    return payload, df


# =========================
# HOME PAGE
# =========================

@app.get("/")
def home():

    """
    Insert updated ML results into the original HTML.

    Existing HTML, CSS, and JavaScript remain unchanged.
    """

    try:

        # Read original HTML
        html, _ = get_original_ui_data()

        # Generate updated dashboard data
        payload, _ = build_dashboard_data()

        # Convert Python dictionary into JSON
        new_data = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":")
        )

        # Safely handle less-than characters
        new_data = new_data.replace(
            "<",
            "\\u003c"
        )

        # Find original embedded data
        start_marker = "const D="
        end_marker = ",APP=false;"

        start = html.find(start_marker)

        if start == -1:

            raise ValueError(
                "Could not find const D= in HTML."
            )

        start += len(start_marker)

        end = html.find(
            end_marker,
            start
        )

        if end == -1:

            raise ValueError(
                "Could not find ,APP=false; in HTML."
            )

        # Replace only the embedded data
        html = (
            html[:start]
            + new_data
            + html[end:]
        )

        return Response(
            html,
            mimetype="text/html"
        )

    except Exception as exc:

        return jsonify({
            "error": str(exc)
        }), 500


# =========================
# HEALTH CHECK API
# =========================

@app.get("/api/health")
def health():

    return jsonify({

        "status": "ok",

        "model_loaded": MODEL_PATH.exists(),

        "data_loaded": DATA_PATH.exists(),

        "html_loaded": HTML_PATH.exists(),

        "timestamp": datetime.now().isoformat(),

    })


# =========================
# DASHBOARD DATA API
# =========================

@app.get("/api/dashboard")
def dashboard():

    """
    Return updated ML predictions
    while preserving original dashboard alerts.
    """

    try:

        payload, _ = build_dashboard_data()

        return jsonify(payload)

    except Exception as exc:

        return jsonify({
            "error": str(exc)
        }), 500


# =========================
# ML PREDICTION API
# =========================

@app.post("/api/predict")
def predict():

    """
    Receive seven model features and
    return an ML risk prediction.
    """

    # Receive JSON
    data = request.get_json(silent=True)

    if not isinstance(data, dict):

        return jsonify({
            "error": "Send a JSON object containing the model features."
        }), 400

    # Check missing features
    missing = [
        feature
        for feature in FEATURES
        if feature not in data
    ]

    if missing:

        return jsonify({

            "error": "Missing required features.",

            "missing": missing,

            "required": FEATURES

        }), 400

    # =========================
    # MAKE ML PREDICTION
    # =========================

    try:

        input_df = pd.DataFrame([{
            feature: data[feature]
            for feature in FEATURES
        }])

        probability = float(
            MODEL.predict_proba(input_df)[0, 1]
        )

    except Exception as exc:

        return jsonify({
            "error": str(exc)
        }), 400

    # =========================
    # RETURN PREDICTION
    # =========================

    return jsonify({

        "risk_score": round(
            probability * 100,
            2
        ),

        "risk_probability": probability

    })


# =========================
# RUN FLASK APPLICATION
# =========================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=True
    )
