from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

import pandas as pd


TRIP_COLUMN_OPTIONS = {
    "trip_id": ["Trip ID", "TripID"],
    "load_id": ["Load ID", "LoadID"],
    "status": ["Load Execution Status", "Status"],
    "driver": ["Driver Name", "DriverName", "Driver"],
    "route": ["Facility Sequence", "Route"],
    "completion_date": ["Stop 2 Actual Arrival Date", "Stop 2  Actual Arrival Date"],
    "completion_time": ["Stop 2 Actual Arrival Time", "Stop 2  Actual Arrival Time"],
    "completion_utc_offset": ["Stop 2 UTC Offset", "Stop 2  UTC Offset"],
}

PAYMENT_COLUMN_OPTIONS = {
    "trip_id": ["Trip ID", "TripID"],
    "load_id": ["Load ID", "LoadID"],
    "gross_pay": ["Gross Pay", "GrossPay", "Amount"],
}


@dataclass(frozen=True)
class ReconciliationResult:
    trips_df: pd.DataFrame
    payment_df: pd.DataFrame
    detected_trip_columns: dict[str, str | None]
    detected_payment_columns: dict[str, str | None]
    completed_df: pd.DataFrame
    missing_df: pd.DataFrame
    cancelled_df: pd.DataFrame
    next_week_df: pd.DataFrame
    total_paid: float
    paid_load_count: int
    paid_trip_count: int
    cancel_rate: float
    diagnostics: dict[str, object]
    warnings: list[str]


class ValidationError(ValueError):
    pass


def normalize_column_name(value: object) -> str:
    return " ".join(str(value).strip().lower().split())


def find_column(options: Iterable[str], columns: Iterable[object]) -> str | None:
    normalized = {normalize_column_name(column): column for column in columns}
    for option in options:
        found = normalized.get(normalize_column_name(option))
        if found is not None:
            return str(found)
    return None


def detect_columns(df: pd.DataFrame, options: dict[str, list[str]]) -> dict[str, str | None]:
    return {key: find_column(values, df.columns) for key, values in options.items()}


def normalize_id(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def safe_float(value: object) -> float:
    if pd.isna(value):
        return 0.0
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_completion_datetime(date_value: object, time_value: object = "") -> pd.Timestamp | None:
    date_text = "" if pd.isna(date_value) else str(date_value).strip()
    time_text = "" if pd.isna(time_value) else str(time_value).strip()
    if not date_text or date_text.upper() == "NAN":
        return None

    combined = f"{date_text} {time_text}".strip()
    formats = [
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        parsed = pd.to_datetime(combined, format=fmt, errors="coerce")
        if not pd.isna(parsed):
            return parsed

    parsed = pd.to_datetime(combined, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed


def normalize_driver(driver: object) -> str:
    if pd.isna(driver):
        return ""
    names = [name.strip() for name in str(driver).split(";") if name.strip()]
    unique_names = list(dict.fromkeys(names))
    if len(unique_names) <= 1:
        return unique_names[0] if unique_names else ""
    return ";".join(sorted(unique_names))


def validate_inputs(
    trips_df: pd.DataFrame,
    payment_df: pd.DataFrame,
    trip_columns: dict[str, str | None],
    payment_columns: dict[str, str | None],
) -> None:
    errors = []

    if trips_df.empty:
        errors.append("Trip file is empty.")
    if payment_df.empty:
        errors.append("Payment file is empty.")

    required_trip_columns = {
        "load_id": "Load ID",
        "status": "Status",
        "completion_date": "Completion date",
    }
    for key, label in required_trip_columns.items():
        if not trip_columns.get(key):
            errors.append(f"Trip file is missing a required column: {label}.")

    if not payment_columns.get("load_id") and not payment_columns.get("trip_id"):
        errors.append("Payment file must include Load ID or Trip ID.")

    load_col = trip_columns.get("load_id")
    if load_col and trips_df[load_col].map(normalize_id).eq("").all():
        errors.append("Trip file Load ID column is blank.")

    if errors:
        raise ValidationError("\n".join(errors))


def _status_bucket(status: object) -> str:
    value = "" if pd.isna(status) else str(status).strip().lower()
    if any(token in value for token in ["cancel", "rejected", "not started"]):
        return "cancelled"
    if "complete" in value:
        return "complete"
    return "other"


def _parse_utc_offset(value: object) -> float | None:
    if pd.isna(value):
        return None
    text = str(value).strip().replace("UTC", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def payment_week_date(completion: pd.Timestamp | None, utc_offset: object = None) -> date | None:
    if completion is None:
        return None

    offset = _parse_utc_offset(utc_offset)
    if offset is None:
        return completion.date()

    pacific_offset = -8
    pacific_completion = completion + timedelta(hours=pacific_offset - offset)
    return pacific_completion.date()


def _completion_maps(
    trips_df: pd.DataFrame,
    trip_columns: dict[str, str | None],
) -> tuple[dict[str, pd.Timestamp], dict[str, date], dict[str, date], int]:
    load_col = trip_columns["load_id"]
    trip_col = trip_columns.get("trip_id")
    date_col = trip_columns["completion_date"]
    time_col = trip_columns.get("completion_time")
    offset_col = trip_columns.get("completion_utc_offset")

    load_completion: dict[str, pd.Timestamp] = {}
    load_payment_date: dict[str, date] = {}
    trip_latest_payment_date: dict[str, date] = {}
    unparseable_dates = 0

    for _, row in trips_df.iterrows():
        load_id = normalize_id(row[load_col])
        trip_id = normalize_id(row[trip_col]) if trip_col else ""
        raw_date = row[date_col]
        completion = parse_completion_datetime(raw_date, row[time_col] if time_col else "")

        if completion is None:
            if not pd.isna(raw_date) and str(raw_date).strip():
                unparseable_dates += 1
            continue

        if load_id:
            load_completion[load_id] = completion
            completion_payment_date = payment_week_date(completion, row[offset_col] if offset_col else None)
            load_payment_date[load_id] = completion_payment_date
            if trip_id and completion_payment_date is not None:
                current = trip_latest_payment_date.get(trip_id)
                if current is None or completion_payment_date > current:
                    trip_latest_payment_date[trip_id] = completion_payment_date

    return load_completion, load_payment_date, trip_latest_payment_date, unparseable_dates


def _add_display_columns(
    df: pd.DataFrame,
    trip_columns: dict[str, str | None],
    completion_by_load: dict[str, pd.Timestamp],
) -> pd.DataFrame:
    if df.empty:
        return df

    output = df.copy()
    load_col = trip_columns["load_id"]
    trip_col = trip_columns.get("trip_id")
    driver_col = trip_columns.get("driver")
    route_col = trip_columns.get("route")
    status_col = trip_columns["status"]

    output.insert(0, "Normalized Load ID", output[load_col].map(normalize_id))
    if trip_col:
        output.insert(0, "Normalized Trip ID", output[trip_col].map(normalize_id))
    else:
        output.insert(0, "Normalized Trip ID", "")

    output["Driver Display"] = output[driver_col].map(normalize_driver) if driver_col else ""
    output["Route Display"] = output[route_col].fillna("").astype(str).str.strip() if route_col else ""
    output["Status Display"] = output[status_col].fillna("").astype(str).str.strip()
    output["Completion Datetime"] = output["Normalized Load ID"].map(completion_by_load)
    return output


def _payment_ids(payment_df: pd.DataFrame, payment_columns: dict[str, str | None]) -> tuple[set[str], set[str]]:
    load_col = payment_columns.get("load_id")
    trip_col = payment_columns.get("trip_id")
    paid_loads = set(payment_df[load_col].map(normalize_id)) - {""} if load_col else set()
    paid_trips = set(payment_df[trip_col].map(normalize_id)) - {""} if trip_col else set()
    return paid_loads, paid_trips


def calculate_total_paid(payment_df: pd.DataFrame, payment_columns: dict[str, str | None]) -> tuple[float, list[str]]:
    gross_col = payment_columns.get("gross_pay")
    warnings: list[str] = []
    if not gross_col:
        warnings.append("Payment amount column was not found, so total paid is shown as $0.00.")
        return 0.0, warnings

    working = payment_df.copy()
    working["_gross_numeric"] = working[gross_col].map(safe_float)
    working = working[working["_gross_numeric"] != 0]

    if working.empty:
        return 0.0, warnings

    load_col = payment_columns.get("load_id")
    trip_col = payment_columns.get("trip_id")

    working["_load_key"] = working[load_col].map(normalize_id) if load_col else ""
    working["_trip_key"] = working[trip_col].map(normalize_id) if trip_col else ""
    working["_has_payment_id"] = (working["_load_key"] != "") | (working["_trip_key"] != "")

    keyed = working[working["_has_payment_id"]].copy()
    summary_rows = working[~working["_has_payment_id"]]

    if not keyed.empty and not summary_rows.empty:
        keyed_total = float(keyed["_gross_numeric"].sum())
        matching_summary = summary_rows[
            (summary_rows["_gross_numeric"] - keyed_total).abs() < 0.01
        ]
        if not matching_summary.empty:
            return float(matching_summary["_gross_numeric"].iloc[-1]), warnings

    if not keyed.empty:
        dedupe_columns = ["_trip_key", "_load_key", "_gross_numeric"]
        item_col = find_column(["Item Type", "ItemType"], payment_df.columns)
        if item_col:
            keyed["_item_type"] = keyed[item_col].fillna("").astype(str).str.strip()
            dedupe_columns.append("_item_type")

        deduped = keyed.drop_duplicates(subset=dedupe_columns)
        if len(deduped) < len(keyed):
            warnings.append("Duplicate payment rows were detected and deduplicated for total paid.")
        return float(deduped["_gross_numeric"].sum()), warnings

    warnings.append("Payment IDs were blank in amount rows, so total paid may include summary rows.")
    return float(working["_gross_numeric"].sum()), warnings


def reconcile(trips_df: pd.DataFrame, payment_df: pd.DataFrame, week_end: date) -> ReconciliationResult:
    trip_columns = detect_columns(trips_df, TRIP_COLUMN_OPTIONS)
    payment_columns = detect_columns(payment_df, PAYMENT_COLUMN_OPTIONS)
    validate_inputs(trips_df, payment_df, trip_columns, payment_columns)

    paid_loads, paid_trips = _payment_ids(payment_df, payment_columns)
    load_completion, load_payment_date, trip_latest_payment_date, unparseable_dates = _completion_maps(
        trips_df,
        trip_columns,
    )

    load_col = trip_columns["load_id"]
    trip_col = trip_columns.get("trip_id")
    status_col = trip_columns["status"]

    missing_rows = []
    cancelled_rows = []
    next_week_rows = []
    completed_rows = []
    completed_reconciliation_statuses = []

    for _, row in trips_df.iterrows():
        load_id = normalize_id(row[load_col])
        trip_id = normalize_id(row[trip_col]) if trip_col else ""
        if not load_id:
            continue

        is_paid = load_id in paid_loads or bool(trip_id and trip_id in paid_trips)
        bucket = _status_bucket(row[status_col])

        if bucket == "complete":
            # Amazon pays multi-stop trips in the period where the whole trip completes.
            completion_payment_date = trip_latest_payment_date.get(trip_id) if trip_id else load_payment_date.get(load_id)

            if is_paid:
                completed_reconciliation_status = "Paid"
            elif completion_payment_date is not None and completion_payment_date > week_end:
                completed_reconciliation_status = "Next Week"
                next_week_rows.append(row)
            else:
                completed_reconciliation_status = "Missing"
                missing_rows.append(row)

            completed_rows.append(row)
            completed_reconciliation_statuses.append(completed_reconciliation_status)
            continue

        if is_paid:
            continue

        if bucket == "cancelled":
            cancelled_rows.append(row)
            continue

        missing_rows.append(row)

    completed_df = pd.DataFrame(completed_rows, columns=trips_df.columns)
    missing_df = pd.DataFrame(missing_rows, columns=trips_df.columns)
    cancelled_df = pd.DataFrame(cancelled_rows, columns=trips_df.columns)
    next_week_df = pd.DataFrame(next_week_rows, columns=trips_df.columns)

    completed_df = _add_display_columns(completed_df, trip_columns, load_completion)
    if not completed_df.empty:
        completed_df["Reconciliation Status"] = completed_reconciliation_statuses
    missing_df = _add_display_columns(missing_df, trip_columns, load_completion)
    cancelled_df = _add_display_columns(cancelled_df, trip_columns, load_completion)
    next_week_df = _add_display_columns(next_week_df, trip_columns, load_completion)

    total_paid, warnings = calculate_total_paid(payment_df, payment_columns)
    duplicate_trip_loads = int(trips_df[load_col].map(normalize_id).duplicated().sum())
    cancel_rate = (len(cancelled_df) / len(trips_df) * 100) if len(trips_df) else 0.0

    diagnostics = {
        "trip_rows": len(trips_df),
        "payment_rows": len(payment_df),
        "unique_trip_load_ids": len(set(trips_df[load_col].map(normalize_id)) - {""}),
        "unique_paid_load_ids": len(paid_loads),
        "unique_paid_trip_ids": len(paid_trips),
        "duplicate_trip_load_ids": duplicate_trip_loads,
        "unparseable_completion_dates": unparseable_dates,
    }

    if unparseable_dates:
        warnings.append(f"{unparseable_dates} completion date(s) could not be parsed.")
    if duplicate_trip_loads:
        warnings.append(f"{duplicate_trip_loads} duplicate trip Load ID row(s) were found.")

    return ReconciliationResult(
        trips_df=trips_df,
        payment_df=payment_df,
        detected_trip_columns=trip_columns,
        detected_payment_columns=payment_columns,
        completed_df=completed_df,
        missing_df=missing_df,
        cancelled_df=cancelled_df,
        next_week_df=next_week_df,
        total_paid=total_paid,
        paid_load_count=len(paid_loads),
        paid_trip_count=len(paid_trips),
        cancel_rate=cancel_rate,
        diagnostics=diagnostics,
        warnings=warnings,
    )
