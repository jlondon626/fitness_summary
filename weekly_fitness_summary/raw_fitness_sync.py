from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

from azure.cosmos import CosmosClient
from dotenv import load_dotenv

try:
    from .fatsecret import get_food_diary_entries
    from .weekly_avg import RenphoScalesData
except ImportError:
    from fatsecret import get_food_diary_entries
    from weekly_avg import RenphoScalesData


load_dotenv()

RAW_CONTAINER_DEFAULT = "fitness_raw"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip().strip('"')


def _optional_env(name: str, default: str) -> str:
    value = os.getenv(name)
    if not value:
        return default
    return value.strip().strip('"')


def _user_slug(user_id: str) -> str:
    return f"user_{user_id.strip().lower().replace(' ', '_')}"


def _synced_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cosmos_raw_container():
    client = CosmosClient.from_connection_string(_required_env("COSMOS_DB_CONNECTION_STRING"))
    database = client.get_database_client(_required_env("COSMOS_DB_DATABASE_NAME"))
    container_name = _optional_env("COSMOS_DB_RAW_CONTAINER_NAME", RAW_CONTAINER_DEFAULT)
    return database.get_container_client(container_name)


def _measurement_date(measurement: dict[str, Any]) -> date | None:
    local_created_at = measurement.get("localCreatedAt")
    if not local_created_at:
        return None

    for fmt in ("%Y-%m-%d    %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(local_created_at, fmt).date()
        except ValueError:
            continue
    return None


def get_renpho_daily_measurement(selected_date: date, email: str, password: str) -> dict[str, Any] | None:
    renpho_data = RenphoScalesData(email, password)
    measurements = [
        measurement
        for measurement in renpho_data.measurements
        if _measurement_date(measurement) == selected_date
    ]

    if not measurements:
        return None

    return max(measurements, key=lambda measurement: measurement.get("localCreatedAt", ""))


def build_renpho_daily_document(
    measurement: dict[str, Any],
    *,
    user_id: str,
    selected_date: date,
    synced_at: str | None = None,
) -> dict[str, Any]:
    user_slug = _user_slug(user_id)

    return {
        "id": f"renpho__{user_slug}__{selected_date.isoformat()}",
        "type": "renpho_daily",
        "userID": user_id,
        "date": selected_date.isoformat(),
        "weightKg": float(measurement["weight"]),
        "bodyFatPct": float(measurement["bodyfat"]),
        "source": "renpho",
        "syncedAt": synced_at or _synced_at(),
    }


def _food_entries_for_day(food_diary_response: dict[str, Any]) -> list[dict[str, Any]]:
    food_entries = (food_diary_response or {}).get("food_entries")
    if not food_entries:
        return []

    entries = food_entries.get("food_entry", [])
    if isinstance(entries, dict):
        return [entries]
    return entries or []


def build_fatsecret_daily_document(
    food_diary_response: dict[str, Any],
    *,
    user_id: str,
    selected_date: date,
    synced_at: str | None = None,
) -> dict[str, Any]:
    entries = _food_entries_for_day(food_diary_response)
    user_slug = _user_slug(user_id)
    protein_total = round(sum(float(entry.get("protein", 0)) for entry in entries), 1)

    return {
        "id": f"fatsecret__{user_slug}__{selected_date.isoformat()}",
        "type": "fatsecret_daily",
        "userID": user_id,
        "date": selected_date.isoformat(),
        "calories": round(sum(float(entry.get("calories", 0)) for entry in entries)),
        "proteinG": int(protein_total) if protein_total.is_integer() else protein_total,
        "logged": bool(entries),
        "source": "fatsecret",
        "syncedAt": synced_at or _synced_at(),
    }


def upsert_raw_fitness_document(document: dict[str, Any]) -> dict[str, Any]:
    return _cosmos_raw_container().upsert_item(document)


def sync_daily_fitness_raw(selected_date: date | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
    selected_date = selected_date or date.today()
    user_id = user_id or _optional_env("FITNESS_COMPETITION_USER_ID", "Jack")
    synced_at = _synced_at()

    saved_documents: list[dict[str, Any]] = []

    measurement = get_renpho_daily_measurement(
        selected_date,
        _required_env("MY_EMAIL"),
        _required_env("MY_PASSWORD"),
    )
    if measurement:
        saved_documents.append(
            upsert_raw_fitness_document(
                build_renpho_daily_document(
                    measurement,
                    user_id=user_id,
                    selected_date=selected_date,
                    synced_at=synced_at,
                )
            )
        )

    saved_documents.append(
        upsert_raw_fitness_document(
            build_fatsecret_daily_document(
                get_food_diary_entries(selected_date),
                user_id=user_id,
                selected_date=selected_date,
                synced_at=synced_at,
            )
        )
    )

    return saved_documents
