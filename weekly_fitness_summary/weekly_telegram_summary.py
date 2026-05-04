from __future__ import annotations
import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import telegram
from azure.cosmos import CosmosClient
from dotenv import load_dotenv
from openai import AsyncAzureOpenAI

from collections import defaultdict

KEY_LIFTS = {
    "Bench Press",
    "Incline Barbell Press",
    "Squat",
    "Deadlift",
    "Romanian Deadlift",
    "Pull-Up",
    "Dumbbell Shoulder Press",
    "Overhead Dumbbell Press",
}

try:
    from .weekly_avg import RenphoScalesData
    from .constants import goal_weight, phase_1_weekly_calories, starting_weight
    from .fatsecret import (
        get_calories_and_protein_summary,
        get_food_diary_entries_for_last_7_days,
    )
except ImportError:
    from weekly_avg import RenphoScalesData
    from constants import goal_weight, phase_1_weekly_calories, starting_weight
    from fatsecret import (
        get_calories_and_protein_summary,
        get_food_diary_entries_for_last_7_days,
    )

load_dotenv()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _optional_env(name: str, default: str) -> str:
    value = os.getenv(name)
    if not value:
        return default
    return value.strip().strip('"')

def build_compact_ai_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Converts verbose raw Cosmos workout records into a compact payload suitable
    for Azure OpenAI.

    Keeps:
    - report/target data
    - existing computed summaries
    - training counts
    - best sets for key lifts
    - compact session summaries

    Removes:
    - ids
    - device ids
    - timestamps per set
    - nested raw Cosmos structure
    """

    records = payload.get("cosmos_records", [])

    period_start = payload.get("previous_week", {}).get("start_date")
    period_end = payload.get("previous_week", {}).get("end_date")

    current_week_records = [
        r for r in records
        if period_start <= r.get("dateISO", "") <= period_end
    ]

    total_sets = 0
    total_reps = 0
    total_volume = 0.0

    sessions = []
    exercise_summary = defaultdict(lambda: {
        "sets": 0,
        "reps": 0,
        "volume": 0.0,
        "best_set": None,
    })

    for session in current_week_records:
        session_sets = 0
        session_reps = 0
        session_volume = 0.0
        session_exercises = []

        for exercise in session.get("exercises", []):
            name = exercise.get("name")
            sets = exercise.get("sets", [])

            if not sets:
                continue

            exercise_sets = 0
            exercise_reps = 0
            exercise_volume = 0.0
            best_set = None

            for s in sets:
                weight = float(s.get("weightKg") or 0)
                reps = int(s.get("reps") or 0)
                volume = weight * reps

                exercise_sets += 1
                exercise_reps += reps
                exercise_volume += volume

                candidate = {
                    "weight_kg": weight,
                    "reps": reps,
                    "volume": volume,
                }

                if best_set is None:
                    best_set = candidate
                else:
                    # Prefer heavier set; use reps as tie-breaker
                    if (
                        candidate["weight_kg"] > best_set["weight_kg"]
                        or (
                            candidate["weight_kg"] == best_set["weight_kg"]
                            and candidate["reps"] > best_set["reps"]
                        )
                    ):
                        best_set = candidate

            total_sets += exercise_sets
            total_reps += exercise_reps
            total_volume += exercise_volume

            session_sets += exercise_sets
            session_reps += exercise_reps
            session_volume += exercise_volume

            exercise_summary[name]["sets"] += exercise_sets
            exercise_summary[name]["reps"] += exercise_reps
            exercise_summary[name]["volume"] += exercise_volume

            existing_best = exercise_summary[name]["best_set"]
            if existing_best is None:
                exercise_summary[name]["best_set"] = best_set
            elif best_set is not None and (
                best_set["weight_kg"] > existing_best["weight_kg"]
                or (
                    best_set["weight_kg"] == existing_best["weight_kg"]
                    and best_set["reps"] > existing_best["reps"]
                )
            ):
                exercise_summary[name]["best_set"] = best_set

            session_exercises.append({
                "name": name,
                "sets": exercise_sets,
                "reps": exercise_reps,
                "volume_kg": round(exercise_volume, 1),
                "best_set": format_set(best_set),
            })

        sessions.append({
            "date": session.get("dateISO"),
            "title": session.get("title"),
            "sets": session_sets,
            "reps": session_reps,
            "volume_kg": round(session_volume, 1),
            "exercises": session_exercises,
        })

    notable_lifts = []

    for exercise_name in KEY_LIFTS:
        if exercise_name in exercise_summary:
            data = exercise_summary[exercise_name]
            notable_lifts.append({
                "exercise": exercise_name,
                "sets": data["sets"],
                "reps": data["reps"],
                "volume_kg": round(data["volume"], 1),
                "best_set": format_set(data["best_set"]),
            })

    compact_payload = {
        "report_date": payload.get("report_date"),
        "period": payload.get("previous_week"),
        "targets": payload.get("targets"),
        "computed_summaries": payload.get("computed_summaries"),
        "training_summary": {
            "sessions_completed": len(current_week_records),
            "session_titles": [s.get("title") for s in current_week_records],
            "total_sets": total_sets,
            "total_reps": total_reps,
            "total_volume_kg": round(total_volume, 1),
            "notable_lifts": notable_lifts,
            "sessions": sessions,
        },
        "instruction": (
            "Write a concise weekly fitness summary. Comment on weight trend, "
            "calorie/protein adherence, training consistency, and any notable lifts. "
            "Be practical and direct. Do not overstate conclusions where food logging "
            "is incomplete."
        ),
    }

    return compact_payload


def format_set(set_data: dict[str, Any] | None) -> str | None:
    if not set_data:
        return None

    weight = set_data["weight_kg"]
    reps = set_data["reps"]

    if weight.is_integer():
        weight = int(weight)

    return f"{weight}kg x {reps}"


def format_change(current, previous, units):
    change = current - previous
    direction = "down" if change < 0 else "up"
    return f"{current:.2f}{units} ({direction} {abs(change):.2f}{units} vs last week)"


def build_weight_summary_message(today: date | None = None) -> str:
    email = _required_env("MY_EMAIL")
    password = _required_env("MY_PASSWORD")
    renpho_data = RenphoScalesData(email, password)

    today = today or datetime.now().date()
    yesterday = today - timedelta(days=1)
    last_week = today - timedelta(days=8)

    weight_now = renpho_data.get_rolling_weekly_avg(yesterday, "weight")
    weight_prev = renpho_data.get_rolling_weekly_avg(last_week, "weight")

    bf_now = renpho_data.get_rolling_weekly_avg(yesterday, "bodyfat")
    bf_prev = renpho_data.get_rolling_weekly_avg(last_week, "bodyfat")

    lines = ["Weekly fitness summary"]
    lines.append(f"Date: {today.strftime('%d/%m/%Y')}")

    if weight_now is not None and weight_prev is not None:
        lines.append(f"Weight: {format_change(weight_now, weight_prev, 'kg')}")
        cumulative_change = weight_now - starting_weight
        target_change_since_start = goal_weight - starting_weight
        proportion_of_goal = (cumulative_change / target_change_since_start) * 100 if target_change_since_start != 0 else 0
        lines.append(f"Cumulative change since start: {cumulative_change:.2f}kg ({proportion_of_goal:.1f}% of the way to goal)")
    else:
        lines.append("Weight: insufficient Renpho data")

    if bf_now is not None and bf_prev is not None:
        lines.append(f"Body fat: {format_change(bf_now, bf_prev, '%')}")
    else:
        lines.append("Body fat: insufficient Renpho data")

    return "\n".join(lines)


def build_food_summary_message(selected_date: date | None = None) -> str:
    selected_date = selected_date or (date.today() - timedelta(days=1))
    food_diary_entries = get_food_diary_entries_for_last_7_days(selected_date)
    nutrition_summary = get_calories_and_protein_summary(food_diary_entries)
    calorie_difference = (
        nutrition_summary["total_calories"]
        - phase_1_weekly_calories * nutrition_summary["logged_day_count"]
    )
    calorie_difference_label = "surplus" if calorie_difference > 0 else "deficit"

    return (
        "Food diary summary\n"
        f"Average daily calories over the last 7 days: {nutrition_summary['average_daily_calories']:.2f}\n"
        f"Average daily protein over the last 7 days: {nutrition_summary['average_daily_protein']:.2f}g\n"
        f"Total calorie {calorie_difference_label}: {abs(calorie_difference):.0f} calories\n"
        f"(Based on {nutrition_summary['logged_day_count']} logged days out of 7)"
    )


def _compact_for_prompt(value: Any, max_string_length: int = 500) -> Any:
    if isinstance(value, dict):
        return {
            key: _compact_for_prompt(item, max_string_length)
            for key, item in value.items()
            if not key.startswith("_")
        }
    if isinstance(value, list):
        return [_compact_for_prompt(item, max_string_length) for item in value[:50]]
    if isinstance(value, str) and len(value) > max_string_length:
        return f"{value[:max_string_length]}..."
    return value


def get_cosmos_fitness_records() -> list[dict[str, Any]]:
    connection_string = _required_env("COSMOS_DB_CONNECTION_STRING")
    database_name = _required_env("COSMOS_DB_DATABASE_NAME")
    container_name = _required_env("COSMOS_DB_CONTAINER_NAME")
    item_limit = max(1, min(_optional_int_env("COSMOS_DB_AI_ITEM_LIMIT", 100), 200))
    query = os.getenv(
        "COSMOS_DB_AI_QUERY",
        f"SELECT TOP {item_limit} * FROM c ORDER BY c._ts DESC",
    )

    client = CosmosClient.from_connection_string(connection_string)
    database = client.get_database_client(database_name)
    container = database.get_container_client(container_name)

    records = list(
        container.query_items(
            query=query,
            enable_cross_partition_query=True,
        )
    )
    return [_compact_for_prompt(record) for record in records[:item_limit]]


def _cosmos_container(container_name: str):
    client = CosmosClient.from_connection_string(_required_env("COSMOS_DB_CONNECTION_STRING"))
    database = client.get_database_client(_required_env("COSMOS_DB_DATABASE_NAME"))
    return database.get_container_client(container_name)


def _query_cosmos(container_name: str, query: str, parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    container = _cosmos_container(container_name)
    return list(
        container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


LEADERBOARD_KINDS = {"week", "month", "final"}
FORFEIT_KEYS_BY_LEADERBOARD_KIND = {
    "week": "weekly",
    "month": "monthly",
    "final": "championship",
}


def _normalise_leaderboard_kind(leaderboard_kind: str | None = None) -> str:
    selected_kind = leaderboard_kind or "week"
    selected_kind = selected_kind.strip().strip('"').lower()
    if selected_kind not in LEADERBOARD_KINDS:
        raise RuntimeError("leaderboard kind must be one of: week, month, final")
    return selected_kind


def get_latest_competition_leaderboard(
    leaderboard_kind: str | None = None,
    today: date | None = None,
) -> dict[str, Any] | None:
    today = today or date.today()
    container_name = _optional_env("COSMOS_DB_COMPETITIONS_CONTAINER_NAME", "fitness_competitions")
    leaderboard_kind = _normalise_leaderboard_kind(leaderboard_kind)
    leaderboard_type = f"leaderboard_{leaderboard_kind}"

    leaderboards = _query_cosmos(
        container_name,
        (
            "SELECT TOP 10 * FROM c WHERE c.type = @leaderboardType "
            "AND c.periodEndDate <= @today "
            "ORDER BY c.periodEndDate DESC"
        ),
        [
            {"name": "@leaderboardType", "value": leaderboard_type},
            {"name": "@today", "value": today.isoformat()},
        ],
    )
    for leaderboard in leaderboards:
        challenge_id = leaderboard.get("challengeID") or leaderboard.get("challengeId")
        challenge = get_competition_challenge(challenge_id) if challenge_id else None
        challenge_start = challenge.get("startDate") if challenge else None
        period_start = leaderboard.get("periodStartDate") or leaderboard.get("weekStartDate")
        if challenge_start and period_start and period_start < challenge_start:
            continue
        return _compact_for_prompt(leaderboard)
    return None


def get_competition_challenge(challenge_id: str) -> dict[str, Any] | None:
    container_name = _optional_env("COSMOS_DB_COMPETITIONS_CONTAINER_NAME", "fitness_competitions")
    challenges = _query_cosmos(
        container_name,
        "SELECT TOP 1 * FROM c WHERE c.type = 'challenge' AND c.challengeID = @challengeID",
        [{"name": "@challengeID", "value": challenge_id}],
    )
    return _compact_for_prompt(challenges[0]) if challenges else None


def get_selected_competition_challenge() -> dict[str, Any] | None:
    container_name = _optional_env("COSMOS_DB_COMPETITIONS_CONTAINER_NAME", "fitness_competitions")
    challenges = _query_cosmos(
        container_name,
        "SELECT TOP 1 * FROM c WHERE c.type = 'challenge' AND c.status = 'active' ORDER BY c.startDate DESC",
        [],
    )
    return _compact_for_prompt(challenges[0]) if challenges else None


def get_competition_period_scores(
    challenge_id: str,
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    container_name = _optional_env("COSMOS_DB_COMPETITIONS_CONTAINER_NAME", "fitness_competitions")
    scores = _query_cosmos(
        container_name,
        (
            "SELECT * FROM c WHERE c.type = 'weekly_score' "
            "AND c.challengeID = @challengeID "
            "AND c.weekEndDate >= @periodStart "
            "AND c.weekEndDate <= @periodEnd"
        ),
        [
            {"name": "@challengeID", "value": challenge_id},
            {"name": "@periodStart", "value": period_start},
            {"name": "@periodEnd", "value": period_end},
        ],
    )
    return [_compact_for_prompt(score) for score in scores]


def get_competition_scoring_rules(challenge_id: str, scoring_version: str | None) -> dict[str, Any] | None:
    if not scoring_version:
        return None

    container_name = _optional_env("COSMOS_DB_COMPETITIONS_CONTAINER_NAME", "fitness_competitions")
    rules = _query_cosmos(
        container_name,
        (
            "SELECT TOP 1 * FROM c WHERE c.type = 'scoring_rules' "
            "AND c.challengeID = @challengeID "
            "AND c.scoringVersion = @scoringVersion"
        ),
        [
            {"name": "@challengeID", "value": challenge_id},
            {"name": "@scoringVersion", "value": scoring_version},
        ],
    )
    return _compact_for_prompt(rules[0]) if rules else None


def get_competition_participants(challenge_id: str) -> list[dict[str, Any]]:
    container_name = _optional_env("COSMOS_DB_COMPETITIONS_CONTAINER_NAME", "fitness_competitions")
    participants = _query_cosmos(
        container_name,
        (
            "SELECT * FROM c WHERE (c.type = 'participant' OR c.type = 'challenge_participant') "
            "AND c.challengeID = @challengeID AND (NOT IS_DEFINED(c.active) OR c.active = true)"
        ),
        [{"name": "@challengeID", "value": challenge_id}],
    )
    if not participants:
        challenges = _query_cosmos(
            container_name,
            "SELECT TOP 1 * FROM c WHERE c.type = 'challenge' AND c.challengeID = @challengeID",
            [{"name": "@challengeID", "value": challenge_id}],
        )
        participants = [
            {
                "id": f"{challenge_id}__{user_id}",
                "type": "challenge_participant",
                "challengeID": challenge_id,
                "userID": user_id,
                "active": True,
            }
            for user_id in (challenges[0].get("participants", []) if challenges else [])
        ]

    enriched_participants = []
    for participant in participants:
        user_id = participant.get("rawUserID") or participant.get("userID") or participant.get("displayName")
        user_profile = None
        if user_id:
            users = _query_cosmos(
                container_name,
                "SELECT TOP 1 * FROM c WHERE c.type = 'user' AND (c.userID = @userID OR c.id = @userID)",
                [{"name": "@userID", "value": user_id}],
            )
            user_profile = users[0] if users else None

        enriched_participants.append(
            {
                **(user_profile or {}),
                **participant,
                "userID": (user_profile or {}).get("userID") or user_id,
                "displayName": participant.get("displayName") or (user_profile or {}).get("displayName") or user_id,
                "participantId": participant.get("participantId") or participant.get("id") or f"{challenge_id}__{user_id}",
                "averageDailyCalorieTarget": (
                    participant.get("averageDailyCalorieTarget")
                    or (user_profile or {}).get("averageDailyCalorieTarget")
                ),
                "weeklyCalorieTarget": participant.get("weeklyCalorieTarget") or (user_profile or {}).get("weeklyCalorieTarget"),
            }
        )

    return [_compact_for_prompt(participant) for participant in enriched_participants]


def get_competition_raw_records(
    participants: list[dict[str, Any]],
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    container_name = _optional_env("COSMOS_DB_RAW_CONTAINER_NAME", "fitness_raw")
    records: list[dict[str, Any]] = []

    for participant in participants:
        user_id = participant.get("rawUserID") or participant.get("userID") or participant.get("displayName")
        if not user_id:
            continue

        records.extend(
            _query_cosmos(
                container_name,
                (
                    "SELECT * FROM c WHERE c.userID = @userID "
                    "AND c.date >= @periodStart AND c.date <= @periodEnd"
                ),
                [
                    {"name": "@userID", "value": user_id},
                    {"name": "@periodStart", "value": period_start},
                    {"name": "@periodEnd", "value": period_end},
                ],
            )
        )

    return [_compact_for_prompt(record, max_string_length=200) for record in records[:100]]


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _last_sunday_on_or_before(value: date) -> date:
    days_since_sunday = (value.weekday() - 6) % 7
    return value - timedelta(days=days_since_sunday)


def _is_first_sunday_after_month_end(value: date) -> bool:
    return value.weekday() == 6 and value.day <= 7


def get_due_competition_leaderboard_kind(today: date | None = None) -> str:
    today = today or date.today()
    challenge = get_selected_competition_challenge()

    if challenge and challenge.get("endDate") and today == _last_sunday_on_or_before(_parse_iso_date(challenge["endDate"])):
        return "final"
    if _is_first_sunday_after_month_end(today):
        return "month"
    return "week"


def should_send_competition_leaderboard(leaderboard_kind: str, today: date | None = None) -> bool:
    return _normalise_leaderboard_kind(leaderboard_kind) == get_due_competition_leaderboard_kind(today)


def _forfeit_for_leaderboard(challenge: dict[str, Any] | None, leaderboard_kind: str) -> dict[str, Any] | None:
    if not challenge:
        return None
    forfeits = challenge.get("forfeits") or {}
    forfeit = forfeits.get(FORFEIT_KEYS_BY_LEADERBOARD_KIND[leaderboard_kind])
    if not forfeit or not forfeit.get("enabled", False):
        return None
    return forfeit


def _ranking_for_participant(leaderboard: dict[str, Any], participant_id: str | None) -> dict[str, Any] | None:
    if not participant_id:
        return None
    return next(
        (
            ranking
            for ranking in leaderboard.get("rankings", [])
            if ranking.get("participantId") == participant_id
        ),
        None,
    )


def _build_competition_ai_payload(
    today: date | None = None,
    leaderboard_kind: str | None = None,
) -> dict[str, Any] | None:
    today = today or date.today()
    requested_kind = _normalise_leaderboard_kind(leaderboard_kind)
    leaderboard = get_latest_competition_leaderboard(requested_kind, today)
    if not leaderboard:
        return None

    challenge_id = leaderboard.get("challengeID") or leaderboard.get("challengeId")
    period_start = leaderboard.get("periodStartDate") or leaderboard.get("weekStartDate")
    period_end = leaderboard.get("periodEndDate") or leaderboard.get("weekEndDate")
    leaderboard_kind = leaderboard.get("leaderboardKind") or leaderboard.get("type", "leaderboard_week").removeprefix("leaderboard_")
    scores = get_competition_period_scores(challenge_id, period_start, period_end)
    scoring_version = next((score.get("scoringVersion") for score in scores if score.get("scoringVersion")), None)
    participants = get_competition_participants(challenge_id)
    challenge = get_competition_challenge(challenge_id)
    forfeit = _forfeit_for_leaderboard(challenge, leaderboard_kind)
    loser_ranking = _ranking_for_participant(leaderboard, leaderboard.get("loserParticipantId"))

    return {
        "report_date": today.isoformat(),
        "message_type": "competition_leaderboard_results",
        "leaderboard_kind": leaderboard_kind,
        "period": {
            "start_date": period_start,
            "end_date": period_end,
        },
        "challenge": challenge,
        "leaderboard": leaderboard,
        "forfeit_for_period": forfeit,
        "forfeit_loser": loser_ranking,
        "weekly_scores_in_period": scores,
        "scoring_rules": get_competition_scoring_rules(challenge_id, scoring_version),
        "participants": participants,
        "raw_fitness_records": get_competition_raw_records(participants, period_start, period_end),
        "instruction": (
            "Create a concise Telegram leaderboard results message for the leaderboard period. "
            "Explain why each participant got their score, what drove the winning and losing result, "
            "and how each participant can improve in the next relevant period."
        ),
    }


def persist_competition_ai_message(payload: dict[str, Any], message: str) -> dict[str, Any]:
    leaderboard = payload["leaderboard"]
    challenge_id = leaderboard.get("challengeID") or leaderboard.get("challengeId")
    leaderboard_id = leaderboard["id"]
    generated_at = _utc_now()
    message_document = {
        "id": f"{leaderboard_id}__ai_message",
        "type": "leaderboard_ai_message",
        "challengeID": challenge_id,
        "challengeId": challenge_id,
        "leaderboardId": leaderboard_id,
        "leaderboardType": leaderboard.get("type"),
        "leaderboardKind": leaderboard.get("leaderboardKind"),
        "periodStartDate": leaderboard.get("periodStartDate") or leaderboard.get("weekStartDate"),
        "periodEndDate": leaderboard.get("periodEndDate") or leaderboard.get("weekEndDate"),
        "status": "generated",
        "channel": "app",
        "message": message,
        "modelDeployment": os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        "generatedAt": generated_at,
        "version": 1,
    }

    container_name = _optional_env("COSMOS_DB_COMPETITIONS_CONTAINER_NAME", "fitness_competitions")
    container = _cosmos_container(container_name)
    saved_message = container.upsert_item(message_document)

    leaderboard_update = {
        **leaderboard,
        "aiMessageId": saved_message["id"],
        "aiMessageGeneratedAt": generated_at,
        "aiMessageStatus": "generated",
        "aiMessage": message,
        "message": message,
    }
    container.upsert_item(leaderboard_update)
    return _compact_for_prompt(saved_message)


def _build_ai_user_prompt(payload: dict[str, Any], is_competition_message: bool) -> str:
    if is_competition_message:
        return (
            "Create a fun, Telegram-friendly competition leaderboard results message from this JSON. "
            "Use only the supplied data. Keep it under 1800 characters. "
            "The leaderboard may be weekly, monthly, or final; use leaderboard_kind, type, "
            "periodStartDate/periodEndDate, and the period-specific points field in rankings. "
            "Use each score document's points object as the source of truth for awarded points. "
            "capsApplied are ceilings caused by missing data, not bonus points or minimum awarded points. "
            "Do not say a participant received capped, minimum, or minimal points unless the points object shows those points were actually awarded. "
            "Lean into friendly rivalry and competitive spirit, but stay encouraging and factual. "
            "Use playful sub-headings and tasteful emoji. Avoid generic coaching language. "
            "Include these sections: "
            "1) A short hype/commentary opener that captures the story of the leaderboard period. "
            "2) The scoreboard: rank, period points, and season points to date. "
            "3) Score breakdown: explain each participant's contributing weekly scores/category explanations. "
            "4) The swing factor: compare the biggest winning and losing score drivers. "
            "5) The forfeit: if forfeit_for_period and forfeit_loser are supplied, clearly name the loser and the relevant forfeit. "
            "If forfeit_for_period is supplied but forfeit_loser is missing because of a tie or single-participant board, say no forfeit is assigned. "
            "For weekly forfeits, include the supplied acknowledgement message template if it fits naturally. "
            "For monthly/final forfeits, describe the item, cost limit, or logistics details if supplied. "
            "6) Next moves: for weekly/monthly leaderboards, give one or two practical improvement actions for the next period; "
            "for final leaderboards, summarize the decisive patterns and lessons for the next challenge. "
            "Mention missing/incomplete data where it affects scoring. "
            "A little banter is fine; do not insult anyone or overstate conclusions. "
            "Do not include raw JSON. Avoid medical claims.\n\n"
            f"{json.dumps(payload, default=str, ensure_ascii=False)}"
        )

    return (
        "Create a concise Telegram-friendly weekly coaching note from this JSON. "
        "Use only the supplied data. Keep it under 1200 characters. "
        "Include these sections: "
        "1) Previous week: specific feedback on weight, nutrition, and training. "
        "2) Week ahead: practical advice for calories, protein, consistency, and recovery. "
        "3) Fitness session aims: suggest target weights/reps/structure only where supported by recent workout data; otherwise give clear progression rules. "
        "4) Key focus: one measurable priority for the next 7 days. "
        "If data is missing or incomplete, say so briefly. Avoid medical claims.\n\n"
        f"{json.dumps(payload, default=str, ensure_ascii=False)}"
    )


def _build_ai_prompt_payload(today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    week_end = today - timedelta(days=1)
    week_start = week_end - timedelta(days=6)

    return {
        "report_date": today.isoformat(),
        "previous_week": {
            "start_date": week_start.isoformat(),
            "end_date": week_end.isoformat(),
        },
        "targets": {
            "goal_weight_kg": goal_weight,
            "starting_weight_kg": starting_weight,
            "daily_calorie_target": phase_1_weekly_calories,
        },
        "computed_summaries": {
            "weight": build_weight_summary_message(today),
            "food": build_food_summary_message(week_end),
        },
        "cosmos_records": get_cosmos_fitness_records(),
    }


async def build_ai_feedback_message(
    today: date | None = None,
    leaderboard_kind: str | None = None,
    competition_only: bool = False,
    persist_competition_message: bool = True,
) -> str:
    deployment = _required_env("AZURE_OPENAI_DEPLOYMENT")
    competition_payload = _build_competition_ai_payload(today, leaderboard_kind)
    if competition_only and competition_payload is None:
        selected_kind = _normalise_leaderboard_kind(leaderboard_kind)
        raise RuntimeError(f"No {selected_kind} competition leaderboard found for AI summary.")

    compact_payload = competition_payload or build_compact_ai_payload(_build_ai_prompt_payload(today))
    is_competition_message = competition_payload is not None

    async with AsyncAzureOpenAI(
        api_key=_required_env("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
        azure_endpoint=_required_env("AZURE_OPENAI_ENDPOINT"),
    ) as client:
        completion = await client.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a practical fitness competition coach. Use only the supplied data. "
                        "Give concise, specific feedback on leaderboard results, scoring drivers, and next-week improvements. "
                        "Do not invent missing data. Do not diagnose medical conditions."
                    ),
                },
                {
                    "role": "user",
                    "content": _build_ai_user_prompt(compact_payload, is_competition_message),
                },
            ],
            max_tokens=650 if is_competition_message else 450,
            temperature=0.4,
        )

    content = completion.choices[0].message.content
    if not content:
        raise RuntimeError("Azure OpenAI returned an empty response")
    heading = "Competition leaderboard feedback" if is_competition_message else "AI coaching feedback"
    message = f"{heading}\n{content.strip()}"

    if is_competition_message and persist_competition_message:
        persist_competition_ai_message(competition_payload, message)

    return message


async def send_telegram_message(message: str) -> None:
    bot_token = _required_env("FITNESS_SUMMARY_BOT_TOKEN")
    chat_id = _required_env("TELEGRAM_CHAT_ID")

    bot = telegram.Bot(bot_token)
    async with bot:
        await bot.send_message(chat_id=chat_id, text=message)


async def send_weight_summary() -> None:
    await send_telegram_message(build_weight_summary_message())


async def send_food_summary() -> None:
    await send_telegram_message(build_food_summary_message())


async def send_ai_feedback_summary() -> None:
    await send_telegram_message(await build_ai_feedback_message())


async def send_competition_leaderboard_summary(leaderboard_kind: str) -> None:
    await send_telegram_message(
        await build_ai_feedback_message(leaderboard_kind=leaderboard_kind, competition_only=True)
    )


async def generate_competition_leaderboard_summary(
    leaderboard_kind: str,
    today: date | None = None,
) -> str:
    return await build_ai_feedback_message(
        today=today,
        leaderboard_kind=leaderboard_kind,
        competition_only=True,
        persist_competition_message=True,
    )


async def main() -> None:
    await send_weight_summary()


if __name__ == "__main__":
    # asyncio.run(main())
    payload = _build_ai_prompt_payload(datetime.today().date())
    compact_payload = build_compact_ai_payload(payload)
    with open("debug_payload.json", "w", encoding="utf-8") as f:
        json.dump(compact_payload, f, default=str, ensure_ascii=False, indent=2)
