from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

from azure.cosmos import CosmosClient
from dotenv import load_dotenv

try:
    from .fatsecret import FatSecretDiaryCredentials, get_food_diary_entries
    from .weekly_avg import RenphoScalesData
except ImportError:
    from fatsecret import FatSecretDiaryCredentials, get_food_diary_entries
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


def _normalise_ref(value: str) -> str:
    return value.strip().upper().replace("-", "_").replace(" ", "_")


def _source_config(user: dict[str, Any], source_name: str) -> dict[str, Any]:
    sync_sources = user.get("syncSources") or {}
    source_config = sync_sources.get(source_name)
    if isinstance(source_config, dict):
        return source_config
    if source_config is True:
        return {"enabled": True}
    if sync_sources.get("renphoFatsecret") is True and source_name in {"renpho", "fatsecret"}:
        return {"enabled": True}
    return {"enabled": False}


def _source_enabled(user: dict[str, Any], source_name: str) -> bool:
    return bool(_source_config(user, source_name).get("enabled"))


def _credential_ref(user: dict[str, Any], source_name: str) -> str:
    source_config = _source_config(user, source_name)
    return (
        source_config.get("credentialRef")
        or user.get("credentialRef")
        or user.get("userID")
        or user.get("id")
    )


def _credential_env_names(source_name: str, credential_ref: str, field_name: str, legacy_name: str | None = None) -> list[str]:
    ref = _normalise_ref(credential_ref)
    names = [f"{source_name.upper()}_{ref}_{field_name.upper()}"]
    if legacy_name:
        names.append(legacy_name)
    return names


def _first_env(env_names: list[str]) -> str:
    for env_name in env_names:
        value = os.getenv(env_name)
        if value:
            return value.strip().strip('"')
    raise RuntimeError(f"Missing required environment variable. Tried: {', '.join(env_names)}")


def _renpho_credentials(user: dict[str, Any]) -> tuple[str, str]:
    credential_ref = _credential_ref(user, "renpho")
    is_jack = str(user.get("userID", "")).lower() == "jack" or str(credential_ref).lower() == "jack"
    return (
        _first_env(_credential_env_names("RENPHO", credential_ref, "EMAIL", "MY_EMAIL" if is_jack else None)),
        _first_env(_credential_env_names("RENPHO", credential_ref, "PASSWORD", "MY_PASSWORD" if is_jack else None)),
    )


def _fatsecret_credentials(user: dict[str, Any]) -> FatSecretDiaryCredentials:
    credential_ref = _credential_ref(user, "fatsecret")
    is_jack = str(user.get("userID", "")).lower() == "jack" or str(credential_ref).lower() == "jack"
    return FatSecretDiaryCredentials(
        consumer_key=_first_env(
            _credential_env_names("FATSECRET", credential_ref, "CONSUMER_KEY", "FATSECRET_CONSUMER_KEY" if is_jack else None)
        ),
        consumer_secret=_first_env(
            _credential_env_names("FATSECRET", credential_ref, "CONSUMER_SECRET", "FATSECRET_CONSUMER_SECRET" if is_jack else None)
        ),
        access_token=_first_env(
            _credential_env_names("FATSECRET", credential_ref, "ACCESS_TOKEN", "FATSECRET_ACCESS_TOKEN" if is_jack else None)
        ),
        access_secret=_first_env(
            _credential_env_names("FATSECRET", credential_ref, "ACCESS_SECRET", "FATSECRET_ACCESS_SECRET" if is_jack else None)
        ),
    )


def _user_slug(user_id: str) -> str:
    return f"user_{user_id.strip().lower().replace(' ', '_')}"


def _synced_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cosmos_raw_container():
    client = CosmosClient.from_connection_string(_required_env("COSMOS_DB_CONNECTION_STRING"))
    database = client.get_database_client(_required_env("COSMOS_DB_DATABASE_NAME"))
    container_name = _optional_env("COSMOS_DB_RAW_CONTAINER_NAME", RAW_CONTAINER_DEFAULT)
    return database.get_container_client(container_name)


def _cosmos_competitions_container():
    client = CosmosClient.from_connection_string(_required_env("COSMOS_DB_CONNECTION_STRING"))
    database = client.get_database_client(_required_env("COSMOS_DB_DATABASE_NAME"))
    container_name = _optional_env("COSMOS_DB_COMPETITIONS_CONTAINER_NAME", "fitness_competitions")
    return database.get_container_client(container_name)


def get_raw_sync_users() -> list[dict[str, Any]]:
    users = list(
        _cosmos_competitions_container().query_items(
            query=(
                "SELECT * FROM c WHERE c.type = 'user' AND c.active = true "
                "AND IS_DEFINED(c.syncSources) "
                "AND (c.syncSources.renphoFatsecret = true "
                "OR c.syncSources.renpho.enabled = true "
                "OR c.syncSources.fatsecret.enabled = true "
                "OR c.syncSources.renpho = true "
                "OR c.syncSources.fatsecret = true)"
            ),
            enable_cross_partition_query=True,
        )
    )

    if not users:
        raise RuntimeError(
            "No active users are configured for Renpho/FatSecret raw sync. "
            "Enable syncSources.renpho or syncSources.fatsecret on at least one user document."
        )

    missing_user_ids = [user.get("id", "<unknown>") for user in users if not user.get("userID")]
    if missing_user_ids:
        raise RuntimeError(f"Raw sync user document(s) missing userID: {', '.join(missing_user_ids)}")

    return users


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
    protein_total = float(round(sum(float(entry.get("protein", 0)) for entry in entries), 1))

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


def sync_daily_fitness_raw_for_user(
    user: dict[str, Any],
    selected_date: date,
    synced_at: str | None = None,
) -> list[dict[str, Any]]:
    user_id = user["userID"]
    synced_at = synced_at or _synced_at()
    saved_documents: list[dict[str, Any]] = []

    if _source_enabled(user, "renpho"):
        renpho_email, renpho_password = _renpho_credentials(user)
        measurement = get_renpho_daily_measurement(selected_date, renpho_email, renpho_password)
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

    if _source_enabled(user, "fatsecret"):
        saved_documents.append(
            upsert_raw_fitness_document(
                build_fatsecret_daily_document(
                    get_food_diary_entries(selected_date, _fatsecret_credentials(user)),
                    user_id=user_id,
                    selected_date=selected_date,
                    synced_at=synced_at,
                )
            )
        )

    return saved_documents


def sync_daily_fitness_raw(selected_date: date | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
    selected_date = selected_date or date.today()
    users = get_raw_sync_users()
    if user_id:
        users = [user for user in users if user.get("userID") == user_id]
        if not users:
            raise RuntimeError(f"No active raw sync user found for userID {user_id}.")

    synced_at = _synced_at()
    saved_documents: list[dict[str, Any]] = []
    for user in users:
        saved_documents.extend(sync_daily_fitness_raw_for_user(user, selected_date, synced_at))

    return saved_documents
