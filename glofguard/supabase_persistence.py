"""Server-only Supabase persistence with a SQLite-backed durable outbox.

This module deliberately uses the Supabase Data API with an ``sb_secret_`` key
only from Python. Browser configuration is never read here. Local SQLite remains
the source of truth; an outbox item is marked remote-saved only after every
idempotent remote write in its bundle completes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import PROJECT_ROOT, load_env_file
from .security import redact_sensitive_text
from .storage import Repository, SyncOutboxItem


REMOTE_SAVED = "REMOTE_SAVED"
REMOTE_PENDING = "REMOTE_PENDING"
REMOTE_FAILED = "REMOTE_FAILED"
LOCAL_SAVED = "LOCAL_SAVED"


class SupabaseConfigurationError(RuntimeError):
    """Raised before network activity when the server-only setup is invalid."""


class SupabaseRemoteError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class SupabaseAuthenticationError(SupabaseRemoteError):
    def __init__(self, message: str = "Supabase authentication failed", *, status_code: int = 401) -> None:
        super().__init__(message, status_code=status_code, retryable=False)


class SupabaseNetworkError(SupabaseRemoteError):
    def __init__(self, message: str = "Supabase network request failed") -> None:
        super().__init__(message, retryable=True)


@dataclass(frozen=True)
class SupabaseConfig:
    """Validated, server-only Supabase configuration with a redacted repr."""

    url: str
    secret_key: str = field(repr=False)
    expected_project_ref: str | None = None
    timeout_seconds: float = 20.0
    retry_attempts: int = 3
    backoff_base_seconds: float = 0.5
    backoff_cap_seconds: float = 4.0
    batch_size: int = 25

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        # This deployment uses a hosted project URL, not an arbitrary API host.
        # Reject embedded credentials and routing components before any request.
        if (
            parsed.scheme != "https"
            or not re.fullmatch(r"[a-z0-9-]+\.supabase\.co", parsed.netloc)
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise SupabaseConfigurationError("SUPABASE_URL must be a plain HTTPS hosted project URL")
        if not re.fullmatch(r"sb_secret_[A-Za-z0-9_-]+", self.secret_key):
            raise SupabaseConfigurationError("SUPABASE_SECRET_KEY must use the server-only sb_secret_ format")
        if self.expected_project_ref and self.expected_project_ref != self.project_ref:
            raise SupabaseConfigurationError("SUPABASE_URL project ref does not match SUPABASE_PROJECT_REF")
        if self.timeout_seconds <= 0:
            raise SupabaseConfigurationError("Supabase timeout_seconds must be positive")
        if self.retry_attempts < 1:
            raise SupabaseConfigurationError("Supabase retry_attempts must be at least one")
        if self.backoff_base_seconds < 0 or self.backoff_cap_seconds < self.backoff_base_seconds:
            raise SupabaseConfigurationError("Supabase backoff settings are invalid")
        if not 1 <= self.batch_size <= 25:
            raise SupabaseConfigurationError("Supabase batch_size must be between 1 and 25")

    @property
    def project_ref(self) -> str:
        host = urlsplit(self.url).hostname or ""
        return host.split(".", 1)[0]

    @property
    def safe_identity(self) -> dict[str, object]:
        return {
            "project_ref": self.project_ref,
            "expected_project_ref": self.expected_project_ref,
            "project_ref_matches_expected": (
                self.project_ref == self.expected_project_ref
                if self.expected_project_ref is not None else None
            ),
        }

    @classmethod
    def from_env(cls, *, required: bool = True) -> "SupabaseConfig | None":
        """Load only secure Python-process variables from the root ``.env`` file."""

        load_env_file(PROJECT_ROOT / ".env")
        return cls.from_mapping(os.environ, required=required)

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, str], *, required: bool = True
    ) -> "SupabaseConfig | None":
        url = str(mapping.get("SUPABASE_URL", "")).strip().rstrip("/")
        secret_key = str(mapping.get("SUPABASE_SECRET_KEY", "")).strip()
        missing = [
            name
            for name, value in (("SUPABASE_URL", url), ("SUPABASE_SECRET_KEY", secret_key))
            if not value
        ]
        if missing:
            if required:
                raise SupabaseConfigurationError(
                    "Missing required server-only Supabase configuration: " + ", ".join(missing)
                )
            return None
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise SupabaseConfigurationError("SUPABASE_URL must be an HTTPS project URL")
        if not secret_key.startswith("sb_secret_"):
            raise SupabaseConfigurationError(
                "SUPABASE_SECRET_KEY must use the server-only sb_secret_ format"
            )
        expected = str(mapping.get("SUPABASE_PROJECT_REF", "")).strip() or None
        config = cls(url=url, secret_key=secret_key, expected_project_ref=expected)
        if expected and config.project_ref != expected:
            raise SupabaseConfigurationError(
                "SUPABASE_URL project ref does not match SUPABASE_PROJECT_REF"
            )
        return config


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    payload: object | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: object | None,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class PersistenceWriter(Protocol):
    """The small writer contract consumed by the durable local outbox worker."""

    config: SupabaseConfig

    def sync_item(self, item: SyncOutboxItem) -> dict[str, object]: ...


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib otherwise forwards apikey headers when following redirects.
        return None


class UrllibTransport:
    """Minimal HTTPS transport; tests inject an in-memory transport instead."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: object | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        body = (
            json.dumps(json_body, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if json_body is not None
            else None
        )
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with build_opener(_RejectRedirects()).open(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return HttpResponse(
                    status_code=int(response.status),
                    payload=_decode_json(raw),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            return HttpResponse(
                status_code=int(error.code),
                payload=_decode_json(raw),
                headers=dict(error.headers.items()) if error.headers else {},
            )
        except (URLError, OSError) as error:
            raise SupabaseNetworkError(redact_sensitive_text(error, secrets=tuple(headers.values()))) from None


def _decode_json(raw: str) -> object | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _as_iso(value: object | None) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _source_availability(freshness: object | None) -> str:
    return "UNAVAILABLE" if _freshness_status(freshness) == "UNAVAILABLE" else "AVAILABLE"


def _freshness_status(value: object | None) -> str:
    """Map local missing/unrecognised values to the schema's explicit state."""

    status = str(value or "UNAVAILABLE").upper()
    return status if status in {"FRESH", "STALE", "NOT_APPLICABLE"} else "UNAVAILABLE"


def _safe_age_hours(value: object | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return max(0.0, round((now - timestamp.astimezone(UTC)).total_seconds() / 3600, 3))
    except ValueError:
        return None


@dataclass(frozen=True)
class ConnectivityResult:
    project_ref: str
    project_ref_matches_expected: bool | None
    table_name: str
    table_query_status: int
    schema_available: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "project_ref": self.project_ref,
            "project_ref_matches_expected": self.project_ref_matches_expected,
            "table_name": self.table_name,
            "table_query_status": self.table_query_status,
            "schema_available": self.schema_available,
        }


class SupabaseWriter:
    """Write one immutable REAL observation bundle using stable conflict targets."""

    def __init__(
        self,
        config: SupabaseConfig,
        *,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibTransport()
        self.sleep = sleep
        self.now = now or (lambda: datetime.now(UTC))
        self._base_url = f"{config.url.rstrip('/')}/rest/v1"

    @classmethod
    def from_env(cls) -> "SupabaseWriter":
        return cls(SupabaseConfig.from_env(required=True))

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.config.secret_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "glofguard-server-sync/1",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _url(self, path: str, parameters: Mapping[str, str] | None = None) -> str:
        query = f"?{urlencode(parameters, safe=',.') }" if parameters else ""
        return f"{self._base_url}/{path.lstrip('/')}{query}"

    def _error_for_response(self, response: HttpResponse) -> SupabaseRemoteError:
        payload = response.payload
        if isinstance(payload, dict):
            detail = " ".join(
                str(payload.get(key, "")) for key in ("message", "hint", "details", "code")
            ).strip()
        else:
            detail = str(payload or "")
        message = redact_sensitive_text(detail, secrets=(self.config.secret_key,))[:500] or "Supabase Data API request failed"
        if response.status_code in {401, 403}:
            return SupabaseAuthenticationError(message, status_code=response.status_code)
        return SupabaseRemoteError(
            message,
            status_code=response.status_code,
            retryable=response.status_code in {408, 429, 500, 502, 503, 504},
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        parameters: Mapping[str, str] | None = None,
        json_body: object | None = None,
        prefer: str | None = None,
    ) -> HttpResponse:
        last_error: SupabaseRemoteError | None = None
        for attempt in range(self.config.retry_attempts):
            try:
                response = self.transport.request(
                    method,
                    self._url(path, parameters),
                    headers=self._headers(prefer),
                    json_body=json_body,
                    timeout_seconds=self.config.timeout_seconds,
                )
                if 200 <= response.status_code < 300:
                    return response
                error = self._error_for_response(response)
            except SupabaseRemoteError as caught:
                error = SupabaseRemoteError(
                    redact_sensitive_text(caught, secrets=(self.config.secret_key,)),
                    status_code=caught.status_code,
                    retryable=caught.retryable,
                )
            except (OSError, TimeoutError) as caught:
                error = SupabaseNetworkError(redact_sensitive_text(caught, secrets=(self.config.secret_key,)))
            if not error.retryable or attempt >= self.config.retry_attempts - 1:
                raise error
            last_error = error
            self.sleep(
                min(
                    self.config.backoff_cap_seconds,
                    self.config.backoff_base_seconds * (2**attempt),
                )
            )
        raise last_error or SupabaseRemoteError("Supabase request failed")

    def connectivity_check(self, table_name: str = "lakes") -> ConnectivityResult:
        """Perform an authenticated table query, never an OpenAPI-root probe."""

        try:
            response = self._request(
                "GET",
                quote(table_name, safe="_"),
                parameters={"select": "*", "limit": "1"},
            )
            if not isinstance(response.payload, list):
                raise SupabaseRemoteError("Authenticated table query returned an invalid representation")
            schema_available = True
            status = response.status_code
        except SupabaseAuthenticationError:
            raise
        except SupabaseRemoteError as error:
            if error.status_code != 404:
                raise
            schema_available = False
            status = 404
        return ConnectivityResult(
            project_ref=self.config.project_ref,
            project_ref_matches_expected=(
                self.config.expected_project_ref == self.config.project_ref
                if self.config.expected_project_ref is not None else None
            ),
            table_name=table_name,
            table_query_status=status,
            schema_available=schema_available,
        )

    def inspect_required_tables(self) -> dict[str, str]:
        """Probe expected API names; this does not discover the real SQL schema."""

        result: dict[str, str] = {}
        for table_name in (
            "lakes",
            "baseline_susceptibility",
            "environmental_observations",
            "source_freshness",
            "ingestion_runs",
            "processing_queue",
        ):
            try:
                result[table_name] = (
                    "PRESENT" if self.connectivity_check(table_name).schema_available else "MISSING_OR_NOT_EXPOSED"
                )
            except SupabaseAuthenticationError:
                raise
            except SupabaseRemoteError as error:
                result[table_name] = f"UNAVAILABLE_{error.status_code or 'NETWORK'}"
        result["sync_outbox"] = "LOCAL_SQLITE_DURABLE_OUTBOX; PRIVATE_REMOTE_TABLE_REQUIRES_DB_ADMIN_INSPECTION"
        return result

    def _upsert(
        self,
        table_name: str,
        rows: list[dict[str, object]],
        *,
        conflict_target: str,
        immutable: bool = False,
    ) -> HttpResponse:
        if not 1 <= len(rows) <= self.config.batch_size:
            raise SupabaseRemoteError("Supabase write batch is outside the configured safe bound")
        prefer = (
            "resolution=ignore-duplicates,return=representation"
            if immutable
            else "resolution=merge-duplicates,return=representation"
        )
        return self._request(
            "POST",
            quote(table_name, safe="_"),
            parameters={"on_conflict": conflict_target},
            json_body=rows,
            prefer=prefer,
        )

    def _patch(self, table_name: str, filters: Mapping[str, str], payload: dict[str, object]) -> HttpResponse:
        return self._request(
            "PATCH",
            quote(table_name, safe="_"),
            parameters=filters,
            json_body=payload,
            prefer="return=representation",
        )

    def _source_rows(
        self, observation_id: str, record: Mapping[str, object]
    ) -> list[dict[str, object]]:
        # Source age belongs to the historical observation, not to a later retry.
        now = datetime.fromisoformat(str(record["prediction_timestamp"]).replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        source_specs = (
            (
                "SENTINEL_2",
                "satellite_freshness_status",
                "satellite_observation_date",
                {
                    "cloud_percentage": record.get("satellite_cloud_percentage"),
                    "area_current_km2": record.get("area_current_km2"),
                },
            ),
            (
                "JAXA_GSMAP",
                "observed_weather_freshness_status",
                "weather_observation_date",
                {
                    "rainfall_last_24h_mm": record.get("rainfall_last_24h"),
                    "rainfall_last_7d_mm": record.get("rainfall_last_7d"),
                    "rainfall_last_30d_mm": record.get("rainfall_last_30d"),
                },
            ),
            (
                "NOAA_GFS",
                "forecast_freshness_status",
                "forecast_creation_time",
                {"forecast_horizon_hours": record.get("forecast_horizon_hours")},
            ),
        )
        rows: list[dict[str, object]] = []
        for source_name, freshness_key, timestamp_key, details in source_specs:
            freshness = _freshness_status(record.get(freshness_key))
            observed_at = _as_iso(record.get(timestamp_key))
            rows.append(
                {
                    "observation_id": observation_id,
                    "lake_id": record["lake_id"],
                    "source_name": source_name,
                    "availability_status": _source_availability(freshness),
                    "freshness_status": freshness,
                    "quality_status": record.get("data_quality_status"),
                    "source_observation_at": observed_at,
                    "age_hours": _safe_age_hours(observed_at, now),
                    "details": details,
                }
            )
        power_status = str(record.get("historical_baseline_status") or "UNAVAILABLE").upper()
        rows.append(
            {
                "observation_id": observation_id,
                "lake_id": record["lake_id"],
                "source_name": "NASA_POWER",
                "availability_status": "AVAILABLE" if power_status == "AVAILABLE" else "UNAVAILABLE",
                "freshness_status": "NOT_APPLICABLE",
                "quality_status": power_status,
                "source_observation_at": _as_iso(record.get("historical_baseline_observation_date")),
                "age_hours": None,
                "details": {"baseline_status": power_status},
            }
        )
        return rows

    @staticmethod
    def _queue_status(record: Mapping[str, object]) -> str:
        sentinel = _freshness_status(record.get("satellite_freshness_status"))
        freshness = {
            sentinel,
            _freshness_status(record.get("observed_weather_freshness_status")),
            _freshness_status(record.get("forecast_freshness_status")),
        }
        if sentinel == "UNAVAILABLE":
            return "SATELLITE_UNAVAILABLE"
        if sentinel == "STALE":
            return "SATELLITE_STALE"
        if freshness == {"FRESH"}:
            return "LIVE_COMPLETE"
        return "LIVE_PARTIAL"

    def _bundle(self, item: SyncOutboxItem) -> dict[str, object]:
        encoded = json.dumps(item.payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != item.payload_sha256:
            raise SupabaseRemoteError("Supabase outbox payload hash mismatch")
        if item.payload.get("schema_version") != "supabase-outbox-v1" or any(
            item.payload.get(key) != getattr(item, key)
            for key in ("event_id", "observation_id", "run_id")
        ):
            raise SupabaseRemoteError("Supabase outbox identity mismatch")
        record = item.payload.get("record")
        if not isinstance(record, dict):
            raise SupabaseRemoteError("Supabase outbox payload is malformed")
        if record.get("source_mode") != "REAL":
            raise SupabaseRemoteError("Refusing to sync non-REAL data", retryable=False)
        if str(record.get("lake_id")) != item.lake_id:
            raise SupabaseRemoteError("Supabase outbox lake identity mismatch", retryable=False)
        retrieved_at = _as_iso(record.get("prediction_timestamp"))
        try:
            datetime.fromisoformat(str(retrieved_at).replace("Z", "+00:00"))
        except ValueError:
            raise SupabaseRemoteError("REAL observation requires a valid saved prediction timestamp") from None
        observation = {
            "observation_id": item.observation_id,
            "run_id": item.run_id,
            "lake_id": record["lake_id"],
            "observation_date": record["observation_date"],
            "input_signature": record.get("input_signature") or item.payload_sha256,
            "source_mode": "REAL",
            "environmental_conditions_score": record.get("risk_score"),
            "environmental_conditions_level": record.get("risk_level"),
            "score_interpretation": "Research environmental conditions index; not an official warning",
            "current_area_km2": record.get("area_current_km2"),
            "sentinel_observation_at": _as_iso(record.get("satellite_observation_date")),
            "sentinel_cloud_percentage": record.get("satellite_cloud_percentage"),
            "current_temperature_c": record.get("temperature_current"),
            "rainfall_last_24h_mm": record.get("rainfall_last_24h"),
            "rainfall_last_7d_mm": record.get("rainfall_last_7d"),
            "rainfall_last_30d_mm": record.get("rainfall_last_30d"),
            "gsmap_observed_at": _as_iso(record.get("weather_observation_date")),
            "forecast_rainfall_next_72h_mm": record.get("forecast_rainfall_next_72h"),
            "forecast_temperature_next_24h_c": None,
            "forecast_temperature_next_7d_c": record.get("forecast_temperature_next_7d"),
            "gfs_created_at": _as_iso(record.get("forecast_creation_time")),
            "nasa_power_baseline_available": (
                str(record.get("historical_baseline_status") or "").upper() == "AVAILABLE"
            ),
            "nasa_power_latest_date": _as_iso(record.get("historical_baseline_observation_date")),
            "confidence": record.get("confidence_level"),
            "data_completeness_warning": record.get("data_quality_warning"),
            "model_version": record.get("model_version") or "unknown",
            "data_version": "supabase-outbox-v1",
            "retrieved_at": retrieved_at,
            "raw_evidence": record,
        }
        run = {
            "run_id": item.run_id,
            "run_kind": "LOCAL_FIRST_REFRESH_SYNC",
            "evidence_version": None,
            "source_mode": "REAL",
            "run_status": "RUNNING",
            "started_at": retrieved_at,
            "completed_at": None,
            "exact_command": None,
            "selected_lake_count": 1,
            "successful_lake_count": 0,
            "failed_lake_count": 0,
            "mock_count": 0,
            "runtime_seconds": None,
            "api_usage": {},
            "data_version": "supabase-outbox-v1",
            "evidence_manifest_sha256": item.payload_sha256,
        }
        queue_status = self._queue_status(record)
        queue = {
            "lake_id": record["lake_id"],
            "processing_status": queue_status,
            "approval_required": True,
            "batch_id": None,
            "priority": 0,
            "attempt_count": 0,
            "last_attempt_at": retrieved_at,
            "last_successful_run_id": item.run_id if queue_status == "LIVE_COMPLETE" else None,
            "last_error": None,
            "updated_at": retrieved_at,
        }
        return {
            "record": record,
            "run": run,
            "observation": observation,
            "freshness": self._source_rows(item.observation_id, record),
            "queue": queue,
        }

    def _assert_remote_lake_parent(self, lake_id: str) -> None:
        """Require a preloaded, geometry-validated lake rather than fabricating it.

        A daily record contains only a point, whereas the controlled inventory
        bootstrap owns the authoritative source polygon and its validation
        metadata. A missing parent must remain a visible failed sync, never a
        fabricated lake row.
        """

        response = self._request(
            "GET",
            "lakes",
            parameters={"lake_id": f"eq.{lake_id}", "select": "lake_id", "limit": "1"},
        )
        rows = response.payload if isinstance(response.payload, list) else []
        if not any(isinstance(row, dict) and str(row.get("lake_id")) == lake_id for row in rows):
            raise SupabaseRemoteError(
                "Remote lake parent is missing; run the separately approved inventory bootstrap first",
                status_code=409,
                retryable=False,
            )

    @staticmethod
    def _stored_value_matches(column: str, expected: object, actual: object) -> bool:
        if column.endswith("_at") and isinstance(expected, str) and isinstance(actual, str):
            # PostgREST normalizes timestamptz offsets and fractional precision.
            def timestamp(value: str) -> datetime:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

            try:
                return timestamp(expected) == timestamp(actual)
            except ValueError:
                return False
        return expected == actual

    def _verify_rows(
        self,
        table_name: str,
        filters: Mapping[str, str],
        expected: list[dict[str, object]],
        identifiers: tuple[str, ...],
    ) -> list[dict[str, object]]:
        """Require authenticated read-back, including existing conflict rows.

        A 2xx response or ignore-duplicate upsert is not evidence that the
        intended data was stored. Do not log returned data on mismatch.
        """
        columns = sorted({column for row in expected for column in row})
        response = self._request(
            "GET", table_name,
            parameters={**filters, "select": ",".join(columns), "limit": str(len(expected) + 1)},
        )
        rows = response.payload
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise SupabaseRemoteError(f"Remote verification returned invalid rows for {table_name}")
        if len(rows) < len(expected):
            raise SupabaseRemoteError(f"Remote verification missing rows in {table_name}", retryable=True)
        if len(rows) != len(expected):
            raise SupabaseRemoteError(f"Remote verification found duplicate or extra rows in {table_name}")
        expected_by_key = {tuple(row[key] for key in identifiers): row for row in expected}
        seen: set[tuple[object, ...]] = set()
        for row in rows:
            key = tuple(row.get(column) for column in identifiers)
            if key not in expected_by_key or key in seen:
                raise SupabaseRemoteError(f"Remote verification identifier mismatch in {table_name}")
            seen.add(key)
            for column, value in expected_by_key[key].items():
                if column not in row or not self._stored_value_matches(column, value, row[column]):
                    raise SupabaseRemoteError(
                        f"Remote verification conflict in {table_name}.{column}", status_code=409
                    )
        return rows

    def sync_item(self, item: SyncOutboxItem) -> dict[str, object]:
        """Persist a single bundle in parent-to-child order with stable keys."""

        bundle = self._bundle(item)
        self._assert_remote_lake_parent(item.lake_id)
        self._upsert("ingestion_runs", [bundle["run"]], conflict_target="run_id", immutable=True)
        run_identity = {
            key: value for key, value in bundle["run"].items()
            if key not in {"run_status", "completed_at", "successful_lake_count", "failed_lake_count"}
        }
        self._verify_rows("ingestion_runs", {"run_id": f"eq.{item.run_id}"}, [run_identity], ("run_id",))
        self._upsert(
            "environmental_observations",
            [bundle["observation"]],
            conflict_target="observation_id",
            immutable=True,
        )
        self._verify_rows(
            "environmental_observations", {"observation_id": f"eq.{item.observation_id}"},
            [bundle["observation"]], ("observation_id",),
        )
        freshness = list(bundle["freshness"])
        for offset in range(0, len(freshness), self.config.batch_size):
            self._upsert(
                "source_freshness", freshness[offset:offset + self.config.batch_size],
                conflict_target="observation_id,source_name", immutable=True,
            )
        self._verify_rows(
            "source_freshness", {"observation_id": f"eq.{item.observation_id}"},
            freshness, ("observation_id", "source_name"),
        )
        # Ignore an existing queue row: a late outbox replay must never reset a newer queue state.
        self._upsert(
            "processing_queue", [bundle["queue"]], conflict_target="lake_id", immutable=True
        )
        self._verify_rows(
            "processing_queue", {"lake_id": f"eq.{item.lake_id}"},
            [{"lake_id": item.lake_id}], ("lake_id",),
        )
        completed_at = self.now().astimezone(UTC).isoformat()
        self._patch(
            "ingestion_runs",
            {"run_id": f"eq.{item.run_id}", "run_status": "eq.RUNNING"},
            {
                "run_status": "COMPLETE",
                "completed_at": completed_at,
                "successful_lake_count": 1,
                "failed_lake_count": 0,
            },
        )
        # A replay of an already COMPLETE run preserves its completion time.
        completed_runs = self._verify_rows(
            "ingestion_runs", {"run_id": f"eq.{item.run_id}", "completed_at": "not.is.null"},
            [{**run_identity, "run_status": "COMPLETE", "successful_lake_count": 1, "failed_lake_count": 0}],
            ("run_id",),
        )
        return {
            "event_id": item.event_id,
            "observation_id": item.observation_id,
            "run_id": item.run_id,
            "source_freshness_rows": len(bundle["freshness"]),
            "lake_id": item.lake_id,
            "source_mode": "REAL",
            "retrieved_at": bundle["observation"]["retrieved_at"],
            "payload_sha256": item.payload_sha256,
            "verified_run_status": completed_runs[0]["run_status"],
            "read_back_verified": True,
            "remote_status": REMOTE_SAVED,
        }


@dataclass(frozen=True)
class SyncReport:
    local_status: str
    remote_saved: int
    remote_pending: int
    remote_failed: int
    queued_events: int
    dry_run: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "local_status": self.local_status,
            REMOTE_SAVED: self.remote_saved,
            REMOTE_PENDING: self.remote_pending,
            REMOTE_FAILED: self.remote_failed,
            "queued_events": self.queued_events,
            "dry_run": self.dry_run,
        }


class SupabaseSyncService:
    """Flushes bounded local-outbox work and records truthful remote statuses."""

    def __init__(
        self,
        repository: Repository,
        writer: PersistenceWriter,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.writer = writer
        self.now = now or (lambda: datetime.now(UTC))

    def dry_run(self, lake_ids: list[str] | None = None, *, limit: int = 25) -> SyncReport:
        items = self.repository.pending_sync_outbox(
            lake_ids, limit=min(limit, self.writer.config.batch_size), force=False
        )
        return SyncReport(
            local_status=LOCAL_SAVED,
            remote_saved=0,
            remote_pending=len(items),
            remote_failed=0,
            queued_events=len(items),
            dry_run=True,
        )

    def sync_pending(
        self,
        lake_ids: list[str] | None = None,
        *,
        limit: int = 25,
        force: bool = False,
    ) -> SyncReport:
        items = self.repository.pending_sync_outbox(
            lake_ids, limit=min(limit, self.writer.config.batch_size), force=force
        )
        saved = pending = failed = 0
        for item in items:
            attempt_count = self.repository.begin_sync_attempt(item.event_id)
            try:
                receipt = self.writer.sync_item(item)
            except SupabaseRemoteError as error:
                retryable = error.retryable
                next_attempt_at = None
                if retryable:
                    delay = min(
                        self.writer.config.backoff_cap_seconds,
                        self.writer.config.backoff_base_seconds * (2 ** max(0, attempt_count - 1)),
                    )
                    next_attempt_at = (self.now().astimezone(UTC) + timedelta(seconds=delay)).isoformat()
                    pending += 1
                else:
                    failed += 1
                self.repository.mark_sync_failed(
                    item.event_id,
                    error,
                    retryable=retryable,
                    next_attempt_at=next_attempt_at,
                )
            else:
                self.repository.mark_sync_saved(item.event_id, receipt)
                saved += 1
        return SyncReport(
            local_status=LOCAL_SAVED,
            remote_saved=saved,
            remote_pending=pending,
            remote_failed=failed,
            queued_events=len(items),
            dry_run=False,
        )
