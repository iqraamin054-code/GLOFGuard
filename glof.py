# -*- coding: utf-8 -*-
"""Legacy tools for preparing the static Pakistan lake inventory.

This module is a local-Python replacement for the original Colab notebook.
Each expensive or interactive operation is exposed as a separate command; run
``py glof.py --help`` (or ``python glof.py --help``) to see the available
commands. The dated live-data system is available through
``python -m glofguard.cli``. Legacy labels are unverified proximity proxies and
must not be presented as flood predictions.
"""

from __future__ import annotations

import argparse
import importlib
import math
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence


BASE_DIR = Path(__file__).resolve().parent
ARCHIVE_PATH = BASE_DIR / "HKH-PK.zip"
EXTRACT_DIR = BASE_DIR / "HKH-PK"
SHAPEFILE_PATH = EXTRACT_DIR / "HKH-PK" / "HKH-PK.shp"
HAZARD_FILE = BASE_DIR / "pakistan_hazardous_lakes_unique.csv"
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"
CLIMATE_COLUMNS = ("avg_temp_c", "avg_precip_mm")
MODEL_FEATURES = ("area_km2", "avg_temp_c", "avg_precip_mm")


def require_package(module_name: str, install_name: str | None = None) -> Any:
    """Import a dependency and give a useful installation error if absent."""
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        package = install_name or module_name
        raise RuntimeError(
            f"Missing dependency '{package}'. Install project dependencies with "
            f"'{sys.executable} -m pip install -r requirements.txt'."
        ) from exc


def require_columns(frame: Any, required: Iterable[str], source: Path | str) -> None:
    """Raise a clear error when a dataframe does not contain required columns."""
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"{source} is missing required columns: {', '.join(missing)}")


def extract_shapefile(
    archive_path: Path = ARCHIVE_PATH,
    extract_dir: Path = EXTRACT_DIR,
) -> Path:
    """Extract the source archive if needed and return the shapefile path."""
    shapefile_path = extract_dir / "HKH-PK" / "HKH-PK.shp"
    if shapefile_path.exists():
        return shapefile_path
    if not archive_path.exists():
        raise FileNotFoundError(f"Source archive not found: {archive_path}")

    extract_root = extract_dir.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        # Refuse archive members that would escape the target directory.
        for member in archive.infolist():
            destination = (extract_dir / member.filename).resolve()
            if extract_root not in destination.parents and destination != extract_root:
                raise ValueError(f"Unsafe path in archive: {member.filename}")
        archive.extractall(extract_dir)

    if not shapefile_path.exists():
        raise FileNotFoundError(
            f"The archive was extracted, but {shapefile_path} was not found."
        )
    return shapefile_path


def load_lakes(shapefile_path: Path = SHAPEFILE_PATH) -> Any:
    """Load lake polygons and add area, latitude, and longitude fields."""
    gpd = require_package("geopandas")
    if not shapefile_path.exists():
        shapefile_path = extract_shapefile()

    lakes = gpd.read_file(shapefile_path)
    if lakes.empty:
        raise ValueError(f"No lake features were found in {shapefile_path}")
    if lakes.crs is None:
        raise ValueError(f"The shapefile has no coordinate reference system: {shapefile_path}")

    lakes = lakes.copy()
    if "Shape_Area" in lakes.columns:
        lakes["area_km2"] = lakes["Shape_Area"] / 1_000_000.0
    elif lakes.crs.is_projected:
        lakes["area_km2"] = lakes.geometry.area / 1_000_000.0
    else:
        equal_area = lakes.to_crs(epsg=6933)
        lakes["area_km2"] = equal_area.geometry.area / 1_000_000.0

    # Calculate centroids in the source projected CRS, then transform the
    # points. Calculating centroids after converting polygons to WGS84 is
    # inaccurate and produces a GeoPandas warning.
    if lakes.crs.is_projected:
        centroids = gpd.GeoSeries(lakes.geometry.centroid, crs=lakes.crs)
    else:
        projected = lakes.to_crs(epsg=6933)
        centroids = gpd.GeoSeries(projected.geometry.centroid, crs=projected.crs)
    centroids_wgs84 = centroids.to_crs(epsg=4326)
    lakes["longitude"] = centroids_wgs84.x.to_numpy()
    lakes["latitude"] = centroids_wgs84.y.to_numpy()
    return lakes


def prepare_lakes(
    min_area_km2: float = 0.01,
    top_n: int = 200,
    raw_output: Path = BASE_DIR / "lakes_raw.csv",
    all_output: Path = BASE_DIR / "all_pakistan_lakes.csv",
) -> tuple[Any, Any]:
    """Prepare the top-lake CSV and a CSV containing every source lake."""
    if min_area_km2 < 0:
        raise ValueError("min_area_km2 must be non-negative")
    if top_n < 1:
        raise ValueError("top_n must be at least 1")

    lakes = load_lakes()
    eligible = lakes.loc[lakes["area_km2"] >= min_area_km2].copy()
    top_lakes = (
        eligible.nlargest(top_n, "area_km2")
        .loc[:, ["latitude", "longitude", "area_km2"]]
        .reset_index(drop=True)
    )
    if top_lakes.empty:
        raise ValueError("No lakes remain after applying the minimum-area filter")

    top_threshold = lakes["area_km2"].nlargest(min(top_n, len(lakes))).min()
    lakes["is_top200"] = lakes["area_km2"] >= top_threshold
    all_lakes = lakes.drop(columns="geometry", errors="ignore")

    raw_output.parent.mkdir(parents=True, exist_ok=True)
    all_output.parent.mkdir(parents=True, exist_ok=True)
    top_lakes.to_csv(raw_output, index=False)
    all_lakes.to_csv(all_output, index=False)
    print(f"Prepared {len(top_lakes)} top lakes in {raw_output}")
    print(f"Prepared {len(all_lakes)} total lakes in {all_output}")
    return top_lakes, all_lakes


def fetch_climate_point(
    latitude: float,
    longitude: float,
    *,
    timeout: float = 30.0,
    retries: int = 3,
) -> tuple[float, float]:
    """Fetch annual temperature and precipitation from NASA POWER."""
    requests = require_package("requests")
    params = {
        "parameters": "T2M,PRECTOTCORR",
        "community": "AG",
        "longitude": float(longitude),
        "latitude": float(latitude),
        "format": "JSON",
    }
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(NASA_POWER_URL, params=params, timeout=timeout)
            response.raise_for_status()
            parameter = response.json()["properties"]["parameter"]
            temperature = float(parameter["T2M"]["ANN"])
            precipitation = float(parameter["PRECTOTCORR"]["ANN"])
            if temperature <= -999 or precipitation <= -999:
                raise ValueError("NASA POWER returned a missing-data sentinel")
            return temperature, precipitation
        except (
            requests.exceptions.RequestException,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 ** (attempt - 1))

    raise RuntimeError(
        f"Climate lookup failed for ({latitude:.6f}, {longitude:.6f}) "
        f"after {retries} attempts: {last_error}"
    ) from last_error


def enrich_climate_csv(
    input_path: Path,
    output_path: Path,
    *,
    delay: float = 0.3,
    checkpoint_every: int = 25,
) -> Any:
    """Add NASA POWER climate fields to a lake CSV, with resumable output."""
    pd = require_package("pandas")
    if delay < 0:
        raise ValueError("delay must be non-negative")
    if checkpoint_every < 1:
        raise ValueError("checkpoint_every must be at least 1")
    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    source = pd.read_csv(input_path)
    require_columns(source, ("latitude", "longitude"), input_path)

    # Continue from a compatible prior output instead of repeating successful
    # network calls.
    frame = source.copy()
    if output_path.exists():
        previous = pd.read_csv(output_path)
        compatible = (
            len(previous) == len(source)
            and {"latitude", "longitude", *CLIMATE_COLUMNS}.issubset(previous.columns)
            and previous[["latitude", "longitude"]]
            .reset_index(drop=True)
            .equals(source[["latitude", "longitude"]].reset_index(drop=True))
        )
        if compatible:
            frame = previous

    for column in CLIMATE_COLUMNS:
        if column not in frame.columns:
            frame[column] = math.nan

    missing = frame[list(CLIMATE_COLUMNS)].isna().any(axis=1)
    pending_indices = frame.index[missing].tolist()
    total = len(pending_indices)
    failures: list[int] = []

    for position, index in enumerate(pending_indices, start=1):
        row = frame.loc[index]
        try:
            temperature, precipitation = fetch_climate_point(
                row["latitude"], row["longitude"]
            )
            frame.loc[index, "avg_temp_c"] = temperature
            frame.loc[index, "avg_precip_mm"] = precipitation
        except RuntimeError as exc:
            failures.append(int(index))
            print(f"Warning: row {index} was skipped: {exc}", file=sys.stderr)

        if position % checkpoint_every == 0 or position == total:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(output_path, index=False)
            print(f"Climate progress: {position}/{total}")
        if delay and position < total:
            time.sleep(delay)

    # The file still needs to be created when no rows were pending.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)
    print(f"Wrote {len(frame)} rows to {output_path}")
    if failures:
        print(
            f"{len(failures)} rows still lack climate data; rerun the command to retry them.",
            file=sys.stderr,
        )
    return frame


def haversine_distances_km(
    latitude: float,
    longitude: float,
    other_latitudes: Any,
    other_longitudes: Any,
) -> Any:
    """Return vectorized great-circle distances from one point."""
    np = require_package("numpy")
    lat1 = np.radians(float(latitude))
    lon1 = np.radians(float(longitude))
    lat2 = np.radians(other_latitudes.astype(float))
    lon2 = np.radians(other_longitudes.astype(float))
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1
    value = np.sin(d_lat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(d_lon / 2) ** 2
    value = np.clip(value, 0.0, 1.0)
    return 6371.0088 * 2 * np.arctan2(np.sqrt(value), np.sqrt(1 - value))


def reverse_geocode_names(frame: Any, delay: float = 1.0) -> list[str]:
    """Look up approximate place names with Nominatim."""
    geocoders = require_package("geopy.geocoders", "geopy")
    geolocator = geocoders.Nominatim(user_agent="glof_project_pakistan")
    names: list[str] = []

    for position, (index, row) in enumerate(frame.iterrows(), start=1):
        fallback = f"Lake {index}"
        try:
            location = geolocator.reverse(
                (row["latitude"], row["longitude"]),
                language="en",
                zoom=10,
                timeout=15,
            )
            name = location.address.split(",", maxsplit=1)[0] if location else fallback
        except Exception as exc:  # geopy adapters can expose different exception types
            print(f"Warning: name lookup failed for row {index}: {exc}", file=sys.stderr)
            name = fallback
        names.append(name)
        if delay and position < len(frame):
            time.sleep(delay)
    return names


def label_lakes_csv(
    input_path: Path,
    hazard_path: Path,
    output_path: Path,
    *,
    radius_km: float = 5.0,
    add_names: bool = False,
) -> Any:
    """Label lakes that lie near a known hazardous-lake coordinate."""
    pd = require_package("pandas")
    if radius_km < 0:
        raise ValueError("radius_km must be non-negative")
    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")
    if not hazard_path.exists():
        raise FileNotFoundError(f"Hazard CSV not found: {hazard_path}")

    lakes = pd.read_csv(input_path)
    hazards = pd.read_csv(hazard_path)
    require_columns(lakes, ("latitude", "longitude"), input_path)
    require_columns(hazards, ("Lat_lake", "Lon_lake"), hazard_path)
    hazards = hazards.dropna(subset=["Lat_lake", "Lon_lake"])
    if hazards.empty:
        raise ValueError(f"No valid hazardous-lake coordinates were found in {hazard_path}")

    labels: list[int] = []
    hazard_lats = hazards["Lat_lake"].to_numpy()
    hazard_lons = hazards["Lon_lake"].to_numpy()
    for _, row in lakes.iterrows():
        distances = haversine_distances_km(
            row["latitude"], row["longitude"], hazard_lats, hazard_lons
        )
        labels.append(int(distances.min() <= radius_km))

    lakes["label"] = labels
    if add_names:
        lakes["lake_name"] = reverse_geocode_names(lakes)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lakes.to_csv(output_path, index=False)
    print(f"Wrote {len(lakes)} lakes ({sum(labels)} hazardous) to {output_path}")
    return lakes


def build_model(keras: Any, input_dim: int) -> Any:
    """Build the binary GLOF risk classifier."""
    model = keras.Sequential(
        [
            keras.layers.Input(shape=(input_dim,)),
            keras.layers.Dense(16, activation="relu"),
            keras.layers.Dropout(0.3),
            keras.layers.Dense(8, activation="relu"),
            keras.layers.Dropout(0.2),
            keras.layers.Dense(1, activation="sigmoid"),
        ]
    )
    model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
    return model


def train_risk_model(
    dataset_path: Path,
    model_output: Path,
    scaler_output: Path,
    *,
    epochs: int = 30,
    folds: int = 5,
    allow_unverified_proxy_experiment: bool = False,
) -> tuple[Any, Any]:
    """Run the legacy proxy-label experiment only after explicit acknowledgement."""
    if not allow_unverified_proxy_experiment:
        raise RuntimeError(
            "Training is disabled by default: the legacy labels are unverified 5 km "
            "proximity proxies and the random folds are not suitable for deployment. "
            "Use glof_experiment.py for the audited geographic proxy experiment, or "
            "construct verified dated labels and time/geographic validation first."
        )
    np = require_package("numpy")
    pd = require_package("pandas")
    joblib = require_package("joblib")
    keras = require_package("tensorflow.keras", "tensorflow")
    model_selection = require_package("sklearn.model_selection", "scikit-learn")
    preprocessing = require_package("sklearn.preprocessing", "scikit-learn")
    metrics = require_package("sklearn.metrics", "scikit-learn")
    class_weight_module = require_package("sklearn.utils.class_weight", "scikit-learn")

    if epochs < 1:
        raise ValueError("epochs must be at least 1")
    if folds < 2:
        raise ValueError("folds must be at least 2")
    if not dataset_path.exists():
        raise FileNotFoundError(f"Training dataset not found: {dataset_path}")

    frame = pd.read_csv(dataset_path)
    require_columns(frame, (*MODEL_FEATURES, "label"), dataset_path)
    numeric = frame.loc[:, [*MODEL_FEATURES, "label"]].apply(pd.to_numeric, errors="coerce")
    invalid_rows = int(numeric.isna().any(axis=1).sum())
    if invalid_rows:
        print(f"Dropping {invalid_rows} rows with missing/non-numeric training values")
    numeric = numeric.dropna()
    if numeric.empty:
        raise ValueError("No valid rows remain in the training dataset")

    X = numeric.loc[:, list(MODEL_FEATURES)].to_numpy(dtype=float)
    y = numeric["label"].to_numpy(dtype=int)
    classes, counts = np.unique(y, return_counts=True)
    if set(classes.tolist()) != {0, 1}:
        raise ValueError("Training labels must contain both 0 and 1")
    n_splits = min(folds, int(counts.min()))
    if n_splits < 2:
        raise ValueError("Each label class needs at least two samples for validation")

    class_values = class_weight_module.compute_class_weight(
        class_weight="balanced", classes=classes, y=y
    )
    class_weights = {int(key): float(value) for key, value in zip(classes, class_values)}
    splitter = model_selection.StratifiedKFold(
        n_splits=n_splits, shuffle=True, random_state=42
    )

    for fold, (train_indices, validation_indices) in enumerate(splitter.split(X, y), start=1):
        fold_scaler = preprocessing.StandardScaler()
        X_train = fold_scaler.fit_transform(X[train_indices])
        X_validation = fold_scaler.transform(X[validation_indices])
        model = build_model(keras, X.shape[1])
        model.fit(
            X_train,
            y[train_indices],
            epochs=epochs,
            batch_size=32,
            verbose=0,
            class_weight=class_weights,
        )
        probabilities = model.predict(X_validation, verbose=0).reshape(-1)
        predictions = (probabilities > 0.5).astype(int)
        print(f"--- Fold {fold}/{n_splits} ---")
        print(
            metrics.classification_report(
                y[validation_indices], predictions, zero_division=0
            )
        )

    scaler = preprocessing.StandardScaler()
    X_scaled = scaler.fit_transform(X)
    final_model = build_model(keras, X.shape[1])
    final_model.fit(
        X_scaled,
        y,
        epochs=epochs,
        batch_size=32,
        verbose=1,
        class_weight=class_weights,
    )

    model_output.parent.mkdir(parents=True, exist_ok=True)
    scaler_output.parent.mkdir(parents=True, exist_ok=True)
    final_model.save(model_output)
    joblib.dump(scaler, scaler_output)
    print(f"Saved model to {model_output}")
    print(f"Saved feature scaler to {scaler_output}")
    return final_model, scaler


def initialize_earth_engine(project: str, authenticate: bool = False) -> Any:
    """Initialize Earth Engine and optionally start its authentication flow."""
    ee = require_package("ee", "earthengine-api")
    if authenticate:
        ee.Authenticate()
    try:
        ee.Initialize(project=project)
    except Exception as exc:
        raise RuntimeError(
            "Earth Engine initialization failed. Authenticate once with "
            f"'{sys.executable} -m ee.cli.eecli authenticate', then verify the project ID."
        ) from exc
    return ee


def first_sentinel_image(
    ee: Any,
    point: Any,
    start_date: str,
    end_date: str,
) -> Any:
    """Return the least-cloudy Sentinel-2 image for a location/date range."""
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(point)
        .filterDate(start_date, end_date)
        .sort("CLOUDY_PIXEL_PERCENTAGE")
    )
    if int(collection.size().getInfo()) == 0:
        raise ValueError(
            f"No Sentinel-2 imagery found from {start_date} to {end_date}"
        )
    return collection.first()


def lake_area_km2(image: Any, region: Any, ee: Any, threshold: float = 0.1) -> float:
    """Estimate water area using MNDWI (green versus SWIR1)."""
    water_mask = image.normalizedDifference(["B3", "B11"]).gt(threshold)
    area_image = water_mask.multiply(ee.Image.pixelArea())
    result = area_image.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=region,
        scale=10,
        maxPixels=1_000_000_000,
    ).getInfo()
    area_m2 = result.get("nd")
    return 0.0 if area_m2 is None else float(area_m2) / 1_000_000.0


def assess_present_lakes(
    hazard_path: Path,
    output_path: Path,
    history_path: Path,
    *,
    project: str,
    start_date: str,
    end_date: str,
    authenticate: bool = False,
) -> Any:
    """Estimate current areas for all known hazardous lakes with Earth Engine."""
    pd = require_package("pandas")
    try:
        parsed_start = date.fromisoformat(start_date)
        parsed_end = date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError("Earth Engine dates must use YYYY-MM-DD format") from exc
    if parsed_start >= parsed_end:
        raise ValueError("start_date must be earlier than end_date")
    ee = initialize_earth_engine(project, authenticate=authenticate)
    if not hazard_path.exists():
        raise FileNotFoundError(f"Hazard CSV not found: {hazard_path}")
    lakes = pd.read_csv(hazard_path)
    require_columns(
        lakes,
        ("Lake_name", "Glacier_name", "Lat_lake", "Lon_lake"),
        hazard_path,
    )

    results: list[dict[str, Any]] = []
    for _, row in lakes.iterrows():
        name = row["Lake_name"]
        try:
            point = ee.Geometry.Point([float(row["Lon_lake"]), float(row["Lat_lake"])])
            lake_box = point.buffer(400).bounds()
            image = first_sentinel_image(ee, point, start_date, end_date)
            area = lake_area_km2(image, lake_box, ee)
            error = ""
        except Exception as exc:
            area = math.nan
            error = str(exc)
            print(f"Warning: {name}: {exc}", file=sys.stderr)
        results.append(
            {
                "lake": name,
                "glacier": row["Glacier_name"],
                "area_km2": area,
                "date_checked": date.today().isoformat(),
                "error": error,
            }
        )

    present = pd.DataFrame(results)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    present.to_csv(output_path, index=False)
    present.to_csv(
        history_path,
        mode="a",
        header=not history_path.exists() or history_path.stat().st_size == 0,
        index=False,
    )
    print(f"Wrote present-area estimates to {output_path}")
    print(f"Appended the same results to {history_path}")
    return present


def parse_path(value: str) -> Path:
    """Resolve command-line paths relative to the current directory."""
    return Path(value).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    prepare_parser = subparsers.add_parser(
        "prepare", help="extract the shapefile and create lake CSV files"
    )
    prepare_parser.add_argument("--min-area", type=float, default=0.01)
    prepare_parser.add_argument("--top-n", type=int, default=200)
    prepare_parser.add_argument("--raw-output", type=parse_path, default=BASE_DIR / "lakes_raw.csv")
    prepare_parser.add_argument(
        "--all-output", type=parse_path, default=BASE_DIR / "all_pakistan_lakes.csv"
    )

    climate_parser = subparsers.add_parser(
        "climate", help="add NASA POWER climate data to a lake CSV"
    )
    climate_parser.add_argument(
        "--input", type=parse_path, default=BASE_DIR / "lakes_raw.csv"
    )
    climate_parser.add_argument(
        "--output", type=parse_path, default=BASE_DIR / "lakes_with_climate.csv"
    )
    climate_parser.add_argument("--delay", type=float, default=0.3)
    climate_parser.add_argument("--checkpoint-every", type=int, default=25)

    label_parser = subparsers.add_parser(
        "label", help="label lakes near known hazardous coordinates"
    )
    label_parser.add_argument(
        "--input", type=parse_path, default=BASE_DIR / "lakes_with_climate.csv"
    )
    label_parser.add_argument("--hazards", type=parse_path, default=HAZARD_FILE)
    label_parser.add_argument(
        "--output", type=parse_path, default=BASE_DIR / "lakes_final_labeled_v2.csv"
    )
    label_parser.add_argument("--radius-km", type=float, default=5.0)
    label_parser.add_argument(
        "--add-names",
        action="store_true",
        help="reverse-geocode approximate names (slow and network-dependent)",
    )

    train_parser = subparsers.add_parser(
        "train", help="legacy unverified proxy-label experiment (not deployable)"
    )
    train_parser.add_argument(
        "--dataset",
        type=parse_path,
        default=BASE_DIR / "pakistan_full_training_dataset.csv",
    )
    train_parser.add_argument(
        "--model-output", type=parse_path, default=BASE_DIR / "glof_risk_model.keras"
    )
    train_parser.add_argument(
        "--scaler-output", type=parse_path, default=BASE_DIR / "feature_scaler.pkl"
    )
    train_parser.add_argument("--epochs", type=int, default=30)
    train_parser.add_argument("--folds", type=int, default=5)
    train_parser.add_argument(
        "--allow-unverified-proxy-experiment",
        action="store_true",
        help="acknowledge that labels/folds are experimental and not a live risk model",
    )

    earth_parser = subparsers.add_parser(
        "earth-engine", help="estimate recent hazardous-lake areas"
    )
    earth_parser.add_argument("--project", required=True, help="Google Cloud project ID")
    earth_parser.add_argument("--hazards", type=parse_path, default=HAZARD_FILE)
    earth_parser.add_argument(
        "--output", type=parse_path, default=BASE_DIR / "lakes_present_area.csv"
    )
    earth_parser.add_argument(
        "--history", type=parse_path, default=BASE_DIR / "lakes_history.csv"
    )
    default_end = date.today()
    default_start = default_end - timedelta(days=90)
    earth_parser.add_argument("--start-date", default=default_start.isoformat())
    earth_parser.add_argument("--end-date", default=default_end.isoformat())
    earth_parser.add_argument(
        "--authenticate",
        action="store_true",
        help="start Earth Engine authentication before initialization",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested pipeline stage."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        if args.command == "prepare":
            prepare_lakes(args.min_area, args.top_n, args.raw_output, args.all_output)
        elif args.command == "climate":
            enrich_climate_csv(
                args.input,
                args.output,
                delay=args.delay,
                checkpoint_every=args.checkpoint_every,
            )
        elif args.command == "label":
            label_lakes_csv(
                args.input,
                args.hazards,
                args.output,
                radius_km=args.radius_km,
                add_names=args.add_names,
            )
        elif args.command == "train":
            train_risk_model(
                args.dataset,
                args.model_output,
                args.scaler_output,
                epochs=args.epochs,
                folds=args.folds,
                allow_unverified_proxy_experiment=args.allow_unverified_proxy_experiment,
            )
        elif args.command == "earth-engine":
            assess_present_lakes(
                args.hazards,
                args.output,
                args.history,
                project=args.project,
                start_date=args.start_date,
                end_date=args.end_date,
                authenticate=args.authenticate,
            )
    except (FileNotFoundError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
