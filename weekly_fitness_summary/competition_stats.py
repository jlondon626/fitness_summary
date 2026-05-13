from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from .competition_scoring import (
    _challenge_end,
    _challenge_start,
    _competitions_container,
    _month_end,
    _parse_date,
    _participant_health_user_id,
    _participant_raw_user_id,
    _query_one,
    _week_start_for,
    get_active_challenges,
    get_apple_health_records,
    get_challenge_participants,
    get_latest_renpho_record_before,
    get_raw_records,
)


VALID_STATS_PERIODS = {"week", "month", "challenge"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today() -> date:
    return date.today()


def _challenge_by_id(challenge_id: str) -> dict[str, Any]:
    challenge = _query_one(
        _competitions_container(),
        "SELECT TOP 1 * FROM c WHERE c.type = 'challenge' AND c.challengeID = @challengeID",
        [{"name": "@challengeID", "value": challenge_id}],
    )
    if not challenge:
        raise RuntimeError(f"No challenge found for challengeID {challenge_id}.")
    return challenge


def _period_bounds(challenge: dict[str, Any], period: str, today: date | None = None) -> tuple[date, date]:
    today = today or _today()
    challenge_start = _challenge_start(challenge)
    challenge_end = _challenge_end(challenge)
    effective_today = min(today, challenge_end)

    if period == "week":
        period_start = _week_start_for(effective_today, challenge.get("weekStartsOn", "SUNDAY"))
        period_end = period_start + timedelta(days=6)
    elif period == "month":
        period_start = effective_today.replace(day=1)
        period_end = _month_end(effective_today)
    elif period == "challenge":
        period_start = challenge_start
        period_end = challenge_end
    else:
        raise ValueError(f"Invalid stats period: {period}")

    return max(period_start, challenge_start), min(period_end, effective_today)


def _date_range(start: date, end: date) -> list[date]:
    if end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _week_label(value: date, period_start: date) -> str:
    return f"W{((value - period_start).days // 7) + 1}"


def _bucket_ranges(period: str, period_start: date, period_end: date) -> list[tuple[str, date, date]]:
    if period == "week":
        return [(day.strftime("%a"), day, day) for day in _date_range(period_start, period_end)]

    buckets: list[tuple[str, date, date]] = []
    bucket_start = period_start
    while bucket_start <= period_end:
        bucket_end = min(bucket_start + timedelta(days=6), period_end)
        buckets.append((_week_label(bucket_start, period_start), bucket_start, bucket_end))
        bucket_start = bucket_end + timedelta(days=1)
    return buckets


def _participant_label(participant: dict[str, Any]) -> str:
    return participant.get("displayName") or participant.get("userID") or participant["participantId"]


def _daily_calorie_target(participant: dict[str, Any]) -> float | None:
    if participant.get("averageDailyCalorieTarget") is not None:
        return float(participant["averageDailyCalorieTarget"])
    if participant.get("weeklyCalorieTarget") is not None:
        return float(participant["weeklyCalorieTarget"]) / 7
    return None


def _record_active_calories(record: dict[str, Any]) -> float:
    if record.get("type") == "apple-health-data":
        return float(record.get("active_energy_kcal") or 0)
    return float(record.get("activeCalories") or 0)


def _percent_change(current: float | None, baseline: float | None) -> float:
    if current is None or baseline is None or baseline == 0:
        return 0
    return round(((current - baseline) / baseline) * 100, 2)


def _empty_series(
    period: str,
    period_start: date,
    period_end: date,
    participants: list[str],
) -> list[dict[str, Any]]:
    return [
        {"label": label, **{participant: 0 for participant in participants}}
        for label, _, _ in _bucket_ranges(period, period_start, period_end)
    ]


def _latest_weight(records: list[dict[str, Any]]) -> float | None:
    weights = [
        record
        for record in records
        if record.get("type") == "renpho_daily" and record.get("weightKg") is not None
    ]
    if not weights:
        return None
    return float(sorted(weights, key=lambda record: record.get("date", ""))[-1]["weightKg"])


def _first_weight(records: list[dict[str, Any]]) -> float | None:
    weights = [
        record
        for record in records
        if record.get("type") == "renpho_daily" and record.get("weightKg") is not None
    ]
    if not weights:
        return None
    return float(sorted(weights, key=lambda record: record.get("date", ""))[0]["weightKg"])


def _food_logged(record: dict[str, Any]) -> bool:
    return record.get("type") == "fatsecret_daily" and bool(record.get("logged"))


def _food_calories(record: dict[str, Any]) -> float:
    return float(record.get("calories") or 0)


def _build_participant_stats(
    participant: dict[str, Any],
    period: str,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    raw_user_id = _participant_raw_user_id(participant)
    raw_records = get_raw_records(raw_user_id, period_start, period_end)
    raw_records.extend(
        get_apple_health_records(
            _participant_health_user_id(participant),
            participant["participantId"],
            period_start,
            period_end,
        )
    )

    pre_period_weight = get_latest_renpho_record_before(raw_user_id, period_start)
    baseline_weight = (
        float(pre_period_weight["weightKg"])
        if pre_period_weight and pre_period_weight.get("weightKg") is not None
        else _first_weight(raw_records)
    )
    target = _daily_calorie_target(participant)

    food_logging_days = len({
        record["date"]
        for record in raw_records
        if record.get("date") and _food_logged(record)
    })
    weigh_in_days = len({
        record["date"]
        for record in raw_records
        if record.get("date") and record.get("type") == "renpho_daily" and record.get("weightKg") is not None
    })

    stats_by_bucket: dict[str, dict[str, float]] = {}
    for label, bucket_start, bucket_end in _bucket_ranges(period, period_start, period_end):
        bucket_records = [
            record
            for record in raw_records
            if record.get("date") and bucket_start <= _parse_date(record["date"]) <= bucket_end
        ]
        food_records = [record for record in bucket_records if _food_logged(record)]
        calorie_variance = 0
        if target is not None and food_records:
            calorie_variance = round(
                sum(_food_calories(record) - target for record in food_records) / len(food_records)
            )

        stats_by_bucket[label] = {
            "weightChangePct": _percent_change(_latest_weight(bucket_records), baseline_weight),
            "calorieAdherence": calorie_variance,
            "activeCalories": round(
                sum(_record_active_calories(record) for record in bucket_records if record.get("type") in {"apple-health-data", "active_calories_daily"})
            ),
        }

    return {
        "statsByBucket": stats_by_bucket,
        "foodLoggingDays": food_logging_days,
        "weighInDays": weigh_in_days,
    }


def build_challenge_stats_payload(
    challenge_id: str,
    period: str,
    today: date | None = None,
) -> dict[str, Any]:
    period = period.strip().lower()
    if period not in VALID_STATS_PERIODS:
        raise ValueError("period must be one of: week, month, challenge")

    challenge = _challenge_by_id(challenge_id)
    period_start, period_end = _period_bounds(challenge, period, today)
    participants = get_challenge_participants(challenge)
    participants = sorted(participants, key=lambda participant: _participant_label(participant).lower())
    participant_labels = [_participant_label(participant) for participant in participants]

    weight_change_pct = _empty_series(period, period_start, period_end, participant_labels)
    calorie_adherence = _empty_series(period, period_start, period_end, participant_labels)
    active_calories = _empty_series(period, period_start, period_end, participant_labels)
    food_logging_days = {participant: 0 for participant in participant_labels}
    weigh_in_days = {participant: 0 for participant in participant_labels}

    series_index = {row["label"]: row for row in weight_change_pct}
    calorie_index = {row["label"]: row for row in calorie_adherence}
    active_index = {row["label"]: row for row in active_calories}

    for participant in participants:
        label = _participant_label(participant)
        participant_stats = _build_participant_stats(participant, period, period_start, period_end)
        food_logging_days[label] = participant_stats["foodLoggingDays"]
        weigh_in_days[label] = participant_stats["weighInDays"]

        for bucket_label, bucket_stats in participant_stats["statsByBucket"].items():
            series_index[bucket_label][label] = bucket_stats["weightChangePct"]
            calorie_index[bucket_label][label] = bucket_stats["calorieAdherence"]
            active_index[bucket_label][label] = bucket_stats["activeCalories"]

    return {
        "period": period,
        "participants": participant_labels,
        "weightChangePct": weight_change_pct,
        "calorieAdherence": calorie_adherence,
        "foodLoggingDays": food_logging_days,
        "activeCalories": active_calories,
        "weighInDays": weigh_in_days,
    }


def build_challenge_stats_document(
    challenge_id: str,
    period: str,
    today: date | None = None,
) -> dict[str, Any]:
    challenge = _challenge_by_id(challenge_id)
    period_start, period_end = _period_bounds(challenge, period, today)
    stats = build_challenge_stats_payload(challenge_id, period, today)
    generated_at = _utc_now()

    return {
        "id": f"challenge_stats__{challenge_id}__{period}",
        "type": "challenge_stats",
        "challengeID": challenge_id,
        "challengeId": challenge_id,
        "period": period,
        "periodStartDate": period_start.isoformat(),
        "periodEndDate": period_end.isoformat(),
        "generatedAt": generated_at,
        "stats": stats,
    }


def upsert_challenge_stats(
    challenge_id: str,
    period: str,
    today: date | None = None,
) -> dict[str, Any]:
    document = build_challenge_stats_document(challenge_id, period, today)
    return _competitions_container().upsert_item(document)


def refresh_active_challenge_stats(today: date | None = None) -> list[dict[str, Any]]:
    saved_documents: list[dict[str, Any]] = []
    today = today or _today()
    for challenge in get_active_challenges():
        if today < _challenge_start(challenge):
            continue
        for period in ("week", "month", "challenge"):
            saved_documents.append(upsert_challenge_stats(challenge["challengeID"], period, today))
    return saved_documents


def get_or_build_challenge_stats(
    challenge_id: str,
    period: str,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or _today()
    period = period.strip().lower()
    if period not in VALID_STATS_PERIODS:
        raise ValueError("period must be one of: week, month, challenge")

    challenge = _challenge_by_id(challenge_id)
    period_start, period_end = _period_bounds(challenge, period, today)
    document = _query_one(
        _competitions_container(),
        (
            "SELECT TOP 1 * FROM c WHERE c.type = 'challenge_stats' "
            "AND c.challengeID = @challengeID AND c.period = @period"
        ),
        [
            {"name": "@challengeID", "value": challenge_id},
            {"name": "@period", "value": period},
        ],
    )
    generated_on = None
    if document and document.get("generatedAt"):
        try:
            generated_on = datetime.fromisoformat(document["generatedAt"].replace("Z", "+00:00")).date()
        except ValueError:
            generated_on = None

    if (
        document
        and document.get("periodStartDate") == period_start.isoformat()
        and document.get("periodEndDate") == period_end.isoformat()
        and generated_on == today
        and isinstance(document.get("stats"), dict)
    ):
        return document["stats"]

    return upsert_challenge_stats(challenge_id, period, today)["stats"]
