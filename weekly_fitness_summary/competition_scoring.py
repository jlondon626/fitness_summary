from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from azure.cosmos import CosmosClient
from dotenv import load_dotenv


load_dotenv()

COMPETITIONS_CONTAINER_DEFAULT = "fitness_competitions"
RAW_CONTAINER_DEFAULT = "fitness_raw"
HEALTH_CONTAINER_DEFAULT = "apple-health-data"


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _database():
    client = CosmosClient.from_connection_string(_required_env("COSMOS_DB_CONNECTION_STRING"))
    return client.get_database_client(_required_env("COSMOS_DB_DATABASE_NAME"))


def _health_database():
    connection_string = os.getenv("COSMOS_DB_HEALTH_CONNECTION_STRING")
    if connection_string:
        client = CosmosClient.from_connection_string(connection_string.strip().strip('"'))
        database_name = _optional_env("COSMOS_DB_HEALTH_DATABASE_NAME", _required_env("COSMOS_DB_DATABASE_NAME"))
        return client.get_database_client(database_name)

    endpoint = os.getenv("COSMOS_HEALTH_ENDPOINT") or os.getenv("COSMOS_ENDPOINT")
    key = os.getenv("COSMOS_HEALTH_KEY") or os.getenv("COSMOS_KEY")
    if endpoint and key:
        client = CosmosClient(endpoint.strip().strip('"'), credential=key.strip().strip('"'))
        database_name = (
            os.getenv("COSMOS_DB_HEALTH_DATABASE_NAME")
            or os.getenv("COSMOS_HEALTH_DATABASE")
            or os.getenv("COSMOS_DATABASE")
            or _required_env("COSMOS_DB_DATABASE_NAME")
        )
        return client.get_database_client(database_name.strip().strip('"'))

    return _database()


def _container(container_name: str):
    return _database().get_container_client(container_name)


def _competitions_container():
    return _container(_optional_env("COSMOS_DB_COMPETITIONS_CONTAINER_NAME", COMPETITIONS_CONTAINER_DEFAULT))


def _raw_container():
    return _container(_optional_env("COSMOS_DB_RAW_CONTAINER_NAME", RAW_CONTAINER_DEFAULT))


def _health_container():
    container_name = _optional_env(
        "COSMOS_DB_HEALTH_CONTAINER_NAME",
        os.getenv("COSMOS_HEALTH_CONTAINER")
        or os.getenv("COSMOS_CONTAINER")
        or HEALTH_CONTAINER_DEFAULT,
    )
    return _health_database().get_container_client(container_name)


def _query_one(container, query: str, parameters: list[dict[str, Any]]) -> dict[str, Any] | None:
    items = list(
        container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
    )
    return items[0] if items else None


def _query_all(container, query: str, parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(
        container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
    )


def get_active_challenges(challenge_id: str | None = None) -> list[dict[str, Any]]:
    container = _competitions_container()

    if challenge_id:
        challenges = _query_all(
            container,
            "SELECT * FROM c WHERE c.type = 'challenge' AND c.challengeID = @challengeID",
            [{"name": "@challengeID", "value": challenge_id.strip()}],
        )
    else:
        challenges = _query_all(
            container,
            "SELECT * FROM c WHERE c.type = 'challenge' AND c.status = 'active' ORDER BY c.startDate DESC",
            [],
        )

    return challenges


def get_active_challenge(challenge_id: str | None = None) -> dict[str, Any]:
    challenges = get_active_challenges(challenge_id)
    if not challenges:
        raise RuntimeError("No active fitness competition challenge found.")
    return challenges[0]


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    user = _query_one(
        _competitions_container(),
        "SELECT TOP 1 * FROM c WHERE c.type = 'user' AND c.userID = @userID",
        [{"name": "@userID", "value": user_id}],
    )
    if user:
        return user

    return _query_one(
        _competitions_container(),
        "SELECT TOP 1 * FROM c WHERE c.type = 'user' AND c.id = @id",
        [{"name": "@id", "value": user_id}],
    )


def _participant_user_id(participant: dict[str, Any]) -> str:
    return (
        participant.get("rawUserID")
        or participant.get("userID")
        or participant.get("displayName")
        or participant.get("participantId")
        or participant["id"]
    )


def _normalise_participant(
    challenge_id: str,
    participant: dict[str, Any] | str,
    user_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(participant, str):
        participant = {
            "id": f"{challenge_id}__{participant}",
            "type": "challenge_participant",
            "challengeID": challenge_id,
            "userID": participant,
            "active": True,
        }

    user_id = _participant_user_id(participant)
    user_profile = user_profile or get_user_profile(user_id) or {}
    participant_id = participant.get("participantId") or participant.get("id") or f"{challenge_id}__{user_id}"

    return {
        **user_profile,
        **participant,
        "challengeID": challenge_id,
        "participantId": participant_id,
        "userID": user_profile.get("userID") or user_id,
        "displayName": participant.get("displayName") or user_profile.get("displayName") or user_id,
        "timezone": participant.get("timezone") or user_profile.get("timezone"),
        "goalWeightKg": participant.get("goalWeightKg") or participant.get("goalWeight") or user_profile.get("goalWeightKg"),
        "averageDailyCalorieTarget": (
            participant.get("averageDailyCalorieTarget")
            or user_profile.get("averageDailyCalorieTarget")
        ),
        "weeklyCalorieTarget": participant.get("weeklyCalorieTarget") or user_profile.get("weeklyCalorieTarget"),
    }


def get_challenge_participants(challenge: dict[str, Any] | str) -> list[dict[str, Any]]:
    if isinstance(challenge, str):
        challenge_id = challenge
        challenge_doc = get_active_challenge(challenge_id)
    else:
        challenge_doc = challenge
        challenge_id = challenge["challengeID"]

    participant_docs = _query_all(
        _competitions_container(),
        (
            "SELECT * FROM c WHERE (c.type = 'participant' OR c.type = 'challenge_participant') "
            "AND c.challengeID = @challengeID AND (NOT IS_DEFINED(c.active) OR c.active = true)"
        ),
        [{"name": "@challengeID", "value": challenge_id}],
    )

    if participant_docs:
        return [_normalise_participant(challenge_id, participant) for participant in participant_docs]

    return [
        _normalise_participant(challenge_id, participant)
        for participant in challenge_doc.get("participants", [])
    ]


def get_scoring_rules(challenge_id: str, scoring_version: str) -> dict[str, Any]:
    rules = _query_one(
        _competitions_container(),
        (
            "SELECT * FROM c WHERE c.type = 'scoring_rules' "
            "AND c.challengeID = @challengeID AND c.scoringVersion = @scoringVersion"
        ),
        [
            {"name": "@challengeID", "value": challenge_id},
            {"name": "@scoringVersion", "value": scoring_version},
        ],
    )
    if not rules:
        raise RuntimeError(f"No scoring rules found for {challenge_id} / {scoring_version}.")
    return rules


def _week_start_for(reference_date: date, week_starts_on: str) -> date:
    week_start_offsets = {
        "MONDAY": 0,
        "TUESDAY": 1,
        "WEDNESDAY": 2,
        "THURSDAY": 3,
        "FRIDAY": 4,
        "SATURDAY": 5,
        "SUNDAY": 6,
    }
    target_weekday = week_start_offsets.get(week_starts_on.upper(), 6)
    days_since_start = (reference_date.weekday() - target_weekday) % 7
    return reference_date - timedelta(days=days_since_start)


def previous_completed_week(challenge: dict[str, Any], today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    current_week_start = _week_start_for(today, challenge.get("weekStartsOn", "SUNDAY"))
    reference_date = current_week_start - timedelta(days=1)
    week_start = _week_start_for(reference_date, challenge.get("weekStartsOn", "SUNDAY"))
    return week_start, week_start + timedelta(days=6)


def _challenge_has_started(challenge: dict[str, Any], today: date | None = None) -> bool:
    today = today or date.today()
    return _challenge_start(challenge) <= today


def get_raw_records(user_id: str, week_start: date, week_end: date) -> list[dict[str, Any]]:
    return _query_all(
        _raw_container(),
        (
            "SELECT * FROM c WHERE c.userID = @userID "
            "AND c.date >= @weekStart AND c.date <= @weekEnd"
        ),
        [
            {"name": "@userID", "value": user_id},
            {"name": "@weekStart", "value": week_start.isoformat()},
            {"name": "@weekEnd", "value": week_end.isoformat()},
        ],
    )


def get_apple_health_records(
    user_id: str,
    participant_id: str,
    week_start: date,
    week_end: date,
) -> list[dict[str, Any]]:
    records = _query_all(
        _health_container(),
        (
            "SELECT * FROM c WHERE c.type = 'apple-health-data' "
            "AND c.userID = @userID AND c.date >= @weekStart AND c.date <= @weekEnd"
        ),
        [
            {"name": "@userID", "value": user_id},
            {"name": "@weekStart", "value": week_start.isoformat()},
            {"name": "@weekEnd", "value": week_end.isoformat()},
        ],
    )

    for record in records:
        record.setdefault("participantId", participant_id)
    return records


def _participant_health_user_id(participant: dict[str, Any]) -> str:
    return (
        participant.get("healthUserID")
        or participant.get("appleHealthUserID")
        or participant.get("userID")
        or participant.get("displayName")
        or participant["participantId"]
    )


def _participant_raw_user_id(participant: dict[str, Any]) -> str:
    return (
        participant.get("rawUserID")
        or participant.get("userID")
        or participant.get("displayName")
        or participant["participantId"]
    )


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _record_active_calories(record: dict[str, Any]) -> float | None:
    if record.get("type") == "apple-health-data":
        value = record.get("active_energy_kcal")
    else:
        value = record.get("activeCalories")

    if value is None:
        return None
    return float(value)


def _has_positive_active_calories(record: dict[str, Any]) -> bool:
    active_calories = _record_active_calories(record)
    return active_calories is not None and active_calories > 0


def build_weekly_metrics(
    participant: dict[str, Any],
    raw_records: list[dict[str, Any]],
    *,
    challenge: dict[str, Any] | None = None,
    week_start: date | None = None,
) -> dict[str, Any]:
    weigh_ins = sorted(
        [record for record in raw_records if record.get("type") == "renpho_daily"],
        key=lambda record: record.get("date", ""),
    )
    food_days = [record for record in raw_records if record.get("type") == "fatsecret_daily"]
    logged_food_days = [record for record in food_days if record.get("logged")]
    active_calorie_days = [
        record
        for record in raw_records
        if record.get("type") in {"active_calories_daily", "apple-health-data"}
        and _has_positive_active_calories(record)
    ]

    weights = [float(record["weightKg"]) for record in weigh_ins if record.get("weightKg") is not None]
    average_bodyweight = _average(weights)
    weekly_weight_change_pct = None
    weight_trend_start_weight = None
    weight_trend_end_weight = None
    weight_trend_method = None

    is_first_challenge_week = (
        challenge is not None
        and week_start is not None
        and week_start == _challenge_start(challenge)
    )

    if is_first_challenge_week and weights and average_bodyweight is not None and weights[0] != 0:
        weight_trend_start_weight = weights[0]
        weight_trend_end_weight = average_bodyweight
        weight_trend_method = "first_challenge_weigh_in_to_first_week_average"
        weekly_weight_change_pct = (average_bodyweight - weights[0]) / weights[0]
    elif len(weights) >= 2 and weights[0] != 0:
        weight_trend_start_weight = weights[0]
        weight_trend_end_weight = weights[-1]
        weight_trend_method = "first_to_last_weigh_in"
        weekly_weight_change_pct = (weights[-1] - weights[0]) / weights[0]

    daily_calorie_target = participant.get("averageDailyCalorieTarget")
    if daily_calorie_target is None and participant.get("weeklyCalorieTarget") is not None:
        daily_calorie_target = float(participant["weeklyCalorieTarget"]) / 7
    daily_calorie_target = float(daily_calorie_target) if daily_calorie_target is not None else None
    average_daily_calories = _average(
        [float(record.get("calories", 0)) for record in logged_food_days]
    )
    average_daily_calorie_variance = None

    if daily_calorie_target is not None and average_daily_calories is not None:
        average_daily_calorie_variance = average_daily_calories - daily_calorie_target

    total_active_calories = sum(_record_active_calories(record) or 0 for record in active_calorie_days)
    weekly_active_calories_per_kg = None
    if average_bodyweight and average_bodyweight > 0:
        weekly_active_calories_per_kg = total_active_calories / average_bodyweight

    return {
        "weeklyWeightChangePct": weekly_weight_change_pct,
        "weightTrendStartWeightKg": round(weight_trend_start_weight, 2) if weight_trend_start_weight is not None else None,
        "weightTrendEndWeightKg": round(weight_trend_end_weight, 2) if weight_trend_end_weight is not None else None,
        "weightTrendMethod": weight_trend_method,
        "daysWithWeighIn": len(weigh_ins),
        "averageBodyweightKg": round(average_bodyweight, 2) if average_bodyweight is not None else None,
        "averageDailyCalories": round(average_daily_calories, 1) if average_daily_calories is not None else None,
        "averageDailyCalorieVariance": (
            round(average_daily_calorie_variance, 1)
            if average_daily_calorie_variance is not None
            else None
        ),
        "daysWithFoodLogged": len(logged_food_days),
        "activeCalorieDays": len(active_calorie_days),
        "totalWeeklyActiveCalories": round(total_active_calories),
        "weeklyActiveCaloriesPerKg": (
            round(weekly_active_calories_per_kg, 2)
            if weekly_active_calories_per_kg is not None
            else None
        ),
    }


def _data_point_count(metric_name: str, metrics: dict[str, Any]) -> int:
    if metric_name == "weighIns":
        return int(metrics.get("daysWithWeighIn") or 0)
    if metric_name == "activeCalorieDays":
        return int(metrics.get("activeCalorieDays") or 0)
    if metric_name in {"foodLoggedDays", "foodLogging"}:
        return int(metrics.get("daysWithFoodLogged") or 0)
    return 0


def _normalised_pct_bands(bands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not bands or not all("minPct" in band and "maxPct" in band for band in bands):
        return bands

    sorted_bands = [dict(band) for band in sorted(bands, key=lambda band: float(band["minPct"]))]
    for index in range(1, len(sorted_bands)):
        previous_max = float(sorted_bands[index - 1]["maxPct"])
        current_min = float(sorted_bands[index]["minPct"])
        if previous_max < current_min:
            sorted_bands[index]["minPct"] = previous_max
    return sorted_bands


def _score_from_bands(metric_value: float | int | None, bands: list[dict[str, Any]]) -> tuple[int, dict[str, Any] | None]:
    if metric_value is None:
        return 0, None

    for band in _normalised_pct_bands(bands):
        if "maxAbsVariance" in band and abs(float(metric_value)) <= float(band["maxAbsVariance"]):
            return int(band["points"]), band
        if "minDays" in band and int(metric_value) >= int(band["minDays"]):
            return int(band["points"]), band
        if "minValue" in band and float(metric_value) >= float(band["minValue"]):
            return int(band["points"]), band
        if "minPct" in band and "maxPct" in band:
            min_pct = float(band["minPct"])
            max_pct = float(band["maxPct"])
            if min_pct <= float(metric_value) < max_pct:
                return int(band["points"]), band

    return 0, None


def score_category(
    category_name: str,
    category_rules: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    metric_name = category_rules.get("metric")
    metric_value = metrics.get(metric_name)
    raw_points, matched_band = _score_from_bands(metric_value, category_rules.get("bands", []))
    capped_points = min(raw_points, int(category_rules.get("maxPoints", raw_points)))

    min_data_points = category_rules.get("minDataPoints")
    data_point_type = category_rules.get("dataPointType")

    if metric_name == "averageDailyCalorieVariance" and data_point_type is None:
        data_point_type = "foodLoggedDays"
        if min_data_points is None:
            min_data_points = 5

    data_points = _data_point_count(data_point_type, metrics) if data_point_type else None

    cap_applied = None
    if min_data_points is not None and data_points is not None and data_points < int(min_data_points):
        cap_applied = {
            "reason": "below_min_data_points",
            "dataPoints": data_points,
            "minDataPoints": int(min_data_points),
            "maxPointsIfBelowMinDataPoints": int(category_rules.get("maxPointsIfBelowMinDataPoints", 0)),
        }
        capped_points = min(capped_points, int(category_rules.get("maxPointsIfBelowMinDataPoints", 0)))

    return {
        "category": category_name,
        "metric": metric_name,
        "metricValue": metric_value,
        "points": capped_points,
        "maxPoints": int(category_rules.get("maxPoints", 0)),
        "matchedBand": matched_band,
        "dataPoints": data_points,
        "minDataPoints": min_data_points,
        "capApplied": cap_applied,
    }


def _format_pct(value: float | int | None) -> str | None:
    if value is None:
        return None
    return f"{abs(float(value)) * 100:.2f}%"


def _format_number(value: float | int | None, suffix: str = "") -> str | None:
    if value is None:
        return None
    value = float(value)
    if value.is_integer():
        return f"{int(value)}{suffix}"
    return f"{value:.1f}{suffix}"


def explain_category(category_name: str, category_score: dict[str, Any], metrics: dict[str, Any]) -> str:
    metric_value = category_score.get("metricValue")

    if category_name == "weightTrend":
        pct = _format_pct(metric_value)
        if pct is None:
            return "Insufficient weigh-in data to calculate a weekly weight trend."
        direction = "Lost" if metric_value < 0 else "Gained" if metric_value > 0 else "Maintained"
        if metrics.get("weightTrendMethod") == "first_challenge_weigh_in_to_first_week_average":
            start_weight = _format_number(metrics.get("weightTrendStartWeightKg"), "kg")
            end_weight = _format_number(metrics.get("weightTrendEndWeightKg"), "kg")
            return (
                f"{direction} {pct} bodyweight from challenge start weigh-in "
                f"({start_weight}) to first-week average ({end_weight})."
            )
        return f"{direction} {pct} bodyweight."

    if category_name == "calorieAdherence":
        variance = metrics.get("averageDailyCalorieVariance")
        if variance is None:
            return "No logged food days available to calculate calorie adherence."
        if variance < 0:
            return f"Average calories were {abs(round(variance))} kcal below target."
        if variance > 0:
            return f"Average calories were {round(variance)} kcal above target."
        return "Average calories matched the target."

    if category_name == "foodLogging":
        days = metrics.get("daysWithFoodLogged", 0)
        return f"Logged food {days} day{'s' if days != 1 else ''}."

    if category_name == "weighIns":
        days = metrics.get("daysWithWeighIn", 0)
        return f"Weighed in {days} day{'s' if days != 1 else ''}."

    if category_name == "activeCalories":
        active_days = metrics.get("activeCalorieDays", 0)
        total = metrics.get("totalWeeklyActiveCalories", 0)
        per_kg = metrics.get("weeklyActiveCaloriesPerKg")
        if per_kg is None:
            return f"Logged active calories {active_days} day{'s' if active_days != 1 else ''}, but bodyweight data was missing."
        return (
            f"Logged {total} active kcal over {active_days} day{'s' if active_days != 1 else ''} "
            f"({_format_number(per_kg, ' kcal/kg')})."
        )

    metric_name = category_score.get("metric")
    return f"{metric_name} was {metric_value}."


def build_points_summary(category_scores: dict[str, dict[str, Any]], total_points: int) -> dict[str, int]:
    points = {
        category_name: int(category_score["points"])
        for category_name, category_score in category_scores.items()
    }
    points["total"] = int(total_points)
    return points


def build_explanations(category_scores: dict[str, dict[str, Any]], metrics: dict[str, Any]) -> dict[str, str]:
    return {
        category_name: explain_category(category_name, category_score, metrics)
        for category_name, category_score in category_scores.items()
    }


def build_caps_applied(category_scores: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"category": category_name, **category_score["capApplied"]}
        for category_name, category_score in category_scores.items()
        if category_score.get("capApplied")
    ]


def validate_scoring_rules(scoring_rules: dict[str, Any]) -> list[str]:
    warnings: list[str] = []

    for category_name, category_rules in scoring_rules.get("categories", {}).items():
        if category_rules.get("metric") != "weeklyWeightChangePct":
            continue

        sorted_bands = sorted(category_rules.get("bands", []), key=lambda band: float(band["minPct"]))
        for previous, current in zip(sorted_bands, sorted_bands[1:]):
            previous_max = float(previous["maxPct"])
            current_min = float(current["minPct"])
            if previous_max < current_min:
                warnings.append(
                    f"{category_name} has a gap between {previous_max} and {current_min}."
                )
            if previous_max > current_min:
                warnings.append(
                    f"{category_name} has an overlap between {current_min} and {previous_max}."
                )

    calorie_rules = scoring_rules.get("categories", {}).get("calorieAdherence", {})
    if "minDataPoints" not in calorie_rules:
        warnings.append("calorieAdherence has no minDataPoints guard; using default 5 logged days.")

    return warnings


def _score_total(score_document: dict[str, Any]) -> int:
    points = score_document.get("points") or {}
    if "total" in points:
        return int(points["total"])
    return int(score_document.get("totalPoints", 0))


def get_score_records_to_date(challenge_id: str, week_end: date) -> list[dict[str, Any]]:
    return _query_all(
        _competitions_container(),
        (
            "SELECT * FROM c WHERE c.type = 'weekly_score' "
            "AND c.challengeID = @challengeID AND c.weekEndDate <= @weekEnd"
        ),
        [
            {"name": "@challengeID", "value": challenge_id},
            {"name": "@weekEnd", "value": week_end.isoformat()},
        ],
    )


def get_score_records_between(challenge_id: str, period_start: date, period_end: date) -> list[dict[str, Any]]:
    return _query_all(
        _competitions_container(),
        (
            "SELECT * FROM c WHERE c.type = 'weekly_score' "
            "AND c.challengeID = @challengeID "
            "AND c.weekStartDate >= @periodStart "
            "AND c.weekEndDate <= @periodEnd"
        ),
        [
            {"name": "@challengeID", "value": challenge_id},
            {"name": "@periodStart", "value": period_start.isoformat()},
            {"name": "@periodEnd", "value": period_end.isoformat()},
        ],
    )


def _weekly_wins_to_date(score_documents: list[dict[str, Any]]) -> dict[str, int]:
    scores_by_week: dict[str, list[dict[str, Any]]] = {}
    wins_by_participant: dict[str, int] = {}

    for score_document in score_documents:
        scores_by_week.setdefault(score_document["weekStartDate"], []).append(score_document)

    for week_scores in scores_by_week.values():
        max_points = max(_score_total(score_document) for score_document in week_scores)
        winning_scores = [
            score_document
            for score_document in week_scores
            if _score_total(score_document) == max_points
        ]
        if len(winning_scores) != 1:
            continue
        participant_id = winning_scores[0]["participantId"]
        wins_by_participant[participant_id] = wins_by_participant.get(participant_id, 0) + 1

    return wins_by_participant


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month_start(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _month_end(value: date) -> date:
    return _next_month_start(value) - timedelta(days=1)


def _challenge_start(challenge: dict[str, Any]) -> date:
    return _parse_date(challenge["startDate"])


def _challenge_end(challenge: dict[str, Any]) -> date:
    return _parse_date(challenge["endDate"])


def _final_score_cutoff(challenge: dict[str, Any]) -> date:
    challenge_end = _challenge_end(challenge)
    if challenge.get("weekStartsOn", "SUNDAY").upper() == "SUNDAY" and challenge_end.weekday() == 6:
        return challenge_end - timedelta(days=1)
    return challenge_end


def _period_points_field(leaderboard_kind: str) -> str:
    return {
        "week": "weeklyPoints",
        "month": "monthlyPoints",
        "final": "finalPoints",
    }[leaderboard_kind]


def _leaderboard_type(leaderboard_kind: str) -> str:
    return {
        "week": "leaderboard_week",
        "month": "leaderboard_month",
        "final": "leaderboard_final",
    }[leaderboard_kind]


def _leaderboard_id(challenge_id: str, leaderboard_kind: str, period_start: date) -> str:
    return f"{challenge_id}__{period_start.isoformat()}__leaderboard_{leaderboard_kind}"


def _scores_by_participant(score_documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_participant: dict[str, dict[str, Any]] = {}

    for score_document in score_documents:
        participant_id = score_document["participantId"]
        existing = by_participant.setdefault(
            participant_id,
            {
                "participantId": participant_id,
                "displayName": score_document.get("displayName"),
                "periodPoints": 0,
                "scoreDocumentIds": [],
            },
        )
        existing["periodPoints"] += _score_total(score_document)
        existing["scoreDocumentIds"].append(score_document["id"])

    return by_participant


def build_leaderboard_document(
    challenge: dict[str, Any],
    period_start: date,
    period_end: date,
    period_scores: list[dict[str, Any]],
    score_records_to_date: list[dict[str, Any]],
    *,
    leaderboard_kind: str = "week",
    published_at: str | None = None,
) -> dict[str, Any]:
    challenge_id = challenge["challengeID"]
    season_points: dict[str, int] = {}

    for score_document in score_records_to_date:
        participant_id = score_document["participantId"]
        season_points[participant_id] = season_points.get(participant_id, 0) + _score_total(score_document)

    weekly_wins = _weekly_wins_to_date(score_records_to_date)
    period_scores_by_participant = _scores_by_participant(period_scores)
    ordered_scores = sorted(
        period_scores_by_participant.values(),
        key=lambda participant_score: (
            -participant_score["periodPoints"],
            -season_points.get(participant_score["participantId"], 0),
            (participant_score.get("displayName") or participant_score["participantId"]).lower(),
        ),
    )

    rankings = []
    previous_points = None
    previous_rank = 0
    period_points_field = _period_points_field(leaderboard_kind)

    for index, participant_score in enumerate(ordered_scores, start=1):
        period_points = participant_score["periodPoints"]
        rank = previous_rank if period_points == previous_points else index
        previous_points = period_points
        previous_rank = rank

        participant_id = participant_score["participantId"]
        ranking = {
            "rank": rank,
            "participantId": participant_id,
            "displayName": participant_score.get("displayName"),
            period_points_field: period_points,
            "periodPoints": period_points,
            "seasonPointsToDate": season_points.get(participant_id, period_points),
            "weeklyWinsToDate": weekly_wins.get(participant_id, 0),
            "scoreDocumentIds": participant_score["scoreDocumentIds"],
        }
        if leaderboard_kind == "week":
            ranking["weeklyPoints"] = period_points
        rankings.append(ranking)

    published_at = published_at or _utc_now()

    top_score = rankings[0]["periodPoints"] if rankings else None
    bottom_score = rankings[-1]["periodPoints"] if len(rankings) > 1 else None

    return {
        "id": _leaderboard_id(challenge_id, leaderboard_kind, period_start),
        "type": _leaderboard_type(leaderboard_kind),
        "challengeID": challenge_id,
        "challengeId": challenge_id,
        "leaderboardKind": leaderboard_kind,
        "periodStartDate": period_start.isoformat(),
        "periodEndDate": period_end.isoformat(),
        "weekStartDate": period_start.isoformat() if leaderboard_kind == "week" else None,
        "weekEndDate": period_end.isoformat() if leaderboard_kind == "week" else None,
        "status": "published",
        "version": 1,
        "rankings": rankings,
        "rows": rankings,
        "winnerParticipantId": _unique_participant_at_score(rankings, top_score),
        "loserParticipantId": _unique_participant_at_score(rankings, bottom_score),
        "telegramMessageId": None,
        "publishedAt": published_at,
    }


def _merge_score_records(
    existing_scores: list[dict[str, Any]],
    saved_documents: list[dict[str, Any]],
    challenge_id: str,
) -> list[dict[str, Any]]:
    score_records_by_id = {
        score_document["id"]: score_document
        for score_document in [*existing_scores, *saved_documents]
        if score_document.get("type") == "weekly_score"
        and score_document.get("challengeID") == challenge_id
    }
    return list(score_records_by_id.values())


def _scores_in_period(
    score_records: list[dict[str, Any]],
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    return [
        score_document
        for score_document in score_records
        if period_start <= _parse_date(score_document["weekEndDate"]) <= period_end
    ]


def _unique_participant_at_score(
    rankings: list[dict[str, Any]],
    score: int | None,
) -> str | None:
    if score is None:
        return None
    matching_rankings = [
        ranking
        for ranking in rankings
        if int(ranking.get("periodPoints", 0)) == score
    ]
    if len(matching_rankings) != 1:
        return None
    return matching_rankings[0]["participantId"]


def build_leaderboard_documents_for_periods(
    challenge: dict[str, Any],
    week_start: date,
    week_end: date,
    score_records_to_date: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    challenge_start = _challenge_start(challenge)
    challenge_end = _challenge_end(challenge)
    month_start = max(_month_start(week_end), challenge_start)
    period_end_to_date = min(week_end, challenge_end)

    period_specs = [
        ("week", week_start, week_end),
        ("month", month_start, period_end_to_date),
    ]

    if week_end >= _final_score_cutoff(challenge):
        period_specs.append(("final", challenge_start, challenge_end))

    leaderboard_documents = []
    for leaderboard_kind, period_start, period_end in period_specs:
        period_scores = _scores_in_period(score_records_to_date, period_start, period_end)
        if not period_scores:
            continue

        leaderboard_documents.append(
            build_leaderboard_document(
                challenge,
                period_start,
                period_end,
                period_scores,
                score_records_to_date,
                leaderboard_kind=leaderboard_kind,
            )
        )

    return leaderboard_documents


def apply_weekly_leaderboard_tallies_to_scores(
    score_records: list[dict[str, Any]],
    leaderboard_document: dict[str, Any],
) -> list[dict[str, Any]]:
    if leaderboard_document.get("leaderboardKind") != "week":
        return []

    score_records_by_id = {
        score_document["id"]: score_document
        for score_document in score_records
        if score_document.get("type") == "weekly_score"
    }
    updated_scores: list[dict[str, Any]] = []

    for ranking in leaderboard_document.get("rankings", []):
        running_tally = {
            "rank": ranking.get("rank"),
            "weeklyPoints": ranking.get("weeklyPoints"),
            "seasonPointsToDate": ranking.get("seasonPointsToDate"),
            "weeklyWinsToDate": ranking.get("weeklyWinsToDate"),
            "leaderboardId": leaderboard_document.get("id"),
            "leaderboardKind": leaderboard_document.get("leaderboardKind"),
            "periodStartDate": leaderboard_document.get("periodStartDate"),
            "periodEndDate": leaderboard_document.get("periodEndDate"),
        }

        for score_document_id in ranking.get("scoreDocumentIds", []):
            score_document = score_records_by_id.get(score_document_id)
            if not score_document:
                continue

            score_document.update(
                {
                    "latestRank": ranking.get("rank"),
                    "latestLeaderboardId": leaderboard_document.get("id"),
                    "seasonPointsToDate": ranking.get("seasonPointsToDate"),
                    "weeklyWinsToDate": ranking.get("weeklyWinsToDate"),
                    "runningTally": running_tally,
                }
            )
            updated_scores.append(score_document)

    return updated_scores


def build_weekly_score_document(
    challenge: dict[str, Any],
    participant: dict[str, Any],
    scoring_rules: dict[str, Any],
    week_start: date,
    week_end: date,
    raw_records: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics = build_weekly_metrics(participant, raw_records, challenge=challenge, week_start=week_start)
    category_scores = {
        category_name: score_category(category_name, category_rules, metrics)
        for category_name, category_rules in scoring_rules.get("categories", {}).items()
    }
    total_points = sum(category_score["points"] for category_score in category_scores.values())
    max_points = int(scoring_rules.get("maxPoints", total_points))

    if scoring_rules.get("aggregation", {}).get("capAtMaxPoints", True):
        total_points = min(total_points, max_points)

    challenge_id = challenge["challengeID"]
    participant_id = participant["participantId"]
    generated_at = _utc_now()

    return {
        "id": f"weekly_score__{challenge_id}__{participant_id}__{week_start.isoformat()}",
        "type": "weekly_score",
        "challengeID": challenge_id,
        "challengeId": challenge_id,
        "participantId": participant_id,
        "displayName": participant.get("displayName"),
        "scoringVersion": scoring_rules.get("scoringVersion"),
        "status": "draft",
        "version": 1,
        "weekStartDate": week_start.isoformat(),
        "weekEndDate": week_end.isoformat(),
        "points": build_points_summary(category_scores, total_points),
        "explanations": build_explanations(category_scores, metrics),
        "capsApplied": build_caps_applied(category_scores),
        "generatedAt": generated_at,
        "publishedAt": None,
        "totalPoints": total_points,
        "maxPoints": max_points,
        "categoryScores": category_scores,
        "metrics": metrics,
        "rawRecordCount": len(raw_records),
        "scoringWarnings": validate_scoring_rules(scoring_rules),
        "computedAt": generated_at,
    }


def score_week(
    week_start: date | None = None,
    *,
    challenge_id: str | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    challenge = get_active_challenge(challenge_id)
    if week_start is None and not _challenge_has_started(challenge, today):
        return []

    if week_start is None:
        week_start, week_end = previous_completed_week(challenge, today)
    else:
        week_end = week_start + timedelta(days=6)

    if week_end < _challenge_start(challenge):
        return []

    scoring_rules = get_scoring_rules(challenge["challengeID"], challenge["scoringVersion"])
    participants = get_challenge_participants(challenge)
    competition_container = _competitions_container()

    saved_scores: list[dict[str, Any]] = []
    for participant in participants:
        raw_records = get_raw_records(_participant_raw_user_id(participant), week_start, week_end)
        raw_records.extend(
            get_apple_health_records(
                _participant_health_user_id(participant),
                participant["participantId"],
                week_start,
                week_end,
            )
        )
        score_document = build_weekly_score_document(
            challenge,
            participant,
            scoring_rules,
            week_start,
            week_end,
            raw_records,
        )
        saved_scores.append(competition_container.upsert_item(score_document))

    score_records_to_date = _merge_score_records(
        get_score_records_to_date(challenge["challengeID"], week_end),
        saved_scores,
        challenge["challengeID"],
    )
    for leaderboard_document in build_leaderboard_documents_for_periods(
        challenge,
        week_start,
        week_end,
        score_records_to_date,
    ):
        for updated_score in apply_weekly_leaderboard_tallies_to_scores(score_records_to_date, leaderboard_document):
            saved_scores.append(competition_container.upsert_item(updated_score))
        saved_scores.append(competition_container.upsert_item(leaderboard_document))

    return saved_scores


def score_active_challenges(today: date | None = None) -> list[dict[str, Any]]:
    saved_scores: list[dict[str, Any]] = []

    for challenge in get_active_challenges():
        if not _challenge_has_started(challenge, today):
            continue

        week_start, week_end = previous_completed_week(challenge, today)
        if week_end < _challenge_start(challenge):
            continue

        scoring_rules = get_scoring_rules(challenge["challengeID"], challenge["scoringVersion"])
        participants = get_challenge_participants(challenge)
        competition_container = _competitions_container()

        for participant in participants:
            raw_records = get_raw_records(_participant_raw_user_id(participant), week_start, week_end)
            raw_records.extend(
                get_apple_health_records(
                    _participant_health_user_id(participant),
                    participant["participantId"],
                    week_start,
                    week_end,
                )
            )
            score_document = build_weekly_score_document(
                challenge,
                participant,
                scoring_rules,
                week_start,
                week_end,
                raw_records,
            )
            saved_scores.append(competition_container.upsert_item(score_document))

        score_records_to_date = _merge_score_records(
            get_score_records_to_date(challenge["challengeID"], week_end),
            saved_scores,
            challenge["challengeID"],
        )
        for leaderboard_document in build_leaderboard_documents_for_periods(
            challenge,
            week_start,
            week_end,
            score_records_to_date,
        ):
            for updated_score in apply_weekly_leaderboard_tallies_to_scores(score_records_to_date, leaderboard_document):
                saved_scores.append(competition_container.upsert_item(updated_score))
            saved_scores.append(competition_container.upsert_item(leaderboard_document))

    return saved_scores
